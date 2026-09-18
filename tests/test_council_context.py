"""Council transfer through real Engine lifecycle and HTTP admission, no providers."""
from dataclasses import replace
import asyncio
import json
import sqlite3

import httpx
import pytest

from dream.core import engine as mod
from dream.core.backends.base import Event
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.moe import MoeConfig
from dream.core.profiles import PROFILES
from test_council_handoff import engine, Backend
from test_compaction import _FakeClient, _text_round


@pytest.fixture(autouse=True)
def _block_live_io_and_models(monkeypatch):
    import socket
    import subprocess
    from dream.memory.embeddings import Embedder, Reranker
    attempts = []
    def blocked(*args, **kwargs):
        attempts.append("unexpected live I/O or model load")
        raise AssertionError(attempts[-1])
    for obj, name in ((socket.socket, 'connect'), (socket.socket, 'connect_ex'),
                      (subprocess.Popen, '__init__'), (Embedder, '_ensure'), (Reranker, '_ensure')):
        monkeypatch.setattr(obj, name, blocked)
    yield
    assert attempts == []


@pytest.fixture
def http_handoff(engine, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('unexpected real HTTP client')
    monkeypatch.setattr(httpx.AsyncClient, '__init__', blocked)
    monkeypatch.setattr(mod.system_prompt, 'build_system_prompt', lambda *args, **kwargs: 'Fixture instructions')
    original = mod.get_provider
    monkeypatch.setattr(mod, 'get_provider', lambda key: replace(original(key), base_url='https://fixture.invalid/v1'))
    clients = []
    async def connect(backend):
        backend._client = _FakeClient([_text_round('answer')])
        clients.append(backend._client)
    monkeypatch.setattr(OpenAICompatBackend, 'connect', connect)
    engine.backend = Backend('original', [])
    async def handoff(window=16384, model='new-model'):
        monkeypatch.setattr(mod, 'resolve_profile', lambda *args, **kwargs: replace(PROFILES['lean'], context_limit=window))
        await engine.configure_council(MoeConfig('openai', [], orchestrator_effort='high'), model=model)
        return engine.backend
    handoff.clients = clients
    return handoff


def log_receipt(engine, content):
    received = []
    assert engine.working.log_turn('user', content, on_commit=received.append) is None
    assert len(received) == 1
    return received[0]


async def collect(engine, text='continue'):
    return [event async for event in engine.ask(text)]


async def test_handoff_keeps_complete_latest_user_tail(engine, http_handoff):
    request = 'Task material ' + 'A' * 4000 + ' LAST_CONSTRAINT: draft only, do not send.'
    engine.working.log_turn('user', request)
    engine.working.log_turn('assistant', 'Understood')
    backend = await http_handoff()
    await collect(engine)
    sent = backend._client.payloads[0]['messages']
    assert request in '\n'.join(m.get('content') or '' for m in sent)
    assert backend._client.payloads[0]['model'] == 'new-model'
    assert backend._client.payloads[0]['reasoning_effort'] == 'high'


async def test_short_latest_goal_reserved_before_assistant_budget(engine, http_handoff):
    engine.working.log_turn('user', 'LATEST_GOAL: write draft only')
    for _ in range(4):
        engine.working.log_turn('assistant', 'A' * 2990)
    backend = await http_handoff(2048)
    events = await collect(engine)
    assert len(backend._client.payloads) == 1
    assert not next(e.data for e in events if e.kind == 'result')['is_error']
    assert 'LATEST_GOAL: write draft only' in str(backend._client.payloads)


async def test_long_first_advisor_cannot_erase_second_advisor(engine, http_handoff):
    await engine.record_council_results('choose', [
        {'advisor': 'codex', 'model': 'a', 'effort': 'high', 'answer': 'A' * 12500},
        {'advisor': 'gemini', 'model': 'b', 'effort': None, 'answer': 'DISSENT_STOP: reject plan'},
    ])
    backend = await http_handoff()
    await collect(engine)
    assert 'DISSENT_STOP: reject plan' in str(backend._client.payloads)


def test_log_turn_delivers_own_receipt_with_fts_and_other_connection_insert(engine, monkeypatch):
    other = sqlite3.connect(engine.store._conn.execute('PRAGMA database_list').fetchone()[2])
    original = engine.store.add_turn
    def racing_insert(*args, **kwargs):
        turn_id = original(*args, **kwargs)
        other.execute('INSERT INTO turns(session_id, role, content, ts) VALUES (?,?,?,?)',
                      (engine.session_id, 'user', 'other connection', 'fixture'))
        other.commit()
        return turn_id
    monkeypatch.setattr(engine.store, 'add_turn', racing_insert)
    try:
        turn_id = log_receipt(engine, 'own exact text')
        row = engine.store._conn.execute('SELECT content FROM turns WHERE id=?', (turn_id,)).fetchone()
        assert row is not None and row[0] == 'own exact text'
        assert engine.store._conn.execute('SELECT COUNT(*) FROM turns_fts').fetchone()[0] == 2
        assert json.loads(engine.working.log_path.read_text().splitlines()[-1])['content'] == 'own exact text'
    finally:
        other.close()


@pytest.mark.parametrize('ending', ['error', 'length', 'eof', 'cancel', 'success_then_error',
                                   'success_then_exception', 'success_then_cancel'])
async def test_partial_turn_does_not_acknowledge_pending_sources(engine, http_handoff, monkeypatch, ending):
    engine.working.log_turn('user', 'ORIGINAL_REQUIRED: do not send')
    await engine.record_council_results('check', [{'advisor': 'codex', 'answer': 'advice'}])
    backend = await http_handoff()
    async def partial(prompt):
        yield Event('assistant_done', 'partial work')
        if ending.startswith('success_then_'):
            yield Event('result', {'is_error': False, 'subtype': 'success'})
        if ending in ('error', 'success_then_error'):
            yield Event('error', 'failed after partial work')
        elif ending == 'length':
            yield Event('result', {'is_error': False, 'subtype': 'length'})
        elif ending in ('cancel', 'success_then_cancel'):
            raise asyncio.CancelledError
        elif ending == 'success_then_exception':
            raise RuntimeError('iterator cleanup failed')
    monkeypatch.setattr(backend, 'ask', partial)
    if ending in ('cancel', 'success_then_cancel'):
        with pytest.raises(asyncio.CancelledError):
            await collect(engine)
    elif ending == 'success_then_exception':
        with pytest.raises(RuntimeError, match='iterator cleanup failed'):
            await collect(engine)
    else:
        await collect(engine)
    assert engine._pending_handoff.required[0].content == 'ORIGINAL_REQUIRED: do not send'
    assert engine._pending_council[0].role == 'council'


async def test_required_preflight_refusal_restores_same_scope_selection(engine, http_handoff):
    from dream.core.context_budget import ContextOverflow
    old = engine.backend
    await old.connect()
    engine.working.log_turn('user', 'DO_NOT_TRUNCATE ' + '界' * 8000)
    try:
        with pytest.raises(ContextOverflow, match='Required prior user input'):
            await http_handoff(2048)
        assert engine.backend is old and engine.model == 'original-model'
        assert engine.provider.key == 'machx' and engine._backend_available
        assert [kind for kind, *_ in old.events] == ['connect', 'disconnect', 'connect']
        assert all(task is asyncio.current_task() for _, _, task in old.events)
        assert engine._pending_handoff.required[0].content.endswith('界' * 8000)
        assert http_handoff.clients[-1].payloads == []
    finally:
        await old.disconnect()


@pytest.mark.parametrize('window', [2048, 16384])
async def test_history_projection_uses_real_destination_and_observes_omissions(engine, http_handoff, window):
    engine.working.log_turn('user', 'Keep exact goal')
    for n in range(4):
        engine.working.log_turn('assistant', f'answer {n}: ' + '界' * 2990)
    backend = await http_handoff(window)
    events = await collect(engine, 'CURRENT_EXACT')
    assert len(backend._client.payloads) == 1
    assert backend.context_report['admitted'] and backend.context_report['window'] == window
    messages = backend._client.payloads[0]['messages']
    assert messages[-1]['content'].startswith('CURRENT_EXACT\n\n[Current Dream Council')
    prior = [m for m in messages if m.get('name') == 'dream_handoff_user']
    assert len(prior) == 1 and prior[0]['content'].endswith('Keep exact goal')
    assert not any('Keep exact goal' in m['content'] for m in messages if m.get('name') == 'dream_council_context')
    if window == 2048:
        assert any('omitted' in str(e.data) for e in events if e.kind == 'system')


async def test_whole_consultation_omission_lists_every_advisor_and_keeps_source(engine, http_handoff):
    rows = [{'advisor': name, 'model': 'MODEL_' + 'M' * 1200, 'effort': 'high',
             'answer': 'do not rubber stamp', 'unavailable': True, 'isolation': {'read_only': True}}
            for name in ('codex', 'gemini', 'anthropic')]
    await engine.record_council_results('check', rows)
    record = engine._pending_council[0]
    backend = await http_handoff(2048)
    events = await collect(engine)
    assert len(backend._client.payloads) == 1
    assert not any(m.get('name') == 'dream_council_context' for m in backend._client.payloads[0]['messages'])
    notice = next(str(e.data) for e in events if e.kind == 'system' and 'Council context omitted' in str(e.data))
    for name in ('codex', 'gemini', 'anthropic'):
        assert name in notice
    assert str(record.turn) in notice and record.session in notice
    stored = engine.store._conn.execute('SELECT content FROM turns WHERE id=?', (record.turn,)).fetchone()[0]
    assert json.loads(stored)['advisors'] == rows


async def test_multiple_consultations_preserve_metadata_and_partial_status(engine, http_handoff):
    for n in (1, 2):
        await engine.record_council_results(f'question {n}', [
            {'advisor': 'codex', 'model': 'first', 'effort': 'high', 'answer': 'HEAD ' + 'x' * 13000 + ' TAIL',
             'isolation': {'memory': 'isolated'}, 'is_error': True},
            {'advisor': 'gemini', 'model': None, 'effort': None, 'answer': f'DISSENT_{n}'},
        ])
    backend = await http_handoff(16384)
    events = await collect(engine)
    bodies = [json.loads(m['content']) for m in backend._client.payloads[0]['messages']
              if m.get('name') == 'dream_council_context']
    assert [b['question'] for b in bodies] == ['question 1', 'question 2']
    for n, body in enumerate(bodies, 1):
        first, second = body['advisors']
        assert first['advisor'] == 'codex' and first['model'] == 'first' and first['effort'] == 'high'
        assert first['isolation'] == {'memory': 'isolated'} and first['is_error'] is True
        assert first['answer_status'] == 'partial' and first['omitted_chars'] > 0
        assert 'HEAD ' in first['answer'] and ' TAIL' in first['answer']
        assert second['answer'] == f'DISSENT_{n}' and second['model'] is None and second['effort'] is None
        assert 'unreported' in second['model_selection']
    assert any('omitted dissent is unknown' in str(e.data) for e in events if e.kind == 'system')


async def test_pending_sources_survive_refused_ask_retry_and_second_handoff(engine, http_handoff):
    from test_compaction import _sse
    engine.working.log_turn('user', 'ORIGINAL_DO_NOT_SEND')
    await engine.record_council_results('question', [{'advisor': 'codex', 'answer': 'original advice'}])
    first = await http_handoff(4096)
    first._client = _FakeClient([[_sse({'error': {'message': 'fixture server refusal'}})]])
    events = await collect(engine, 'continue one')
    assert next(e.data for e in events if e.kind == 'result')['is_error']
    await collect(engine, 'continue two')
    assert sum(m.get('name') == 'dream_handoff_user' for m in first.messages) == 2
    assert sum(m.get('name') == 'dream_council_context' for m in first.messages) <= 1
    assert engine._pending_handoff.required[0].content == 'ORIGINAL_DO_NOT_SEND'
    second = await http_handoff(4096, model='second-model')
    assert [r.content for r in engine._pending_handoff.required] == ['ORIGINAL_DO_NOT_SEND', 'continue one', 'continue two']
    await collect(engine, 'CURRENT_NEW')
    sent = second._client.payloads[0]['messages']
    assert any(m.get('name') == 'dream_handoff_user' and m['content'].endswith('ORIGINAL_DO_NOT_SEND') for m in sent)
    assert any(m.get('name') == 'dream_handoff_user' and m['content'].endswith('continue two') for m in sent)
    assert engine._pending_handoff is None and engine._pending_council == ()
    await collect(engine, 'next')
    assert sum(m.get('name') in ('dream_prior_user', 'dream_handoff_user') for m in second.messages) == 3


async def test_failed_new_user_constraint_survives_later_continue(engine, http_handoff):
    from test_compaction import _sse
    engine.working.log_turn('user', 'ORIGINAL_DO_NOT_SEND')
    backend = await http_handoff(4096)
    backend._client = _FakeClient([[_sse({'error': {'message': 'fixture refusal'}})]])
    await collect(engine, 'NEW_CONSTRAINT: do not delete files')
    await collect(engine, 'continue')
    assert 'NEW_CONSTRAINT: do not delete files' in [r.content for r in engine._pending_handoff.required]
    second = await http_handoff(4096, model='second-model')
    source_ids = [r.turn for r in engine._pending_handoff.required]
    assert len(source_ids) == len(set(source_ids)) == 3
    await collect(engine, 'continue with all constraints')
    assert 'NEW_CONSTRAINT: do not delete files' in str(second._client.payloads)


async def test_actual_new_request_overflow_retains_required_and_sends_nothing(engine, http_handoff):
    engine.working.log_turn('user', 'PRIOR_REQUIRED')
    backend = await http_handoff(2048)
    current = 'CURRENT ' + '界' * 3000
    events = await collect(engine, current)
    assert backend._client.payloads == []
    assert any('Required prior user input plus the current request' in str(e.data) for e in events if e.kind == 'error')
    assert backend.messages[-1]['content'].startswith(current)
    assert any(m.get('name') == 'dream_handoff_user' and m['content'].endswith('PRIOR_REQUIRED') for m in backend.messages)
    assert engine._pending_handoff is not None


async def test_native_boundary_keeps_string_api_exact_user_and_unknown_fit(engine, monkeypatch):
    original = '用户 ' + 'R' * 5000 + ' DO_NOT_SEND'
    engine.working.log_turn('user', original)
    events = []
    native = Backend('native', events)
    engine.backend = Backend('old', events)
    async def create():
        native.set_effort(engine.effort)
        return native
    monkeypatch.setattr(engine, '_create_backend', create)
    try:
        await engine.configure_council(MoeConfig('anthropic', [], orchestrator_effort='max'), model='native-model')
        observed = await collect(engine, 'new current')
        assert isinstance(native.prompts[0], str) and original in native.prompts[0]
        assert native.prompts[0].startswith('new current')
        assert any('private-context fit is unknown' in str(e.data) for e in observed if e.kind == 'system')
        assert engine.model == 'native-model' and engine.effort == native.effort == 'max'
        assert engine._pending_handoff is None
    finally:
        await native.disconnect()


@pytest.mark.parametrize('rows', [[None, 'malformed', {'advisor': 'codex', 'answer': {'error': 'unavailable'}}],
                                 [{'advisor': 'codex', 'answer': None, 'status': 'unavailable'}]])
async def test_malformed_result_rows_remain_visible_and_valid_json(engine, http_handoff, rows):
    await engine.record_council_results('check', rows)
    backend = await http_handoff()
    await collect(engine)
    bodies = [json.loads(m['content']) for m in backend._client.payloads[0]['messages']
              if m.get('name') == 'dream_council_context']
    assert len(bodies[0]['advisors']) == len(rows)
    assert bodies[0]['advisors'][-1]['advisor'] == 'codex'
    assert bodies[0]['advisors'][-1]['malformed_answer']


def test_zero_optional_room_emits_manifest_without_residual_model_marker():
    from dream.core.council_context import Record, Transfer, project
    record = Record('session', 41, 'council', json.dumps({'question': 'q', 'advisors': [
        {'advisor': 'codex', 'answer': 'yes'}, {'advisor': 'gemini', 'answer': 'no'}]}), 0)
    selected, notices = project(Transfer(consultations=(record,)), lambda messages: not messages)
    assert selected == []
    assert len(notices) == 1 and all(x in notices[0] for x in ('codex', 'gemini', 'session', '41', 'omitted'))


async def test_near_admission_boundary_omits_advice_without_blocking_current(engine, http_handoff):
    await engine.record_council_results('q', [{'advisor': 'codex', 'answer': 'optional'}])
    backend = await http_handoff(2048)
    current = 'X' * 4800
    events = await collect(engine, current)
    assert len(backend._client.payloads) == 1
    assert backend._client.payloads[0]['messages'][-1]['content'].startswith(current)
    assert not any(m.get('name') == 'dream_council_context' for m in backend._client.payloads[0]['messages'])
    assert any('codex' in str(e.data) and 'omitted' in str(e.data) for e in events if e.kind == 'system')


async def test_preflight_counts_real_tool_schemas_before_switching(engine, http_handoff):
    from types import SimpleNamespace
    from dream.core.context_budget import ContextOverflow
    async def never_run(args):
        raise AssertionError('preflight ran a tool')
    engine._built_tools['tools'] = [SimpleNamespace(name='write_file', description='D' * 10000,
        input_schema={'type': 'object', 'properties': {}}, handler=never_run)]
    old = engine.backend
    await old.connect()
    try:
        with pytest.raises(ContextOverflow, match='Required prior user input'):
            await http_handoff(2048)
        assert engine.backend is old
        assert http_handoff.clients[-1].payloads == []
    finally:
        await old.disconnect()


async def test_actual_sdk_query_keeps_native_options_and_transferred_user(engine, http_handoff, monkeypatch):
    from dream.core.backends import anthropic
    from claude_agent_sdk import AssistantMessage, TextBlock, ResultMessage
    captured = []
    class SDK:
        def __init__(self, options):
            self.options = options
        async def connect(self):
            captured.append(self.options)
        async def disconnect(self):
            pass
        async def query(self, prompt):
            captured.append(prompt)
        async def receive_response(self):
            yield AssistantMessage(content=[TextBlock(text='native answer')], model='native-model')
            yield ResultMessage(subtype='success', duration_ms=0, duration_api_ms=0,
                is_error=False, num_turns=1, session_id='fake-native', usage={})
    monkeypatch.setattr(anthropic, 'ClaudeSDKClient', SDK)
    prior = 'NATIVE_REQUIRED ' + '界' * 4000
    engine.working.log_turn('user', prior)
    await engine.configure_council(MoeConfig('anthropic', [], orchestrator_effort='max'), model='native-model')
    try:
        events = await collect(engine)
        assert captured[0].model == 'native-model' and captured[0].effort == 'max'
        assert isinstance(captured[1], str) and prior in captured[1]
        assert any('fit is unknown' in str(e.data) for e in events if e.kind == 'system')
        assert engine._pending_handoff is None
    finally:
        await engine.backend.disconnect()


@pytest.mark.parametrize('fault', ['timing', 'meter', 'emit', 'bridge', 'bridge_cancel', 'foreground', 'none'])
async def test_outer_cleanup_preserves_sources_through_retry_and_second_handoff(
        engine, http_handoff, monkeypatch, fault):
    from types import SimpleNamespace
    from dream.telemetry.runtime import RunMeter
    from dream.telemetry.turn import TurnTiming
    from test_compaction import _sse

    original = 'ORIGINAL exact ' + '界' * 3100 + ' DO_NOT_SEND'
    source_id = log_receipt(engine, original)
    await engine.record_council_results('check', [{'advisor': 'codex', 'answer': 'retained advice'}])
    backend = await http_handoff(16384)
    acknowledgment = backend.acknowledge_council_context
    acknowledgments = []
    def acknowledge():
        acknowledgments.append('ack')
        acknowledgment()
    monkeypatch.setattr(backend, 'acknowledge_council_context', acknowledge)
    observed = []
    def fail(*args, **kwargs):
        if fault == 'bridge_cancel':
            raise asyncio.CancelledError('owned bridge canceled')
        raise RuntimeError('owned cleanup ' + fault)
    with monkeypatch.context() as patch:
        if fault == 'timing':
            finish = TurnTiming.finish
            def timing_finish(timing, *args, **kwargs):
                if any(e.kind == 'result' for e in observed):
                    fail()
                return finish(timing, *args, **kwargs)
            patch.setattr(TurnTiming, 'finish', timing_finish)
        elif fault == 'meter':
            record = RunMeter.record
            def meter_record(meter, kind, **kwargs):
                if kind == 'turn_timing':
                    fail()
                return record(meter, kind, **kwargs)
            patch.setattr(RunMeter, 'record', meter_record)
        elif fault == 'emit':
            patch.setattr(engine, 'emit', lambda event: fail() if event.kind == 'turn_timing' else None)
        elif fault in ('bridge', 'bridge_cancel'):
            async def close():
                fail()
            patch.setattr(engine, '_tool_bridge', SimpleNamespace(cancel_pending=close))
        elif fault == 'foreground':
            patch.setattr(backend, 'foreground_finished', fail)
        async def consume():
            async for event in engine.ask('NEW constraint\n  do not delete  '):
                observed.append(event)
        if fault == 'none':
            await consume()
        else:
            with pytest.raises(asyncio.CancelledError if fault == 'bridge_cancel' else RuntimeError):
                await consume()
    assert any(e.kind == 'result' and e.data.get('subtype') == 'success' for e in observed)
    assert len(backend._client.payloads) == 1
    if fault == 'none':
        assert engine._pending_handoff is None and engine._pending_council == ()
        assert acknowledgments == ['ack']
        assert not any(m.get('name') == 'dream_handoff_user' for m in backend.messages)
        assert len([m for m in backend._client.payloads[0]['messages']
                    if m.get('name') == 'dream_handoff_user']) == 1
        return
    assert acknowledgments == []
    assert engine._pending_handoff is not None
    assert [r.content for r in engine._pending_handoff.required] == [original, 'NEW constraint\n  do not delete  ']
    assert engine._pending_council
    assert any(m.get('name') == 'dream_handoff_user' for m in backend.messages)
    backend._client = _FakeClient([[_sse({'error': {'message': 'retry refusal'}})]])
    await collect(engine, 'continue')
    required = engine._pending_handoff.required
    assert required[0].turn == source_id and len({r.turn for r in required}) == 3
    second = await http_handoff(16384, model='second-after-cleanup')
    await collect(engine, 'next exact')
    sent = [m for m in second._client.payloads[0]['messages'] if m.get('name') == 'dream_handoff_user']
    assert len(sent) == len(required)
    for record in required:
        assert any(m['content'].endswith(record.content) and
                   json.dumps({'session': record.session, 'turn': record.turn}) in m['content'] for m in sent)
    assert engine._pending_handoff is None and engine._pending_council == ()


async def test_real_background_preparation_cancel_retains_attempted_raw_user(engine, http_handoff):
    from test_compaction import _sse
    engine.working.log_turn('user', 'ORIGINAL_REQUIRED')
    backend = await http_handoff()
    backend.enable_background_filing(engine._background_event)
    queue = backend._idle_work
    queue.idle_grace = 0
    running, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def owned_job():
        running.set()
        try:
            await asyncio.Future()
        finally:
            closing.set()
            await release.wait()
    assert queue.enqueue(owned_job, label='no-IO fixture')
    await asyncio.wait_for(running.wait(), 1)
    raw = 'NEW exact constraint\n  no deletion 日本語  '
    caller = asyncio.create_task(collect(engine, raw))
    try:
        await asyncio.wait_for(closing.wait(), 1)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
    finally:
        release.set()
        await queue.close()
    assert backend._client.payloads == []
    assert engine._turn_index == 0
    stored = engine.store._conn.execute("SELECT id, content FROM turns WHERE role='user' ORDER BY id").fetchall()
    assert [r[1] for r in stored] == ['ORIGINAL_REQUIRED', raw]
    assert [(r.turn, r.content) for r in engine._pending_handoff.required] == [tuple(r) for r in stored]
    backend._client = _FakeClient([[_sse({'error': {'message': 'retry refusal'}})]])
    await collect(engine, 'continue')
    second = await http_handoff(model='after-preparation-cancel')
    await collect(engine, 'continue again')
    messages = second._client.payloads[0]['messages']
    assert any(m.get('name') == 'dream_handoff_user' and m['content'].endswith(raw) for m in messages)
    assert engine._pending_handoff is None
    stored = engine.store._conn.execute("SELECT content FROM turns WHERE role='user' ORDER BY id").fetchall()
    assert [r[0] for r in stored] == ['ORIGINAL_REQUIRED', raw, 'continue', 'continue again']


@pytest.mark.parametrize('stop_at', ['assistant_done', 'result'])
async def test_consumer_close_before_engine_exhaustion_keeps_transfer(engine, http_handoff, stop_at):
    engine.working.log_turn('user', 'REQUIRED until clean exhaustion')
    await engine.record_council_results('check', [{'advisor': 'codex', 'answer': 'advice'}])
    backend = await http_handoff()
    events = engine.ask('CURRENT exact')
    async for event in events:
        if event.kind == stop_at:
            break
    await events.aclose()
    assert engine._pending_handoff is not None and engine._pending_council
    assert any(m.get('name') == 'dream_handoff_user' for m in backend.messages)


@pytest.mark.parametrize('cancellations', [1, 3])
@pytest.mark.parametrize('append_failure', [False, True])
async def test_pending_capture_cancellation_waits_for_ordinary_storage_thread(
        engine, http_handoff, monkeypatch, cancellations, append_failure):
    import anyio
    import threading

    engine.working.log_turn('user', 'ORIGINAL_REQUIRED')
    backend = await http_handoff()
    limiter = anyio.to_thread.current_default_thread_limiter()
    old_tokens = limiter.total_tokens
    limiter.total_tokens = 1
    entered, release = threading.Event(), threading.Event()
    def occupy_worker():
        entered.set()
        assert release.wait(3), 'fixture worker release timed out'
    blocker = asyncio.create_task(anyio.to_thread.run_sync(occupy_worker))
    caller = None
    raw = 'NEW constraint while storage busy'
    if append_failure:
        from pathlib import Path
        real_open = Path.open
        def failed_append(path, *args, **kwargs):
            if path == engine.working.log_path and args and args[0] == 'a':
                raise OSError('fixture canceled transcript append failure')
            return real_open(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', failed_append)
    try:
        async with asyncio.timeout(2):
            while not entered.is_set():
                await asyncio.sleep(0)
            caller = asyncio.create_task(collect(engine, raw))
            while limiter.statistics().tasks_waiting == 0:
                await asyncio.sleep(0)
            for _ in range(cancellations):
                caller.cancel()
                await asyncio.sleep(0)
            release.set()
            with pytest.raises(asyncio.CancelledError) as canceled:
                await caller
            if append_failure:
                assert any('transcript append failure' in note for note in canceled.value.__notes__)
            await blocker
        stored = engine.store._conn.execute("SELECT content FROM turns WHERE role='user' ORDER BY id").fetchall()
        assert [r[0] for r in stored] == ['ORIGINAL_REQUIRED', raw]
        assert [r.content for r in engine._pending_handoff.required] == ['ORIGINAL_REQUIRED', raw]
        assert backend._client.payloads == [] and engine._turn_index == 0
    finally:
        release.set()
        await blocker
        if caller is not None and not caller.done():
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
        limiter.total_tokens = old_tokens


@pytest.mark.parametrize('note_count', [0, 2, 8])
async def test_committed_user_survives_transcript_append_failure(engine, http_handoff, monkeypatch, note_count):
    from pathlib import Path
    from test_compaction import _sse

    engine.working.log_turn('user', 'ORIGINAL_REQUIRED')
    backend = await http_handoff()
    raw = 'NEW exact persisted constraint\n  no publish  '
    for index in range(note_count):
        engine.working.note(f'real note {index}')
    previous_local_id = engine.store._conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    if note_count:
        assert previous_local_id == note_count
    real_open = Path.open
    def failed_append(path, *args, **kwargs):
        if path == engine.working.log_path and args and args[0] == 'a':
            raise OSError('fixture transcript append failure')
        return real_open(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', failed_append)
        with pytest.raises(OSError, match='transcript append failure'):
            await collect(engine, raw)
    rows = engine.store._conn.execute("SELECT id, content FROM turns WHERE role='user' ORDER BY id").fetchall()
    assert [r[1] for r in rows] == ['ORIGINAL_REQUIRED', raw]
    assert rows[-1][0] == 2
    if note_count == 2:
        assert rows[-1][0] == previous_local_id
    assert [r.turn for r in engine._pending_handoff.required] == [r[0] for r in rows]
    assert backend._client.payloads == []
    backend._client = _FakeClient([[_sse({'error': {'message': 'retry refused'}})]])
    await collect(engine, 'continue')
    second = await http_handoff(model='after-append-failure')
    await collect(engine, 'continue again')
    assert any(m.get('name') == 'dream_handoff_user' and m['content'].endswith(raw)
               for m in second._client.payloads[0]['messages'])
    assert engine._pending_handoff is None


@pytest.mark.parametrize('failure', ['no_insert', 'old_identical', 'mismatched', 'uncommitted'])
async def test_failed_capture_never_invents_committed_source(engine, http_handoff, monkeypatch, failure):
    raw = 'CURRENT exact attempted input'
    original = raw if failure == 'old_identical' else 'ORIGINAL_REQUIRED'
    original_id = log_receipt(engine, original)
    backend = await http_handoff()
    real_log = engine.working.log_turn
    def failed_log(role, content, *, on_commit=None):
        if failure == 'mismatched':
            real_log(role, 'unrelated committed user')
        elif failure == 'uncommitted':
            engine.store._conn.execute(
                'INSERT INTO turns(session_id,role,content,ts) VALUES(?,?,?,?)',
                (engine.session_id, role, content, 'fixture'))
        raise OSError('fixture persistence failure')
    with monkeypatch.context() as patch:
        patch.setattr(engine.working, 'log_turn', failed_log)
        with pytest.raises(OSError, match='fixture persistence failure'):
            await collect(engine, raw)
    assert [(r.turn, r.content) for r in engine._pending_handoff.required] == [(original_id, original)]
    assert backend._client.payloads == [] and engine._turn_index == 0
    if failure == 'uncommitted':
        assert engine.store._conn.in_transaction
        engine.store._conn.rollback()


@pytest.mark.parametrize('own_write', [False, True])
@pytest.mark.parametrize('note_count', [2, 8])
async def test_capture_identity_distinguishes_other_connection_from_own_commit(
        engine, http_handoff, monkeypatch, own_write, note_count):
    from pathlib import Path

    original_id = log_receipt(engine, 'ORIGINAL_REQUIRED')
    backend = await http_handoff()
    for index in range(note_count):
        engine.working.note(f'real note {index}')
    raw = 'CURRENT exact competing input'
    db_path = engine.store._conn.execute('PRAGMA database_list').fetchone()[2]
    previous_changes = engine.store._conn.total_changes
    real_add = engine.store.add_turn
    sentinel = OSError('fixture failed append' if own_write else 'fixture failed before own insert')
    def race(*args, **kwargs):
        if own_write:
            turn_id = real_add(*args, **kwargs)
        other = sqlite3.connect(db_path)
        try:
            if own_write:
                other.execute('INSERT INTO turns(session_id,role,content,ts) VALUES(?,?,?,?)',
                              (engine.session_id, 'user', raw, 'fixture'))
            else:
                # The competing row matches both raw input and this connection's
                # prior note ID, but this attempt has not written on its connection.
                other.execute('INSERT INTO turns(id,session_id,role,content,ts) VALUES(?,?,?,?,?)',
                              (note_count, engine.session_id, 'user', raw, 'fixture'))
            other.commit()
        finally:
            other.close()
        if not own_write:
            raise sentinel
        return turn_id
    real_open = Path.open
    def failed_append(path, *args, **kwargs):
        if path == engine.working.log_path and args and args[0] == 'a':
            raise sentinel
        return real_open(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(engine.store, 'add_turn', race)
        patch.setattr(Path, 'open', failed_append)
        with pytest.raises(OSError) as caught:
            await collect(engine, raw)
    assert caught.value is sentinel and backend._client.payloads == []
    assert not engine.store._conn.in_transaction
    retained = [(r.turn, r.content) for r in engine._pending_handoff.required]
    if own_write:
        assert engine.store._conn.total_changes > previous_changes
        assert retained == [(original_id, 'ORIGINAL_REQUIRED'), (2, raw)]
        assert engine.store._conn.execute('SELECT MAX(id) FROM turns').fetchone()[0] == 3
    else:
        assert engine.store._conn.total_changes == previous_changes
        assert retained == [(original_id, 'ORIGINAL_REQUIRED')]
        assert engine.store._conn.execute('SELECT content FROM turns WHERE id=?', (note_count,)).fetchone()[0] == raw


@pytest.mark.parametrize('competing', [False, True])
async def test_rolled_back_insert_never_adopts_identical_competing_commit(
        engine, http_handoff, monkeypatch, competing):
    original_id = log_receipt(engine, 'ORIGINAL_REQUIRED')
    backend = await http_handoff()
    conn = engine.store._conn
    conn.execute("CREATE TRIGGER rollback_turn BEFORE UPDATE ON sessions BEGIN SELECT RAISE(ROLLBACK, 'own transaction rolled back'); END")
    raw = 'identical competing raw constraint'
    real_add = engine.store.add_turn
    failures = []
    attempts = []
    def rolled_back_add(*args, **kwargs):
        attempts.append('insert')
        try:
            return real_add(*args, **kwargs)
        except sqlite3.IntegrityError as exc:
            failures.append(exc)
            assert not conn.in_transaction
            assert conn.execute('SELECT COUNT(*) FROM turns').fetchone()[0] == 1
            if competing:
                with sqlite3.connect(engine.store.db_path) as other:
                    cursor = other.execute('INSERT INTO turns(session_id,role,content,ts) VALUES(?,?,?,?)',
                                           (engine.session_id, 'user', raw, 'other writer'))
                    assert cursor.lastrowid == 2
            raise
    monkeypatch.setattr(engine.store, 'add_turn', rolled_back_add)
    with pytest.raises(sqlite3.IntegrityError) as caught:
        await collect(engine, raw)
    assert caught.value is failures[0] and attempts == ['insert']
    assert [r.turn for r in engine._pending_handoff.required] == [original_id]
    assert backend._client.payloads == [] and engine._turn_index == 0
    assert conn.execute('SELECT COUNT(*) FROM turns').fetchone()[0] == 1 + int(competing)


@pytest.mark.parametrize('receipt', ['missing', None, True, 0, -1, '1', 1.0])
async def test_pending_capture_rejects_missing_or_invalid_callback_receipt(
        engine, http_handoff, monkeypatch, receipt):
    log_receipt(engine, 'ORIGINAL_REQUIRED')
    backend = await http_handoff()
    original_pending = engine._pending_handoff
    attempts = []
    def replacement(role, content, *, on_commit=None):
        attempts.append('log')
        engine.store.add_turn(engine.session_id, role, content)
        if receipt != 'missing':
            on_commit(receipt)
    monkeypatch.setattr(engine.working, 'log_turn', replacement)
    with pytest.raises(RuntimeError, match='receipt'):
        await collect(engine, 'new constraint')
    assert attempts == ['log']
    assert engine._pending_handoff is original_pending
    assert backend._client.payloads == [] and engine._turn_index == 0
    assert len(engine.store.session_turns(engine.session_id)) == 2


async def test_pending_receipt_preparation_failure_keeps_old_state_and_skips_jsonl(
        engine, http_handoff, monkeypatch):
    import dream.core.council_context as context
    log_receipt(engine, 'ORIGINAL_REQUIRED')
    backend = await http_handoff()
    original_pending = engine._pending_handoff
    original_transcript = engine.working.log_path.read_text()
    original_error = RuntimeError('pending record preparation failed')
    unique = context.unique
    def failed_unique(records):
        if any(r.content == 'new constraint' for r in records):
            raise original_error
        return unique(records)
    monkeypatch.setattr(context, 'unique', failed_unique)
    with pytest.raises(RuntimeError) as caught:
        await collect(engine, 'new constraint')
    assert caught.value is original_error
    assert engine._pending_handoff is original_pending
    assert engine.working.log_path.read_text() == original_transcript
    assert len(engine.store.session_turns(engine.session_id)) == 2
    assert backend._client.payloads == [] and engine._turn_index == 0


async def test_ordinary_engine_logging_does_not_pass_callback_keyword(engine, monkeypatch):
    engine.backend = Backend('ordinary', [])
    original = engine.working.log_turn
    logged = []
    def legacy_log(role, content, tool_name=None):
        logged.append((role, content))
        return original(role, content, tool_name)
    monkeypatch.setattr(engine.working, 'log_turn', legacy_log)
    events = await collect(engine, 'ordinary input')
    assert ('user', 'ordinary input') in logged
    assert any(e.kind == 'result' and e.data.get('subtype') == 'success' for e in events)
    assert engine._pending_handoff is None
