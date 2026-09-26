"""SDK assessment receipts through real collectors and durable consumers."""
import asyncio
import hashlib
import json
import os
import socket
import subprocess
from types import SimpleNamespace

import anyio
import claude_agent_sdk as sdk
import pytest

from dream.core.backends.base import Event
from dream.core.engine import Engine
from dream.core.evaluator import ReviewSettings, SDKReviewBackend, ScopedReader, collect_review
from dream.core.loop import AutonomousLoop
from dream.core.profiles import resolve_profile
from dream.core.providers import get_provider
from dream.memory.embeddings import Embedder, Reranker
from dream.telemetry.runtime import RunMeter
from dream.tools import context
from test_council_handoff import engine
from test_durable_autonomy import Worker


@pytest.fixture(autouse=True)
def deny_external_operations(monkeypatch):
    attempts = []
    def denied(*args, **kwargs):
        attempts.append('external operation, model load or Engine.start')
        raise AssertionError(attempts[-1])
    for obj, name in ((socket.socket, 'connect'), (socket.socket, 'connect_ex'),
                      (subprocess.Popen, '__init__'), (os, 'system'),
                      (asyncio, 'create_subprocess_exec'), (asyncio, 'create_subprocess_shell'),
                      (Embedder, '_ensure'), (Reranker, '_ensure'), (Engine, 'start')):
        monkeypatch.setattr(obj, name, denied)
    for name in ('DREAM_EVALUATOR_PROVIDER', 'DREAM_EVALUATOR_MODEL', 'DREAM_EVALUATOR_TIMEOUT'):
        monkeypatch.delenv(name, raising=False)
    yield
    assert attempts == []


def terminal(subtype='success', *, reason=None, error=False, usage=None):
    return sdk.ResultMessage(subtype=subtype, duration_ms=1, duration_api_ms=1,
                             is_error=error, num_turns=1, session_id='fixture-review',
                             terminal_reason=reason, usage=usage, total_cost_usd=0.25)


def assistant(text='VERDICT: PASS\nGAPS: none'):
    return sdk.AssistantMessage(content=[sdk.TextBlock(text=text)], model='fixture-sdk')


class Query:
    """One finite query with real AnyIO scope ownership and explicit close."""
    def __init__(self, messages, *, read_error=None, close_error=None, hold=None):
        self.messages = list(messages)
        self.read_error, self.close_error, self.hold = read_error, close_error, hold
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = self.closes = 0
        self.exhausted = False

    def __call__(self, *, prompt, options):
        self.calls += 1
        self.prompt, self.options = prompt, options
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not hasattr(self, 'owner'):
            self.owner = asyncio.current_task()
            self.scope = anyio.CancelScope()
            self.scope.__enter__()
            self.users = [message async for message in self.prompt]
        if self.messages:
            return self.messages.pop(0)
        if self.hold == 'read':
            self.entered.set()
            await self.release.wait()
        if self.read_error:
            raise self.read_error
        self.exhausted = True
        raise StopAsyncIteration

    async def aclose(self):
        self.closes += 1
        self.close_owner = asyncio.current_task()
        assert self.close_owner is self.owner
        try:
            if self.hold == 'close':
                self.entered.set()
                try:
                    await self.release.wait()
                finally:
                    if self.close_error:
                        raise self.close_error
            elif self.close_error:
                raise self.close_error
        finally:
            self.scope.__exit__(None, None, None)


def backend(tmp_path, query, *, meter=None, tools=()):
    return SDKReviewBackend(ReviewSettings(get_provider('anthropic'), 'fixture-sdk', runtime_meter=meter),
                            list(tools), 'fixture review system', tmp_path, query)


