"""Session-owned computer targets with observed, single-use action references.

The controlled browser is separate from Studio and the native browser, and reaches
only the public internet: every connection it makes goes through a network boundary
of its own (_Boundary) that applies the shared destination rule. Desktop control is
explicitly attached to an X11 window, never a guessed active target.
All mutations are exposed through the normal tool permission middleware.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import time
from urllib.parse import urlsplit
from uuid import uuid4

from PIL import ImageGrab
from . import config
from .core import sandbox_net


class ComputerError(ValueError):
    pass


_DOM = r"""() => {
  const all=[...document.querySelectorAll('button,a[href],input,textarea,select,[role=button],[role=tab],[contenteditable=true]')];
  const elements=all.map((e,index)=>{
    const r=e.getBoundingClientRect(),s=getComputedStyle(e);
    if(!r.width||!r.height||s.visibility==='hidden'||s.display==='none')return null;
    return {id:String(index),tag:e.tagName.toLowerCase(),role:e.getAttribute('role')||'',
      name:(e.getAttribute('aria-label')||e.labels?.[0]?.innerText||e.innerText||e.getAttribute('placeholder')||'').trim().slice(0,200),
      type:e.type||'',value:e.type==='password'?'[redacted]':String(e.value??'').slice(0,200),
      href:e.href||'',formAction:e.hasAttribute('formaction')?e.formAction:(e.form?.action||''),
      formMethod:e.hasAttribute('formmethod')?e.formMethod:(e.form?.method||''),
      disabled:!!e.disabled,checked:!!e.checked,
      rect:[r.x,r.y,r.width,r.height].map(v=>Math.round(v)),visible:r.bottom>0&&r.right>0&&r.top<innerHeight&&r.left<innerWidth};
  }).filter(Boolean).sort((a,b)=>Number(b.visible)-Number(a.visible)).slice(0,500);
  return {url:location.href,title:document.title,viewport:[innerWidth,innerHeight],scroll:[scrollX,scrollY],focus:all.indexOf(document.activeElement),
    text:(document.body?.innerText||'').slice(0,6000),elements,
    note:'Main document controls only; embedded frames and closed shadow roots require another available route.'};
}"""
_SELECTOR = 'button,a[href],input,textarea,select,[role=button],[role=tab],[contenteditable=true]'


def _fingerprint(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


# Dream's own desktop shell (desktop/window.py: this title, program class Dream, WM_CLASS dream-desktop/Dream)
# and the Studio page (gui/static/index.html <title>), which `dream --gui` shows as a tab in the owner's own
# browser: no PID or class of Dream's, and a browser window's title follows its active tab, so this check runs
# on every action, not only at attach. The model must never drive the window that shows its own approval cards.
_OWN_TITLE = 'Dream — Workspace'
_STUDIO_TITLE = 'Dream Studio'
_OWN_CLASSES = {'dream-desktop', 'Dream'}
# Terminal emulators by WM_CLASS: a keystroke there is a shell command with no sandbox and no approval.
_TERMINAL_CLASSES = {'gnome-terminal', 'gnome-terminal-server', 'kgx', 'org.gnome.console', 'xterm', 'uxterm', 'konsole',
                     'xfce4-terminal', 'mate-terminal', 'lxterminal', 'qterminal', 'terminator', 'tilix', 'kitty', 'alacritty',
                     'wezterm', 'org.wezfurlong.wezterm', 'ghostty', 'com.mitchellh.ghostty', 'warp', 'dev.warp.warp',
                     'rio', 'contour', 'cosmic-term', 'com.system76.cosmicterm', 'io.elementary.terminal', 'roxterm',
                     'mlterm', 'urxvt', 'rxvt', 'st', 'st-256color', 'tilda', 'guake', 'yakuake', 'sakura', 'terminology',
                     'termite', 'foot', 'ptyxis', 'org.gnome.ptyxis', 'com.raggesilver.blackbox', 'deepin-terminal',
                     'cool-retro-term', 'hyper', 'tabby', 'x-terminal-emulator'}


# Dream's own live-Blender window (media/blender_live.py, DREAM-109): the nested X server (Xephyr) the
# session's launcher starts, titled by the launcher and drawn on the owner's display. Xephyr sets no
# _NET_WM_PID, so the window's owner is taken from Dream's own process tree (_launched_xephyr).
_LIVE_BLENDER_TITLE = 'Dream live Blender: '


def _argv(pid):
    try:
        return Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')[:-1]
    except OSError:
        return None


def _launched_xephyr(title):
    """The pid of the Xephyr that Dream's live-Blender launcher started for the window titled `title`, or
    None: a process below this one whose command is Xephyr with exactly that -title (the launcher puts the
    window's title on its command line). Derived from Dream's own process tree on every read, never
    registered: the launcher runs as a process of its own, and a window with this title prefix and WM_CLASS
    on the owner's display can only be one the launcher opened (the model's sandbox cannot reach that
    display), so the Xephyr showing this title is its owner. A decoy process with the same title could at
    most be taken as the owner instead -- which changes nothing the model can use: a descendant is never
    Dream's own window, a pty it held would make the window view-only, and another pid only asks again."""
    below = _descendants(os.getpid())
    if below is None:
        return None
    wanted = title.encode()
    for pid in below:
        argv = _argv(pid)
        if argv and os.path.basename(argv[0]) == b'Xephyr':
            if any(flag == b'-title' and value == wanted for flag, value in zip(argv, argv[1:])):
                return pid
    return None


def _own_pids():
    """This process and its ancestors: the desktop shell or terminal that hosts Dream."""
    pids, pid = set(), os.getpid()
    while pid > 1 and pid not in pids:
        pids.add(pid)
        try:
            found = re.search(r'^PPid:\s*(\d+)', Path(f'/proc/{pid}/status').read_text(), re.MULTILINE)
        except OSError:
            break
        pid = int(found.group(1)) if found else 0
    return pids


def _own_window(state):
    title = state.get('title') or ''
    return (state.get('pid') in _own_pids() or title == _OWN_TITLE or _STUDIO_TITLE in title
            or bool(_OWN_CLASSES & set(state.get('wm_class', ()))))


_DESCENDANT_CAP = 4096   # processes below the window's that the walk will visit; a larger tree is not walked to its end


def _stat_fields(pid):
    """The fields of /proc/<pid>/stat after the command name (field 3, the state, first): None for a process
    that is confirmed gone (ENOENT/ESRCH). Any other failure raises -- the read refused, the file malformed
    -- and the caller knows nothing."""
    try:
        stat = Path(f'/proc/{pid}/stat').read_text()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return stat[stat.rindex(')') + 2:].split()


def _descendants(pid):
    """The processes below `pid`, by parent, read from /proc (this kernel exposes no children list): every
    process that could be read. None when the enumeration is incomplete -- /proc cannot be listed, a stat
    is refused or malformed, or the tree is larger than _DESCENDANT_CAP; only a process confirmed gone
    during the read is skipped (it holds nothing)."""
    children = {}
    try:
        entries = os.listdir('/proc')
    except OSError:
        return None
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            fields = _stat_fields(entry)
            parent = None if fields is None else int(fields[1])
        except (OSError, ValueError, IndexError):
            return None
        if parent is not None:
            children.setdefault(parent, []).append(int(entry))
    found, frontier = [], [pid]
    while frontier:
        for child in children.get(frontier.pop(), ()):
            if child != pid and child not in found:
                if len(found) >= _DESCENDANT_CAP:
                    return None
                found.append(child)
                frontier.append(child)
    return found


