"""Actual wrapped handlers under host-only assisted admission; no providers."""
from copy import deepcopy

import pytest
from claude_agent_sdk import SdkMcpTool

from dream.core.task_checkpoints import ManagedTaskCoordinator, bind, wrap_tool
from test_help_continuation import assisted, budget
from test_task_checkpoints import managed, evidence


def admission(assisted):
    old,key,parent,_=assisted
    c=ManagedTaskCoordinator(old.journal,old.tasks,verification_authority=key,
        recovery_check_authority=key,assistance_authority=key,
        assistance_budget=lambda:budget(parent,old))
    c.attach()
    return c,key,parent


def start_worker(c,key,parent):
    c.authorize_assistance(authority=key)
    budget(parent,c,'worker')
    c.begin_assisted_stage('worker',authority=key)
    c.open_actions()


def tool(handler):
    return wrap_tool(SdkMcpTool(name='actual_fixture',description='Synthetic fixture action',
        input_schema={},handler=handler))


async def test_real_handler_then_review_and_check_preserve_original_recovery(assisted):
    c,key,parent=admission(assisted)
    origin=deepcopy(c._state['recovery']);revision=c._state['action_revision'];calls=[]
    async def handler(args):calls.append(args);return {'content':[{'type':'text','text':'new actual result'}]}
    actual=tool(handler)
    start_worker(c,key,parent)
    with bind(c):await actual.handler({'part':'changed'})
    assert calls==[{'part':'changed'}] and c._state['action_revision']==revision+2
    assert c._state['recovery']==origin
    assert c.assistance_state()['observations'][0]['call_digest']
    c.finish_assisted_stage(passed=True,authority=key)
    assert not c.actions_admitted and c.pending_recovery['kind']=='needs_help'
    budget(parent,c,'independent_review')
    c.begin_assisted_stage('review',authority=key)
    c.finish_assisted_stage(passed=True,authority=key)
    c.begin_assisted_stage('check',authority=key)
    invoked=[]
    def check():invoked.append('actual');return True
    c.execute_assisted_check('acceptance',check,check_version='b'*64,authority=key)
    assert invoked==['actual'] and c.pending_recovery is None and not c.actions_admitted
    assert c._state['recovery']==origin and c.task['status']!='done'
    with pytest.raises(PermissionError):c.begin_action('actual_fixture',{'extra':True})
    receipt=c.record_verification(authority=key,verdict='PASS',inspected=evidence(c),assessment={'review':'actual fixture'})
    c.complete_task(receipt['id'])
    assert c.task['status']=='done' and c.boundary_eligibility()['eligible']


async def test_unsettled_worker_token_cannot_be_reconstructed(assisted):
    c,key,parent=admission(assisted);start_worker(c,key,parent)
    reopened=ManagedTaskCoordinator(c.journal,c.tasks,assistance_authority=key,
        assistance_budget=lambda:budget(parent,c))
    reopened.attach()
    assert not reopened.actions_admitted and reopened.pending_recovery['kind']=='needs_help'
    with pytest.raises(PermissionError):reopened.begin_action('actual_fixture',{})
    with pytest.raises(PermissionError):reopened.begin_assisted_stage('worker',authority=key)


@pytest.mark.parametrize('wrong',['authorize','begin','finish','check'])
async def test_model_cannot_supply_assistance_authority(assisted,wrong):
    c,key,parent=admission(assisted)
    with pytest.raises(PermissionError):
        if wrong=='authorize':c.authorize_assistance(authority=object())
        elif wrong=='begin':c.begin_assisted_stage('worker',authority=object())
        elif wrong=='finish':c.finish_assisted_stage(passed=True,authority=object())
        else:c.execute_assisted_check('acceptance',lambda:True,check_version='b'*64,authority=object())
    assert c.journal.state.get('help_continuation') is None and not c.actions_admitted


async def test_failed_handler_closes_lease_and_retains_actual_uncertainty(assisted):
    c,key,parent=admission(assisted);start_worker(c,key,parent)
    calls=[]
    async def handler(args):calls.append(args);raise RuntimeError('synthetic handler failure')
    actual=tool(handler)
    with bind(c),pytest.raises(RuntimeError):await actual.handler({'part':'changed'})
    assert len(calls)==1 and c.unresolved_actions and not c.actions_admitted
    assert c.assistance_state()['status']=='closed'
    with bind(c),pytest.raises(PermissionError):await actual.handler({'part':'repeat'})
    assert len(calls)==1


