"""Appearance and header in the complete Studio shell; controlled terminals only."""
import json

from playwright.async_api import expect

from test_deepsleep_ui import BoardAPI
from test_frontier_switches_ui import frontiers


async def test_complete_shell_character_header_and_account_geometry(frontiers):
    page, _, _, errors = frontiers
    api = BoardAPI()
    api.state['workspace'] = '/workspace/Dream'
    await page.route('**/api/deepsleep', api.route)
    await page.route_web_socket('**/ws/deepsleep/*', api.socket)
    await page.reload()
    await page.set_viewport_size({'width': 1680, 'height': 1040})
    await page.locator('#dream-nav-deepsleep').click()
    header = page.locator('#dream-deepsleep-header')
    await expect(header).to_be_visible()
    await expect(page.locator('#dream-deepsleep > .ds-toolbar')).to_be_hidden()
    for provider in ['codex', 'anthropic', 'antigravity']:
        await header.get_by_label('New terminal provider').select_option(provider)
        await header.get_by_role('button', name='Add terminal', exact=True).click()
        await expect(header.get_by_role('button', name='Add terminal', exact=True)).to_be_enabled()
        api.state['nodes'][-1]['workspace'] = '/workspace/Dream'
    await header.get_by_role('button', name='Tile terminals', exact=True).click()
    await expect(header.get_by_role('button', name='Tile terminals', exact=True)).to_be_enabled()
    await header.locator('#ds-character-toggle').click()
    await page.wait_for_function('Array.from(document.querySelectorAll(".ds-avatar-slot img")).every(i=>i.complete&&i.naturalWidth>0)')
    await expect(page.locator('.ds-terminal-status').first).to_contain_text('Transport ready')
    for i, title in enumerate(['Reviewing the changes', 'Checking the layout', 'Verifying the behavior'], 1):
        api.sockets[f'node-{i}'].send(json.dumps({'type': 'output', 'data':
            '\x1b[2J\x1b[H\x1b[38;2;242;166;92m' + title + '\x1b[0m\r\n\r\n'
            'The terminal stays connected.\r\n'
            'Your text, input and tools stay here.\r\n\r\n'
            '\x1b[38;2;228;197;138m  • Same running session\r\n'
            '  • Optional character\r\n'
            '  • Existing handoff controls\x1b[0m\r\n\r\n'
            '> '}))
    await page.wait_for_timeout(150)
    pills = page.locator('#dream-frontiers .dream-frontier-pill')
    before = [await pills.nth(i).bounding_box() for i in range(4)]
    for username in ['reader@example.test', '', 'reader@example.test', '']:
        await page.evaluate('''username => dispatchEvent(new CustomEvent('dream:frontier-state', {detail:{
            controls:Object.fromEntries(['anthropic','grok','antigravity','codex'].map(key=>[key,
            {signed_in:true,status:'signed_in',title:'Signed in',username}]))}}))''', username)
        assert [await pills.nth(i).bounding_box() for i in range(4)] == before
    await page.wait_for_function('Array.from(document.querySelectorAll(".dream-provider-logo")).every(i=>i.complete&&i.naturalWidth>0)')
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    await page.screenshot(path='/tmp/dream271-complete-characters.png')
    await header.locator('#ds-character-toggle').click()
    await page.screenshot(path='/tmp/dream271-complete-terminal.png')
    assert len(api.sockets) == 3
    assert sum('token' in msg for _, msg in api.socket_messages) == 3
    assert not errors
