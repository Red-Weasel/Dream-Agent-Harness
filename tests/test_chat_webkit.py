"""Dream Chat in the desktop's real web view (DREAM-229): the Chat page loaded by the real `DreamWindow` in its
WebKitGTK view, at the window's default size, with a scripted conversation. Batch B1 was built and tested in
Chromium only; this proves the same layout, the code block's Copy and the composer in the engine the owner sees.

How it runs: the test starts a private headless GTK display (`broadwayd`, its sockets in a private runtime
directory, no TCP port) and, on it, this file as a small driver process (`--driver`): the real window, its
`webview(local_only=True)` view, and one JSON command per line on stdin (evaluate, click, snapshot, clipboard).
Clicks are GDK button events delivered to the view, the path a mouse click takes, so the page sees a user gesture.
Nothing opens on the owner's display and the owner's clipboard is not touched. No model, no engine.

System GTK is optional in pytest's venv: without the GI bindings or `broadwayd` the tests skip. Run with the
system Python (`run-sys.sh`), like tests/test_desktop_*.py."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
MARK = '@@wk '
SHOTS = os.environ.get('DREAM_CHAT_WEBKIT_SHOTS')   # a folder: the tests also save their snapshots there
X11 = os.environ.get('DREAM_CHAT_WEBKIT_X11')       # an X display (":99", a nested or virtual server) to use instead of broadwayd


# ---------------------------------------------------------------------------------------------------------------
# The driver process (system Python, GTK main loop). Everything below `driver()` runs only under `--driver`.
def driver():
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')
    gi.require_version('WebKit2', '4.1')
    from gi.repository import Gdk, GLib, Gtk, WebKit2
    import cairo

    out = os.fdopen(os.dup(1), 'w')          # replies go to the real stdout; stray prints go to stderr
    os.dup2(2, 1)

    def reply(**data):
        out.write(MARK + json.dumps(data) + '\n'); out.flush()

    if not Gtk.init_check()[0]:
        reply(error='no display'); return 3
    # A display without a resolution (broadway) reports -1 dpi, and WebKit scales the page by dpi / 96.
    screen = Gdk.Screen.get_default()
    if screen.get_resolution() <= 0:
        screen.set_resolution(96)
    sys.path.insert(0, str(ROOT))
    from dream.desktop.window import DreamWindow
    workspace = tempfile.mkdtemp(prefix='dream-wk-ws-')
    window = DreamWindow(sys.executable, workspace, [], autostart=False)   # no session, no terminal child
    view = window.studio
    pointer = Gdk.Display.get_default().get_default_seat().get_pointer()

    def mouse(kind, x, y):
        event = Gdk.Event.new(kind)
        event.window = view.get_window()
        event.send_event = True
        event.time = GLib.get_monotonic_time() // 1000
        event.x, event.y = float(x), float(y)
        origin = view.get_window().get_origin()
        event.x_root, event.y_root = origin.x + float(x), origin.y + float(y)
        event.state = Gdk.ModifierType.BUTTON1_MASK if kind == Gdk.EventType.BUTTON_RELEASE else 0
        if kind != Gdk.EventType.MOTION_NOTIFY:
            event.button = 1
        event.set_device(pointer)
        Gtk.main_do_event(event)

    def command(message):
        op = message['op']
        if op == 'connect':
            # What DreamWindow._polled does when the session answers, without a session process.
            window.address_info = (message['base'], message['token'])
            window.session_id = message.get('session_id', '')
            window.connected = True
            seen = []
            def loaded(_view, event):
                if event == WebKit2.LoadEvent.FINISHED and not seen:
                    seen.append(1)
                    reply(ok=True, uri=view.get_uri())
            view.connect('load-changed', loaded)
            window._connect_studio()
        elif op == 'eval':
            def done(_view, result, _data):
                try:
                    value = view.call_async_javascript_function_finish(result)
                    reply(value=json.loads(value.to_string()))
                except Exception as exc:   # a script error is the caller's to see
                    reply(error=str(exc))
            view.call_async_javascript_function(
                'return JSON.stringify((await (' + message['js'] + ')) ?? null);', -1, None, None, None, None, done, None)
        elif op == 'click':
            mouse(Gdk.EventType.MOTION_NOTIFY, message['x'], message['y'])
            mouse(Gdk.EventType.BUTTON_PRESS, message['x'], message['y'])
            def release(*_):
                mouse(Gdk.EventType.BUTTON_RELEASE, message['x'], message['y'])
                reply(ok=True)
            during = message.get('during')
            if during == 'poll':
                # The window's own 700 ms poll (DreamWindow._sync_web_view) evaluating its script while the button is down.
                def poll():
                    if window.reading_web_view:
                        return True
                    window._sync_web_view()
                    GLib.timeout_add(10, lambda: window.reading_web_view or release())
                    return False
                GLib.timeout_add(10, poll)
            else:
                release()
        elif op == 'geometry':
            a, w, origin = view.get_allocation(), window.get_allocation(), view.get_window().get_origin()
            reply(view=[a.width, a.height], window=[w.width, w.height], origin=[origin.x, origin.y],
                  trusted=window._trusted_studio_document(),
                  settings={'javascript_can_access_clipboard': view.get_settings().get_javascript_can_access_clipboard()},
                  webkit=[WebKit2.get_major_version(), WebKit2.get_minor_version(), WebKit2.get_micro_version()])
        elif op == 'snapshot':
            def done(_view, result, _data):
                try:
                    surface = view.get_snapshot_finish(result)
                    if message.get('path'):
                        surface.write_to_png(message['path'])
                    pixels = []
                    for x, y in message.get('pixels', []):     # what was painted there: [r, g, b]
                        at = int(y) * surface.get_stride() + int(x) * 4
                        b, g, r = surface.get_data()[at:at + 3]
                        pixels.append([r, g, b])
                    reply(ok=True, size=[surface.get_width(), surface.get_height()], pixels=pixels)
                except Exception as exc:
                    reply(error=str(exc))
            view.get_snapshot(WebKit2.SnapshotRegion.VISIBLE, WebKit2.SnapshotOptions.NONE, None, done, None)
        elif op == 'window_png':
            # The whole native window (the rail, the top strip, the web view) drawn into an image.
            a = window.get_allocation()
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, a.width, a.height)
            window.draw(cairo.Context(surface))
            surface.write_to_png(message['path'])
            reply(ok=True, size=[a.width, a.height])
        elif op == 'focus':
            # A headless display has no window manager to make the window the active one.
            event = Gdk.Event.new(Gdk.EventType.FOCUS_CHANGE)
            event.window = window.get_window()
            event.send_event = True
            event.in_ = 1
            Gtk.main_do_event(event)
            view.grab_focus()
            reply(ok=True, active=window.is_active(), focus=view.has_focus())
        elif op == 'resize':
            window.resize(message['width'], message['height'])
            reply(ok=True)
        elif op == 'clipboard':
            reply(text=Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text())
        elif op == 'quit':
            reply(ok=True)
            window.destroy()                 # its own handler removes the private folder and ends the main loop
        else:
            reply(error='unknown op ' + op)

    pending = b''
    def readable(_fd, condition):
        nonlocal pending
        data = os.read(0, 65536)
        if not data:
            Gtk.main_quit(); return False
        pending += data
        while b'\n' in pending:
            line, pending = pending.split(b'\n', 1)
            try:
                command(json.loads(line))
            except Exception as exc:
                reply(error=f'{type(exc).__name__}: {exc}')
        return True
    GLib.io_add_watch(0, GLib.IO_IN | GLib.IO_HUP, readable)
    reply(ready=True)
    Gtk.main()
    shutil.rmtree(workspace, ignore_errors=True)
    return 0


if __name__ == '__main__' and sys.argv[1:] == ['--driver']:
    sys.exit(driver())


# ---------------------------------------------------------------------------------------------------------------
# The tests.
from dream.core.backends.base import Event   # noqa: E402
from dream.gui.bus import EventBus           # noqa: E402
from dream.gui.server import StudioServer    # noqa: E402

pytestmark = pytest.mark.skipif(importlib.util.find_spec('gi') is None or shutil.which('broadwayd') is None,
                                reason='System GTK/WebKit bindings and broadwayd required')

CODE = 'def greet(name: str) -> str:\n    return f"<b>Hello & welcome, {name}</b>"'
REPLY = ('## Release summary\n\nThe build is **ready**; two notes, one ~~dropped~~ and one kept, '
         'and the guide is at https://example.org/guide.\n\n'
         '| Model | Prefill | Decode |\n|:------|--------:|:------:|\n| Flash | 686 tok/s | `26.9` |\n| Large | 300 tok/s | 12.0 |\n\n'
         '1. Install the package\n2. Configure it\n   - set the port\n   - set the host\n3. Run the checks\n\n'
         f'```python\n{CODE}\n```\n\n> Quoted: keep the old behaviour.\n\n---\n\nA closing paragraph, long enough to wrap: '
         + 'the column keeps one readable measure. ' * 8)


class WebKit:
    """The driver process, one JSON command at a time."""
    def __init__(self, process):
        self.process = process

    async def call(self, op, **arguments):
        self.process.stdin.write((json.dumps({'op': op, **arguments}) + '\n').encode())
        await self.process.stdin.drain()
        return await self.answer()

    async def answer(self, timeout=30):
        while True:
            line = await asyncio.wait_for(self.process.stdout.readline(), timeout)
            if not line:
                raise RuntimeError('the WebKit driver exited')
            text = line.decode(errors='replace')
            if text.startswith(MARK):
                data = json.loads(text[len(MARK):])
                if 'error' in data:
                    raise RuntimeError(data['error'])
                return data

    async def js(self, expression):
        return (await self.call('eval', js=expression))['value']

    async def wait(self, expression, timeout=8.0):
        """Poll until the expression is truthy; its value."""
        end = asyncio.get_event_loop().time() + timeout
        while True:
            value = await self.js(expression)
            if value:
                return value
            if asyncio.get_event_loop().time() > end:
                raise AssertionError(f'never true in the web view: {expression}')
            await asyncio.sleep(0.05)

    async def centre(self, selector):
        box = await self.js(f"(() => {{ const e = document.querySelector({json.dumps(selector)}); e.scrollIntoView({{block:'nearest'}});"
                            " const r = e.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()")
        return box

    async def click(self, selector, during=None):
        """A mouse click (GDK button events) on the element's centre. `during='poll'` runs the window's own 700 ms
        poll, which evaluates a script in the page, while the button is down."""
        x, y = await self.centre(selector)
        # A script the native side evaluates takes the page's user activation away when it ends (WebKitGTK 2.52);
        # one landing between a press and its click is the case the `during` tests make on purpose, so the
        # evaluations around an ordinary click keep their distance from it.
        await asyncio.sleep(0.2)
        await self.call('click', x=x, y=y, **({'during': during} if during else {}))
        await asyncio.sleep(0.3)

    async def shot(self, name, tmp_path):
        target = Path(SHOTS or tmp_path) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        await self.call('snapshot', path=str(target))
        return target


@pytest.fixture
async def desktop(monkeypatch):
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    prompts, controls = [], []
    srv = StudioServer(EventBus(), on_prompt=prompts.append, on_control=controls.append,
                       session={'provider': 'test', 'model': 'fixture', 'session_id': 'chat-test'})
    url = await srv.start()
    runtime = tempfile.mkdtemp(prefix='dream-wk-')    # short: a unix socket path has 108 characters
    os.chmod(runtime, 0o700)
    env = {'HOME': os.environ.get('HOME', runtime), 'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8',
           'XDG_RUNTIME_DIR': runtime, 'GDK_BACKEND': 'broadway', 'BROADWAY_DISPLAY': ':5', 'NO_AT_BRIDGE': '1',
           'PYTHONPATH': os.environ.get('PYTHONPATH', ''), 'PYTHONDONTWRITEBYTECODE': '1'}
    log = open(Path(runtime) / 'driver.log', 'wb')
    display = process = None
    try:
        if X11:
            env.update(GDK_BACKEND='x11', DISPLAY=X11)
        else:
            display = subprocess.Popen(['broadwayd', '--unixsocket', str(Path(runtime) / 'http.socket'), ':5'],
                                       env=env, stdout=log, stderr=log)
            for _ in range(100):                       # its client socket is an abstract one: the log line says it listens
                if b'Listening on' in Path(log.name).read_bytes():
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.skip('broadwayd did not start')
        process = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).resolve()), '--driver',
                                                       env=env, stdin=asyncio.subprocess.PIPE,
                                                       stdout=asyncio.subprocess.PIPE, stderr=log)
        wk = WebKit(process)
        await wk.answer()
        base, _, token = url.partition('/#token=')
        await wk.call('connect', base=base, token=token, session_id='chat-test')
        await wk.call('focus')
        await wk.wait("document.getElementById('stat')?.textContent === 'Ready' && document.documentElement.classList.contains('dream-native')")
        yield srv, wk, prompts, controls
    except BaseException:
        log.flush()
        sys.stderr.write(Path(log.name).read_text(errors='replace')[-4000:])
        raise
    finally:
        if process is not None:
            try:
                await asyncio.wait_for(wk.call('quit'), 5)
                await asyncio.wait_for(process.wait(), 5)
            except Exception:
                process.kill()
        if display is not None:
            display.terminate(); display.wait(5)
        log.close()
        await srv.stop()
        shutil.rmtree(runtime, ignore_errors=True)


GEOMETRY = """(() => {
  const box = el => { const r = el.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height, r: r.right}; };
  const main = document.getElementById('main');
  return {col: box(document.getElementById('stream')), pane: box(main), composer: box(document.querySelector('footer .composer')),
          inner: [innerWidth, innerHeight], paneScrolls: main.scrollWidth > main.clientWidth,
          pageScrolls: document.documentElement.scrollWidth > innerWidth};
})()"""


async def converse(srv, wk, running=False):
    await srv._accept_prompt('Summarise the release for me.')
    srv.bus.publish(Event('text_delta', REPLY))
    srv.bus.publish(Event('result', {}))
    await wk.wait("document.querySelectorAll('#stream .msg.dream').length === 1 && !document.querySelector('#stream .cursor')")
    if running:
        await srv._accept_prompt('And the next step?')
        srv.bus.publish(Event('turn_start', {}))
        srv.bus.publish(Event('text_delta', 'The next step is to run the **checks** and then'))
        await wk.wait("document.querySelectorAll('#stream .msg.dream').length === 2 && !!document.querySelector('#stream .cursor')")


async def test_the_real_window_loads_chat_at_its_default_size(desktop, tmp_path):
    srv, wk, _, _ = desktop
    g = await wk.call('geometry')
    assert g['trusted'], g                                  # the window recognises its own document (dream-native is dispatched)
    assert g['window'][0] >= 1600 and g['window'][1] >= 1000, g   # DreamWindow's default size
    assert g['view'][0] >= 1000, g                          # the web view beside the native rail
    inner = await wk.js('[innerWidth, innerHeight]')
    assert inner == g['view'], (inner, g)                   # one CSS px is one device px: the page is not scaled
    support = await wk.js("({has: CSS.supports('selector(:has(a))'), mix: CSS.supports('color', 'color-mix(in srgb, red 50%, transparent)'),"
                          " gutter: CSS.supports('scrollbar-gutter', 'stable both-edges'), mask: CSS.supports('mask', 'url(x) center/contain no-repeat'),"
                          " is: CSS.supports('selector(:is(a))')})")
    assert support == {'has': True, 'mix': True, 'gutter': True, 'mask': True, 'is': True}, support
    await wk.shot('empty.png', tmp_path)
    await wk.call('window_png', path=str(Path(SHOTS or tmp_path) / 'window-empty.png'))


async def test_column_bubble_markdown_and_composer_in_webkit(desktop, tmp_path):
    srv, wk, _, _ = desktop
    await converse(srv, wk)
    g = await wk.js(GEOMETRY)
    col, pane, composer = g['col'], g['pane'], g['composer']
    assert abs(col['w'] - 960) <= 1, col                                                       # the column's cap (768 until DREAM-240)
    assert abs((col['x'] + col['w'] / 2) - (pane['x'] + pane['w'] / 2)) <= 1.5, (col, pane)    # centered in the pane
    assert abs(composer['w'] - 960) <= 1 and abs(composer['x'] - col['x']) <= 1.5, (composer, col)   # the composer shares it
    assert not g['pageScrolls'] and not g['paneScrolls'], g                                    # no sideways scroll
    you = await wk.js("""(() => { const m = document.querySelector('#stream .msg.you'), b = m.querySelector('.body').getBoundingClientRect(),
        s = getComputedStyle(m.querySelector('.body')), w = m.querySelector('.who').getBoundingClientRect();
        return {right: b.right, left: b.x, w: b.width, radius: parseFloat(s.borderTopLeftRadius), bg: s.backgroundColor, who: [w.width, w.height]}; })()""")
    # DREAM-240, the Nested view's look: the owner's message is plain text under its name, from the column's left edge.
    assert abs(you['left'] - col['x']) <= 1 and you['right'] <= col['r'] + 0.5, (you, col)
    assert you['bg'] == 'rgba(0, 0, 0, 0)' and you['radius'] == 0 and you['who'][1] >= 12, you  # no bubble; the name is shown
    dream = await wk.js("""(() => { const m = document.querySelector('#stream .msg.dream'), s = getComputedStyle(m), b = m.querySelector('.body');
        return {x: b.getBoundingClientRect().x, bg: s.backgroundColor, border: s.borderTopWidth, shadow: s.boxShadow,
                tags: ['h2', 'table', 'ol', 'ol ul', 'del', 'a', 'blockquote', 'hr', '.codeblock'].map(t => b.querySelectorAll(t).length),
                list: getComputedStyle(b.querySelector('ol')).listStyleType, tableInside: b.querySelector('.md-table').getBoundingClientRect().right}; })()""")
    assert abs(dream['x'] - col['x']) <= 1 and dream['bg'] == 'rgba(0, 0, 0, 0)' and dream['border'] == '0px' and dream['shadow'] == 'none', dream
    assert dream['tags'] == [1, 1, 1, 1, 1, 1, 1, 1, 1] and dream['list'] == 'decimal', dream  # the markdown renders
    assert dream['tableInside'] <= col['r'] + 0.5, (dream, col)
    code = await wk.js("""(() => { const c = document.querySelector('#stream .codeblock'), h = c.querySelector('.codehead').getBoundingClientRect(),
        p = c.querySelector('pre').getBoundingClientRect(), b = c.querySelector('.codecopy'), r = b.getBoundingClientRect();
        return {lang: c.querySelector('.codelang').textContent, copy: b.textContent, button: [r.width, r.height], headBottom: h.bottom, preTop: p.y,
                right: c.getBoundingClientRect().right, headDisplay: getComputedStyle(c.querySelector('.codehead')).display}; })()""")
    assert code['lang'] == 'python' and code['copy'] == 'Copy' and code['headDisplay'] == 'flex', code   # the header and its Copy
    assert code['button'][0] >= 30 and code['button'][1] >= 24 and code['headBottom'] <= code['preTop'] + 0.5, code
    assert code['right'] <= col['r'] + 0.5, (code, col)
    send = await wk.js("""(() => { const b = document.getElementById('send'), r = b.getBoundingClientRect(), s = getComputedStyle(b),
        i = getComputedStyle(b, '::before'), a = document.getElementById('attach'), ar = a.getBoundingClientRect(), box = document.querySelector('.composer').getBoundingClientRect();
        return {w: r.width, h: r.height, radius: s.borderTopLeftRadius, bg: s.backgroundColor, font: s.fontSize, text: b.textContent, hidden: b.hidden,
                icon: [i.width, i.height, i.maskImage || i.webkitMaskImage], attach: [ar.width, ar.height, ar.x > box.x, ar.right < box.right],
                attachIcon: getComputedStyle(a, '::before').maskImage || getComputedStyle(a, '::before').webkitMaskImage,
                right: box.right - r.right, stopHidden: document.getElementById('stop').hidden}; })()""")
    assert (send['h'], send['radius'], send['text']) == (32, '2px', 'Send') and send['w'] >= 52 and not send['hidden'], send   # Send says Send (DREAM-240)
    assert send['bg'] not in ('rgba(0, 0, 0, 0)', 'transparent') and send['font'] == '14px', send
    assert send['attach'][:2] == [32, 32] and send['attach'][2] and send['attach'][3] and 'svg' in send['attachIcon'], send   # "+" keeps its mask
    assert 0 <= send['right'] <= 12 and send['stopHidden'], send
    await wk.shot('markdown.png', tmp_path)
    await wk.js("(() => { document.getElementById('main').scrollTop = 0; return true; })()")
    await wk.shot('markdown-top.png', tmp_path)


WATCH_CLIPBOARD = """(() => { window.__clip = []; const c = navigator.clipboard, write = c.writeText.bind(c), exec = document.execCommand.bind(document);
    c.writeText = t => write(t).then(() => { __clip.push('clipboard.writeText'); }, e => { __clip.push('refused: ' + e.name); throw e; });
    document.execCommand = (...a) => { const ok = exec(...a); __clip.push('execCommand: ' + ok); return ok; }; return true; })()"""


async def copy_click(wk, during=None):
    """Click the code block's Copy; the label it showed, the system clipboard, and the way the page got there."""
    await wk.js(WATCH_CLIPBOARD)
    await wk.click('#stream .codeblock .codecopy', during=during)
    label = await wk.wait("(t => t !== 'Copy' && t)(document.querySelector('#stream .codecopy').textContent)")
    return label, (await wk.call('clipboard'))['text'], await wk.js('window.__clip')


