"""The Tools pop-up reworked (the owner, 2026-10-10): two ways to prompt a terminal, its own prompt and the
Message Board. The draft box, Copy to terminal and the forwarding around them are gone; what used to fill the draft
types into the terminal's own prompt, never submitted; the Chat tab's mode, Handoff and Preset sit under the bar."""
import json

from playwright.async_api import expect

from test_deepsleep_ui import add, board_page  # noqa: F401  (board_page is the fixture)

MODES = {'mode': 'auto', 'modes': ['ask', 'accept', 'auto'], 'labels': {'ask': 'asking each change', 'accept': 'accept edits on', 'auto': 'auto mode on'}}
PRESETS = {'presets': [{'name': 'Software Development', 'cost': {'skills': 22, 'tools': 54, 'tokens': 11900}},
                       {'name': 'Writing', 'cost': {'skills': 6, 'tools': 20, 'tokens': 4100}}],
           'active': 'Software Development', 'sha256': 'fixture'}


async def last_call(calls, expected):
    import asyncio
    for _ in range(100):
        if calls and calls[-1] == expected:
            return
        await asyncio.sleep(.03)
    assert calls and calls[-1] == expected, calls


async def routes(page):
    calls = []

    async def control(route):
        body = route.request.post_data_json
        calls.append(body)
        if body['action'] == 'read_twice':
            result = {'read_twice': True}
        elif body['action'] == 'permission_mode':
            result = dict(MODES, mode='accept')
        else:
            result = dict(MODES, read_twice=False, context='ctx 57.7k/200k (29%)')
        await route.fulfill(json={'ok': True, 'result': result})

    async def presets(route):
        if route.request.method == 'POST':
            calls.append(route.request.post_data_json)
        await route.fulfill(json=PRESETS)
    await page.route('**/api/deepsleep/control', control)
    await page.route('**/api/presets', presets)
    return calls


async def test_the_draft_and_its_helpers_are_gone_and_the_rest_of_the_toolbar_stays(board_page):
    page, api, errors = board_page
    await routes(page)
    await add(page)
    tile = page.locator('.ds-node').first
    for gone in ('Prompt Claude', 'Send to Claude', 'Expand multiline draft', 'Send selected text from Claude'):
        await expect(tile.get_by_role('button', name=gone)).to_have_count(0)
    await expect(tile.get_by_role('textbox', name='Prompt Claude')).to_have_count(0)
    for words in ('Task draft', 'Forward selected text', 'Rich input', 'Copy to terminal'):
        await expect(tile.get_by_text(words)).to_have_count(0)
    for kept in ('Reference attached files in the prompt', 'File explorer for Claude', 'Dictate into the prompt',
                 'Save terminal name', 'Place character for Claude', 'Reset character position for Claude', 'Recorded activity for Claude'):
        await expect(tile.get_by_role('button', name=kept)).to_have_count(1)
    await expect(tile.get_by_label('Character for Claude', exact=True)).to_have_count(1)
    await expect(tile.get_by_text('Your message label (all terminals)')).to_have_count(1)
    assert not errors


async def test_a_native_shortcut_and_a_file_go_into_the_terminals_own_prompt_without_enter(board_page):
    page, api, errors = board_page
    await routes(page)
    api.state['workspace'] = '/work/project'
    api.state['nodes'] = [{'id': 'node-n', 'provider': 'codex', 'title': 'Codex', 'profile': 'native', 'state': 'ready',
                           'workspace': '/work/project', 'capabilities': {'linked': True, 'automatic': False, 'commands': {'model': '/model'}},
                           'layout': {'x': 30, 'y': 30, 'width': 620, 'height': 480}}]
    await page.get_by_role('button', name='Refresh workspace').click()
    tile = page.locator('.ds-node').first
    await expect(tile).to_have_class(__import__('re').compile('ds-native-pane'))
    await expect(tile.locator('.ds-chat-row')).to_have_count(0)               # a native CLI keeps its own mode
    await expect(tile.get_by_text('Prepare a prompt')).to_have_count(0)
    await expect(tile.get_by_role('button', name='Copy draft for Codex')).to_have_count(0)
    await page.wait_for_function("() => document.querySelector('.ds-node .xterm-rows')?.textContent.includes('Connected fixture terminal')")
    await tile.get_by_role('button', name='Terminal tools for Codex').click()
    await tile.get_by_role('button', name='Model in Codex').click()
    typed = ''.join(m.get('data', '') for _, m in api.socket_messages if m.get('type') == 'input')
    assert '/model' in typed and '\r' not in typed and '\n' not in typed, typed   # typed into the prompt, not submitted
    await expect(tile.locator('.ds-node-note')).to_contain_text('Nothing has been sent')
    assert not errors


async def test_the_chat_tabs_mode_handoff_and_preset_sit_under_the_bar_for_a_dream_terminal(board_page):
    page, api, errors = board_page
    calls = await routes(page)
    await add(page)
    tile = page.locator('.ds-node').first
    row = tile.locator('.ds-chat-row')
    mode = row.get_by_role('button', name='Cycle permission mode in Claude')
    await expect(mode).to_have_text('auto mode on')                           # the terminal's own mode, read through the relay
    assert {'node': 'node-1', 'action': 'tile_status'} in calls
    await expect(row).to_contain_text('ctx 57.7k/200k (29%)')                  # its context
    read = row.get_by_role('button', name='Read ×2 in Claude')
    await expect(read).to_have_attribute('aria-pressed', 'false')
    await read.click()
    await expect(read).to_have_attribute('aria-pressed', 'true')
    assert calls[-1] == {'node': 'node-1', 'action': 'read_twice'}
    await mode.click()
    await expect(mode).to_have_text('accept edits on')
    assert calls[-1] == {'node': 'node-1', 'action': 'permission_mode'}
    await row.get_by_role('button', name='Handoff in Claude').click()
    await last_call(calls, {'node': 'node-1', 'action': 'fresh_start'})
    preset = row.get_by_label('Skill preset for new sessions')
    await expect(preset).to_have_value('Software Development')
    await expect(row).to_contain_text('22 skills · 54 tools · ~11.9K tokens')
    await preset.select_option('Writing')
    await last_call(calls, {'op': 'use', 'preset': 'Writing', 'sha256': 'fixture'})
    bar, chips, repo = (await tile.locator('.ds-repo-bar').bounding_box(), await row.bounding_box(),
                        await tile.locator('.ds-repo-badge').bounding_box())
    assert chips['y'] >= bar['y'] + bar['height'] - 1 and abs(chips['x'] - repo['x']) <= 120   # under the bar, at the repo
    assert not errors
