"""Synthetic host checks, real ledgers; no provider or engine starts."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from dream.core.help_continuation import AssistancePolicy, HelpContinuation, budget_from_run
from dream.core.recovery import payload_digest
from dream.core.recovery_runtime import RecoveryRuntime
from dream.core.run_state import RunState
from test_frontier_help import inputs, reserve_help, Transport
from test_task_checkpoints import managed


def test_old_runtime_cannot_enable_assistance_late(managed):
    c,_=managed
    old=deepcopy(c._state['recovery'])
    assert RecoveryRuntime(c.journal.state,c._state).snapshot()==old
    with pytest.raises(ValueError):
        RecoveryRuntime(c.journal.state,c._state,assistance_policy=object())


def test_policy_is_strict_and_bounded():
    with pytest.raises(ValueError):
        AssistancePolicy('a'*64,'2099-01-01T00:00:00+00:00',12,'accept','b'*64,max_actions=True)


@pytest.fixture
async def assisted(managed,tmp_path):
    from dream.core.frontier_help import FrontierHelp
    from dream.core.help_continuation import HelpContinuation
    c,key=managed
    parent=RunState(tmp_path/'schedules')
    now=datetime.now(timezone.utc);deadline=(now+timedelta(hours=1)).isoformat()
    phase={'id':'phase1','goal':c.journal.state['goal'],'contract':c.journal.state['contract'],
           'check_name':'acceptance','check_version':'b'*64}
    manifest={'phases':[phase],'workspace':c.journal.state['worker_workspace'],'project':c._state['project'],
              'limits':{'max_dispatches':12,'max_wall_seconds':3600}}
    manifest_sha=payload_digest(manifest)
    parent.record('phase_schedule_created',phase_schedule=dict(version=1,manifest=manifest,manifest_sha256=manifest_sha,
        created_at=now.isoformat(),last_clock=now.isoformat(),deadline=deadline,cursor=0,dispatches=0,
        phases=[{'id':'phase1','run_id':c.journal.run_id,'status':'running','completion':None}]))
    c.journal.record('phase_bound',phase_binding=dict(schedule_id=parent.run_id,schedule_sha256=manifest_sha,
        phase_id='phase1',phase_index=0,phase_sha256=payload_digest(phase)))
    budget=budget_from_run(parent,c.journal)
    policy=AssistancePolicy(budget.identity,budget.deadline,budget.maximum,budget.check_name,budget.check_version)
    state=c._state;state.pop('recovery')
    state['recovery']=RecoveryRuntime(c.journal.state,state,assistance_policy=policy).snapshot()
    c.journal.record('fixture_initial_policy',managed=state)
    reserve_help(c)
    route,packet=inputs()
    await FrontierHelp(c).dispatch(route,packet,Transport())
    service=HelpContinuation(c.journal,authority=key)
    yield c,key,parent,service
    parent.close()


def budget(parent,c,kind=None):
    if kind:
        state=deepcopy(parent.state['phase_schedule'])
        state.update(dispatches=state['dispatches']+1,last_dispatch={'phase':0,'kind':kind},
                     last_clock=datetime.now(timezone.utc).isoformat())
        parent.record('phase_dispatch_reserved',phase_schedule=state)
    return budget_from_run(parent,c.journal)


def start(assisted):
    c,key,parent,service=assisted
    service.authorize(budget(parent,c),authority=key)
    token=service.begin_stage('worker',budget(parent,c,'worker'),authority=key)
    return c,key,parent,service,token


def observed_action(c,key,parent,service,stage,*,tool='read_fixture',arguments=None,result=None,evidence=''):
    """Synthetic future host admission, actual coordinator returned receipt.

    Existing coordinator admission remains closed. This fixture records the same
    managed intent shape to exercise the independent primitive's receipt guard.
    Parent activation must atomically wire both admissions before real dispatch.
    """
    from uuid import uuid4
    arguments=arguments or {'part':'new'}; result=result or {'content':[{'type':'text','text':'observed data'}]}
    call=payload_digest([tool,payload_digest(arguments)])
    token=service.reserve_action(stage,call,budget(parent,c),authority=key)
    state=c._state; action_id=uuid4().hex
    state['unresolved'][action_id]=dict(id=action_id,tool=tool,arguments_sha256=payload_digest(arguments),disposition='intent')
    state['action_revision']+=1
    c.journal.record('fixture_host_action_intent',managed=state)
    receipt=c.finish_action(action_id,'returned-success',result)
    service.finish_action(stage,token,receipt,authority=key,evidence_digest=evidence)
    return receipt


async def test_actual_returned_advice_bound_lease_preserves_origin_and_admission(assisted):
    c,key,parent,service,stage=start(assisted)
    original=deepcopy(c._state['recovery'])
    observed_action(c,key,parent,service,stage,evidence=payload_digest('actual novel data'))
    assert c._state['recovery']==original
    assert not c.actions_admitted and c.pending_recovery['kind']=='needs_help'
    with pytest.raises(PermissionError):c.begin_action('write_file',{})
    snapshot=service.inspect()
    from dream.core.recovery import assisted_projection
    projection=assisted_projection(snapshot['origin']['controller'],snapshot['observations'])
    assert projection['help_used']==1 and projection['attempts']==2
    assert projection['steps']==original['controller']['steps']+1
    assert projection['no_progress']==original['controller']['no_progress']
    assert original['controller']['calls'].items() <= projection['calls'].items()


async def test_one_worker_review_check_at_final_chain_never_completes_task(assisted):
    c,key,parent,service,worker=start(assisted)
    observed_action(c,key,parent,service,worker,evidence=payload_digest('source evidence'))
    service.finish_stage(worker,budget(parent,c),authority=key,passed=True)
    review=service.begin_stage('review',budget(parent,c,'independent_review'),authority=key)
    service.finish_stage(review,budget(parent,c),authority=key,passed=True)
    check=service.begin_stage('check',budget(parent,c),authority=key)
    calls=[]
    def actual_check():calls.append('executed');return True
    saved=service.execute_check(check,actual_check,budget(parent,c),authority=key,
                               check_name='acceptance',check_version='b'*64)
    assert calls==['executed'] and saved['status']=='ready_for_verification'
    assert saved['stages'][-1]['anchor']==saved['stages'][-2]['anchor']==saved['anchor']
    assert c.task['status']=='active' and not c.boundary_eligibility()['eligible']
    with pytest.raises(PermissionError):service.authorize(budget(parent,c),authority=key)


@pytest.mark.parametrize('stage',['worker','review','check'])
async def test_restart_unsettled_stage_never_replays(assisted,stage):
    c,key,parent,service,token=start(assisted)
    if stage!='worker':
        service.finish_stage(token,budget(parent,c),authority=key,passed=True)
        token=service.begin_stage('review',budget(parent,c,'independent_review'),authority=key)
    if stage=='check':
        service.finish_stage(token,budget(parent,c),authority=key,passed=True)
        token=service.begin_stage('check',budget(parent,c),authority=key)
    c.journal.close()
    journal=RunState(c.journal.path.parent,c.journal.run_id)
    try:
        restarted=HelpContinuation(journal,authority=key)
        assert restarted.inspect()['stage']['kind']==stage
        view=budget_from_run(parent,journal)
        with pytest.raises(PermissionError):restarted.begin_stage(stage,view,authority=key)
        with pytest.raises(PermissionError):restarted.finish_stage(token,view,authority=key,passed=True)
    finally:journal.close()


@pytest.mark.parametrize('mutation',['packet','outcome','scope','unknown','origin','policy'])
async def test_binding_mutation_refuses_authorization(assisted,mutation):
    c,key,parent,service=assisted
    data=deepcopy(c.journal.state)
    if mutation=='packet':data['frontier_help']['binding']['packet_sha256']='c'*64
    if mutation=='outcome':data['frontier_help']['outcome']['advice']='forged DONE'
    if mutation=='scope':data['frontier_help']['binding']['scope']['run_id']='foreign'
    if mutation=='unknown':data['frontier_help'].update(status='intent',outcome=None)
    if mutation=='origin':data['managed']['recovery']['pending']['sequence']+=1
    if mutation=='policy':data['managed']['recovery']['assistance_policy']['check_version']='c'*64
    # A packet digest is not compared with private packet bytes on disk, but a
    # completed lease must pin whatever valid durable adapter binding was read.
    if mutation=='packet':
        service.authorize(budget(parent,c),authority=key)
    c.journal.record('fixture_corruption',**{k:v for k,v in data.items() if k!='run_id'})
    with pytest.raises((ValueError,PermissionError)):
        if mutation=='packet':service.inspect()
        else:service.authorize(budget(parent,c),authority=key)


@pytest.mark.parametrize('bad',[True,0,9,-1,1.0])
def test_action_limit_validation(bad):
    with pytest.raises(ValueError):AssistancePolicy('a'*64,'2099-01-01T00:00:00+00:00',12,'accept','b'*64,max_actions=bad)


async def test_private_capability_and_parent_reservation_required(assisted):
    c,key,parent,service=assisted
    with pytest.raises(PermissionError):service.authorize(budget(parent,c),authority=object())
    service.authorize(budget(parent,c),authority=key)
    with pytest.raises(PermissionError):service.begin_stage('worker',budget(parent,c),authority=key)
    old=budget(parent,c);budget(parent,c,'worker')
    with pytest.raises(ValueError):service.begin_stage('worker',old,authority=key)


@pytest.mark.parametrize('reason',['stopped','uncertain','storage','interrupted'])
async def test_host_close_no_refund_or_reopen(assisted,reason):
    c,key,parent,service,stage=start(assisted);original=deepcopy(c._state['recovery'])
    saved=service.close(reason,authority=key)
    assert saved['status']=='closed' and c._state['recovery']==original
    with pytest.raises(PermissionError):service.begin_stage('worker',budget(parent,c),authority=key)


async def test_action_allowance_keeps_actual_check_slot_and_novelty_no_refill(assisted):
    c,key,parent,service,stage=start(assisted)
    for n in range(4):
        observed_action(c,key,parent,service,stage,arguments={'part':n},evidence=payload_digest(['source',n]))
    with pytest.raises(PermissionError):service.reserve_action(stage,payload_digest('fifth'),budget(parent,c),authority=key)
    assert service.inspect()['status']=='closed' and len(service.inspect()['observations'])==4


@pytest.mark.parametrize('point',['assistance_authorized','assistance_stage_intent','assistance_action_intent'])
@pytest.mark.parametrize('after',[False,True])
async def test_journal_failure_returns_no_entry_token(assisted,monkeypatch,point,after):
    c,key,parent,service=assisted
    if point!='assistance_authorized':service.authorize(budget(parent,c),authority=key)
    if point=='assistance_action_intent':stage=service.begin_stage('worker',budget(parent,c,'worker'),authority=key)
    original=c.journal.record
    def fail(event,**kw):
        if event==point:
            if after:original(event,**kw)
            raise OSError('synthetic storage failure')
        return original(event,**kw)
    monkeypatch.setattr(c.journal,'record',fail)
    with pytest.raises(OSError):
        if point=='assistance_authorized':service.authorize(budget(parent,c),authority=key)
        elif point=='assistance_stage_intent':service.begin_stage('worker',budget(parent,c,'worker'),authority=key)
        else:service.reserve_action(stage,payload_digest('action'),budget(parent,c),authority=key)
    with pytest.raises(PermissionError):service.authorize(budget(parent,c),authority=key)
    assert c.pending_recovery['kind']=='needs_help'


def reach_check(assisted):
    c,key,parent,service,worker=start(assisted)
    service.finish_stage(worker,budget(parent,c),authority=key,passed=True)
    review=service.begin_stage('review',budget(parent,c,'independent_review'),authority=key)
    service.finish_stage(review,budget(parent,c),authority=key,passed=True)
    check=service.begin_stage('check',budget(parent,c),authority=key)
    return c,key,parent,service,check


async def test_model_pass_cannot_substitute_for_actual_check(assisted):
    c,key,parent,service,check=reach_check(assisted)
    with pytest.raises(PermissionError):service.finish_stage(check,budget(parent,c),authority=key,passed=True)
    assert service.inspect()['stage']['kind']=='check'


@pytest.mark.parametrize('result',[False,None,1,'PASS',{},[]])
async def test_actual_nonboolean_or_failed_check_seals_without_refund(assisted,result):
    c,key,parent,service,check=reach_check(assisted);calls=[];origin=deepcopy(c._state['recovery'])
    def run():calls.append(1);return result
    if type(result) is bool:
        service.execute_check(check,run,budget(parent,c),authority=key,check_name='acceptance',check_version='b'*64)
    else:
        with pytest.raises(ValueError):service.execute_check(check,run,budget(parent,c),authority=key,check_name='acceptance',check_version='b'*64)
    assert calls==[1] and service.inspect()['status']=='closed' and c._state['recovery']==origin
    with pytest.raises(PermissionError):service.execute_check(check,run,budget(parent,c),authority=key,check_name='acceptance',check_version='b'*64)
    assert calls==[1]


@pytest.mark.parametrize('change',['name','version','async','noncallable'])
async def test_wrong_host_check_never_executes(assisted,change):
    c,key,parent,service,check=reach_check(assisted);calls=[]
    def run():calls.append(1);return True
    async def asynchronous():calls.append(1);return True
    callback=asynchronous if change=='async' else None if change=='noncallable' else run
    with pytest.raises(ValueError):service.execute_check(check,callback,budget(parent,c),authority=key,
        check_name='wrong' if change=='name' else 'acceptance',check_version='c'*64 if change=='version' else 'b'*64)
    assert calls==[]


async def test_ready_evidence_refuses_new_untracked_action_chain(assisted):
    c,key,parent,service,check=reach_check(assisted)
    service.execute_check(check,lambda:True,budget(parent,c),authority=key,check_name='acceptance',check_version='b'*64)
    state=c._state;state['action_revision']+=1;state['evidence_chain']='c'*64
    c.journal.record('fixture_outside_action',managed=state)
    with pytest.raises((ValueError,PermissionError)):service.inspect()


@pytest.mark.parametrize('fault',['unknown','bool_version','stage_skip','duplicate_stage','missing_observation',
                                'origin_help_refund','origin_intervention_refund','wrong_check_digest','anchor','allowance'])
async def test_strict_snapshot_corruption_is_not_previewed_as_valid(assisted,fault):
    c,key,parent,service,check=reach_check(assisted)
    service.execute_check(check,lambda:True,budget(parent,c),authority=key,check_name='acceptance',check_version='b'*64)
    saved=service.inspect()
    if fault=='unknown':saved['model_override']='continue'
    elif fault=='bool_version':saved['version']=True
    elif fault=='stage_skip':saved['stage_index']=2
    elif fault=='duplicate_stage':saved['stages'][1]['id']=saved['stages'][0]['id']
    elif fault=='missing_observation':saved['observations']=[]
    elif fault=='origin_help_refund':saved['origin']['controller']['help_used']=0
    elif fault=='origin_intervention_refund':saved['origin']['total_interventions']=0
    elif fault=='wrong_check_digest':saved['observations'][0]['check_digest']='c'*64
    elif fault=='anchor':saved['anchor']['revision']+=2
    elif fault=='allowance':saved['policy']['max_actions']=8
    c.journal.record('fixture_corruption',help_continuation=saved)
    with pytest.raises((ValueError,PermissionError)):service.inspect()


@pytest.mark.parametrize('mode',['phase','dispatch','deadline'])
async def test_exhausted_original_cap_refuses_authorization(assisted,mode):
    c,key,parent,service=assisted
    if mode=='phase':
        state=c._state
        state['recovery']['controller']['policy']['phase_step_budget']=state['recovery']['controller']['steps']+1
        c.journal.record('fixture_minimal_remaining_budget',managed=state)
    elif mode=='dispatch':
        schedule=deepcopy(parent.state['phase_schedule']);schedule['dispatches']=12
        parent.record('fixture_used_dispatches',phase_schedule=schedule)
    else:service.clock=lambda:datetime.fromisoformat(parent.state['phase_schedule']['deadline'])
    with pytest.raises(PermissionError):service.authorize(budget(parent,c),authority=key)
    assert service.inspect() is None


@pytest.mark.parametrize('mode',['deadline','reverse_clock','uncertain','stop'])
async def test_new_stop_or_budget_fault_durably_closes_existing_stage(assisted,mode):
    c,key,parent,service,stage=start(assisted)
    if mode=='deadline':service.clock=lambda:datetime.fromisoformat(parent.state['phase_schedule']['deadline'])
    elif mode=='reverse_clock':service.clock=lambda:datetime.fromisoformat(service.inspect()['last_clock'])-timedelta(seconds=1)
    elif mode=='uncertain':c.journal.record('fixture_uncertain',uncertain=True)
    else:c.stop()
    with pytest.raises(PermissionError):service.reserve_action(stage,payload_digest('next'),budget(parent,c),authority=key)
    assert service.inspect()['status']=='closed'


async def test_repeated_trigger_closes_instead_of_third_intervention(assisted):
    c,key,parent,service,stage=start(assisted)
    observed_action(c,key,parent,service,stage,tool='fixture_read',arguments={'same':'generic'})
    assert service.inspect()['reason']=='recovery_signal'
    assert c._state['recovery']['total_interventions']==2 and c._state['recovery']['controller']['help_used']==1


@pytest.mark.parametrize('mutation',['new_field','bool_version','none_policy','changed_policy'])
def test_runtime_v2_rejects_bad_schema_and_late_policy(managed,mutation):
    c,_=managed;state=c._state
    if mutation=='changed_policy':
        with pytest.raises(ValueError):RecoveryRuntime(c.journal.state,state,
            assistance_policy=AssistancePolicy('a'*64,'2099-01-01T00:00:00+00:00',12,'accept','b'*64))
        return
    if mutation=='new_field':state['recovery']['assistance_policy']={}
    elif mutation=='bool_version':state['recovery']['version']=True
    else:state['recovery'].update(version=2,assistance_policy=None)
    with pytest.raises(ValueError):RecoveryRuntime(c.journal.state,state)


async def test_budget_view_closed_parent_and_foreign_child_refused(assisted,tmp_path):
    c,key,parent,service=assisted
    other=RunState(tmp_path/'foreign')
    try:
        other.record('created',**{k:v for k,v in c.journal.state.items() if k!='run_id'})
        with pytest.raises(ValueError):budget_from_run(parent,other)
    finally:other.close()
    parent.close()
    with pytest.raises(PermissionError):budget_from_run(parent,c.journal)


async def test_worker_review_cannot_borrow_check_allowance_or_reorder(assisted):
    c,key,parent,service,worker=start(assisted)
    with pytest.raises(PermissionError):service.begin_stage('review',budget(parent,c,'independent_review'),authority=key)
    service.finish_stage(worker,budget(parent,c),authority=key,passed=True)
    with pytest.raises(PermissionError):service.begin_stage('check',budget(parent,c),authority=key)


async def test_actual_failed_managed_receipt_closes_and_preserves_uncertain_effect(assisted):
    from uuid import uuid4
    c,key,parent,service,worker=start(assisted)
    arguments={'path':'generic'};tool='write_fixture'
    slot=service.reserve_action(worker,payload_digest([tool,payload_digest(arguments)]),budget(parent,c),authority=key)
    state=c._state;action_id=uuid4().hex
    state['unresolved'][action_id]=dict(id=action_id,tool=tool,arguments_sha256=payload_digest(arguments),disposition='intent')
    state['action_revision']+=1
    c.journal.record('fixture_host_action_intent',managed=state)
    receipt=c.finish_action(action_id,'error',{'is_error':True,'content':[]})
    service.finish_action(worker,slot,receipt,authority=key,evidence_digest=payload_digest('model says done'))
    saved=service.inspect()
    assert saved['status']=='closed' and saved['reason']=='failed_action'
    assert saved['observations'][0]['evidence_digest']=='' and action_id in c.unresolved_actions


async def test_forged_receipt_without_actual_managed_chain_is_refused(assisted):
    c,key,parent,service,worker=start(assisted)
    slot=service.reserve_action(worker,payload_digest('call'),budget(parent,c),authority=key)
    with pytest.raises(ValueError):service.finish_action(worker,slot,{'disposition':'returned-success'},authority=key)
    assert service.inspect()['action']['id']==slot


async def test_action_intent_survives_restart_without_another_reservation(assisted):
    c,key,parent,service,worker=start(assisted)
    slot=service.reserve_action(worker,payload_digest('call'),budget(parent,c),authority=key)
    c.journal.close();journal=RunState(c.journal.path.parent,c.journal.run_id)
    try:
        restarted=HelpContinuation(journal,authority=key)
        assert restarted.inspect()['action']['id']==slot
        with pytest.raises(PermissionError):restarted.begin_stage('worker',budget_from_run(parent,journal),authority=key)
    finally:journal.close()


async def test_two_host_instances_cannot_reserve_the_same_stage(assisted):
    from concurrent.futures import ThreadPoolExecutor
    c,key,parent,service=assisted
    service.authorize(budget(parent,c),authority=key);view=budget(parent,c,'worker')
    other=HelpContinuation(c.journal,authority=key)
    def reserve(adapter):
        try:return adapter.begin_stage('worker',view,authority=key)
        except PermissionError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(reserve,[service,other]))
    assert sum(item is not None for item in results)==1
    assert service.inspect()['stage']['kind']=='worker'


@pytest.mark.parametrize('fault',['exception','interrupt','deadline'])
async def test_actual_check_fault_never_reexecutes(assisted,fault):
    c,key,parent,service,check=reach_check(assisted);calls=[]
    class Interrupted(BaseException):pass
    def actual_check():
        calls.append(1)
        if fault=='exception':raise OSError('synthetic checker fault')
        if fault=='interrupt':raise Interrupted()
        service.clock=lambda:datetime.fromisoformat(parent.state['phase_schedule']['deadline'])
        return True
    with pytest.raises((OSError,Interrupted,PermissionError)):
        service.execute_check(check,actual_check,budget(parent,c),authority=key,check_name='acceptance',check_version='b'*64)
    assert calls==[1] and service.inspect()['status']=='closed'
    if fault=='deadline':assert service.inspect()['observations'][-1]['check_passed'] is True
    with pytest.raises(PermissionError):service.execute_check(check,actual_check,budget(parent,c),authority=key,check_name='acceptance',check_version='b'*64)
    assert calls==[1]


@pytest.mark.parametrize('point',['assistance_action_observed','assistance_stage_observed'])
async def test_observation_write_loss_seals_current_host_and_retains_intent(assisted,monkeypatch,point):
    c,key,parent,service,worker=start(assisted);original=c.journal.record
    def fail(event,**kw):
        if event==point:raise OSError('synthetic observation persistence loss')
        return original(event,**kw)
    monkeypatch.setattr(c.journal,'record',fail)
    with pytest.raises(OSError):
        if point=='assistance_action_observed':observed_action(c,key,parent,service,worker)
        else:service.finish_stage(worker,budget(parent,c),authority=key,passed=True)
    with pytest.raises(PermissionError):service.finish_stage(worker,budget(parent,c),authority=key,passed=True)
    assert service.inspect()['stage']['id']==worker.id


async def test_predeclared_policy_roundtrip_and_impossible_late_creation(managed):
    c,_=managed;state=c._state
    policy=AssistancePolicy('a'*64,'2099-01-01T00:00:00+00:00',12,'accept','b'*64)
    state.pop('recovery');state['action_revision']=2
    with pytest.raises(ValueError):RecoveryRuntime(c.journal.state,state,assistance_policy=policy)
    state['action_revision']=0
    saved=RecoveryRuntime(c.journal.state,state,assistance_policy=policy).snapshot()
    state['recovery']=saved
    assert saved['version']==2 and RecoveryRuntime(c.journal.state,state).snapshot()==saved
    with pytest.raises(ValueError):RecoveryRuntime(c.journal.state,state,
        assistance_policy=AssistancePolicy('a'*64,'2099-01-01T00:00:00+00:00',12,'changed','b'*64))


@pytest.mark.parametrize('mutation',['dispatch_bool','reverse_deadline','manifest','phase_identity','check_version','scope'])
async def test_actual_parent_budget_invalidity_refused(assisted,mutation):
    c,key,parent,service=assisted;schedule=deepcopy(parent.state['phase_schedule'])
    if mutation=='dispatch_bool':schedule['dispatches']=True
    elif mutation=='reverse_deadline':schedule['deadline']='2000-01-01T00:00:00+00:00'
    elif mutation=='manifest':schedule['manifest']['limits']['max_dispatches']=512
    elif mutation=='phase_identity':schedule['phases'][0]['run_id']='foreign'
    elif mutation=='check_version':schedule['manifest']['phases'][0]['check_version']='c'*64
    else:schedule['manifest']['workspace']='/foreign'
    parent.record('fixture_bad_budget',phase_schedule=schedule)
    with pytest.raises(ValueError):budget_from_run(parent,c.journal)


async def test_budget_can_predeclare_policy_before_first_managed_attachment(assisted):
    c,key,parent,service=assisted
    original=deepcopy(c.journal.state)
    c.journal.record('fixture_before_initial_attachment',managed=None)
    view=budget_from_run(parent,c.journal)
    policy=AssistancePolicy(view.identity,view.deadline,view.maximum,view.check_name,view.check_version)
    initial=deepcopy(original['managed'])
    initial.update(action_revision=0,unresolved={},recent=[])
    initial.pop('recovery')
    saved=RecoveryRuntime(c.journal.state,initial,assistance_policy=policy).snapshot()
    assert saved['version']==2 and saved['controller']['steps']==0
    assert saved['assistance_policy']['budget_identity']==view.identity


async def test_next_phase_policy_can_read_prior_phase_dispatch_without_borrowing_it(assisted):
    c,key,parent,service=assisted
    schedule=deepcopy(parent.state['phase_schedule'])
    current=schedule['manifest']['phases'][0]
    prior={**current,'id':'prior'}
    schedule['manifest']['phases']=[prior,current]
    schedule['manifest_sha256']=payload_digest(schedule['manifest'])
    schedule.update(cursor=1,dispatches=2,last_dispatch={'phase':0,'kind':'independent_review'},
        phases=[{'id':'prior','run_id':'prior-run','status':'accepted','completion':{}},schedule['phases'][0]])
    parent.record('fixture_next_phase',phase_schedule=schedule)
    binding={**c.journal.state['phase_binding'],'schedule_sha256':schedule['manifest_sha256'],'phase_index':1}
    c.journal.record('fixture_next_phase_binding',phase_binding=binding,managed=None)
    view=budget_from_run(parent,c.journal)
    assert view.used==2 and view.last_kind is None


async def test_parent_clock_rollback_before_authorization_persists_no_lease(assisted):
    c,key,parent,service=assisted
    view=budget(parent,c)
    service.clock=lambda:datetime.fromisoformat(parent.state['phase_schedule']['last_clock'])-timedelta(days=1)
    before=c.journal.seq
    with pytest.raises(PermissionError):service.authorize(view,authority=key)
    assert c.journal.seq==before and service.inspect() is None


@pytest.mark.parametrize('kind',['worker','review'])
async def test_parent_reservation_clock_rollback_refuses_stage_token(assisted,kind):
    c,key,parent,service=assisted
    service.authorize(budget(parent,c),authority=key)
    if kind=='review':
        worker=service.begin_stage('worker',budget(parent,c,'worker'),authority=key)
        service.finish_stage(worker,budget(parent,c),authority=key,passed=True)
    lease_clock=datetime.fromisoformat(service.inspect()['last_clock'])
    schedule=deepcopy(parent.state['phase_schedule'])
    schedule.update(last_clock=(lease_clock+timedelta(seconds=20)).isoformat(),dispatches=schedule['dispatches']+1,
        last_dispatch={'phase':0,'kind':'worker' if kind=='worker' else 'independent_review'})
    parent.record('phase_dispatch_reserved',phase_schedule=schedule)
    service.clock=lambda:lease_clock+timedelta(seconds=10)
    with pytest.raises(PermissionError):service.begin_stage(kind,budget(parent,c),authority=key)
    assert service.inspect()['status']=='closed' and service.inspect()['stage'] is None


async def test_parent_clock_advance_before_check_refuses_callback_entry(assisted):
    c,key,parent,service,check=reach_check(assisted)
    lease_clock=datetime.fromisoformat(service.inspect()['last_clock'])
    schedule=deepcopy(parent.state['phase_schedule']);schedule['last_clock']=(lease_clock+timedelta(seconds=20)).isoformat()
    parent.record('fixture_parent_clock_observed',phase_schedule=schedule)
    service.clock=lambda:lease_clock+timedelta(seconds=10)
    called=[]
    def actual_check():called.append(True);return True
    with pytest.raises(PermissionError):
        service.execute_check(check,actual_check,budget(parent,c),authority=key,check_name='acceptance',check_version='b'*64)
    assert called==[] and service.inspect()['status']=='closed'