async def test_copy_click_in_the_desktop_view(desktop, tmp_path):
    """The desktop's web view allows the page's asynchronous clipboard write inside a click, with
    `javascript-can-access-clipboard` off: no WebKit setting and no native bridge is needed."""
    srv, wk, _, _ = desktop
    await converse(srv, wk)
    settings = (await wk.call('geometry'))['settings']
    assert settings == {'javascript_can_access_clipboard': False}, settings   # the clipboard is not open to scripts
    label, clip, path = await copy_click(wk)
    print('WEBKIT COPY:', json.dumps({'label': label, 'clipboard_matches': clip == CODE, 'path': path, 'settings': settings}))
    assert label == 'Copied', (label, path)                 # a click copies
    assert clip == CODE, clip                               # exactly the code, on the system clipboard
    # The window's poll can land inside any click (the next test makes it); either way is a copy.
    assert path in (['clipboard.writeText'], ['refused: NotAllowedError', 'execCommand: true']), path
    await wk.shot('copied.png', tmp_path)
    await wk.wait("document.querySelector('#stream .codecopy').textContent === 'Copy'", timeout=4)   # the label returns


@pytest.mark.parametrize('n', range(3))
async def test_copy_survives_the_window_poll_between_press_and_click(desktop, n):
    """WebKitGTK takes the page's user activation away when a script the native side evaluated ends. The window
    evaluates one every 700 ms (`DreamWindow._sync_web_view`); when it lands between a mouse press and its click,
    `navigator.clipboard.writeText` is refused, and the page's fallback inside the same click still copies."""
    srv, wk, _, _ = desktop
    await converse(srv, wk)
    label, clip, path = await copy_click(wk, during='poll')
    print('WEBKIT COPY during the poll:', json.dumps({'label': label, 'clipboard_matches': clip == CODE, 'path': path}))
    assert label == 'Copied' and clip == CODE, (label, clip, path)
    assert path == ['refused: NotAllowedError', 'execCommand: true'], path   # the refusal, then the fallback


