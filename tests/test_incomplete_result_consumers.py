"""Finite reported-incomplete consumer checks, without providers or process creation."""
import asyncio
from contextlib import aclosing
import copy
import json
import socket
import subprocess
from types import SimpleNamespace

import pytest

from dream.core.backends.base import Event
from dream.core.backends.grok_adapter import GrokAdapter
from dream.core.loop import AutonomousLoop
from dream.memory.embeddings import Embedder, Reranker
from dream.tui.app import App
from dream.workflows import WorkflowService
from test_council_handoff import engine
from test_durable_autonomy import Reviewer


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    attempts = []
    def blocked(*args, **kwargs):
        attempts.append('external I/O or model load')
        raise AssertionError(attempts[-1])
    for obj, name in ((socket.socket, 'connect'), (socket.socket, 'connect_ex'),
                      (subprocess.Popen, '__init__'), (Embedder, '_ensure'),
                      (Reranker, '_ensure')):
        monkeypatch.setattr(obj, name, blocked)
    for key in ('DREAM_EVALUATOR_PROVIDER', 'DREAM_EVALUATOR_MODEL', 'DREAM_EVALUATOR_TIMEOUT'):
        monkeypatch.delenv(key, raising=False)
    yield
    assert attempts == []


def grok_events(reason='MaxTurns', text='Partial findings.\nSTATUS: DONE'):
    adapter, state = GrokAdapter(), {}
    return (adapter.translate({'type': 'text', 'data': text}, state)
            + adapter.translate({'type': 'end', 'stopReason': reason,
                                 'usage': {'input_tokens': 7, 'output_tokens': 3},
                                 'total_cost_usd': 0.25}, state))


class FiniteBackend:
    def __init__(self, events, *, held=False):
        self.events = events
        self.prompts = []
        self.cleanup_entered = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False
        self.interrupts = 0
        if not held:
            self.release.set()

    async def ask(self, prompt):
        self.prompts.append(prompt)
        self.owner = asyncio.current_task()
        try:
            for event in self.events:
                yield event
        finally:
            self.cleanup_owner = asyncio.current_task()
            self.cleanup_entered.set()
            await self.release.wait()
            self.closed = True

    async def interrupt(self):
        self.interrupts += 1


# Different missing/null and falsey fields are deliberate compatibility controls.
CASES = [
    pytest.param({'subtype': 'MaxTurns', 'is_error': False}, 'incomplete', False, False, id='max-turns'),
    pytest.param({'subtype': 'length', 'is_error': False}, 'length', False, False, id='length'),
    pytest.param({'subtype': '', 'is_error': False}, 'incomplete', False, False, id='empty'),
    pytest.param({'subtype': False, 'is_error': None}, 'incomplete', False, False, id='false'),
    pytest.param({'subtype': 0, 'is_error': 0}, 'incomplete', False, False, id='zero'),
    pytest.param({'subtype': 'success', 'is_error': False}, 'completed', True, True, id='success'),
    pytest.param({'is_error': False}, 'completed', True, False, id='missing-subtype'),
    pytest.param({'subtype': None, 'is_error': False}, 'completed', True, False, id='null-subtype'),
    pytest.param({'subtype': 'MaxTurns'}, 'incomplete', False, False, id='missing-is-error'),
    pytest.param({'subtype': 'success', 'is_error': True}, 'error', False, False, id='failed-success'),
    pytest.param({'is_error': 'failure'}, 'error', False, False, id='truthy-error'),
    pytest.param({'subtype': 'length', 'is_error': True}, 'length', False, False, id='failed-length'),
]


def events_for(data):
    events = grok_events()
    events[-1].data = {**events[-1].data, **data}
    if 'subtype' not in data:
        events[-1].data.pop('subtype')
    if 'is_error' not in data:
        events[-1].data.pop('is_error')
    return events


async def pending_context(engine):
    engine.working.log_turn('user', 'REQUIRED: retain this goal')
    engine._pending_handoff = engine._council_transfer()
    await engine.record_council_results('check', [{'advisor': 'codex', 'answer': 'ADVICE: inspect partial effects'}])


