"""The prompt optimizer's buttons inside a Dream terminal on the DeepSleep page are clickable (the owner, 2026-10-11:
[feedback]). A real Dream terminal on a controlled model server, no GPU:
[ Skip next ] and [ Optimizer ] on the prompt box's line flip their switches when clicked, idle and while a turn
works, and the rewrite's [S] Send sends it."""
import asyncio
import threading
from dataclasses import replace
from http.server import ThreadingHTTPServer

from playwright.async_api import async_playwright

from dream import config
from dream.core import providers
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from test_deepsleep_live_integration import eventually
from test_deepsleep_ui import STATIC
from test_turn_prompt_box_pty import TurnModel

CAPTURE = '''
window.fixtureTerminals=[];const BaseTerminal=window.Terminal;
window.Terminal=class extends BaseTerminal{constructor(...args){super(...args);fixtureTerminals.push(this);}};
window.bufferText=()=>{const b=fixtureTerminals[0].buffer.active;
  return Array.from({length:b.length},(_,j)=>b.getLine(j).translateToString(true)).join('\\n');};
window.cellOf=text=>{const t=fixtureTerminals[0],b=t.buffer.active;
  for(let r=t.rows-1;r>=0;r--){const i=(b.getLine(b.viewportY+r)?.translateToString(true)||'').lastIndexOf(text);
    if(i>=0){const s=t.element.querySelector('.xterm-screen').getBoundingClientRect();
      return {x:s.left+(i+text.length/2)*s.width/t.cols, y:s.top+(r+.5)*s.height/t.rows};}}
  return null;};'''


async def click_text(page, text):
    """Click a button drawn in the terminal's text, as the owner would: hover (xterm finds the link), then click."""
    await page.wait_for_function('text => cellOf(text) !== null', arg=text)
    point, previous = await page.evaluate('text => cellOf(text)', text), None
    # A tile still fitting its terminal moves the text, and a redraw can take it away for a moment (a full run under
    # load): wait until it is found at the same place twice, at most 20 s.
    for _ in range(80):
        if point is not None and point == previous:
            break
        await page.wait_for_timeout(250)
        point, previous = await page.evaluate('text => cellOf(text)', text), point
    assert point is not None and point == previous, f'{text!r} did not come to rest in the terminal'
    await page.mouse.move(point['x'], point['y'])
    await page.wait_for_timeout(150)
    await page.mouse.click(point['x'], point['y'])


async def test_the_terminals_optimizer_buttons_and_send_are_clickable(tmp_path, monkeypatch):
    monkeypatch.setenv('DREAM_PROMPT_OPTIMIZER', '1')
    runtime, workspace = tmp_path / 'runtime', tmp_path / 'sample-repo'
    runtime.mkdir(); workspace.mkdir()
    (workspace / 'notes.txt').write_text('fixture notes')
    monkeypatch.setattr(config, 'ROOT', runtime)
    http = ThreadingHTTPServer(('127.0.0.1', 0), TurnModel)
    http.requests, http.main_seen = [], threading.Event()
    threading.Thread(target=http.serve_forever, daemon=True).start()
    monkeypatch.setitem(providers.PROVIDERS, 'machx', replace(providers.PROVIDERS['machx'], base_url=f'http://127.0.0.1:{http.server_port}/v1'))
    server = StudioServer(EventBus(), session={'session_id': 'box-buttons', 'workspace': str(workspace), 'provider': 'machx'})
    url = await server.start()
    board = server._board()
    try:
        node = await board.create({'provider': 'machx', 'profile': 'dream', 'model': 'controlled-model'})
        await eventually(lambda: node.state == 'ready', 60)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--disable-gpu'])
            try:
                page = await browser.new_page(viewport={'width': 1400, 'height': 900})
                page.set_default_timeout(20000)
                await page.route('**/api/accounts', lambda r: r.fulfill(json={'accounts': []}))
                await page.route('**/assets/vendor/xterm.js', lambda r: r.fulfill(
                    content_type='text/javascript', body=(STATIC / 'vendor/xterm.js').read_text() + CAPTURE))
                await page.goto(url.replace('/#', '/?companion=1#'))
                await page.get_by_role('button', name='Go to DeepSleep', exact=True).click()
                await page.wait_for_function('fixtureTerminals.length===1 && cellOf("[ Skip next: off ]")!==null', timeout=60000)
                await click_text(page, '[ Skip next: off ]')
                await page.wait_for_function('cellOf("[ Skip next: on ]")!==null')
                await click_text(page, '[ Skip next: on ]')
                await page.wait_for_function('cellOf("[ Skip next: off ]")!==null')
                await page.locator('.ds-node .xterm-helper-textarea').first.focus()
                await page.keyboard.type('tidy the notes please')
                await page.keyboard.press('Enter')
                await click_text(page, '[S] Send')
                await eventually(lambda: http.main_seen.is_set(), 20)
                await click_text(page, '[ Optimizer: on ]')                 # while the turn works
                await page.wait_for_function('cellOf("[ Optimizer: off ]")!==null')
                await page.wait_for_function('bufferText().includes("Done.")', timeout=30000)
                await page.wait_for_function('cellOf("[ Optimizer: off ]")!==null')   # still off at the next prompt
            finally:
                await browser.close()
    finally:
        await server.stop()
        http.shutdown()
        http.server_close()
    def users(request):
        return [m.get('content') for m in request.get('messages', []) if m.get('role') == 'user']
    assert any('REWRITTEN-TASK' in str(u) for r in http.requests for u in users(r))   # Send sent the rewrite
