"""The Studio preview in the desktop's real web view (A0): the real `DreamWindow` and its WebKitGTK view, the real
StudioServer and the real `done` tool, three pages of the owner's kinds -- a site with its own style.css, app.js
and logo.png; a page with an importmap and a dynamic import; a page busy for 5 s from its load -- checked inside
the frame (the bridge's eval, `server.ask_frame`), on the parent page (the frame's rect, the link, the note) and
in the painted pixels (a Gdk snapshot sampled on the site's solid swatch). Every pytest browser test is Chromium;
this is the engine the owner sees, which had never been tested (SPEC.md 2.0, cause 6).

How it runs: `tests/test_desktop_preview_native.py` starts a private headless GTK display (`broadwayd`, no TCP
port) and, on it, this file as a driver process (`--driver`, system Python with the GI bindings): the window,
its `webview(local_only=True)` view, one JSON command per line on stdin. Nothing opens on the owner's display.
Standalone on the owner's display: `python3 tests/desktop_preview_native.py [shots-folder]` runs the same three
checks on the current DISPLAY (the window opens and closes) and prints a report. No model, no engine, no GPU
beyond what the owner's display already uses."""
from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MARK = '@@wk '
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAEklEQVR4nGP4z8DwHwyBNAMDAHbjD/0ie3IoAAAAAElFTkSuQmCC")
SITE = {"index.html": ('<!doctype html><html><head><title>Coffee Shop</title><link rel="stylesheet" href="style.css">'
                       '</head><body><h1 id="h">booting...</h1><img id="logo" src="logo.png" alt="logo">'
                       '<script src="app.js"></script></body></html>'),
        "style.css": "body{background:rgb(1, 2, 3);color:#fff;margin:0}#swatch{width:160px;height:160px;background:rgb(200, 30, 40)}",
        "app.js": "document.getElementById('h').textContent = 'running'; document.body.insertAdjacentHTML('beforeend', '<div id=swatch></div>');"}
FALCON = ('<!doctype html><html><head><title>Falcon 9</title><script type="importmap">{"imports":{"three":"./vendor/three.js"}}'
          '</script></head><body><h1 id="h">booting...</h1><script type="module">import("./falcon9/main.js").then(m => m.start());'
          '</script></body></html>')
BUSY = ('<!doctype html><title>Busy</title><h1 id="h">busy</h1><script>'
        "addEventListener('load', () => { const ch = new MessageChannel(); ch.port1.onmessage = () => { "
        "const t = Date.now(); while (Date.now() - t < 5000) {} document.getElementById('h').textContent = 'done'; }; "
        "ch.port2.postMessage(1); });</script>")


# ---------------------------------------------------------------------------------------------------------------
# The driver process (system Python, GTK main loop). Everything below `driver()` runs only under `--driver`.
def driver():
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')
    gi.require_version('WebKit2', '4.1')
    from gi.repository import Gdk, GLib, Gtk, WebKit2

    out = os.fdopen(os.dup(1), 'w')          # replies go to the real stdout; stray prints go to stderr
    os.dup2(2, 1)

    def reply(**data):
        out.write(MARK + json.dumps(data) + '\n'); out.flush()

    if not Gtk.init_check()[0]:
        reply(error='no display'); return 3
    screen = Gdk.Screen.get_default()
    if screen.get_resolution() <= 0:         # broadway reports no resolution; WebKit would scale by dpi / 96
        screen.set_resolution(96)
    sys.path.insert(0, str(ROOT))
    from dream.desktop.window import DreamWindow
    workspace = tempfile.mkdtemp(prefix='dream-pv-ws-')
    window = DreamWindow(sys.executable, workspace, [], autostart=False)   # no session, no terminal child
    view = window.studio

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
        elif op == 'geometry':
            a, w = view.get_allocation(), window.get_allocation()
            reply(view=[a.width, a.height], window=[w.width, w.height], trusted=window._trusted_studio_document(),
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
# The checks (the test process: pytest under run-sys.sh, or `main()` on the owner's display).

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
        end = asyncio.get_event_loop().time() + timeout
        while True:
            value = await self.js(expression)
            if value:
                return value
            if asyncio.get_event_loop().time() > end:
                raise AssertionError(f'never true in the web view: {expression}')
            await asyncio.sleep(0.05)


async def start_driver(env, log):
    """The driver process on the display `env` names; its WebKit handle."""
    process = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).resolve()), '--driver', env=env,
                                                   stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=log)
    wk = WebKit(process)
    await wk.answer()
    return process, wk