async def test_stop_is_the_round_button_while_a_turn_runs(desktop, tmp_path):
    srv, wk, _, controls = desktop
    await converse(srv, wk, running=True)
    busy = await wk.js("""(() => { const box = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
            return {w: r.width, h: r.height, x: r.x, radius: s.borderTopLeftRadius, text: e.textContent, shown: !e.hidden && r.width > 0}; };
        return {stop: box(document.getElementById('stop')), send: box(document.getElementById('send')), steer: box(document.getElementById('steer')),
                progress: document.getElementById('chat-progress').hidden, cursor: document.querySelectorAll('#stream .cursor').length}; })()""")
    # DREAM-237: at the desktop's width Stop says Stop beside its square (the round icon was missed); DREAM-240: the
    # Nested view's 32 px button with the box's inner corner.
    assert busy['stop']['w'] >= 66 and (busy['stop']['h'], busy['stop']['radius'], busy['stop']['text']) == (32, '2px', 'Stop'), busy
    assert busy['send']['text'] == 'Queue' and busy['send']['shown'] and busy['steer']['shown'], busy       # Queue and Steer stay
    assert busy['send']['x'] < busy['steer']['x'] < busy['stop']['x'] and not busy['progress'] and busy['cursor'] == 1, busy
    g = await wk.js(GEOMETRY)
    assert not g['pageScrolls'] and not g['paneScrolls'], g
    await wk.shot('streaming.png', tmp_path)
    await wk.call('window_png', path=str(Path(SHOTS or tmp_path) / 'window-streaming.png'))
    await wk.click('#stop')                                 # a real click on Stop reaches Dream as an interrupt
    for _ in range(100):
        if {'action': 'interrupt'} in controls:
            break
        await asyncio.sleep(0.05)
    assert {'action': 'interrupt'} in controls, controls


