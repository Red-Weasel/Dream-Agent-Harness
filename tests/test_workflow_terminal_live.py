"""Real Dream PTY and shipped xterm: stream, resize, and inspect the transcript."""
import asyncio
from dataclasses import replace
from http.server import ThreadingHTTPServer
import json
import threading
import time

from playwright.async_api import async_playwright

from dream import config
from dream.core import providers
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from test_deepsleep_live_integration import ModelHTTP, eventually
from test_deepsleep_ui import STATIC


class StreamingHTTP(ModelHTTP):
    def do_GET(self):
        if self.path.startswith('/props'):
            body = json.dumps({'default_generation_settings': {'n_ctx': 262144},
                               'total_slots': 1, 'slot_ctx': 262144}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()

    def do_POST(self):
        self.server.requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Connection', 'close')
        self.end_headers()
        for i in range(32):
            content = f'ROW{i:04d}_ ' + ('readable text ' * 12) + '\n'
            chunk = dict(choices=[dict(index=0, delta={'content': content}, finish_reason=None)])
            self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.flush()
            time.sleep(.07)
        ending = ('END_STREAM_269\n\n## Layout check complete\n\n'
                  '- Streaming text stays in order.\n'
                  '- Resizing keeps the conversation intact.\n'
                  '- Repo, context and permissions remain visible.\n\n'
                  'Use `Tools` to open your task draft.\n'
                  'Use `Files` to browse this workspace.\n\n'
                  'Ready for the next task.')
        chunk = dict(choices=[dict(index=0, delta={'content': ending}, finish_reason='stop')],
                     usage={'prompt_tokens': 15000, 'completion_tokens': 300, 'total_tokens': 15300})
        self.wfile.write(('data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n').encode())
        self.wfile.flush()
        self.close_connection = True


async def test_workflow_stream_resize_transcript_and_minimal_prompt(tmp_path, monkeypatch):
    # The runner disables colors globally; this fixture previews the color UI.
    monkeypatch.delenv('NO_COLOR', raising=False)
    http = ThreadingHTTPServer(('127.0.0.1', 0), StreamingHTTP)
    http.requests = []
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    isolated, workspace = tmp_path / 'runtime', tmp_path / 'sample-repo'
    isolated.mkdir(); workspace.mkdir()
    monkeypatch.setattr(config, 'ROOT', isolated)
    monkeypatch.setitem(providers.PROVIDERS, 'machx', replace(providers.PROVIDERS['machx'],
                        base_url=f'http://127.0.0.1:{http.server_port}/v1'))
    server = StudioServer(EventBus(), session={'session_id': 'layout-fixture',
                          'workspace': str(workspace), 'provider': 'machx'})
    url = await server.start()
    try:
        node = await server._board().create({'provider': 'machx', 'profile': 'dream', 'model': 'controlled-model'})
        await eventually(lambda: node.state == 'ready')
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--disable-gpu'])
            try:
                page = await browser.new_page(viewport={'width': 1400, 'height': 950})
                await page.route('**/api/accounts', lambda r: r.fulfill(json={'accounts': []}))
                capture = '''\nconst OriginalTerminal=window.Terminal;window.terminals=[];
window.Terminal=class extends OriginalTerminal {constructor(...args){super(...args);window.terminals.push(this);}};
window.transcript=()=>{const b=terminals[0].buffer.normal;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).join('\\n');};'''
                await page.route('**/assets/vendor/xterm.js', lambda r: r.fulfill(
                    content_type='text/javascript', body=(STATIC/'vendor/xterm.js').read_text()+capture))
                await page.goto(url.replace('/#', '/?companion=1#'))
                await page.get_by_role('button', name='Go to DeepSleep', exact=True).click()
                try:
                    await page.wait_for_function('window.terminals?.length && transcript().includes("you")', timeout=10000)
                except Exception:
                    await page.screenshot(path='/tmp/dream-workflow-start-failure.png')
                    raise AssertionError((node.output[-4000:], await page.evaluate('window.terminals?.length ? transcript().slice(-4000) : "no terminal"')))

                await page.locator('.xterm-helper-textarea').focus()
                await page.keyboard.type('Show the formatting fixture.')
                await page.keyboard.press('Enter')
                await page.wait_for_function('transcript().includes("ROW0004_")')
                # Resize the live terminal through its real host ResizeObserver.
                node.layout.update(width=720, height=540)
                await page.locator('.ds-node').evaluate("el=>{el.style.width='720px';el.style.height='540px';}")
                await page.wait_for_function('transcript().includes("ROW0016_")')
                node.layout.update(width=940, height=700)
                await page.locator('.ds-node').evaluate("el=>{el.style.width='940px';el.style.height='700px';}")
                await page.wait_for_function('transcript().includes("END_STREAM_269")')
                await asyncio.sleep(1.3)  # one idle prompt refresh after Live teardown
                text = await page.evaluate('transcript()')
                for i in range(32):
                    assert text.count(f'ROW{i:04d}_') == 1, (i, text)
                assert text.count('END_STREAM_269') == 1
                assert 'sample-repo' in text and 'ctx 15.0k/262k (6%)' in text
                assert 'monitor' not in text.split('END_STREAM_269')[-1]
                assert await page.evaluate('terminals[0].buffer.active.viewportY === terminals[0].buffer.active.baseY')
                await page.locator('.ds-node').screenshot(path='/tmp/dream-workflow-terminal-preview.png')
                assert '\x1b[38;2;' in node.output  # actual truecolor PTY output
                assert len(http.requests) == 1
            finally:
                await browser.close()
    finally:
        await server.stop()
        http.shutdown(); http.server_close(); thread.join(timeout=2)
    assert node.process.returncode is not None and node.master is None
