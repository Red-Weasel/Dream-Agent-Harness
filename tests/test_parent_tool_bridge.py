"""Private parent transport and real stdio MCP; no provider/model invocations."""
import asyncio
import base64
import json
import os
from pathlib import Path
import socket
import sys
import uuid

import httpx
import mcp.types as types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest
from starlette.applications import Starlette
import uvicorn

from dream.mcp.bridge import BridgeError, ParentBridgeClient, SessionToolBridge, read_discovery
from dream.mcp import server as mcp_server


SCHEMA = {'name': 'studio_fixture', 'description': 'parent-owned fixture',
          'inputSchema': {'type': 'object', 'properties': {'value': {'type': 'string'}}}}
RICH_RESULT = {'content': [{'type': 'text', 'text': 'parent refusal'},
                          {'type': 'image', 'mimeType': 'image/png', 'data': base64.b64encode(b'fixture').decode()}],
               'structuredContent': {'artifact': 'fixture-id'}, 'is_error': True}


@pytest.fixture
async def bridge():
    calls = []
    active_tools = [SCHEMA]
    async def call(name, arguments):
        calls.append((name, arguments))
        return RICH_RESULT
    parent = SessionToolBridge('fixture-session', lambda: active_tools, call)
    parent.publish('http://127.0.0.1:8123')
    app = Starlette(routes=parent.routes())
    transport = httpx.ASGITransport(app=app, client=('127.0.0.1', 2345))
    client = ParentBridgeClient(parent.discovery_path, transport=transport)
    yield parent, client, calls, active_tools, transport
    await client.close()
    await parent.close()


async def test_dynamic_parent_tools_and_rich_results_survive(bridge):
    parent, client, calls, active, _ = bridge
    assert [tool.name for tool in await client.list_tools()] == ['studio_fixture']
    result = await client.call_tool('studio_fixture', {'value': 'inspect'})
    assert result.isError and result.structuredContent == {'artifact': 'fixture-id'}
    assert result.content[1].type == 'image'
    assert calls == [('studio_fixture', {'value': 'inspect'})]
    active.clear()
    assert await client.list_tools() == []
    result = await client.call_tool('studio_fixture', {})
    assert result.isError and len(calls) == 1


async def test_real_sdk_type_maps_and_typeddicts_match_sdk_wire_schema(bridge):
    from typing import Annotated, NotRequired, TypedDict
    from claude_agent_sdk import create_sdk_mcp_server, tool

    class TypedArguments(TypedDict):
        name: str
        count: NotRequired[int]

    @tool('sdk_simple', 'SDK shorthand declarations',
          {'value': str, 'count': int, 'ratio': float, 'flag': bool, 'names': list[str],
           'options': dict, 'label': Annotated[str, 'label description']})
    async def simple(arguments):
        pytest.fail('Schema listing must never invoke the tool handler')

    @tool('sdk_typed', 'SDK TypedDict declaration', TypedArguments)
    async def typed(arguments):
        pytest.fail('Schema listing must never invoke the tool handler')

    parent, client, calls, active, _ = bridge
    active[:] = [simple, SCHEMA, typed]
    expected_server = create_sdk_mcp_server(name='expected-schemas', tools=[simple, typed])['instance']
    expected = await expected_server.request_handlers[types.ListToolsRequest](types.ListToolsRequest(method='tools/list'))
    listing = await client.list_tools()
    assert [item.name for item in listing] == ['sdk_simple', 'studio_fixture', 'sdk_typed']
    by_name = {item.name: item.inputSchema for item in listing}
    assert by_name['sdk_simple'] == expected.root.tools[0].inputSchema
    assert by_name['sdk_typed'] == expected.root.tools[1].inputSchema
    assert by_name['sdk_simple']['properties']['value'] == {'type': 'string'}
    assert by_name['sdk_simple']['properties']['names']['items'] == {'type': 'string'}
    assert by_name['sdk_typed']['required'] == ['name']
    # Conversion is reused by the dispatch-side live-tool check too. Only the
    # parent callback runs, never the handler used to obtain the schema.
    result = await client.call_tool('sdk_simple', {'value': 'parent callback'})
    assert result.isError and calls == [('sdk_simple', {'value': 'parent callback'})]


async def test_discovery_private_session_bound_and_removed_on_close(bridge, tmp_path):
    parent, client, *_ = bridge
    path = parent.discovery_path
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert parent.environment['DREAM_PARENT_BRIDGE_FILE'] == str(path)
    assert set(parent.environment) == {'DREAM_PARENT_BRIDGE_FILE', 'DREAM_SESSION_ID'}
    with pytest.raises(BridgeError):
        read_discovery(path, 'another-session')
    path.chmod(0o644)
    with pytest.raises(BridgeError):
        read_discovery(path)
    path.chmod(0o600)
    alias = path.parent / 'symlink'
    alias.symlink_to(path)
    with pytest.raises(BridgeError):
        read_discovery(alias)
    await parent.close()
    assert not path.exists()
    with pytest.raises(BridgeError):
        await client.list_tools()


