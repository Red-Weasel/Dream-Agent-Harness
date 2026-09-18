"""Real installed SDK validation/control protocol; only its transport is fake."""
import asyncio
import json

import pytest

from dream.core import moe
from dream.core.evaluator import ReviewSettings, ScopedReader, collect_review, review_backend
from dream.core.providers import get_provider
from dream.tools import context


@pytest.fixture
def sdk_wire(monkeypatch):
    class WireLog(list):
        hold = False
        failed = False
    instances = WireLog()

    class FixtureTransport:
        def __init__(self, prompt, options):
            self.options = options
            self.queue = asyncio.Queue()
            self.user_seen = asyncio.Event()
            self.closed = False
            self.users = []
            self.responses = {}
            self.hang = instances.hold
            instances.append(self)

        async def connect(self):
            pass

        def is_ready(self):
            return not self.closed

        async def close(self):
            self.closed = True

        async def end_input(self):
            pass

        async def read_messages(self):
            while not self.closed:
                message = await self.queue.get()
                if message is None:
                    return
                yield message

        async def request(self, identity, **request):
            await self.queue.put({'type': 'control_request', 'request_id': identity, 'request': request})

        async def finish(self):
            if self.hang:
                return
            await self.queue.put({'type': 'assistant', 'message': {
                'role': 'assistant', 'model': 'fixture-sdk',
                'content': [{'type': 'text', 'text': 'One visitor (S1).'}]}})
            await self.queue.put({'type': 'result', 'subtype': 'error_fixture' if instances.failed else 'success',
                'duration_ms': 1, 'duration_api_ms': 1, 'is_error': instances.failed,
                'num_turns': 1, 'session_id': 'fixture-session',
                'usage': {'input_tokens': 6, 'output_tokens': 2}})
            # A one-shot CLI exits after its terminal result. Keep EOF distinct
            # from the result so premature breaks fail the ownership assertion.
            await self.queue.put(None)

        async def write(self, data):
            for line in data.splitlines():
                obj = json.loads(line)
                if obj['type'] == 'control_request':
                    assert obj['request']['subtype'] == 'initialize'
                    await self.queue.put({'type': 'control_response', 'response': {
                        'subtype': 'success', 'request_id': obj['request_id'], 'response': {}}})
                elif obj['type'] == 'user':
                    self.users.append(obj)
                    self.user_seen.set()
                    await self.request('native', subtype='can_use_tool', tool_name='Bash',
                                       input={'command': 'must never execute'})
                elif obj['type'] == 'control_response':
                    response = obj['response']
                    identity = response['request_id']
                    self.responses[identity] = response
                    if identity == 'native':
                        await self.request('foreign', subtype='can_use_tool',
                                           tool_name='mcp__worker__run_script', input={})
                    elif identity == 'foreign':
                        await self.request('reader', subtype='can_use_tool',
                                           tool_name='mcp__review__read_file', input={'path': 'source.txt'})
                    elif identity == 'reader' and 'review' in self.options.mcp_servers:
                        await self.request('mcp-init', subtype='mcp_message', server_name='review',
                            message={'jsonrpc': '2.0', 'id': 0, 'method': 'initialize',
                                     'params': {'protocolVersion': '2024-11-05', 'capabilities': {},
                                                'clientInfo': {'name': 'fixture', 'version': '1'}}})
                    elif identity == 'mcp-init':
                        await self.request('mcp-ready', subtype='mcp_message', server_name='review',
                            message={'jsonrpc': '2.0', 'method': 'notifications/initialized'})
                    elif identity == 'mcp-ready':
                        await self.request('list-result', subtype='mcp_message', server_name='review',
                            message={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}})
                    elif identity == 'list-result':
                        await self.request('read-result', subtype='mcp_message', server_name='review',
                            message={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                                     'params': {'name': 'read_file', 'arguments': {'path': 'source.txt'}}})
                    else:
                        await self.finish()

    # query() and InternalClient remain real: the old string prompt fails in
    # _process_query_inner before this constructor can ever be reached.
    monkeypatch.setattr('claude_agent_sdk._internal.client.SubprocessCLITransport', FixtureTransport)
    return instances