def _pty_ownership(pid):
    """Whether the window's process, or any process below it, holds a pseudo-terminal master -- a terminal
    emulator's signal, wherever it keeps its pty (the GUI process, or a helper it spawned): 'held', 'free',
    or 'unknown' whenever the inspection is incomplete -- the window's process gone, a file table or a link
    refused, the tree unlistable or larger than the walk. 'free' is answered only for the process that was
    inspected: still there afterwards, with the same start time (a pid reused by another process, or the
    root gone with its children, is not the window's process any more)."""
    def holds(process, gone):
        try:
            entries = os.scandir(f'/proc/{process}/fd')
        except (FileNotFoundError, ProcessLookupError):
            return gone
        except OSError:
            return 'unknown'
        with entries:
            for entry in entries:
                try:
                    if os.readlink(entry.path) in ('/dev/ptmx', '/dev/pts/ptmx'):
                        return 'held'
                except (FileNotFoundError, ProcessLookupError):
                    continue   # closed meanwhile
                except OSError:
                    return 'unknown'   # a link refused: not seen, not known
        return 'free'
    def generation():
        try:
            fields = _stat_fields(pid)
            return None if fields is None else fields[19]   # field 22, starttime: the process behind this pid
        except (OSError, ValueError, IndexError):
            return None
    started = generation()
    if started is None:
        return 'unknown'
    verdict = holds(pid, 'unknown')   # the window's own process gone: nothing to identify it by
    if verdict != 'free':
        return verdict
    below = _descendants(pid)
    if below is None:
        return 'unknown'
    for process in below:
        verdict = holds(process, 'free')   # a child confirmed gone meanwhile holds nothing
        if verdict != 'free':
            return verdict
    return 'free' if generation() == started else 'unknown'


def _view_only(state):
    """Why a window takes no input, or '' when it may: a terminal emulator by class; a window whose process,
    or a process below it, holds a pseudo-terminal; a window whose ownership is unavailable -- no
    _NET_WM_PID, a process that is gone, a file table that cannot be read -- since a terminal that cannot
    be identified is one. Unknown falls to view-only, never to writable."""
    if _TERMINAL_CLASSES & {name.lower() for name in state.get('wm_class', ())}:
        return 'Terminal window'
    pid = state.get('pid')
    if pid is None:
        return 'Window of unknown ownership (no _NET_WM_PID)'
    verdict = _pty_ownership(pid)
    if verdict == 'held':
        return 'Terminal window (its process, or one below it, holds a pseudo-terminal)'
    if verdict == 'unknown':
        return 'Window of unknown ownership (its process is gone, or its file table cannot be read)'
    return ''


_BROWSER_MODIFIERS = {'Control', 'Alt', 'Shift', 'Meta', 'ControlOrMeta',
                      'ControlLeft', 'ControlRight', 'AltLeft', 'AltRight',
                      'ShiftLeft', 'ShiftRight', 'MetaLeft', 'MetaRight'}
_BROWSER_KEYS = set("Backspace Tab Enter Escape Space Delete Insert Home End PageUp PageDown "
                    "ArrowUp ArrowDown ArrowLeft ArrowRight CapsLock NumLock ScrollLock Pause "
                    "PrintScreen ContextMenu AltGraph Backquote Minus Equal BracketLeft "
                    "BracketRight Backslash Semicolon Quote Comma Period Slash "
                    "NumpadDivide NumpadMultiply NumpadSubtract NumpadAdd NumpadDecimal NumpadEnter "
                    "AudioVolumeMute AudioVolumeDown AudioVolumeUp MediaTrackNext "
                    "MediaTrackPrevious MediaPlayPause".split())
_BROWSER_ALIASES = {'ctrl': 'Control', 'control': 'Control', 'shift': 'Shift',
                    'alt': 'Alt', 'option': 'Alt', 'meta': 'Meta', 'cmd': 'Meta',
                    'command': 'Meta', 'esc': 'Escape', 'return': 'Enter', 'del': 'Delete'}


def _browser_key(key):
    parts = [_BROWSER_ALIASES.get(p.lower(), p) for p in key.split('+')]
    modifiers = [re.sub(r'(Left|Right)$', '', p) for p in parts[:-1]]
    duplicate = len(modifiers) != len(set(modifiers)) or ('ControlOrMeta' in modifiers and bool({'Control','Meta'} & set(modifiers)))
    if (duplicate or any(p not in _BROWSER_MODIFIERS for p in parts[:-1]) or
        not all(p in _BROWSER_KEYS or p in _BROWSER_MODIFIERS or
                re.fullmatch(r'[A-Za-z0-9_]|Key[A-Z]|Digit[0-9]|Numpad[0-9]|F(?:[1-9]|1[0-2])', p) for p in parts)):
        raise ComputerError('Unsupported browser key/chord; use named keys and Control/Alt/Shift/Meta modifiers. No input dispatched.')
    return '+'.join(parts)


async def _command(*args: str, env=None) -> str:
    """Fixed binary/argument operations; no model-supplied shell or scripts."""
    process = await asyncio.create_subprocess_exec(*args, env=env, stdout=asyncio.subprocess.PIPE,
                                                  stderr=asyncio.subprocess.PIPE)
    try:
        out, error = await asyncio.wait_for(process.communicate(), 5)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await asyncio.shield(process.wait())
        raise
    if process.returncode:
        raise ComputerError(error.decode(errors='replace')[:1000] or f'{args[0]} failed ({process.returncode})')
    return out.decode(errors='replace')[:16000].strip()


async def _release_input(operation):
    """Finish bounded release before surrendering ownership, even on repeated Stop."""
    cleanup = asyncio.create_task(asyncio.wait_for(operation, 5))
    cancelled = None
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError as exc:
            cancelled = exc
    cleanup.result()  # Release failure must remain visible.
    if cancelled is not None:
        raise cancelled


# The controlled browser's destination rule (DREAM-187): the one the sandbox proxy applies to the model's
# shell and the browse tool applies to its pages (core/sandbox_net), with no grant of any kind -- browse
# admits a local destination the owner typed; this browser admits none, the owner's Browser tab being the
# place for local pages. Refused before any connection: loopback, private and link-local addresses, this
# machine's own addresses, `localhost` by name, any name resolving to one of those -- and Dream's own
# Studio by name, so the model can never operate its own approval cards through this browser.
_PUBLIC_RULE = ("The controlled browser reaches only the public internet: no local, private or own-machine "
                "destination opens in it (use the Browser tab for local pages), and Dream's own Studio never does.")
# How long a document's boundary check (_verify_through_boundary) may take before the target is refused.
_CHECK_S = 5.0
# What an action's dispatch sent, once it returned: carried into the observation that follows it, so a
# refusal raised there says what went out rather than that nothing did.
_DISPATCHED = {'click': 'The click was dispatched', 'type': 'The text was typed', 'key': 'The keys were pressed and released',
               'scroll': 'The wheel was scrolled', 'drag': 'The drag was dispatched and the button released',
               'stroke': 'The stroke was dispatched and the button released', 'focus': 'The window was activated'}