async def connect(play, wk):
    base, _, token = play.url.partition('/#token=')
    await wk.call('connect', base=base, token=token, session_id=play.session_id)
    await wk.wait("document.getElementById('stat')?.textContent === 'Ready' && document.documentElement.classList.contains('dream-native')")
    await play.connected()


async def in_frame(play, code, timeout=5.0):
    """Evaluate inside the open artifact frame, through the bridge (what eval_js_user_view does)."""
    return await play.server.ask_frame('eval', {'code': code}, timeout=timeout)


FRAME_RECT = "(() => { const f = document.querySelector('#artbody iframe'); if(!f) return null; const r = f.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; })()"


async def check_site(play, wk, shots):
    """(1) style applied, script run, image decoded, the result says loaded clean, the frame >= 300x300, the pixels."""
    from dream.tools.native import write_file
    from dream.tools.studio import done
    from _artifact_play import text
    ws = play.workspace
    (ws / 'site').mkdir(exist_ok=True)
    (ws / 'site' / 'logo.png').write_bytes(PNG)
    steps = [(write_file, {'path': f'site/{name}', 'content': body}) for name, body in SITE.items()]
    results = await play.turn('build a landing page for a coffee shop', steps + [(done, {'path': 'site/index.html'})])
    result = text(results[-1])
    report = {'result_error': bool(results[-1].get('is_error')),
              'owner_view': [l for l in result.splitlines() if l.startswith("Studio (owner's view)")]}
    await wk.wait("!!document.querySelector('#artbody iframe')")
    rect = await wk.wait(FRAME_RECT)
    report['frame_rect'] = rect
    report['h'] = await in_frame(play, "document.getElementById('h').textContent")
    report['background'] = await in_frame(play, "getComputedStyle(document.body).backgroundColor")
    report['naturalWidth'] = await in_frame(play, "document.getElementById('logo').naturalWidth")
    report['swatch'] = await in_frame(play, "getComputedStyle(document.getElementById('swatch')).backgroundColor")
    report['notice'] = await in_frame(play, "!!document.getElementById('__dream_notice')")
    inner = await in_frame(play, "(() => { const r = document.getElementById('swatch').getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()")
    point = [rect[0] + inner[0], rect[1] + inner[1]]
    snap = await wk.call('snapshot', path=str(shots / 'webkit-site.png') if shots else None, pixels=[point])
    report['pixel_at_swatch'] = snap['pixels'][0]
    return report


async def check_importmap(play, wk, shots):
    """(2) the owner-visible notice in the frame, the Open in browser link on the parent, the result not clean."""
    from dream.tools.studio import done
    from _artifact_play import text
    ws = play.workspace
    (ws / 'falcon9').mkdir(exist_ok=True)
    (ws / 'falcon9' / 'main.js').write_text("export function start(){ document.getElementById('h').textContent = 'running'; }")
    (ws / 'vendor').mkdir(exist_ok=True)
    (ws / 'vendor' / 'three.js').write_text("export const THREE = 1;")
    (ws / 'falcon9-launch.html').write_text(FALCON)
    (result,) = await play.turn('show the launch viewer', [(done, {'path': 'falcon9-launch.html'})])
    await wk.wait("document.getElementById('artname')?.textContent === 'falcon9-launch.html'")
    await wk.wait("!!document.querySelector('#artbody iframe')")
    report = {'result_error': bool(result.get('is_error')), 'hidden_frame_not_clean': 'Hidden frame is NOT clean' in text(result),
              'owner_view': [l for l in text(result).splitlines() if l.startswith("Studio (owner's view)")]}
    for _ in range(40):
        got = await in_frame(play, "(() => { const n = document.getElementById('__dream_notice'); if(!n) return null; const r = n.getBoundingClientRect(); return {text: n.textContent, top: r.top, height: r.height, position: getComputedStyle(n).position}; })()")
        if got:
            break
        await asyncio.sleep(0.1)
    report['notice'] = got
    report['h'] = await in_frame(play, "document.getElementById('h').textContent")
    report['link'] = await wk.wait("(() => { const a = document.getElementById('artext'); return a && a.style.display !== 'none' && a.href ? {text: a.textContent, href: a.href} : null; })()")
    if shots:
        await wk.call('snapshot', path=str(shots / 'webkit-importmap-notice.png'))
    return report