async def test_selects_are_drawn_flat_like_the_other_controls(desktop, tmp_path):
    """WebKitGTK paints a themed menu button (grey, about 29 px tall) for a select whatever its background says;
    chat.css draws the two selects of the Chat view itself, so the row under the composer keeps its one 24 px line."""
    srv, wk, _, _ = desktop
    await converse(srv, wk)
    await wk.js("(() => { document.getElementById('main').scrollTop = 0; return true; })()")
    await wk.wait("document.getElementById('preset-select').options.length > 0")
    info = await wk.js("""(() => { const box = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
            return {x: r.x, y: r.y, w: r.width, h: r.height, appearance: s.appearance, bg: s.backgroundColor, image: s.backgroundImage.slice(0, 24)}; };
        return {preset: box(document.getElementById('preset-select')), detail: box(document.getElementById('feed-detail')),
                row: box(document.querySelector('footer .hint')), mode: box(document.getElementById('chat-mode'))}; })()""")
    preset, detail, row = info['preset'], info['detail'], info['row']
    assert preset['appearance'] == 'none' and detail['appearance'] == 'none', info
    assert preset['h'] <= 24 and row['h'] <= 24.5 and detail['h'] <= 32, info                 # the row is one line of 24 px controls
    assert 'svg' in preset['image'] and 'svg' in detail['image'], info                        # each keeps a chevron
    # What was painted: inside each select, left of its text, and beside it.
    points = [[preset['x'] + 2, preset['y'] + preset['h'] / 2], [row['x'] + 3, row['y'] + 2],
              [detail['x'] + 3, detail['y'] + detail['h'] / 2]]
    painted = (await wk.call('snapshot', pixels=points))['pixels']
    near = lambda a, b: all(abs(x - y) <= 6 for x, y in zip(a, b))
    assert near(painted[0], painted[1]), painted                                              # Preset: the row's own surface, no grey fill
    assert near(painted[2], [28, 26, 33]), painted                                            # Feed detail: the Nested palette's raised surface (DREAM-240)
    await wk.shot('selects.png', tmp_path)


