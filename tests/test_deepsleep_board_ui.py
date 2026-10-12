"""The Message Board shows the project's open board beside the owner's tasks and the direct link messages, each
with its own look (the owner, 2026-10-10)."""
import time

from playwright.async_api import expect

from test_deepsleep_channel_ui import channel_setup
from test_deepsleep_ui import board_page  # noqa: F401  (the fixture)


async def test_board_posts_tasks_and_direct_messages_look_distinct_and_read_in_time_order(board_page):
    page, api, errors = board_page
    draft, send = await channel_setup(page, api)
    now = time.time()
    api.state['board'] = {'error': '', 'posts': [
        {'id': 7, 'at': now - 30, 'author': 'session-abc123', 'node': 'node-1', 'kind': 'note', 'reply_to': None,
         'thread': 7, 'text': 'Tests are red on main; I am on it.'}]}
    api.state['channel'] = [
        {'id': 'task-1', 'source': 'owner', 'targets': ['node-2'], 'text': '@Codex review it', 'state': 'completed',
         'receipts': [], 'trimmed': False, 'at': now - 60, 'direct': False},
        {'id': 'direct-1', 'source': 'node-1', 'targets': ['node-2'], 'text': 'Work with me on the cache key.',
         'state': 'completed', 'receipts': [], 'trimmed': False, 'at': now - 10, 'direct': True}]
    await page.get_by_role('button', name='Refresh workspace').click()
    history = page.locator('.ds-channel-history')
    await expect(history.locator('.ds-channel-board')).to_contain_text('Board · Claude')
    await expect(history.locator('.ds-channel-board')).to_contain_text('Tests are red on main')
    await expect(history.locator('.ds-channel-direct')).to_contain_text('Direct · Claude → Codex')
    await expect(history.locator('.ds-channel-request')).to_contain_text('You → @Codex')
    order = await history.locator('.ds-channel-author').all_inner_texts()
    assert order == ['You → @Codex', 'Board · Claude', 'Direct · Claude → Codex'], order
    assert not errors


async def test_a_message_with_no_mention_posts_to_the_board_and_sends_no_task(board_page):
    page, api, errors = board_page
    draft, send = await channel_setup(page, api)
    await draft.fill('Heads up: the deploy is frozen until noon.')
    await send.click()
    await expect(draft).to_have_value('')
    assert api.calls[-1] == {'action': 'board_post', 'text': 'Heads up: the deploy is frozen until noon.'}
    await expect(page.locator('.ds-channel-board')).to_contain_text('Board · You')
    await expect(page.locator('.ds-channel-status')).to_contain_text('Posted to the board')
    assert not [c for c in api.calls if c['action'] in ('channel_send', 'send')]
    assert not errors
