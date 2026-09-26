"""SDK Council receipts through real consumers, with finite local dependencies."""
import asyncio
import json
from types import SimpleNamespace

import anyio
import claude_agent_sdk as sdk
import pytest

from dream.core import moe
from dream.core.profiles import resolve_profile
from dream.core.providers import get_provider
from dream.telemetry.runtime import RunMeter
from dream.tools import context
from test_council_handoff import engine


@pytest.fixture(autouse=True)
def block_external_operations(monkeypatch):
    import socket
    import subprocess
    from dream.memory.embeddings import Embedder, Reranker
    attempts = []
    def blocked(*args, **kwargs):
        attempts.append('unexpected external operation or model load')
        raise AssertionError(attempts[-1])
    for obj, name in ((socket.socket, 'connect'), (socket.socket, 'connect_ex'),
                      (subprocess.Popen, '__init__'), (Embedder, '_ensure'), (Reranker, '_ensure')):
        monkeypatch.setattr(obj, name, blocked)
    yield
    assert attempts == []


def result(subtype='success', *, reason=None, error=False, usage=None):
    return sdk.ResultMessage(subtype=subtype, duration_ms=1, duration_api_ms=1,
                             is_error=error, num_turns=1, session_id='fixture-sdk',
                             terminal_reason=reason, usage=usage)


def assistant(text='PARTIAL_FIXTURE DONE PASS'):
    return sdk.AssistantMessage(content=[sdk.TextBlock(text=text)], model='fixture-sdk')


def install_query(monkeypatch, messages):
    observed = {'queries': 0, 'closed': False, 'messages': []}
    async def query(*, prompt, options):
        observed['queries'] += 1
        observed['options'] = options
        observed['prompts'] = [message async for message in prompt]
        owner = asyncio.current_task()
        with anyio.CancelScope():
            try:
                for message in messages:
                    observed['messages'].append(message)
                    yield message
            finally:
                assert asyncio.current_task() is owner
                observed['closed'] = True
    monkeypatch.setattr(sdk, 'query', query)
    return observed


@pytest.mark.parametrize('messages,cause', [
    ([assistant()], 'missing'),
    ([], 'missing'),
    ([assistant(), result('MaxTurns')], 'subtype'),
    ([assistant(), result('error_during_execution')], 'subtype'),
    ([assistant(), result('')], 'subtype'),
    ([assistant(), result(None)], 'subtype'),
    ([assistant(), result(reason='max_turns')], 'terminal_reason'),
    ([assistant(), result(reason='aborted_streaming')], 'terminal_reason'),
    ([assistant(), result(reason='aborted_tools')], 'terminal_reason'),
    ([assistant(), result(reason='')], 'terminal_reason'),
    ([assistant(), result(reason=False)], 'terminal_reason'),
    ([assistant(), result(error=True)], 'failed'),
    ([assistant(), result(), result()], 'duplicate'),
    ([assistant(), result('MaxTurns'), result()], 'subtype'),
    ([assistant(), result(reason='max_turns'), result()], 'terminal_reason'),
    ([assistant(), result(error=True), result()], 'failed'),
    ([assistant(), result(), result(error=True)], 'duplicate'),
])
async def test_actual_council_rejects_missing_or_contradictory_receipt(monkeypatch, tmp_path, messages, cause):
    observed = install_query(monkeypatch, messages)
    rows = await moe.council(['anthropic'], 'question', 'shared background', cwd=str(tmp_path),
                             models={'anthropic': 'fixture-sdk'}, efforts={'anthropic': 'high'})
    row, = rows
    assert 'unavailable' in row['answer'] and cause in row['answer'].lower()
    assert row['answer'] != 'PARTIAL_FIXTURE DONE PASS'
    assert row == dict(advisor='anthropic', label=get_provider('anthropic').label,
                      model='fixture-sdk', effort='high', answer=row['answer'],
                      independent=True, verification='unverified', consensus_is_proof=False,
                      isolation=row['isolation'])
    # DREAM-137: a Claude row carries its run provenance, as CLI rows do.
    assert row['isolation']['isolation'] == 'none: runs like the main-model Claude path'
    assert observed['closed'] and observed['queries'] == 1
    assert observed['messages'] == messages
    assert observed['prompts'][0]['message']['content'] == 'shared background\n\nquestion'


