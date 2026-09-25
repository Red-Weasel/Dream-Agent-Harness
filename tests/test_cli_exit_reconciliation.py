"""Process-exit reconciliation through real CLI parsing and its consumers.

Every process and reader below is in memory. Barriers define lifecycle ordering;
the fixtures forbid real process, provider, network and model entry points.
"""
from __future__ import annotations

import asyncio
from collections import deque
import copy
import json
import os
import socket
import subprocess
from types import SimpleNamespace

import pytest

from dream.core.backends import cli_agent
from dream.core.backends.base import Event
from dream.core.backends.cli_agent import CliAgentBackend, CodexAdapter, adapter_for
from dream.core.profiles import PROFILES


def record(obj):
    return (json.dumps(obj) + '\n').encode()


SUCCESS = {'type': 'turn.completed', 'usage': {'input_tokens': 11, 'output_tokens': 7}}
PARTIAL = {'type': 'item.completed', 'item': {
    'type': 'agent_message', 'text': 'STATUS: DONE\nNEXT: inspect the existing artifact',
}}


class Reader:
    def __init__(self, chunks=(), *, held=False):
        self.chunks = deque(chunks)
        self.blocked = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()
        if not held:
            self.release.set()

    async def read(self, limit):
        if self.chunks:
            chunk = self.chunks.popleft()
            if isinstance(chunk, BaseException):
                raise chunk
            if len(chunk) > limit:
                self.chunks.appendleft(chunk[limit:])
            return chunk[:limit]
        self.blocked.set()
        try:
            await self.release.wait()
            return b''
        finally:
            self.closed.set()


class Process:
    def __init__(self, chunks, *, rc=0, held=False, stdout_held=False, stderr=()):
        self.stdout = Reader(chunks, held=stdout_held)
        self.stderr = Reader(stderr)
        self.rc = rc
        self.returncode = None
        self.cleaned = False
        self.terminations = self.kills = 0
        self.wait_started = asyncio.Event()
        self.release = asyncio.Event()
        if not held:
            self.release.set()

    async def wait(self):
        self.wait_started.set()
        await self.release.wait()
        self.returncode = self.rc
        self.cleaned = True
        return self.rc

    def terminate(self):
        self.terminations += 1
        self.rc = -15
        self.stdout.release.set()
        self.release.set()

    def kill(self):
        self.kills += 1
        self.rc = -9
        self.stdout.release.set()
        self.release.set()


