import asyncio
import contextlib
import json
from types import SimpleNamespace

import httpx
import pytest

from dream.core.backends.base import Event
from dream.core.linked_sessions import LinkedMessages, LinkedPrompt, TurnCapture, _CURRENT, current_envelope, activate, deactivate
from dream.tools import session_tools


def envelope(**changes):
    return {'id': 'message-1', 'source': 'sender', 'target': 'receiver', 'prompt': '/quit',
            'root_id': 'root-1', 'visited': ['sender'], 'hops': 1, **changes}


def test_accept_idempotent_and_conflict_does_not_requeue():
    messages, queue = LinkedMessages('receiver'), []
    first = messages.accept(envelope(), queue.append)
    assert messages.accept(envelope(), queue.append) == first
    with pytest.raises(ValueError, match='different content'):
        messages.accept(envelope(prompt='different'), queue.append)
    assert len(queue) == 1 and isinstance(queue[0], LinkedPrompt)
    assert not queue[0].text.startswith('/') and 'External linked-session material' in queue[0].text
    first['state'] = 'completed'
    assert messages.receipts()[0]['state'] == 'queued'


@pytest.mark.parametrize('changes', [{'target': 'other'}, {'source': 'receiver'}, {'visited': ['receiver']},
                                    {'visited': ['sender', 'sender']}, {'visited': 'sender'}, {'hops': True},
                                    {'hops': 9}, {'id': '../bad'}, {'prompt': ''}, {'prompt': 'x' * 32769},
                                    {'prompt': '\U0001f600' * 20000}, {'unexpected': 'field'}])
def test_malformed_or_cyclic_envelope_refused(changes):
    messages, queue = LinkedMessages('receiver'), []
    with pytest.raises(ValueError):
        messages.accept(envelope(**changes), queue.append)
    assert not queue and not messages.receipts()


def test_queue_refusal_does_not_consume_id_and_restart_does_not_replay():
    messages = LinkedMessages('receiver')
    def refuse(_):
        raise ValueError('queue full')
    with pytest.raises(ValueError):
        messages.accept(envelope(), refuse)
    assert not messages.receipts()
    messages.accept(envelope(), lambda _: None)
    assert LinkedMessages('receiver').receipts() == []


def test_receipt_capacity_does_not_evict_duplicate_protection():
    messages = LinkedMessages('receiver')
    for i in range(128):
        messages.accept(envelope(id=f'm-{i}'), lambda _: None)
    with pytest.raises(ValueError, match='capacity'):
        messages.accept(envelope(id='overflow'), lambda _: None)
    assert messages.accept(envelope(id='m-0'), lambda _: pytest.fail('duplicate enqueued'))['id'] == 'm-0'


def test_terminal_settlement_is_once_and_output_is_bounded():
    messages = LinkedMessages('receiver')
    messages.accept(envelope(), lambda _: None)
    with pytest.raises(ValueError):
        messages.finish('message-1', 'completed')
    messages.start('message-1')
    messages.finish('message-1', 'completed', 'x' * 70000)
    receipt = messages.receipts()[0]
    assert len(receipt['result']) == 65536 and receipt['result_truncated']
    with pytest.raises(ValueError):
        messages.finish('message-1', 'failed')


def test_intermediate_assistant_or_turn_end_is_not_success():
    capture = TurnCapture()
    for event in [Event('assistant_done', 'intermediate'), Event('turn_end', {})]:
        capture.observe(event)
    assert capture.result == 'intermediate' and not capture.saw_result
    capture.observe(Event('result', {'subtype': 'success', 'result': 'final'}))
    assert capture.saw_result and not capture.failed and capture.result == 'final'
    capture.observe(Event('error', 'secret-bearing exception'))
    assert capture.failed and 'secret' not in capture.error


@pytest.fixture
def app():
    from dream.tui.app import App
    instance = App.__new__(App)
    instance._linked_messages = LinkedMessages('receiver')
    instance._linked_capture = None
    instance._gui_prompts = asyncio.Queue(maxsize=32)
    instance._deferred_gui_prompt = None
    instance._council_pending = None
    instance.engine = SimpleNamespace()
    instance.interrupted = False
    instance._render_event = lambda event: None
    return instance


