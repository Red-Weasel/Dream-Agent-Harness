"""The Message Board is the project's open board (the owner, 2026-10-10).

RAM (dream.memory.ram) is the store: any terminal posts to it and reads it with no link, and the owner posts from
the Message Board. Messages over a link stay direct and are marked as such, so the two never look alike.
"""
import time

import pytest

from dream import config
from dream.gui.deepsleep import DeepSleep, Node, layout
from dream.memory.ram import Board


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'MEMORY_DIR', tmp_path / 'memory')
    workspace = tmp_path / 'project'
    workspace.mkdir()

    def request(url, token, envelope):
        return dict(envelope, state='completed', result='reply from ' + envelope['target'], error='')
    ds = DeepSleep(lambda: workspace, lambda: 'http://127.0.0.1:1', child_request=request)
    for key, title, profile in (('grok1', 'Grok', 'dream'), ('claude1', 'Claude', 'dream'), ('codex1', 'Codex', 'native')):
        node = Node(key, 'codex', '', title, layout({}), state='ready', profile=profile)
        node.address = ('http://127.0.0.1:1', 'fixture-token')
        ds.nodes[key] = node
    ds.ram = Board.for_workspace(workspace)
    return ds


def test_recent_posts_show_without_moving_anyones_read_position(board):
    for n in range(3):
        board.ram.post('session-a', f'finding {n}', node='grok1')
    shown = board.ram.recent(2)
    assert [p.text for p in shown] == ['finding 1', 'finding 2']            # the newest, oldest first
    assert board.ram.unread('session-b') == 3                               # nobody's cursor moved


def test_the_snapshot_carries_the_board_with_authors_and_times(board):
    before = time.time()
    board.ram.post('session-a', 'The cache key ignores the prompt head.', node='grok1')
    posts = board.snapshot()['board']['posts']
    assert [(p['author'], p['node'], p['text']) for p in posts] == [('session-a', 'grok1', 'The cache key ignores the prompt head.')]
    assert before - 2 <= posts[0]['at'] <= time.time() + 2


async def test_the_owner_posts_to_the_board_without_the_agents_rate_limit(board):
    for n in range(5):                                                      # agents stop at 3 in 2 minutes
        await board.action({'action': 'board_post', 'text': f'note {n}'})
    posts = board.snapshot()['board']['posts']
    assert [p['author'] for p in posts] == ['owner'] * 5
    assert not any(r for r in board.receipts.values())                      # a post delivers no task


async def test_an_owner_post_that_looks_like_a_secret_is_refused(board):
    with pytest.raises(ValueError, match='secret'):
        await board.action({'action': 'board_post', 'text': 'api_key = sk-' 'abcdefghijklmnopqrstuvwxyz123456'})
    assert board.snapshot()['board']['posts'] == []


async def test_direct_link_messages_are_marked_direct_and_carry_a_time(board):
    await board.action({'action': 'link', 'source': 'grok1', 'target': 'claude1', 'auto_forward': False})
    await board.send('claude1', 'work with me on xyz', source='grok1', message_id='direct-1')
    await board.action({'action': 'channel_send', 'id': 'channel-1', 'targets': ['grok1'], 'prompt': '@Grok go'})
    rows = {r['id']: r for r in board.snapshot()['channel']}
    assert rows['direct-1']['direct'] is True and rows['direct-1']['source'] == 'grok1'
    assert rows['channel-1']['direct'] is False
    assert all(abs(r['at'] - time.time()) < 60 for r in rows.values())