BAD = [
    pytest.param([], 'missing', id='missing'),
    pytest.param([terminal('MaxTurns')], 'subtype', id='incomplete-subtype'),
    pytest.param([terminal('')], 'subtype', id='empty-subtype'),
    pytest.param([terminal(None)], 'subtype', id='null-subtype'),
    pytest.param([terminal(reason='max_turns')], 'terminal_reason', id='max-turns'),
    pytest.param([terminal(reason='aborted_streaming')], 'terminal_reason', id='aborted-streaming'),
    pytest.param([terminal(reason='aborted_tools')], 'terminal_reason', id='aborted-tools'),
    pytest.param([terminal(reason='')], 'terminal_reason', id='empty-reason'),
    pytest.param([terminal(reason=False)], 'terminal_reason', id='false-reason'),
    pytest.param([terminal(error=True)], 'failed', id='true-error'),
    pytest.param([terminal(), terminal()], 'duplicate', id='duplicate-success'),
    pytest.param([terminal('MaxTurns'), terminal()], 'subtype', id='late-success'),
    pytest.param([terminal(), terminal(error=True)], 'duplicate', id='late-error'),
]


@pytest.mark.parametrize('receipts,cause', BAD)
async def test_collect_review_rejects_partial_pass(tmp_path, receipts, cause):
    query = Query([assistant(), *receipts])
    with pytest.raises(RuntimeError, match=cause):
        await collect_review(backend(tmp_path, query), 'fixture question', 1)
    assert query.calls == query.closes == 1 and query.exhausted


async def test_empty_stream_requires_terminal(tmp_path):
    query = Query([])
    with pytest.raises(RuntimeError, match='missing'):
        await collect_review(backend(tmp_path, query), 'q', 1)
    assert query.exhausted and query.closes == 1


@pytest.mark.parametrize('reason', ['absent', None, 'completed'])
@pytest.mark.parametrize('text', ['VERDICT: PASS\nGAPS: none', ''])
async def test_completed_receipt_preserves_assessment_contract(tmp_path, reason, text):
    receipt = terminal(reason=reason)
    if reason == 'absent':
        del receipt.terminal_reason
    query = Query([assistant(text), receipt])
    review = backend(tmp_path, query)
    if text:
        assert await collect_review(review, 'q', 1) == text
    else:
        with pytest.raises(ValueError, match='no assessment'):
            await collect_review(review, 'q', 1)
    assert query.calls == query.closes == 1 and query.exhausted


@pytest.mark.parametrize('field', ['subtype', 'terminal_reason'])
async def test_provider_diagnostics_are_bounded_and_quoted(tmp_path, field):
    receipt = terminal()
    value = 'bad"\n\x1b' + '\U0001f34d' * 5000
    setattr(receipt, field, value)
    query = Query([assistant(), receipt])
    with pytest.raises(RuntimeError) as caught:
        await collect_review(backend(tmp_path, query), 'q', 1)
    message = str(caught.value)
    assert field in message and len(message) < 2200
    assert '\n' not in message and '\x1b' not in message
    assert json.dumps(value[:160], ensure_ascii=True) in message


@pytest.mark.parametrize('first', [None, {}, {'input_tokens': -1}, {'input_tokens': True},
                                  {'input_tokens': 6, 'output_tokens': 2, 'cache_read_input_tokens': 4}])
@pytest.mark.parametrize('failed', [False, True])
async def test_first_terminal_usage_only_and_explicit_parent(tmp_path, monkeypatch, first, failed):
    provider = get_provider('machx')
    parent = RunMeter('parent', 1, resolve_profile(provider))
    unrelated = RunMeter('unrelated', 1, resolve_profile(provider))
    monkeypatch.setattr(context, '_CTX', SimpleNamespace(runtime_meter=unrelated))
    query = Query([assistant(), terminal(error=failed, usage=first),
                   terminal(usage={'input_tokens': 100, 'output_tokens': 100})])
    with context.bind_context(SimpleNamespace(runtime_meter=unrelated)):
        with pytest.raises(RuntimeError):
            await collect_review(backend(tmp_path, query, meter=parent), 'q', 1)
    valid = first == {'input_tokens': 6, 'output_tokens': 2, 'cache_read_input_tokens': 4}
    assert parent.prompt_tokens == (10 if valid else 0)
    assert parent.output_tokens == (2 if valid else 0)
    assert parent.cached_tokens == (4 if valid else 0)
    assert unrelated.prompt_tokens == unrelated.output_tokens == 0
    assert set(parent.phases) == ({'evaluator:anthropic:lead'} if valid else set())
    if valid:
        assert parent.phases['evaluator:anthropic:lead']['requests'] == 1