@pytest.mark.asyncio
@pytest.mark.parametrize('events,interrupted,state,result', [
    ([Event('assistant_done', 'final'), Event('result', {'subtype': 'success'})], False, 'completed', 'final'),
    ([Event('assistant_done', 'partial')], False, 'failed', 'partial'),
    ([Event('assistant_done', 'partial'), Event('result', {'is_error': True})], False, 'failed', 'partial'),
    ([Event('error', 'failure'), Event('result', {'subtype': 'success'})], False, 'failed', ''),
    ([Event('assistant_done', 'partial'), Event('result', {'subtype': 'success'})], True, 'interrupted', 'partial'),
])
async def test_real_stream_to_correlated_receipt(app, events, interrupted, state, result):
    async def ask_stream(prompt):
        assert not prompt.startswith('/')
        assert current_envelope()['id'] == 'message-1'
        for event in events:
            yield event
    app.engine.ask_chat = app.engine.ask = ask_stream
    async def ask(prompt):
        outcome = await app._stream(prompt)
        app.interrupted = interrupted
        return outcome
    app._ask = ask
    await app.linked_accept(envelope())
    await app._ask_linked(await app._gui_prompts.get())
    receipt = app.linked_receipts()[0]
    assert receipt['state'] == state and receipt['result'] == result
    assert current_envelope() is None
    assert await app.linked_accept(envelope()) == receipt
    assert app._gui_prompts.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize('exception,state', [(RuntimeError('private'), 'failed'), (asyncio.CancelledError(), 'interrupted')])
async def test_turn_exception_and_cancel_settle_before_propagating(app, exception, state):
    async def ask(prompt):
        raise exception
    app._ask = ask
    await app.linked_accept(envelope())
    with pytest.raises(type(exception)):
        await app._ask_linked(await app._gui_prompts.get())
    assert app.linked_receipts()[0]['state'] == state
    assert 'private' not in json.dumps(app.linked_receipts())
    assert current_envelope() is None


@pytest.mark.asyncio
async def test_run_routes_linked_slash_text_to_model_not_owner_command(app):
    calls = []
    async def noop(*args, **kwargs): pass
    async def incoming():
        if calls:
            raise EOFError
        return LinkedPrompt('message-1', '/quit')
    async def linked(prompt): calls.append(prompt)
    app.start = app._shutdown = noop
    app._next_input = incoming
    app._ask_linked = linked
    app._command = lambda _: pytest.fail('external slash reached owner command')
    app._sigint_cancels = lambda _: contextlib.nullcontext()
    app.renderer = SimpleNamespace(sep=lambda: None)
    await app.run()
    assert len(calls) == 1


@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.setenv('DREAM_DEEPSLEEP_NODE_ID', 'receiver')
    monkeypatch.setenv('DREAM_DEEPSLEEP_BRIDGE_URL', 'http://127.0.0.1:9999/api/deepsleep/bridge')
    monkeypatch.setenv('DREAM_DEEPSLEEP_BRIDGE_TOKEN', 'secret-token-123456789')
    calls = []
    outcome = [httpx.Response(200, json={'ok': True, 'result': {'nodes': []}})]
    def handle(request):
        calls.append(request)
        return outcome[0]
    native = httpx.AsyncClient
    def client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return native(transport=httpx.MockTransport(handle), **kwargs)
    monkeypatch.setattr(session_tools.httpx, 'AsyncClient', client)
    return calls, outcome


@pytest.mark.asyncio
async def test_tools_fixed_bridge_authority_and_ancestry(bridge):
    token = _CURRENT.set(envelope())
    try:
        await session_tools.session_send.handler({'target': 'third', 'prompt': 'work', 'id': 'safe-retry'})
    finally:
        _CURRENT.reset(token)
    request = bridge[0][0]
    assert request.headers['X-Dream-Token'] == 'secret-token-123456789'
    assert json.loads(request.content) == {'operation': 'send', 'target': 'third', 'prompt': 'work', 'id': 'safe-retry',
                                           'message_id': 'message-1', 'root_id': 'root-1', 'visited': ['sender', 'receiver'], 'hops': 2}
    await session_tools.session_inbox.handler({})
    await session_tools.session_list.handler({})
    assert [json.loads(r.content)['operation'] for r in bridge[0]] == ['send', 'inbox', 'list']


@pytest.mark.asyncio
@pytest.mark.parametrize('url', ['https://127.0.0.1:9999/api/deepsleep/bridge', 'http://example.com/api/deepsleep/bridge',
                               'http://127.0.0.1:9999/wrong', 'http://user@127.0.0.1:9999/api/deepsleep/bridge',
                               'http://127.0.0.1:9999/api/deepsleep/bridge?token=secret'])