@pytest.mark.parametrize('reason', ['absent', None, 'completed'])
@pytest.mark.parametrize('text', ['  Complete advice  ', ''])
async def test_success_receipt_and_older_optional_reason_keep_existing_answer_contract(monkeypatch, reason, text):
    terminal = result(reason=reason)
    if reason == 'absent':
        del terminal.terminal_reason
    install_query(monkeypatch, [assistant(text), terminal])
    answer = await moe.consult_advisor('anthropic', 'q')
    assert answer == ('Complete advice' if text else '[unavailable — advisor returned no answer]')


@pytest.mark.parametrize('field', ['subtype', 'terminal_reason'])
async def test_provider_terminal_diagnostic_is_bounded_and_quoted(monkeypatch, field):
    value = 'bad"\n\x1b[31m' + '\U0001f34d' * 5000
    terminal = result()
    setattr(terminal, field, value)
    install_query(monkeypatch, [assistant(), terminal])
    answer = await moe.consult_advisor('anthropic', 'q')
    assert 'unavailable' in answer and field in answer
    assert len(answer) < 1200
    assert '\n' not in answer and '\x1b' not in answer
    assert '\\"' in answer and '\\n' in answer and '\\u001b' in answer


@pytest.mark.parametrize('first', ['success', 'subtype', 'reason', 'error', 'none', 'invalid_usage'])
@pytest.mark.parametrize('duplicate', [False, True])
async def test_only_first_receipt_reports_usage_to_own_parent(monkeypatch, first, duplicate):
    usage = {'input_tokens': 6, 'output_tokens': 2, 'cache_read_input_tokens': 4}
    terminal = result('MaxTurns' if first == 'subtype' else 'success',
                      reason='max_turns' if first == 'reason' else None,
                      error=first == 'error', usage='bad' if first == 'invalid_usage' else usage)
    messages = [assistant()] + ([] if first == 'none' else [terminal])
    if duplicate and first != 'none':
        messages.append(result(usage={'input_tokens': 1000, 'output_tokens': 500}))
    observed = install_query(monkeypatch, messages)
    parent = RunMeter('owner', 1, resolve_profile(get_provider('machx')))
    wrong = RunMeter('wrong-owner', 1, parent.profile)
    monkeypatch.setattr(context, '_CTX', SimpleNamespace(runtime_meter=wrong))
    with context.bind_context(SimpleNamespace(runtime_meter=parent)):
        answer = await moe.consult_advisor('anthropic', 'q')
    accounted = first not in ('none', 'invalid_usage')
    assert (parent.prompt_tokens, parent.output_tokens, parent.cached_tokens) == ((10, 2, 4) if accounted else (0, 0, 0))
    assert parent.phases == ({'council:anthropic:lead': {'prompt_tokens': 10, 'output_tokens': 2,
                                                     'requests': 1}} if accounted else {})
    assert wrong.prompt_tokens == wrong.output_tokens == 0 and wrong.phases == {}
    assert observed['queries'] == 1 and observed['closed']
    if first in ('none', 'subtype', 'reason', 'error') or duplicate:
        assert 'unavailable' in answer


class OwnedStream:
    """A query boundary whose real AnyIO scope stays open until explicit close."""
    def __init__(self, messages, *, hold_read=False, hold_close=False, read_fault=False, close_fault=False):
        self.messages = iter(messages)
        self.hold_read, self.hold_close = hold_read, hold_close
        self.read_fault, self.close_fault = read_fault, close_fault
        self.reading, self.closing = asyncio.Event(), asyncio.Event()
        self.release_read, self.release_close = asyncio.Event(), asyncio.Event()
        self.closed = False
        self.owner = self.scope = None

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.owner is None:
            self.owner = asyncio.current_task()
            self.scope = anyio.CancelScope()
            self.scope.__enter__()
        assert asyncio.current_task() is self.owner
        try:
            return next(self.messages)
        except StopIteration:
            self.reading.set()
            if self.hold_read:
                await self.release_read.wait()
            if self.read_fault:
                raise OSError('fixture read fault')
            raise StopAsyncIteration

    async def aclose(self):
        assert asyncio.current_task() is self.owner
        self.closing.set()
        try:
            if self.hold_close:
                await self.release_close.wait()
            if self.close_fault:
                raise OSError('fixture close fault')
        finally:
            self.scope.__exit__(None, None, None)
            self.closed = True