@pytest.mark.parametrize('hold', ['read', 'close'])
async def test_assessment_waits_for_read_and_owned_close(tmp_path, hold):
    query = Query([assistant(), terminal()], hold=hold)
    task = asyncio.create_task(collect_review(backend(tmp_path, query), 'q', 2))
    try:
        await asyncio.wait_for(query.entered.wait(), 1)
        assert not task.done()
        query.release.set()
        assert await asyncio.wait_for(task, 1) == 'VERDICT: PASS\nGAPS: none'
    finally:
        query.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert query.exhausted and query.closes == 1


@pytest.mark.parametrize('where', ['read', 'close'])
async def test_fault_after_terminal_keeps_usage_and_prevents_pass(tmp_path, where):
    parent = RunMeter('parent', 1, resolve_profile(get_provider('machx')))
    fault = RuntimeError('fixture fault')
    query = Query([assistant(), terminal(usage={'input_tokens': 7, 'output_tokens': 3})],
                   **{where + '_error': fault})
    with pytest.raises(RuntimeError) as caught:
        await collect_review(backend(tmp_path, query, meter=parent), 'q', 1)
    assert caught.value is fault
    assert parent.prompt_tokens == 7 and parent.output_tokens == 3
    assert parent.phases['evaluator:anthropic:lead']['requests'] == 1
    assert query.closes == 1


@pytest.mark.parametrize('hold', ['read', 'close'])
@pytest.mark.parametrize('close_fault', [False, True])
async def test_external_cancel_survives_owned_cleanup(tmp_path, hold, close_fault):
    query = Query([assistant(), terminal()], hold=hold,
                   close_error=RuntimeError('fixture cleanup failure') if close_fault else None)
    task = asyncio.create_task(collect_review(backend(tmp_path, query), 'q', 2))
    try:
        await asyncio.wait_for(query.entered.wait(), 1)
        task.cancel('fixture external cancellation')
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        query.release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert query.closes == 1


@pytest.mark.parametrize('hold', ['read', 'close'])
@pytest.mark.parametrize('close_fault', [False, True])
async def test_outer_deadline_never_returns_assessment(tmp_path, hold, close_fault):
    query = Query([assistant(), terminal()], hold=hold,
                   close_error=RuntimeError('fixture timeout cleanup') if close_fault else None)
    with pytest.raises(TimeoutError):
        await collect_review(backend(tmp_path, query), 'q', 0.03)
    assert query.closes == 1


async def test_early_consumer_close_settles_query_in_owner(tmp_path):
    query = Query([assistant(), terminal()])
    stream = backend(tmp_path, query).ask('q')
    assert (await anext(stream)).kind == 'assistant_done'
    await stream.aclose()
    assert query.closes == 1 and query.owner is asyncio.current_task()
    assert not query.exhausted


@pytest.mark.parametrize('wrapped', [False, True])
async def test_query_generator_finalizer_cannot_replace_read_cancellation(tmp_path, wrapped):
    entered = asyncio.Event()
    observed = []
    async def query(**kwargs):
        owner = asyncio.current_task()
        with anyio.CancelScope():
            try:
                yield assistant()
                entered.set()
                await asyncio.Event().wait()
            finally:
                observed.append(asyncio.current_task() is owner)
                if wrapped:
                    raise RuntimeError('fixture generator finalizer')
    task = asyncio.create_task(collect_review(backend(tmp_path, query), 'q', 2))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel('fixture query cancellation')
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await asyncio.gather(task, return_exceptions=True)
    assert observed == [True]


def evidence(root):
    root.mkdir(exist_ok=True)
    (root / 'contract.md').write_text('# Contract\n\n- Preserve fixture evidence.\n')
    (root / 'progress.md').write_text('# Progress\n\nFixture evidence is present.\n')
    return {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.name in ('contract.md', 'progress.md')}


