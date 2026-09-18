"""Opt-in native acceptance check. Run with system Python on a graphical display.

Uses a real Studio server and real `done` tool, with an isolated HTML fixture.
Does not start an inference model or touch the user's memory/database.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HTML = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Canvas · Dream preview example</title><style>
*{box-sizing:border-box}body{margin:0;background:#eff0f7;color:#292c4a;font:16px system-ui}header{padding:26px 36px;display:flex;justify-content:space-between;border-bottom:1px solid #d5d6e5}main{padding:52px 36px}small{color:#63578e}h1{font-size:clamp(36px,5vw,64px);line-height:1.05;letter-spacing:-2px;max-width:640px;font-weight:650;margin:20px 0}p{max-width:440px;line-height:1.7;color:#5c6078}button{border:0;background:#5545a7;color:white;border-radius:100px;padding:15px 25px;cursor:pointer;font-size:15px}.canvas{height:280px;background:#dcd7f1;margin:36px 0;border-radius:18px;position:relative;overflow:hidden}.orb{position:absolute;border-radius:50%;height:230px;width:230px;background:#9a83d9;left:calc(50% - 180px);top:35px;box-shadow:95px -42px 0 #b7aaea,180px 70px 0 #6754a4}.orb:after{content:"";position:absolute;inset:50px;border-radius:50%;border:1px solid #eeecff}.foot{display:flex;justify-content:space-between;font-size:13px;color:#686482}button:focus-visible{outline:3px solid #9684dd;outline-offset:5px}
</style><header><b>Canvas.</b><small>Dream preview example</small></header><main><small>A little space for a big idea</small><h1>Build it.<br>See it come alive.</h1><p>This is a live HTML artifact inside Dream. Change the palette below to check that the preview is interactive.</p><button id="change" onclick="document.querySelector('.canvas').style.background='#c8e5e4';this.textContent='Palette updated';window.clicked=true">Try another palette</button><div class="canvas"><div class="orb"></div></div><div class="foot"><span>HTML + CSS + a little imagination</span><span id="proof">Interactive preview</span></div></main></html>'''


async def fixture_server(workspace: str):
    from dream.gui.server import StudioServer
    from dream.gui.bus import EventBus
    from dream.gui import preview
    from dream.tools.context import ToolContext, set_context, set_studio
    from dream.tools.studio import done
    from dream import config
    root = Path(workspace)
    page = root / 'canvas.html'
    page.write_text(HTML)
    config.SCREENSHOT_DIR = root / 'shots'
    commands = asyncio.Queue()
    server = StudioServer(EventBus(), session={'provider': 'Preview validation', 'model': 'No inference model',
                          'workspace': str(root), 'session_id': 'desktop-smoke'}, on_prompt=commands.put_nowait)
    def emit(event):
        server.retain_show(event)
        server.bus.publish(event)
    set_context(ToolContext(store=None, working=None, browser=None, session_id='desktop-smoke', workspace=root, emit=emit))
    set_studio(server)
    try:
        await server.start()
        print('Dream Desktop acceptance check\n\nReal Studio server. Real browser. No inference model.\n', flush=True)
        # Queued before any client attaches: the browser must replay this.
        result = await done.handler({'path': 'canvas.html'})
        print(result['content'][0]['text'], flush=True)
        while True:
            command = await commands.get()
            if command == '/quit':
                break
            if command == 'verify-frame':
                answer = await server.ask_frame('eval', {'code': "document.getElementById('change').click(); return window.clicked === true;"}, timeout=8)
                (root / 'frame-result.json').write_text(json.dumps(answer))
                print('Live frame interaction:', answer, flush=True)
            if command == 'show-again':
                result = await done.handler({'path': 'canvas.html'})
                print(result['content'][0]['text'], flush=True)
    finally:
        await server.stop()
        if preview._PREVIEW:
            await preview._PREVIEW.aclose()
        set_studio(None)


def native_check(output: Path):
    from dream.desktop.window import DreamWindow, Gtk, GLib, Gdk, Vte
    from urllib.request import Request, build_opener, ProxyHandler
    output.mkdir(parents=True, exist_ok=True)
    report = []
    start = time.monotonic()
    temporary = tempfile.TemporaryDirectory(prefix='dream-native-check-')
    workspace = Path(temporary.name)
    w = DreamWindow(str(ROOT / '.venv/bin/python'), str(ROOT), [], autostart=False)
    w.running = True
    env = dict(os.environ, DREAM_DESKTOP_SESSION_FILE=str(w.discovery), DREAM_GUI_OPEN='0')
    w.terminal.spawn_async(Vte.PtyFlags.DEFAULT, str(ROOT),
                          [str(ROOT / '.venv/bin/python'), str(Path(__file__).resolve()), '--server', str(workspace)],
                          [f'{k}={v}' for k,v in env.items()], GLib.SpawnFlags.DEFAULT,
                          None, None, -1, None, w._spawned, None)
    state = {'stage': 0, 'busy': False, 'error': None}

    def send(prompt):
        base, token = w.address_info
        req = Request(base + '/api/prompt', data=json.dumps({'prompt': prompt}).encode(),
                      headers={'Content-Type': 'application/json', 'x-dream-token': token})
        with build_opener(ProxyHandler({})).open(req, timeout=3) as response:
            response.read()

    def screenshot(name):
        pixbuf = Gdk.pixbuf_get_from_window(w.get_window(),0,0,w.get_allocated_width(),w.get_allocated_height())
        pixbuf.savev(str(output / name), 'png', [], [])

    def evaluate(script, then):
        state['busy'] = True
        def callback(view, result, _data):
            state['busy'] = False
            try:
                value = view.evaluate_javascript_finish(result).to_json(0)
                then(json.loads(value) if value else None)
            except Exception as exc:
                state['error'] = str(exc)
        w.studio.evaluate_javascript(script,-1,None,None,None,callback,None)

    def tick():
        try:
            if time.monotonic()-start > 65:
                raise AssertionError(f"Native smoke timed out at stage {state['stage']}: {state['error']}")
            if state['busy']:
                return True
            if state['stage'] == 0 and w.connected:
                def loaded(value):
                    if value:
                        report.append('Studio connected; queued HTML replay rendered in the native WebKit pane')
                        send('verify-frame')
                        state['stage'] = 1
                evaluate("!!document.querySelector('#artbody iframe')",loaded)
            elif state['stage'] == 1 and (workspace/'frame-result.json').exists():
                assert json.loads((workspace/'frame-result.json').read_text()) is True, 'The live artifact button did not return true'
                report.append('Real ask_frame evaluated a button click in the visible artifact')
                screenshot('studio-canvas.png')
                w._connect_studio(force=True)
                state['stage'] = 2
            elif state['stage'] == 2:
                def reloaded(value):
                    if value:
                        report.append('Reconnecting Studio restored the last delivered artifact')
                        w.workspace.set_visible_child_name('browser')
                        send('show-again')
                        state['stage'] = 3
                evaluate("!!document.querySelector('#artbody iframe')",reloaded)
            elif state['stage'] == 3 and w.workspace.get_visible_child_name() == 'studio':
                report.append('A new explicit show returned the right pane from Browser to Studio')
                # Exercise native URL rejection and a real unreachable dev-server failure.
                w.browser.navigate('file:///etc/passwd')
                assert w.browser.view.get_uri() in (None,'about:blank')
                w.workspace.set_visible_child_name('browser')
                w.browser.navigate('http://127.0.0.1:59999/')
                state['stage'] = 4
            elif state['stage'] == 4 and w.browser.stack.get_visible_child_name() == 'error':
                report.append('Browser refused file URLs and displayed an actionable failed-navigation state')
                screenshot('browser-recovery.png')
                w.browser.stack.set_visible_child_name('web')
                w.browser.view.load_html(HTML, 'http://127.0.0.1/')
                w.unmaximize()
                w.resize(820, 900)
                state['resized_at'] = time.monotonic()
                state['stage'] = 4.5
            elif state['stage'] == 4.5 and time.monotonic() - state['resized_at'] > 2:
                assert w.browser.view.get_allocated_height() <= w.browser.get_allocated_height(), 'Hidden pages forced the browser outside its visible allocation'
                assert not w.terminal_panel.get_mapped(), 'Inactive terminal must not consume the browser workspace'
                assert w.workspace.get_allocated_width() <= w.get_allocated_width()
                report.append('Narrow tab layout keeps the rendered browser within its full-width workspace')
                screenshot('responsive-canvas.png')
                send('/quit')
                state['stage'] = 5
            elif state['stage'] == 5 and not w.running:
                report.append('Queued /quit shut down the Studio child cleanly')
                w.destroy()
                return False
        except Exception as exc:
            state['error'] = str(exc)
            report.append('FAIL: '+str(exc))
            if w.address_info:
                try: send('/quit')
                except Exception: pass
            w.destroy()
            return False
        return True
    GLib.timeout_add(500,tick)
    Gtk.main()
    temporary.cleanup()
    (output/'native-report.json').write_text(json.dumps(report,indent=2))
    print('\n'.join(report))
    if state['stage'] != 5 or any(r.startswith('FAIL:') for r in report):
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--server')
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/desktop-validation')
    args=parser.parse_args()
    if args.server:
        asyncio.run(fixture_server(args.server))
    else:
        native_check(args.output)