@pytest.mark.parametrize('error', [False, True])
@pytest.mark.parametrize('held', ['read', 'close'])
async def test_result_waits_for_eof_and_owned_closure(monkeypatch, error, held):
    stream = OwnedStream([assistant('complete text'), result(error=error)],
                         hold_read=held == 'read', hold_close=held == 'close')
    monkeypatch.setattr(sdk, 'query', lambda **kwargs: stream)
    task = asyncio.create_task(moe.consult_advisor('anthropic', 'q', timeout=2))
    try:
        async with asyncio.timeout(1):
            await (stream.reading if held == 'read' else stream.closing).wait()
            assert not task.done() and not stream.closed
            stream.release_read.set()
            stream.release_close.set()
            answer = await task
        assert stream.closed
        assert ('unavailable' in answer) if error else answer == 'complete text'
    finally:
        stream.release_read.set()
        stream.release_close.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('fault', ['read', 'close'])
@pytest.mark.parametrize('error', [False, True])
async def test_terminal_never_hides_read_or_close_fault(monkeypatch, fault, error):
    stream = OwnedStream([assistant(), result(error=error)],
                         read_fault=fault == 'read', close_fault=fault == 'close')
    monkeypatch.setattr(sdk, 'query', lambda **kwargs: stream)
    answer = await moe.consult_advisor('anthropic', 'q')
    assert 'unavailable' in answer and f'fixture {fault} fault' in answer
    assert stream.closed


@pytest.mark.parametrize('ending', ['missing', 'success', 'error'])
async def test_cancellation_propagates_after_owned_cleanup(monkeypatch, ending):
    messages = [assistant()] + ([] if ending == 'missing' else [result(error=ending == 'error')])
    stream = OwnedStream(messages, hold_read=True, hold_close=True)
    monkeypatch.setattr(sdk, 'query', lambda **kwargs: stream)
    task = asyncio.create_task(moe.council(['anthropic'], 'q', timeout=2))
    try:
        async with asyncio.timeout(1):
            await stream.reading.wait()
            task.cancel()
            await stream.closing.wait()
            assert not task.done() and not stream.closed
            stream.release_close.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert stream.closed
    finally:
        stream.release_read.set()
        stream.release_close.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_existing_five_second_close_deadline_remains_bounded(monkeypatch):
    stream = OwnedStream([assistant(), result()], hold_close=True)
    monkeypatch.setattr(sdk, 'query', lambda **kwargs: stream)
    async with asyncio.timeout(7):
        answer = await moe.consult_advisor('anthropic', 'q', timeout=6)
    assert stream.closed and stream.closing.is_set()
    assert 'unavailable' in answer and 'TimeoutError' in answer