def review_factory(receipts, observed):
    def factory(settings, tools, system, cwd):
        query = Query([assistant(), *receipts])
        observed.append(query)
        return SDKReviewBackend(settings, tools, system, cwd, query)
    return factory


@pytest.mark.parametrize('receipts,expected', [([], 'UNVERIFIED'), ([terminal('MaxTurns')], 'UNVERIFIED'),
    ([terminal(reason='max_turns')], 'UNVERIFIED'), ([terminal(reason='aborted_streaming')], 'UNVERIFIED'),
    ([terminal(reason='completed')], 'PASS')])
async def test_actual_evaluate_rejects_partial_pass_with_valid_evidence(tmp_path, receipts, expected):
    worker = Worker(tmp_path)
    worker.provider, worker.model = get_provider('anthropic'), 'fixture-sdk'
    observed = []
    loop = AutonomousLoop(worker, state_dir=tmp_path / 'runs', evaluator_timeout=1,
                          evaluator_backend_factory=review_factory(receipts, observed))
    loop.workspace = tmp_path / 'evidence'
    hashes = evidence(loop.workspace)
    verdict, gaps = await loop._evaluate('fixture goal')
    assert verdict == expected
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == sha for p, sha in hashes.items())
    assert observed[0].calls == observed[0].closes == 1 and observed[0].exhausted
    assert all(str(p) in observed[0].users[0]['message']['content'] for p in hashes)
    if expected == 'PASS':
        assert loop.last_review['inspected'] == {str(p): sha for p, sha in hashes.items()}
    else:
        assert 'unavailable' in gaps and loop.last_review['verdict'] == 'UNVERIFIED'


@pytest.mark.parametrize('receipts,expected', [([], 'unverified'), ([terminal('MaxTurns')], 'unverified'),
    ([terminal(reason='aborted_tools')], 'unverified'), ([terminal(reason='completed')], 'done')])
async def test_actual_engine_loop_run_requires_completed_sdk_review(engine, tmp_path, receipts, expected):
    observed, worker_calls, hashes = [], [], {}
    class CompletedWorker:
        async def ask(self, prompt):
            worker_calls.append(prompt)
            path = loop.workspace / 'progress.md'
            path.write_text('# Progress\n\nFixture evidence is present.\n')
            for name in ('contract.md', 'progress.md'):
                p = loop.workspace / name
                hashes[p] = hashlib.sha256(p.read_bytes()).hexdigest()
            yield Event('assistant_done', 'Fixture findings.\nSTATUS: DONE')
            yield Event('result', {'subtype': 'success', 'is_error': False, 'usage': {}})
    engine.backend = CompletedWorker()
    loop = AutonomousLoop(engine, state_dir=tmp_path / 'runs', evaluator_provider='anthropic',
                          evaluator_model='fixture-sdk', evaluator_timeout=1,
                          evaluator_backend_factory=review_factory(receipts, observed))
    result = await asyncio.wait_for(loop.run('fixture goal',
        acceptance_criteria='- Preserve fixture evidence.'), 5)
    assert result.status == expected
    ledger = [json.loads(line) for line in (loop.workspace / 'ledger.jsonl').read_text().splitlines()]
    assert len([row for row in ledger if row['event'] == 'worker_finished']) == 1
    assert len(worker_calls) == len(observed) == 1
    assert observed[0].exhausted and observed[0].closes == 1
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == sha for p, sha in hashes.items())
    assert ledger[-1]['state']['status'] == expected
    assert ledger[-1]['state']['uncertain'] is False
    assert [row['data']['verdict'] for row in ledger if row['event'] == 'evaluation'] == [
        'PASS' if expected == 'done' else 'UNVERIFIED']
    if expected != 'done':
        assert all(row['state']['status'] != 'done' for row in ledger)


