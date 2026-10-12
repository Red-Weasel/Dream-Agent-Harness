"""Host schedule through real Loop/coordinator/SQLite/HTTP boundaries; no models."""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from dream.core.backends.base import Event
from dream.core.task_checkpoints import current
from test_local_subagents import _backend
from test_managed_loop import worker, tool_bundle


class ReadonlyReviewer:
    def __init__(self,tools,path):self.tools,self.path=tools,path
    async def connect(self):pass
    async def disconnect(self):pass
    async def ask(self,prompt):
        await self.tools[0].handler({'path':str(self.path)})
        yield Event('assistant_done','VERDICT: PASS\nGAPS: none')


def configured(worker,tmp_path):
    backend=_backend([],None)
    backend.messages=[{'role':'system','content':'original full standing head'}]
    worker.backend=backend
    worker._tool_context=SimpleNamespace(workspace=tmp_path,session_id='phase-fixture')
    worker.invocations=[]
    async def handler(args):
        c=current()
        (tmp_path/'artifact.txt').write_text(c.journal.state['goal'])
        (c.journal.path/'progress.md').write_text('Host-visible completed fixture work.')
        return {'content':[{'type':'text','text':'saved actual fixture artifact'}]}
    tool=tool_bundle(handler)
    async def ask(prompt):
        c=current();worker.invocations.append((c.journal.run_id,c.journal.state['goal']))
        await tool.handler({})
        yield Event('assistant_done','STATUS: DONE')
    worker.ask=ask
    return lambda settings,tools,*a:ReadonlyReviewer(tools,tmp_path/'artifact.txt')


def phases(tmp_path):
    from dream.core.phase_runner import PhaseSpec
    return tuple(PhaseSpec(id=f'phase-{i}',goal=f'generic phase {i}',contract=f'- artifact names generic phase {i}',
        check_name=f'check-{i}',check_version='a'*64,
        check=lambda i=i:(tmp_path/'artifact.txt').read_text()==f'generic phase {i}') for i in (1,2))


async def test_two_phases_use_distinct_managed_runs_checked_archives_and_no_replay(worker,tmp_path):
    from dream.core.phase_runner import PhaseRunner
    factory=configured(worker,tmp_path)
    runner=PhaseRunner(worker,phases(tmp_path),goal='original full schedule goal',state_dir=tmp_path/'schedules',
        evaluator_backend_factory=factory)
    result=await runner.run()
    assert result.status=='done' and result.completed_phases==2
    assert len(worker.invocations)==2 and len({r for r,_ in worker.invocations})==2
    state=runner.inspect(result.schedule_id)
    assert state['cursor']==2 and state['dispatches']==4
    assert all(p['completion']['boundary_status']=='acknowledged' for p in state['phases'])
    assert all(p['completion']['host_check_passed'] for p in state['phases'])
    from dream.core.run_state import inspect_run
    for row in state['phases']:
        child=inspect_run(tmp_path/'schedules'/'children',row['run_id'])['state']
        assert child['managed']['action_revision']==2
        assert len(child['managed']['recent'])==1
        assert child['managed']['recent'][0]['disposition']=='returned-success'
        assert child['phase_check']['action_revision']==2
        assert child['managed']['receipt']['action_revision']==2
    resumed=await runner.run(resume_schedule_id=result.schedule_id)
    assert resumed.status=='done' and len(worker.invocations)==2


async def test_host_check_failure_cannot_complete_or_advance(worker,tmp_path):
    from dream.core.phase_runner import PhaseRunner,PhaseSpec
    factory=configured(worker,tmp_path)
    checks=[]
    def failed():checks.append(1);return False
    spec=PhaseSpec('phase-1','generic phase 1','- actual check must pass','named','a'*64,failed)
    runner=PhaseRunner(worker,(spec,),goal='full goal',state_dir=tmp_path/'schedules',evaluator_backend_factory=factory)
    result=await runner.run()
    assert result.status=='need_input' and result.completed_phases==0
    assert len(checks)==2
    state=runner.inspect(result.schedule_id)
    assert state['cursor']==0 and state['phases'][0]['completion'] is None
    count=len(worker.invocations)
    refused=await runner.run(resume_schedule_id=result.schedule_id)
    assert refused.status=='need_input' and len(worker.invocations)==count


