"""Read-only activity does not create work and never exposes child credentials."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from dream.core.backends.base import Event
from dream.gui.bus import EventBus
from dream.gui.deepsleep import Node, layout
from dream.gui.server import StudioServer
from dream.gui.terminal_activity import ActivityReader, LIMIT, fetch, project
from dream.gui.conversation import Conversation


def node():
    return Node('fixture', 'codex', '', 'Codex', layout({}), state='ready',
                process=SimpleNamespace(returncode=None),
                address=('http://127.0.0.1:8124', 'private-child-token'), launch_generation=2)


def test_projection_is_bounded_and_does_not_expose_executable_artifacts():
    events = [{'kind': 'text_delta', 'data': 'readable answer'},
              {'kind': 'artifact', 'data': {'html': '<script>run()</script>'}}]
    assert project({'events': events})['events'] == [{'id': '0', 'kind': 'text_delta', 'data': 'readable answer'}]
    result = project({'events': [{'kind': 'tool_result', 'data': {'content': 'x' * 50000}}] * 450})
    assert result['trimmed'] and len(result['events']) <= 300
    assert len(json.dumps(result).encode()) < LIMIT
    assert 'Display truncated' in result['events'][-1]['data']['content']


def test_large_event_preserves_prior_rows_and_source_ids_survive_both_projections():
    conversation = Conversation(max_events=305)
    for i in range(305):
        conversation.append(Event('user', str(i)))
    before = project(project(conversation.snapshot(with_ids=True)))
    conversation.append(Event('tool_use', {str(i): 'x' * 16000 for i in range(20)}))
    after = project(project(conversation.snapshot(with_ids=True)))
    assert after['trimmed'] and len(after['events']) == 300
    assert 'omitted' in after['events'][-1]['data']
    previous = {row['data']: row['id'] for row in before['events']}
    for row in after['events'][:-1]:
        assert previous[row['data']] == row['id']
    conversation.clear()
    conversation.append(Event('text_delta', 'a'))
    first_id = conversation.snapshot(with_ids=True)['events'][0]['id']
    conversation.append(Event('text_delta', 'b'))
    assert conversation.snapshot(with_ids=True)['events'][0] == {'id': first_id, 'kind': 'text_delta', 'data': 'ab'}
    assert 'id' not in conversation.snapshot()['events'][0]


@pytest.mark.parametrize('address', ['https://127.0.0.1:8124', 'http://example.test:8124',
    'http://127.0.0.1:8124/path', 'http://user@127.0.0.1:8124',
    'http://127.0.0.1:8124?url=http://example.test', 'file:///tmp/data'])
async def test_reader_refuses_non_owned_address_without_network(address, monkeypatch):
    opener = Mock()
    monkeypatch.setattr('dream.gui.terminal_activity.httpx.AsyncClient', opener)
    with pytest.raises(ValueError):
        await fetch(address, 'secret')
    opener.assert_not_called()


async def test_native_and_stopped_nodes_never_read_child():
    read = Mock()
    reader = ActivityReader(read)
    n = node()
    board = SimpleNamespace(nodes={n.id: n})
    n.profile = 'native'
    assert not (await reader.get(board, n))['available']
    n.profile = 'dream'
    n.process.returncode = 0
    assert not (await reader.get(board, n))['available']
    read.assert_not_called()


async def test_concurrent_reads_are_coalesced_and_generation_change_discards_result():
    gate, started = asyncio.Event(), asyncio.Event()
    calls = []
    async def read(*args):
        calls.append(args)
        started.set()
        await asyncio.wait_for(gate.wait(), 3)
        return project({'events': [{'kind': 'tool_use', 'data': {'name': 'read_file', 'id': 'tool1'}}]})
    reader = ActivityReader(read)
    n = node()
    board = SimpleNamespace(nodes={n.id: n})
    tasks = [asyncio.create_task(reader.get(board, n)) for _ in range(8)]
    try:
        await asyncio.wait_for(started.wait(), 2)
        n.launch_generation += 1
        gate.set()
        results = await asyncio.gather(*tasks)
        assert len(calls) == 1
        assert all(not result['available'] for result in results)
        assert all('private-child-token' not in json.dumps(result) for result in results)
    finally:
        gate.set()
        await reader.close()


async def test_cancellation_does_not_duplicate_inflight_read():
    gate, started = asyncio.Event(), asyncio.Event()
    calls = []
    async def read(*args):
        calls.append(args)
        started.set()
        await asyncio.wait_for(gate.wait(), 3)
        return project({'events': []})
    reader = ActivityReader(read)
    n = node()
    board = SimpleNamespace(nodes={n.id: n})
    task = asyncio.create_task(reader.get(board, n))
    try:
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        next_task = asyncio.create_task(reader.get(board, n))
        gate.set()
        assert (await next_task)['available']
        assert (await reader.get(board, n))['available']
        assert len(calls) == 1
    finally:
        gate.set()
        await reader.close()


async def test_authenticated_routes_are_display_only(tmp_path, monkeypatch):
    monkeypatch.delenv('DREAM_DEEPSLEEP_NODE_ID', raising=False)
    bus = EventBus()
    prompt = Mock()
    server = StudioServer(bus, session={'workspace': str(tmp_path)}, on_prompt=prompt)
    bus.publish(Event('text_delta', 'existing answer'))
    board = server._board()
    n = node()
    board.nodes[n.id] = n
    read = AsyncMock(return_value=project({'events': [{'kind': 'thinking_delta', 'data': 'Reported activity'}]}))
    server._terminal_activity = ActivityReader(read)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://127.0.0.1:8123') as client:
        for path in ['/api/conversation', '/api/deepsleep/activity?node=fixture']:
            assert (await client.get(path)).status_code == 401
            assert (await client.get(path + ('&' if '?' in path else '?') + 'token=' + server.token)).status_code == 401
        headers = {'X-Dream-Token': server.token}
        result = (await client.get('/api/conversation', headers=headers)).json()
        assert result['events'][0]['data'] == 'existing answer'
        result = (await client.get('/api/deepsleep/activity?node=fixture', headers=headers)).json()
        assert result['available'] and result['generation'] == '2'
        assert result['events'][0]['kind'] == 'thinking_delta'
        assert 'private-child-token' not in json.dumps(result)
        assert (await client.get('/api/deepsleep/activity?node=missing', headers=headers)).status_code == 404
        assert (await client.get('/api/deepsleep/activity?node=../wrong', headers=headers)).status_code == 400
        assert (await client.post('/api/conversation', headers=headers)).status_code == 405
        prompt.assert_not_called()
    await server._terminal_activity.close()


@pytest.mark.parametrize('mode', ['oversize', 'redirect', 'trickled_headers'])
async def test_http_reader_bounds_entire_response_and_never_follows_redirects(mode):
    handlers, requests = set(), []
    async def serve(reader, writer):
        task = asyncio.current_task()
        handlers.add(task)
        try:
            requests.append((await reader.readuntil(b'\r\n\r\n')).decode())
            if mode == 'redirect':
                writer.write(b'HTTP/1.1 302 Found\r\nLocation: http://example.test/\r\nContent-Length: 0\r\n\r\n')
            elif mode == 'oversize':
                body = b'x' * (LIMIT + 1)
                writer.write(f'HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n\r\n'.encode() + body)
            else:
                writer.write(b'HTTP/1.1 200 OK\r\nX-Trickle: ')
                while True:
                    writer.write(b'x')
                    await writer.drain()
                    await asyncio.sleep(.1)
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            handlers.discard(task)
    server = await asyncio.start_server(serve, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async with asyncio.timeout(5):
            with pytest.raises((ValueError, httpx.HTTPStatusError, TimeoutError)):
                await fetch(f'http://127.0.0.1:{port}', 'fixture-token')
        assert len(requests) == 1
        assert requests[0].startswith('GET /api/conversation HTTP/1.1')
        assert 'x-dream-token: fixture-token' in requests[0].lower()
    finally:
        server.close()
        await server.wait_closed()
        pending = list(handlers)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
