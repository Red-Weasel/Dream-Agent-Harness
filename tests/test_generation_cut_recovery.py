"""Trusted generation observations with real local journals; no inference."""
from copy import deepcopy
from dataclasses import fields
from inspect import signature
import json

import pytest

from dream.core.recovery import EvidenceAuthority, RecoveryController, RecoveryObservation, payload_digest
from dream.core.recovery_runtime import RecoveryRuntime
from dream.core.run_state import RunState
from dream.core.stream_text_repetition import TextRepetitionDetector
from dream.core.task_checkpoints import ManagedTaskCoordinator, bind
from test_assisted_admission import admission, start_worker
from test_help_continuation import assisted
from test_task_checkpoints import managed, evidence


LINE='A generation repeatedly promises the same unfinished generic artifact.'


def cut():
    return TextRepetitionDetector().observe('content',(LINE+'\n')*6)


def observation(sequence, **kwargs):
    assert 'generation_cut' in {f.name for f in fields(RecoveryObservation)}, 'Typed generation-cut observation is absent.'
    return RecoveryObservation(sequence,**kwargs)


def controller():
    return RecoveryController(payload_digest('task'),payload_digest('phase'),evidence_authority=EvidenceAuthority())


def host_observe(c, value=None):
    method=getattr(c,'observe_generation_cut',None)
    assert callable(method), 'Managed host generation-cut admission API is absent.'
    return method(cut() if value is None else value)


def worker_phase(c):
    c.journal.record('fixture_worker_started',phase='worker_running',uncertain=False)


@pytest.mark.parametrize('bad',[0,1,None,'true',[],{}])
def test_generation_signal_requires_an_actual_boolean(bad):
    with pytest.raises(ValueError):observation(1,generation_cut=bad)


def test_false_default_preserves_original_observation_fingerprint():
    implicit,explicit=controller(),controller()
    call=payload_digest('actual prior call')
    implicit.observe(observation(1,call_digest=call))
    explicit.observe(observation(1,call_digest=call,generation_cut=False))
    assert implicit.to_snapshot()==explicit.to_snapshot()
    assert implicit.to_snapshot()['last_observation']==payload_digest(dict(
        sequence=1,call=call,error='',check='',passed=None,evidence='',evidence_kind=''))
    assert implicit.to_snapshot()['reason']==''


def test_each_real_cut_spends_one_original_intervention_then_reserves_help():
    c=controller()
    actions=[]
    for seq in (1,2,3):
        actions.append(c.observe(observation(seq,generation_cut=True)))
    assert [a.kind for a in actions]==['recover','recover','needs_help']
    assert all(a.reason=='generation_repetition' for a in actions)
    saved=c.to_snapshot()
    assert saved['steps']==saved['no_progress']==3
    assert saved['attempts']==2 and saved['help_used']==1
    assert saved['calls']==saved['checks']=={} and saved['evidence']==[]
    assert actions[-1].help_request.signal_digest==saved['last_observation']
    assert RecoveryController.from_snapshot(saved,evidence_authority=EvidenceAuthority()).to_snapshot()==saved


def test_exact_redelivery_does_not_reserve_an_extra_intervention():
    c=controller();value=observation(1,generation_cut=True)
    assert c.observe(value).kind=='recover'
    saved=c.to_snapshot()
    c.observe(value)
    assert c.to_snapshot()==saved


def test_runtime_uses_previously_spent_task_budget_instead_of_refilling(managed):
    c,_=managed;runtime=RecoveryRuntime(c.journal.state,c._state)
    for _ in range(3):runtime.observe(call_digest=payload_digest('actual same earlier operation'))
    assert runtime.pending['kind']=='recover' and runtime.total_interventions==1
    runtime.consume_intervention()
    assert 'generation_cut' in signature(runtime.observe).parameters
    first=runtime.observe(generation_cut=True)
    assert first['total_interventions']==2 and first['pending']['kind']=='recover'
    runtime.consume_intervention()
    second=runtime.observe(generation_cut=True)
    assert second['pending']['kind']=='needs_help' and second['total_interventions']==2
    assert second['controller']['steps']==5 and second['controller']['help_used']==1


