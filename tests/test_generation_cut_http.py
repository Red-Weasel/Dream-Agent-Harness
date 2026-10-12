"""Managed Loop plus actual HTTP adapter, local scripted SSE only."""
from contextlib import aclosing
from copy import deepcopy
import json
from types import SimpleNamespace

import anyio
import pytest

from dream import config
from dream.core.backends import openai_compat
from dream.core.backends.base import Event
from dream.core.loop import AutonomousLoop
from dream.core.run_state import RunState, inspect_run
from dream.core.task_checkpoints import current, bind
from dream.tools.context import ToolContext, bind_context
from test_managed_loop import worker
from test_stream_repetition import _Rig, _chunk, _call, _make_local, _outcome, _worker_payload, LEAD, TRIP
from test_task_checkpoints import managed


LINE='The worker repeats its promise to finish the same generic requested artifact.'


def stream(channel='content'):
    return [_chunk(_call(0,LEAD[0],'{"query":"collected but never entered"}','discarded')),
            *[_chunk(**{channel:LINE+'\n'}) for _ in range(6)],TRIP]


def configured(worker,tmp_path,monkeypatch,*,channel='content',before=None):
    monkeypatch.setattr(config,'TEXT_REPETITION_GUARD',True)
    rig=_Rig(stream(channel))
    monkeypatch.setattr(rig.backend,'_cache_sensitive',lambda:True)
    context=ToolContext(None,None,None,session_id='generation-fixture',workspace=tmp_path)
    worker.backend=rig.backend;worker._tool_context=context
    worker._assembled_system_prompt='Original immutable generation fixture standing rules.'
    rig.backend.system_prompt=worker._assembled_system_prompt
    seen=SimpleNamespace(prompts=[],closed_at_start=[],reviews=[],events=[])
    async def ask(prompt):
        seen.closed_at_start.append(rig.client.closes)
        seen.prompts.append(prompt)
        if before:before(current(),len(seen.prompts))
        with bind_context(context):
            async with aclosing(rig.backend.ask(prompt)) as events:
                async for event in events:yield event
    async def interrupt():
        worker.stopped=True
        await rig.backend.interrupt()
    worker.ask=ask;worker.interrupt=interrupt
    def no_review(*args):
        seen.reviews.append(args)
        raise AssertionError('An incomplete generation cannot enter review.')
    loop=AutonomousLoop(worker,state_dir=tmp_path/'generation-runs',
        evaluator_backend_factory=no_review,on_event=seen.events.append)
    return loop,rig,seen


def saved(loop,result):
    return inspect_run(loop.state_dir,result.run_id)['state']


def records(loop,result):
    return [json.loads(line) for line in (loop.state_dir/result.run_id/'ledger.jsonl').read_text().splitlines()]