async def test_browser_wrong_token_wrong_session_and_host_are_rejected(bridge):
    parent, _, calls, _, transport = bridge
    data = read_discovery(parent.discovery_path)
    valid = {'x-dream-bridge-token': data['token'], 'x-dream-session-id': parent.session_id}
    async with httpx.AsyncClient(base_url=data['url'], transport=transport) as http:
        for headers in ({}, dict(valid, origin='http://127.0.0.1:8123'),
                        dict(valid, **{'x-dream-bridge-token': 'wrong'}),
                        dict(valid, **{'x-dream-session-id': 'wrong'}), dict(valid, host='attacker.example')):
            response = await http.get('/api/mcp/tools', headers=headers)
            assert response.status_code == 403
    assert calls == []


async def test_duplicate_request_id_never_replays_completed_handler(bridge):
    parent, _, calls, _, transport = bridge
    data = read_discovery(parent.discovery_path)
    headers = {'x-dream-bridge-token': data['token'], 'x-dream-session-id': parent.session_id}
    body = {'request_id': str(uuid.uuid4()), 'name': 'studio_fixture', 'arguments': {}}
    async with httpx.AsyncClient(base_url=data['url'], headers=headers, transport=transport) as http:
        results = await asyncio.gather(*(http.post('/api/mcp/call', json=body) for _ in range(2)))
        assert sorted(result.status_code for result in results) == [200, 409]
        assert (await http.post('/api/mcp/call', json=body)).status_code == 409
    assert len(calls) == 1


async def test_timeout_records_uncertain_effect_and_never_replays():
    count = 0
    async def call(name, arguments):
        nonlocal count
        count += 1
        await asyncio.sleep(10)
    parent = SessionToolBridge('timeout-session', lambda: [SCHEMA], call, timeout=.02)
    parent.publish('http://127.0.0.1:8123')
    transport = httpx.ASGITransport(app=Starlette(routes=parent.routes()), client=('127.0.0.1', 1))
    data = read_discovery(parent.discovery_path)
    headers = {'x-dream-bridge-token': data['token'], 'x-dream-session-id': parent.session_id}
    body = {'request_id': str(uuid.uuid4()), 'name': 'studio_fixture', 'arguments': {}}
    try:
        async with httpx.AsyncClient(base_url=data['url'], headers=headers, transport=transport) as http:
            result = await http.post('/api/mcp/call', json=body)
            assert result.status_code == 504 and 'effects may have completed' in result.text
            result = await http.post('/api/mcp/call', json=body)
            assert result.status_code == 409
        assert count == 1
    finally:
        await parent.close()


async def test_interrupt_cancels_parent_handler_but_preserves_bridge_for_next_turn():
    entered, cancelled = asyncio.Event(), asyncio.Event()
    effects = []
    async def call(name, arguments):
        effects.append(arguments['value'])
        if arguments['value'] == 'long':
            entered.set()
            try:
                await asyncio.sleep(30)
            finally:
                cancelled.set()
        return {'content': [{'type': 'text', 'text': 'next turn works'}]}
    parent = SessionToolBridge('interrupt-session', lambda: [SCHEMA], call)
    parent.publish('http://127.0.0.1:8123')
    discovery_before = parent.discovery_path.read_bytes()
    transport = httpx.ASGITransport(app=Starlette(routes=parent.routes()), client=('127.0.0.1', 1))
    client = ParentBridgeClient(parent.discovery_path, transport=transport)
    current = asyncio.create_task(client.call_tool('studio_fixture', {'value': 'long'}))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(parent.cancel_pending(), 3)
        with pytest.raises(BridgeError, match='409'):
            await current
        assert cancelled.is_set() and not parent._pending
        assert parent.discovery_path.read_bytes() == discovery_before
        assert not parent._closed
        assert all('uncertain' in status for status in parent._requests.values())
        assert [tool.name for tool in await client.list_tools()] == ['studio_fixture']
        result = await client.call_tool('studio_fixture', {'value': 'next'})
        assert result.content[0].text == 'next turn works' and not result.isError
        assert effects == ['long', 'next']
    finally:
        current.cancel()
        await asyncio.gather(current, return_exceptions=True)
        await client.close()
        await parent.close()


