"""Terminal names before launch and intentional terminal follow behavior."""
import json
from pathlib import Path

from playwright.async_api import expect
from test_deepsleep_ui import board_page, add, STATIC


async def test_two_claude_names_before_open_and_failed_create_keeps_draft(board_page):
    page, api, errors = board_page
    name = page.get_by_label('New terminal name', exact=True)
    for title in ('Claude · Research', 'Claude · Review'):
        await name.fill(title)
        await add(page)
        await expect(name).to_have_value('')
    assert [n['title'] for n in api.state['nodes']] == ['Claude · Research', 'Claude · Review']
    await expect(page.locator('.ds-node h2')).to_have_text(['Claude · Research', 'Claude · Review'])
    await page.get_by_role('button', name='Plan a group task').click()
    assert await page.get_by_label('Synthesis terminal').locator('option').all_text_contents() == ['Claude · Research', 'Claude · Review']
    await page.get_by_role('button', name='Close group task').click()
    api.fail = 'create'
    await name.fill('Claude · Kept after failure')
    await page.get_by_role('button', name='Add terminal', exact=True).click()
    await expect(page.locator('.ds-feedback')).to_contain_text('not sent')
    await expect(name).to_have_value('Claude · Kept after failure')
    assert len(api.state['nodes']) == 2 and not errors
    await page.screenshot(path='/tmp/dream282-terminal-names.png')


async def test_duplicate_titles_disambiguate_receivers_without_changing_pane_titles(board_page):
    page, api, errors = board_page
    for _ in range(2):
        await page.get_by_label('New terminal name', exact=True).fill('Research')
        await add(page)
    await expect(page.locator('.ds-node h2')).to_have_text(['Research', 'Research'])
    await page.get_by_role('button', name='Plan a group task').click()
    receiver = page.get_by_label('Synthesis terminal')
    assert await receiver.locator('option').all_text_contents() == ['Research · node-1', 'Research · node-2']
    await page.get_by_label('Research terminals').select_option('node-1')
    await receiver.select_option('node-2')
    await page.get_by_label('Group task prompt').fill('Controlled routing fixture')
    await page.get_by_role('button', name='Run group task', exact=True).click()
    await expect(page.get_by_role('button', name='Run group task', exact=True)).to_be_enabled()
    group = next(call for call in api.calls if call['action'] == 'group')
    assert group['targets'] == ['node-1'] and group['synthesis_target'] == 'node-2'
    assert not errors


async def test_bottom_click_and_noop_wheel_follow_but_selection_holds_history(board_page):
    page, api, errors = board_page
    await add(page)
    card = page.locator('.ds-node')
    socket = api.sockets['node-1']
    async def output(text):
        socket.send(json.dumps({'type': 'output', 'data': text}))
        await page.wait_for_function('''text => {const b=fixtureTerminals[0].buffer.active;
          return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString()).some(s=>s.includes(text));}''', arg=text.split('\r\n')[-1])
    bottom = '()=>{const b=fixtureTerminals[0].buffer.active;return b.viewportY===b.baseY;}'
    await output(''.join(f'\r\nHISTORY_{i:03}' for i in range(160)))
    await page.wait_for_function(bottom)
    screen = card.locator('.xterm-screen')
    await screen.click(position={'x': 35, 'y': 30})
    await page.mouse.wheel(0, 500)  # Already at bottom; no actual history movement.
    await page.wait_for_timeout(350)
    await output('\r\nAFTER_PROMPT_CLICK')
    await page.wait_for_function(bottom)
    latest = card.get_by_role('button', name='Follow latest output in Claude')
    await expect(latest).to_be_hidden()
    await screen.dblclick(position={'x': 40, 'y': 8})
    await page.wait_for_function('fixtureTerminals[0].hasSelection()')
    await expect(latest).to_be_visible()
    anchor = await page.evaluate('fixtureTerminals[0].buffer.active.viewportY')
    await output('\r\nWHILE_READING_SELECTION')
    assert await page.evaluate('fixtureTerminals[0].buffer.active.viewportY') == anchor
    assert await page.evaluate('fixtureTerminals[0].hasSelection()')
    await latest.click()
    await page.wait_for_function(bottom)
    await output('\r\nFOLLOWING_AGAIN')
    await page.wait_for_function(bottom)
    assert not errors