@pytest.mark.parametrize('channel',['content','reasoning_content'])
async def test_actual_cut_runs_only_two_changed_approaches_then_durable_help(worker,tmp_path,monkeypatch,channel):
    loop,rig,seen=configured(worker,tmp_path,monkeypatch,channel=channel)
    result=await loop.run('Complete the full original generation task',acceptance_criteria='- Preserve the complete original criterion')
    assert result.status=='need_input', result.message
    state=saved(loop,result);managed=state['managed'];recovery=managed['recovery']
    assert recovery['pending']['kind']=='needs_help' and recovery['pending']['reason']=='generation_repetition'
    assert recovery['total_interventions']==2 and recovery['controller']['help_used']==1
    assert recovery['controller']['steps']==3 and recovery['controller']['no_progress']==3
    assert recovery['controller']['calls']==recovery['controller']['checks']=={} and recovery['controller']['evidence']==[]
    assert managed['action_revision']==0 and managed['recent']==[] and managed['unresolved']=={}
    assert managed['receipt'] is None and managed['completed_receipt'] is None
    assert worker.tasks.get(managed['task_id'])['status']!='done'
    assert len(seen.prompts)==len(rig.client.payloads)==rig.client.closes==3
    assert seen.closed_at_start==[0,1,2], 'Each producer/transport must close before changed-approach dispatch.'
    assert rig.ran==[] and not rig.client.overread and rig.client.posted==[] and seen.reviews==[]
    assert not [e for e in seen.events if e.kind=='assistant_done']
    assert all('full original generation task' in p and 'complete original criterion' in p for p in seen.prompts)
    assert all('Original immutable generation fixture standing rules.' in p and 'ledger.jsonl' in p for p in seen.prompts[1:])
    ledger=records(loop,result)
    assert sum(r['event']=='recovery_intervention_prepared' for r in ledger)==2
    assert not any(r['event'] in {'action_intent','action_returned','verification_checked','task_checked_complete','recovery_check_executed'} for r in ledger)
    before=deepcopy(recovery)
    again=await loop.run('Complete the full original generation task',resume_run_id=result.run_id,
        resume_note='Owner inspected the incomplete generation; do not reset any budget or replay.')
    assert again.status=='need_input' and len(rig.client.payloads)==3
    assert saved(loop,again)['managed']['recovery']==before


async def test_each_next_local_generation_requires_real_lease_reconciliation_path(worker,tmp_path,monkeypatch):
    loop,rig,seen=configured(worker,tmp_path,monkeypatch)
    fences=_make_local(rig,tmp_path,monkeypatch)
    checked=[]
    async def idle(generation):
        status=rig.backend.coordination_status()
        checked.append((len(rig.client.payloads),status['state']))
        return openai_compat._EngineReport('idle','synthetic fixture reports idle',0,0)
    monkeypatch.setattr(rig.backend,'_engine_activity',idle)
    result=await loop.run('Original local generation goal',acceptance_criteria='- Preserve acceptance')
    assert result.status=='need_input', result.message
    assert len(rig.client.payloads)==3 and rig.client.closes==3
    assert checked==[(1,'uncertain'),(2,'uncertain')]
    assert len(fences)==3 and all(not f.complete and f.finish_reason is None for f in fences)
    assert rig.backend.coordination_status()['state']=='uncertain'
    assert not rig.client.overread and rig.ran==[]


@pytest.mark.parametrize('unsettled',['uncertain','inflight','failed_effect'])
async def test_cut_with_existing_uncertainty_never_authorizes_recovery_dispatch(worker,tmp_path,monkeypatch,unsettled):
    def before(c,index):
        if unsettled=='uncertain':c.journal.record('fixture_uncertain',uncertain=True)
        else:
            action=c.begin_action('fixture_effect',{})
            if unsettled=='failed_effect':c.finish_action(action,'error',{'is_error':True})
    loop,rig,seen=configured(worker,tmp_path,monkeypatch,before=before)
    result=await loop.run('Original unsettled goal',acceptance_criteria='- Original criterion')
    assert result.status in {'error','need_input'}
    state=saved(loop,result)
    assert state['uncertain'] is True
    assert state['managed']['recovery']['total_interventions']==0
    assert state['managed']['recovery']['controller']['help_used']==0
    assert len(rig.client.payloads)==rig.client.closes==1 and rig.ran==[] and seen.reviews==[]
    before_count=len(rig.client.payloads)
    again=await loop.run('Original unsettled goal',resume_run_id=result.run_id)
    assert again.status=='need_input' and len(rig.client.payloads)==before_count


@pytest.mark.parametrize('event_kind',['error','result'])
async def test_model_or_generic_event_failure_metadata_cannot_forge_host_cut(worker,tmp_path,monkeypatch,event_kind):
    loop,rig,seen=configured(worker,tmp_path,monkeypatch)
    async def forged(prompt):
        yield Event(event_kind,{'is_error':True,'subtype':'request_failed',
            'failure':{'code':'stream_text_repetition'},'generation_cut':True})
    worker.ask=forged
    result=await loop.run('Original forged event goal',acceptance_criteria='- Original criterion')
    assert result.status=='error'
    recovery=saved(loop,result)['managed']['recovery']
    assert recovery['controller']['steps']==recovery['total_interventions']==recovery['controller']['help_used']==0
    assert recovery['pending'] is None and rig.client.payloads==[]