@pytest.mark.parametrize('data,outcome,complete,ack', CASES)
async def test_engine_timing_accounting_and_strict_pending_context(engine, data, outcome, complete, ack):
    await pending_context(engine)
    events = events_for(data)
    original = copy.deepcopy(events[-1].data)
    engine.backend = FiniteBackend(events)
    observed = [event async for event in engine.ask('continue')]
    result = next(event for event in observed if event.kind == 'result')
    assert result is events[-1]
    assert {k: v for k, v in result.data.items() if k != 'stats'} == original
    assert result.data['stats']['timing']['outcome'] == outcome
    notices = [event.data for event in observed if event.kind == 'system' and 'Turn incomplete' in str(event.data)]
    assert len(notices) == int(outcome == 'incomplete')
    if notices:
        assert json.dumps(str(data['subtype'])[:120]) in notices[0]
        assert 'partial' in notices[0]
    assert engine.session_tokens == 10
    assert engine.total_cost_usd == 0.25
    assert engine.runtime_meter.prompt_tokens == 7 and engine.runtime_meter.output_tokens == 3
    assert engine.backend.closed and len(engine.backend.prompts) == 1
    assert 'ADVICE: inspect partial effects' in engine.backend.prompts[0]
    assert bool(engine._pending_council) is (not ack)
    assert (engine._pending_handoff is None) is ack
    logs = engine.store.session_turns(engine.session_id)
    assert sum(row['role'] == 'assistant' for row in logs) == 1
    assert next(e.data for e in observed if e.kind == 'assistant_done').endswith('STATUS: DONE')


@pytest.mark.parametrize('data,outcome,complete,ack', CASES)
async def test_loop_rejects_incomplete_before_review_and_retains_uncertainty(engine, tmp_path, data, outcome, complete, ack):
    engine.backend = FiniteBackend(events_for(data))
    reviews = []
    def reviewer(*args):
        reviews.append(args)
        return Reviewer()
    loop = AutonomousLoop(engine, state_dir=tmp_path / 'runs', evaluator_backend_factory=reviewer)
    result = await loop.run('fixture goal', acceptance_criteria='- fixture evidence')
    ledger = [json.loads(line) for line in (loop.workspace / 'ledger.jsonl').read_text().splitlines()]
    assert result.status == ('done' if complete else 'error')
    assert len(reviews) == int(complete)
    assert sum(row['event'] == 'worker_finished' for row in ledger) == int(complete)
    assert ledger[-1]['state']['uncertain'] is (not complete)
    assert len(engine.backend.prompts) == 1 and engine.backend.closed
    if not complete:
        if not data.get('is_error'):
            assert 'incomplete' in result.message.lower()
        resumed = await loop.run('fixture goal', resume_run_id=result.run_id)
        assert resumed.status == 'need_input'
        assert len(engine.backend.prompts) == 1


async def queued_task(app, tmp_path):
    svc = WorkflowService(tmp_path)
    task = svc.create('report', {'goal': 'Report fixture observations'})
    await svc.start(task['id'], 1, 'fixture-start', app._queue_workflow)
    queued = app._gui_prompts.get_nowait()
    assert svc.claim(task['id'], attempt=queued.workflow_attempt, expected_version=queued.workflow_version)
    output = tmp_path / task['output_path']
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('# Report\n\nHere are the observations supplied by this finite fixture.\n')
    return svc, task, queued


@pytest.mark.parametrize('data,outcome,complete,ack', CASES)
async def test_guided_stream_rejects_incomplete_even_with_valid_artifact(engine, tmp_path, monkeypatch, data, outcome, complete, ack):
    engine.backend = FiniteBackend(events_for(data))
    app = App(workspace=tmp_path, gui=False)
    app.engine = engine
    monkeypatch.setattr(app, '_render_event', lambda event: None)
    svc, task, queued = await queued_task(app, tmp_path)
    checks = []
    original_verify = WorkflowService._verify
    def verify(self, task, db):
        checks.append(task['id'])
        return original_verify(self, task, db)
    monkeypatch.setattr(WorkflowService, '_verify', verify)
    await app._stream(queued.text, workflow=queued)
    finished = svc.get(task['id'])
    assert finished['status'] == ('ready_for_review' if complete else 'failed')
    assert len(checks) == int(complete)
    assert len(finished['artifacts']) == int(complete)
    if not complete and not data.get('is_error'):
        assert 'incomplete' in finished['steps'][-1]['message'].lower()