async def test_scoped_read_permissions_options_and_streamed_input_survive(tmp_path, monkeypatch):
    path = tmp_path / 'artifact.txt'
    path.write_text('fixture evidence')
    reader = ScopedReader(tmp_path)
    tools = reader.tools()
    query = Query([assistant(), terminal(reason='completed')])
    sentinel = object()
    monkeypatch.setattr(context, '_CTX', sentinel)
    assert await collect_review(backend(tmp_path, query, tools=tools), 'fixture question', 1) == 'VERDICT: PASS\nGAPS: none'
    opts = query.options
    assert opts.model == 'fixture-sdk'
    assert opts.system_prompt == {'type': 'preset', 'preset': 'claude_code', 'append': 'fixture review system'}
    # DREAM-140: Claude Code's posture (was DREAM-137's main-path options); no mode is ask = plan.
    assert opts.cwd == str(tmp_path) and opts.max_turns is None
    assert opts.setting_sources == ['user', 'project', 'local'] and opts.strict_mcp_config is False
    assert opts.permission_mode == 'plan' and opts.settings is None
    assert {'mcp__review__read_file', 'mcp__review__list_files'} <= set(opts.allowed_tools)
    assert (await opts.can_use_tool('Bash', {}, None)).behavior == 'deny'
    assert (await opts.can_use_tool('mcp__review__read_file', {}, None)).behavior == 'allow'
    assert query.users == [{'type': 'user', 'message': {'role': 'user', 'content': 'fixture question'},
                            'parent_tool_use_id': None, 'session_id': ''}]
    read = next(tool for tool in tools if tool.name == 'read_file')
    assert 'fixture evidence' in (await read.handler({'path': str(path)}))['content'][0]['text']
    assert (await read.handler({'path': '../outside.txt'}))['is_error'] is True
    assert reader.unchanged() and context._CTX is sentinel


@pytest.mark.parametrize('subtype,reason,error', [('success', 'completed', False),
    ('MaxTurns', None, False), ('success', 'aborted_streaming', False), ('success', None, True)])
async def test_single_terminal_retains_observed_usage_on_each_outcome(tmp_path, subtype, reason, error):
    parent = RunMeter('parent', 1, resolve_profile(get_provider('machx')))
    query = Query([assistant(), terminal(subtype, reason=reason, error=error,
                                        usage={'input_tokens': 7, 'output_tokens': 3})])
    review = backend(tmp_path, query, meter=parent)
    if subtype == 'success' and reason == 'completed':
        assert await collect_review(review, 'q', 1) == 'VERDICT: PASS\nGAPS: none'
    else:
        with pytest.raises(RuntimeError):
            await collect_review(review, 'q', 1)
    assert parent.prompt_tokens == 7 and parent.output_tokens == 3
    assert parent.phases['evaluator:anthropic:lead']['requests'] == 1
    assert query.exhausted and query.closes == 1


@pytest.mark.parametrize('where', ['read', 'close'])
@pytest.mark.parametrize('prior', [0, 1, 2])
async def test_historical_cancellation_context_does_not_replace_an_ordinary_fault(tmp_path, where, prior):
    async def run():
        owner = asyncio.current_task()
        for _ in range(prior):
            owner.cancel('historical cancellation')
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass
        fault = RuntimeError('ordinary fault with stale context')
        fault.__context__ = asyncio.CancelledError('historical cancellation')
        query = Query([assistant(), terminal()], **{where + '_error': fault})
        with pytest.raises(RuntimeError) as caught:
            async for _ in backend(tmp_path, query).ask('q'):
                pass
        assert caught.value is fault and owner.cancelling() == prior
        assert query.closes == 1
    await asyncio.wait_for(asyncio.create_task(run()), 1)


