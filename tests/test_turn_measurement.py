"""Turn settings and assessment metadata, using real Engine and finite transport."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from dream import config
from dream.core.backends.base import Event
from dream.telemetry.turn import TurnTiming
from test_engine_profile_task import isolated_runtime, task_engine, Transport
from test_compaction import _text_round


def logs(engine):
    return [json.loads(line) for line in (config.LOG_DIR / 'runtime' / f'{engine.session_id}.jsonl').read_text().splitlines()]


async def ask(engine, transport, text='A short request'):
    async with asyncio.timeout(10):
        return [event async for event in engine.ask(text)]


async def test_frozen_selection_and_payload_settings_reach_real_runtime(task_engine, monkeypatch):
    e, t, _ = await task_engine(model='roomy', control='false_done', history=False)
    e.backend.set_performance_mode('quick')
    original = e.backend.prepare_turn
    def change_during_preparation(names):
        e.backend.set_performance_mode('thorough')
        original(names)
    monkeypatch.setattr(e.backend, 'prepare_turn', change_during_preparation)
    await ask(e, t)
    row = next(row for row in logs(e) if row['event'] == 'turn_timing')
    c = row['configuration']
    assert c['source'] == 'prepared_http'
    assert c['output_ceiling'] == 2048
    assert c['reasoning_effort'] == t.payloads[0]['reasoning_effort'] == 'high'
    assert c['turn'] == row['turn'] == 1
    request = row['requests'][0]['configuration']
    assert request['max_tokens'] == t.payloads[0]['max_tokens'] == 2048
    assert request['context']['window'] == 65536
    assert request['context']['output'] == 2048
    assert request['model'] == 'roomy'
    assert row['outcome_scope'] == 'protocol'
    assert not any(row['event'] == 'task_assessment' for row in logs(e))


async def test_shared_run_meter_keeps_actual_turn_identity(task_engine):
    e, t, _ = await task_engine(control='false_done', history=False)
    t.rounds = [_text_round('one'), _text_round('two')]
    e.begin_run_budget()
    try:
        await ask(e, t)
        await ask(e, t)
    finally:
        e.end_run_budget()
    timing = [r for r in logs(e) if r['event'] == 'turn_timing']
    assert [r['turn'] for r in timing] == [1, 2]
    assert [r['configuration']['turn'] for r in timing] == [1, 2]
    assert [r['turn'] for r in logs(e) if r['event'] == 'turn_started'] == [1, 2]


async def test_overflow_records_selection_without_request(task_engine):
    e, t, _ = await task_engine(control='false_done', history=False)
    await ask(e, t, 'Required input ' * 10000)
    row = next(r for r in logs(e) if r['event'] == 'turn_timing')
    assert row['configuration']['context_window'] == 8192
    assert row['configuration']['reasoning_effort'] == 'high'
    assert row['requests'] == [] and t.payloads == []
    assert row['outcome'] == 'error'


async def test_native_configuration_never_claims_effective_provider_defaults(task_engine):
    e, _, _ = await task_engine(control='false_done', history=False)
    class Native:
        model = None
        _effort = 'high'
        async def ask(self, prompt):
            yield Event('result', {'subtype': 'success', 'usage': {}})
    e.backend = Native()
    e.model = None
    e.effort = 'high'
    await ask(e, None)
    row = next(r for r in logs(e) if r['event'] == 'turn_timing')
    c = row['configuration']
    assert c['source'] == 'native_configuration_only'
    assert c['model'] is None and c['configured_effort'] == 'high'
    assert c['reasoning_effort'] is None
    assert c['context_window'] is None and c['output_ceiling'] is None
    assert row['requests'] == []


def test_configuration_is_bounded_detached_and_omits_private_values():
    timing = TurnTiming()
    data = {'provider': 'machx', 'model': '/private/owner/model.gguf', 'profile': 'lean', 'turn': 2,
            'source': 'prepared_http', 'reasoning_effort': 'high', 'output_ceiling': 2048,
            'context_window': 8192, 'prompt': 'PRIVATE_PROMPT', 'api_key': 'PRIVATE_KEY'}
    timing.configure(data)
    data['reasoning_effort'] = 'low'
    first = timing.summary()['configuration']
    assert first['model'] == 'model.gguf'
    assert len(first['model_sha256']) == 64
    assert first['reasoning_effort'] == 'high'
    first['reasoning_effort'] = 'low'
    assert timing.summary()['configuration']['reasoning_effort'] == 'high'
    text = json.dumps(timing.summary())
    assert '/private/owner' not in text and 'PRIVATE_' not in text
    timing.finish('completed')
    timing.configure({'provider': 'bad'})
    assert timing.summary()['configuration']['provider'] == 'machx'


def test_request_configuration_sanitizes_nonfinite_and_raw_extras():
    timing = TurnTiming()
    c = {'model': 'fixture', 'max_tokens': float('inf'), 'reasoning_effort': 'private words',
         'context': {'window': 8192, 'output': 1024, 'remaining': 33, 'messages': 'PRIVATE_MESSAGES'},
         'prompt': 'PRIVATE_PROMPT', 'phase': 'lead'}
    timing.request(1, configuration=c)
    c['context']['window'] = 1234
    row = timing.summary()['requests'][0]['configuration']
    assert row['max_tokens'] is None and row['reasoning_effort'] is None
    assert row['context']['window'] == 8192
    assert 'PRIVATE_' not in json.dumps(row)


@pytest.mark.parametrize('valid', [True, False])
async def test_guided_output_reference_is_committed_format_check_only(task_engine, monkeypatch, valid):
    from test_guided_workflows_runtime import app_fixture, queue_task
    e, t, _ = await task_engine(control='false_done', history=False)
    app = app_fixture(e.workspace, monkeypatch, [])
    app.engine = e
    svc, task, queued = await queue_task(app, e.workspace)
    if valid:
        p = e.workspace / task['output_path']; p.parent.mkdir(parents=True)
        p.write_text('# Fictional result\n\nThe answer is not factually established.\n')
    await app._stream(queued.text, workflow=queued)
    stored = svc.get(task['id'])
    refs = [r for r in logs(e) if r['event'] == 'task_assessment']
    assert len(refs) == 1
    ref = refs[0]
    assert ref['assessment_kind'] == 'artifact_format'
    assert ref['reference'] == task['id'] and ref['attempt'] == 1 and ref['turn'] == 1
    assert ref['status'] == stored['status'] == ('ready_for_review' if valid else 'needs_attention')
    assert ref['artifact_sha256'] == [a['sha256'] for a in stored['artifacts']]
    assert ref['task_success'] is None
    assert 'Fictional' not in json.dumps(ref) and 'output_path' not in ref
    assert logs(e).index(ref) > next(i for i,r in enumerate(logs(e)) if r['event'] == 'turn_timing')


@pytest.mark.parametrize('verdict', ['PASS', 'FAIL', 'UNVERIFIED'])
async def test_loop_reference_points_to_committed_review(task_engine, tmp_path, verdict):
    from dream.core.loop import AutonomousLoop
    e, t, _ = await task_engine(control='false_done', history=False)
    t.rounds = [_text_round('STATUS: DONE')]
    class Review:
        async def connect(self):
            if verdict == 'UNVERIFIED':
                raise RuntimeError('Fixture review unavailable')
        async def disconnect(self): pass
        async def ask(self, prompt):
            yield Event('assistant_done', f'VERDICT: {verdict}\nGAPS: ' + ('none' if verdict == 'PASS' else 'Fixture gap'))
    loop = AutonomousLoop(e, state_dir=tmp_path/'runs', max_iterations=1,
                          evaluator_backend_factory=lambda *a: Review())
    result = await loop.run('Inspect fixture', acceptance_criteria='- Inspect the retained fixture')
    ledger = [json.loads(line) for line in (loop.workspace/'ledger.jsonl').read_text().splitlines()]
    committed = next(r for r in ledger if r['event'] == 'evaluation')
    refs = [r for r in logs(e) if r['event'] == 'task_assessment']
    assert len(refs) == 1
    ref = refs[0]
    assert ref['assessment_kind'] == 'independent_model_review'
    assert ref['reference'] == result.run_id and ref['sequence'] == committed['seq']
    assert ref['status'] == committed['data']['verdict'] == verdict
    assert ref['turn'] == 1 and ref['task_success'] is None
    assert 'response' not in ref and 'gaps' not in ref and 'endpoint' not in ref


async def test_failed_ledger_fsync_cannot_emit_committed_review_reference(task_engine, tmp_path, monkeypatch):
    from dream.core.loop import AutonomousLoop
    from dream.core.run_state import RunState
    import dream.core.run_state as state
    e, t, _ = await task_engine(control='false_done', history=False)
    t.rounds = [_text_round('STATUS: DONE')]
    class Review:
        async def connect(self): pass
        async def disconnect(self): pass
        async def ask(self, prompt):
            yield Event('assistant_done', 'VERDICT: PASS\nGAPS: none')
    original = RunState.record
    def fail_review_fsync(journal, event, **kwargs):
        if event == 'evaluation':
            with monkeypatch.context() as patch:
                def fail(*args): raise OSError('injected fsync failure')
                patch.setattr(state.os, 'fsync', fail)
                return original(journal, event, **kwargs)
        return original(journal, event, **kwargs)
    monkeypatch.setattr(RunState, 'record', fail_review_fsync)
    loop = AutonomousLoop(e, state_dir=tmp_path/'runs', evaluator_backend_factory=lambda *a: Review())
    result = await loop.run('Inspect fixture', acceptance_criteria='- Inspect the fixture')
    assert result.status == 'error'
    assert not any(r['event'] == 'task_assessment' for r in logs(e))


async def test_admission_reduces_recorded_request_output_not_frozen_ceiling(task_engine):
    e, t, _ = await task_engine(control='false_done', history=False)
    e.profile = e.backend.profile = replace(e.profile, output_tokens=6000)
    e.backend._context_overflow = 'error'
    await ask(e, t, 'Preserve the requested output. ' * 40)
    row = next(r for r in logs(e) if r['event'] == 'turn_timing')
    assert row['configuration']['output_ceiling'] == 6000
    request = row['requests'][0]['configuration']
    assert 256 <= request['max_tokens'] < 6000
    assert request['max_tokens'] == t.payloads[0]['max_tokens'] == request['context']['output']
    assert request['context']['remaining'] >= 0


async def test_model_switch_records_new_selection_and_discards_old_admission(task_engine):
    e, t, _ = await task_engine(control='false_done', history=False)
    t.rounds = [_text_round('first'), _text_round('second')]
    await ask(e, t)
    await e.set_model('roomy')
    await ask(e, t)
    records = [r for r in logs(e) if r['event'] == 'turn_timing']
    assert [r['configuration']['model'] for r in records] == ['small', 'roomy']
    assert [r['configuration']['context_window'] for r in records] == [8192, 65536]
    assert [r['requests'][0]['configuration']['context']['window'] for r in records] == [8192, 65536]
    assert records[1]['configuration']['reasoning_effort'] == 'high'
    assert records[0]['configuration']['model_sha256'] != records[1]['configuration']['model_sha256']


async def test_nonstream_request_uses_its_own_context_not_saved_lead(task_engine):
    from dream.core.context_budget import account
    e, _, _ = await task_engine(control='false_done', history=False)
    b = e.backend
    b.turn_timing = TurnTiming()
    b.context_report = {'window': 1234, 'output': 999}
    payload = {'model': 'small', 'messages': [{'role': 'user', 'content': 'Delegated input'}],
               'tools': [], 'max_tokens': 512, 'reasoning_effort': 'low'}
    class Client:
        async def post(self, *args, **kwargs):
            return SimpleNamespace(status_code=200, json=lambda: {'usage': {'prompt_tokens': 12, 'completion_tokens': 2}})
    b._client = Client()
    await b._post_with_retry(payload)
    record = b.turn_timing.summary()['requests'][0]['configuration']
    assert record['phase'] == 'delegated' and record['reasoning_effort'] == 'low'
    assert record['context']['window'] == 8192 and record['context']['output'] == 512
    assert record['context']['input_tokens'] == account(payload['messages'], [], 8192, 512).input_tokens
    assert b.context_report == {'window': 1234, 'output': 999}


async def test_interruption_retains_configuration_without_task_assessment(task_engine, monkeypatch):
    from contextlib import asynccontextmanager
    e, t, _ = await task_engine(control='false_done', history=False)
    entered = asyncio.Event()
    @asynccontextmanager
    async def stream(payload):
        class Response:
            async def aiter_lines(self):
                entered.set()
                await asyncio.Event().wait()
                yield ''
        yield Response()
    monkeypatch.setattr(e.backend, '_stream_with_retry', stream)
    work = asyncio.create_task(ask(e, t))
    await asyncio.wait_for(entered.wait(), 2)
    work.cancel()
    with pytest.raises(asyncio.CancelledError): await work
    row = next(r for r in logs(e) if r['event'] == 'turn_timing')
    assert row['outcome'] == 'interrupted'
    assert row['configuration']['reasoning_effort'] == 'high'
    assert row['requests'][0]['configuration']['model'] == 'small'
    assert not any(r['event'] == 'task_assessment' for r in logs(e))


async def test_failed_workflow_transaction_emits_no_assessment(task_engine, monkeypatch):
    from test_guided_workflows_runtime import app_fixture, queue_task
    from dream.workflows.store import WorkflowStore
    e, t, _ = await task_engine(control='false_done', history=False)
    app = app_fixture(e.workspace, monkeypatch, []); app.engine = e
    svc, task, queued = await queue_task(app, e.workspace)
    output = e.workspace/task['output_path'];output.parent.mkdir(parents=True)
    output.write_text('# Report\n\nSome fixture observations with sufficient body.\n')
    original = WorkflowStore.save
    def fail_save(store, task, db):
        if task['status'] == 'ready_for_review':raise OSError('injected workflow transaction failure')
        return original(store, task, db)
    monkeypatch.setattr(WorkflowStore, 'save', fail_save)
    with pytest.raises(ValueError, match='workflow state') as caught:
        await app._stream(queued.text, workflow=queued)
    assert isinstance(caught.value.__cause__, OSError)
    assert 'transaction failure' in str(caught.value.__cause__)
    assert not any(r['event'] == 'task_assessment' for r in logs(e))
    assert svc.get(task['id'])['status'] != 'ready_for_review'


async def test_first_preparation_failure_has_unknown_turn_without_binding_accounting(task_engine, monkeypatch):
    e, t, _ = await task_engine(control='false_done', history=False)
    assert e.runtime_meter is None
    original_context_meter = e._tool_context.runtime_meter
    callbacks=[];e.emit=callbacks.append
    async def fail():raise RuntimeError('first preparation fails')
    monkeypatch.setattr(e.backend, 'prepare_user_turn', fail)
    with pytest.raises(RuntimeError, match='preparation fails'):await ask(e, t)
    row = next(r for r in logs(e) if r['event'] == 'turn_timing')
    assert row['turn'] is None and row['configuration']['turn'] is None
    assert row['outcome'] == 'error' and row['final'] is True
    assert row['configuration']['source'] == 'unprepared_http'
    callback=next(ev.data for ev in callbacks if ev.kind=='turn_timing')
    assert callback['configuration']['turn'] is None and callback['final'] is False
    assert row['requests'] == [] and e._turn_index == 0
    assert e.runtime_meter is None and e._tool_context.runtime_meter is original_context_meter
    assert not any(r['event']=='turn_started' for r in logs(e))


async def test_final_timing_waits_for_owned_bridge_cleanup(task_engine):
    e, t, _ = await task_engine(control='false_done', history=False)
    entered, release = asyncio.Event(), asyncio.Event()
    class Bridge:
        async def cancel_pending(self):
            entered.set();await release.wait()
    e._tool_bridge = Bridge()
    events=[]
    async def consume():
        async for event in e.ask('Inspect fixture'):
            events.append(event)
    work=asyncio.create_task(consume())
    await asyncio.wait_for(entered.wait(), 2)
    try:
        provisional=next(ev.data['stats']['timing'] for ev in events if ev.kind=='result')
        assert provisional['outcome']=='completed' and provisional['final'] is False
        assert not any(r['event']=='turn_timing' for r in logs(e))
    finally:
        release.set();await work
    final=next(r for r in logs(e) if r['event']=='turn_timing')
    assert final['final'] is True and final['outcome']=='completed'
    assert final['elapsed_s'] >= provisional['elapsed_s']


@pytest.mark.parametrize('failure', ['error','cancel'])
async def test_bridge_cleanup_failure_determines_final_timing(task_engine,failure):
    e,t,_=await task_engine(control='false_done',history=False)
    class Bridge:
        async def cancel_pending(self):
            if failure=='error':raise RuntimeError('bridge cleanup failure')
            raise asyncio.CancelledError('bridge cleanup cancellation')
    e._tool_bridge=Bridge()
    with pytest.raises(RuntimeError if failure=='error' else asyncio.CancelledError):await ask(e,t)
    final=next(r for r in logs(e) if r['event']=='turn_timing')
    assert final['outcome']==('error' if failure=='error' else 'interrupted')
    assert final['final'] is True


@pytest.mark.parametrize('failure', ['error','cancel'])
async def test_acknowledgement_error_determines_final_timing(task_engine,monkeypatch,failure):
    e,t,_=await task_engine(control='false_done',history=False)
    error=RuntimeError if failure=='error' else asyncio.CancelledError
    def fail():raise error('acknowledgement failure')
    monkeypatch.setattr(e.backend,'acknowledge_council_context',fail)
    with pytest.raises(error,match='acknowledgement failure'):await ask(e,t)
    rows=logs(e)
    primary=next(r for r in rows if r['event']=='turn_timing')
    correction=next(r for r in rows if r['event']=='turn_timing_correction')
    assert primary['outcome']=='completed' and primary['revision']==0
    assert primary['boundary']=='pre_acknowledgement'
    assert correction['turn']==primary['turn']==1 and correction['session']==primary['session']
    assert correction['revision']==1 and correction['supersedes_revision']==0
    assert correction['boundary']=='acknowledgement_failed'
    assert correction['outcome']==('error' if failure=='error' else 'interrupted')
    assert correction['final'] is True
    # Resolve revisions within the existing session/turn, never count a new turn.
    resolved={}
    for row in rows:
        if row['event'] in {'turn_timing','turn_timing_correction'}:
            key=(row['session'],row['turn'])
            if key not in resolved or row['revision']>resolved[key]['revision']:resolved[key]=row
    assert len(resolved)==1 and next(iter(resolved.values()))==correction
    assert e.turn_timing.summary()['outcome']==correction['outcome']


@pytest.mark.parametrize('failure', ['error','cancel'])
async def test_bound_context_exit_failure_prevents_acknowledgement(task_engine, monkeypatch,failure):
    from contextlib import contextmanager
    import dream.core.engine as engine_module
    e,t,_=await task_engine(control='false_done',history=False)
    original=engine_module.bind_context
    retained=object(); acknowledgements=[]
    error=RuntimeError if failure=='error' else asyncio.CancelledError
    @contextmanager
    def fail_on_exit(context):
        with original(context):
            yield
        e._pending_handoff=retained
        raise error('bound context exit failure')
    monkeypatch.setattr(engine_module,'bind_context',fail_on_exit)
    monkeypatch.setattr(e.backend,'acknowledge_council_context',lambda: acknowledgements.append(True))
    with pytest.raises(error,match='bound context exit failure'):await ask(e,t)
    assert e._pending_handoff is retained and acknowledgements==[]
    final=next(r for r in logs(e) if r['event']=='turn_timing')
    assert final['outcome']==('error' if failure=='error' else 'interrupted') and final['final'] is True


@pytest.mark.parametrize('failure', ['error','cancel'])
async def test_timing_callback_failure_prevents_acknowledgement(task_engine,monkeypatch,failure):
    e,t,_=await task_engine(control='false_done',history=False)
    retained=object(); acknowledgements=[]
    error=RuntimeError if failure=='error' else asyncio.CancelledError
    def fail(event):
        if event.kind=='turn_timing':
            e._pending_handoff=retained
            raise error('timing callback failure')
    e.emit=fail
    monkeypatch.setattr(e.backend,'acknowledge_council_context',lambda: acknowledgements.append(True))
    with pytest.raises(error,match='timing callback failure'):await ask(e,t)
    assert e._pending_handoff is retained and acknowledgements==[]
    final=next(r for r in logs(e) if r['event']=='turn_timing')
    assert final['outcome']==('error' if failure=='error' else 'interrupted') and final['final'] is True


async def test_callback_is_provisional_until_acknowledgement_and_final_record(task_engine):
    e,t,_=await task_engine(control='false_done',history=False)
    callbacks=[];e.emit=callbacks.append
    events=await ask(e,t)
    result=next(ev.data['stats']['timing'] for ev in events if ev.kind=='result')
    callback=next(ev.data for ev in callbacks if ev.kind=='turn_timing')
    final=next(r for r in logs(e) if r['event']=='turn_timing')
    assert result['final'] is callback['final'] is False
    assert final['final'] is True and e.turn_timing.summary()['final'] is True
    assert result['outcome']==callback['outcome']==final['outcome']=='completed'
    assert final['elapsed_s'] >= callback['elapsed_s'] >= result['elapsed_s']
    assert final['revision']==0 and final['boundary']=='pre_acknowledgement'
    assert len([r for r in logs(e) if r['event']=='turn_timing'])==1
    assert not any(r['event']=='turn_timing_correction' for r in logs(e))


@pytest.mark.parametrize('failure', ['error','cancel'])
@pytest.mark.parametrize('report_failure', ['error','cancel'])
async def test_ack_correction_failure_preserves_primary_exception_and_snapshot(task_engine,monkeypatch,failure,report_failure):
    from dream.telemetry.runtime import RunMeter
    e,t,_=await task_engine(control='false_done',history=False)
    original=RunMeter.record; snapshots=[]; timing_snapshots=[]; retained=object()
    primary_error=RuntimeError('acknowledgement failed') if failure=='error' else asyncio.CancelledError('acknowledgement canceled')
    def record(meter,kind,**metadata):
        if kind=='turn_timing':snapshots.append(deepcopy(metadata))
        if kind=='turn_timing_correction':
            raise (RuntimeError if report_failure=='error' else asyncio.CancelledError)('correction reporting failed')
        return original(meter,kind,**metadata)
    def acknowledge():
        timing_snapshots.append((e.turn_timing,e.turn_timing.summary()))
        e._pending_handoff=retained
        raise primary_error
    monkeypatch.setattr(RunMeter,'record',record)
    monkeypatch.setattr(e.backend,'acknowledge_council_context',acknowledge)
    with pytest.raises(type(primary_error)) as caught:await ask(e,t)
    assert caught.value is primary_error and e._pending_handoff is retained
    assert len(snapshots)==1 and snapshots[0]['revision']==0
    assert snapshots[0]['outcome']=='completed' and snapshots[0]['boundary']=='pre_acknowledgement'
    assert len([r for r in logs(e) if r['event']=='turn_timing'])==1
    assert not any(r['event']=='turn_timing_correction' for r in logs(e))
    previous,previous_summary=timing_snapshots[0]
    assert previous is not e.turn_timing and previous.summary()==previous_summary
    status=e.turn_timing.summary()
    assert status['revision']==1 and status['outcome']==('error' if failure=='error' else 'interrupted')


@pytest.mark.parametrize('fault', ['timing','meter'])
async def test_finalization_failure_retains_input_and_status_is_not_completed(task_engine,monkeypatch,fault):
    from dream.telemetry.runtime import RunMeter
    e,t,_=await task_engine(control='false_done',history=False)
    retained=object();acknowledgements=[]
    monkeypatch.setattr(e.backend,'acknowledge_council_context',lambda:acknowledgements.append(True))
    if fault=='timing':
        def finish(*args,**kwargs):
            e._pending_handoff=retained
            raise RuntimeError('timing failed')
        monkeypatch.setattr(TurnTiming,'finish',finish)
    else:
        original=RunMeter.record
        def record(meter,kind,**kwargs):
            if kind=='turn_timing':
                e._pending_handoff=retained
                raise RuntimeError('meter failed')
            return original(meter,kind,**kwargs)
        monkeypatch.setattr(RunMeter,'record',record)
    with pytest.raises(RuntimeError,match=fault+' failed'):await ask(e,t)
    assert e._pending_handoff is retained and acknowledgements==[]
    assert e.turn_timing.summary()['outcome']=='error'
    assert e.turn_timing.summary()['boundary']=='measurement_failed'
    assert not any(r['event'] in {'turn_timing','turn_timing_correction'} for r in logs(e))


@pytest.mark.parametrize('observed', ['error','result_error','length','incomplete','tool_budget','success','partial'])
@pytest.mark.parametrize('stop', ['close','cancel'])
async def test_consumer_close_preserves_failure_but_actual_cancellation_interrupts(task_engine,monkeypatch,observed,stop):
    e,t,_=await task_engine(control='false_done',history=False)
    event = {
        'error': Event('error','fixture provider failure'),
        'result_error': Event('result',{'is_error':True,'subtype':'error'}),
        'length': Event('result',{'is_error':False,'subtype':'length'}),
        'incomplete': Event('result',{'is_error':False,'subtype':'fixture_partial'}),
        'tool_budget': Event('tool_result',{'name':'fixture','content':'finished tool'}),
        'success': Event('result',{'is_error':False,'subtype':'success'}),
        'partial': Event('assistant_done','fixture partial text'),
    }[observed]
    backend_closed=[];acknowledgements=[]
    async def scripted(prompt):
        try:
            yield event
            await asyncio.Event().wait()
        finally:backend_closed.append(True)
    monkeypatch.setattr(e.backend,'ask',scripted)
    monkeypatch.setattr(e.backend,'acknowledge_council_context',lambda:acknowledgements.append(True))
    if observed=='tool_budget':e.tool_budget.limit=0
    entered=asyncio.Event()
    class Bridge:
        async def cancel_pending(self):
            entered.set()
            if stop=='cancel':await asyncio.Event().wait()
    e._tool_bridge=Bridge()
    retained=object()
    async def consume_and_close():
        # One task owns entry and closure, matching Engine's context binding.
        stream=e.ask('fixture request')
        while (await anext(stream)) is not event:pass
        e._pending_handoff=retained
        await stream.aclose()
    async with asyncio.timeout(5):
        if stop=='close':await consume_and_close()
        else:
            closing=asyncio.create_task(consume_and_close())
            await entered.wait();closing.cancel()
            with pytest.raises(asyncio.CancelledError):await closing
    expected='interrupted' if stop=='cancel' or observed in {'success','partial'} else 'error' if observed in {'error','result_error'} else observed
    assert e.turn_timing.summary()['outcome']==expected
    primary=next(r for r in logs(e) if r['event']=='turn_timing')
    assert primary['outcome']==expected and primary['turn']==1
    assert primary['boundary']=='pre_acknowledgement' and primary['final'] is True
    assert backend_closed==[True] and entered.is_set() and acknowledgements==[]
    assert e._pending_handoff is retained