async def check_busy(play, wk, shots):
    """(3) a page busy for 5 s from its load is still open 9 s later, with no 'navigated away'."""
    from dream.tools.studio import done
    ws = play.workspace
    (ws / 'busy.html').write_text(BUSY)
    (result,) = await play.turn('show it', [(done, {'path': 'busy.html'})])
    await wk.wait("document.getElementById('artname')?.textContent === 'busy.html'")
    await asyncio.sleep(9.0)
    report = {'result_error': bool(result.get('is_error')),
              'frame_present': await wk.js("!!document.querySelector('#artbody iframe')"),
              'note': await wk.js("document.getElementById('artnote').textContent"),
              'closed_lines': await wk.js("[...document.querySelectorAll('.sys')].map(e => e.textContent).filter(t => t.includes('navigated away'))")}
    if report['frame_present']:
        report['h'] = await in_frame(play, "document.getElementById('h').textContent")
    if shots:
        await wk.call('snapshot', path=str(shots / 'webkit-busy.png'))
    return report


def judge_site(r):
    problems = []
    if r['result_error']: problems.append('done returned an error')
    if r['owner_view'] != ["Studio (owner's view): loaded clean (no errors)."]: problems.append(f"owner's view line: {r['owner_view']}")
    if r['h'] != 'running': problems.append(f"app.js did not run: h={r['h']!r}")
    if r['background'] != 'rgb(1, 2, 3)': problems.append(f"style.css not applied: {r['background']}")
    if r['naturalWidth'] != 2: problems.append(f"logo.png not decoded: naturalWidth={r['naturalWidth']}")
    if r['swatch'] != 'rgb(200, 30, 40)': problems.append(f"swatch style: {r['swatch']}")
    if r['notice']: problems.append('a notice on a page with nothing to warn about')
    if not r['frame_rect'] or r['frame_rect'][2] < 300 or r['frame_rect'][3] < 300: problems.append(f"frame rect {r['frame_rect']}")
    px = r['pixel_at_swatch']
    if not all(abs(a - b) <= 8 for a, b in zip(px, [200, 30, 40])): problems.append(f'painted pixel at the swatch: {px}')
    return problems


def judge_importmap(r):
    """DREAM-238: the page's local modules are inlined, so the importmap page RUNS in the real WebKitGTK frame (it
    was refused with a notice before): done delivers it, the owner's view is clean, the dynamic import ran."""
    problems = []
    if r['result_error'] or r['hidden_frame_not_clean']: problems.append('done refused the module page')
    if r['notice']: problems.append(f"a notice on a page that runs: {r['notice']}")
    if r['h'] != 'running': problems.append(f"the page's module did not run: h={r['h']!r}")
    if not r['link'] or 'falcon9-launch.html' not in r['link']['href']: problems.append(f"Open in browser link: {r['link']}")
    if r['owner_view'] != ["Studio (owner's view): loaded clean (no errors)."]: problems.append(f"owner's view line: {r['owner_view']}")
    return problems


def judge_busy(r):
    problems = []
    if r['result_error']: problems.append('done returned an error')
    if not r['frame_present']: problems.append('the frame was closed')
    if 'navigated away' in r['note'] or r['closed_lines']: problems.append(f"closed as navigated away: {r['note']!r} {r['closed_lines']}")
    if r.get('h') != 'done': problems.append(f"the busy task did not finish in the frame: h={r.get('h')!r}")
    return problems


CHECKS = [('site', check_site, judge_site), ('importmap', check_importmap, judge_importmap), ('busy', check_busy, judge_busy)]


async def main(shots=None):
    """Standalone, on the owner's display: the three checks in one window; a report on stdout; exit 1 on a problem."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(ROOT))
    from _artifact_play import Play
    shots = Path(shots) if shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    ws = Path(tempfile.mkdtemp(prefix='dream-pv-'))
    env = {**os.environ, 'PYTHONPATH': str(ROOT), 'PYTHONDONTWRITEBYTECODE': '1'}
    failed = False
    async with Play(ws, session_id='preview-native') as play:
        process, wk = await start_driver(env, sys.stderr)
        try:
            await connect(play, wk)
            print('WebKitGTK', '.'.join(map(str, (await wk.call('geometry'))['webkit'])))
            for name, check, judge in CHECKS:
                report = await check(play, wk, shots)
                problems = judge(report)
                failed = failed or bool(problems)
                print(f"[{name}] {'PASS' if not problems else 'FAIL: ' + '; '.join(problems)}")
                print('   ', json.dumps(report))
        finally:
            try:
                await asyncio.wait_for(wk.call('quit'), 5)
                await asyncio.wait_for(process.wait(), 5)
            except Exception:
                process.kill()
    shutil.rmtree(ws, ignore_errors=True)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None)))