async def test_no_sideways_scroll_at_the_window_minimum_size(desktop, tmp_path):
    srv, wk, _, _ = desktop
    await converse(srv, wk, running=True)
    await wk.call('resize', width=720, height=520)                                            # DreamWindow's size request
    width = await wk.wait('innerWidth < 700 && innerWidth')
    g = await wk.js(GEOMETRY)
    assert not g['pageScrolls'] and not g['paneScrolls'], g
    assert g['col']['x'] >= 0 and g['col']['r'] <= width and g['composer']['x'] >= 0 and g['composer']['r'] <= width, g
    buttons = await wk.js("['attach', 'send', 'steer', 'stop'].map(id => { const r = document.getElementById(id).getBoundingClientRect(); return [r.x, r.right, r.width]; })")
    assert all(x >= g['composer']['x'] and right <= g['composer']['r'] and w > 0 for x, right, w in buttons), (buttons, g)
    await wk.shot('minimum-size-streaming.png', tmp_path)


async def test_streaming_polish_in_webkit(desktop, tmp_path):
    """Batch B2 in the desktop's engine: the cursor at the end of the text, "Jump to latest" while scrolled up,
    a real click on it, and the Stopped mark."""
    srv, wk, _, _ = desktop
    await converse(srv, wk, running=True)
    cursor = await wk.js("""(() => { const c = document.querySelector('#stream .cursor'), r = c.getBoundingClientRect(), p = c.parentElement.getBoundingClientRect();
        return {host: c.parentElement.tagName, x: r.x, hostX: p.x, top: r.y, bottom: r.bottom, hostTop: p.y, hostBottom: p.bottom}; })()""")
    assert cursor['host'] == 'P' and cursor['x'] > cursor['hostX'] + 100, cursor                 # after the last word,
    assert cursor['top'] >= cursor['hostTop'] - 1 and cursor['bottom'] <= cursor['hostBottom'] + 1, cursor   # on its line
    assert await wk.js("document.getElementById('to-latest').hidden")
    srv.bus.publish(Event('text_delta', ' the report.\n\n' + 'Another paragraph of the running reply. ' * 120))
    await wk.wait("document.querySelector('#stream').textContent.includes('the report.')")
    await wk.wait("(m => m.scrollHeight - m.scrollTop - m.clientHeight <= 2)(document.getElementById('main'))")   # it follows
    await wk.js("(() => { document.getElementById('main').scrollTop -= 500; return true; })()")              # the reader moves up
    await wk.wait("!document.getElementById('to-latest').hidden")
    jump = await wk.js("""(() => { const b = document.getElementById('to-latest'), r = b.getBoundingClientRect(), s = getComputedStyle(b),
        c = document.querySelector('footer .composer').getBoundingClientRect(), i = getComputedStyle(b, '::before');
        return {w: r.width, h: r.height, radius: s.borderTopLeftRadius, mid: r.x + r.width / 2, composerMid: c.x + c.width / 2, bottom: r.bottom, composerTop: c.y,
                icon: i.maskImage || i.webkitMaskImage, name: b.textContent}; })()""")
    assert (jump['w'], jump['h'], jump['radius'], jump['name']) == (36, 36, '50%', 'Jump to latest'), jump
    assert abs(jump['mid'] - jump['composerMid']) <= 1.5 and jump['bottom'] <= jump['composerTop'] and 'svg' in jump['icon'], jump
    reading = await wk.js("document.getElementById('main').scrollTop")
    srv.bus.publish(Event('text_delta', 'More words while the reader is up the page. ' * 40))
    await wk.wait("document.querySelector('#stream').textContent.includes('More words while')")
    assert abs(await wk.js("document.getElementById('main').scrollTop") - reading) <= 2                    # not pulled back
    await wk.shot('jump-to-latest.png', tmp_path)
    await wk.click('#to-latest')
    await wk.wait("(m => m.scrollHeight - m.scrollTop - m.clientHeight <= 2)(document.getElementById('main'))")
    await wk.wait("document.getElementById('to-latest').hidden")
    srv.bus.publish(Event('turn_end', {'interrupted': True}))
    await wk.wait("document.querySelector('#stream .msg.dream .msg-stopped')?.textContent === 'Stopped' && !document.querySelector('#stream .cursor')")
    await wk.wait("(m => m.scrollHeight - m.scrollTop - m.clientHeight <= 2)(document.getElementById('main'))")
    await wk.shot('stopped.png', tmp_path)


