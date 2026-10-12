"""Native terminal host controls with real xterm and controlled browser boundaries."""
from copy import deepcopy

import pytest
from playwright.async_api import async_playwright, expect

from test_deepsleep_ui import BoardAPI, STATIC, avatar_asset, xterm_assets


class NativeBoardAPI(BoardAPI):
    def __init__(self):
        super().__init__()
        self.state['workspace'] = '/work/sample-repo'
        self.state['providers'].extend([{'key': 'machx', 'label': 'Local', 'models': []}, {'key':'grok','label':'Grok','models':[]}])
        self.state['handoffs'] = []
        self.uploads = []
        self.file_calls = []
        self.files_fail = False
        self.upload_fail = False

    async def route(self, route):
        body = route.request.post_data_json if route.request.method == 'POST' else None
        if body and body.get('action') == 'handoff':
            self.calls.append(body)
            if not any(item['id']==body['id'] for item in self.state['handoffs']):
                self.state['handoffs'].append({'id':body['id'],'source':body['source'],'target':body['target'],'text':'[External linked content]\n'+body['prompt'],'state':'prepared'})
            return await route.fulfill(json={'result':deepcopy(self.state)})
        if not body or body.get('action') != 'create':
            return await super().route(route)
        self.calls.append(body)
        key = body['provider']
        title = next(p['label'] for p in self.state['providers'] if p['key'] == key)
        native = body['profile'] == 'native'
        commands = {'model': '/model', 'effort': '/model' if key == 'codex' else '/effort', 'permissions': '/permissions'} if native else {'model': '/model', 'effort': '/effort', 'changes': '/changes'}
        if key == 'anthropic' and native:
            commands.update(remote='/remote-control', voice='/voice')
        if key == 'codex' and native:
            commands['voice'] = '/voice settings'
        self.state['nodes'].append({
            'id': 'node-' + str(len(self.state['nodes']) + 1), 'provider': key,
            'title': title, 'profile': body['profile'], 'model': body.get('model', ''), 'state': 'ready',
            'workspace': self.state['workspace'], 'initial_effort': 'high', 'initial_permissions': 'ask',
            'capabilities': {'linked': True, 'automatic':not native, 'commands': commands},
            'layout': {'x': 30 + len(self.state['nodes']) * 620, 'y': 30, 'width': 580, 'height': 570},
        })
        await route.fulfill(json={'result': deepcopy(self.state)})

    async def files(self, route):
        payload = route.request.post_data_json
        self.file_calls.append(payload)
        if self.files_fail:
            return await route.fulfill(status=409, json={'error': 'Workspace unavailable'})
        entries = [{'name': 'notes.md', 'path': 'docs/notes.md', 'directory': False}] if payload['path'] else [
            {'name': 'docs', 'path': 'docs', 'directory': True},
            {'name': 'README.md', 'path': 'README.md', 'directory': False},
        ]
        await route.fulfill(json={'path': payload['path'], 'entries': entries, 'workspace': self.state['workspace']})

    async def upload(self, route):
        self.uploads.append(route.request.url)
        if self.upload_fail:
            return await route.fulfill(status=413, json={'error': 'Upload refused'})
        await route.fulfill(json={'id': 'fixture-upload', 'path': 'uploads/note.txt', 'workspace': self.state['workspace']})


