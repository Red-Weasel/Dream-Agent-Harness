"""Owner channel uses the same exact-ID, receipt-backed delivery as terminals."""
import asyncio
import json
import threading

import httpx
import pytest

from dream import config
from dream.gui.bus import EventBus
from dream.gui.deepsleep import DeepSleep, Node, layout
from dream.gui.server import StudioServer


@pytest.fixture
def board(tmp_path):
    calls = []
    def request(url, token, envelope):
        calls.append(envelope)
        return dict(envelope, state='completed', result='reply ' + envelope['target'], error='')
    board = DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:1', child_request=request)
    for key in ('first', 'second', 'native'):
        node = Node(key, 'codex', '', 'Same name', layout({}), state='ready',
                    profile='native' if key == 'native' else 'dream')
        node.address = ('http://127.0.0.1:1', 'fixture-token')
        board.nodes[key] = node
    board.test_calls = calls
    return board


def message(**overrides):
    return dict(action='channel_send', id='channel-one', targets=['first', 'second'],
                prompt='Hello both', **overrides)


async def test_broadcast_exact_ids_once_and_retry_conflict(board):
    request = message()
    snapshot = await board.action(request)
    assert [c['target'] for c in board.test_calls] == ['first', 'second']
    assert all(c['source'] == 'owner' for c in board.test_calls)
    row, = snapshot['channel']
    assert row['text'] == 'Hello both' and row['state'] == 'completed'
    assert [r['result'] for r in row['receipts']] == ['reply first', 'reply second']
    assert await board.action(request) == snapshot
    assert len(board.test_calls) == 2
    for changed in ({'prompt': 'changed'}, {'targets': ['second', 'first']}):
        with pytest.raises(ValueError, match='another message'):
            await board.action({**request, **changed})
    assert len(board.test_calls) == 2


@pytest.mark.parametrize('changes', [
    {'targets': ['first', 'native']}, {'targets': ['first', 'missing']},
    {'targets': ['first', 'first']}, {'targets': []}, {'prompt': ' '},
    {'prompt': 'x\0y'}, {'id': 'invalid id'},
])
async def test_invalid_batch_never_partially_sends(board, changes):
    with pytest.raises(ValueError):
        await board.action({**message(), **changes})
    assert not board.test_calls and not board.receipts and not board._channel_attempts


async def test_uncertain_send_is_not_replayed_and_other_receiver_completes(board):
    real = board._request
    def request(url, token, envelope):
        if envelope['target'] == 'first':
            board.test_calls.append(envelope)
            raise OSError('Lost acknowledgement')
        return real(url, token, envelope)
    board._request = request
    result = await board.action(message())
    assert [r['state'] for r in result['channel'][0]['receipts']] == ['uncertain', 'completed']
    await board.action(message())
    assert len(board.test_calls) == 2


async def test_concurrent_retry_does_not_duplicate_inflight_delivery(board):
    entered, release = threading.Event(), threading.Event()
    real = board._request
    def request(*args):
        entered.set()
        assert release.wait(3)
        return real(*args)
    board._request = request
    send = asyncio.create_task(board.action({**message(), 'targets': ['first']}))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        retry = await board.action({**message(), 'targets': ['first']})
        assert retry['channel'][0]['state'] == 'sending'
    finally:
        release.set()
        await send
    assert len(board.test_calls) == 1


async def test_channel_and_agent_messages_stay_chronological_and_private(board):
    await board.send('first', 'earlier', message_id='before')
    await board.action(message())
    await board.send('second', 'later', message_id='after')
    assert [r['id'] for r in board.snapshot()['channel']] == ['before', 'channel-one', 'after']
    encoded = json.dumps(board.snapshot()['channel'])
    assert '_envelope' not in encoded and 'fixture-token' not in encoded
    with pytest.raises(ValueError):
        await board.bridge(board.nodes['first'].bridge_token,
                           {**message(), 'operation': 'channel_send'})


async def test_large_unicode_broadcast_keeps_all_statuses_with_bounded_projection(board):
    for i in range(12):
        key = 'node' + str(i)
        board.nodes[key] = Node(key, 'codex', '', key, layout({}), state='ready')
        board.nodes[key].address = ('http://127.0.0.1:1', 'fixture-token')
    board._request = lambda url, token, envelope: dict(envelope, state='completed', result='🌲' * 4000)
    snapshot = await board.action({**message(), 'targets': ['node' + str(i) for i in range(12)]})
    row, = snapshot['channel']
    assert row['trimmed'] and row['state'] == 'completed'
    assert len(row['receipts']) == 12 and all(r['result_truncated'] for r in row['receipts'])
    assert len(json.dumps(snapshot['channel'], ensure_ascii=True).encode()) < 200_000
    assert all(len(r['result']) == 4000 for r in board.receipts.values())


async def test_batch_capacity_checked_before_any_delivery(board):
    board.receipts = {str(i): {} for i in range(1023)}
    with pytest.raises(ValueError, match='message limit'):
        await board.action(message())
    assert not board.test_calls and not board._channel_attempts


async def test_actual_owner_http_auth_and_child_cannot_broadcast(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'ROOT', tmp_path / 'runtime')
    parent = StudioServer(EventBus(), session={'session_id': 'owner', 'workspace': str(tmp_path)})
    try:
        url = (await asyncio.wait_for(parent.start(), 5)).split('/#')[0]
        board = parent._board()
        board.nodes['first'] = Node('first', 'codex', '', 'First', layout({}), state='ready')
        board.nodes['first'].address = ('http://127.0.0.1:1', 'fixture-token')
        calls = []
        def receive(url, token, envelope):
            calls.append(envelope)
            return dict(envelope, state='queued')
        board._request = receive
        request = {**message(), 'targets': ['first']}
        async with httpx.AsyncClient(trust_env=False) as client:
            for token in ('wrong', board.nodes['first'].bridge_token):
                response = await client.post(url + '/api/deepsleep',
                    headers={'X-Dream-Token': token}, json=request)
                assert response.status_code == 401
            assert not calls
            response = await client.post(url + '/api/deepsleep',
                headers={'X-Dream-Token': parent.token}, json=request)
            assert response.status_code == 200 and response.json()['channel'][0]['state'] == 'queued'
            response = await client.post(url + '/api/deepsleep',
                headers={'X-Dream-Token': parent.token}, json=request)
            assert response.status_code == 200 and len(calls) == 1
    finally:
        await parent.stop()