@pytest.mark.parametrize('ending', ['missing', 'subtype', 'reason', 'error', 'timeout', 'success'])
async def test_actual_app_and_engine_retain_advisor_outcome_and_healthy_sibling(
        monkeypatch, tmp_path, engine, ending):
    from rich.console import Console
    from dream.core.backends.openai_compat import OpenAICompatBackend
    from dream.gui.bus import EventBus
    from dream.tui.app import App
    from test_compaction import _FakeClient, _text_round

    options_seen, prompts_seen, streams = [], [], []
    def query(*, prompt, options):
        options_seen.append(options)
        async def consume():
            prompts_seen.extend([m['message']['content'] async for m in prompt])
            messages = [assistant('SDK advice')]
            if ending != 'missing':
                messages.append(result('MaxTurns' if ending == 'subtype' else 'success',
                                       reason='max_turns' if ending == 'reason' else None,
                                       error=ending == 'error'))
            stream = OwnedStream(messages, hold_read=ending == 'timeout')
            streams.append(stream)
            try:
                async for message in stream:
                    yield message
            finally:
                await stream.aclose()
        return consume()
    monkeypatch.setattr(sdk, 'query', query)
    clients = []
    class Client(_FakeClient):
        closed = False
        async def aclose(self):
            self.closed = True
    async def connect(backend):
        backend._client = Client([_text_round('Healthy independent dissent')])
        clients.append(backend._client)
    monkeypatch.setattr(OpenAICompatBackend, 'connect', connect)
    engine.working.log_turn('user', 'Original shared question context')
    engine._moe = moe.MoeConfig('machx', ['anthropic', 'openai'], timeout_seconds=.05 if ending == 'timeout' else 2,
                                advisor_models={'anthropic': 'fixture-sdk', 'openai': 'healthy-fixture'},
                                advisor_efforts={'anthropic': 'high', 'openai': 'medium'})
    app = App.__new__(App)
    app.engine, app.workspace, app.mode = engine, tmp_path, 'ask'
    app.bus = EventBus()
    app.renderer = SimpleNamespace(console=Console(record=True, width=240))
    async with asyncio.timeout(3):
        rows = (await app._consult_council('Review this'))['results']
    sdk_row, sibling = rows
    assert sdk_row['advisor'] == 'anthropic' and sdk_row['model'] == 'fixture-sdk' and sdk_row['effort'] == 'high'
    if ending == 'success':
        assert sdk_row['answer'] == 'SDK advice'
    else:
        assert 'unavailable' in sdk_row['answer']
        assert ('TimeoutError' if ending == 'timeout' else 'terminal_reason' if ending == 'reason' else ending if ending != 'error' else 'failed') in sdk_row['answer']
    assert sibling['answer'] == 'Healthy independent dissent'
    assert sibling['advisor'] == 'openai' and sibling['model'] == 'healthy-fixture' and sibling['effort'] == 'medium'
    assert all(row['independent'] and row['verification'] == 'unverified' and not row['consensus_is_proof'] for row in rows)
    assert len(options_seen) == len(streams) == len(clients) == 1 and streams[0].closed
    options = options_seen[0]
    assert options.model == 'fixture-sdk' and options.effort == 'high' and options.cwd == str(tmp_path)
    # DREAM-137: the main Claude path's options (was: no tools, one turn); ask mode stays read-only.
    assert options.setting_sources == [] and options.strict_mcp_config and options.max_turns is None
    assert json.loads(options.settings) == {'disableAllHooks': True}
    assert options.permission_mode == 'default'
    assert (await options.can_use_tool('Bash', {'command': 'forbidden'}, None)).behavior == 'deny'
    assert (await options.can_use_tool('Write', {'file_path': str(tmp_path / 'x')}, None)).behavior == 'deny'
    assert clients[0].closed
    payload, = clients[0].payloads
    assert payload['model'] == 'healthy-fixture' and payload['reasoning_effort'] == 'medium'
    http_prompt = payload['messages'][-1]['content']
    assert len(prompts_seen) == 1
    assert http_prompt == prompts_seen[0] + '\n\n[id:m0001]'
    assert 'Original shared question context' in http_prompt and 'Review this' in http_prompt
    assert 'SDK advice' not in http_prompt and 'Healthy independent dissent' not in http_prompt
    saved, = engine.store._conn.execute("SELECT id, content FROM turns WHERE role='council'").fetchall()
    assert json.loads(saved['content']) == {'question': 'Review this', 'advisors': rows}
    pending, = engine._pending_council
    assert pending.turn == saved['id'] and pending.session == engine.session_id and pending.content == saved['content']
    displayed = app.renderer.console.export_text()
    bus = str(app.bus.conversation.snapshot())
    for row in rows:
        assert row['answer'] in displayed and row['answer'] in bus
    assert engine.model == 'original-model' and engine.provider.key == 'machx'


class CancellationRecordingStream(OwnedStream):
    """Retain the actual read cancellation for identity and cleanup assertions."""
    cancellation = None

    async def __anext__(self):
        try:
            return await super().__anext__()
        except asyncio.CancelledError as exc:
            self.cancellation = exc
            raise