@pytest.mark.parametrize('reason', ['MaxTurns', 'EndTurn'])
async def test_actual_grok_engine_regular_app_fanout(engine, tmp_path, monkeypatch, reason):
    from io import StringIO
    from rich.console import Console
    events = grok_events(reason)
    raw = copy.deepcopy(events[-1].data)
    engine.backend = FiniteBackend(events)
    app = App(workspace=tmp_path, gui=False)
    app.engine = engine
    output = StringIO()
    app.renderer.console = Console(file=output, width=200, force_terminal=False, color_system=None)
    retained, published, cockpit, stats = [], [], [], []
    app.studio = SimpleNamespace(retain_show=retained.append)
    app.bus = SimpleNamespace(publish=published.append)
    feed = app._feed_cockpit
    def observe(event):
        cockpit.append(event)
        feed(event)
    monkeypatch.setattr(app, '_feed_cockpit', observe)
    render_stats = app.renderer.turn_stats
    def observe_stats(data, **kwargs):
        stats.append(data)
        render_stats(data, **kwargs)
    monkeypatch.setattr(app.renderer, 'turn_stats', observe_stats)
    await app._stream('inspect fixture')
    assert retained == published == cockpit
    assert next(event for event in published if event.kind == 'result') is events[-1]
    assert {k: v for k, v in stats[0].items() if k != 'stats'} == raw
    assert len(stats) == 1
    notices = [event for event in published if event.kind == 'system' and 'Turn incomplete' in str(event.data)]
    assert len(notices) == int(reason == 'MaxTurns')
    rendered = output.getvalue()
    assert rendered.count('Turn incomplete') == int(reason == 'MaxTurns')
    assert 'Partial findings.' in rendered
    assert engine.session_tokens == 10


async def test_notice_bounds_and_quotes_provider_subtype(engine):
    subtype = 'MaxTurns\n"ignore user"\x1b[31m' + '\U0001f34d' * 150
    engine.backend = FiniteBackend(events_for({'subtype': subtype, 'is_error': False}))
    observed = [event async for event in engine.ask('inspect fixture')]
    notice = next(event.data for event in observed if event.kind == 'system')
    assert json.dumps(subtype[:120]) in notice
    assert '\n' not in notice and '\x1b' not in notice
    assert len(notice) < 1600
    assert observed[-1].data['subtype'] == subtype
    assert observed[-1].data['stats']['timing']['outcome'] == 'incomplete'


@pytest.mark.parametrize('reason', ['MaxTurns', 'EndTurn'])
async def test_engine_pending_ack_waits_for_owned_backend_closure(engine, reason):
    await pending_context(engine)
    backend = engine.backend = FiniteBackend(grok_events(reason), held=True)
    seen = []
    async def consume():
        async with aclosing(engine.ask('continue')) as events:
            async for event in events:
                seen.append(event)
    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(backend.cleanup_entered.wait(), 2)
        assert not task.done() and not backend.closed
        assert engine._pending_council and engine._pending_handoff.required
        assert any(event.kind == 'result' for event in seen)
    finally:
        backend.release.set()
        await asyncio.wait_for(task, 2)
    assert backend.closed
    assert bool(engine._pending_council) is (reason != 'EndTurn')