async def invoke(consumer, workspace):
    if consumer == 'council':
        return await moe._consult_anthropic(get_provider('anthropic'), 'Source S1: one visitor.',
                                           cwd=str(workspace), model='fixture-sdk')
    backend = review_backend(ReviewSettings(get_provider('anthropic'), 'fixture-sdk'),
                             ScopedReader(workspace).tools(), 'Review only.', workspace)
    return await collect_review(backend, 'Source S1: one visitor.', 2)


@pytest.mark.parametrize('consumer', ['council', 'evaluator'])
async def test_real_sdk_accepts_streaming_prompt_and_enforces_review_scope(consumer, tmp_path, monkeypatch, sdk_wire):
    sentinel = object()
    monkeypatch.setattr(context, '_CTX', sentinel)
    (tmp_path / 'source.txt').write_text('Source S1: one blue ticket admits one visitor.')
    assert await invoke(consumer, tmp_path) == 'One visitor (S1).'
    wire, = sdk_wire
    assert wire.closed
    assert wire.users == [{'type': 'user', 'message': {'role': 'user', 'content': 'Source S1: one visitor.'},
                           'parent_tool_use_id': None, 'session_id': ''}]
    opts = wire.options
    assert opts.tools == [] and opts.setting_sources == [] and opts.strict_mcp_config
    assert json.loads(opts.settings)['disableAllHooks'] is True
    assert opts.permission_mode == 'default'
    assert opts.permission_prompt_tool_name == 'stdio'  # SDK's streaming callback configuration
    for name in ('native', 'foreign'):
        assert wire.responses[name]['response']['behavior'] == 'deny'
    assert wire.responses['reader']['response']['behavior'] == ('allow' if consumer == 'evaluator' else 'deny')
    if consumer == 'evaluator':
        assert set(opts.allowed_tools) == {'mcp__review__read_file', 'mcp__review__list_files'}
        listed = wire.responses['list-result']['response']['mcp_response']['result']['tools']
        assert {tool['name'] for tool in listed} == {'read_file', 'list_files'}
        schema = next(tool['inputSchema'] for tool in listed if tool['name'] == 'read_file')
        assert schema['type'] == 'object' and schema['required'] == ['path']
        assert schema['properties']['path'] == {'type': 'string'}
        read = wire.responses['read-result']['response']['mcp_response']['result']
        assert 'one blue ticket admits one visitor' in read['content'][0]['text']
    else:
        assert opts.mcp_servers == {} and opts.allowed_tools == []
    assert context._CTX is sentinel


@pytest.mark.parametrize('consumer', ['council', 'evaluator'])
async def test_real_sdk_error_result_closes_transport_before_reporting_failure(consumer, tmp_path, sdk_wire):
    (tmp_path / 'source.txt').write_text('Source S1: one visitor.')
    sdk_wire.failed = True
    with pytest.raises(RuntimeError, match='error_fixture'):
        await invoke(consumer, tmp_path)
    assert sdk_wire[0].closed


@pytest.mark.parametrize('consumer', ['council', 'evaluator'])
async def test_real_sdk_streaming_cancellation_closes_transport(consumer, tmp_path, sdk_wire):
    (tmp_path / 'source.txt').write_text('Source S1: one visitor.')
    sdk_wire.hold = True
    task = asyncio.create_task(invoke(consumer, tmp_path))
    try:
        for _ in range(100):
            if sdk_wire:
                break
            if task.done():
                await task
            await asyncio.sleep(.001)
        assert sdk_wire
        wire = sdk_wire[0]
        wire.hang = True
        await asyncio.wait_for(wire.user_seen.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert wire.closed
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