async def test_bridge_refuses_nonfixed_or_remote_url(bridge, monkeypatch, url):
    monkeypatch.setenv('DREAM_DEEPSLEEP_BRIDGE_URL', url)
    assert (await session_tools.session_list.handler({}))['is_error']
    assert not bridge[0]


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [401, 403, 302, 500])
async def test_unauthorized_redirect_and_error_do_not_leak_body_or_retry(bridge, status):
    bridge[1][0] = httpx.Response(status, text='secret-token-123456789')
    result = await session_tools.session_send.handler({'target': 'third', 'prompt': 'work', 'id': 'retry-id'})
    assert result['is_error'] and 'retry-id' in str(result) and 'secret-token' not in str(result)
    assert len(bridge[0]) == 1


@pytest.mark.parametrize('code,expected', [('link_required', 'directed link'), ('id_conflict', 'different content'),
                                         ('unknown_target', 'exact node ID'), ('unexpected', 'request is invalid'),
                                         ([], 'invalid error response')])
async def test_bridge_validation_errors_use_fixed_messages_only(bridge, code, expected):
    bridge[1][0] = httpx.Response(400, json={'code': code, 'error': 'secret-token-123456789 PRIVATE ARBITRARY BODY'})
    result = await session_tools.session_send.handler({'target': 'third', 'prompt': 'work', 'id': 'retry-id'})
    assert result['is_error'] and expected in str(result)
    assert 'PRIVATE' not in str(result) and 'secret-token' not in str(result)
    assert len(bridge[0]) == 1


async def test_bridge_error_response_size_remains_bounded(bridge):
    bridge[1][0] = httpx.Response(400, content=b'x' * 262145)
    result = await session_tools.session_inbox.handler({})
    assert result['is_error'] and 'size limit' in str(result)


@pytest.mark.asyncio
async def test_response_size_and_credential_redaction(bridge):
    bridge[1][0] = httpx.Response(200, json={'ok': True, 'result': {'unexpected': 'secret-token-123456789'}})
    assert 'secret-token' not in str(await session_tools.session_list.handler({}))
    bridge[1][0] = httpx.Response(200, content=b'x' * 262145)
    assert (await session_tools.session_inbox.handler({}))['is_error']


@pytest.mark.asyncio
async def test_model_cannot_supply_ancestry_or_credentials(bridge):
    result = await session_tools.session_send.handler({'target': 'third', 'prompt': 'work', 'root_id': 'forged'})
    assert result['is_error'] and not bridge[0]


@pytest.mark.parametrize('child', [False, True])
def test_registry_offers_session_tools_only_to_children_even_with_narrow_preset(monkeypatch, child):
    from dream.tools import registry
    from dream.core import task_checkpoints
    monkeypatch.setattr(registry, '_load_custom_tools', lambda: ([], []))
    monkeypatch.setattr(registry.extensions, 'filter_tools', lambda tools: tools)
    monkeypatch.setattr(registry.extensions, 'guard_tool', lambda tool: tool)
    monkeypatch.setattr(registry.presets, 'session', lambda: {'tools': frozenset(), 'plugins': frozenset()})
    monkeypatch.setattr(task_checkpoints, 'wrap_tool', lambda tool: tool)
    monkeypatch.setattr(registry, 'create_sdk_mcp_server', lambda *args: {})
    monkeypatch.setattr('dream.core.backends.anthropic.always_load', lambda server, names: server)
    if child:
        monkeypatch.setenv('DREAM_DEEPSLEEP_NODE_ID', 'receiver')
    else:
        monkeypatch.delenv('DREAM_DEEPSLEEP_NODE_ID', raising=False)
    names = set(registry.build()['names'])
    assert {'session_list', 'session_send', 'session_inbox'}.issubset(names) is child


@pytest.mark.asyncio
async def test_app_rejects_nonchild_and_queue_full_without_receipt(app):
    app._linked_messages = None
    with pytest.raises(ValueError, match='does not accept'):
        await app.linked_accept(envelope())
    app._linked_messages = LinkedMessages('receiver')
    for _ in range(32):
        app._gui_prompts.put_nowait('existing')
    with pytest.raises(ValueError, match='queue is full'):
        await app.linked_accept(envelope())
    assert not app.linked_receipts()


@pytest.mark.asyncio
async def test_simultaneous_same_id_acceptance_enqueues_once(app):
    receipts = await asyncio.gather(*(app.linked_accept(envelope()) for _ in range(10)))
    assert all(receipt == receipts[0] for receipt in receipts)
    assert app._gui_prompts.qsize() == 1