async def test_restart_rejects_changed_contract_or_schedule_order(worker,tmp_path):
    from dream.core.phase_runner import PhaseRunner
    factory=configured(worker,tmp_path)
    original=phases(tmp_path)
    runner=PhaseRunner(worker,original,goal='full goal',state_dir=tmp_path/'schedules',evaluator_backend_factory=factory)
    completed=await runner.run()
    altered=PhaseRunner(worker,tuple(reversed(original)),goal='full goal',state_dir=tmp_path/'schedules',evaluator_backend_factory=factory)
    result=await altered.run(resume_schedule_id=completed.schedule_id)
    assert result.status=='error' and len(worker.invocations)==2


async def test_dispatch_budget_is_not_replenished_by_restart(worker,tmp_path):
    from dream.core.phase_runner import PhaseRunner,PhaseLimits
    factory=configured(worker,tmp_path)
    runner=PhaseRunner(worker,phases(tmp_path),goal='full goal',state_dir=tmp_path/'schedules',
        limits=PhaseLimits(max_dispatches=1),evaluator_backend_factory=factory)
    result=await runner.run()
    assert result.status=='budget' and len(worker.invocations)==1
    resumed=await runner.run(resume_schedule_id=result.schedule_id,resume_note='Owner explicitly resumes without new budget.')
    assert resumed.status=='budget' and len(worker.invocations)==1
    assert runner.inspect(result.schedule_id)['dispatches']==1


async def test_native_unsupported_refuses_before_any_worker(worker,tmp_path):
    from dream.core.phase_runner import PhaseRunner
    worker.backend=SimpleNamespace(managed_action_receipts=False)
    runner=PhaseRunner(worker,phases(tmp_path),goal='full goal',state_dir=tmp_path/'schedules')
    result=await runner.run()
    assert result.status=='unverified' and worker.calls==0