def test_host_cut_is_one_digest_only_journal_observation_not_a_handler(managed):
    c,key=managed;worker_phase(c)
    before=c._state;seq=c.journal.seq
    with bind(c):assert host_observe(c) is True
    after=c._state
    assert c.journal.seq==seq+1
    assert after['action_revision']==before['action_revision']
    assert after['recent']==before['recent']==[] and after['unresolved']=={}
    assert after['evidence_chain']==before['evidence_chain']
    assert after['recovery']['controller']['steps']==1
    assert after['recovery']['controller']['calls']==after['recovery']['controller']['checks']=={}
    assert after['recovery']['controller']['evidence']==[]
    assert c.pending_recovery['reason']=='generation_repetition'
    record=json.loads((c.journal.path/'ledger.jsonl').read_text().splitlines()[-1])
    assert LINE not in json.dumps(record)
    assert cut().line_sha256 in json.dumps(record)
    with pytest.raises(PermissionError):c.begin_action('new_tool',{})
    with pytest.raises(ValueError):c.record_verification(authority=key,verdict='PASS',inspected=evidence(c),assessment={})


def test_changed_line_or_channel_cannot_rename_generation_error_debt(managed):
    c,_=managed;worker_phase(c)
    with bind(c):assert host_observe(c) is True
    first=deepcopy(c._state['recovery']['controller']['errors'])
    assert len(first)==1 and list(first.values())==[1]
    c.prepare_recovery_intervention();c.open_actions()
    other=TextRepetitionDetector().observe('reasoning_content',('Changed wording still repeats the same uncompleted generic task.\n')*6)
    assert other.line_sha256!=cut().line_sha256
    with bind(c):assert host_observe(c,other) is True
    second=c._state['recovery']['controller']['errors']
    assert second.keys()==first.keys() and list(second.values())==[2]
    assert c._state['recovery']['controller']['calls']==c._state['recovery']['controller']['checks']=={}
    assert c._state['recent']==[]


@pytest.mark.parametrize('condition',['contract','review','uncertain','tools_disallowed','sealed','inflight','failed_effect','stopped'])
def test_ineligible_cut_does_not_change_recovery_or_claim_effect_resolution(managed,condition):
    c,_=managed;worker_phase(c)
    if condition in {'contract','review'}:
        c.journal.record('fixture_phase',phase='contract_running' if condition=='contract' else 'review_pending')
    elif condition=='uncertain':c.journal.record('fixture_uncertainty',uncertain=True)
    elif condition=='sealed':c.seal_actions()
    elif condition in {'inflight','failed_effect'}:
        action=c.begin_action('fixture_mutation',{})
        if condition=='failed_effect':c.finish_action(action,'error',{'is_error':True})
    elif condition=='stopped':c.stop()
    before=deepcopy(c.journal.state);seq=c.journal.seq
    with bind(c,allow_tools=condition!='tools_disallowed'):
        assert host_observe(c) is False
    assert c.journal.seq==seq and c.journal.state==before


def test_resume_cannot_refill_cut_history_and_help_reservation(managed):
    c,key=managed;worker_phase(c)
    for index in range(3):
        with bind(c):assert host_observe(c) is True
        if index<2:
            c.prepare_recovery_intervention();c.open_actions()
    before=deepcopy(c._state['recovery'])
    path,run_id=c.journal.path.parent,c.journal.run_id
    c.journal.close();journal=RunState(path,run_id)
    try:
        reopened=ManagedTaskCoordinator(journal,c.tasks,verification_authority=key);reopened.attach()
        assert reopened._state['recovery']==before
        with bind(reopened):assert host_observe(reopened) is False
        assert reopened._state['recovery']==before and not reopened.actions_admitted
        with pytest.raises(PermissionError):reopened.begin_action('fixture_mutation',{})
    finally:journal.close()