# What the context's proxy does not cover, closed rather than routed (the browser is launched with these):
# WebRTC's ICE reaches the network by itself over UDP, so it may use no UDP outside a proxy; QUIC is UDP
# too and not proxied; and the browser resolves no name itself (prefetch, preconnect: a lookup is a way
# out) -- behind an HTTP proxy every name travels to the boundary unresolved, which the boundary resolves
# and judges; the proxy's own loopback address stays resolvable.
_LAUNCH_ARGS = ['--force-webrtc-ip-handling-policy=disable_non_proxied_udp', '--disable-quic',
                '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1']


def _collect(task):
    """A done callback that takes a task's outcome off the loop's hands (an exception nobody retrieves
    would be reported when the garbage collector finds the task)."""
    if not task.cancelled():
        task.exception()


def _origin(url):
    """The origin a document's URL names, for the 'always' approval (Computer._identity): scheme, the exact
    host as written (letter case folded, userinfo dropped, a trailing dot kept -- `a.example.` is another
    origin) and the effective port; a blob: URL takes its inner URL's origin. None for an opaque or unknown
    origin (data:, about:, file:, anything else). Not the destination-admission parser (web/browser.py),
    which drops credentialed URLs and normalises hosts: two origins must never fall together here."""
    if url.startswith('blob:'):
        return _origin(url[5:])
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        return None
    host = f'[{parts.hostname}]' if ':' in parts.hostname else parts.hostname
    if port is None:
        port = 443 if parts.scheme == 'https' else 80
    return f'{parts.scheme}://{host}:{port}'


def _destination_refused(host, port):
    """Why the controlled browser must not connect to host:port before any lookup, or None: Dream's own
    Studio (or another own listener) by name."""
    service = sandbox_net.own_service(host, port)
    if service:
        return f"{host}:{port} is Dream's own {service}, which the controlled browser never drives"
    return None


class _Boundary:
    """Dream's network boundary for one controlled browser context, alive as long as the context: an HTTP
    proxy on loopback that this context alone is given, so every connection its pages ask for -- the main
    frame, redirect hops, child frames, resources, WebSocket handshakes -- reaches it by name (Chromium
    resolves nothing itself behind a proxy, and Playwright routes loopback through it too) and is judged
    there: Dream's own Studio refused by name, everything else resolved and judged by the shared public
    rule (sandbox_net), a public destination dialled by the addresses judged and never re-resolved, a local
    or private one refused with nothing dialled. Every refusal is recorded for the observation. Once closed,
    nothing is accepted, judged or dialled, and every connection it holds is closed."""

    def __init__(self):
        self.server = None
        self.address = None
        self.refused = []
        self._tasks = set()
        self._streams = []
        self._closed = False

    async def start(self):
        self.server = await asyncio.start_server(self._accept, '127.0.0.1', 0, start_serving=False)
        self.address = self.server.sockets[0].getsockname()[:2]
        try:
            await self.server.start_serving()
        except BaseException:
            self.close()
            raise
        return self

    @property
    def proxy(self):
        return 'http://%s:%d' % self.address

    def _accept(self, reader, writer):
        if self._closed:
            writer.close()
            return
        self._streams = [stream for stream in self._streams if not stream.is_closing()] + [writer]
        task = asyncio.create_task(sandbox_net._proxy_connection(reader, writer, self._connect))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(_collect)

    async def _connect(self, scheme, host, port):
        if self._closed:
            raise sandbox_net._Refused('the browser target is closed')
        name = f"{f'[{host}]' if ':' in host else host}:{port}"
        reason = _destination_refused(host, port)
        if reason:
            self.refused.append(name)
            raise sandbox_net._Refused(f'{reason}. {_PUBLIC_RULE}')
        try:
            addresses = await sandbox_net._public_addresses(host)
        except sandbox_net._Refused as exc:
            self.refused.append(name)
            raise sandbox_net._Refused(f'{exc}. {_PUBLIC_RULE}') from None
        if self._closed:  # closed as this was judged: nothing is dialled after it
            raise sandbox_net._Refused('the browser target is closed')
        upstream = await sandbox_net._connect(host, port, addresses, abandon=lambda: self._closed)
        if self._closed:  # closed during the dial: what it reached is not kept
            upstream[1].close()
            raise sandbox_net._Refused('the browser target is closed')
        self._streams.append(upstream[1])
        return upstream

    def close(self):
        """At once and without waiting, each part on its own so one that will not close keeps no other
        open: nothing new is accepted, judged or dialled; every handler is stopped; every connection and
        the listener are closed."""
        self._closed = True
        for task in list(self._tasks):
            task.cancel()
        for stream in self._streams:
            try:
                stream.close()
            except Exception:
                pass
        self._streams = []
        if self.server is not None:
            try:
                self.server.close()
            except Exception:
                pass