async def test_completed_child_repairs_parent_commit_at_spent_dispatch_cap(worker,tmp_path,monkeypatch):
    from dream.core.phase_runner import PhaseRunner,PhaseLimits
    from dream.core.run_state import RunState
    factory=configured(worker,tmp_path)
    runner=PhaseRunner(worker,phases(tmp_path)[:1],goal='full goal',state_dir=tmp_path/'schedules',
        limits=PhaseLimits(max_dispatches=2),evaluator_backend_factory=factory)
    real=RunState.record
    def fail(self,event,**kwargs):
        if event=='phase_accepted':raise OSError('parent commit unavailable after checked child')
        return real(self,event,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(RunState,'record',fail)
        first=await runner.run()
    assert first.status=='error' and len(worker.invocations)==1
    resumed=await runner.run(resume_schedule_id=first.schedule_id)
    assert resumed.status=='done' and resumed.completed_phases==1
    assert len(worker.invocations)==1 and runner.inspect(first.schedule_id)['dispatches']==2


async def test_wrong_project_refuses_before_worker(worker,tmp_path):
    from dream.core.phase_runner import PhaseRunner
    factory=configured(worker,tmp_path)
    runner=PhaseRunner(worker,phases(tmp_path),goal='full goal',project='foreign',
        state_dir=tmp_path/'schedules',evaluator_backend_factory=factory)
    result=await runner.run()
    assert result.status=='error' and worker.invocations==[]


async def test_unknown_host_check_requires_specific_report_then_only_review_retries(worker,tmp_path):
    from dream.core.phase_runner import PhaseRunner,PhaseSpec
    from dream.core.run_state import inspect_run
    factory=configured(worker,tmp_path);calls=[]
    def check():
        calls.append(1)
        if len(calls)==1:raise RuntimeError('fixture interrupted acceptance check')
        return True
    spec=PhaseSpec('phase-1','generic phase 1','- fixture check passes','check','a'*64,check)
    runner=PhaseRunner(worker,(spec,),goal='full goal',state_dir=tmp_path/'schedules',evaluator_backend_factory=factory)
    first=await runner.run()
    assert first.status=='error' and calls==[1]
    child=runner.inspect(first.schedule_id)['phases'][0]['run_id']
    token=inspect_run(tmp_path/'schedules'/'children',child)['state']['phase_check']['id']
    refused=await runner.run(resume_schedule_id=first.schedule_id,resume_note='Owner requests explicit continuation.')
    assert refused.status=='need_input' and calls==[1]
    resumed=await runner.run(resume_schedule_id=first.schedule_id,resume_note='Owner inspected the check effects.',
        reconciled_checks={token:'This check invocation was inspected and a new check is authorized.'})
    assert resumed.status=='done' and calls==[1,1]
    assert len(worker.invocations)==1


async def test_stop_requires_note_and_specific_action_reconciliation(worker,tmp_path):
    from claude_agent_sdk import SdkMcpTool
    from dream.core.phase_runner import PhaseRunner
    from dream.core.run_state import inspect_run
    from dream.core.task_checkpoints import wrap_tool
    factory=configured(worker,tmp_path);base=worker.ask;entered=asyncio.Event();interrupted=asyncio.Event();blocked=True
    async def handler(args):
        entered.set();await interrupted.wait();raise asyncio.CancelledError
    async def interrupt():interrupted.set()
    worker.interrupt=interrupt
    tool=wrap_tool(SdkMcpTool(name='fixture_wait',description='',input_schema={},handler=handler))
    async def ask(prompt):
        if blocked:await tool.handler({})
        async for event in base(prompt):yield event
    worker.ask=ask
    runner=PhaseRunner(worker,phases(tmp_path)[:1],goal='full goal',state_dir=tmp_path/'schedules',evaluator_backend_factory=factory)
    task=asyncio.create_task(runner.run());await entered.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    schedule_id=runner._journal.run_id
    child=runner.inspect(schedule_id)['phases'][0]['run_id']
    state=inspect_run(tmp_path/'schedules'/'children',child)['state']
    action=next(iter(state['managed']['unresolved']))
    blocked=False
    assert (await runner.run(resume_schedule_id=schedule_id)).status=='stopped'
    assert (await runner.run(resume_schedule_id=schedule_id,resume_note='Owner inspected the run.')).status=='need_input'
    resumed=await runner.run(resume_schedule_id=schedule_id,resume_note='Owner explicitly requests continuation.',
        reconciled_actions={action:'This exact interrupted action was inspected; do not replay.'})
    assert resumed.status=='done' and worker.invocations[0][0]==child


async def test_adapter_claim_without_ack_cannot_advance(worker,tmp_path):
    from dream.core.phase_runner import PhaseRunner
    factory=configured(worker,tmp_path)
    async def fake_boundary(coordinator):return True
    worker.backend.checked_task_boundary=fake_boundary
    runner=PhaseRunner(worker,phases(tmp_path),goal='full goal',state_dir=tmp_path/'schedules',evaluator_backend_factory=factory)
    result=await runner.run()
    assert result.status=='unverified' and result.completed_phases==0
    assert len(worker.invocations)==1


async def test_parent_accepted_summary_cannot_replace_child_evidence(worker,tmp_path):
    from dream.core.phase_runner import PhaseRunner
    from dream.core.run_state import RunState
    factory=configured(worker,tmp_path)
    runner=PhaseRunner(worker,phases(tmp_path)[:1],goal='full goal',state_dir=tmp_path/'schedules',evaluator_backend_factory=factory)
    first=await runner.run()
    journal=RunState(tmp_path/'schedules',first.schedule_id)
    state=journal.state['phase_schedule']
    state['phases'][0]['completion']['receipt_id']='invented-receipt'
    journal.record('fixture_corrupted_summary',incremental=True,phase_schedule=state)
    journal.close()
    resumed=await runner.run(resume_schedule_id=first.schedule_id)
    assert resumed.status=='error' and 'checked child receipt' in resumed.message
    assert len(worker.invocations)==1


async def test_expired_schedule_cannot_reset_deadline_on_resume(worker,tmp_path):
    from dream.core.phase_runner import PhaseRunner,PhaseLimits
    factory=configured(worker,tmp_path)
    now=[datetime(2026,10,5,tzinfo=timezone.utc)]
    base=worker.ask
    async def ask(prompt):
        async for event in base(prompt):yield event
        now[0]+=timedelta(seconds=3)
    worker.ask=ask
    runner=PhaseRunner(worker,phases(tmp_path),goal='full goal',state_dir=tmp_path/'schedules',
        limits=PhaseLimits(max_wall_seconds=2),clock=lambda:now[0],evaluator_backend_factory=factory)
    first=await runner.run()
    assert first.status=='budget' and len(worker.invocations)==1
    deadline=runner.inspect(first.schedule_id)['deadline']
    resumed=await runner.run(resume_schedule_id=first.schedule_id,resume_note='Owner resumes the same schedule.')
    assert resumed.status=='budget' and len(worker.invocations)==1
    assert runner.inspect(first.schedule_id)['deadline']==deadline


async def test_backwards_clock_refuses_new_dispatch(worker,tmp_path):
    from dream.core.phase_runner import PhaseRunner
    factory=configured(worker,tmp_path);now=[datetime(2026,10,5,tzinfo=timezone.utc)];base=worker.ask
    async def ask(prompt):
        async for event in base(prompt):yield event
        now[0]-=timedelta(seconds=1)
    worker.ask=ask
    runner=PhaseRunner(worker,phases(tmp_path),goal='full goal',state_dir=tmp_path/'schedules',
        clock=lambda:now[0],evaluator_backend_factory=factory)
    result=await runner.run()
    assert result.status=='error' and 'backwards' in result.message
    assert runner.inspect(result.schedule_id)['dispatches']==1


async def test_parent_dispatch_reservation_failure_never_calls_worker(worker,tmp_path,monkeypatch):
    from dream.core.phase_runner import PhaseRunner
    from dream.core.run_state import RunState
    factory=configured(worker,tmp_path)
    runner=PhaseRunner(worker,phases(tmp_path),goal='full goal',state_dir=tmp_path/'schedules',evaluator_backend_factory=factory)
    record=RunState.record
    def fail(self,event,**kwargs):
        if event=='phase_dispatch_reserved':raise OSError('fixture parent admission storage failure')
        return record(self,event,**kwargs)
    monkeypatch.setattr(RunState,'record',fail)
    result=await runner.run()
    assert result.status=='error' and worker.invocations==[]
    assert runner.inspect(result.schedule_id)['dispatches']==0


async def test_check_changing_reviewed_bytes_cannot_issue_completion(worker,tmp_path):
    from dream.core.phase_runner import PhaseRunner,PhaseSpec
    factory=configured(worker,tmp_path)
    def changed():
        (tmp_path/'artifact.txt').write_text('changed after independent inspection')
        return True
    spec=PhaseSpec('phase-1','generic phase 1','- artifact reviewed and checked','check','a'*64,changed)
    runner=PhaseRunner(worker,(spec,),goal='full goal',state_dir=tmp_path/'schedules',evaluator_backend_factory=factory)
    result=await runner.run()
    assert result.status=='error' and result.completed_phases==0
    assert runner.inspect(result.schedule_id)['phases'][0]['completion'] is None


async def test_unavailable_independent_reviewer_never_calls_host_check(worker,tmp_path):
    from dream.core.phase_runner import PhaseRunner,PhaseSpec
    configured(worker,tmp_path);calls=[]
    def check():calls.append(1);return True
    spec=PhaseSpec('phase-1','generic phase 1','- inspect actual artifact','check','a'*64,check)
    def unavailable(*a):raise RuntimeError('fixture reviewer unavailable')
    runner=PhaseRunner(worker,(spec,),goal='full goal',state_dir=tmp_path/'schedules',
        evaluator_backend_factory=unavailable)
    result=await runner.run()
    assert result.status=='unverified' and result.completed_phases==0 and calls==[]


@pytest.mark.parametrize('field,value',[
    ('id','../other'),('goal',''),('contract',' '),('check_name','x'*129),
    ('check_version','unversioned'),('check',None),
])
def test_malformed_phase_is_rejected_before_persistence(field,value):
    from dream.core.phase_runner import PhaseSpec
    values={'id':'phase-1','goal':'generic phase','contract':'- explicit criterion',
        'check_name':'named','check_version':'a'*64,'check':lambda:True}
    values[field]=value
    with pytest.raises(ValueError):PhaseSpec(**values)


def test_duplicate_or_oversized_schedule_is_rejected(worker,tmp_path):
    from dream.core.phase_runner import PhaseRunner
    spec=phases(tmp_path)[0]
    for items in ((spec,spec),(spec,)*33,()):
        with pytest.raises(ValueError):
            PhaseRunner(worker,items,goal='generic goal',state_dir=tmp_path/'unused')
    assert not (tmp_path/'unused').exists()


async def test_wall_deadline_interrupts_actual_handler_and_preserves_uncertainty(worker,tmp_path):
    from dream.core.phase_runner import PhaseRunner,PhaseLimits
    from dream.core.run_state import inspect_run
    factory=configured(worker,tmp_path);interrupted=asyncio.Event()
    async def handler(args):
        await interrupted.wait()
        raise asyncio.CancelledError
    async def interrupt():interrupted.set()
    worker.interrupt=interrupt;tool=tool_bundle(handler)
    async def ask(prompt):
        await tool.handler({})
        yield Event('assistant_done','STATUS: DONE')
    worker.ask=ask
    runner=PhaseRunner(worker,phases(tmp_path),goal='full goal',state_dir=tmp_path/'schedules',
        limits=PhaseLimits(max_wall_seconds=1),evaluator_backend_factory=factory)
    result=await runner.run()
    assert result.status=='budget' and result.completed_phases==0
    state=runner.inspect(result.schedule_id)
    child=inspect_run(tmp_path/'schedules'/'children',state['phases'][0]['run_id'])['state']
    assert child['uncertain'] and child['managed']['unresolved']
    assert child['managed']['recovery']['pending']['kind']=='stopped'
    resumed=await runner.run(resume_schedule_id=result.schedule_id,resume_note='Owner requests resume; no budget replenishment.')
    assert resumed.status=='budget'
    assert runner.inspect(result.schedule_id)['dispatches']==1


async def test_runtime_source_version_change_refuses_resume(worker,tmp_path,monkeypatch):
    import dream.core.phase_runner as module
    factory=configured(worker,tmp_path)
    runner=module.PhaseRunner(worker,phases(tmp_path)[:1],goal='full goal',state_dir=tmp_path/'schedules',
        evaluator_backend_factory=factory)
    first=await runner.run()
    changed={**module._runtime_sources(),'loop.py':'f'*64}
    monkeypatch.setattr(module,'_runtime_sources',lambda:changed)
    resumed=await runner.run(resume_schedule_id=first.schedule_id)
    assert resumed.status=='error' and len(worker.invocations)==1


async def test_eight_phase_backlog_uses_independent_tasks_without_endurance_claim(worker,tmp_path):
    from dream.core.phase_runner import PhaseRunner,PhaseSpec,PhaseLimits
    factory=configured(worker,tmp_path)
    specs=tuple(PhaseSpec(f'phase-{i}',f'generic phase {i}',f'- artifact names phase {i}',
        f'check-{i}','a'*64,lambda i=i:(tmp_path/'artifact.txt').read_text()==f'generic phase {i}') for i in range(8))
    runner=PhaseRunner(worker,specs,goal='complete fixed eight-phase schedule',state_dir=tmp_path/'schedules',
        limits=PhaseLimits(max_dispatches=16),evaluator_backend_factory=factory)
    result=await runner.run()
    state=runner.inspect(result.schedule_id)
    assert result.status=='done' and result.completed_phases==8 and state['dispatches']==16
    assert [goal for _,goal in worker.invocations]==[spec.goal for spec in specs]
    assert len({row['completion']['task_id'] for row in state['phases']})==8
    assert len({row['run_id'] for row in state['phases']})==8
    assert 'qualification' not in state and 'endurance' not in state
    assert (await runner.run(resume_schedule_id=result.schedule_id)).status=='done'
    assert len(worker.invocations)==8


@pytest.mark.parametrize('fault',['acknowledgement','archive','stop','stop_stale_evidence','stop_uncertain'])
async def test_checked_boundary_repair_at_spent_budget_without_any_new_dispatch(worker,tmp_path,monkeypatch,fault):
    from dream.core.phase_runner import PhaseRunner,PhaseLimits
    from dream.core.task_checkpoints import ManagedTaskCoordinator
    from dream.core.tool_artifacts import ArtifactStore
    from dream.core.run_state import inspect_run
    factory=configured(worker,tmp_path);checks=[]
    from dataclasses import replace
    spec=replace(phases(tmp_path)[0],check=lambda:checks.append(1) or True)
    now=[datetime(2026,10,5,tzinfo=timezone.utc)]
    runner=PhaseRunner(worker,(spec,),goal='full goal',state_dir=tmp_path/'schedules',
        limits=PhaseLimits(max_dispatches=2,max_wall_seconds=1),clock=lambda:now[0],
        evaluator_backend_factory=factory)
    def fail(*a,**kw):raise OSError('fixture checked boundary storage interruption')
    def stopped(coordinator, *args, **kwargs):
        coordinator.stop()
        raise asyncio.CancelledError()
    with monkeypatch.context() as patch:
        if fault=='acknowledgement':patch.setattr(ManagedTaskCoordinator,'acknowledge_boundary',fail)
        elif fault.startswith('stop'):patch.setattr(ManagedTaskCoordinator,'acknowledge_boundary',stopped)
        else:patch.setattr(ArtifactStore,'save',fail)
        if fault.startswith('stop'):
            with pytest.raises(asyncio.CancelledError):await runner.run()
            first=SimpleNamespace(schedule_id=runner._journal.run_id,status='stopped')
        else:first=await runner.run()
    assert first.status==('stopped' if fault.startswith('stop') else 'error') and len(worker.invocations)==1 and checks==[1]
    state=runner.inspect(first.schedule_id);child=state['phases'][0]['run_id']
    saved=inspect_run(tmp_path/'schedules'/'children',child)['state']
    boundary=saved['managed']['boundary']['id']
    assert saved['managed']['completed_receipt'] and saved['managed']['boundary']['status']=='prepared'
    if fault=='stop_stale_evidence':
        (tmp_path/'artifact.txt').write_text('Changed after independent review')
    elif fault=='stop_uncertain':
        from dream.core.run_state import RunState
        journal=RunState(tmp_path/'schedules'/'children',child)
        try:journal.record('fixture_uncertain_effect',uncertain=True)
        finally:journal.close()
    now[0]+=timedelta(seconds=5)
    refused=await runner.run(resume_schedule_id=first.schedule_id)
    assert refused.status==('stopped' if fault.startswith('stop') else 'need_input')
    resumed=await runner.run(resume_schedule_id=first.schedule_id,resume_note='Owner authorizes only settled checked-boundary reconciliation.')
    if fault in {'stop_stale_evidence','stop_uncertain'}:
        assert resumed.status=='error' and resumed.completed_phases==0
        assert len(worker.invocations)==1 and checks==[1]
        assert runner.inspect(first.schedule_id)['dispatches']==2
        return
    assert resumed.status=='done' and resumed.completed_phases==1
    assert len(worker.invocations)==1 and checks==[1]
    after=inspect_run(tmp_path/'schedules'/'children',child)['state']
    assert after['managed']['boundary']['id']==boundary
    assert after['managed']['boundary']['status']=='acknowledged'
    assert after['managed']['completed_receipt']==saved['managed']['completed_receipt']
    assert runner.inspect(first.schedule_id)['dispatches']==2
    if fault=='stop':
        assert after['managed']['recovery']==saved['managed']['recovery_before_stop']


async def test_boundary_only_loop_cannot_fall_back_to_work_or_review(worker,tmp_path):
    from dream.core.phase_runner import PhaseRunner,_PhaseLoop
    factory=configured(worker,tmp_path)
    runner=PhaseRunner(worker,phases(tmp_path),goal='full goal',state_dir=tmp_path/'schedules',evaluator_backend_factory=factory)
    loop=_PhaseLoop(runner,0,'fixture-reservation',boundary_repair=True)
    for call in (loop._drive('work'),loop._evaluate('goal'),loop._establish_contract('goal')):
        with pytest.raises(PermissionError,match='repair cannot'):await call
    assert worker.invocations==[]


async def test_programmatic_schedule_inherits_real_engine_task_project(worker,tmp_path):
    from dream.core.phase_runner import PhaseRunner
    from dream.memory.project import project_key
    from dream.memory.store import MemoryStore
    from dream.memory.tasks import TaskStore
    canonical=project_key(tmp_path)
    store=MemoryStore(tmp_path/'scoped-tasks.db')
    store.project=canonical
    try:
        worker.tasks=TaskStore(store);worker.project=canonical
        factory=configured(worker,tmp_path)
        with pytest.raises(ValueError,match='configured engine workspace'):
            PhaseRunner(worker,phases(tmp_path),goal='full goal',project='foreign',state_dir=tmp_path/'schedules')
        runner=PhaseRunner(worker,phases(tmp_path),goal='full goal',state_dir=tmp_path/'schedules',
            evaluator_backend_factory=factory)
        result=await runner.run()
        assert result.status=='done' and result.completed_phases==2
        assert runner.project==canonical
        for row in runner.inspect(result.schedule_id)['phases']:
            assert worker.tasks.get(row['completion']['task_id'])['project']==canonical
    finally:store.close()


async def test_phase_prompt_keeps_current_contract_without_loading_future_contracts(worker,tmp_path):
    from dream.core.phase_runner import PhaseRunner,PhaseSpec
    import json
    factory=configured(worker,tmp_path)
    prompts=[];ask=worker.ask
    async def capture(prompt):
        prompts.append(prompt)
        async for event in ask(prompt):yield event
    worker.ask=capture
    current_goal='Finish the current small step'
    future_goal='Next phase: '+'λ'*3000
    current_contract='- Current result names this exact goal'
    future_contract='- Future-only acceptance criterion: '+('future detail '*400).rstrip()
    specs=tuple(PhaseSpec(f'phase-{i}',goal,contract,f'check-{i}','a'*64,
        lambda expected=goal:(tmp_path/'artifact.txt').read_text()==expected)
        for i,(goal,contract) in enumerate(((current_goal,current_contract),(future_goal,future_contract))))
    runner=PhaseRunner(worker,specs,goal='Original complete schedule goal',state_dir=tmp_path/'schedules',
        evaluator_backend_factory=factory)
    result=await runner.run()
    assert result.status=='done' and result.completed_phases==2
    assert current_goal in prompts[0] and current_contract in prompts[0]
    assert future_contract not in prompts[0] and future_goal not in prompts[0]
    assert future_goal in prompts[1] and future_contract in prompts[1]
    assert len(prompts[0].encode('utf-8'))<6000
    hint=json.loads(prompts[0].split('Host-owned immutable phase schedule (JSON):\n',1)[1])
    assert hint['schedule_sha256']==runner.manifest_sha
    assert hint['backlog'][1]['id']=='phase-1'
    assert all('contract' not in row and 'goal' not in row for row in hint['backlog'])
    assert len(hint['upcoming'][0]['goal_hint'].encode('utf-8'))<=192
    saved=runner.inspect(result.schedule_id)
    assert saved['manifest']['phases'][1]['contract']==future_contract


def test_prior_phase_prompt_references_are_bounded_without_erasing_host_backlog(worker,tmp_path):
    from dream.core.phase_runner import PhaseRunner,PhaseSpec,_PhaseLoop
    specs=tuple(PhaseSpec(f'phase-{i}',f'Phase {i}','- Fixed acceptance',f'check-{i}','a'*64,
                         lambda:True) for i in range(12))
    runner=PhaseRunner(worker,specs,goal='Original goal',state_dir=tmp_path/'schedules')
    from copy import deepcopy
    # Exercise the actual prompt projection with a large settled prefix; these
    # caller fixtures confer no execution or artifact permission.
    rows=[{'id':f'prior-{i}','status':'accepted','run_id':f'run-{i}',
           'completion':{'run_id':f'run-{i}','receipt_id':str(i),'context_archive':{'handle':str(i)}}}
          for i in range(12)]
    state={'phase_schedule':{'phases':rows}}
    original=deepcopy(state)
    runner._journal=SimpleNamespace(state=state,path=tmp_path/'schedules'/'parent',run_id='schedule-fixture')
    loop=_PhaseLoop(runner,10,'run-10')
    loop.workspace=tmp_path/'child';loop.worker_workspace=tmp_path
    loop._resuming=False
    import json
    prompt=loop._seed(specs[0].goal,specs[0].contract)
    hint=json.loads(prompt.split('Host-owned immutable phase schedule (JSON):\n',1)[1])
    assert len(hint['checked_previous_phases'])==2
    assert [row['run_id'] for row in hint['checked_previous_phases']]==['run-8','run-9']
    assert hint['earlier_checked_phase_count']==8
    assert len(hint['backlog'])==12 and len(hint['upcoming'])<=2
    assert state==original
