"""Finite SDK responses through the real main adapter, Engine and durable Loop."""
import asyncio
from contextlib import aclosing
import json
import os
import socket
import subprocess

import anyio
import pytest
from claude_agent_sdk import (
    AssistantMessage, ClaudeSDKClient, ResultMessage, StreamEvent, TextBlock,
    ThinkingBlock, ToolResultBlock, ToolUseBlock, UserMessage,
)

from dream.core.backends.anthropic import AnthropicBackend
from dream.core.engine import Engine
from dream.core.loop import AutonomousLoop
from dream.core.providers import get_provider
from dream.memory.embeddings import Embedder, Reranker
from test_council_handoff import engine
from test_durable_autonomy import Reviewer


@pytest.fixture(autouse=True)
def deny_external(monkeypatch):
    attempts = []
    def denied(*args, **kwargs):
        attempts.append('forbidden socket/process/model/start')
        raise AssertionError(attempts[-1])
    for obj, name in ((socket.socket, 'connect'), (socket.socket, 'connect_ex'),
                      (subprocess.Popen, '__init__'), (os, 'system'),
                      (asyncio, 'create_subprocess_exec'), (asyncio, 'create_subprocess_shell'),
                      (Embedder, '_ensure'), (Reranker, '_ensure'), (Engine, 'start')):
        monkeypatch.setattr(obj, name, denied)
    for key in ('DREAM_EVALUATOR_PROVIDER', 'DREAM_EVALUATOR_MODEL', 'DREAM_EVALUATOR_TIMEOUT'):
        monkeypatch.delenv(key, raising=False)
    yield
    assert attempts == []


def terminal(subtype='success', reason='completed', is_error=False):
    fields = dict(subtype=subtype, duration_ms=12, duration_api_ms=7,
                  is_error=is_error, num_turns=2, session_id='native-fixture',
                  total_cost_usd=0.25, usage={'input_tokens': 7, 'output_tokens': 3},
                  api_error_status=529 if is_error else None)
    if reason != 'ABSENT':
        fields['terminal_reason'] = reason
    return ResultMessage(**fields)


CASES = [
    pytest.param(None, False, id='missing'),
    pytest.param(terminal(), True, id='completed'),
    pytest.param(terminal(reason='ABSENT'), True, id='older-absent'),
    pytest.param(terminal(reason=None), True, id='null-reason'),
    pytest.param(terminal(reason='max_turns'), False, id='max-turns'),
    pytest.param(terminal(reason='aborted_streaming'), False, id='aborted-streaming'),
    pytest.param(terminal(reason='aborted_tools'), False, id='aborted-tools'),
    pytest.param(terminal(reason=''), False, id='empty-reason'),
    pytest.param(terminal(reason=False), False, id='false-reason'),
    pytest.param(terminal(subtype='MaxTurns'), False, id='incomplete-subtype'),
    pytest.param(terminal(subtype=None), False, id='null-subtype'),
    pytest.param(terminal(subtype=''), False, id='empty-subtype'),
    pytest.param(terminal(is_error=True), False, id='true-error'),
    pytest.param(terminal(is_error='failed'), False, id='truthy-error'),
]


class FiniteClient:
    """Installed first-terminal boundary with a finite owned response wrapper."""
    def __init__(self, result, *, body=None, read_error=None, close_error=None,
                 held=False, blocked=False):
        self.messages = ([AssistantMessage(content=[TextBlock('Findings.\nSTATUS: DONE')],
                                           model='fixture-sdk')] if body is None else body)
        if result is not None:
            self.messages = [*self.messages, result]
        self.result = result
        self.read_error, self.close_error = read_error, close_error
        self.blocked = blocked
        self.read_entered = asyncio.Event()
        self.read_release = asyncio.Event()
        self.close_entered = asyncio.Event()
        self.close_release = asyncio.Event()
        if not held:
            self.close_release.set()
        self.prompts, self.owners, self.close_owners = [], [], []
        self.closes = 0
        self.exhaustions = 0
        self.overread = False
        self.before_query = None
        self.interrupts = 0

    async def query(self, prompt):
        self.prompts.append(prompt)
        if self.before_query:
            self.before_query()

    async def receive_messages(self):
        # An iterator without cleanup of its own isolates receive_response ownership.
        for message in self.messages:
            yield message
        if self.result is not None:
            self.overread = True
            raise AssertionError('consumed next-response sentinel')

    async def receive_response(self):
        self.owners.append(asyncio.current_task())
        with anyio.CancelScope():
            try:
                if self.blocked:
                    self.read_entered.set()
                    await self.read_release.wait()
                async with aclosing(ClaudeSDKClient.receive_response(self)) as response:
                    async for message in response:
                        yield message
                self.exhaustions += 1
                if self.read_error:
                    raise self.read_error
            finally:
                self.close_owners.append(asyncio.current_task())
                self.close_entered.set()
                await self.close_release.wait()
                self.closes += 1
                if self.close_error:
                    raise self.close_error

    async def interrupt(self):
        self.interrupts += 1
        self.read_release.set()