class Computer:
    def __init__(self, workspace: Path, captures: Path):
        self.workspace = Path(workspace).resolve()
        self.captures = captures
        self.targets = {}
        self._pw = self._browser = None
        self._lock = asyncio.Lock()

    def capabilities(self):
        desktop = (os.environ.get('XDG_SESSION_TYPE') != 'wayland'
                   and re.fullmatch(r':\d+(?:\.\d+)?', os.environ.get('DISPLAY', '')) is not None
                   and all(shutil.which(binary) for binary in ('xdotool', 'xwininfo', 'xprop')))
        return {'browser': 'Chromium starts on explicit open; runtime not yet qualified',
                'desktop': bool(desktop), 'desktop_note': 'X11, xdotool, xwininfo and xprop required; Wayland control is unavailable',
                'drawing': {'actions': ['click', 'drag', 'stroke'], 'button': 'left',
                            'max_points': 128, 'duration_ms': {'minimum': 1, 'maximum': 2000, 'optional': True},
                            'max_paced_points': 256, 'browser_coordinates': 'viewport CSS pixels',
                            'desktop_coordinates': 'unscaled window-relative pixels'},
                'targets': [{'target_id': k, 'kind': v['kind']} for k, v in self.targets.items()]}

    async def open(self, kind: str, *, url: str = '', window_id: str = ''):
        async with self._lock:
            if len(self.targets) >= 4:
                raise ComputerError('Close an owned target before opening another (maximum four).')
            token = uuid4().hex
            if kind == 'browser':
                if not re.match(r'^https?://[^/\s]+', url) or len(url) > 4000:
                    raise ComputerError('Browser targets require an explicit http or https URL.')
                await self._judge_url(url)  # refused before anything launches or resolves further
                if self._browser is None:
                    from playwright.async_api import async_playwright
                    self._pw = await async_playwright().start()
                    try:
                        self._browser = await self._pw.chromium.launch(headless=True, args=_LAUNCH_ARGS)
                    except BaseException:
                        await self._pw.stop()
                        self._pw = None
                        raise
                boundary = await _Boundary().start()
                try:
                    # The boundary is this context's proxy: every connection any of its pages asks for goes
                    # through it, loopback included, and a proxy that cannot be reached fails the request
                    # (Chromium does not fall back to a direct connection from a fixed proxy).
                    context = await self._browser.new_context(viewport={'width': 1280, 'height': 800},
                                                               accept_downloads=False, service_workers='block',
                                                               proxy={'server': boundary.proxy})
                except BaseException:
                    boundary.close()
                    raise
                async def route(request):
                    if request.request.url.startswith(('http://', 'https://', 'data:', 'blob:', 'about:')):
                        await request.continue_()
                    else:
                        await request.abort()
                target = {'kind': kind, 'context': context, 'navigation': 0, 'boundary': boundary, 'checks': set()}
                try:
                    await context.route('**/*', route)
                    page = await context.new_page()
                    target['page'] = page
                    context.on('page', lambda other: asyncio.create_task(other.close()) if other is not page else None)
                    page.on('dialog', lambda dialog: asyncio.create_task(dialog.dismiss()))
                    page.on('response', lambda response: self._verify_through_boundary(target, response))
                    await page.goto(url, wait_until='domcontentloaded', timeout=15000)
                except BaseException:
                    await context.close()
                    boundary.close()
                    raise
                def navigated(frame):
                    if frame == page.main_frame:
                        target['navigation'] += 1
                page.on('framenavigated', navigated)
            elif kind == 'desktop':
                if not self.capabilities()['desktop']:
                    raise ComputerError('Desktop control requires X11, xdotool, xwininfo and xprop. Wayland is unsupported.')
                if not re.fullmatch(r'[1-9][0-9]{0,11}|0x[0-9a-fA-F]{1,8}', window_id):
                    raise ComputerError('Choose an explicit numeric X11 window ID from the window list.')
                target = {'kind': kind, 'window_id': str(int(window_id, 0) if window_id.startswith('0x') else int(window_id))}
                state = await self._desktop_state(target)  # Attach only; never switch focus implicitly.
                if _own_window(state):
                    raise ComputerError("Dream's own window is not a target: the model would be driving its own approval cards.")
            else:
                raise ComputerError('Target kind must be browser or desktop.')
            self.targets[token] = target
            try:
                return await self._observe(token, target)
            except BaseException:
                self.targets.pop(token, None)
                if kind == 'browser':
                    await self._close_browser(target)
                raise

    async def _judge_url(self, url):
        """The URL a browser target opens at, judged by the boundary's rule before the browser is touched:
        a plain http(s) URL naming neither Dream's own Studio nor a local or private destination."""
        from .web.browser import destination, _why_not   # here, not at import: see _observe
        dest = destination(url)
        if dest is None:
            raise ComputerError(f'Browser targets need a plain http or https URL: {_why_not(url)}.')
        reason = _destination_refused(dest[1], dest[2])
        if reason:
            raise ComputerError(f'{reason}. {_PUBLIC_RULE}')
        try:
            await sandbox_net._public_addresses(dest[1])
        except sandbox_net._Refused as exc:
            raise ComputerError(f'{exc}. {_PUBLIC_RULE}') from None
        except OSError as exc:
            raise ComputerError(f'{dest[1]} did not resolve: {exc.strerror or exc}') from None

    def _verify_through_boundary(self, target, response):
        """Every document a page receives must have come through the boundary: the address the browser
        reports having connected to is the boundary's, or the target is breached and refuses everything
        (_state) -- a document that came another way is never trusted, whatever let it through."""
        if not response.request.is_navigation_request() or not response.url.startswith(('http://', 'https://')):
            return
        async def verify():
            address = await response.server_addr()
            boundary = target['boundary'].address
            if not address:
                target['breach'] = (f'the browser did not report the address it connected to for {response.url}, so it '
                                    f'cannot be shown to have gone through the network boundary')
            elif (address['ipAddress'].strip('[]'), address['port']) != boundary:
                target['breach'] = (f"the browser connected to {address['ipAddress']}:{address['port']} for "
                                    f"{response.url}, not through the network boundary at {boundary[0]}:{boundary[1]}")
        def settle(task):
            """Structural, whatever way the check ended: a check that did not run to its end -- cancelled
            before or during its body, or failed -- is a breach; only one that completed and found the
            boundary's address is not. The exception is retrieved here, so nothing is left for the garbage
            collector to report."""
            if task.cancelled():
                target.setdefault('breach', f'the document check for {response.url} was cancelled before it completed')
            elif task.exception() is not None:
                exc = task.exception()
                fixed = f'the document check for {response.url} failed'
                if target.setdefault('breach', fixed) is fixed:   # recorded before any detail is formatted: a detail that will not format changes nothing
                    try:
                        target['breach'] = f'{fixed}: {type(exc).__name__}: {exc}'
                    except Exception:
                        target['breach'] = f'{fixed}: {type(exc).__name__} (its text could not be formatted)'
        task = asyncio.create_task(verify())
        target['checks'].add(task)
        task.add_done_callback(settle)                     # first the verdict...
        task.add_done_callback(target['checks'].discard)   # ...then the check leaves the live set

    async def _close_browser(self, target):
        if target.get('boundary'):
            target['boundary'].close()   # first: nothing the closing page asks for is judged or dialled
        with contextlib.suppress(Exception):
            await target['context'].close()

    async def windows(self):
        if not self.capabilities()['desktop'] or not shutil.which('wmctrl'):
            raise ComputerError('Listing desktop windows requires X11, xdotool, xwininfo, xprop and wmctrl.')
        output = await _command(shutil.which('wmctrl'), '-lp')
        rows = []
        for line in output.splitlines()[:64]:
            parts = line.split(None, 4)
            if len(parts) == 5:
                rows.append({'window_id': parts[0], 'pid': parts[2], 'title': parts[4][:200]})
        return {'windows': rows, 'note': 'Choose a window explicitly; opening a target does not focus it.'}

    def _target(self, identifier):
        try:
            return self.targets[identifier]
        except KeyError:
            raise ComputerError('Unknown target in this session; open or list targets first.') from None

    async def _desktop_state(self, target):
        binary = shutil.which('xdotool')
        geometry_binary = shutil.which('xwininfo')
        property_binary = shutil.which('xprop')
        if not binary or not geometry_binary or not property_binary:
            raise ComputerError('xdotool, xwininfo or xprop is unavailable.')
        window = target['window_id']
        # libxdo's location helper double-counts parent offsets on decorated
        # clients. xwininfo reports the actual root origin used by our crop;
        # xdotool mousemove --window already translates client points correctly.
        geometry = await _command(geometry_binary, '-id', window, env={**os.environ, 'LC_ALL': 'C'})
        rect = []
        for field in ('Absolute upper-left X', 'Absolute upper-left Y', 'Width', 'Height'):
            values = re.findall(r'^\s*'+re.escape(field)+r':\s*(-?\d+)\s*$', geometry, re.MULTILINE)
            if len(values) != 1:
                raise ComputerError('Malformed desktop client geometry from xwininfo.')
            rect.append(int(values[0]))
        if not 1 <= rect[2] <= 7680 or not 1 <= rect[3] <= 4320:
            raise ComputerError('Unsupported desktop window dimensions.')
        # Who owns the window decides the self-exclusion and the view-only tier, so it is part of
        # every state read and of the fingerprint.
        properties = await _command(property_binary, '-id', window, '_NET_WM_PID', 'WM_CLASS')
        pid = re.search(r'^_NET_WM_PID\(CARDINAL\) = (\d+)$', properties, re.MULTILINE)
        classes = re.search(r'^WM_CLASS\(STRING\) = (.+)$', properties, re.MULTILINE)
        state = {'window_id': window, 'title': await _command(binary, 'getwindowname', window),
                 'focus': await _command(binary, 'getwindowfocus'), 'rect': rect,
                 'pid': int(pid.group(1)) if pid else None,
                 'wm_class': re.findall(r'"((?:[^"\\]|\\.)*)"', classes.group(1)) if classes else [],
                 'coordinate_space': 'unscaled window-relative pixels',
                 'note': 'Desktop stale checks cover window title, focus and geometry. Pixels are an on-screen crop that may include overlays; inspect the visible target before acting.'}
        if state['pid'] is None and 'Xephyr' in state['wm_class'] and state['title'].startswith(_LIVE_BLENDER_TITLE):
            # Dream's own live-Blender window: its owner is the Xephyr Dream's launcher started for this exact
            # title, found in Dream's own process tree on every read -- while it lives the window is drivable
            # and keyed to it; without it the window is of unknown ownership like any other.
            state['pid'] = await asyncio.to_thread(_launched_xephyr, state['title'])
        reason = await asyncio.to_thread(_view_only, state)   # walks /proc
        state['view_only'] = bool(reason)
        if reason:
            state['note'] += f' {reason}: view-only (observe and focus only; no clicks, keys, typing or scrolling).'
        return state

    async def _refuse_breached(self, target, sent=None, close=True):
        """A browser target whose page received a document outside the boundary (_verify_through_boundary)
        refuses everything: closed at once (`close`), or -- mid-input, when input already pressed must be
        released on the live page first -- closed by act() right after. The refusal says what went out:
        `sent` names it, '' means nothing did, and None means the caller does not know -- then what the
        action being observed dispatched (target['dispatched'], set by act()) is said, or nothing is."""
        if target['kind'] != 'browser':
            return
        checks = target.get('checks')
        if checks is not None:
            # The LIVE set, drained under one deadline: a document arriving while earlier checks are awaited
            # (a child frame, a redirect hop) adds its own check, and it is awaited too. Nothing proceeds
            # with a check unfinished; one not done in time is a breach, not a pass.
            loop = asyncio.get_running_loop()
            deadline = loop.time() + _CHECK_S
            while checks and loop.time() < deadline:
                await asyncio.wait(set(checks), timeout=deadline - loop.time())
            if checks:
                for check in list(checks):
                    check.cancel()
                target.setdefault('breach', f'{len(checks)} document check(s) did not complete within {_CHECK_S:g}s')
        if target.get('breach'):
            if close:
                await self._close_browser(target)   # fail closed: the page runs no longer
            if sent is None:
                sent = target.get('dispatched')
            nothing = '' if sent is None else (f'{sent}; nothing further dispatched. ' if sent else 'No input dispatched. ')
            raise ComputerError(f"Browser target breached its network boundary: {target['breach']}. {nothing}"
                                f"The target is closed; open another. {_PUBLIC_RULE}")

    async def _state(self, target, sent=None, close=True):
        if target['kind'] == 'browser':
            await self._refuse_breached(target, sent, close)
            state = {**await asyncio.wait_for(target['page'].evaluate(_DOM), 5), 'navigation': target['navigation']}
            # A document arriving during the read (a child frame: main document and navigation count
            # unchanged) registered its check meanwhile: drained before the state is handed out.
            await self._refuse_breached(target, sent, close)
            refused = target['boundary'].refused if target.get('boundary') else []
            if refused:   # what the page reached for and was refused: part of the state, so a fresh refusal is a change
                state['refused'] = list(dict.fromkeys(refused[-32:]))
                state['refused_note'] = 'Destinations this page asked for that the network boundary refused, nothing connected. ' + _PUBLIC_RULE
            return state
        return await self._desktop_state(target)

    async def _capture(self, target, state):
        if target['kind'] == 'browser':
            return await target['page'].screenshot(type='png', timeout=5000)
        # Capturing an obscured/background window would misidentify the pixels.
        if state['focus'] != state['window_id']:
            return None
        x, y, w, h = state['rect']
        def capture():
            shot = ImageGrab.grab(bbox=(x, y, x+w, y+h), xdisplay=os.environ['DISPLAY'])
            buffer = io.BytesIO()
            shot.save(buffer, format='PNG')
            return buffer.getvalue()
        return await asyncio.to_thread(capture)

    async def _settled_capture(self, target):
        latest = None
        async def sample_until_quiet():
            nonlocal latest
            if target['kind'] == 'browser':
                await target['page'].evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))')
            started = time.monotonic()
            quiet_since = started
            previous = None
            while True:
                state = await self._state(target)
                pixels = await self._capture(target, state)
                consistent = await self._state(target) == state
                if consistent:
                    latest = (state, pixels)
                signature = (_fingerprint(state), hashlib.sha256(pixels).hexdigest() if pixels else None)
                now = time.monotonic()
                if not consistent or signature != previous:
                    quiet_since = now
                if consistent and now-started >= .5 and now-quiet_since >= .25:
                    return state, pixels, True
                previous = signature if consistent else None
                await asyncio.sleep(.1)
        deadline = asyncio.timeout(3)
        try:
            async with deadline:
                return await sample_until_quiet()
        except asyncio.TimeoutError as exc:
            if not deadline.expired():
                raise
            if latest is not None:
                return *latest, False
            return None, None, False  # Readiness hints must not prevent an ordinary fresh capture.

    async def _observe(self, identifier, target, offset=0):
        # A failed refresh must not pair an old token with newly acquired handles.
        target.pop('observation', None)
        target.pop('visible_ids', None)
        await self._refuse_breached(target)
        await self._release_elements(target)
        state, settled_pixels, sampled_quiet = await self._settled_capture(target)
        if state is None:
            state = await self._state(target)
        if target['kind'] == 'browser':
            collection = await target['page'].evaluate_handle(
                '(ids)=>{const nodes=document.querySelectorAll('+json.dumps(_SELECTOR)+');return Object.fromEntries(ids.map(id=>[id,nodes[Number(id)]]));}',
                [e['id'] for e in state['elements'][offset:offset+30]])
            try:
                target['elements'] = await collection.get_properties()
            finally:
                await collection.dispose()
        pixels = await self._capture(target, state)
        # Do not issue an actionable token for state that changed during capture.
        if await self._state(target) != state:
            target.pop('observation', None)
            raise ComputerError('Interface changed during capture; observe again before acting.')
        observation = uuid4().hex
        path = None
        if pixels:
            self.captures.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = self.captures / (identifier + '-' + observation + '.png')
            path.write_bytes(pixels)
            path.chmod(0o600)
        public = json.loads(json.dumps(state))
        if 'elements' in public:
            elements=public['elements']
            if type(offset) is not int or offset<0 or (offset>=len(elements) and offset!=0):
                raise ComputerError('Element offset is outside the current observation.')
            public.update(elements=elements[offset:offset+30], element_offset=offset, total_elements=len(elements))
        value = {'target_id': identifier, 'kind': target['kind'], 'observation_id': observation,
                'state': public, 'screenshot': str(path) if path else None,
                'image_note': 'Fresh pixels attached when image input is available.' if path else 'No pixels: selected window is not focused. Focus it, then observe.',
                'verification': 'observed state, not a task-success judgment',
                'readiness': {'status': 'pixels_unavailable' if pixels is None else
                              ('sampled_quiet' if sampled_quiet and pixels == settled_pixels else 'unsettled'),
                              'note': 'Bounded recent samples only; later changes remain possible. Action stale/pixel checks still apply.'}}
        # Bound serialized JSON, including escaped control characters, as the model receives it: a browser
        # observation travels inside the untrusted_web_content block (tools/computer_tools._text), whose own
        # tags and the escaping of the page's copies count against the same cap. Keep room for action
        # metadata; never hand transport an invalid truncated object.
        budget=config.TOOL_RESULT_CAP-1800
        from .tools.context import untrusted_web_content   # here, not at import: the desktop shell's tests import this module under the system Python
        def size():
            text=json.dumps(value,ensure_ascii=False)
            return len(untrusted_web_content(text) if target['kind']=='browser' else text)
        while size()>budget:
            if len(public.get('elements',[]))>1:
                public['elements'].pop()
            elif len(public.get('text',''))>128:
                public['text']=public['text'][:len(public['text'])//2]
                public['text_truncated']=True
            else:
                def shorten(item):
                    if isinstance(item,dict):return {k:shorten(v) for k,v in item.items()}
                    if isinstance(item,list):return [shorten(v) for v in item]
                    return item[:max(32,len(item)//2)] if isinstance(item,str) and len(item)>64 else item
                shortened=shorten(public)
                if shortened==public:
                    raise ComputerError('Tool result budget is too small for an observation.')
                public.clear();public.update(shortened);public['text_truncated']=True
        if 'elements' in public:
            end=offset+len(public['elements'])
            public['next_element_offset']=end if end<public['total_elements'] else None
            public['elements_note']='Up to 500 visible-first main-document controls; page with element_offset when present.'
            target['visible_ids']={e['id'] for e in public['elements']}
        target.update(observation=observation, fingerprint=_fingerprint(state), observed=time.monotonic(),
                      pixels_sha256=hashlib.sha256(pixels).hexdigest() if pixels else None,
                      identity=self._identity(target, state))
        return value

    @staticmethod
    def _identity(target, state):
        """What the target is, as observed: a browser target's document origin (_origin, from the browser's
        own record of the page's URL, never from page script) -- an opaque origin is this document alone,
        until the main frame navigates again; a desktop target's window with its owner (pid, WM_CLASS). An
        'always' approval of actions on a target is for the target as this (tui/app.py): the same target at
        another origin or another document, or a window whose owner changed, asks again."""
        if target['kind'] == 'browser':
            page = target.get('page')
            origin = _origin(page.url if page is not None else '')
            if origin is None:
                return f"browser\x00opaque\x00{target.get('navigation', 0)}"
            return f'browser\x00{origin}'
        return 'desktop\x00%s\x00%s\x00%s' % (target.get('window_id'), state.get('pid'), json.dumps(state.get('wm_class', [])))

    def identity(self, identifier):
        """The target's identity as last observed (_identity), or None for a target this session does not own."""
        target = self.targets.get(identifier)
        return target.get('identity') if target is not None else None

    async def observe(self, identifier, *, offset=0):
        async with self._lock:
            return await self._observe(identifier, self._target(identifier), offset)

    async def _release_elements(self, target):
        for handle in target.pop('elements', {}).values():
            await handle.dispose()

    async def act(self, identifier, observation, action, **args):
        async with self._lock:
            target = self._target(identifier)
            if action not in {'click', 'type', 'key', 'scroll', 'focus', 'drag', 'stroke'}:
                raise ComputerError('Use click, drag, stroke, type, key, scroll or desktop focus.')
            # Nothing sent yet: a refusal raised before dispatch says so (_refuse_breached reads this when the
            # drain knows nothing itself); the action's own history replaces it once dispatch has returned.
            target['dispatched'] = ''
            await self._refuse_breached(target)
            if not observation or target.get('observation') != observation or time.monotonic()-target.get('observed', 0) > 300:
                raise ComputerError('Observation is stale or already used; observe again.')
            before = await self._state(target)
            if _fingerprint(before) != target['fingerprint']:
                target.pop('observation', None)
                raise ComputerError('Target state changed; observe again instead of replaying the action.')
            if target['kind'] == 'desktop' and _own_window(before):   # the window behind the ID may have changed since open()
                raise ComputerError("Dream's own window is not a target: the model would be driving its own approval cards.")
            if target['kind'] == 'desktop' and action != 'focus' and before.get('view_only', True):   # the verdict _desktop_state reached, off the loop
                raise ComputerError('Terminal windows are view-only: observe or focus them; clicks, keys, typing, drags and scrolling are refused.')
            text = args.get('text', '')
            key = args.get('key', '')
            if action == 'type' and (not isinstance(text, str) or not text or len(text) > 4000 or '\x00' in text):
                raise ComputerError('Type needs 1–4000 text characters without NUL.')
            if action == 'key' and (not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_+]{1,80}', key)):
                raise ComputerError('Key needs a named key or chord, such as Tab or Control+Return.')
            if action == 'key' and target['kind'] == 'browser':
                args['key'] = _browser_key(key)
            allowed = ({'click': {'element_id','x','y'}, 'drag': {'points','duration_ms'}, 'stroke': {'points','duration_ms'}, 'type': {'element_id','text'}, 'key': {'key'}, 'scroll': {'dx','dy'}}
                       if target['kind']=='browser' else
                       {'click': {'x','y'}, 'drag': {'points','duration_ms'}, 'stroke': {'points','duration_ms'}, 'type': {'text'}, 'key': {'key'}, 'scroll': {'dy'}, 'focus': set()})
            if action not in allowed or set(args)-allowed[action]:
                raise ComputerError('Unsupported action or unused parameters for this target; check the action schema.')
            if action=='scroll':
                self._deltas(args)
            coordinate = action in {'drag','stroke'} or (action=='click' and 'element_id' not in args)
            if action=='click' and 'element_id' in args and ({'x','y'} & set(args)):
                raise ComputerError('Use either element_id or x/y, not both.')
            if action in {'click','type'} and target['kind']=='browser' and not coordinate:
                entry=next((e for e in before['elements'] if e['id']==args.get('element_id')),None)
                if entry is None or entry['disabled'] or entry['id'] not in target.get('visible_ids',set()):
                    raise ComputerError('Choose an enabled element_id from the current observation.')
            if coordinate:
                points = self._points(before, target['kind'], action, args)
                if 'duration_ms' in args:
                    self._paced_plan(points, args['duration_ms'])
                pixels = await self._capture(target, before)
                if pixels is None or hashlib.sha256(pixels).hexdigest() != target.get('pixels_sha256'):
                    target.pop('observation', None)
                    raise ComputerError('Target pixels changed or unavailable; observe again.')
                if await self._state(target) != before:
                    target.pop('observation', None)
                    raise ComputerError('Target state changed during pixel check; observe again.')
            target.pop('observation', None)  # Consume before any possible side effect, including timeout.
            try:
                if target['kind'] == 'browser':
                    timing = await asyncio.wait_for(self._browser_action(target, before, action, args), 8)
                else:
                    # Preserve 4000-character input with conservative keystroke
                    # pacing and time for bounded per-chunk state checks.
                    timeout = 15 + len(text)*.012 + math.ceil(len(text)/128) if action == 'type' else 15
                    timing = await asyncio.wait_for(self._desktop_action(target, before, action, args), timeout)
            except Exception as exc:
                if target.get('breach'):   # found mid-dispatch, input already pressed released on the live page: closed now
                    await self._close_browser(target)
                raise ComputerError(f'Action outcome is uncertain: {type(exc).__name__}: {exc}. Observe before retrying; no action was replayed.') from exc
            target['dispatched'] = _DISPATCHED[action]   # what this call sent: said by any refusal the observation raises
            try:
                after = await self._observe(identifier, target)
            except Exception as exc:
                # Dispatch returned normally. A closed dialog or failed capture
                # must not turn that evidence into an invitation to replay input.
                target.pop('observation', None)
                target.pop('visible_ids', None)
                after = {'target_id': identifier, 'kind': target['kind'],
                         'observation_id': None, 'screenshot': None,
                         'observation_error': {'type': type(exc).__name__, 'message': str(exc)[:1000]},
                         'recovery': 'Do not replay the action. Observe this same target to verify the application outcome. '
                                     'If the window is gone, explicitly list windows and select/open the intended replacement; '
                                     'no target was switched automatically.',
                         'verification': 'Input dispatch completed; subsequent observation failed. Task success is unverified.'}
            finally:
                target.pop('dispatched', None)
            after['action_result'] = {'action': action, 'before_observation_id': observation,
                                      'before_state_sha256': _fingerprint(before), 'after_observation_id': after['observation_id'],
                                      'outcome': 'dispatch completed; verify the requested result',
                                      'source': 'computer adapter', 'task_success': 'unverified'}
            if 'observation_error' in after:
                after['action_result']['action_dispatched'] = True
            if timing is not None:
                after['action_result'].update(timing)
            return after

    async def _browser_action(self, target, state, action, args):
        page = target['page']
        if action in {'drag', 'stroke'} or (action == 'click' and 'element_id' not in args):
            points = self._points(state, 'browser', action, args)
            plan = self._paced_plan(points, args['duration_ms']) if 'duration_ms' in args else None
            hit = await page.evaluate_handle('([x,y])=>document.elementFromPoint(x,y)', points[0])
            try:
                # Every fresh input is preceded by a drain of the document checks, after the last await
                # before it (a document can arrive during any protocol wait); a breach found here leaves
                # the page open until what was pressed is released (close=False), act() closing it then.
                await self._refuse_breached(target, '', close=False)
                await page.mouse.move(*points[0])
                current = await self._state(target, 'The pointer was moved', close=False)
                if any(current[k] != state[k] for k in ('viewport', 'scroll', 'navigation', 'url', 'elements')):
                    raise ComputerError('Browser target or actionable controls changed before press.')
                if not await hit.evaluate('(e,[x,y])=>!!e && e.isConnected && document.elementFromPoint(x,y)===e', points[0]):
                    raise ComputerError('Coordinate target was replaced or overlaid before press; observe again.')
            finally:
                await hit.dispose()   # awaits a protocol reply: a document can arrive during it, so the last drain follows it
            async def check():
                current = await self._state(target, 'The button was pressed and is released', close=False)
                if any(current[k] != state[k] for k in ('viewport', 'scroll', 'navigation', 'url')):
                    raise ComputerError('Browser viewport or navigation changed during stroke.')
            # The last drain before the press, after the disposal's await and outside the press's try: a
            # refusal here sends no unmatched release, and says only that the pointer was moved.
            await self._refuse_breached(target, 'The pointer was moved', close=False)
            timing = None
            try:
                await page.mouse.down(button='left')
                if plan is not None:
                    timing = await self._paced_moves(plan, check, lambda point: page.mouse.move(*point))
                else:
                    for point in points[1:]:
                        # Pressing can change focus/DOM; never continue across viewport or navigation changes.
                        await check()
                        await page.mouse.move(*point)
            finally:
                await _release_input(page.mouse.up(button='left'))
            return timing
        elif action in {'click', 'type'}:
            entry = next((e for e in state['elements'] if e['id'] == args.get('element_id')), None)
            if entry is None:
                raise ComputerError('Choose an element_id from the current observation.')
            if entry['disabled']:
                raise ComputerError('Target control is disabled.')
            handle = target.get('elements', {}).get(entry['id'])
            element = handle.as_element() if handle is not None else None
            if element is None or not await element.evaluate('(e)=>e.isConnected'):
                raise ComputerError('Observed element was replaced; observe again.')
            await self._refuse_breached(target, '', close=False)   # after the await above, before the click or fill
            if action == 'click':
                await element.click(timeout=3000)
            else:
                await element.fill(args['text'], timeout=3000)
        elif action == 'key':
            attempted = []
            async def release_keys():
                failures = []
                for key in reversed(attempted):
                    try:
                        await page.keyboard.up(key)
                    except Exception as exc:
                        failures.append(exc)
                if failures:
                    raise failures[0]
            dispatch_error = None
            try:
                for key in args['key'].split('+'):
                    # before each press, after the previous one's await: a breach leaves the page open for the
                    # release below (close=False), act() closing it then
                    await self._refuse_breached(target, f"{'+'.join(attempted)} pressed" if attempted else '', close=False)
                    attempted.append(key)
                    await page.keyboard.down(key)
            except BaseException as exc:
                dispatch_error = exc
            try:
                await _release_input(release_keys())
            except BaseException as cleanup_error:
                if dispatch_error is None:
                    raise
                if isinstance(cleanup_error, asyncio.CancelledError):
                    cleanup_error.add_note(f'Keyboard dispatch also failed: {dispatch_error}')
                    raise cleanup_error from dispatch_error
                if isinstance(dispatch_error, asyncio.CancelledError):
                    dispatch_error.add_note(f'Keyboard release also failed: {cleanup_error}')
                    raise dispatch_error from cleanup_error
                raise ComputerError(f'Keyboard dispatch failed: {dispatch_error}; keyboard release also failed: {cleanup_error}') from dispatch_error
            if dispatch_error is not None:
                raise dispatch_error
        elif action == 'scroll':
            dx, dy = self._deltas(args)
            await self._refuse_breached(target, '', close=False)   # immediately before the wheel
            await page.mouse.wheel(dx, dy)
            await page.wait_for_timeout(100)
        else:
            raise ComputerError('Browser targets already have their own focus; focus is a desktop action.')

    @staticmethod
    def _points(state, kind, action, args):
        points = [{'x': args.get('x'), 'y': args.get('y')}] if action == 'click' else args.get('points')
        low, high = (1, 1) if action == 'click' else ((2, 2) if action == 'drag' else (2, 128))
        if not isinstance(points, list) or not low <= len(points) <= high:
            raise ComputerError(f'{action} needs {low}–{high} points.')
        width, height = state['viewport'] if kind == 'browser' else state['rect'][2:]
        if any(not isinstance(p, dict) or set(p) != {'x','y'} or
               type(p['x']) is not int or type(p['y']) is not int or
               not 0 <= p['x'] < width or not 0 <= p['y'] < height for p in points):
            raise ComputerError('All points need integer x/y inside the observed viewport/window.')
        return [(p['x'], p['y']) for p in points]

    @staticmethod
    def _paced_plan(points, duration_ms):
        if type(duration_ms) is not int or not 1 <= duration_ms <= 2000:
            raise ComputerError('duration_ms needs an integer from 1 to 2000 for drag/stroke.')
        duration = duration_ms / 1000
        lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
        total = sum(lengths)
        if not total:
            count = math.ceil(duration / .02)
            return [(duration*i/count, points[0]) for i in range(count+1)]
        segments = []
        for a, b, length in zip(points, points[1:], lengths):
            seconds = duration * length / total
            # Two-pixel unrounded steps keep rounded native moves within four pixels.
            count = max(1, math.ceil(length/2), math.ceil(seconds/.02))
            segments.append((a, b, seconds, count))
        if 1 + sum(n for _, _, _, n in segments) > 256:
            raise ComputerError('Paced stroke needs too many subdivisions (maximum 256 points); use a shorter path.')
        plan = [(0, points[0])]
        elapsed = 0
        for a, b, seconds, count in segments:
            for i in range(1, count+1):
                point = b if i == count else tuple(round(x+(y-x)*i/count) for x,y in zip(a,b))
                plan.append((elapsed+seconds*i/count, point))
            elapsed += seconds
        plan[-1] = (duration, points[-1])
        return plan

    @staticmethod
    async def _paced_moves(plan, check, move):
        started = time.monotonic()
        for scheduled, point in plan[1:]:
            await check()
            await asyncio.sleep(max(0, started+scheduled-time.monotonic()))
            await check()
            await move(point)
        await check()
        return {'requested_duration_ms': round(plan[-1][0]*1000),
                'actual_hold_ms': round((time.monotonic()-started)*1000, 1)}

    @staticmethod
    def _deltas(args):
        dx, dy = args.get('dx', 0), args.get('dy', 0)
        if any(type(n) is not int or abs(n) > 2000 for n in (dx, dy)) or not (dx or dy):
            raise ComputerError('Scroll needs integer dx/dy within ±2000 and a nonzero delta.')
        return dx, dy

    async def _revalidate(self, target, state, action, moment, sent=''):
        """The native state read again and every security predicate re-applied immediately before input
        goes out -- and at each check during a stroke or between chunks of typing: the window behind the
        ID may have become Dream's own since the action was authorized (an approval card's window: never
        driven, not even focused), a terminal or a window of unknown ownership (view-only: no input;
        focusing it, to read it, is allowed), or another window altogether -- its focus, geometry, title
        or identity (pid, WM_CLASS) not the one observed. `sent` names what has already gone out at this
        point ('' for nothing), so the refusal tells the truth about it. Nothing goes out -- not even a
        pointer move -- before the first check passes."""
        current = await self._desktop_state(target)
        nothing = f'{sent}; nothing further dispatched.' if sent else 'No input dispatched.'
        if _own_window(current):
            raise ComputerError(f"Dream's own window is not a target ({moment}): the model would be driving its own "
                                f"approval cards. {nothing}")
        if action != 'focus' and current.get('view_only', True):
            raise ComputerError(f'Window became view-only {moment} (a terminal, or ownership unknown). {nothing}')
        changed = [k for k in ('focus', 'rect', 'title', 'pid', 'wm_class') if current.get(k) != state.get(k)]
        if changed:
            raise ComputerError(f"Desktop {', '.join(changed)} changed {moment}. {nothing} Observe again.")

    async def _desktop_action(self, target, state, action, args):
        binary = shutil.which('xdotool')
        window = target['window_id']
        if action == 'focus':
            await self._revalidate(target, state, action, 'before focusing')
            await _command(binary, 'windowactivate', '--sync', window)
            return
        if state['focus'] != window:
            raise ComputerError('Selected window is not focused. Focus it explicitly, then observe.')
        if action in {'drag', 'stroke'}:
            points = self._points(state, 'desktop', action, args)
            plan = self._paced_plan(points, args['duration_ms']) if 'duration_ms' in args else None
            async def check(sent='The button was pressed and is released'):
                await self._revalidate(target, state, action, 'during stroke', sent)
            await check('')
            await _command(binary, 'mousemove', '--window', window, *map(str, points[0]))
            await check('The pointer was moved')
            timing = None
            try:
                await _command(binary, 'mousedown', '1')
                if plan is not None:
                    async def move(point):
                        await _command(binary, 'mousemove', '--window', window, *map(str, point))
                    timing = await self._paced_moves(plan, check, move)
                else:
                    for point in points[1:]:
                        await check()
                        await _command(binary, 'mousemove', '--window', window, *map(str, point))
                    await check()
            finally:
                await _release_input(_command(binary, 'mouseup', '1'))
            return timing
        elif action == 'click':
            x, y = args.get('x'), args.get('y')
            if type(x) is not int or type(y) is not int or not 0 <= x < state['rect'][2] or not 0 <= y < state['rect'][3]:
                raise ComputerError('Click needs integer x/y within the observed window.')
            await self._revalidate(target, state, action, 'before the pointer moves')
            await _command(binary, 'mousemove', '--window', window, str(x), str(y))
            await self._revalidate(target, state, action, 'before click', 'The pointer was moved')
            await _command(binary, 'click', '1')
        elif action == 'type':
            async def check_typing(sent=''):
                await self._revalidate(target, state, action, 'during typing', sent)
            await check_typing()
            for offset in range(0, len(args['text']), 128):
                try:
                    # Finish the current small chunk on Stop, allowing xdotool
                    # to release generated keys and restore cleared modifiers.
                    # Subprocess/server failures cannot guarantee that cleanup.
                    await _release_input(_command(binary, 'type', '--clearmodifiers', '--delay', '12', '--', args['text'][offset:offset+128]))
                except asyncio.CancelledError as exc:
                    exc.add_note('Native typing cancelled after the current chunk; partial text may remain. Observe before retrying.')
                    raise
                except Exception as exc:
                    raise ComputerError(f'Native typing failed; partial text or held keys/modifiers may remain; cleanup could not be confirmed: {exc}') from exc
                await check_typing('Text was typed so far (partial text may remain)')
        elif action == 'key':
            await self._revalidate(target, state, action, 'before key dispatch')
            await _command(binary, 'key', '--clearmodifiers', args['key'])
        elif action == 'scroll':
            dx, dy = self._deltas(args)
            if dx:
                raise ComputerError('Desktop scrolling supports vertical dy only.')
            await self._revalidate(target, state, action, 'before the pointer moves')
            await _command(binary, 'mousemove', '--window', window, str(state['rect'][2]//2), str(state['rect'][3]//2))
            await self._revalidate(target, state, action, 'before scrolling', 'The pointer was moved')
            await _command(binary, 'click', '--repeat', str(min(20, max(1, abs(dy)//100))), '5' if dy > 0 else '4')

    async def close(self, identifier):
        async with self._lock:
            target = self._target(identifier)
            await self._release_elements(target)
            if target['kind'] == 'browser':
                await self._close_browser(target)
            del self.targets[identifier]
            return {'closed': identifier, 'note': 'Detached only; the desktop application stays open.' if target['kind']=='desktop' else 'Owned browser context closed.'}

    async def aclose(self):
        async with self._lock:
            for target in self.targets.values():
                if target.get('boundary'):
                    target['boundary'].close()
            self.targets.clear()
            try:
                if self._browser is not None:
                    await self._browser.close()
            finally:
                self._browser = None
                if self._pw is not None:
                    await self._pw.stop()
                    self._pw = None