@pytest.fixture(autouse=True)
def deny_live(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Live process/provider/network/model operation forbidden')

    import httpx
    from dream.core.backends.anthropic import AnthropicBackend
    from dream.core.backends.openai_compat import OpenAICompatBackend
    from dream.memory.embeddings import Embedder, Reranker
    monkeypatch.setattr(subprocess.Popen, '__init__', forbidden)
    monkeypatch.setattr(asyncio, 'create_subprocess_shell', forbidden)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', forbidden)
    monkeypatch.setattr(os, 'system', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(httpx.AsyncClient, 'send', forbidden)
    monkeypatch.setattr(httpx.Client, 'send', forbidden)
    for cls in (CliAgentBackend, AnthropicBackend, OpenAICompatBackend):
        monkeypatch.setattr(cls, 'connect', forbidden)
    for cls in (Embedder, Reranker):
        monkeypatch.setattr(cls, '_ensure', forbidden)


@pytest.fixture
def cli(tmp_path, monkeypatch):
    pending, calls = deque(), []
    monkeypatch.setattr(cli_agent, 'supervised_command', lambda argv: argv)

    async def spawn(*argv, **kwargs):
        assert argv[0] == '/fixture/cli-exit-only'
        assert kwargs['start_new_session'] is True
        calls.append(argv)
        return pending.popleft()

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)

    def make(objects=(), *, adapter=None, chunks=None, idle_timeout=1, backend=None, **kwargs):
        proc = Process([record(obj) for obj in objects] if chunks is None else chunks, **kwargs)
        pending.append(proc)
        if backend is None:
            adapter = adapter or CodexAdapter()
            adapter.cmd = '/fixture/cli-exit-only'
            backend = CliAgentBackend(adapter, system_prompt='fixture system', cwd=str(tmp_path),
                                      idle_timeout=idle_timeout)
        return backend, proc

    yield make, calls
    assert not pending


async def collect(backend, delivered=None):
    delivered = [] if delivered is None else delivered
    async with asyncio.timeout(5):
        async for event in backend.ask('fixture request'):
            delivered.append(event)
    return delivered


def results(events):
    return [event.data for event in events if event.kind == 'result']


@pytest.mark.parametrize('key,obj', [
    ('codex', SUCCESS),
    ('grok', {'type': 'end', 'stopReason': 'EndTurn', 'sessionId': 'session-23',
              'usage': {'input_tokens': 11, 'output_tokens': 7}, 'total_cost_usd': .25}),
    ('gemini', {'type': 'result', 'status': 'success',
                'stats': {'input_tokens': 11, 'output_tokens': 7, 'cached': 3}}),
])
@pytest.mark.parametrize('rc', [0, 3, -15, None])
async def test_builtin_terminal_waits_for_cleanup_and_reconciles_exit(cli, key, obj, rc):
    make, calls = cli
    adapter = adapter_for(key)
    original = next(ev.data for ev in adapter.translate(obj, {}) if ev.kind == 'result')
    backend, proc = make([obj], adapter=adapter, rc=rc, held=True)
    delivered = []
    task = asyncio.create_task(collect(backend, delivered))
    try:
        await asyncio.wait_for(proc.wait_started.wait(), 1)
        assert results(delivered) == []
        assert backend._proc is proc
        with pytest.raises(RuntimeError, match='active turn'):
            await anext(backend.ask('must not spawn'))
    finally:
        proc.release.set()
        await asyncio.wait_for(task, 1)
    assert proc.cleaned and proc.stderr.closed.is_set() and backend._proc is None
    assert len(calls) == len(results(delivered)) == 1
    actual = results(delivered)[0]
    if rc == 0:
        assert actual == original
    else:
        assert actual['is_error'] is True and actual['subtype'] == 'error'
        assert actual['provider_subtype'] == original['subtype']
        assert actual['cli_exit_code'] == rc
        assert actual['usage'] == original['usage']
        assert actual['total_cost_usd'] == original['total_cost_usd']
        assert f'exited {rc}' in actual['error']
    if key == 'grok':
        assert backend._session_id == 'session-23'


@pytest.mark.parametrize('statuses', [('success', 'success'), ('success', 'error'), ('error', 'success')])
async def test_duplicate_terminal_fails_and_retains_first_accounting(cli, statuses):
    make, _ = cli
    objects = [{'type': 'result', 'status': status, 'stats': {'input_tokens': count},
                'error': {'message': 'first provider reason' if count == 11 else 'second reason'}}
               for status, count in zip(statuses, (11, 99))]
    backend, _ = make(objects, adapter=adapter_for('gemini'))
    events = await collect(backend)
    assert len(results(events)) == 1
    final = results(events)[0]
    assert final['is_error'] is True and final['subtype'] == 'error'
    assert final['usage']['input_tokens'] == 11
    assert final['provider_subtype'] == ('error' if statuses[0] == 'error' else 'success')
    assert 'multiple terminal' in final['error'].lower()
    assert 'first provider reason' in final['error'] and 'second reason' not in final['error']


@pytest.mark.parametrize('before', [True, False])
async def test_fatal_event_stays_visible_and_prevents_later_success(cli, before):
    make, _ = cli
    terminal = {'type': 'end', 'stopReason': 'EndTurn', 'usage': {'input_tokens': 11}}
    fatal = {'type': 'error', 'message': 'provider fatal reason'}
    backend, proc = make([fatal, terminal] if before else [terminal, fatal],
                         adapter=adapter_for('grok'), stdout_held=True)
    stream = backend.ask('fixture request')
    first = await asyncio.wait_for(anext(stream), 1)
    assert first.kind == 'error' and first.data == 'provider fatal reason'
    assert not proc.cleaned
    proc.stdout.release.set()
    events = [event async for event in stream]
    assert len(results(events)) == 1
    assert results(events)[0]['is_error'] is True
    assert 'provider fatal reason' in results(events)[0]['error']


@pytest.mark.parametrize('action', ['interrupt', 'disconnect'])
async def test_explicit_stop_wins_after_zero_exit_before_publication_and_next_turn_works(cli, action):
    make, calls = cli
    backend, proc = make([{'type': 'thread.started', 'thread_id': 'retained'}, SUCCESS], held=True)
    proc.returncode = 0
    delivered = []
    task = asyncio.create_task(collect(backend, delivered))
    await asyncio.wait_for(proc.wait_started.wait(), 1)
    stop = asyncio.create_task(getattr(backend, action)())
    # The stop call must enter the owned process wait before cleanup is released.
    entered = asyncio.Event()
    old_wait = proc.wait
    async def stop_wait():
        entered.set()
        return await old_wait()
    proc.wait = stop_wait
    try:
        await asyncio.wait_for(entered.wait(), 1)
    finally:
        proc.release.set()
        await asyncio.wait_for(asyncio.gather(stop, task), 1)
    assert not any(not result.get('is_error') for result in results(delivered))
    assert any('interrupt' in str(event.data).lower() for event in delivered)
    assert backend._session_id == 'retained' and proc.terminations == 0
    make([SUCCESS], backend=backend)
    assert results(await collect(backend))[0]['is_error'] is False
    assert len(calls) == 2 and 'resume' in calls[1] and 'retained' in calls[1]


@pytest.mark.parametrize('reason', [None, 'EndTurn', 'stop', 'end_turn', 'StopSequence', 'complete',
                                   'COMPLETED', 'MaxTurns', 'Refusal', ''])
async def test_grok_existing_stop_reason_contract_is_preserved(cli, reason):
    make, _ = cli
    obj = {'type': 'end', 'stopReason': reason, 'usage': {'custom': 4}, 'total_cost_usd': .7}
    adapter = adapter_for('grok')
    original = adapter.translate(obj, {})[0].data
    backend, _ = make([obj], adapter=adapter)
    assert results(await collect(backend)) == [original]


@pytest.mark.parametrize('status_fields', [
    {}, {'status': None}, {'status': ''}, {'status': 'unknown'},
    {'status': 'cancelled'}, {'status': False}, {'status': {}},
    {'status': ['success']},
])
async def test_gemini_malformed_terminal_cannot_complete_on_clean_exit(cli, status_fields):
    make, _ = cli
    backend, proc = make([
        {'type': 'message', 'role': 'assistant', 'content': 'partial output'},
        {'type': 'result', 'stats': {'input_tokens': 11, 'output_tokens': 7},
         **status_fields},
    ], adapter=adapter_for('gemini'))
    events = await collect(backend)
    assert proc.cleaned and backend._proc is None
    assert any(event.kind == 'assistant_done' and event.data == 'partial output'
               for event in events)
    assert len(results(events)) == 1
    final = results(events)[0]
    assert final['is_error'] is True and final['subtype'] == 'error'
    assert 'terminal status' in final['error'].lower()
    assert final['usage']['input_tokens'] == 11
    assert final['usage']['output_tokens'] == 7


@pytest.mark.parametrize('status', [None, 'success', 'unknown', 'error'])
@pytest.mark.parametrize('rc', [0, 5])
async def test_gemini_original_reason_usage_and_defaults(cli, status, rc):
    make, _ = cli
    obj = {'type': 'result', 'stats': {'input_tokens': 11, 'output_tokens': 7},
           'error': {'message': 'provider explanation'}}
    if status is not None:
        obj['status'] = status
    adapter = adapter_for('gemini')
    original = adapter.translate(obj, {})[0].data
    backend, _ = make([obj], adapter=adapter, rc=rc, stderr=[b'x' * 70000 + b'last diagnostic'])
    actual = results(await collect(backend))[0]
    if rc == 0:
        assert actual == original
    else:
        assert actual['is_error'] is True and actual['subtype'] == 'error'
        assert actual['provider_subtype'] == original['subtype']
        assert actual['error'].startswith('provider explanation; ')
        assert actual['error'].endswith('last diagnostic') and len(actual['error']) < 1000
        assert actual['usage'] == original['usage']


@pytest.mark.parametrize('is_error,subtype', [(False, 'fixture'), (True, 'fixture-failed'), (False, 'length')])
async def test_custom_zero_exit_metadata_is_exact(cli, is_error, subtype):
    make, _ = cli
    expected = {'is_error': is_error, 'subtype': subtype, 'error': 'custom reason',
                'usage': {'custom': 9}, 'total_cost_usd': .2, 'extension': {'nested': ['retained']}}
    class Custom(CodexAdapter):
        def translate(self, obj, state):
            return [Event('result', copy.deepcopy(expected))]
    backend, _ = make([{}], adapter=Custom())
    assert results(await collect(backend)) == [expected]


@pytest.mark.parametrize('key,objects,kinds', [
    ('codex', [{'type': 'item.completed', 'item': {'type': 'error', 'message': 'recoverable notice'}},
               {'type': 'item.started', 'item': {'type': 'command_execution', 'command': 'fixture', 'id': '1'}},
               {'type': 'item.completed', 'item': {'type': 'command_execution', 'exit_code': 2, 'id': '1',
                                                'aggregated_output': 'tool failed'}}, PARTIAL, SUCCESS],
     ['system', 'tool_use', 'tool_result', 'assistant_done', 'result']),
    ('gemini', [{'type': 'error', 'message': 'recoverable notice'},
                {'type': 'tool_use', 'tool_name': 'fixture', 'tool_id': '1'},
                {'type': 'tool_result', 'tool_id': '1', 'status': 'error', 'output': 'tool failed'},
                {'type': 'result', 'status': 'success'}],
     ['system', 'tool_use', 'tool_result', 'result']),
])
async def test_system_and_tool_errors_remain_recoverable_and_stream_before_cleanup(cli, key, objects, kinds):
    make, _ = cli
    backend, proc = make(objects, adapter=adapter_for(key), held=True)
    delivered = []
    task = asyncio.create_task(collect(backend, delivered))
    try:
        await asyncio.wait_for(proc.wait_started.wait(), 1)
        assert [event.kind for event in delivered] == kinds[:-1]
        assert next(event.data for event in delivered if event.kind == 'tool_result')['is_error'] is True
    finally:
        proc.release.set()
        await asyncio.wait_for(task, 1)
    assert [event.kind for event in delivered] == kinds
    assert results(delivered)[0]['is_error'] is False


async def test_stderr_settlement_holds_publication_and_active_guard(cli):
    make, _ = cli
    backend, proc = make([SUCCESS])
    proc.stderr.release.clear()
    delivered = []
    task = asyncio.create_task(collect(backend, delivered))
    try:
        await asyncio.wait_for(proc.wait_started.wait(), 1)
        assert proc.cleaned and not proc.stderr.closed.is_set()
        assert backend._proc is proc and results(delivered) == []
        with pytest.raises(RuntimeError, match='active turn'):
            await anext(backend.ask('must not spawn'))
    finally:
        proc.stderr.release.set()
        await asyncio.wait_for(task, 1)
    assert proc.stderr.closed.is_set() and results(delivered)[0]['is_error'] is False


@pytest.mark.parametrize('fault', ['stdout', 'translate', 'cleanup', 'stderr'])
async def test_fault_after_terminal_discards_success_and_is_observable(cli, fault):
    make, _ = cli
    error = RuntimeError('fixture ' + fault + ' failed')
    class FaultAdapter(CodexAdapter):
        def translate(self, obj, state):
            if obj.get('type') == 'fault':
                raise error
            return super().translate(obj, state)
    backend, proc = make([SUCCESS], adapter=FaultAdapter())
    if fault == 'stdout':
        proc.stdout.chunks.append(error)
    elif fault == 'translate':
        proc.stdout.chunks.append(record({'type': 'fault'}))
    elif fault == 'stderr':
        proc.stderr.chunks.append(error)
    else:
        async def failed_wait():
            proc.cleaned = True
            raise error
        proc.wait = failed_wait
    delivered = []
    with pytest.raises(RuntimeError) as caught:
        await collect(backend, delivered)
    assert caught.value is error and results(delivered) == []
    assert proc.cleaned and backend._proc is None


async def test_terminal_followed_by_idle_expiry_discards_success(cli):
    make, _ = cli
    backend, proc = make([SUCCESS], stdout_held=True, idle_timeout=.01)
    events = await asyncio.wait_for(collect(backend), 1)
    assert results(events) == [] and len(events) == 1
    assert events[0].kind == 'error' and 'idle timeout' in events[0].data
    assert proc.cleaned and proc.terminations == 1 and backend._proc is None


@pytest.mark.parametrize('at', ['before_eof', 'process_cleanup', 'stderr_cleanup'])
async def test_repeated_cancellation_joins_cleanup_without_publication(cli, at):
    make, calls = cli
    backend, proc = make([SUCCESS], held=at == 'process_cleanup', stdout_held=at == 'before_eof')
    if at == 'stderr_cleanup':
        proc.stderr.release.clear()
    delivered = []
    task = asyncio.create_task(collect(backend, delivered))
    barrier = proc.stdout.blocked if at == 'before_eof' else proc.wait_started
    await asyncio.wait_for(barrier.wait(), 1)
    task.cancel()
    # Run a scheduled callback after the cancellation request is delivered.
    checkpoint = asyncio.Event()
    asyncio.get_running_loop().call_soon(checkpoint.set)
    await checkpoint.wait()
    if at != 'before_eof':
        assert not task.done() and backend._proc is proc
    task.cancel()
    proc.release.set()
    proc.stderr.release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert results(delivered) == [] and proc.cleaned and proc.stderr.closed.is_set()
    assert backend._proc is None and len(calls) == 1


@pytest.mark.parametrize('after_terminal', [False, True])
async def test_consumer_close_never_flushes_pending_success(cli, after_terminal):
    make, _ = cli
    objects = [SUCCESS, PARTIAL] if after_terminal else [PARTIAL, SUCCESS]
    backend, proc = make(objects, stdout_held=True)
    stream = backend.ask('fixture request')
    assert (await anext(stream)).kind == 'assistant_done'
    await stream.aclose()
    assert proc.cleaned and proc.terminations == 1 and backend._proc is None


async def test_published_result_is_not_changed_by_later_interrupt(cli):
    make, _ = cli
    backend, proc = make([SUCCESS])
    stream = backend.ask('fixture request')
    event = await anext(stream)
    original = copy.deepcopy(event.data)
    await backend.interrupt()
    await stream.aclose()
    assert event.data == original and event.data['is_error'] is False
    assert proc.terminations == 0


@pytest.fixture
def engine(tmp_path, monkeypatch):
    from dream.core.engine import Engine
    from dream.memory.store import MemoryStore
    from dream.memory.working import WorkingMemory
    from dream.skills.selection import TaskGuidance
    from dream.tools.context import ToolContext
    monkeypatch.setattr('dream.config.SESSIONS_DIR', tmp_path / 'sessions')
    monkeypatch.setattr('dream.skills.selection.select_for_task', lambda *_, **__: TaskGuidance())
    monkeypatch.setattr('dream.projects.build_context', lambda *args: {
        'text': '', 'warnings': [], 'names': [], 'revision': 0,
    })
    e = Engine(provider='codex', model='fixture-model', workspace=tmp_path)
    e.store = MemoryStore(tmp_path / 'fixture.db')
    e.store.start_session(e.session_id)
    e.working = WorkingMemory(e.store, e.session_id)
    e._tool_context = ToolContext(e.store, e.working, None, e.session_id, workspace=tmp_path)
    e._started = True
    yield e
    e.store.close()


@pytest.mark.parametrize('rc,duplicate', [(0, False), (3, False), (0, True)])
async def test_actual_engine_accounts_first_result_once_and_retains_required_context(cli, engine, rc, duplicate):
    from dream.core.council_context import snapshot
    make, calls = cli
    terminal = {'type': 'end', 'stopReason': 'EndTurn', 'usage': {'input_tokens': 11, 'output_tokens': 7},
                'total_cost_usd': .25}
    objects = [{'type': 'text', 'data': 'STATUS: DONE'}, terminal]
    if duplicate:
        objects.append({**terminal, 'usage': {'input_tokens': 100}, 'total_cost_usd': 9})
    engine.backend, proc = make(objects, adapter=adapter_for('grok'), rc=rc)
    engine.working.log_turn('user', 'required prior user constraint')
    engine._pending_handoff = snapshot(engine.store, engine.session_id)
    events = await collect(engine)
    failed = rc != 0 or duplicate
    assert len(results(events)) == 1 and results(events)[0]['is_error'] is failed
    assert engine.session_tokens == 18 and engine.last_context_tokens == 11
    assert engine.total_cost_usd == .25
    assert engine.runtime_meter.prompt_tokens == 11 and engine.runtime_meter.output_tokens == 7
    assert engine.runtime_meter.phases['lead']['requests'] == 1
    assert engine.turn_timing.summary()['outcome'] == ('error' if failed else 'completed')
    assert (engine._pending_handoff is not None) is failed
    if failed:
        assert [r.content for r in engine._pending_handoff.required] == [
            'required prior user constraint', 'fixture request',
        ]
    assert 'required prior user constraint' in ' '.join(calls[0])
    assert proc.cleaned and len(calls) == 1


@pytest.mark.parametrize('rc', [0, 3, -15])
async def test_actual_loop_never_finishes_or_reviews_failed_cli_exit(cli, engine, tmp_path, rc):
    from dream.core.loop import AutonomousLoop
    make, calls = cli
    engine.backend, proc = make([PARTIAL, SUCCESS], rc=rc)
    reviews = []
    class Reviewer:
        async def connect(self):
            pass
        async def disconnect(self):
            pass
        async def ask(self, prompt):
            reviews.append(prompt)
            yield Event('assistant_done', 'VERDICT: PASS\nGAPS: none')
    loop = AutonomousLoop(engine, state_dir=tmp_path / 'runs',
                          evaluator_backend_factory=lambda *args: Reviewer())
    result = await loop.run('fixture goal', acceptance_criteria='- inspect the existing artifact')
    ledger = [json.loads(line) for line in (loop.workspace / 'ledger.jsonl').read_text().splitlines()]
    assert result.status == ('done' if rc == 0 else 'error')
    assert len(reviews) == int(rc == 0)
    assert any(row['event'] == 'worker_finished' for row in ledger) is (rc == 0)
    assert ledger[-1]['state']['uncertain'] is (rc != 0)
    assert engine.turn_timing.summary()['outcome'] == ('completed' if rc == 0 else 'error')
    assert engine.session_tokens == 18
    if rc:
        assert all(row['state'].get('phase') != 'done' for row in ledger)
        blocked = await loop.run('fixture goal', resume_run_id=result.run_id)
        assert blocked.status == 'need_input' and reviews == []
    assert proc.cleaned and len(calls) == 1


@pytest.mark.parametrize('rc', [0, 3])
async def test_guided_workflow_does_not_finalize_after_failed_exit(cli, engine, tmp_path, monkeypatch, rc):
    from dream.tui.app import App
    from dream.workflows import WorkflowService
    make, _ = cli
    engine.backend, _ = make([PARTIAL, SUCCESS], rc=rc)
    app = App(workspace=tmp_path, gui=False)
    app.engine = engine
    monkeypatch.setattr(app, '_render_event', lambda _: None)
    for name in ('live_begin', 'live_end', 'working'):
        monkeypatch.setattr(app.renderer, name, lambda *args: None)
    async def seal():
        pass
    monkeypatch.setattr(app, '_seal_checkpoint', seal)
    service = WorkflowService(tmp_path)
    task = service.create('report', {'goal': 'Report fixture observations'})
    await service.start(task['id'], 1, 'fixture-start', app._queue_workflow)
    queued = app._gui_prompts.get_nowait()
    output = tmp_path / task['output_path']
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('# Report\n\nFixture observations with a pre-existing output.\n')
    await app._ask(queued.text, workflow=queued)
    assert service.get(task['id'])['status'] == ('ready_for_review' if rc == 0 else 'failed')


@pytest.mark.parametrize('rc', [0, 3])
async def test_consolidation_does_not_retire_notes_or_trust_failed_summary(cli, tmp_path, monkeypatch, rc):
    from dream.core.engine import Engine
    from dream.core import engine as engine_module
    make, calls = cli
    backend, _ = make([{'type': 'item.completed', 'item': {
        'type': 'agent_message', 'text': 'SUMMARY: Fixture consolidation complete.',
    }}, SUCCESS], rc=rc)
    retired, summaries, logs = [], [], []
    store = SimpleNamespace(
        find_conflicts=lambda: [], stray_notes=lambda _: [{'id': 17, 'note': 'retain source note'}],
        mark_notes_consolidated_ids=lambda ids: retired.append(('stray', ids)),
        mark_notes_consolidated=lambda sid: retired.append(('session', sid)),
        end_session=lambda sid, summary: summaries.append(summary),
        get_session=lambda sid: {}, upsert_memory=lambda **kwargs: {'body': kwargs['body']},
        merge_duplicates=lambda: SimpleNamespace(updated=[], deleted=[], count=0),
    )
    monkeypatch.setattr(engine_module.curation, 'curate', lambda _: {})
    monkeypatch.setattr(engine_module.longterm, 'write_markdown', lambda _: None)
    # What consolidate() reads off a real Engine: the profile its own tool budget is cut from (DREAM-118),
    # the turn its runtime events are filed under, the run meter it puts back, and the event funnel.
    shell = SimpleNamespace(backend=backend, store=store, session_id='fixture', _log_stderr=logs.append,
                            profile=PROFILES['lean'], _turn_index=1, runtime_meter=None, emit=None)
    summary = await Engine.consolidate(shell)
    assert summary == ('Fixture consolidation complete.' if rc == 0 else None)
    assert summaries == [summary]
    assert retired == ([('stray', [17]), ('session', 'fixture')] if rc == 0 else [])
    assert len(calls) == 1


async def test_stderr_read_timeout_is_not_mistaken_for_cleanup_deadline(cli):
    make, _ = cli
    backend, proc = make([SUCCESS], stderr=[asyncio.TimeoutError('stderr read failed')])
    delivered = []
    with pytest.raises(asyncio.TimeoutError, match='stderr read failed'):
        await collect(backend, delivered)
    assert results(delivered) == [] and proc.cleaned and backend._proc is None


async def test_identical_repeated_results_are_protocol_failure(cli):
    make, _ = cli
    backend, _ = make([SUCCESS, SUCCESS, SUCCESS])
    final = results(await collect(backend))
    assert len(final) == 1 and final[0]['is_error'] is True
    assert final[0]['usage']['input_tokens'] == 11
    assert 'multiple terminal' in final[0]['error']


@pytest.mark.parametrize('chunk_size,final_newline', [(1, True), (7, False)])
async def test_split_final_line_and_late_session_capture(cli, chunk_size, final_newline):
    make, _ = cli
    wire = record(PARTIAL) + record(SUCCESS) + record({'type': 'thread.started', 'thread_id': 'late-session'})
    if not final_newline:
        wire = wire.rstrip(b'\n')
    backend, proc = make(chunks=[wire[i:i + chunk_size] for i in range(0, len(wire), chunk_size)])
    events = await collect(backend)
    assert [event.kind for event in events] == ['assistant_done', 'result']
    assert backend._session_id == 'late-session' and proc.cleaned


async def test_oversized_wire_line_does_not_hide_valid_later_terminal(cli, monkeypatch):
    make, _ = cli
    monkeypatch.setattr(cli_agent, 'STDOUT_LIMIT', 256)
    backend, _ = make(chunks=[b'x' * 300, b'x' * 300 + b'\n', record(PARTIAL), record(SUCCESS)])
    events = await collect(backend)
    assert [event.kind for event in events] == ['assistant_done', 'result']
    assert results(events)[0]['is_error'] is False


@pytest.mark.parametrize('action', ['cancel', 'interrupt', 'disconnect'])
async def test_stop_during_spawn_keeps_guard_and_joins_owned_cleanup(cli, monkeypatch, action):
    make, calls = cli
    backend, proc = make([SUCCESS])
    spawn_started, release_spawn = asyncio.Event(), asyncio.Event()
    original_spawn = asyncio.create_subprocess_exec
    async def held_spawn(*args, **kwargs):
        spawn_started.set()
        await release_spawn.wait()
        return await original_spawn(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', held_spawn)
    delivered = []
    task = asyncio.create_task(collect(backend, delivered))
    await asyncio.wait_for(spawn_started.wait(), 1)
    if action == 'cancel':
        task.cancel()
        stop = None
    else:
        stop = asyncio.create_task(getattr(backend, action)())
    checkpoint = asyncio.Event()
    asyncio.get_running_loop().call_soon(checkpoint.set)
    await checkpoint.wait()
    assert backend._spawning is not None and not task.done()
    with pytest.raises(RuntimeError, match='active turn'):
        await anext(backend.ask('not a second turn'))
    if action == 'cancel':
        task.cancel()
    release_spawn.set()
    if stop is not None:
        await asyncio.wait_for(stop, 1)
        await asyncio.wait_for(task, 1)
        assert len(results(delivered)) == 1 and results(delivered)[0]['is_error'] is True
    else:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert results(delivered) == []
    assert proc.cleaned and proc.terminations >= 1
    assert backend._proc is backend._spawning is None and len(calls) == 1


async def test_cancellation_joins_stderr_close_and_holds_turn_guard(cli, monkeypatch):
    make, _ = cli
    backend, proc = make([SUCCESS])
    closing, release_close = asyncio.Event(), asyncio.Event()
    class SlowClose(Reader):
        async def read(self, limit):
            self.blocked.set()
            try:
                await self.release.wait()
            finally:
                closing.set()
                await release_close.wait()
                self.closed.set()
            return b''
    proc.stderr = SlowClose(held=True)
    monkeypatch.setattr(cli_agent, '_TERM_GRACE_S', .01)
    delivered = []
    task = asyncio.create_task(collect(backend, delivered))
    try:
        await asyncio.wait_for(closing.wait(), 1)
        task.cancel()
        checkpoint = asyncio.Event()
        asyncio.get_running_loop().call_soon(checkpoint.set)
        await checkpoint.wait()
        task.cancel()
        assert not task.done() and backend._proc is proc
        with pytest.raises(RuntimeError, match='active turn'):
            await anext(backend.ask('not a second turn'))
    finally:
        release_close.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
    assert proc.stderr.closed.is_set() and backend._proc is None and results(delivered) == []


class StderrCloseReader(Reader):
    """A held pipe whose close outcome is controlled without a real process."""
    def __init__(self, error=None):
        super().__init__(held=True)
        self.error = error

    async def read(self, limit):
        self.blocked.set()
        try:
            await self.release.wait()
            return b''
        finally:
            self.closed.set()
            if self.error is not None:
                raise self.error


@pytest.mark.parametrize('error_type', [RuntimeError, asyncio.TimeoutError])
async def test_stderr_close_error_discards_terminal_after_deadline(cli, monkeypatch, error_type):
    make, _ = cli
    backend, proc = make([SUCCESS])
    error = error_type('fixture stderr close failure')
    proc.stderr = StderrCloseReader(error)
    monkeypatch.setattr(cli_agent, '_TERM_GRACE_S', .01)
    delivered = []
    with pytest.raises(error_type) as caught:
        await collect(backend, delivered)
    assert caught.value is error
    assert proc.cleaned and proc.stderr.closed.is_set() and backend._proc is None
    assert results(delivered) == []


@pytest.mark.parametrize('grace_expired', [False, True])
async def test_benign_stderr_cancellation_or_graceful_eof_keeps_clean_success(cli, monkeypatch, grace_expired):
    make, _ = cli
    backend, proc = make([SUCCESS])
    proc.stderr = StderrCloseReader()
    monkeypatch.setattr(cli_agent, '_TERM_GRACE_S', .01 if grace_expired else 1)
    delivered = []
    task = asyncio.create_task(collect(backend, delivered))
    await asyncio.wait_for(proc.stderr.blocked.wait(), 1)
    if not grace_expired:
        proc.stderr.release.set()
    await asyncio.wait_for(task, 1)
    assert results(delivered)[0]['is_error'] is False
    assert len(results(delivered)) == 1 and proc.stderr.closed.is_set()
    assert proc.cleaned and backend._proc is None


@pytest.mark.parametrize('error_type', [RuntimeError, asyncio.TimeoutError])
async def test_actual_engine_stderr_close_failure_retains_context_and_never_completes(cli, engine, monkeypatch, error_type):
    from dream.core.council_context import snapshot
    make, _ = cli
    engine.backend, proc = make([PARTIAL, SUCCESS])
    error = error_type('fixture Engine stderr close failure')
    proc.stderr = StderrCloseReader(error)
    monkeypatch.setattr(cli_agent, '_TERM_GRACE_S', .01)
    engine.working.log_turn('user', 'retained required user constraint')
    engine._pending_handoff = snapshot(engine.store, engine.session_id)
    delivered = []
    with pytest.raises(error_type) as caught:
        await collect(engine, delivered)
    assert caught.value is error and results(delivered) == []
    assert engine.turn_timing.summary()['outcome'] == 'error'
    assert [r.content for r in engine._pending_handoff.required] == [
        'retained required user constraint', 'fixture request',
    ]
    assert engine.session_tokens == 0 and engine.total_cost_usd == 0
    assert engine.runtime_meter.prompt_tokens == engine.runtime_meter.output_tokens == 0
    assert proc.cleaned and proc.stderr.closed.is_set() and engine.backend._proc is None


@pytest.mark.parametrize('primary_kind', ['stdout', 'process', 'stdout_and_process', 'cancel'])
@pytest.mark.parametrize('error_type', [RuntimeError, asyncio.TimeoutError])
async def test_primary_failure_survives_secondary_stderr_close_failure(cli, monkeypatch, primary_kind, error_type):
    make, _ = cli
    backend, proc = make([SUCCESS], held=primary_kind == 'cancel')
    primary = ValueError('fixture primary ' + primary_kind + ' failure')
    secondary = error_type('fixture secondary stderr close failure')
    proc.stderr = StderrCloseReader(secondary)
    monkeypatch.setattr(cli_agent, '_TERM_GRACE_S', .01)
    if primary_kind in ('stdout', 'stdout_and_process'):
        proc.stdout.chunks.append(primary)
    if primary_kind in ('process', 'stdout_and_process'):
        async def failed_wait():
            proc.cleaned = True
            if primary_kind == 'stdout_and_process':
                raise OSError('fixture secondary process cleanup failure')
            raise primary
        proc.wait = failed_wait
    delivered = []
    if primary_kind == 'cancel':
        task = asyncio.create_task(collect(backend, delivered))
        await asyncio.wait_for(proc.wait_started.wait(), 1)
        task.cancel()
        checkpoint = asyncio.Event()
        asyncio.get_running_loop().call_soon(checkpoint.set)
        await checkpoint.wait()
        task.cancel()
        proc.release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(task, 1)
    else:
        with pytest.raises(ValueError) as caught:
            await collect(backend, delivered)
        assert caught.value is primary
    notes = '\n'.join(getattr(caught.value, '__notes__', []))
    assert 'fixture secondary stderr close failure' in notes
    assert error_type.__name__ in notes
    if primary_kind == 'stdout_and_process':
        assert 'fixture secondary process cleanup failure' in notes
    assert results(delivered) == [] and proc.cleaned and proc.stderr.closed.is_set()
    assert backend._proc is None


@pytest.mark.parametrize('fault', ['none', 'stderr', 'stderr_timeout', 'process', 'both', 'both_timeout'])
async def test_aclose_exposes_cleanup_failure_without_publishing_pending_result(cli, monkeypatch, fault):
    make, _ = cli
    backend, proc = make([SUCCESS, PARTIAL])
    close_error = (asyncio.TimeoutError if 'timeout' in fault else RuntimeError)('fixture aclose stderr failure')
    process_error = ValueError('fixture aclose process failure')
    if fault in ('stderr', 'stderr_timeout', 'both', 'both_timeout'):
        proc.stderr = StderrCloseReader(close_error)
    if fault in ('process', 'both', 'both_timeout'):
        async def failed_wait():
            proc.cleaned = True
            raise process_error
        proc.wait = failed_wait
    monkeypatch.setattr(cli_agent, '_TERM_GRACE_S', .01)
    stream = backend.ask('fixture close request')
    assert (await asyncio.wait_for(anext(stream), 1)).kind == 'assistant_done'
    if fault == 'none':
        assert await asyncio.wait_for(stream.aclose(), 1) is None
    else:
        expected = process_error if fault in ('process', 'both', 'both_timeout') else close_error
        with pytest.raises(type(expected)) as caught:
            await asyncio.wait_for(stream.aclose(), 1)
        assert caught.value is expected
        if fault.startswith('both'):
            assert 'fixture aclose stderr failure' in '\n'.join(caught.value.__notes__)
    assert proc.cleaned and backend._proc is None
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    make([SUCCESS], backend=backend)
    assert results(await collect(backend))[0]['is_error'] is False


@pytest.mark.parametrize('cancel', [False, True])
@pytest.mark.parametrize('fault', ['none', 'stderr', 'process', 'both'])
async def test_aclose_held_cleanup_preserves_guard_and_repeated_cancellation(cli, monkeypatch, cancel, fault):
    make, _ = cli
    backend, proc = make([SUCCESS, PARTIAL])
    closing, release_close = asyncio.Event(), asyncio.Event()
    stderr_error = RuntimeError('fixture held aclose stderr failure')
    process_error = ValueError('fixture held aclose process failure')
    class HeldCloseReader(Reader):
        async def read(self, limit):
            try:
                await self.release.wait()
            finally:
                closing.set()
                await release_close.wait()
                self.closed.set()
                if fault in ('stderr', 'both'):
                    raise stderr_error
            return b''
    proc.stderr = HeldCloseReader(held=True)
    if fault in ('process', 'both'):
        async def failed_wait():
            proc.cleaned = True
            raise process_error
        proc.wait = failed_wait
    monkeypatch.setattr(cli_agent, '_TERM_GRACE_S', .01)
    stream = backend.ask('fixture held close request')
    assert (await asyncio.wait_for(anext(stream), 1)).kind == 'assistant_done'
    closer = asyncio.create_task(stream.aclose())
    try:
        await asyncio.wait_for(closing.wait(), 1)
        assert backend._proc is proc and not closer.done()
        with pytest.raises(RuntimeError, match='active turn'):
            await anext(backend.ask('must not start while closing'))
        if cancel:
            for _ in range(3):
                closer.cancel()
                checkpoint = asyncio.Event()
                asyncio.get_running_loop().call_soon(checkpoint.set)
                await checkpoint.wait()
                assert not closer.done() and backend._proc is proc
    finally:
        release_close.set()
    if cancel:
        with pytest.raises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(closer, 1)
        notes = '\n'.join(getattr(caught.value, '__notes__', []))
        if fault in ('stderr', 'both'):
            assert 'fixture held aclose stderr failure' in notes
        if fault in ('process', 'both'):
            assert 'fixture held aclose process failure' in notes
    elif fault == 'none':
        assert await asyncio.wait_for(closer, 1) is None
    else:
        expected = process_error if fault in ('process', 'both') else stderr_error
        with pytest.raises(type(expected)) as caught:
            await asyncio.wait_for(closer, 1)
        assert caught.value is expected
        if fault == 'both':
            assert 'fixture held aclose stderr failure' in '\n'.join(caught.value.__notes__)
    assert proc.cleaned and proc.stderr.closed.is_set() and backend._proc is None
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    make([SUCCESS], backend=backend)
    assert results(await collect(backend))[0]['is_error'] is False


@pytest.mark.parametrize('fault', ['none', 'stderr', 'process', 'both'])
async def test_actual_engine_aclose_reports_cleanup_failure_and_retains_required_context(cli, engine, monkeypatch, fault):
    from dream.core.council_context import snapshot
    make, _ = cli
    engine.backend, proc = make([SUCCESS, PARTIAL])
    stderr_error = RuntimeError('fixture Engine aclose stderr failure')
    process_error = ValueError('fixture Engine aclose process failure')
    if fault in ('stderr', 'both'):
        proc.stderr = StderrCloseReader(stderr_error)
    if fault in ('process', 'both'):
        async def failed_wait():
            proc.cleaned = True
            raise process_error
        proc.wait = failed_wait
    monkeypatch.setattr(cli_agent, '_TERM_GRACE_S', .01)
    engine.working.log_turn('user', 'required context before close')
    engine._pending_handoff = snapshot(engine.store, engine.session_id)
    stream = engine.ask('fixture Engine close request')
    # Keep Engine's context-manager entry and close in the same task.
    async with asyncio.timeout(2):
        for _ in range(5):
            event = await anext(stream)
            if event.kind == 'assistant_done':
                break
            assert event.kind == 'system'
        else:
            pytest.fail('Engine did not stream the fixture assistant text')
        if fault == 'none':
            assert await stream.aclose() is None
        else:
            expected = process_error if fault in ('process', 'both') else stderr_error
            with pytest.raises(type(expected)) as caught:
                await stream.aclose()
            assert caught.value is expected
            if fault == 'both':
                assert 'fixture Engine aclose stderr failure' in '\n'.join(caught.value.__notes__)
    assert engine.turn_timing.summary()['outcome'] == ('interrupted' if fault == 'none' else 'error')
    assert [r.content for r in engine._pending_handoff.required] == [
        'required context before close', 'fixture Engine close request',
    ]
    assert engine.session_tokens == 0 and engine.total_cost_usd == 0
    assert engine.runtime_meter.prompt_tokens == engine.runtime_meter.output_tokens == 0
    assert proc.cleaned and engine.backend._proc is None
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    make([SUCCESS], backend=engine.backend)
    assert results(await collect(engine))[0]['is_error'] is False
    assert engine.turn_timing.summary()['outcome'] == 'completed'
    assert engine._pending_handoff is None and engine.session_tokens == 18
