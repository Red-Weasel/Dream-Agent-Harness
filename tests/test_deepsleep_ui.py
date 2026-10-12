"""DeepSleep shipped UI with real xterm rendering and controlled API/WS."""
from copy import deepcopy
from pathlib import Path

import pytest
from playwright.async_api import async_playwright, expect

STATIC = Path(__file__).resolve().parents[1] / 'dream/gui/static'


class BoardAPI:
    def __init__(self):
        self.state = {'nodes': [], 'links': [], 'receipts': [], 'providers': [
            {'key': 'anthropic', 'label': 'Claude', 'models': ['opus-fixture']},
            {'key': 'antigravity', 'label': 'Gemini', 'models': ['gemini-fixture']},
            {'key': 'codex', 'label': 'Codex', 'models': ['codex-fixture']},
        ]}
        self.calls = []
        self.fail = None
        self.socket_messages = []
        self.sockets = {}

    async def route(self, route):
        if route.request.method == 'GET':
            await route.fulfill(json=deepcopy(self.state))
            return
        body = route.request.post_data_json
        self.calls.append(body)
        if body['action'] == self.fail:
            await route.fulfill(status=409, json={'error': 'Terminal is busy; your prompt was not sent.'})
            return
        action = body['action']
        if action == 'create':
            key = body['provider']
            title = body.get('title') or next(p['label'] for p in self.state['providers'] if p['key'] == key)
            self.state['nodes'].append({'id': 'node-' + str(len(self.state['nodes']) + 1), 'provider': key,
                'title': title, 'model': body.get('model', ''), 'state': 'ready',
                'layout': {'x': 30 + len(self.state['nodes']) * 620, 'y': 30, 'width': 580, 'height': 480},
                'agents': [{'name': 'researcher', 'state': 'working'}]})
        elif action == 'layout':
            node = next(n for n in self.state['nodes'] if n['id'] == body['node'])
            node['layout'] = {k: body[k] for k in ['x', 'y', 'width', 'height']}
        elif action == 'link':
            self.state['links'].append({'id': 'link-1', 'source': body['source'], 'target': body['target'], 'auto_forward': body['auto_forward']})
        elif action == 'unlink':
            self.state['links'] = [link for link in self.state['links'] if link['id'] != body['id']]
        elif action == 'close':
            self.state['nodes'] = [n for n in self.state['nodes'] if n['id'] != body['node']]
            self.state['links'] = [link for link in self.state['links'] if body['node'] not in [link['source'], link['target']]]
        elif action in ['send', 'group']:
            self.state['receipts'].append({'id': 'receipt-1', 'status': 'accepted', 'message': 'Task accepted'})
        elif action == 'board_post':   # the owner's post on the project's open board (2026-10-10)
            posts = self.state.setdefault('board', {'posts': [], 'error': ''})['posts']
            posts.append({'id': len(posts) + 1, 'at': 0, 'author': 'owner', 'node': '', 'kind': 'note',
                          'reply_to': None, 'thread': len(posts) + 1, 'text': body['text']})
        await route.fulfill(json={'ok': True, 'result': deepcopy(self.state)})

    def socket(self, ws):
        node = ws.url.rsplit('/', 1)[-1]
        self.sockets[node] = ws
        def received(message):
            import json
            data = json.loads(message)
            self.socket_messages.append((node, data))
            if 'token' in data:
                ws.send(json.dumps({'type': 'output', 'data': 'Connected fixture terminal\r\n$ '}))
                ws.send(json.dumps({'type': 'state', 'state': 'ready'}))
        ws.on_message(received)


def xterm_assets():
    # Root vendors these dependencies locally; missing assets must fail integration.
    js = list(STATIC.rglob('xterm.js'))
    fit = list(STATIC.rglob('addon-fit.js')) + list(STATIC.rglob('xterm-addon-fit.js'))
    css = list(STATIC.rglob('xterm.css'))
    assert js and fit and css, 'DeepSleep requires the locally vendored xterm and FitAddon assets'
    return js[0], fit[0], css[0]


async def avatar_asset(route):
    name = route.request.url.rsplit('/', 1)[-1]
    assert name in {'penguin.png', 'fox.png', 'wolf.png', 'robot.png', 'rabbit.png'}
    path = STATIC / 'avatars' / name
    assert path.is_file(), f'Missing shipped character asset: {name}'
    await route.fulfill(path=str(path), content_type='image/png')


