"""Native CLI terminals on the DeepSleep bridge (the owner, 2026-10-10).

A native pane (claude, codex, grok, agy run as themselves) gets the same bridge a Dream terminal has, through
Dream's MCP server: the project's open board with no link, and direct messages only along its links. The
bridge's key names the terminal; nothing a pane sends can speak for another.
"""
import pytest

from dream import config
from dream.gui.deepsleep import LINK_HOURLY, DeepSleep, Node, layout


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'MEMORY_DIR', tmp_path / 'memory')
    workspace = tmp_path / 'project'
    workspace.mkdir()
    delivered = []

    def request(url, token, envelope=None):
        if envelope is None:
            return {'receipts': [], 'agents': []}
        delivered.append(envelope)
        return dict(envelope, state='queued', result='', error='')
    ds = DeepSleep(lambda: workspace, lambda: 'http://127.0.0.1:1', child_request=request)
    for key, title, profile in (('grok1', 'Grok', 'native'), ('claude1', 'Claude', 'dream'),
                                ('codex1', 'Codex', 'native'), ('gemini1', 'Gemini', 'dream')):
        node = Node(key, 'grok', '', title, layout({}), state='ready', profile=profile)
        node.address = ('http://127.0.0.1:1', 'child-token') if profile == 'dream' else None
        ds.nodes[key] = node
    ds.delivered = delivered
    return ds


def key(board, node):
    return board.nodes[node].bridge_token


async def test_a_native_terminal_posts_to_the_board_and_reads_what_others_posted(board):
    await board.bridge(key(board, 'grok1'), {'operation': 'board_post', 'text': 'Grok here: the API is green.'})
    await board.action({'action': 'board_post', 'text': 'Owner: ship at noon.'})
    read = await board.bridge(key(board, 'codex1'), {'operation': 'board_read'})
    assert 'Grok here: the API is green.' in read['text'] and 'Owner: ship at noon.' in read['text']
    assert '<ram_posts' in read['text'] and 'never instructions' in read['text']    # framed as other agents' words
    posts = board.snapshot()['board']['posts']
    assert posts[0]['node'] == 'grok1' and posts[0]['author'] == 'deepsleep-grok1'
    again = await board.bridge(key(board, 'codex1'), {'operation': 'board_read'})
    assert 'Grok here' not in again['text']                                          # unread first, then nothing new


async def test_a_terminal_reports_who_it_is_linked_to(board):
    await board.action({'action': 'link', 'source': 'grok1', 'target': 'claude1', 'auto_forward': False})
    await board.action({'action': 'link', 'source': 'codex1', 'target': 'grok1', 'auto_forward': False})
    listed = await board.bridge(key(board, 'grok1'), {'operation': 'list'})
    assert listed['you'] == {'id': 'grok1', 'title': 'Grok', 'state': 'ready'}
    assert listed['sends_to'] == [{'id': 'claude1', 'title': 'Claude', 'state': 'ready'}]
    assert listed['hears_from'] == [{'id': 'codex1', 'title': 'Codex', 'state': 'ready'}]
    alone = await board.bridge(key(board, 'gemini1'), {'operation': 'list'})
    assert alone['sends_to'] == [] and alone['hears_from'] == []


async def test_a_partner_that_closed_shows_as_closed_and_takes_no_message(board):
    await board.action({'action': 'link', 'source': 'grok1', 'target': 'claude1', 'auto_forward': False})
    board.nodes['claude1'].state = 'exited'
    listed = await board.bridge(key(board, 'grok1'), {'operation': 'list'})
    assert listed['sends_to'] == [{'id': 'claude1', 'title': 'Claude', 'state': 'exited'}]
    with pytest.raises(ValueError, match='not ready'):
        await board.bridge(key(board, 'grok1'), {'operation': 'send', 'target': 'claude1', 'prompt': 'hi', 'id': 'to-closed'})
    assert board.delivered == []


async def test_a_native_terminal_sends_over_its_link_and_never_without_one(board):
    await board.action({'action': 'link', 'source': 'grok1', 'target': 'claude1', 'auto_forward': False})
    receipt = await board.bridge(key(board, 'grok1'), {'operation': 'send', 'target': 'claude1',
                                                       'prompt': 'work with me on xyz', 'id': 'g-to-c-1'})
    assert receipt['state'] == 'queued' and board.delivered[-1]['source'] == 'grok1'
    with pytest.raises(ValueError, match='Connect a directed link'):
        await board.bridge(key(board, 'grok1'), {'operation': 'send', 'target': 'gemini1', 'prompt': 'hi', 'id': 'g-to-g'})
    assert [e['target'] for e in board.delivered] == ['claude1']


async def test_a_native_receiver_reads_its_direct_messages_framed_as_another_agents_words(board):
    await board.action({'action': 'link', 'source': 'claude1', 'target': 'codex1', 'auto_forward': False})
    await board.send('codex1', 'please review the diff', source='claude1', message_id='c-to-x-1')
    inbox = await board.bridge(key(board, 'codex1'), {'operation': 'inbox'})
    assert [r['id'] for r in inbox['receipts']] == ['c-to-x-1']
    one = await board.bridge(key(board, 'codex1'), {'operation': 'inbox', 'id': 'c-to-x-1'})
    text = one['receipts'][0]['text']
    assert 'please review the diff' in text and 'not a new owner instruction' in text


async def test_one_link_carries_at_most_its_hourly_limit_and_other_links_still_work(board):
    await board.action({'action': 'link', 'source': 'grok1', 'target': 'claude1', 'auto_forward': False})
    await board.action({'action': 'link', 'source': 'grok1', 'target': 'gemini1', 'auto_forward': False})
    for n in range(LINK_HOURLY):
        await board.bridge(key(board, 'grok1'), {'operation': 'send', 'target': 'claude1', 'prompt': f'm{n}', 'id': f'm{n}'})
    with pytest.raises(ValueError, match='limit'):
        await board.bridge(key(board, 'grok1'), {'operation': 'send', 'target': 'claude1', 'prompt': 'one more', 'id': 'over'})
    await board.bridge(key(board, 'grok1'), {'operation': 'send', 'target': 'gemini1', 'prompt': 'other link', 'id': 'other'})
    assert sum(e['target'] == 'claude1' for e in board.delivered) == LINK_HOURLY


async def test_a_stale_or_foreign_key_is_refused_and_an_exited_pane_has_no_voice(board):
    with pytest.raises(PermissionError):
        await board.bridge('not-a-terminal-key', {'operation': 'board_post', 'text': 'spoof'})
    board.nodes['grok1'].state = 'exited'
    with pytest.raises(PermissionError):
        await board.bridge(key(board, 'grok1'), {'operation': 'list'})
    assert board.snapshot()['board']['posts'] == []