def backend_for(native):
    backend = AnthropicBackend(system_prompt='fixture system', mcp_server={},
        preapproved_tool_ids=['fixture-read'], agents=None, permission_cb=None,
        model='fixture-sdk', cwd='/tmp')
    backend.set_effort('high')
    backend.client = native
    return backend


async def configure(engine, native):
    engine.backend = backend_for(native)
    engine.provider = get_provider('anthropic')
    engine.model = 'fixture-sdk'
    engine.working.log_turn('user', 'REQUIRED: retain fixture goal')
    engine._pending_handoff = engine._council_transfer()
    await engine.record_council_results('fixture check', [
        {'advisor': 'codex', 'answer': 'ADVICE: inspect partial effects'}])


async def drain(stream):
    async with aclosing(stream):
        return [event async for event in stream]


def assert_owned(native, turns=1):
    assert native.closes == turns
    assert native.owners == native.close_owners
    assert not native.overread
    assert len(native.prompts) == turns


@pytest.mark.parametrize('receipt,complete', CASES)
async def test_actual_engine_completion_and_receipt_accounting(engine, receipt, complete):
    native = FiniteClient(receipt)
    await configure(engine, native)
    observed = await drain(engine.ask('continue'))
    results = [e.data for e in observed if e.kind == 'result']
    assert engine.turn_timing.summary()['outcome'] == ('completed' if complete else 'error')
    assert (engine._pending_handoff is None) is complete
    assert bool(engine._pending_council) is (not complete)
    assert len(results) == int(receipt is not None)
    assert next(e.data for e in observed if e.kind == 'assistant_done').endswith('STATUS: DONE')
    if receipt:
        data = results[0]
        assert data['subtype'] == receipt.subtype
        assert data['terminal_reason'] == receipt.terminal_reason
        assert bool(data['is_error']) is (not complete)
        assert data['usage'] == {'input_tokens': 7, 'output_tokens': 3}
        assert data['num_turns'] == 2 and data['duration_ms'] == 12
        assert data['api_error_status'] == receipt.api_error_status
        assert engine.session_tokens == 10 and engine.total_cost_usd == 0.25
        assert engine.runtime_meter.prompt_tokens == 7 and engine.runtime_meter.output_tokens == 3
    else:
        assert engine.session_tokens == 0 and engine.total_cost_usd == 0
    if not complete:
        assert any(e.kind == 'error' for e in observed)
    assert_owned(native)


@pytest.mark.parametrize('receipt,complete', CASES)
async def test_actual_loop_rejects_unfinished_worker_before_review(engine, tmp_path, receipt, complete):
    native = FiniteClient(receipt)
    await configure(engine, native)
    reviews = []
    loop = AutonomousLoop(engine, state_dir=tmp_path / 'runs', evaluator_timeout=2,
                          evaluator_backend_factory=lambda *args: reviews.append(args) or Reviewer())
    native.before_query = lambda: (loop.workspace / 'progress.md').write_text('# Progress\nFixture evidence\n')
    result = await loop.run('fixture goal', acceptance_criteria='- Preserve fixture evidence')
    ledger = [json.loads(row) for row in (loop.workspace / 'ledger.jsonl').read_text().splitlines()]
    assert result.status == ('done' if complete else 'error')
    assert len(reviews) == sum(row['event'] == 'worker_finished' for row in ledger) == int(complete)
    assert ledger[-1]['state']['uncertain'] is (not complete)
    assert (engine._pending_handoff is None) is complete
    assert bool(engine._pending_council) is (not complete)
    assert engine.session_tokens == (10 if receipt else 0)
    assert_owned(native)