def test_failed_cut_append_seals_admission_without_inventing_persisted_budget(managed,monkeypatch):
    c,_=managed;worker_phase(c);before=deepcopy(c._state['recovery'])
    def fail(*args,**kwargs):raise OSError('synthetic generation observation append failure')
    monkeypatch.setattr(c.journal,'record',fail)
    with bind(c),pytest.raises(OSError):host_observe(c)
    assert c._state['recovery']==before and not c.actions_admitted
    with pytest.raises(PermissionError):c.begin_action('fixture_mutation',{})


async def test_assisted_worker_cut_closes_stage_without_refilling_origin(assisted):
    c,key,parent=admission(assisted);start_worker(c,key,parent);worker_phase(c)
    origin=deepcopy(c._state['recovery']);lease=deepcopy(c.assistance_state())
    with bind(c):assert host_observe(c) is True
    closed=c.assistance_state()
    assert closed['status']=='closed' and closed['observations']==lease['observations']
    assert c._state['recovery']==origin
    assert c.pending_recovery['kind']=='needs_help' and not c.actions_admitted
    seq=c.journal.seq
    with bind(c):assert host_observe(c) is False
    assert c.journal.seq==seq and c._state['recovery']==origin


async def test_assisted_cut_close_append_failure_seals_admission_and_preserves_history(assisted,monkeypatch):
    c,key,parent=admission(assisted);start_worker(c,key,parent);worker_phase(c)
    before=deepcopy(c.journal.state);seq=c.journal.seq
    actual_record=c.journal.record
    attempted=[]
    def fail_close(event,**kwargs):
        if event=='assistance_closed':
            attempted.append(event)
            raise OSError('synthetic assisted generation-cut closure append failure')
        return actual_record(event,**kwargs)
    monkeypatch.setattr(c.journal,'record',fail_close)
    with bind(c),pytest.raises(OSError,match='closure append failure'):host_observe(c)
    assert attempted==['assistance_closed']
    assert c.journal.seq==seq and c.journal.state==before
    assert not c.actions_admitted, 'A failed assisted closure must seal coordinator admission too.'
    with pytest.raises(PermissionError):c.begin_action('fixture_mutation',{})
    assert c.journal.state['managed']==before['managed']
    assert c.journal.state['help_continuation']==before['help_continuation']


async def test_preexisting_helper_write_failure_cannot_leave_coordinator_admission_open(assisted,monkeypatch):
    c,key,parent=admission(assisted);start_worker(c,key,parent);worker_phase(c)
    before=deepcopy(c.journal.state);seq=c.journal.seq
    actual_record=c.journal.record
    def fail_close(event,**kwargs):
        if event=='assistance_closed':raise OSError('synthetic prior helper write failure')
        return actual_record(event,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(c.journal,'record',fail_close)
        with pytest.raises(OSError,match='prior helper write failure'):
            c._assistance().close('interrupted',authority=key)
    assert c.journal.seq==seq and c.journal.state==before
    assert not c.actions_admitted, 'A helper write failure must remain visible to coordinator admission.'
    with bind(c):assert host_observe(c) is False
    with pytest.raises(PermissionError):c.begin_action('fixture_mutation',{})
    assert c.journal.seq==seq and c.journal.state==before


async def test_assisted_generation_cut_seals_before_entering_close_append(assisted,monkeypatch):
    c,key,parent=admission(assisted);start_worker(c,key,parent);worker_phase(c)
    origin=deepcopy(c._state['recovery']);lease=deepcopy(c.assistance_state())
    recent=deepcopy(c._state['recent'])
    actual_record=c.journal.record
    entries=[]
    def check_close_entry(event,**kwargs):
        if event=='assistance_closed':
            entries.append(event)
            assert not c._accepting, 'Generation cut must seal actions before the closure append callback.'
            assert not c.actions_admitted
            with pytest.raises(PermissionError):c.begin_action('fixture_mutation',{})
        return actual_record(event,**kwargs)
    monkeypatch.setattr(c.journal,'record',check_close_entry)
    with bind(c):assert host_observe(c) is True
    assert entries==['assistance_closed']
    assert c._state['recovery']==origin and c._state['recent']==recent
    assert c.assistance_state()['status']=='closed'
    assert c.assistance_state()['observations']==lease['observations']