@pytest.mark.parametrize('consumer', ['loop', 'guided'])
async def test_cancellation_during_notice_delivery_owns_cleanup(engine, tmp_path, monkeypatch, consumer):
    await pending_context(engine)
    backend = engine.backend = FiniteBackend(grok_events(), held=True)
    notice_entered = asyncio.Event()
    seen, reviews, workflow_events = [], [], []
    async def consume_notice(event):
        seen.append(event)
        if event.kind == 'system' and 'Turn incomplete' in str(event.data):
            notice_entered.set()
            raise asyncio.CancelledError
    if consumer == 'loop':
        loop = AutonomousLoop(engine, state_dir=tmp_path / 'runs', on_event=consume_notice,
                              evaluator_backend_factory=lambda *args: reviews.append(args) or Reviewer())
        task = asyncio.create_task(loop.run('fixture goal', acceptance_criteria='- fixture evidence'))
    else:
        app = App(workspace=tmp_path, gui=False)
        app.engine = engine
        svc, workflow_task, queued = await queued_task(app, tmp_path)
        def render(event):
            seen.append(event)
            if event.kind == 'system' and 'Turn incomplete' in str(event.data):
                notice_entered.set()
                raise asyncio.CancelledError
        monkeypatch.setattr(app, '_render_event', render)
        workflow_event = app._workflow_event
        async def record(workflow, kind, detail=''):
            workflow_events.append(kind)
            await workflow_event(workflow, kind, detail)
        monkeypatch.setattr(app, '_workflow_event', record)
        task = asyncio.create_task(app._stream(queued.text, workflow=queued))
    try:
        await asyncio.wait_for(notice_entered.wait(), 2)
        await asyncio.wait_for(backend.cleanup_entered.wait(), 2)
        assert not task.done(), 'Consumer returned before its backend cleanup settled'
        assert not backend.closed
        assert engine._pending_council and engine._pending_handoff.required
        if consumer == 'loop':
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
    finally:
        backend.release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    assert backend.closed and backend.cleanup_owner is backend.owner
    assert not reviews and 'final' not in workflow_events
    assert not any(event.kind == 'result' for event in seen)
    assert engine.turn_timing.summary()['outcome'] != 'completed'
    if consumer == 'loop':
        rows = [json.loads(line) for line in (loop.workspace / 'ledger.jsonl').read_text().splitlines()]
        assert rows[-1]['state']['uncertain']
        assert not any(row['event'] == 'worker_finished' for row in rows)
    else:
        assert svc.get(workflow_task['id'])['status'] != 'ready_for_review'


@pytest.mark.parametrize('data,outcome,complete,ack', CASES)
async def test_consolidation_keeps_incomplete_summary_notes_and_conflicts(engine, monkeypatch, data, outcome, complete, ack):
    engine.store.add_note(engine.session_id, 'source note requiring consolidation')
    a = engine.store.upsert_memory('semantic', 'Fixture A', 'First observation', slug='fixture-a')
    b = engine.store.upsert_memory('semantic', 'Fixture B', 'Second observation', slug='fixture-b')
    # Supply a finite conflict scan without embedding; retirement is the actual store method.
    monkeypatch.setattr(engine.store, 'find_conflicts', lambda: [[a, b]])
    reconciled = []
    mark = engine.store.mark_reconciled
    def record(slugs):
        reconciled.append(slugs)
        return mark(slugs)
    monkeypatch.setattr(engine.store, 'mark_reconciled', record)
    events = events_for(data)
    events[1].data = 'SUMMARY: fixture summary'
    engine.backend = FiniteBackend(events)
    summary = await engine.consolidate()
    assert summary == ('fixture summary' if complete else None)
    assert len(reconciled) == int(complete)
    assert len(engine.store.session_notes(engine.session_id, only_unconsolidated=True)) == int(not complete)
    assert len(engine.store.all_memories(kind='episodic')) == int(complete)
    assert engine.store.get_session(engine.session_id)['summary'] == summary
    assert engine.backend.closed and len(engine.backend.prompts) == 1


@pytest.mark.parametrize('consumer', ['engine', 'loop', 'guided'])
@pytest.mark.parametrize('prefix,reason,complete', [
    pytest.param([Event('error', 'fatal fixture failure')], 'EndTurn', False, id='fatal-then-success'),
    pytest.param([Event('error', 'fatal fixture failure')], 'MaxTurns', False, id='fatal-then-incomplete'),
    pytest.param([Event('tool_result', {'name': 'fixture_read', 'content': 'recoverable fixture error', 'is_error': True}),
                  Event('system', 'Recovered fixture tool error.')], 'EndTurn', True, id='recoverable-then-success'),
])
async def test_fatal_and_recoverable_event_controls(engine, tmp_path, monkeypatch, consumer, prefix, reason, complete):
    await pending_context(engine)
    engine.backend = FiniteBackend(copy.deepcopy(prefix) + grok_events(reason))
    if consumer == 'engine':
        events = [event async for event in engine.ask('continue')]
        result = next(event for event in events if event.kind == 'result')
        assert result.data['stats']['timing']['outcome'] == ('completed' if complete else 'error')
        assert bool(engine._pending_council) is (not complete)
    elif consumer == 'loop':
        reviews = []
        loop = AutonomousLoop(engine, state_dir=tmp_path / 'runs',
                              evaluator_backend_factory=lambda *args: reviews.append(args) or Reviewer())
        result = await loop.run('fixture goal', acceptance_criteria='- fixture evidence')
        assert result.status == ('done' if complete else 'error')
        assert len(reviews) == int(complete)
    else:
        app = App(workspace=tmp_path, gui=False)
        app.engine = engine
        monkeypatch.setattr(app, '_render_event', lambda event: None)
        svc, task, queued = await queued_task(app, tmp_path)
        await app._stream(queued.text, workflow=queued)
        assert svc.get(task['id'])['status'] == ('ready_for_review' if complete else 'failed')
    assert engine.backend.closed and len(engine.backend.prompts) == 1