@pytest.mark.parametrize('receipt', [None, terminal(), terminal(is_error=True)])
@pytest.mark.parametrize('fault', ['read', 'close', 'both'])
async def test_faults_retain_first_receipt_once_and_prevent_ack(engine, receipt, fault):
    native = FiniteClient(receipt, read_error=RuntimeError('read fault') if fault != 'close' else None,
                          close_error=RuntimeError('close fault') if fault != 'read' else None)
    await configure(engine, native)
    events = await drain(engine.ask('continue'))
    results = [e.data for e in events if e.kind == 'result']
    assert len(results) == int(receipt is not None)
    assert all(r['is_error'] for r in results)
    assert engine.session_tokens == (10 if receipt else 0)
    assert engine.total_cost_usd == (0.25 if receipt else 0)
    assert engine._pending_council and engine._pending_handoff
    assert engine.turn_timing.summary()['outcome'] == 'error'
    errors = ' '.join(str(e.data) for e in events if e.kind == 'error')
    if fault != 'close':
        assert 'read fault' in errors
    if fault != 'read':
        assert 'close fault' in errors
    assert_owned(native)


@pytest.mark.parametrize('fault', [False, True])
async def test_engine_publishes_no_terminal_before_owned_response_close(engine, fault):
    native = FiniteClient(terminal(), held=True, close_error=RuntimeError('close fault') if fault else None)
    await configure(engine, native)
    seen = []
    async def consume():
        async with aclosing(engine.ask('continue')) as stream:
            async for event in stream:
                seen.append(event)
    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(native.close_entered.wait(), 2)
        assert any(e.kind == 'assistant_done' for e in seen)
        assert not any(e.kind == 'result' for e in seen)
        assert engine._pending_council and engine._pending_handoff
        assert not task.done()
    finally:
        native.close_release.set()
        await asyncio.wait_for(task, 2)
    assert bool(engine._pending_council) is fault
    assert_owned(native)


@pytest.mark.parametrize('fault', [False, True])
async def test_early_close_settles_same_anyio_scope_without_yielding(fault):
    native = FiniteClient(terminal(), close_error=RuntimeError('close fault') if fault else None)
    stream = backend_for(native).ask('continue')
    assert (await anext(stream)).kind == 'assistant_done'
    if fault:
        with pytest.raises(RuntimeError, match='close fault'):
            await stream.aclose()
    else:
        await stream.aclose()
    assert_owned(native)
    assert native.exhaustions == 0


@pytest.mark.parametrize('position', ['read', 'close'])
@pytest.mark.parametrize('fault', [False, True])
async def test_external_cancellation_survives_cleanup_fault(engine, position, fault):
    native = FiniteClient(terminal(), blocked=position == 'read', held=position == 'close',
                          close_error=RuntimeError('close fault') if fault else None)
    await configure(engine, native)
    task = asyncio.create_task(drain(engine.ask('continue')))
    barrier = native.read_entered if position == 'read' else native.close_entered
    await asyncio.wait_for(barrier.wait(), 2)
    task.cancel('external cancellation')
    try:
        with pytest.raises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(task, 2)
        assert engine._pending_council and engine._pending_handoff
        assert engine.turn_timing.summary()['outcome'] == 'interrupted'
        if fault and position == 'read':
            assert 'close fault' in ' '.join(getattr(caught.value, '__notes__', []))
        assert native.owners == native.close_owners
    finally:
        native.close_release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_empty_stream_has_failure_without_invented_usage():
    native = FiniteClient(None, body=[])
    events = await drain(backend_for(native).ask('continue'))
    assert [e.kind for e in events] == ['error']
    assert 'result' in events[0].data.lower() or 'terminal' in events[0].data.lower()
    assert_owned(native)


async def test_bounded_quoted_diagnostics_keep_raw_provider_fields():
    subtype = 'error\n"injected"\x1b[31m' + '🍍' * 150
    reason = 'aborted\n"injected"\x1b[31m' + '🍍' * 150
    native = FiniteClient(terminal(subtype=subtype, reason=reason))
    events = await drain(backend_for(native).ask('continue'))
    result = next(e.data for e in events if e.kind == 'result')
    notice = next(e.data for e in events if e.kind == 'error')
    assert result['subtype'] == subtype and result['terminal_reason'] == reason
    assert json.dumps(subtype[:120]) in notice and json.dumps(reason[:120]) in notice
    assert '\n' not in notice and '\x1b' not in notice and len(notice) < 3400