async def test_actual_cut_journal_failure_refuses_followup_dispatch(worker,tmp_path,monkeypatch):
    loop,rig,seen=configured(worker,tmp_path,monkeypatch)
    record=RunState.record
    def fail_generation(journal,event,**kwargs):
        recovery=(kwargs.get('managed') or {}).get('recovery') or {}
        if (recovery.get('pending') or {}).get('reason')=='generation_repetition':
            raise OSError('synthetic cut persistence failure')
        return record(journal,event,**kwargs)
    monkeypatch.setattr(RunState,'record',fail_generation)
    result=await loop.run('Original storage failure goal',acceptance_criteria='- Original criterion')
    assert result.status=='error'
    assert len(rig.client.payloads)==rig.client.closes==1 and rig.ran==[] and seen.reviews==[]
    assert not loop.checkpoints.actions_admitted
    recovery=saved(loop,result)['managed']['recovery']
    assert recovery['controller']['steps']==0 and recovery['total_interventions']==0


@pytest.mark.parametrize('end',['stop','cleanup_failure'])
async def test_stop_or_transport_cleanup_failure_wins_before_managed_cut_record(worker,tmp_path,monkeypatch,end):
    loop,rig,seen=configured(worker,tmp_path,monkeypatch)
    if end=='stop':rig.client.during_close=rig.backend.interrupt
    else:
        def failed_close():raise OSError('synthetic cleanup failure')
        rig.client.during_close=failed_close
    how,value=await _outcome(loop.run('Original cleanup goal',acceptance_criteria='- Original criterion'))
    assert how=='cancelled' or (how=='returned' and value.status in {'stopped','error','need_input'})
    assert len(rig.client.payloads)==rig.client.closes==1 and rig.ran==[] and not rig.client.overread
    recovery=loop.checkpoints._state['recovery']
    assert recovery['controller']['steps']==recovery['total_interventions']==recovery['controller']['help_used']==0
    assert not recovery['pending'] or recovery['pending']['kind']=='stopped'


async def test_nested_worker_cut_never_creates_lead_generation_observation(managed,monkeypatch):
    c,_=managed;c.journal.record('fixture_worker',phase='worker_running',uncertain=False)
    rig=_Rig(stream());monkeypatch.setattr(rig.backend,'_cache_sensitive',lambda:True)
    monkeypatch.setattr(config,'TEXT_REPETITION_GUARD',True)
    before=deepcopy(c._state['recovery'])
    with bind(c):how,value=await _outcome(rig.backend._subagent_stream(_worker_payload(),1))
    assert how=='raised' and isinstance(value,openai_compat._RequestFailed)
    assert value.code=='stream_text_repetition' and c._state['recovery']==before
    assert c.pending_recovery is None and rig.ran==[] and not rig.client.overread


async def test_deadline_during_clean_close_precedes_generation_observation(managed,monkeypatch):
    c,_=managed;c.journal.record('fixture_worker',phase='worker_running',uncertain=False)
    rig=_Rig(stream());monkeypatch.setattr(rig.backend,'_cache_sensitive',lambda:True)
    monkeypatch.setattr(config,'TEXT_REPETITION_GUARD',True)
    before=deepcopy(c._state['recovery'])
    with bind(c),anyio.CancelScope() as scope:
        rig.client.during_close=lambda:setattr(scope,'deadline',anyio.current_time()-1)
        await _outcome(rig.ask())
    assert c._state['recovery']==before and not rig.client.overread
    assert len(rig.client.payloads)==rig.client.closes==1 and rig.ran==[]