@pytest.mark.parametrize('entry', ['direct', 'public'])
@pytest.mark.parametrize('ending', ['missing', 'success', 'error'])
@pytest.mark.parametrize('close_fault', [False, True])
async def test_read_cancellation_survives_owned_close_fault(monkeypatch, entry, ending, close_fault):
    messages = [assistant()] + ([] if ending == 'missing' else [result(error=ending == 'error')])
    stream = CancellationRecordingStream(messages, hold_read=True, close_fault=close_fault)
    monkeypatch.setattr(sdk, 'query', lambda **kwargs: stream)
    call = (moe._consult_anthropic(get_provider('anthropic'), 'q') if entry == 'direct'
            else moe.consult_advisor('anthropic', 'q', timeout=2))
    task = asyncio.create_task(call)
    try:
        async with asyncio.timeout(3):
            await stream.reading.wait()
            task.cancel('original caller cancellation')
            with pytest.raises(asyncio.CancelledError) as canceled:
                await task
        assert canceled.value is stream.cancellation
        assert str(canceled.value) == 'original caller cancellation'
        assert stream.closed and stream.closing.is_set()
        notes = getattr(canceled.value, '__notes__', [])
        if close_fault:
            assert len(notes) == 1 and 'OSError' in notes[0] and 'fixture close fault' in notes[0]
        else:
            assert notes == []
    finally:
        stream.release_read.set()
        stream.release_close.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_read_cancellation_survives_actual_five_second_close_deadline(monkeypatch):
    stream = CancellationRecordingStream([assistant(), result()], hold_read=True, hold_close=True)
    monkeypatch.setattr(sdk, 'query', lambda **kwargs: stream)
    task = asyncio.create_task(moe.consult_advisor('anthropic', 'q', timeout=10))
    try:
        async with asyncio.timeout(7):
            await stream.reading.wait()
            task.cancel('cancel before bounded cleanup')
            with pytest.raises(asyncio.CancelledError) as canceled:
                await task
        assert canceled.value is stream.cancellation
        assert str(canceled.value) == 'cancel before bounded cleanup'
        assert stream.closed
        notes = getattr(canceled.value, '__notes__', [])
        assert len(notes) == 1 and 'TimeoutError' in notes[0]
    finally:
        stream.release_read.set()
        stream.release_close.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_second_cancel_during_close_preserves_original_read_cancellation(monkeypatch):
    stream = CancellationRecordingStream([assistant(), result()], hold_read=True, hold_close=True)
    monkeypatch.setattr(sdk, 'query', lambda **kwargs: stream)
    task = asyncio.create_task(moe.consult_advisor('anthropic', 'q', timeout=2))
    try:
        async with asyncio.timeout(3):
            await stream.reading.wait()
            task.cancel('first cancellation')
            await stream.closing.wait()
            task.cancel('second cancellation')
            with pytest.raises(asyncio.CancelledError) as canceled:
                await task
        assert canceled.value is stream.cancellation
        assert str(canceled.value) == 'first cancellation'
        assert stream.closed
        notes = getattr(canceled.value, '__notes__', [])
        assert len(notes) == 1 and 'CancelledError' in notes[0] and 'second cancellation' in notes[0]
    finally:
        stream.release_read.set()
        stream.release_close.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_secondary_close_failure_note_is_bounded_and_quoted(monkeypatch):
    hostile = 'bad"\n\x1b[31m' + '\U0001f34d' * 5000
    class Stream(CancellationRecordingStream):
        async def aclose(self):
            await super().aclose()
            raise OSError(hostile)
    stream = Stream([assistant(), result()], hold_read=True)
    monkeypatch.setattr(sdk, 'query', lambda **kwargs: stream)
    task = asyncio.create_task(moe.consult_advisor('anthropic', 'q', timeout=2))
    try:
        async with asyncio.timeout(3):
            await stream.reading.wait()
            task.cancel('original cancellation')
            with pytest.raises(asyncio.CancelledError) as canceled:
                await task
        assert canceled.value is stream.cancellation and stream.closed
        note, = canceled.value.__notes__
        assert len(note) < 2200 and 'OSError' in note
        assert '\n' not in note and '\x1b' not in note
        assert '\\"' in note and '\\n' in note and '\\u001b' in note
    finally:
        stream.release_read.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class CloseCancellationStream(OwnedStream):
    """Record cancellation first delivered in close, before a finalizer fault."""
    cancellation = None

    async def aclose(self):
        assert asyncio.current_task() is self.owner
        self.closing.set()
        try:
            await self.release_close.wait()
        except asyncio.CancelledError as exc:
            self.cancellation = exc
            raise
        finally:
            await super().aclose()