@pytest.fixture
async def board_page():
    terminal_js, fit_js, terminal_css = xterm_assets()
    api = BoardAPI()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--disable-gpu'])
        page = await browser.new_page(viewport={'width': 1500, 'height': 980})
        page.set_default_timeout(4000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.route('**/api/deepsleep', api.route)
        await page.route('**/assets/avatars/*', avatar_asset)
        await page.route_web_socket('**/ws/deepsleep/*', api.socket)
        await page.route('http://dream.test/', lambda route: route.fulfill(content_type='text/html', body='''<!doctype html><html data-dream-view="deepsleep"><body><script>const TOKEN='fixture-token';window.logins=[];window.DreamFrontiers={login:p=>logins.push(p)};</script></body></html>'''))
        await page.goto('http://dream.test/')
        for path in [terminal_css, STATIC / 'deepsleep.css', STATIC / 'deepsleep-drawer.css']:
            await page.add_style_tag(path=str(path))
        for path in [terminal_js, fit_js, STATIC / 'deepsleep-drawer.js', STATIC / 'deepsleep-activity.js', STATIC / 'deepsleep-flow.js', STATIC / 'deepsleep.js']:
            if path.name == 'deepsleep.js':
                await page.evaluate('''() => { window.fixtureTerminals=[]; const Xterm=window.Terminal;
                  window.Terminal=class extends Xterm { constructor(options) {super(options); fixtureTerminals.push(this);} }; }''')
            await page.add_script_tag(path=str(path))
        await expect(page.get_by_label('New terminal provider')).to_have_value('anthropic')
        yield page, api, errors
        await browser.close()


async def add(page, provider='anthropic'):
    await page.get_by_label('New terminal provider').select_option(provider)
    await page.get_by_role('button', name='Add terminal', exact=True).click()
    await expect(page.get_by_role('button', name='Add terminal', exact=True)).to_be_enabled()
    await page.locator('.ds-node').last.get_by_role('button', name='Terminal tools for ', exact=False).click()


async def test_real_terminal_auth_input_prompt_and_independent_login(board_page):
    page, api, errors = board_page
    await add(page)
    terminal = page.locator('[data-node="node-1"]')
    await expect(terminal.locator('.xterm')).to_be_visible()
    await expect(terminal.locator('.ds-terminal-status')).to_contain_text('Transport ready')
    await expect(terminal.get_by_role('list', name='Agents in Claude')).to_contain_text('researcher · working')
    await terminal.get_by_role('button', name='Sign in to Claude').click()
    assert await page.evaluate('logins') == ['anthropic']
    await terminal.locator('.xterm-helper-textarea').focus()
    await page.keyboard.type('echo hello')
    assert any(msg.get('data') == 'e' for _, msg in api.socket_messages)
    assert api.socket_messages[0] == ('node-1', {'token': 'fixture-token'})
    # The draft box and its Send are gone (2026-10-10): the terminal's own prompt above is the one place to type.
    await expect(terminal.get_by_label('Prompt Claude')).to_have_count(0)
    await add(page, 'antigravity')
    await page.get_by_role('button', name='Sign in to Gemini').click()
    assert await page.evaluate('logins') == ['anthropic', 'antigravity']
    assert len(api.sockets) == 2
    assert not errors


async def test_workflow_output_follows_tail_but_keeps_intentional_scrollback(board_page):
    import json
    page, api, errors = board_page
    await add(page)
    card = page.locator('[data-node="node-1"]')
    socket = api.sockets['node-1']
    def output(text):
        socket.send(json.dumps({'type': 'output', 'data': text}))
    async def parsed(text):
        await page.wait_for_function('''text => {
          const buffer=fixtureTerminals[0].buffer.active;
          return Array.from({length:buffer.length},(_,i)=>buffer.getLine(i).translateToString()).some(line=>line.includes(text));
        }''', arg=text)
    bottom = '() => {const b=fixtureTerminals[0].buffer.active;return b.viewportY===b.baseY}'
    output(''.join(f'\r\nConversation line {i:03}' for i in range(160)))
    await parsed('Conversation line 159')
    await page.wait_for_function(bottom)
    terminal = card.locator('.xterm-screen')
    await terminal.hover()
    await page.mouse.wheel(0, -500)
    latest = card.get_by_role('button', name='Follow latest output in Claude')
    await expect(latest).to_be_visible()
    anchor = await page.evaluate('fixtureTerminals[0].buffer.active.viewportY')
    for i in range(12):
        output(f'\r\nNew result {i:02}')
    await parsed('New result 11')
    assert await page.evaluate('fixtureTerminals[0].buffer.active.viewportY') == anchor
    # Reconciliation and resize keep the user in history; no reconnect or replay.
    await page.get_by_role('button', name='Refresh workspace').click()
    await expect(page.get_by_role('button', name='Refresh workspace')).to_be_enabled()
    await card.get_by_role('button', name='Resize Claude; use arrow keys').press('ArrowDown')
    await expect(latest).to_be_visible()
    assert not await page.evaluate(bottom)
    await latest.focus()
    await latest.press('Enter')
    await page.wait_for_function(bottom)
    await expect(latest).to_be_hidden()
    output('\r\nFollowing resumed')
    await parsed('Following resumed')
    await page.wait_for_function(bottom)
    # A footer change resizes the terminal (the multiline draft that did this is gone, 2026-10-10): Tools closed and open.
    await card.get_by_role('button', name='Terminal tools for Claude').click()
    await page.wait_for_function(bottom)
    await card.get_by_role('button', name='Terminal tools for Claude').click()
    await page.evaluate("document.documentElement.dataset.dreamView='chat'")
    output('\r\nOutput while hidden')
    await parsed('Output while hidden')
    await page.evaluate("document.documentElement.dataset.dreamView='deepsleep'")
    await page.wait_for_function(bottom)
    # Scrolling all the way down manually also resumes following.
    await terminal.hover()
    await page.mouse.wheel(0, -400)
    await expect(latest).to_be_visible()
    for _ in range(10):                      # one wheel turn moves a bounded number of rows; scroll until the end
        await page.mouse.wheel(0, 10000)
        if await page.evaluate(bottom):
            break
    await expect(latest).to_be_hidden()
    output('\r\nManual follow resumed')
    await parsed('Manual follow resumed')
    await page.wait_for_function(bottom)
    assert sum('token' in message for _, message in api.socket_messages) == 1
    assert not errors


async def test_fit_does_not_repeat_unchanged_sizes_or_send_hidden_dimensions(board_page):
    page, api, errors = board_page
    await add(page)
    await page.wait_for_function('fixtureTerminals[0].rows > 5')
    await page.wait_for_timeout(100)
    before = [message for _, message in api.socket_messages if message.get('type') == 'resize']
    assert before
    for _ in range(3):
        await page.get_by_role('button', name='Refresh workspace').click()
        await expect(page.get_by_role('button', name='Refresh workspace')).to_be_enabled()
    await page.wait_for_timeout(100)
    assert [message for _, message in api.socket_messages if message.get('type') == 'resize'] == before
    await page.evaluate("document.documentElement.dataset.dreamView='chat'")
    await page.set_viewport_size({'width': 900, 'height': 700})
    await page.wait_for_timeout(100)
    assert [message for _, message in api.socket_messages if message.get('type') == 'resize'] == before
    await page.evaluate("document.documentElement.dataset.dreamView='deepsleep'")
    await page.get_by_role('button', name='Maximize Claude').click()
    await page.wait_for_function('fixtureTerminals[0].cols > 80')
    await page.get_by_role('button', name='Plan a group task').click()
    await page.wait_for_timeout(100)
    bounds = await page.locator('.ds-node').bounding_box()
    viewport = await page.locator('.ds-viewport').bounding_box()
    assert bounds['y'] + bounds['height'] <= viewport['y'] + viewport['height'] + 1
    assert all(message['cols'] >= 2 and message['rows'] >= 1 for _, message in api.socket_messages if message.get('type') == 'resize')
    assert not errors


async def test_character_skin_preserves_terminal_socket_draft_selection_and_scrollback(board_page):
    import json
    page, api, errors = board_page
    await add(page)
    card = page.locator('[data-node="node-1"]')
    await card.get_by_role('button',name='Place character for Claude').click()
    await page.locator('#ds-character-toggle').click()
    await page.evaluate('window.originalTerminal=fixtureTerminals[0]')
    api.sockets['node-1'].send(json.dumps({'type':'output','data':''.join(f'\r\nTranscript line {i:03}' for i in range(180))}))
    await page.wait_for_function('fixtureTerminals[0].buffer.active.baseY > 100')
    await card.locator('.xterm-screen').hover()
    await page.mouse.wheel(0,-600)
    await expect(card.get_by_role('button',name='Follow latest output in Claude')).to_be_visible()
    await page.evaluate('fixtureTerminals[0].select(0,fixtureTerminals[0].buffer.active.viewportY,10)')
    before = await page.evaluate('''() => ({top:originalTerminal.buffer.active.viewportY,rows:originalTerminal.rows,cols:originalTerminal.cols,selection:originalTerminal.getSelection()})''')
    calls = deepcopy(api.calls)
    socket = api.sockets['node-1']
    toggle = page.locator('#ds-character-toggle')
    await expect(toggle).to_have_attribute('data-action','character-skin')
    await toggle.focus()
    await toggle.press('Enter')
    await expect(toggle).to_have_attribute('aria-pressed','true')
    await expect(page.locator('.ds-avatar-slot').nth(await card.evaluate('(n)=>Array.from(document.querySelectorAll(".ds-node")).indexOf(n)'))).to_be_visible()
    await page.wait_for_function('document.querySelector(".ds-avatar-slot img").naturalWidth > 0')
    after = await page.evaluate('''() => ({top:originalTerminal.buffer.active.viewportY,rows:originalTerminal.rows,cols:originalTerminal.cols,selection:originalTerminal.getSelection()})''')
    assert before == after
    assert await page.evaluate('fixtureTerminals.length===1 && fixtureTerminals[0]===originalTerminal')
    assert api.calls == calls and api.sockets['node-1'] is socket
    assert sum('token' in message for _, message in api.socket_messages) == 1
    await toggle.press('Enter')
    await expect(toggle).to_have_attribute('aria-pressed','false')
    await expect(page.locator('.ds-avatar-slot').nth(await card.evaluate('(n)=>Array.from(document.querySelectorAll(".ds-node")).indexOf(n)'))).to_be_hidden()
    assert await page.evaluate('fixtureTerminals[0].buffer.active.viewportY') == before['top']
    assert not errors


async def test_character_preferences_persist_and_survive_blocked_storage(board_page):
    page, api, errors = board_page
    api.state['workspace'] = '/work/first'
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(page.get_by_role('button',name='Refresh workspace')).to_be_enabled()
    await add(page)
    await page.get_by_label('Character for Claude',exact=True).select_option('wolf')
    await page.locator('#ds-character-toggle').click()
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(page.locator('.ds-node')).to_have_attribute('data-character','wolf')
    await page.reload()
    terminal_js, fit_js, terminal_css = xterm_assets()
    for path in [terminal_css,STATIC/'deepsleep.css',STATIC/'deepsleep-drawer.css']:
        await page.add_style_tag(path=str(path))
    for path in [terminal_js,fit_js,STATIC/'deepsleep-drawer.js',STATIC/'deepsleep-activity.js',STATIC/'deepsleep-flow.js',STATIC/'deepsleep.js']:
        await page.add_script_tag(path=str(path))
    await expect(page.locator('#ds-character-toggle')).to_have_attribute('aria-pressed','true')
    await expect(page.locator('.ds-node')).to_have_attribute('data-character','wolf')
    await page.wait_for_function('document.querySelector(".ds-avatar-slot img").naturalWidth > 0')
    # Same node id in another workspace gets its own preference.
    api.state['workspace'] = '/work/second'
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(page.locator('#ds-character-toggle')).to_have_attribute('aria-pressed','false')
    await expect(page.locator('.ds-node')).to_have_attribute('data-character','robot')
    await page.get_by_role('button',name='Terminal tools for Claude').click()
    await page.get_by_label('Character for Claude',exact=True).select_option('rabbit')
    api.state['workspace'] = '/work/first'
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(page.locator('.ds-node')).to_have_attribute('data-character','wolf')
    await expect(page.locator('#ds-character-toggle')).to_have_attribute('aria-pressed','true')
    await page.evaluate('''() => {Storage.prototype.setItem=function(){throw new DOMException('Blocked','SecurityError')};Storage.prototype.getItem=function(){throw new DOMException('Blocked','SecurityError')};}''')
    await page.get_by_label('Character for Claude',exact=True).select_option('fox')
    await expect(page.locator('.ds-node')).to_have_attribute('data-character','fox')
    await expect(page.locator('.ds-feedback')).to_contain_text('Browser storage is unavailable')
    await page.locator('#ds-character-toggle').click()
    await expect(page.locator('#ds-character-toggle')).to_have_attribute('aria-pressed','false')
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(page.locator('.ds-node')).to_have_attribute('data-character','fox')
    assert not errors


async def test_unknown_character_falls_back_to_rabbit_and_missing_asset_is_bounded(board_page):
    page, api, errors = board_page
    api.state['providers'].append({'key':'future-provider','label':'Future provider','models':[]})
    await page.route('**/assets/avatars/rabbit.png', lambda route: route.fulfill(status=404,body='missing'))
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(page.get_by_role('button',name='Refresh workspace')).to_be_enabled()
    await add(page,'future-provider')
    card=page.locator('.ds-node')
    await expect(card).to_have_attribute('data-character','rabbit')
    await page.locator('#ds-character-toggle').click()
    await card.get_by_role('button',name='Place character for Future provider').click()
    await expect(page.locator('.ds-avatar-fallback')).to_have_attribute('aria-label','rabbit character image unavailable')
    await expect(page.locator('.ds-avatar-fallback')).to_be_visible()
    await expect(card.locator('.xterm')).to_be_visible()
    await card.get_by_label('Character for Future provider',exact=True).select_option('penguin')
    await page.wait_for_function('document.querySelector(".ds-avatar-slot img").naturalWidth>0')
    await expect(page.locator('.ds-avatar-fallback')).to_be_hidden()
    assert not errors


@pytest.mark.parametrize('viewport_size', [(1500,980),(390,844)])
async def test_three_character_nodes_render_without_avatar_or_wire_overlap(board_page, viewport_size):
    import json
    page, api, errors = board_page
    await page.set_viewport_size({'width':viewport_size[0],'height':viewport_size[1]})
    for provider in ['codex','anthropic','antigravity']:
        await add(page,provider)
        await page.locator('.ds-node').last.get_by_role('button',name='Terminal tools for ',exact=False).click()
    await page.get_by_role('button',name='Tile terminals',exact=True).click()
    await expect(page.get_by_role('button',name='Tile terminals',exact=True)).to_be_enabled()
    await page.get_by_role('button',name='Link from Codex').click()
    await page.get_by_role('button',name='Link to Claude').click()
    await page.get_by_role('button',name='Show connections and delivery activity').click()
    for i in range(3):
        api.sockets[f'node-{i+1}'].send(json.dumps({'type':'output','data':'\r\n\x1b[33mSame terminal, quieter frame\x1b[0m\r\n\r\nReadable monospace output.\r\nThe existing session stays here.\r\n'}))
    await page.wait_for_timeout(100)
    await page.screenshot(path=f'/tmp/dream271-terminal-three-{viewport_size[0]}.png')
    await page.locator('#ds-character-toggle').click()
    await page.wait_for_function('Array.from(document.querySelectorAll(".ds-avatar-slot img")).every(img=>img.complete&&img.naturalWidth>0)')
    for index,character in enumerate(['penguin','robot','fox'],1):
        card = page.locator(f'[data-node="node-{index}"]')
        await expect(card).to_have_attribute('data-character',character)
        avatar, terminal, head, pane = await page.locator('.ds-avatar-slot').nth(await card.evaluate('(n)=>Array.from(document.querySelectorAll(".ds-node")).indexOf(n)')).bounding_box(), await card.locator('.ds-terminal').bounding_box(), await card.locator('.ds-node-head').bounding_box(), await card.bounding_box()
        assert abs(terminal['x']-pane['x']) <= 2, 'No reserved avatar gutter'
        anchor=page.locator(f'.ds-avatar-anchor[data-avatar-node="node-{index}"]')
        if avatar:
            wrapper=await anchor.bounding_box()
            assert avatar['height']==104, 'Medium is the larger default'
            assert wrapper['x'] >= pane['x'] and wrapper['x']+wrapper['width'] <= pane['x']+pane['width']
            assert wrapper['y']+wrapper['height'] <= head['y']+head['height']
            assert wrapper['y']+wrapper['height'] <= terminal['y'], 'Default character cannot cover terminal text'
            title_end=await card.locator('h2').evaluate('(e)=>{const r=document.createRange();r.selectNodeContents(e);return r.getBoundingClientRect().right;}')
            assert wrapper['x'] >= title_end, 'Character ports cannot cover the pane title'
            for control in await card.locator('.ds-node-head button').all():
                box=await control.bounding_box()
                if box: assert wrapper['x']+wrapper['width']<=box['x'] or wrapper['x']>=box['x']+box['width']
        else:
            await expect(anchor).to_be_hidden()
            for port in await card.locator('.ds-header-port').all():
                await expect(port).to_be_visible()
                await expect(port).to_be_enabled()
        assert await card.evaluate('(n)=>n.scrollWidth<=n.clientWidth')
        if index > 1 and viewport_size[0] < 700:
            previous = await page.locator(f'[data-node="node-{index-1}"]').bounding_box()
            assert previous['y']+previous['height'] <= pane['y'], 'Wrapped footer rows must not overlap the next pane'
    await page.screenshot(path=f'/tmp/dream271-characters-three-{viewport_size[0]}.png')
    await page.get_by_role('button',name='Refresh workspace').click()
    assert [await page.locator(f'[data-node="node-{i}"]').get_attribute('data-character') for i in range(1,4)] == ['penguin','robot','fox']
    assert not errors


async def test_disconnected_terminal_refuses_input_and_sign_in_still_works(board_page):
    # Was test_drafts_survive_refresh_and_failed_send_without_replay; its draft half went with the draft box (2026-10-10).
    page, api, errors = board_page
    await add(page)
    await api.sockets['node-1'].close()
    await expect(page.locator('.ds-terminal-status')).to_contain_text('Disconnected')
    await page.get_by_role('button', name='Sign in to Claude').click()
    assert await page.evaluate('logins') == ['anthropic']
    await page.locator('.xterm-helper-textarea').focus()
    await page.keyboard.type('x')
    await expect(page.locator('.ds-node-note')).to_contain_text('input was not sent')
    assert not errors


async def test_move_resize_link_unlink_and_close(board_page):
    page, api, errors = board_page
    await add(page)
    move = page.get_by_role('button', name='Move Claude; use arrow keys')
    await move.focus()
    await move.press('ArrowRight')
    await expect(page.locator('[data-node="node-1"]')).to_have_css('left', '40px')
    resize = page.get_by_role('button', name='Resize Claude; use arrow keys')
    await resize.focus()
    await resize.press('ArrowRight')
    await expect(page.locator('[data-node="node-1"]')).to_have_css('width', '590px')
    handle = await move.bounding_box()
    await page.mouse.move(handle['x'] + 5, handle['y'] + 5)
    await page.mouse.down()
    await page.mouse.move(handle['x'] + 55, handle['y'] + 45)
    await page.mouse.up()
    await expect(page.locator('[data-node="node-1"]')).to_have_css('left', '90px')
    await add(page, 'antigravity')
    await page.get_by_role('button', name='Link from Claude').click()
    await page.get_by_role('button', name='Link to Gemini').click()
    await expect(page.locator('.ds-wire')).to_have_count(1)
    assert api.state['links'][0]['source'] == 'node-1'
    assert api.state['links'][0]['target'] == 'node-2'
    await page.get_by_role('button', name='Show connections and delivery activity').click()
    await page.locator('.ds-channel-connections>summary').click()
    await page.get_by_role('button', name='Unlink link-1').click()
    await expect(page.locator('.ds-wire')).to_have_count(0)
    await page.get_by_role('button', name='Show connections and delivery activity').click()
    await page.get_by_role('button', name='Close Gemini', exact=True).click()
    await expect(page.locator('.ds-node')).to_have_count(1)
    assert not errors


async def test_group_task_and_rendered_viewports(board_page):
    page, api, errors = board_page
    await add(page)
    await add(page, 'codex')
    await page.get_by_role('button', name='Plan a group task').click()
    await page.get_by_label('Research terminals').select_option(['node-1'])
    await page.get_by_label('Synthesis terminal').select_option('node-2')
    await page.get_by_label('Group task prompt').fill('Compare approaches with evidence')
    await page.get_by_role('button', name='Run group task', exact=True).click()
    await expect(page.get_by_label('Group task prompt')).to_have_value('')
    assert api.calls[-1] == {'action': 'group', 'targets': ['node-1'], 'synthesis_target': 'node-2', 'prompt': 'Compare approaches with evidence'}
    await page.get_by_role('button', name='Close group task').click()
    await page.get_by_role('button', name='Show connections and delivery activity').click()
    await page.screenshot(path='/tmp/dream-deepsleep-1500.png')
    await page.set_viewport_size({'width': 390, 'height': 844})
    await page.screenshot(path='/tmp/dream-deepsleep-390.png')
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors


async def test_layout_refusal_restores_position_and_terminal_is_not_recreated(board_page):
    page, api, errors = board_page
    await add(page)
    api.fail = 'layout'
    await page.get_by_role('button', name='Move Claude; use arrow keys').press('ArrowRight')
    await expect(page.locator('.ds-feedback')).to_contain_text('was not sent')
    await expect(page.locator('[data-node="node-1"]')).to_have_css('left', '30px')
    assert sum('token' in message for _, message in api.socket_messages) == 1
    assert not errors


async def test_unknown_result_locks_changes_until_refresh(board_page):
    # Was test_unknown_send_requires_refresh_and_preserves_draft: the draft's Send is gone (2026-10-10); a change
    # whose reply is lost still locks the board until a refresh shows what happened.
    page, api, errors = board_page
    await add(page)
    async def lost_reply(route):
        if route.request.method == 'POST':
            await route.abort('connectionreset')
        else:
            await api.route(route)
    await page.route('**/api/deepsleep', lost_reply)
    await page.get_by_role('button', name='Add terminal', exact=True).click()
    await expect(page.get_by_role('button', name='Add terminal', exact=True)).to_be_disabled()
    await page.get_by_role('button', name='Refresh workspace').click()
    await expect(page.get_by_role('button', name='Add terminal', exact=True)).to_be_enabled()
    assert not errors


async def test_pending_layout_reply_cannot_reset_new_pointer_drag(board_page):
    import asyncio
    page, api, errors = board_page
    await add(page)
    held = asyncio.Event()
    release = asyncio.Event()
    first_layout = True
    async def hold_layout(route):
        nonlocal first_layout
        body = route.request.post_data_json if route.request.method == 'POST' else None
        if body and body.get('action') == 'layout' and first_layout:
            first_layout = False
            held.set()
            await release.wait()
        await api.route(route)
    await page.route('**/api/deepsleep', hold_layout)
    card = page.locator('[data-node="node-1"]')
    await page.get_by_role('button', name='Resize Claude; use arrow keys').press('ArrowRight')
    await asyncio.wait_for(held.wait(), timeout=4)
    handle = await page.get_by_role('button', name='Move Claude; use arrow keys').bounding_box()
    await page.mouse.move(handle['x'] + 5, handle['y'] + 5)
    await page.mouse.down()
    await page.mouse.move(handle['x'] + 55, handle['y'] + 45)
    await expect(card).to_have_css('left', '80px')
    release.set()
    # The earlier save finishes while the pointer is still held down.
    await expect(page.get_by_role('button', name='Add terminal', exact=True)).to_be_enabled()
    await expect(card).to_have_css('left', '80px')
    await page.get_by_role('button', name='Refresh workspace').evaluate('(button) => button.click()')
    await expect(page.get_by_role('button', name='Refresh workspace')).to_be_enabled()
    await expect(card).to_have_css('left', '80px')
    await page.mouse.up()
    await expect(page.get_by_role('button', name='Add terminal', exact=True)).to_be_enabled()
    await expect(card).to_have_css('left', '80px')
    await expect(card).to_have_css('width', '590px')
    assert api.state['nodes'][0]['layout']['x'] == 80
    assert api.state['nodes'][0]['layout']['y'] == 70
    assert not errors


async def test_authenticated_startup_accepts_cursor_reply_and_keyboard_but_stopping_does_not(board_page):
    import asyncio
    import json
    page, api, errors = board_page
    cursor_reply = asyncio.Event()
    def startup_socket(ws):
        api.sockets['node-1'] = ws
        def received(message):
            data = json.loads(message)
            api.socket_messages.append(('node-1', data))
            if 'token' in data:
                ws.send(json.dumps({'type': 'state', 'state': 'starting'}))
                ws.send(json.dumps({'type': 'output', 'data': '\x1b[6n'}))
            elif data.get('type') == 'input' and data.get('data', '').endswith('R'):
                cursor_reply.set()
        ws.on_message(received)
    await page.route_web_socket('**/ws/deepsleep/*', startup_socket)
    await add(page)
    await asyncio.wait_for(cursor_reply.wait(), timeout=3)
    card = page.locator('[data-node="node-1"]')
    await expect(card.locator('.ds-state')).to_have_text('starting')
    inputs = [msg['data'] for _, msg in api.socket_messages if msg.get('type') == 'input']
    assert any(data.startswith('\x1b[') and data.endswith('R') for data in inputs)
    await card.locator('.xterm-helper-textarea').focus()
    await page.keyboard.type('startup answer')
    assert len([msg for _, msg in api.socket_messages if msg.get('type') == 'input']) > len(inputs)
    api.sockets['node-1'].send(json.dumps({'type': 'state', 'state': 'stopping'}))
    await expect(card.locator('.ds-state')).to_have_text('stopping')
    before = len([msg for _, msg in api.socket_messages if msg.get('type') == 'input'])
    await page.keyboard.type('blocked')
    assert len([msg for _, msg in api.socket_messages if msg.get('type') == 'input']) == before
    await expect(card.locator('.ds-node-note')).to_contain_text('input was not sent')
    api.sockets['node-1'].send(json.dumps({'type': 'state', 'state': 'ready'}))
    await expect(card.locator('.ds-node-note')).to_have_text('')
    # A later transport/provider error must survive the readiness transition.
    api.sockets['node-1'].send(json.dumps({'type': 'state', 'state': 'stopping'}))
    await expect(card.locator('.ds-state')).to_have_text('stopping')
    await page.keyboard.type('blocked again')
    api.sockets['node-1'].send(json.dumps({'type': 'error', 'message': 'Provider requires sign-in'}))
    api.sockets['node-1'].send(json.dumps({'type': 'state', 'state': 'ready'}))
    await expect(card.locator('.ds-node-note')).to_have_text('Provider requires sign-in')
    assert not errors


async def test_held_scrollbar_drag_keeps_history_after_new_output(board_page):
    """A thumb held beyond the wheel-settle interval remains a user gesture."""
    import json
    page,api,errors=board_page
    await add(page)
    card=page.locator('.ds-node')
    socket=api.sockets['node-1']
    socket.send(json.dumps({'type':'output','data':''.join(f'\r\nDRAG_HISTORY_{i:03}' for i in range(240))}))
    await page.wait_for_function('()=>fixtureTerminals[0].buffer.active.baseY>200')
    viewport=card.locator('.xterm-viewport')
    metrics=await viewport.evaluate('(e)=>{const r=e.getBoundingClientRect();return {x:r.right-5,y:r.bottom-(e.clientHeight/e.scrollHeight*e.clientHeight)/2,height:e.clientHeight};}')
    await page.mouse.move(metrics['x'],metrics['y'])
    await page.mouse.down()
    for step in range(1,6):
        await page.mouse.move(metrics['x'],metrics['y']-metrics['height']*step/12)
        await page.wait_for_timeout(100)
    await page.wait_for_function('()=>{const b=fixtureTerminals[0].buffer.active;return b.viewportY<b.baseY-20;}')
    await page.mouse.up()
    await page.wait_for_timeout(300)
    anchor=await page.evaluate('fixtureTerminals[0].buffer.active.viewportY')
    socket.send(json.dumps({'type':'output','data':'\r\nOUTPUT_AFTER_HELD_DRAG'}))
    await page.wait_for_function('()=>{const b=fixtureTerminals[0].buffer.active;return b.getLine(b.length-1).translateToString().includes("OUTPUT_AFTER_HELD_DRAG");}')
    assert await page.evaluate('fixtureTerminals[0].buffer.active.viewportY')==anchor
    await expect(card.get_by_role('button',name='Follow latest output in Claude')).to_be_visible()
    assert not errors


async def test_resize_waits_for_pending_output_and_uses_latest_host_size(board_page):
    """Queued stream data must precede resize; no final observer change is lost."""
    import json
    page,api,errors=board_page
    await add(page)
    card=page.locator('.ds-node')
    await card.get_by_role('button',name='Terminal tools for Claude').click()
    await page.evaluate('''()=>{const t=fixtureTerminals[0];window.resizeCalls=[];const resize=t.resize.bind(t);t.resize=(...size)=>{resizeCalls.push(size);resize(...size);};const write=t.write.bind(t);window.releaseQueuedOutput=null;t.write=(data,callback)=>{if(data.includes('QUEUED_STREAM_BEGIN'))window.releaseQueuedOutput=()=>write(data,callback);else write(data,callback);};}''')
    api.sockets['node-1'].send(json.dumps({'type':'output','data':'\r\nQUEUED_STREAM_BEGIN\r\n'+''.join(f'QUEUE_ROW_{i:04}\r\n' for i in range(100))+'QUEUED_STREAM_END'}))
    await page.wait_for_function('releaseQueuedOutput!==null')
    before=await page.evaluate('resizeCalls.length')
    await card.evaluate('(e)=>{e.style.width="400px";e.style.height="600px";}')
    await page.wait_for_timeout(80)
    await card.evaluate('(e)=>{e.style.width="850px";e.style.height="470px";}')
    await page.wait_for_timeout(80)
    assert await page.evaluate('resizeCalls.length')==before
    await page.evaluate('releaseQueuedOutput()')
    await page.wait_for_function('resizeCalls.length>0')
    await page.wait_for_function('''()=>{const t=fixtureTerminals[0],b=t.buffer.normal;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).some(s=>s.includes('QUEUED_STREAM_END'));}''')
    measured=await page.evaluate('''()=>{const t=fixtureTerminals[0],check=new FitAddon.FitAddon();t.loadAddon(check);const proposed=check.proposeDimensions();return {cols:t.cols,rows:t.rows,proposed};}''')
    assert measured['cols']==measured['proposed']['cols'] and measured['rows']==measured['proposed']['rows']
    text=await page.evaluate('''()=>{const b=fixtureTerminals[0].buffer.normal;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).join('\\n');}''')
    for i in range(100):assert text.count(f'QUEUE_ROW_{i:04}')==1
    assert text.count('QUEUED_STREAM_BEGIN')==1 and text.count('QUEUED_STREAM_END')==1
    assert not [call for call in api.calls if call['action']=='send']
    assert not errors


async def test_prompt_metadata_retains_disconnected_paint_and_rejects_invalid_offset(board_page):
    import json
    page,api,errors=board_page
    await add(page)
    card=page.locator('.ds-node')
    socket=api.sockets['node-1']
    # Let creation's observed geometry settle before introducing an active prompt.
    await page.wait_for_timeout(200)
    socket.send(json.dumps({'type':'output','data':'\r\nPROMPT_RETAINED\x1b]777;dream-prompt;0\x07'}))
    await page.wait_for_function('''()=>{const b=fixtureTerminals[0].buffer.normal;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).some(s=>s.includes('PROMPT_RETAINED'));}''')
    await socket.close()
    await expect(card.locator('.ds-terminal-status')).to_contain_text('Disconnected')
    await card.evaluate('(e)=>{e.style.width="400px";e.style.height="600px";}')
    await page.wait_for_timeout(150)
    text=await page.evaluate('''()=>{const b=fixtureTerminals[0].buffer.normal;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).join('\\n');}''')
    assert text.count('PROMPT_RETAINED')==1
    assert not errors


async def test_invalid_prompt_offsets_do_not_erase_committed_text(board_page):
    import json
    page,api,errors=board_page
    await add(page)
    card=page.locator('.ds-node')
    socket=api.sockets['node-1']
    for offset in ['-2','2000','1.5','oops','done']:
        socket.send(json.dumps({'type':'output','data':f'\r\nCOMMITTED_{offset}\x1b]777;dream-prompt;{offset}\x07'}))
        await page.wait_for_function('''marker=>{const b=fixtureTerminals[0].buffer.normal;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).some(s=>s.includes(marker));}''',arg=f'COMMITTED_{offset}')
        await card.evaluate('(e)=>{e.style.width="400px";e.style.height="600px";}')
        await page.wait_for_timeout(50)
        await card.evaluate('(e)=>{e.style.width="700px";e.style.height="450px";}')
        await page.wait_for_timeout(50)
    text=await page.evaluate('''()=>{const b=fixtureTerminals[0].buffer.normal;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).join('\\n');}''')
    for offset in ['-2','2000','1.5','oops','done']:assert text.count(f'COMMITTED_{offset}')==1
    assert not errors


async def test_resize_during_pending_prompt_redraw_keeps_final_dimensions(board_page):
    import json
    page,api,errors=board_page
    await add(page)
    card=page.locator('.ds-node')
    await card.get_by_role('button',name='Terminal tools for Claude').click()
    api.sockets['node-1'].send(json.dumps({'type':'output','data':'\r\nACTIVE_PROMPT\x1b]777;dream-prompt;0\x07'}))
    await page.wait_for_function('''()=>{const b=fixtureTerminals[0].buffer.normal;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).some(s=>s.includes('ACTIVE_PROMPT'));}''')
    await page.evaluate(r'''()=>{const t=fixtureTerminals[0],write=t.write.bind(t);window.releasePromptRedraw=null;let held=false;t.write=(data,callback)=>{if(!held && data.includes('\x1b[J')){held=true;window.releasePromptRedraw=()=>write(data,callback);}else write(data,callback);};}''')
    await card.evaluate('(e)=>{e.style.width="400px";e.style.height="600px";}')
    await page.wait_for_function('releasePromptRedraw!==null')
    await card.evaluate('(e)=>{e.style.width="850px";e.style.height="470px";}')
    await page.wait_for_timeout(80)
    api.sockets['node-1'].send(json.dumps({'type':'output','data':'\r\nOUTPUT_DURING_REDRAW_A\r\nOUTPUT_DURING_REDRAW_B'}))
    await page.wait_for_timeout(30)
    await page.evaluate('releasePromptRedraw()')
    await page.wait_for_function('''()=>{const t=fixtureTerminals[0],check=new FitAddon.FitAddon();t.loadAddon(check);const p=check.proposeDimensions();return t.cols===p.cols&&t.rows===p.rows;}''')
    last=[message for _,message in api.socket_messages if message.get('type')=='resize'][-1]
    size=await page.evaluate('({cols:fixtureTerminals[0].cols,rows:fixtureTerminals[0].rows})')
    assert last['cols']==size['cols'] and last['rows']==size['rows']
    await page.wait_for_function('''()=>{const b=fixtureTerminals[0].buffer.normal;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).some(s=>s.includes('OUTPUT_DURING_REDRAW_B'));}''')
    text=await page.evaluate(r'''()=>{const b=fixtureTerminals[0].buffer.normal;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).join('\n');}''')
    assert text.count('OUTPUT_DURING_REDRAW_A')==1 and text.count('OUTPUT_DURING_REDRAW_B')==1
    assert not errors