@pytest.mark.parametrize('where', ['read', 'close'])
@pytest.mark.parametrize('prior', [0, 2])
async def test_direct_cancellation_identity_notes_and_counts(tmp_path, where, prior):
    entered = asyncio.Event()
    seen = {}
    secondary = RuntimeError('cleanup"\n\x1b' + '\U0001f34d' * 5000)
    class Wrapped(Query):
        async def __anext__(self):
            if where == 'read' and hasattr(self, 'owner') and not self.messages:
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError as exc:
                    seen['cancel'] = exc
                    raise secondary
            return await super().__anext__()

        async def aclose(self):
            if where == 'close':
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError as exc:
                    seen['cancel'] = exc
                    try:
                        raise secondary
                    finally:
                        await super().aclose()
            else:
                await super().aclose()

    query = Wrapped([assistant(), terminal()])
    async def run():
        owner = asyncio.current_task()
        for _ in range(prior):
            owner.cancel('historical')
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass
        try:
            async for _ in backend(tmp_path, query).ask('q'):
                pass
        except asyncio.CancelledError as exc:
            assert exc is seen['cancel']
            assert exc.args == ('current cancellation',)
            assert owner.cancelling() == prior + 1
            note, = exc.__notes__
            assert json.dumps(str(secondary)[:160], ensure_ascii=True) in note
            assert '\n' not in note and '\x1b' not in note and len(note) < 2100
            raise
    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel('current cancellation')
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await asyncio.gather(task, return_exceptions=True)
    assert query.closes == 1


async def test_cancellation_during_close_after_read_error_takes_precedence(tmp_path):
    query = Query([assistant(), terminal()], read_error=ValueError('read failed'),
                   hold='close', close_error=RuntimeError('close finalizer failed'))
    async def run():
        async for _ in backend(tmp_path, query).ask('q'):
            pass
    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(query.entered.wait(), 1)
        task.cancel('current close cancellation')
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.args == ('current close cancellation',)
    finally:
        query.release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert query.closes == 1


async def test_early_close_failure_remains_observable(tmp_path):
    fault = RuntimeError('explicit close failure')
    query = Query([assistant(), terminal()], close_error=fault)
    stream = backend(tmp_path, query).ask('q')
    assert (await anext(stream)).kind == 'assistant_done'
    with pytest.raises(RuntimeError) as caught:
        await stream.aclose()
    assert caught.value is fault and query.closes == 1


@pytest.mark.parametrize('where', ['read', 'close'])
@pytest.mark.parametrize('split_reads', [False, True])
@pytest.mark.parametrize('current_context', [False, True])
async def test_reused_generator_requires_latest_injected_cancellation_identity(
        tmp_path, where, split_reads, current_context):
    entered = [asyncio.Event(), asyncio.Event()]
    observed, faults = [], []

    async def history():
        # Both exceptions originate in this same generator frame and at the
        # same source line. Their messages are deliberately identical.
        for index in range(2):
            entered[index].set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as exc:
                observed.append(exc)
                try:
                    raise RuntimeError('retained cleanup failure')
                except RuntimeError as fault:
                    faults.append(fault)
            if index == 0 and split_reads:
                yield terminal()
        raise faults[1 if current_context else 0]

    class HistoryQuery(Query):
        def __init__(self):
            packets = [assistant(), terminal()] if where == 'close' and not split_reads else [assistant()]
            super().__init__(packets)
            self.history = history()
            self.reads = 0

        async def __anext__(self):
            self.reads += 1
            if self.reads > 1 and where == 'read':
                return await anext(self.history)
            if self.reads == 2 and where == 'close' and split_reads:
                return await anext(self.history)
            return await super().__anext__()

        async def aclose(self):
            try:
                if where == 'close':
                    await anext(self.history)
            finally:
                await self.history.aclose()
                await super().aclose()

    query = HistoryQuery()
    async def run():
        try:
            async for _ in backend(tmp_path, query).ask('q'):
                pass
        except BaseException as exc:
            return exc
        raise AssertionError('Partial review unexpectedly succeeded')

    task = asyncio.create_task(run())
    try:
        for at in entered:
            await asyncio.wait_for(at.wait(), 1)
            task.cancel('same cancellation text')
        outcome = await task
        assert observed[0] is not observed[1]
        assert outcome is (observed[1] if current_context else faults[0])
        assert outcome is not observed[0]
        assert query.closes == 1 and query.close_owner is query.owner
        assert task.cancelling() == 2
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('mode', ['send', 'cancel', 'suppress', 'wrapper', 'base',
                                  'generator-exit', 'next-only', 'sync-fault'])