@pytest.mark.parametrize('consumer', ['loop', 'guided'])
@pytest.mark.parametrize('reason', ['MaxTurns', 'EndTurn'])
async def test_terminal_cleanup_barrier_precedes_completion(engine, tmp_path, monkeypatch, consumer, reason):
    await pending_context(engine)
    backend = engine.backend = FiniteBackend(grok_events(reason), held=True)
    reviews, workflow_events = [], []
    if consumer == 'loop':
        loop = AutonomousLoop(engine, state_dir=tmp_path / 'runs',
                              evaluator_backend_factory=lambda *args: reviews.append(args) or Reviewer())
        task = asyncio.create_task(loop.run('fixture goal', acceptance_criteria='- fixture evidence'))
    else:
        app = App(workspace=tmp_path, gui=False)
        app.engine = engine
        monkeypatch.setattr(app, '_render_event', lambda event: None)
        svc, workflow_task, queued = await queued_task(app, tmp_path)
        workflow_event = app._workflow_event
        async def record(workflow, kind, detail=''):
            workflow_events.append(kind)
            await workflow_event(workflow, kind, detail)
        monkeypatch.setattr(app, '_workflow_event', record)
        task = asyncio.create_task(app._stream(queued.text, workflow=queued))
    try:
        await asyncio.wait_for(backend.cleanup_entered.wait(), 2)
        assert not task.done() and not backend.closed
        assert not reviews and 'final' not in workflow_events
        assert engine._pending_council and engine._pending_handoff.required
        if consumer == 'loop':
            rows = [json.loads(line) for line in (loop.workspace / 'ledger.jsonl').read_text().splitlines()]
            assert rows[-1]['state']['phase'] == 'worker_running'
            assert not any(row['event'] == 'worker_finished' for row in rows)
    finally:
        backend.release.set()
        result = await asyncio.wait_for(task, 2)
    assert backend.closed
    if consumer == 'loop':
        assert result.status == ('done' if reason == 'EndTurn' else 'error')
    else:
        assert svc.get(workflow_task['id'])['status'] == ('ready_for_review' if reason == 'EndTurn' else 'failed')


async def test_recovery_report_survives_new_incomplete_turn_without_clearing_uncertainty(engine, tmp_path):
    backend = engine.backend = FiniteBackend(grok_events())
    reviews = []
    loop = AutonomousLoop(engine, state_dir=tmp_path / 'runs',
                          evaluator_backend_factory=lambda *args: reviews.append(args) or Reviewer())
    first = await loop.run('fixture goal', acceptance_criteria='- fixture evidence')
    note = '  CALLER: prior fixture action was inspected. Keep the source note.  '
    second = await loop.run('fixture goal', resume_run_id=first.run_id, resume_note=note)
    assert first.status == second.status == 'error'
    assert len(backend.prompts) == 2 and not reviews
    marker = 'Run reconciliation caller reports (JSON):\n'
    reports = json.loads(backend.prompts[-1].split(marker, 1)[1])
    assert len(reports) == 1 and reports[0]['text'] == note
    rows = [json.loads(line) for line in (loop.workspace / 'ledger.jsonl').read_text().splitlines()]
    submitted = [row for row in rows if row['event'] == 'resume_requested']
    assert len(submitted) == 1 and submitted[0]['data']['note'] == note
    assert rows[-1]['state']['uncertain']
    assert not any(row['event'] == 'worker_finished' for row in rows)
    third = await loop.run('fixture goal', resume_run_id=first.run_id)
    assert third.status == 'need_input' and len(backend.prompts) == 2