@pytest.fixture
async def native_page():
    terminal_js, fit_js, terminal_css = xterm_assets()
    api = NativeBoardAPI()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=['--disable-gpu'])
        page = await browser.new_page(viewport={'width': 1500, 'height': 980})
        page.set_default_timeout(4000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.route('**/api/deepsleep', api.route)
        await page.route('**/assets/avatars/*', avatar_asset)
        await page.route('**/api/deepsleep/files', api.files)
        await page.route('**/api/deepsleep/upload?*', api.upload)
        await page.route_web_socket('**/ws/deepsleep/*', api.socket)
        await page.route('http://dream.test/', lambda route: route.fulfill(content_type='text/html', body='''<!doctype html><html data-dream-view="deepsleep"><body><script>const TOKEN='fixture-token';window.copied=[];Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>copied.push(text)},configurable:true});window.SpeechRecognition=undefined;window.webkitSpeechRecognition=undefined;</script></body></html>'''))
        await page.goto('http://dream.test/')
        for path in [terminal_css, STATIC / 'deepsleep.css', STATIC / 'deepsleep-drawer.css']:
            await page.add_style_tag(path=str(path))
        for path in [terminal_js, fit_js, STATIC / 'deepsleep-drawer.js', STATIC / 'deepsleep-activity.js', STATIC / 'deepsleep-flow.js', STATIC / 'deepsleep.js']:
            await page.add_script_tag(path=str(path))
        await expect(page.get_by_label('New terminal provider')).to_have_value('anthropic')
        yield page, api, errors
        await browser.close()


async def create(page, provider='anthropic', profile='native', expand=True):
    await page.get_by_label('New terminal provider').select_option(provider)
    if provider != 'machx':
        await page.get_by_label('Terminal experience').select_option(profile)
    await page.get_by_role('button', name='Add terminal', exact=True).click()
    await expect(page.get_by_role('button', name='Add terminal', exact=True)).to_be_enabled()
    node_id = await page.locator('.ds-node').last.get_attribute('data-node')
    card = page.locator('[data-node="' + node_id + '"]')
    if expand:
        await card.get_by_role('button', name='Terminal tools for ', exact=False).click()
    return card


async def test_native_shortcuts_type_into_the_terminal_prompt_and_never_submit(native_page):
    # The draft box went (the owner, 2026-10-10): its steps are gone; what it filled now types into the terminal's prompt.
    page, api, errors = native_page
    card = await create(page)
    assert api.calls[0]['profile'] == 'native'
    await expect(card.locator('.xterm')).to_be_visible()
    await expect(card.get_by_label('Launch workspace')).to_contain_text('sample-repo')
    await expect(card.locator('.ds-launch-info')).to_have_text('At launch: effort high · permissions ask')
    await expect(card.get_by_label('Prompt Claude')).to_have_count(0)
    for name, command in (('Model in Claude', '/model'), ('Voice controls in Claude', '/voice'), ('Remote control in Claude', '/remote-control')):
        await card.get_by_role('button', name=name).click()
        assert command in ''.join(m.get('data', '') for _, m in api.socket_messages if m.get('type') == 'input')
    assert '\r' not in ''.join(m.get('data', '') for _, m in api.socket_messages if m.get('type') == 'input') and '\n' not in ''.join(m.get('data', '') for _, m in api.socket_messages if m.get('type') == 'input')                    # typed, never submitted
    await expect(card.locator('.ds-node-note')).to_contain_text('Nothing has been sent')
    assert not [call for call in api.calls if call['action'] == 'send']
    await card.locator('.xterm-helper-textarea').focus()
    await page.keyboard.type('/help')
    assert '/help' in ''.join(m.get('data', '') for _, m in api.socket_messages if m.get('type') == 'input')
    assert not errors

async def test_native_capability_boundary_preserves_managed_links(native_page):
    page, api, errors = native_page
    native = await create(page, 'codex')
    await expect(native.locator('[data-command=remote]')).to_be_disabled()
    await expect(native.locator('[data-command=remote]')).to_be_hidden()
    await expect(native.get_by_role('button', name='Voice controls in Codex')).to_be_enabled()
    await expect(native.get_by_role('button', name='Link from Codex')).to_be_enabled()
    managed = await create(page, 'anthropic', 'dream')
    await expect(managed.get_by_role('button', name='Link from Claude')).to_be_enabled()
    local = await create(page, 'machx')
    await expect(page.get_by_label('Terminal experience')).to_have_value('dream')
    await expect(page.get_by_label('Terminal experience')).to_be_disabled()
    assert api.calls[-1]['profile'] == 'dream'
    await managed.get_by_role('button', name='Link from Claude').click()
    await local.get_by_role('button', name='Link to Local').click()
    await expect(page.locator('.ds-wire')).to_have_count(1)
    await page.get_by_role('button', name='Plan a group task').click()
    assert await page.get_by_label('Research terminals').locator('option').all_text_contents() == ['Claude', 'Local']
    await page.get_by_role('button', name='Close group task').click()
    # The draft's Send is gone (2026-10-10); a workflow terminal has the Chat tab's controls, a native CLI its own.
    await expect(managed.locator('.ds-chat-row')).to_have_count(1)
    await expect(native.locator('.ds-chat-row')).to_have_count(0)
    assert not errors


async def test_files_and_uploads_type_their_paths_into_the_prompt_with_error_recovery(native_page):
    # The draft box went (the owner, 2026-10-10): its steps are gone; what it filled now types into the terminal's prompt.
    page, api, errors = native_page
    card = await create(page)
    await card.get_by_role('button', name='File explorer for Claude').click()
    await card.get_by_role('button', name='Open folder docs').click()
    await card.get_by_role('button', name='Add file notes.md').click()
    assert '"/work/sample-repo/docs/notes.md"' in ''.join(m.get('data', '') for _, m in api.socket_messages if m.get('type') == 'input')
    assert api.file_calls[-1] == {'node': 'node-1', 'path': 'docs'}
    await card.locator('input[type=file]').set_input_files({'name': 'note.txt', 'mimeType': 'text/plain', 'buffer': b'note'})
    for _ in range(50):
        if '"/work/sample-repo/uploads/note.txt"' in ''.join(m.get('data', '') for _, m in api.socket_messages if m.get('type') == 'input'):
            break
        await page.wait_for_timeout(20)
    assert '"/work/sample-repo/uploads/note.txt"' in ''.join(m.get('data', '') for _, m in api.socket_messages if m.get('type') == 'input')
    assert len(api.uploads) == 1
    api.upload_fail = True
    await card.locator('input[type=file]').set_input_files({'name': 'bad.txt', 'mimeType': 'text/plain', 'buffer': b'note'})
    await expect(card.locator('.ds-node-note')).to_contain_text('Upload refused')
    assert 'bad.txt' not in ''.join(m.get('data', '') for _, m in api.socket_messages if m.get('type') == 'input')
    api.files_fail = True
    await card.get_by_role('button', name='File explorer for Claude').click()
    await expect(card.locator('.ds-file-list')).to_contain_text('Workspace unavailable')
    api.files_fail = False
    await card.get_by_role('button', name='Retry loading files').click()
    await expect(card.get_by_role('button', name='Open folder docs')).to_be_visible()
    await card.get_by_role('button', name='Open folder docs').focus()
    await page.keyboard.press('Escape')
    await expect(card.locator('.ds-file-explorer')).to_be_hidden()
    assert not [call for call in api.calls if call['action'] == 'send']
    assert '\r' not in ''.join(m.get('data', '') for _, m in api.socket_messages if m.get('type') == 'input') and '\n' not in ''.join(m.get('data', '') for _, m in api.socket_messages if m.get('type') == 'input')                    # paths typed, never submitted
    assert not errors

async def test_managed_commands_do_not_become_task_input(native_page):
    # The draft box went (the owner, 2026-10-10): its steps are gone; what it filled now types into the terminal's prompt.
    page, api, errors = native_page
    card = await create(page, 'anthropic', 'dream')
    await card.get_by_role('button', name='Model in Claude').click()
    assert await page.evaluate('copied') == ['/model']
    assert not [call for call in api.calls if call['action'] == 'send']
    assert not [msg for _, msg in api.socket_messages if msg.get('type') == 'input']
    assert not errors

async def test_native_tile_resizes_and_its_footer_stays_reachable_on_a_phone(native_page):
    # Was test_rich_input_keyboard_clipboard_failure_and_responsive_render: Rich input and Copy draft went with the
    # draft box (2026-10-10); the resize and the phone-width checks stay.
    page, api, errors = native_page
    card = await create(page)
    await card.get_by_role('button', name='Resize Claude; use arrow keys').press('ArrowRight')
    await expect(card).to_have_css('width', '590px')
    await page.screenshot(path='/tmp/dream-native-terminal-desktop.png')
    await page.set_viewport_size({'width': 390, 'height': 844})
    tools = card.get_by_role('button', name='Terminal tools for Claude')
    await tools.scroll_into_view_if_needed()
    await expect(tools).to_be_visible()
    await page.screenshot(path='/tmp/dream-native-terminal-mobile.png')
    assert not errors

async def test_small_terminal_keeps_footer_reachable(native_page):
    page, api, errors = native_page
    card = await create(page)
    api.state['nodes'][0]['layout'].update(width=360, height=320)
    await page.get_by_role('button', name='Refresh workspace').click()
    await expect(card).to_have_css('width', '360px')
    bounds = await card.bounding_box()
    # The bottom bar's Tools and Out stay inside the resized terminal (the draft's Copy button went, 2026-10-10).
    for name in ('Terminal tools for Claude', 'Link from Claude'):
        button = await card.get_by_role('button', name=name).bounding_box()
        assert button['y'] + button['height'] <= bounds['y'] + bounds['height'], name + ' must stay inside the resized terminal'
    assert not errors

@pytest.mark.parametrize('viewport_size,pane_size', [((1500,980),(1000,760)), ((390,844),(360,550))])
async def test_workflow_minimal_footer_readable_and_shortcuts_copy_commands(native_page, viewport_size, pane_size):
    # Was ..._and_queue_failure_recovery: that recovery was the draft's queued send, gone with the draft (2026-10-10).
    import json
    page, api, errors = native_page
    await page.set_viewport_size({'width':viewport_size[0], 'height':viewport_size[1]})
    card = await create(page, 'anthropic', 'dream', expand=False)
    api.state['nodes'][0]['layout'].update(x=6,y=6,width=pane_size[0],height=pane_size[1])
    await page.get_by_role('button', name='Refresh workspace').click()
    await expect(card).to_have_css('width', f'{pane_size[0]}px')
    prompt = card.get_by_label('Prompt Claude')
    repo = card.get_by_label('Launch workspace')
    effort = card.get_by_role('button', name='Effort in Claude')
    await expect(repo).to_have_text('sample-repo')
    await expect(repo).to_have_attribute('title', 'Launch workspace: /work/sample-repo')
    await expect(effort).to_have_text('Effort ↗')
    await expect(card.locator('.ds-tools-drawer')).to_be_hidden()
    await expect(card.get_by_label('Handoff receiver for Claude')).to_be_hidden()
    for name in ['Reference attached files in the prompt','File explorer for Claude','Dictate into the prompt']:
        await expect(card.get_by_role('button',name=name)).to_be_hidden()
    remote = card.locator('[data-command=remote]')
    await expect(remote).to_be_hidden()
    await expect(remote).to_be_disabled()
    await expect(remote).to_have_attribute('title', 'Remote control is not available for Dream workflow')
    await expect(card.get_by_role('button', name='Link to Claude')).to_be_visible()
    await expect(card.get_by_role('button', name='Link from Claude')).to_be_visible()
    await expect(prompt).to_have_count(0)                                       # no draft box (2026-10-10)
    repo_box, tools_box = await repo.bounding_box(), await card.get_by_role('button', name='Terminal tools for Claude').bounding_box()
    assert repo_box['x'] >= tools_box['x'] + tools_box['width']
    assert abs(repo_box['y']+repo_box['height']/2-tools_box['y']-tools_box['height']/2) <= 2
    assert await repo.evaluate("n=>n.previousElementSibling.getAttribute('aria-label')") == 'Terminal tools for Claude'
    assert await repo.evaluate('(n) => parseFloat(getComputedStyle(n).fontSize)') >= 11
    bounds, transcript = await card.bounding_box(), await card.locator('.ds-terminal').bounding_box()
    assert transcript['height'] / bounds['height'] >= .60
    assert await card.evaluate('(n) => n.scrollWidth <= n.clientWidth')
    await card.get_by_role('button', name='Terminal tools for Claude').click()
    await expect(card.get_by_role('button',name='File explorer for Claude')).to_be_visible()
    await effort.click()
    assert await page.evaluate('copied') == ['/effort']
    changes = card.get_by_role('button', name='Changes in Claude')
    await expect(changes).to_be_enabled()
    await expect(changes).to_have_attribute('title', 'Show files changed in this session: copy /changes, paste into terminal')
    await changes.click()
    assert await page.evaluate('copied') == ['/effort', '/changes']
    assert not [msg for _, msg in api.socket_messages if msg.get('type') == 'input']
    assert not [call for call in api.calls if call['action']=='send']
    await card.get_by_role('button', name='File explorer for Claude').click()
    await card.get_by_role('button', name='Open folder docs').focus()
    await page.keyboard.press('Escape')
    await expect(card.get_by_role('button', name='File explorer for Claude')).to_be_focused()
    await card.get_by_role('button', name='Terminal tools for Claude').click()
    await expect(card.locator('.ds-launch-info')).to_have_text('At launch: effort high · permissions ask')
    await card.get_by_role('button', name='Terminal tools for Claude').click()
    api.sockets['node-1'].send(json.dumps({'type':'output', 'data':'\r\n\x1b[1mReview complete\x1b[0m\r\n\r\nThe conversation stays at the center.\r\n\r\n  1. Inspect the requested change.\r\n  2. Preserve the current draft.\r\n  3. Report the observed result.\r\n\r\nReady for the next task.\r\n'}))
    await page.wait_for_timeout(100)
    await page.screenshot(path=f'/tmp/dream270-workflow-controls-{viewport_size[0]}.png')
    assert not errors


@pytest.mark.parametrize('width', [340, 1000])
async def test_workflow_repository_badge_wraps_and_remote_cannot_stage_commands(native_page, width):
    page, api, errors = native_page
    basename = 'sample-repository-with-a-long-readable-name-' * 3
    api.state['workspace'] = '/work/projects/' + basename
    card = await create(page, 'anthropic', 'dream', expand=False)
    api.state['nodes'][0]['layout'].update(x=6,y=6,width=width,height=680)
    # A workflow has no verified remote-control implementation, even if stale
    # metadata advertises a provider shortcut. Never stage or enable it.
    api.state['nodes'][0]['capabilities']['commands']['remote'] = '/remote-control'
    await page.get_by_role('button', name='Refresh workspace').click()
    await expect(card).to_have_css('width', f'{width}px')
    repo = card.get_by_label('Launch workspace')
    await expect(repo).to_have_text(basename)
    await expect(repo).to_have_attribute('title', 'Launch workspace: /work/projects/' + basename)
    await expect(repo.locator('svg')).to_be_visible()
    await repo.hover()
    tools = card.get_by_role('button', name='Terminal tools for Claude')
    badge_box, tools_box = await repo.bounding_box(), await tools.bounding_box()
    assert badge_box['x'] >= tools_box['x'] + tools_box['width']
    assert abs(badge_box['y']+badge_box['height']/2-tools_box['y']-tools_box['height']/2) <= 2
    assert await card.evaluate('(n)=>n.scrollWidth<=n.clientWidth')
    assert await repo.evaluate('(n)=>n.getBoundingClientRect().right<=n.closest(".ds-node").getBoundingClientRect().right')
    remote = card.locator('[data-command=remote]')
    await expect(remote).to_be_disabled()
    await expect(remote).to_have_attribute('title', 'Remote control is not available for Dream workflow')
    await remote.evaluate('(n)=>n.click()')
    assert await page.evaluate('copied') == []
    assert not [msg for _,msg in api.socket_messages if msg.get('type')=='input']
    assert not [call for call in api.calls if call['action']=='send']
    await tools.focus()
    await tools.press('Enter')
    await expect(card.locator('.ds-tools-drawer')).to_be_visible()             # the drawer, now without a draft (2026-10-10)
    await tools.press('Enter')
    await expect(card.locator('.ds-tools-drawer')).to_be_hidden()
    assert not errors


async def test_workflow_uploads_and_files_type_into_the_prompt_and_the_warm_palette_holds(native_page):
    # Was test_workflow_tools_hold_optional_draft_and_staging_reopens_it: the optional draft went (2026-10-10);
    # uploads and chosen files now type their paths into the terminal's own prompt.
    page, api, errors = native_page
    card = await create(page, 'anthropic', 'dream', expand=False)
    tools = card.get_by_role('button', name='Terminal tools for Claude')
    await expect(tools).to_have_attribute('aria-expanded', 'false')
    await card.locator('.xterm-helper-textarea').focus()
    await page.keyboard.type('normal terminal input')
    assert 'normal terminal input' in ''.join(m.get('data', '') for _, m in api.socket_messages if m.get('type') == 'input')
    assert not [call for call in api.calls if call['action'] == 'send']
    await tools.focus()
    await tools.press('Enter')
    await expect(card.locator('.ds-tools-drawer')).to_be_visible()
    await expect(card.get_by_label('Prompt Claude')).to_have_count(0)
    api.upload_fail = True
    await card.locator('input[type=file]').set_input_files({'name':'ref.txt','mimeType':'text/plain','buffer':b'example'})
    await expect(card.locator('.ds-node-note')).to_contain_text('Upload refused')
    assert 'uploads' not in ''.join(m.get('data', '') for _, m in api.socket_messages if m.get('type') == 'input')
    api.upload_fail = False
    await card.locator('input[type=file]').set_input_files({'name':'ref.txt','mimeType':'text/plain','buffer':b'example'})
    for _ in range(50):
        if '"/work/sample-repo/uploads/note.txt"' in ''.join(m.get('data', '') for _, m in api.socket_messages if m.get('type') == 'input'):
            break
        await page.wait_for_timeout(20)
    assert '"/work/sample-repo/uploads/note.txt"' in ''.join(m.get('data', '') for _, m in api.socket_messages if m.get('type') == 'input')
    await card.get_by_role('button', name='File explorer for Claude').click()
    await card.get_by_role('button', name='Add file README.md').click()
    assert ''.join(m.get('data', '') for _, m in api.socket_messages if m.get('type') == 'input').endswith('"/work/sample-repo/README.md"')
    assert '\r' not in ''.join(m.get('data', '') for _, m in api.socket_messages if m.get('type') == 'input').replace('normal terminal input', '')       # nothing submitted
    assert not [call for call in api.calls if call['action'] == 'send']
    # The host and actual xterm viewport keep the warm palette even when the
    # enclosing app has older purple variables.
    await page.evaluate("document.documentElement.style.setProperty('--bg','#221133')")
    await expect(page.locator('.ds-toolbar')).to_have_css('background-color', 'rgb(25, 22, 18)')
    await expect(card.locator('.ds-node-head')).to_have_css('background-color', 'rgb(24, 22, 18)')
    await expect(card.locator('.xterm-viewport')).to_have_css('background-color', 'rgb(17, 16, 14)')
    await page.screenshot(path='/tmp/dream269-workflow-warm-draft.png')
    assert not errors

async def test_browser_dictation_aborts_on_navigation_and_close(native_page):
    page, api, errors = native_page
    await page.evaluate('''() => {
      window.speechStarts=0;window.speechAborts=0;
      window.SpeechRecognition=class {start(){speechStarts++} stop(){this.abort()}
        abort(){speechAborts++;this.onend?.()}};
    }''')
    card = await create(page, 'machx')
    mic = card.get_by_role('button', name='Dictate into the prompt', include_hidden=True)
    await mic.click()
    assert await page.evaluate('speechStarts') == 1
    await page.evaluate("document.documentElement.dataset.dreamView='chat'")
    await expect(mic).to_have_attribute('aria-pressed', 'false')
    assert await page.evaluate('speechAborts') == 1
    await page.evaluate("document.documentElement.dataset.dreamView='deepsleep'")
    await mic.click()
    await card.get_by_role('button', name='Close Local', exact=True).click()
    await expect(card).to_have_count(0)
    assert await page.evaluate('speechAborts') == 2
    assert not errors


@pytest.mark.parametrize('size', [(1920, 1080), (1440, 900)])
async def test_six_native_terminals_tile_in_view_with_large_terminal_area(native_page, size):
    import json
    page, api, errors = native_page
    await page.set_viewport_size({'width': size[0], 'height': size[1]})
    for provider in ['anthropic', 'codex', 'antigravity', 'anthropic', 'codex', 'antigravity']:
        await create(page, provider, expand=False)
    first = page.locator('[data-node="node-1"]')
    await page.get_by_role('button', name='Tile terminals', exact=True).click()
    await expect(page.get_by_role('button', name='Tile terminals', exact=True)).to_be_enabled()
    viewport = await page.locator('.ds-viewport').bounding_box()
    measures = []
    for index in range(6):
        node_id = f'node-{index+1}'
        card = page.locator(f'[data-node="{node_id}"]')
        await expect(card.locator('.ds-prompt')).to_be_hidden()
        await expect(card.locator('.ds-ports')).to_be_hidden()
        bounds = await card.bounding_box()
        terminal = await card.locator('.ds-terminal').bounding_box()
        assert bounds['x'] >= viewport['x'] and bounds['y'] >= viewport['y']
        assert bounds['x'] + bounds['width'] <= viewport['x'] + viewport['width'] + 1
        assert bounds['y'] + bounds['height'] <= viewport['y'] + viewport['height'] + 1
        assert terminal['height'] / bounds['height'] >= .82
        resize = [message for key, message in api.socket_messages if key == node_id and message.get('type') == 'resize'][-1]
        # The approved character gutter reserves 56 px in both skins, so the
        # narrow three-column layout now fits 45 cells at the unchanged font.
        assert resize['cols'] >= 45 and resize['rows'] >= 22
        measures.append({'node': node_id, 'cols': resize['cols'], 'rows': resize['rows'], 'terminal_fraction': round(terminal['height']/bounds['height'], 3)})
        api.sockets[node_id].send(json.dumps({'type':'output','data':'\r\nNative terminal fixture\r\n/model    /permissions    /help\r\nReady for your next task.\r\n'}))
    layouts = deepcopy([node['layout'] for node in api.state['nodes']])
    assert len({layout['x'] for layout in layouts}) == 3
    assert len({layout['y'] for layout in layouts}) == 2
    await page.screenshot(path=f'/tmp/dream-compact-six-{size[0]}.png')
    from pathlib import Path
    Path(f'/tmp/dream-compact-six-{size[0]}.json').write_text(json.dumps(measures, indent=2))
    await first.get_by_role('button', name='Maximize Claude').click()
    maximized = await first.bounding_box()
    assert maximized['width'] >= viewport['width'] - 14
    await page.get_by_role('button', name='Refresh workspace').click()
    await expect(first.get_by_role('button', name='Restore Claude')).to_be_visible()
    await first.get_by_role('button', name='Restore Claude').click()
    assert [node['layout'] for node in api.state['nodes']] == layouts
    await expect(first).to_have_css('width', str(layouts[0]['width']) + 'px')
    await first.get_by_role('button', name='Resize Claude; use arrow keys').press('ArrowRight')
    await expect(first).to_have_css('width', str(layouts[0]['width'] + 10) + 'px')
    await page.get_by_role('button', name='Refresh workspace').click()
    await expect(first).to_have_css('width', str(layouts[0]['width'] + 10) + 'px')
    assert not errors


async def test_failed_process_is_not_reported_connected_and_cannot_receive_input(native_page):
    import json
    page, api, errors = native_page
    card = await create(page, expand=False)
    await expect(card.locator('.ds-terminal-status')).to_contain_text('Transport ready')
    api.state['nodes'][0]['state'] = 'failed'
    api.state['nodes'][0]['error'] = 'Provider command exited before startup'
    api.sockets['node-1'].send(json.dumps({'type':'state','state':'failed'}))
    await page.get_by_role('button', name='Refresh workspace').click()
    await expect(card.locator('.ds-state')).to_have_text('failed')
    await expect(card.locator('.ds-terminal-status')).to_contain_text('Process failed · transport ready')
    await expect(card.get_by_role('button', name='Restart Claude')).to_be_visible()
    await card.locator('.xterm-helper-textarea').focus()
    await page.keyboard.type('do not send')
    assert not [msg for _, msg in api.socket_messages if msg.get('type') == 'input']
    await expect(card.locator('.ds-node-note')).to_contain_text('input was not sent')
    api.sockets['node-1'].send(json.dumps({'type':'state','state':'exited'}))
    await expect(card.locator('.ds-terminal-status')).to_contain_text('Process exited')
    assert not errors


async def test_tile_refusal_preserves_layout_without_false_success(native_page):
    # Was ..._preserves_layout_and_draft_...: the draft went (2026-10-10); the layout refusal checks stay.
    page, api, errors = native_page
    first = await create(page)
    await create(page, 'codex', expand=False)
    original = deepcopy(api.state['nodes'][0]['layout'])
    api.fail = 'layout'
    await page.get_by_role('button', name='Tile terminals', exact=True).click()
    await expect(page.get_by_role('button', name='Tile terminals', exact=True)).to_be_enabled()
    await expect(page.locator('.ds-feedback')).to_contain_text('was not sent')
    await expect(first).to_have_css('left', str(original['x']) + 'px')
    assert len([c for c in api.calls if c['action'] == 'layout']) == 1
    assert not errors

async def test_socket_open_waits_for_server_state_before_accepting_input(native_page):
    import json
    page, api, errors = native_page
    def delayed_socket(ws):
        api.sockets['node-1'] = ws
        ws.on_message(lambda message: api.socket_messages.append(('node-1', json.loads(message))))
    await page.route_web_socket('**/ws/deepsleep/*', delayed_socket)
    card = await create(page, expand=False)
    await expect(card.locator('.ds-terminal-status')).to_contain_text('Checking access')
    await card.locator('.xterm-helper-textarea').focus()
    await page.keyboard.type('blocked')
    assert not [msg for _, msg in api.socket_messages if msg.get('type') == 'input']
    api.sockets['node-1'].send(json.dumps({'type': 'state', 'state': 'ready'}))
    await expect(card.locator('.ds-terminal-status')).to_contain_text('Transport ready')
    await page.keyboard.type('accepted')
    assert [msg for _, msg in api.socket_messages if msg.get('type') == 'input']
    assert not errors


async def test_tiled_layout_survives_browser_reload(native_page):
    page, api, errors = native_page
    await create(page, expand=False)
    await create(page, 'codex', expand=False)
    await page.get_by_role('button', name='Tile terminals', exact=True).click()
    await expect(page.get_by_role('button', name='Tile terminals', exact=True)).to_be_enabled()
    saved = deepcopy([node['layout'] for node in api.state['nodes']])
    await page.reload()
    terminal_js, fit_js, terminal_css = xterm_assets()
    for path in [terminal_css, STATIC / 'deepsleep.css', STATIC / 'deepsleep-drawer.css']:
        await page.add_style_tag(path=str(path))
    for path in [terminal_js, fit_js, STATIC / 'deepsleep-drawer.js', STATIC / 'deepsleep-activity.js', STATIC / 'deepsleep-flow.js', STATIC / 'deepsleep.js']:
        await page.add_script_tag(path=str(path))
    await expect(page.locator('.ds-node')).to_have_count(2)
    for i, shape in enumerate(saved, 1):
        card = page.locator(f'[data-node="node-{i}"]')
        for key, css in [('x','left'),('y','top'),('width','width'),('height','height')]:
            await expect(card).to_have_css(css, str(shape[key])+'px')
        await expect(card.locator('.ds-prompt')).to_be_hidden()
    assert not errors


async def test_old_http_snapshot_cannot_resurrect_failed_socket_state(native_page):
    import asyncio
    import json
    page, api, errors = native_page
    card = await create(page, expand=False)
    held = asyncio.Event()
    release = asyncio.Event()
    hold_next = True
    async def delayed_snapshot(route):
        nonlocal hold_next
        if route.request.method == 'GET' and hold_next:
            hold_next = False
            old = deepcopy(api.state)
            held.set()
            await release.wait()
            await route.fulfill(json=old)
        else:
            await api.route(route)
    await page.route('**/api/deepsleep', delayed_snapshot)
    await page.get_by_role('button', name='Refresh workspace').click()
    await asyncio.wait_for(held.wait(), timeout=3)
    api.sockets['node-1'].send(json.dumps({'type':'state','state':'failed'}))
    await expect(card.locator('.ds-state')).to_have_text('failed')
    release.set()
    await expect(page.get_by_role('button', name='Refresh workspace')).to_be_enabled()
    await expect(card.locator('.ds-state')).to_have_text('failed')
    await expect(card.locator('.ds-terminal-status')).to_contain_text('Process failed')
    await card.locator('.xterm-helper-textarea').focus()
    await page.keyboard.type('must stay blocked')
    assert not [msg for _, msg in api.socket_messages if msg.get('type') == 'input']
    # Metadata/layout refresh is still applied while socket state remains failed.
    api.state['nodes'][0]['layout']['x'] = 85
    await page.get_by_role('button', name='Refresh workspace').click()
    await expect(card).to_have_css('left', '85px')
    await expect(card.locator('.ds-state')).to_have_text('failed')
    await api.sockets['node-1'].close()
    await expect(card.get_by_role('button', name='Reconnect Claude')).to_be_visible()
    await card.get_by_role('button', name='Reconnect Claude').click()
    # The newly authenticated socket explicitly reports ready in this fixture.
    await expect(card.locator('.ds-state')).to_have_text('Running')
    await expect(card.locator('.ds-terminal-status > span')).to_have_text('Transport ready')
    await card.locator('.xterm-helper-textarea').focus()
    await page.keyboard.type('new session accepts input')
    assert [msg for _, msg in api.socket_messages if msg.get('type') == 'input']
    assert not errors


@pytest.mark.parametrize('source_provider,target_provider,source_profile,target_profile', [
    ('anthropic','codex','native','native'),
    ('codex','antigravity','native','native'),
    ('antigravity','grok','native','native'),
    ('grok','anthropic','native','native'),
    ('anthropic','codex','dream','native'),
    ('codex','anthropic','native','dream'),
])
async def test_links_between_native_and_workflow_terminals_are_drawn_and_labelled(native_page, source_provider, target_provider, source_profile, target_profile):
    page, api, errors = native_page
    source = await create(page, source_provider, source_profile)
    target = await create(page, target_provider, target_profile, expand=False)
    source_title = api.state['nodes'][0]['title']
    target_title = api.state['nodes'][1]['title']
    await page.get_by_role('button', name='Show connections and delivery activity').click()
    await page.locator('.ds-channel-connections>summary').click()
    await page.get_by_label('Automatically forward completed replies on new links').check()
    await page.get_by_role('button', name='Show connections and delivery activity').click()
    await source.get_by_role('button', name='Link from '+source_title).click()
    await target.get_by_role('button', name='Link to '+target_title).click()
    assert api.calls[-1] == {'action':'link','source':'node-1','target':'node-2','auto_forward':False}
    await expect(page.locator('.ds-wire-manual')).to_have_count(1)
    wire_y=float((await page.locator('.ds-wire-manual').get_attribute('d')).split()[2])
    svg_box=await page.locator('.ds-wires').bounding_box()
    row_box=await source.get_by_role('button', name='Link from '+source_title).bounding_box()   # Out: the bottom bar (2026-10-10)
    assert abs(svg_box['y']+wire_y-row_box['y']-row_box['height'])<=2   # from the Out pill's bottom edge
    await expect(page.locator('.ds-links-list')).to_contain_text('native ')   # its link tools carry it (2026-10-10)
    # Forward selected text and Add to draft went with the draft box (2026-10-10): linked agents message each other
    # directly now (test_deepsleep_native_links.py, test_deepsleep_mcp.py).
    await expect(source.get_by_role('button', name='Send selected text from '+source_title)).to_have_count(0)
    assert not [call for call in api.calls if call['action'] in ('handoff', 'send')]
    assert not [msg for _, msg in api.socket_messages if msg.get('type')=='input']
    assert not errors