async def test_bounded_parent_concurrency():
    active = peak = 0
    async def call(name, arguments):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(.02)
            return {'content': [{'type': 'text', 'text': 'ok'}]}
        finally:
            active -= 1
    parent = SessionToolBridge('concurrent-session', lambda: [SCHEMA], call, max_concurrency=2)
    parent.publish('http://127.0.0.1:8123')
    transport = httpx.ASGITransport(app=Starlette(routes=parent.routes()), client=('127.0.0.1', 1))
    client = ParentBridgeClient(parent.discovery_path, transport=transport)
    try:
        await asyncio.gather(*(client.call_tool('studio_fixture', {}) for _ in range(6)))
        assert peak == 2 and active == 0
    finally:
        await client.close()
        await parent.close()


async def test_bridge_bounds_queued_calls_before_retaining_large_arguments():
    release = asyncio.Event()
    async def call(name, arguments):
        await release.wait()
        return {'content': [{'type': 'text', 'text': 'ok'}]}
    parent = SessionToolBridge('queue-session', lambda: [SCHEMA], call, max_concurrency=1)
    parent.publish('http://127.0.0.1:8123')
    transport = httpx.ASGITransport(app=Starlette(routes=parent.routes()), client=('127.0.0.1', 1))
    client = ParentBridgeClient(parent.discovery_path, transport=transport)
    pending = [asyncio.create_task(client.call_tool('studio_fixture', {})) for _ in range(4)]
    try:
        async with asyncio.timeout(2):
            while len(parent._pending) < 4:
                await asyncio.sleep(.001)
            with pytest.raises(BridgeError, match='429'):
                await client.call_tool('studio_fixture', {})
            assert len(parent._requests) == 4
            release.set()
            await asyncio.gather(*pending)
    finally:
        release.set()
        await asyncio.gather(*pending, return_exceptions=True)
        await client.close()
        await parent.close()


@pytest.mark.parametrize('url', ['https://127.0.0.1:1', 'http://localhost:1', 'http://example.com:1',
                                  'http://user:pass@127.0.0.1:1', 'http://127.0.0.1:1/path'])
async def test_discovery_rejects_nonlocal_or_ambiguous_endpoints(url):
    parent = SessionToolBridge('s', lambda: [], lambda *a: None)
    try:
        with pytest.raises(BridgeError):
            parent.publish(url)
    finally:
        await parent.close()


async def test_no_proxy_no_redirect_no_retry(bridge, monkeypatch):
    parent, *_ = bridge
    monkeypatch.setenv('HTTP_PROXY', 'http://should-never-connect.invalid:1')
    monkeypatch.setenv('HTTPS_PROXY', 'http://should-never-connect.invalid:1')
    calls = []
    def handler(request):
        calls.append(request.url)
        return httpx.Response(307, headers={'Location': 'http://elsewhere.invalid'})
    client = ParentBridgeClient(parent.discovery_path, transport=httpx.MockTransport(handler))
    try:
        assert client._http._trust_env is False
        with pytest.raises(BridgeError, match='307'):
            await client.call_tool('studio_fixture', {})
        assert len(calls) == 1 and calls[0].host == '127.0.0.1'
    finally:
        await client.close()


async def test_configured_bad_bridge_never_initializes_standalone_context(tmp_path, monkeypatch):
    monkeypatch.setenv('DREAM_PARENT_BRIDGE_FILE', str(tmp_path / 'missing'))
    monkeypatch.setattr(mcp_server, 'build_context_from_env', lambda: pytest.fail('global tool context touched'))
    with pytest.raises(BridgeError):
        await mcp_server.serve()


async def test_real_stdio_mcp_proxies_parent_tools_without_opening_memory(tmp_path):
    calls = []
    async def call(name, arguments):
        calls.append((name, arguments))
        return RICH_RESULT
    parent = SessionToolBridge('stdio-fixture', lambda: [SCHEMA], call)
    app = Starlette(routes=parent.routes())
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off'))
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    client_env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(tmp_path),
                  'DREAM_DB': str(tmp_path / 'must-not-open.sqlite'), 'PYTHONDONTWRITEBYTECODE': '1'}
    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(.01)
            parent.publish(f'http://127.0.0.1:{port}')
            client_env.update(parent.environment)
            params = StdioServerParameters(command=sys.executable, args=['-B', '-m', 'dream.mcp'], env=client_env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    assert [tool.name for tool in listing.tools] == ['studio_fixture']
                    result = await session.call_tool('studio_fixture', {'value': 'real-stdio'})
                    assert result.isError and result.structuredContent == {'artifact': 'fixture-id'}
                    assert result.content[1].type == 'image'
            assert calls == [('studio_fixture', {'value': 'real-stdio'})]
            assert not Path(client_env['DREAM_DB']).exists()
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, 3)
        sock.close()
        await parent.close()