@pytest.mark.parametrize('entry', ['direct', 'public'])
@pytest.mark.parametrize('ending', ['missing', 'success', 'error'])
@pytest.mark.parametrize('close_fault', [False, True])
async def test_close_entry_cancellation_survives_finalizer_fault(monkeypatch, entry, ending, close_fault):
    messages = [assistant()] + ([] if ending == 'missing' else [result(error=ending == 'error')])
    stream = CloseCancellationStream(messages, close_fault=close_fault)
    monkeypatch.setattr(sdk, 'query', lambda **kwargs: stream)
    call = (moe._consult_anthropic(get_provider('anthropic'), 'q') if entry == 'direct'
            else moe.consult_advisor('anthropic', 'q', timeout=2))
    task = asyncio.create_task(call)
    try:
        async with asyncio.timeout(3):
            await stream.closing.wait()
            task.cancel('first delivered in close')
            with pytest.raises(asyncio.CancelledError) as canceled:
                await task
        assert canceled.value is stream.cancellation and stream.closed
        assert str(canceled.value) == 'first delivered in close'
        notes = getattr(canceled.value, '__notes__', [])
        if close_fault:
            assert len(notes) == 1 and 'OSError' in notes[0] and 'fixture close fault' in notes[0]
        else:
            assert notes == []
    finally:
        stream.release_close.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('already_handled', [False, True])
@pytest.mark.parametrize('wrapped', [False, True])
async def test_historical_close_cancellation_context_is_not_active(monkeypatch, already_handled, wrapped):
    historical = asyncio.CancelledError('unrelated old cancellation')
    middle = ValueError('old intermediary')
    middle.__context__ = historical
    failure = OSError('ordinary close failure')
    failure.__context__ = middle if wrapped else historical
    class Stream(OwnedStream):
        async def aclose(self):
            await super().aclose()
            raise failure
    stream = Stream([assistant(), result()])
    monkeypatch.setattr(sdk, 'query', lambda **kwargs: stream)
    async def invoke():
        if already_handled:
            asyncio.current_task().cancel('previously handled task cancellation')
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass
            assert asyncio.current_task().cancelling() == 1
        return await moe.consult_advisor('anthropic', 'q', timeout=2)
    async with asyncio.timeout(3):
        answer = await asyncio.create_task(invoke())
    assert 'unavailable' in answer and 'ordinary close failure' in answer
    assert stream.closed and not getattr(historical, '__notes__', [])


async def test_close_entry_cancellation_survives_wrapped_hostile_finalizer_fault(monkeypatch):
    hostile = 'bad"\n\x1b[31m' + '\U0001f34d' * 5000
    class Stream(CloseCancellationStream):
        async def aclose(self):
            try:
                await super().aclose()
            finally:
                raise RuntimeError(hostile)
    stream = Stream([assistant(), result()], close_fault=True)
    monkeypatch.setattr(sdk, 'query', lambda **kwargs: stream)
    task = asyncio.create_task(moe.consult_advisor('anthropic', 'q', timeout=2))
    try:
        async with asyncio.timeout(3):
            await stream.closing.wait()
            task.cancel('close cancellation through nested errors')
            with pytest.raises(asyncio.CancelledError) as canceled:
                await task
        assert canceled.value is stream.cancellation and stream.closed
        note, = canceled.value.__notes__
        assert len(note) < 2200 and 'RuntimeError' in note
        assert '\n' not in note and '\x1b' not in note
        assert '\\"' in note and '\\n' in note and '\\u001b' in note
    finally:
        stream.release_close.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_close_entry_context_cycle_without_cancellation_evidence_is_finite(monkeypatch):
    first, second = OSError('cyclic close failure'), ValueError('cycle intermediary')
    first.__context__, second.__context__ = second, first
    class Stream(OwnedStream):
        async def aclose(self):
            self.closing.set()
            try:
                await self.release_close.wait()
            except asyncio.CancelledError:
                # This fixture removes the delivered cancellation from the
                # error context. The collector must not invent its identity.
                pass
            await super().aclose()
            raise first
    stream = Stream([assistant(), result()])
    monkeypatch.setattr(sdk, 'query', lambda **kwargs: stream)
    task = asyncio.create_task(moe.consult_advisor('anthropic', 'q', timeout=2))
    try:
        async with asyncio.timeout(3):
            await stream.closing.wait()
            task.cancel('not retained in exception context')
            answer = await task
        assert stream.closed and 'unavailable' in answer and 'cyclic close failure' in answer
    finally:
        stream.release_close.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('entry', ['direct', 'public'])