@pytest.mark.asyncio
async def test_linked_prompt_traverses_actual_gui_queue_reader(app, monkeypatch):
    import dream.tui.app as app_module
    async def typed():
        await asyncio.Future()
    app.studio = object()
    app.session = SimpleNamespace(prompt_async=lambda _: typed())
    shown = []
    app.renderer = SimpleNamespace(system=shown.append)
    monkeypatch.setattr(app_module, 'patch_stdout', contextlib.nullcontext)
    await app.linked_accept(envelope())
    received = await app._next_input()
    assert isinstance(received, LinkedPrompt) and received.identifier == 'message-1'
    assert shown and 'External linked-session' in shown[0]


@pytest.mark.asyncio
async def test_native_mcp_separate_task_retains_active_turn_ancestry(bridge):
    from contextvars import Context
    token = activate(envelope())
    try:
        # A server task was started outside the turn's ContextVar scope.
        task = asyncio.create_task(session_tools.session_send.handler({'target': 'third', 'prompt': 'work'}), context=Context())
        await task
        body = json.loads(bridge[0][0].content)
        assert body['root_id'] == 'root-1' and body['visited'] == ['sender', 'receiver']
    finally:
        deactivate(token)
    assert current_envelope() is None


def test_worker_snapshot_is_bounded_and_excludes_transcript_and_tools(app):
    from dream.gui.bus import EventBus
    app.bus = EventBus()
    for i in range(40):
        app.bus.publish(Event('agent_activity', {'run_id': f'worker-{i}', 'agent': 'researcher',
            'kind': 'status', 'status': 'running', 'text': 'private transcript', 'model': 'example-model'}))
    app.bus.publish(Event('agent_activity', {'run_id': 'worker-39', 'agent': 'researcher',
        'kind': 'status', 'status': 'completed', 'text': 'private result'}))
    rows = app.linked_agents()
    assert len(rows) == 32 and rows[-1]['state'] == 'completed'
    assert rows[-1]['model'] == 'example-model'
    assert 'private' not in json.dumps(rows)


@pytest.mark.asyncio
@pytest.mark.parametrize('success,state', [(True, 'completed'), (False, 'failed')])
async def test_owner_prompt_gets_one_correlated_receipt_without_reframing(app, success, state):
    prompts = []
    async def turn(prompt, *, workflow=None):
        prompts.append(prompt)
        assert current_envelope()['source'] == 'owner'
        app._linked_capture.observe(Event('assistant_done', 'owner answer'))
        app._linked_capture.observe(Event('result', {'subtype': 'success' if success else 'failed'}))
        return success
    app._ask_turn = turn
    result = await app._ask('Ordinary owner request')
    assert result is success and prompts == ['Ordinary owner request']
    assert len(app.linked_receipts()) == 1
    receipt = app.linked_receipts()[0]
    assert receipt['source'] == 'owner' and receipt['target'] == 'receiver'
    assert receipt['root_id'] == receipt['id'] and receipt['hops'] == 0 and receipt['visited'] == []
    assert receipt['state'] == state and receipt['result'] == 'owner answer'


@pytest.mark.asyncio
async def test_linked_prompt_through_ask_wrapper_does_not_make_owner_receipt(app):
    async def turn(prompt, *, workflow=None):
        app._linked_capture.observe(Event('result', {'subtype': 'success', 'result': 'linked answer'}))
        return True
    app._ask_turn = turn
    await app.linked_accept(envelope())
    assert await app._ask_linked(await app._gui_prompts.get()) is True
    assert len(app.linked_receipts()) == 1 and app.linked_receipts()[0]['source'] == 'sender'
def test_result_byte_limit_preserves_unicode_and_truthful_truncation():
    from dream.core.linked_sessions import bounded_result, TurnCapture, MAX_RESULT
    from types import SimpleNamespace
    answer, truncated = bounded_result('漢' * 30000)
    assert len(answer.encode()) <= MAX_RESULT and truncated
    assert answer == '漢' * (MAX_RESULT // 3)
    capture = TurnCapture()
    capture.observe(SimpleNamespace(kind='assistant_done', data='漢' * 30000))
    assert capture.result == answer and capture.truncated
    capture.observe(SimpleNamespace(kind='result', data={'result': '漢' * 30000}))
    assert capture.result == answer and capture.truncated and capture.saw_result