async def test_message_actions_in_webkit(desktop, tmp_path):
    """Batch B3 in the desktop's engine: the reply's row, real clicks on Copy (the reply's markdown source, the owner's
    message), an earlier row shown under the pointer, and the turn's timing behind the numbers."""
    from test_turn_timing_ui import TIMING
    srv, wk, _, _ = desktop
    await converse(srv, wk)
    second = 'The **second** reply:\n\n- with a list'
    await srv._accept_prompt('And the next step?')
    srv.bus.publish(Event('turn_start', {}))
    srv.bus.publish(Event('text_delta', second))
    srv.bus.publish(Event('result', {'stats': {'gen_tps': 26.9, 'duration_s': 14.2}}))
    srv.bus.publish(Event('turn_timing', TIMING))
    srv.bus.publish(Event('turn_end', {}))
    await wk.wait("document.querySelectorAll('#stream .msg.dream .msg-copy').length === 2 && !!document.querySelector('#stream .msg.dream.latest button.msg-meta')")
    row = await wk.js("""(() => { const m = document.querySelector('#stream .msg.dream.latest'), f = m.querySelector('.msg-foot').getBoundingClientRect(), b = m.querySelector('.body').getBoundingClientRect(),
        c = m.querySelector('.msg-copy'), t = m.querySelector('.msg-meta'), cr = c.getBoundingClientRect(), tr = t.getBoundingClientRect();
        return {under: f.y >= b.bottom - 1, left: Math.abs(cr.x - b.x) <= 12, copy: [c.getAttribute('aria-label'), getComputedStyle(c).opacity, cr.height, cr.width], meta: [t.textContent, Math.round(f.right - tr.right), tr.x > cr.right],
                hover: matchMedia('(hover:hover)').matches, pointer: matchMedia('(pointer:fine)').matches}; })()""")
    print('WEBKIT ROW:', json.dumps(row))
    assert row['under'] and row['left'] and row['copy'][:2] == ['Copy', '1'] and row['copy'][2] >= 24 and row['copy'][3] >= 30, row      # the last reply's row, visible
    assert row['meta'][0] == '26.9 tok/s \u00b7 14.2 s' and row['meta'][1] <= 1 and row['meta'][2], row             # the numbers at its right
    await wk.click('#stream .msg.dream.latest .msg-copy')
    await wk.wait("document.querySelector('#stream .msg.dream.latest .msg-copy').getAttribute('aria-label') === 'Copied'")
    assert (await wk.call('clipboard'))['text'] == second                                                        # the markdown source
    older = "document.querySelector('#stream .msg.dream:not(.latest) .msg-copy')"
    if row['hover']:
        assert await wk.js(f"getComputedStyle({older}).opacity") == '0'                                           # an earlier row waits
    await wk.click('#stream .msg.dream:not(.latest) .msg-copy')                                                  # the pointer goes there: it shows, and copies
    assert await wk.js(f"getComputedStyle({older}).opacity") == '1'
    await wk.wait(f"{older}.getAttribute('aria-label') === 'Copied'")
    assert (await wk.call('clipboard'))['text'] == REPLY
    await wk.shot('actions-older-row-under-the-pointer.png', tmp_path)
    await wk.click('#stream .msg.you .msg-copy')
    await wk.wait("document.querySelector('#stream .msg.you .msg-copy').getAttribute('aria-label') === 'Copied'")
    assert (await wk.call('clipboard'))['text'] == 'Summarise the release for me.'
    await wk.click('#stream .msg.dream.latest .msg-meta')
    await wk.wait("(c => c && !c.hidden && c.open)(document.querySelector('#stream > .turn-timing'))")           # the timing detail, one click away
    await wk.shot('actions-timing-detail.png', tmp_path)