async def test_two_turns_keep_native_options_tools_and_stream_payloads(engine):
    body = [
        StreamEvent(uuid='text', session_id='native-fixture', event={'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'hello'}}),
        StreamEvent(uuid='think', session_id='native-fixture', event={'type': 'content_block_delta', 'delta': {'type': 'thinking_delta', 'thinking': 'consider'}}),
        StreamEvent(uuid='nested', session_id='native-fixture', parent_tool_use_id='child', event={'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'PRIVATE'}}),
        AssistantMessage(model='fixture-sdk', content=[ThinkingBlock(thinking='hidden', signature='sig'), TextBlock(' hello '), ToolUseBlock(id='t1', name='fixture-read', input={'path': 'x'})]),
        UserMessage(content=[ToolResultBlock(tool_use_id='t1', content=[{'type': 'text', 'text': 'seen'}, {'type': 'image', 'data': 'fixture'}], is_error=False)]),
    ]
    native = FiniteClient(terminal(), body=body)
    await configure(engine, native)
    backend = engine.backend
    options = backend._build_options()
    state = (engine.session_id, backend.model, backend._effort, backend.mcp_server,
             backend.permission_cb, backend.preapproved_tool_ids)
    for _ in range(2):
        events = await drain(engine.ask('continue'))
        kinds = [e.kind for e in events if e.kind != 'system']
        assert kinds == ['text_delta', 'thinking_delta', 'tool_use', 'assistant_done', 'tool_result', 'result']
        assert next(e.data for e in events if e.kind == 'text_delta') == 'hello'
        assert next(e.data for e in events if e.kind == 'thinking_delta') == 'consider'
        assert next(e.data for e in events if e.kind == 'assistant_done') == 'hello'
        assert next(e.data for e in events if e.kind == 'tool_use') == {'name': 'fixture-read', 'input': {'path': 'x'}, 'id': 't1'}
        assert next(e.data for e in events if e.kind == 'tool_result') == {'name': 'fixture-read', 'content': 'seen\n[image]', 'is_error': False, 'id': 't1'}
        assert backend.client is native and backend._build_options() == options
        assert state == (engine.session_id, backend.model, backend._effort, backend.mcp_server,
                         backend.permission_cb, backend.preapproved_tool_ids)
    assert engine.session_tokens == 20 and engine.total_cost_usd == 0.5
    assert native.exhaustions == 2
    assert_owned(native, 2)


class ExplicitCloseClient(FiniteClient):
    """Separate iterator exhaustion from its explicit async close contract."""
    def __init__(self, receipt, *, held=False, close_error=None, read_error=None):
        super().__init__(receipt)
        self.explicit_entered = asyncio.Event()
        self.explicit_release = asyncio.Event()
        if not held:
            self.explicit_release.set()
        self.explicit_error = close_error
        self.explicit_read_error = read_error
        self.explicit_owner = None
        self.explicit_closes = 0

    def receive_response(self):
        source = super().receive_response()
        client = self
        class Response:
            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return await anext(source)
                except StopAsyncIteration:
                    if client.explicit_read_error:
                        raise client.explicit_read_error
                    raise

            async def aclose(self):
                client.explicit_owner = asyncio.current_task()
                client.explicit_entered.set()
                try:
                    await source.aclose()
                    await client.explicit_release.wait()
                finally:
                    client.explicit_closes += 1
                    if client.explicit_error:
                        raise client.explicit_error
        return Response()


@pytest.mark.parametrize('receipt', [None, terminal(), terminal(is_error=True)])
@pytest.mark.parametrize('read_fault', [False, True])
async def test_explicit_aclose_fault_keeps_accounting_and_both_failures(engine, receipt, read_fault):
    native = ExplicitCloseClient(receipt, close_error=OSError('explicit close fault'),
        read_error=RuntimeError('separate read fault') if read_fault else None)
    await configure(engine, native)
    events = await drain(engine.ask('continue'))
    results = [e.data for e in events if e.kind == 'result']
    assert len(results) == int(receipt is not None)
    assert all(r['is_error'] for r in results)
    assert engine.session_tokens == (10 if receipt else 0)
    errors = ' '.join(str(e.data) for e in events if e.kind == 'error')
    assert 'explicit close fault' in errors
    if read_fault:
        assert 'separate read fault' in errors
    assert engine._pending_council and engine._pending_handoff
    assert native.explicit_closes == 1 and native.explicit_owner is native.owners[0]
    assert_owned(native)


@pytest.mark.parametrize('cancel', [False, True])
@pytest.mark.parametrize('fault', [False, True])
async def test_actual_engine_waits_for_explicit_aclose_and_preserves_cancellation(engine, cancel, fault):
    native = ExplicitCloseClient(terminal(), held=True,
                                 close_error=OSError('explicit close fault') if fault else None)
    await configure(engine, native)
    observed = []
    async def consume():
        async with aclosing(engine.ask('continue')) as events:
            async for event in events:
                observed.append(event)
    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(native.explicit_entered.wait(), 2)
        assert native.exhaustions == 1
        assert not any(e.kind == 'result' for e in observed)
        assert engine._pending_council and engine._pending_handoff
        if cancel:
            task.cancel('cancel during explicit close')
            with pytest.raises(asyncio.CancelledError) as caught:
                await asyncio.wait_for(task, 2)
            if fault:
                assert 'explicit close fault' in ' '.join(caught.value.__notes__)
            assert not any(e.kind in ('result', 'error') for e in observed)
        else:
            native.explicit_release.set()
            await asyncio.wait_for(task, 2)
        assert bool(engine._pending_council) is (cancel or fault)
        assert native.explicit_closes == 1 and native.explicit_owner is native.owners[0]
    finally:
        native.explicit_release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('fault', [False, True])
async def test_engine_consumer_aclose_retains_ownership_and_operational_failure(engine, fault):
    native = ExplicitCloseClient(terminal(), close_error=OSError('explicit close fault') if fault else None)
    await configure(engine, native)
    stream = engine.ask('continue')
    while (await anext(stream)).kind != 'assistant_done':
        pass
    if fault:
        with pytest.raises(OSError, match='explicit close fault'):
            await stream.aclose()
    else:
        await stream.aclose()
    assert engine._pending_council and engine._pending_handoff
    assert native.explicit_closes == 1 and native.explicit_owner is native.owners[0]
    assert_owned(native)


class ObservedCloseClient(ExplicitCloseClient):
    """Observe the actual cancellation replaced by the explicit close finalizer."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_cancellation = None

    def receive_response(self):
        response = super().receive_response()
        client = self
        class Response:
            def __aiter__(self):
                return self

            async def __anext__(self):
                return await anext(response)

            async def aclose(self):
                try:
                    await response.aclose()
                except BaseException as exc:
                    cause = exc
                    while cause is not None:
                        if isinstance(cause, asyncio.CancelledError):
                            client.current_cancellation = cause
                            break
                        cause = cause.__context__
                    raise
        return Response()


@pytest.mark.parametrize('consumer', ['adapter', 'engine'])
@pytest.mark.parametrize('history', ['none', 'cleared', 'retained'])
async def test_new_cancel_after_read_fault_survives_close_finalizer(engine, consumer, history):
    native = ObservedCloseClient(terminal(), held=True,
        read_error=RuntimeError('earlier read failure'),
        close_error=OSError('later close failure'))
    await configure(engine, native)
    observed, historical = [], []
    async def consume():
        stream = engine.ask('new request') if consumer == 'engine' else engine.backend.ask('new request')
        async with aclosing(stream):
            async for event in stream:
                observed.append(event)
    async def caller():
        if history == 'none':
            return await consume()
        task = asyncio.current_task()
        task.cancel('historical cancellation')
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError as exc:
            historical.append(exc)
            if history == 'cleared':
                task.uncancel()
            assert task.cancelling() == int(history == 'retained')
            return await consume()
    task = asyncio.create_task(caller())
    try:
        await asyncio.wait_for(native.explicit_entered.wait(), 2)
        task.cancel('current request cancellation')
        with pytest.raises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(task, 2)
        assert caught.value is native.current_cancellation
        assert not historical or caught.value is not historical[0]
        notes = ' '.join(getattr(caught.value, '__notes__', []))
        assert 'earlier read failure' in notes and 'later close failure' in notes
        assert not any(e.kind in ('result', 'error') for e in observed)
        if consumer == 'engine':
            assert engine.turn_timing.summary()['outcome'] == 'interrupted'
            assert engine.session_tokens == 0
        assert engine._pending_council and engine._pending_handoff
        assert native.explicit_closes == 1 and native.explicit_owner is native.owners[0]
        assert_owned(native)
    finally:
        native.explicit_release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('consumer', ['adapter', 'engine'])
@pytest.mark.parametrize('retain_count', [False, True])
@pytest.mark.parametrize('boundary', ['read', 'normal-finalizer', 'explicit-close'])
async def test_historical_cancel_context_is_not_a_new_request_cancellation(engine, consumer, retain_count, boundary):
    fault = RuntimeError('ordinary new request failure')
    if boundary == 'explicit-close':
        native = ExplicitCloseClient(terminal(), close_error=fault)
    else:
        native = FiniteClient(terminal(), read_error=fault if boundary == 'read' else None,
                              close_error=fault if boundary == 'normal-finalizer' else None)
    await configure(engine, native)
    historical = []
    async def recovered():
        task = asyncio.current_task()
        task.cancel('old handled cancellation')
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError as exc:
            historical.append(exc)
            if not retain_count:
                task.uncancel()
            assert task.cancelling() == int(retain_count)
            stream = engine.ask('recovered request') if consumer == 'engine' else engine.backend.ask('recovered request')
            events = await drain(stream)
            assert task.cancelling() == int(retain_count)
            return events
    task = asyncio.create_task(recovered())
    outcome = (await asyncio.gather(task, return_exceptions=True))[0]
    assert isinstance(outcome, list), repr(outcome)
    assert not task.cancelled()
    assert not getattr(historical[0], '__notes__', [])
    results = [e.data for e in outcome if e.kind == 'result']
    errors = [e.data for e in outcome if e.kind == 'error']
    assert len(results) == 1 and results[0]['is_error']
    assert len(errors) == 1 and 'ordinary new request failure' in errors[0]
    assert results[0]['usage'] == {'input_tokens': 7, 'output_tokens': 3}
    if consumer == 'engine':
        assert engine.turn_timing.summary()['outcome'] == 'error'
        assert engine.session_tokens == 10 and engine.total_cost_usd == 0.25
    assert engine._pending_council and engine._pending_handoff
    assert_owned(native)


@pytest.mark.parametrize('consumer', ['adapter', 'engine'])
@pytest.mark.parametrize('boundary', ['read', 'normal-finalizer', 'explicit-close'])
async def test_direct_cancelled_error_keeps_identity_despite_retained_history(engine, consumer, boundary):
    current = asyncio.CancelledError('direct current cancellation')
    if boundary == 'explicit-close':
        native = ExplicitCloseClient(terminal(), close_error=current)
    else:
        native = FiniteClient(terminal(), read_error=current if boundary == 'read' else None,
                              close_error=current if boundary == 'normal-finalizer' else None)
    await configure(engine, native)
    observed = []
    async def recovered():
        task = asyncio.current_task()
        task.cancel('historical retained cancellation')
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            assert task.cancelling() == 1
            stream = engine.ask('direct cancellation') if consumer == 'engine' else engine.backend.ask('direct cancellation')
            try:
                async with aclosing(stream):
                    async for event in stream:
                        observed.append(event)
            except BaseException as exc:
                return exc
    outcome = await asyncio.create_task(recovered())
    assert outcome is current
    assert not any(e.kind in ('result', 'error') for e in observed)
    if consumer == 'engine':
        assert engine.turn_timing.summary()['outcome'] == 'interrupted'
    assert_owned(native)


@pytest.mark.parametrize('cancel', [False, True])
async def test_secondary_boundary_diagnostics_survive_long_primary_failure(engine, cancel):
    native = ObservedCloseClient(terminal(), held=cancel,
        read_error=RuntimeError('READ_BOUNDARY "\n\x1b' + '🍍' * 600),
        close_error=OSError('CLOSE_BOUNDARY "\n\x1b' + '🍍' * 600))
    await configure(engine, native)
    task = asyncio.create_task(drain(engine.ask('bounded failure diagnostics')))
    try:
        if cancel:
            await asyncio.wait_for(native.explicit_entered.wait(), 2)
            task.cancel('current cancellation')
            with pytest.raises(asyncio.CancelledError) as caught:
                await asyncio.wait_for(task, 2)
            assert caught.value is native.current_cancellation
            diagnostic = ' '.join(caught.value.__notes__)
        else:
            events = await asyncio.wait_for(task, 2)
            diagnostic = next(e.data for e in events if e.kind == 'error')
            assert engine.session_tokens == 10 and engine.total_cost_usd == 0.25
        assert 'READ_BOUNDARY' in diagnostic and 'CLOSE_BOUNDARY' in diagnostic
        assert 'RuntimeError' in diagnostic and 'OSError' in diagnostic
        assert '\n' not in diagnostic and '\x1b' not in diagnostic
        assert len(diagnostic) < 6500
        assert engine._pending_council and engine._pending_handoff
        assert_owned(native)
    finally:
        native.explicit_release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