async def test_actual_review_await_protocol_preserves_values_and_exception_identity(tmp_path, mode):
    token, supplied = object(), object()
    packet = assistant()
    calls = []
    failure = (GeneratorExit('close') if mode == 'generator-exit' else
               BaseException('base failure') if mode == 'base' else
               RuntimeError('synchronous failure') if mode == 'sync-fault' else
               asyncio.CancelledError('current cancellation'))

    class Protocol:
        def __await__(self):
            return iter([token]) if mode == 'next-only' else self
        def __iter__(self):
            return self
        def __next__(self):
            calls.append('next')
            if mode == 'sync-fault':
                raise failure
            return token
        def send(self, value):
            assert value is supplied
            calls.append('send')
            raise StopIteration(packet)
        def throw(self, exc):
            calls.append('throw')
            assert exc is failure
            if mode == 'suppress':
                raise StopIteration(packet)
            if mode == 'wrapper':
                try:
                    raise exc
                except BaseException:
                    raise RuntimeError('provider wrapper')
            raise exc
        def close(self):
            calls.append('close')

    class ProtocolQuery:
        reads = closes = 0
        def __call__(self, **kwargs):
            return self
        def __aiter__(self):
            return self
        def __anext__(self):
            self.reads += 1
            if self.reads == 1:
                return Protocol()
            async def immediate():
                if self.reads == 2:
                    return terminal()
                raise StopAsyncIteration
            return immediate()
        async def aclose(self):
            self.closes += 1

    query = ProtocolQuery()
    stream = backend(tmp_path, query).ask('q')
    driver = anext(stream).__await__()
    try:
        if mode == 'sync-fault':
            with pytest.raises(RuntimeError) as caught:
                next(driver)
            assert caught.value is failure
        else:
            assert next(driver) is token
            if mode in ('send', 'suppress'):
                with pytest.raises(StopIteration) as stopped:
                    driver.send(supplied) if mode == 'send' else driver.throw(failure)
                assert stopped.value.value.kind == 'assistant_done'
                assert stopped.value.value.data == 'VERDICT: PASS\nGAPS: none'
                assert [event async for event in stream] == []
            elif mode == 'next-only':
                with pytest.raises(StopAsyncIteration):
                    driver.send(None)
            else:
                with pytest.raises(type(failure)) as caught:
                    driver.throw(failure)
                assert caught.value is failure
                if mode == 'wrapper':
                    assert 'provider wrapper' in ' '.join(caught.value.__notes__)
        if mode == 'generator-exit':
            assert calls == ['next', 'close']
        if mode == 'next-only':
            assert calls == []
        assert query.closes == 1
    finally:
        driver.close()
        await stream.aclose()


@pytest.mark.parametrize('action', ['continue', 'close', 'throw'])
async def test_successful_read_cannot_lend_cancellation_to_consumer_failure(tmp_path, action):
    ready = asyncio.Event()
    saved = []
    class ConsumedRead(Query):
        async def __anext__(self):
            if not hasattr(self, 'owner'):
                ready.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError as exc:
                    try:
                        raise RuntimeError('retained failure after successful read')
                    except RuntimeError as fault:
                        saved.append((exc, fault))
            return await super().__anext__()
    query = ConsumedRead([assistant(), terminal()])
    async def consume():
        stream = backend(tmp_path, query).ask('q')
        try:
            event = await anext(stream)
            assert event.kind == 'assistant_done'
            if action == 'continue':
                assert [event async for event in stream] == []
            elif action == 'close':
                await stream.aclose()
            else:
                # Direct async-generator consumer failure, not collect_review.
                with pytest.raises(RuntimeError) as caught:
                    await stream.athrow(saved[0][1])
                assert caught.value is saved[0][1]
                assert caught.value.__context__ is saved[0][0]
            assert asyncio.current_task().cancelling() == 1
        finally:
            await stream.aclose()
    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(ready.wait(), 1)
        task.cancel('already consumed read cancellation')
        await task
        assert query.closes == 1 and query.close_owner is query.owner
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