async def test_owned_ptys_named_before_launch_and_dream_stream_follows(tmp_path, monkeypatch):
    import os
    import sys
    import threading
    from dataclasses import replace
    from http.server import ThreadingHTTPServer
    from playwright.async_api import async_playwright
    from dream import config
    from dream.core import providers
    from dream.gui.bus import EventBus
    from dream.gui.server import StudioServer
    from test_deepsleep_live_integration import eventually
    from test_workflow_terminal_live import StreamingHTTP

    runtime, workspace = tmp_path / 'runtime', tmp_path / 'workspace'
    runtime.mkdir(); workspace.mkdir()
    native_script = tmp_path / 'owned_terminal.py'
    native_script.write_text("import sys\nprint('READY_OWNED_PTY',flush=True)\nfor line in sys.stdin:\n print('OWNED_ECHO '+line.strip(),flush=True)\n")
    monkeypatch.setattr(config, 'ROOT', runtime)
    monkeypatch.setattr('dream.gui.deepsleep.native_launch', lambda *a, **k: (
        [sys.executable, '-u', str(native_script)], dict(os.environ, TERM='xterm-256color')))
    http = ThreadingHTTPServer(('127.0.0.1', 0), StreamingHTTP)
    http.requests = []
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setitem(providers.PROVIDERS, 'machx', replace(providers.PROVIDERS['machx'],
        base_url=f'http://127.0.0.1:{http.server_port}/v1'))
    server = StudioServer(EventBus(), session={'session_id': 'names-follow-fixture',
                                              'workspace': str(workspace), 'provider': 'machx'})
    url = await server.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--disable-gpu'])
            try:
                page = await browser.new_page(viewport={'width': 1920, 'height': 1080})
                page.set_default_timeout(15000)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.route('**/api/accounts', lambda route: route.fulfill(json={'accounts': []}))
                capture = '''\nwindow.fixtureTerminals=[];const Original=window.Terminal;window.Terminal=class extends Original{constructor(...args){super(...args);fixtureTerminals.push(this);}};window.bufferText=i=>{const b=fixtureTerminals[i].buffer.active;return Array.from({length:b.length},(_,j)=>b.getLine(j).translateToString(true)).join('\\n');};'''
                await page.route('**/assets/vendor/xterm.js', lambda route: route.fulfill(
                    content_type='text/javascript', body=(STATIC / 'vendor/xterm.js').read_text() + capture))
                await page.goto(url.replace('/#', '/?companion=1#'))
                await page.get_by_role('button', name='Go to DeepSleep', exact=True).click()
                for title in ('Claude · Research', 'Claude · Review'):
                    await page.get_by_label('New terminal provider', exact=True).select_option('anthropic')
                    await page.get_by_label('Terminal experience', exact=True).select_option('native')
                    await page.get_by_label('New terminal name', exact=True).fill(title)
                    await page.get_by_role('button', name='Add terminal', exact=True).click()
                    await expect(page.get_by_label('New terminal name', exact=True)).to_have_value('')
                await eventually(lambda: len(server._board().nodes) == 2 and all(n.state == 'ready' for n in server._board().nodes.values()))
                await expect(page.locator('.ds-node h2')).to_have_text(['Claude · Research', 'Claude · Review'])
                await page.get_by_label('New terminal provider', exact=True).select_option('machx')
                await page.get_by_label('New terminal name', exact=True).fill('Dream · Stream')
                await page.get_by_role('button', name='Add terminal', exact=True).click()
                await page.wait_for_function('fixtureTerminals.length===3 && bufferText(2).includes("you")')
                board = server._board()
                await eventually(lambda: len(board.nodes) == 3 and all(n.state == 'ready' for n in board.nodes.values()))
                assert len({n.process.pid for n in board.nodes.values()}) == 3
                card = page.locator('.ds-node').nth(2)
                await card.get_by_role('button', name='Maximize Dream · Stream').click()
                await card.locator('.xterm-screen').click(position={'x': 40, 'y': 20})
                await page.keyboard.type('Stream the controlled fixture')
                await page.keyboard.press('Enter')
                await page.wait_for_function('bufferText(2).includes("ROW0004_")')
                await card.locator('.xterm-screen').click(position={'x': 45, 'y': 35})
                await page.mouse.wheel(0, 200)
                await page.wait_for_function('bufferText(2).includes("END_STREAM_269")')
                await page.wait_for_function('()=>{const b=fixtureTerminals[2].buffer.active;return b.viewportY===b.baseY;}')
                latest = card.get_by_role('button', name='Follow latest output in Dream · Stream')
                await expect(latest).to_be_hidden()
                await page.screenshot(path='/tmp/dream282-live-stream-follow.png')
                await card.locator('.xterm-helper-textarea').focus()
                await page.keyboard.type('Stream again while I read history')
                await page.keyboard.press('Enter')
                await eventually(lambda: len(http.requests) == 2)
                await card.locator('.xterm-screen').hover()
                await page.mouse.wheel(0, -700)
                await expect(latest).to_be_visible()
                await page.wait_for_timeout(350)
                anchor = await page.evaluate('fixtureTerminals[2].buffer.active.viewportY')
                await page.wait_for_function('bufferText(2).split("END_STREAM_269").length===3')
                assert await page.evaluate('fixtureTerminals[2].buffer.active.viewportY') == anchor
                await latest.click()
                await page.wait_for_function('()=>{const b=fixtureTerminals[2].buffer.active;return b.viewportY===b.baseY;}')
                assert not errors and len(http.requests) == 2
            finally:
                await browser.close()
    finally:
        await server.stop()
        http.shutdown(); http.server_close(); thread.join(timeout=2)
    assert all(n.process.returncode is not None and n.master is None for n in server._board().nodes.values())