@pytest.mark.parametrize('ending', ['missing', 'success', 'error'])
async def test_query_generator_finalizer_preserves_read_cancellation(monkeypatch, entry, ending):
    reached = asyncio.Event()
    canceled, closed = [], []
    async def query(**kwargs):
        owner = asyncio.current_task()
        with anyio.CancelScope(), anyio.CancelScope():
            try:
                yield assistant()
                if ending != 'missing':
                    yield result(error=ending == 'error')
                reached.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError as exc:
                    canceled.append(exc)
                    raise
            finally:
                assert asyncio.current_task() is owner
                closed.append(True)
                raise OSError('query finalizer failed during read cancellation')
    monkeypatch.setattr(sdk, 'query', query)
    call = (moe._consult_anthropic(get_provider('anthropic'), 'q') if entry == 'direct'
            else moe.consult_advisor('anthropic', 'q', timeout=2))
    task = asyncio.create_task(call)
    try:
        async with asyncio.timeout(3):
            await reached.wait()
            task.cancel('original query read cancellation')
            with pytest.raises(asyncio.CancelledError) as observed:
                await task
        assert canceled == [observed.value] and closed == [True]
        assert str(observed.value) == 'original query read cancellation'
        note, = observed.value.__notes__
        assert 'OSError' in note and 'query finalizer failed' in note
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_query_generator_hostile_finalizer_note_is_bounded(monkeypatch):
    reached = asyncio.Event()
    canceled, closed = [], []
    hostile = 'bad"\n\x1b[31m' + '\U0001f34d' * 5000
    async def query(**kwargs):
        with anyio.CancelScope():
            try:
                yield assistant()
                yield result()
                reached.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError as exc:
                    canceled.append(exc)
                    raise
            finally:
                closed.append(True)
                raise OSError(hostile)
    monkeypatch.setattr(sdk, 'query', query)
    task = asyncio.create_task(moe.consult_advisor('anthropic', 'q', timeout=2))
    try:
        async with asyncio.timeout(3):
            await reached.wait()
            task.cancel('read cancellation with hostile finalizer')
            with pytest.raises(asyncio.CancelledError) as observed:
                await task
        assert canceled == [observed.value] and closed == [True]
        note, = observed.value.__notes__
        assert len(note) < 2200 and 'OSError' in note
        assert '\n' not in note and '\x1b' not in note
        assert '\\"' in note and '\\n' in note and '\\u001b' in note
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('already_handled', [False, True])
async def test_query_generator_historical_context_is_not_active(monkeypatch, already_handled):
    historical = asyncio.CancelledError('old query cancellation')
    failure = OSError('ordinary query finalizer fault')
    failure.__context__ = historical
    async def query(**kwargs):
        with anyio.CancelScope():
            yield assistant()
            yield result()
        raise failure
    monkeypatch.setattr(sdk, 'query', query)
    async def invoke():
        if already_handled:
            asyncio.current_task().cancel('already handled before query')
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass
            assert asyncio.current_task().cancelling() == 1
        return await moe.consult_advisor('anthropic', 'q', timeout=2)
    async with asyncio.timeout(3):
        answer = await asyncio.create_task(invoke())
    assert 'unavailable' in answer and 'ordinary query finalizer fault' in answer
    assert not getattr(historical, '__notes__', [])


async def test_query_generator_cyclic_context_without_identity_is_finite(monkeypatch):
    reached = asyncio.Event()
    first, second = OSError('cyclic query error'), ValueError('query cycle')
    first.__context__, second.__context__ = second, first
    async def query(**kwargs):
        with anyio.CancelScope():
            yield assistant()
            yield result()
            reached.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                pass
        raise first
    monkeypatch.setattr(sdk, 'query', query)
    task = asyncio.create_task(moe.consult_advisor('anthropic', 'q', timeout=2))
    try:
        async with asyncio.timeout(3):
            await reached.wait()
            task.cancel('identity deliberately absent from context')
            answer = await task
        assert 'unavailable' in answer and 'cyclic query error' in answer
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