async def test_action_receipt_survives_assisted_observation_storage_failure(assisted,monkeypatch):
    c,key,parent=admission(assisted);start_worker(c,key,parent)
    actual_record=c.journal.record;calls=[]
    def fail(event,**kwargs):
        if event=='assistance_action_observed':raise OSError('synthetic assisted storage failure')
        return actual_record(event,**kwargs)
    monkeypatch.setattr(c.journal,'record',fail)
    async def handler(args):calls.append(args);return {'content':[{'type':'text','text':'saved effect'}]}
    actual=tool(handler)
    with bind(c),pytest.raises(OSError):await actual.handler({'part':'changed'})
    assert c._state['recent'][-1]['disposition']=='returned-success'
    assert not c.actions_admitted and len(calls)==1
    with bind(c),pytest.raises(PermissionError):await actual.handler({'part':'repeat'})
    assert len(calls)==1


async def test_stop_closes_assistance_and_owner_resume_does_not_refill(assisted):
    c,key,parent=admission(assisted);start_worker(c,key,parent)
    c.stop()
    assert c.pending_recovery['kind']=='stopped' and c.assistance_state()['status']=='closed'
    c.resume_stopped(note='Explicit fixture owner inspected the stopped turn')
    assert c.pending_recovery['kind']=='needs_help' and not c.actions_admitted
    assert c._state['recovery']['controller']['help_used']==1
    with pytest.raises(PermissionError):c.authorize_assistance(authority=key)


async def test_stop_inside_entered_handler_still_settles_real_receipt(assisted):
    c,key,parent=admission(assisted);start_worker(c,key,parent)
    async def handler(args):
        c.stop()
        return {'content':[{'type':'text','text':'returned after Stop'}]}
    with bind(c):result=await tool(handler).handler({'part':'changed'})
    assert result['content'][0]['text']=='returned after Stop'
    assert c._state['recent'][-1]['disposition']=='returned-success'
    assert not c.unresolved_actions and c.pending_recovery['kind']=='stopped'
    assert c.assistance_state()['status']=='closed' and not c._storage_failed


async def test_ordinary_check_and_verification_cannot_skip_assisted_stages(assisted):
    c,key,parent=admission(assisted);start_worker(c,key,parent)
    invoked=[]
    with pytest.raises(PermissionError):c.execute_check('acceptance',lambda:invoked.append(1) or True,authority=key)
    with pytest.raises(ValueError):c.record_verification(authority=key,verdict='PASS',inspected=evidence(c),assessment={})
    assert invoked==[] and c.task['status']!='done'


@pytest.mark.parametrize('wrong', ['status', 'check_name', 'check_version', 'action_revision', 'action_chain'])
async def test_stale_phase_check_intent_cannot_execute_assisted_callback(assisted, wrong):
    c,key,parent=admission(assisted)
    start_worker(c,key,parent)
    c.finish_assisted_stage(passed=True,authority=key)
    budget(parent,c,'independent_review')
    c.begin_assisted_stage('review',authority=key)
    c.finish_assisted_stage(passed=True,authority=key)
    c.begin_assisted_stage('check',authority=key)
    state=c._state
    token=dict(id='fixture-phase-check',status='started',phase_id='fixture',check_name='acceptance',
        check_version='b'*64,action_revision=state['action_revision'],action_chain=state['evidence_chain'])
    token[wrong] = state['action_revision']+1 if wrong=='action_revision' else 'changed'
    c.journal.record('fixture_stale_phase_intent',incremental=True,phase_check=token)
    calls=[]
    with pytest.raises(ValueError,match='exact current phase intent'):
        c.execute_assisted_check('acceptance',lambda:calls.append(1) or True,
            check_version='b'*64,authority=key)
    assert calls==[] and c.task['status']!='done' and not c.actions_admitted
    assert c.assistance_state()['status']=='assisted'
