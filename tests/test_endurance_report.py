"""Generic synthetic canonical ledgers; no long run or provider qualification claim."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest
from test_task_checkpoints import managed

BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)
NOW = BASE + timedelta(days=1)
CHECK = 'c' * 64


def hashed(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def ledger(tmp_path, run_id='fixture', entries=None):
    root = tmp_path / 'runs'
    folder = root / run_id
    folder.mkdir(parents=True)
    (folder / '.lock').touch()
    state = {'run_id': run_id, 'goal': 'generic source task', 'contract': '- criterion',
             'contract_sha256': 'a'*64, 'status': 'running', 'phase': 'ready',
             'uncertain': False, 'iterations': 0}
    lines = []
    for seq, (seconds, event, changes, data) in enumerate(entries or [], 1):
        state.update(deepcopy(changes))
        envelope = {'seq': seq, 'at': (BASE + timedelta(seconds=seconds)).isoformat(),
                    'event': event, 'data': data}
        if seq == 1:
            envelope['state'] = deepcopy(state)
        else:
            envelope.update(version=2, changes={**deepcopy(changes), 'run_id': run_id})
        lines.append(json.dumps(envelope)+'\n')
    (folder / 'ledger.jsonl').write_text(''.join(lines))
    return root, folder


def report(root, ids, **kwargs):
    from dream.core.endurance_report import build_endurance_report
    return build_endurance_report(root, ids, now=NOW, **kwargs)


def test_timestamp_only_twelve_hours_and_done_claims_never_qualify(tmp_path):
    entries = [(0, 'worker_started', {'phase': 'worker_running'}, {})]
    entries += [(i*5400, 'worker_finished', {'worker_status': 'DONE'}, {}) for i in range(1,9)]
    root, _ = ledger(tmp_path, entries=entries)
    result = report(root, ['fixture'])
    assert result['timing']['observed_wall_window_s'] == 43200
    assert result['timing']['observed_active_s'] == 0
    assert result['timing']['unobserved_s'] == 43200
    assert result['runs'][0]['model_done_claims'] == 8
    assert all(not gate['qualified'] for gate in result['gates'])
    assert result['accepted_work_count'] == 0
    assert result['metrics']['provider_total_tokens'] is None
    assert result['metrics']['cost'] is None


def test_paused_gaps_and_overlap_are_not_double_counted(tmp_path):
    entries = [(0, 'worker_started', {'phase':'worker_running'}, {}),
               (60, 'outcome', {'status':'stopped'}, {}),
               (3660, 'resume_requested', {'status':'running','phase':'worker_running'}, {'note':'explicit'}),
               (3720, 'worker_finished', {'phase':'ready'}, {})]
    root, _ = ledger(tmp_path, 'one', entries)
    ledger(tmp_path, 'two', [(0, 'worker_started', {'phase':'worker_running'}, {}),
                            (60, 'worker_finished', {'phase':'ready'}, {})])
    result = report(root, ['one', 'two'])
    assert result['timing']['observed_active_s'] == 120
    assert result['timing']['recorded_idle_s'] == 3600
    assert result['timing']['observed_wall_window_s'] == 3720
    assert result['runs'][0]['explicit_resume_requests'] == 1
    assert result['runs'][0]['process_restarts'] is None


def completion_entries(*, uncertain=False, host_check=True):
    managed = dict(version=1,task_id=1,project='generic',revision=1,contract_sha256='a'*64,
        unresolved={},recent=[],action_revision=2,evidence_chain='b'*64,receipt=None,
        completed_receipt=None,boundary=None,generation=0)
    entries = [(0,'worker_started',{'phase':'worker_running','managed':deepcopy(managed)}, {})]
    if host_check:
        entries.append((60,'recovery_check_executed',{}, {'check_sha256':CHECK,'passed':True,'exception_type':None}))
    inspected = {'generic.py':'d'*64}
    receipt = dict(id='fixture-receipt',run_id='fixture',task_id=1,revision=1,project='generic',
        contract_sha256='a'*64,action_revision=2,action_chain='b'*64,
        evidence_sha256=hashed(inspected),inspected=inspected,
        assessment_kind='independent_model_review',assessment_sha256='e'*64)
    managed['receipt'] = receipt
    entries.append((120,'verification_checked',{'managed':deepcopy(managed)}, {'receipt':receipt}))
    managed['completed_receipt'] = receipt['id']
    entries.append((121,'task_checked_complete',{'managed':deepcopy(managed),'uncertain':uncertain}, {'receipt_id':receipt['id']}))
    entries.append((122,'outcome',{'status':'done','phase':'ready'}, {}))
    return entries


def task_lookup(task_id):
    return dict(id=task_id,managed_run='fixture',managed_contract='- criterion',managed_revision=1,
                project='generic',managed_receipt='fixture-receipt',status='done',managed_state='checked_complete')


@pytest.mark.parametrize('uncertain,host_check,accepted',[(False,True,1),(True,True,0),(False,False,0)])
def test_host_checks_task_projection_and_clean_completion_are_all_required(tmp_path, uncertain, host_check, accepted):
    root, _ = ledger(tmp_path, entries=completion_entries(uncertain=uncertain,host_check=host_check))
    result = report(root,['fixture'],task_lookup=task_lookup,acceptance_checks={'fixture':[CHECK]})
    run = result['runs'][0]
    assert result['accepted_work_count'] == accepted
    assert run['model_review_receipts'] == 1
    assert run['host_checks']['passed'] == int(host_check)
    assert all(not gate['qualified'] for gate in result['gates'])


def test_failed_or_stale_host_check_and_wrong_task_scope_do_not_accept(tmp_path):
    entries = completion_entries()
    entries[1][3]['passed'] = False
    root, _ = ledger(tmp_path, entries=entries)
    assert report(root,['fixture'],task_lookup=task_lookup,acceptance_checks={'fixture':[CHECK]})['accepted_work_count'] == 0
    other, _ = ledger(tmp_path,'stale',completion_entries())
    def wrong(_):
        return {**task_lookup(1),'project':'elsewhere'}
    assert report(other,['stale'],task_lookup=wrong,acceptance_checks={'stale':[CHECK]})['accepted_work_count'] == 0


@pytest.mark.parametrize('mutation',['future','reversed','naive','sequence','identity','partial'])
def test_malformed_ledgers_are_rejected(tmp_path,mutation):
    root, folder = ledger(tmp_path, entries=[(0,'created',{},{}),(1,'outcome',{'status':'stopped'}, {})])
    path=folder/'ledger.jsonl'; records=[json.loads(x) for x in path.read_text().splitlines()]
    if mutation=='future': records[-1]['at']=(NOW+timedelta(seconds=1)).isoformat()
    elif mutation=='reversed': records[-1]['at']=(BASE-timedelta(seconds=1)).isoformat()
    elif mutation=='naive': records[-1]['at']='2026-10-01T00:00:01'
    elif mutation=='sequence': records[-1]['seq']=4
    elif mutation=='identity': records[-1]['changes']['run_id']='other'
    path.write_text(''.join(json.dumps(r)+'\n' for r in records).rstrip('\n') if mutation=='partial' else ''.join(json.dumps(r)+'\n' for r in records))
    with pytest.raises(ValueError): report(root,['fixture'])


def test_readonly_inspection_and_strict_byte_limits(tmp_path):
    from dream.core.endurance_report import ReportLimits
    root, folder = ledger(tmp_path, entries=[(0,'created',{},{}),(60,'outcome',{'status':'stopped'}, {})])
    before={p.name:p.read_bytes() for p in folder.iterdir()}
    report(root,['fixture'])
    assert {p.name:p.read_bytes() for p in folder.iterdir()} == before
    with pytest.raises(ValueError,match='bound|limit|capacity'):
        report(root,['fixture'],limits=ReportLimits(max_run_bytes=10))
    with pytest.raises(ValueError): report(root,['fixture','fixture'])


def test_symlink_and_live_writer_are_rejected_without_repair(tmp_path):
    import fcntl
    root, folder = ledger(tmp_path,entries=[(0,'created',{}, {})])
    with (folder/'.lock').open('rb') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError,match='active|busy|stable'):
            report(root,['fixture'])
    original=folder/'ledger.jsonl'; renamed=folder/'original.jsonl'; original.rename(renamed);original.symlink_to(renamed)
    with pytest.raises((ValueError,OSError)): report(root,['fixture'])


def test_later_failed_host_check_supersedes_earlier_pass(tmp_path):
    entries=completion_entries()
    entries.insert(2,(90,'recovery_check_executed',{}, {'check_sha256':CHECK,'passed':False,'exception_type':None}))
    root,_=ledger(tmp_path,entries=entries)
    result=report(root,['fixture'],task_lookup=task_lookup,acceptance_checks={'fixture':[CHECK]})
    assert result['accepted_work_count']==0
    assert result['runs'][0]['host_checks']=={'passed':1,'failed':1}


def test_host_check_before_latest_action_revision_is_stale(tmp_path):
    entries=completion_entries()
    entries[0][2]['managed']['action_revision']=1
    root,_=ledger(tmp_path,entries=entries)
    result=report(root,['fixture'],task_lookup=task_lookup,acceptance_checks={'fixture':[CHECK]})
    assert result['accepted_work_count']==0
    assert result['runs'][0]['completion']['receipt_linked']


def test_missing_task_projection_or_unknown_selected_check_cannot_accept(tmp_path):
    root,_=ledger(tmp_path,entries=completion_entries())
    assert report(root,['fixture'],acceptance_checks={'fixture':[CHECK]})['accepted_work_count']==0
    assert report(root,['fixture'],task_lookup=task_lookup,acceptance_checks={'fixture':['f'*64]})['accepted_work_count']==0
    assert report(root,['fixture'],task_lookup=lambda _:None,acceptance_checks={'fixture':[CHECK]})['accepted_work_count']==0


def test_runtime_counter_coverage_and_duplicate_summary_accounting(tmp_path):
    usage={'prompt_tokens':12,'output_tokens':3,'cached_tokens':2,'tools':1,'failures':0}
    root,_=ledger(tmp_path,'complete',[(0,'created',{},{}),(10,'runtime_budget',{}, {'usage':usage}),
                                     (11,'runtime_budget',{}, {'usage':usage})])
    complete=report(root,['complete'])
    assert complete['metrics']['reported_prompt_tokens']==12
    ledger(tmp_path,'missing',[(0,'created',{},{}),(10,'runtime_budget',{}, {'usage':usage}),
                              (20,'resume_requested',{}, {'note':'explicit'}),(30,'outcome',{'status':'stopped'}, {})])
    assert report(root,['missing'])['metrics']['reported_prompt_tokens'] is None


def test_duplicate_json_keys_record_bounds_and_invalid_selectors_refused(tmp_path):
    from dream.core.endurance_report import ReportLimits
    root,folder=ledger(tmp_path,entries=[(0,'created',{},{}),(1,'outcome',{}, {})])
    with pytest.raises(ValueError):report(root,['fixture'],limits=ReportLimits(max_records=1))
    with pytest.raises(ValueError):report(root,['fixture'],acceptance_checks={'unselected':[CHECK]})
    path=folder/'ledger.jsonl'
    path.write_text(path.read_text().replace('"seq": 1','"seq": 1, "seq": 1',1))
    with pytest.raises(ValueError,match='Duplicate'):report(root,['fixture'])


def test_admitted_inflight_intent_is_not_itself_a_fault(tmp_path):
    managed=deepcopy(completion_entries()[0][2]['managed'])
    action={'id':'action-1','tool':'fixture','disposition':'intent'}
    managed['unresolved']={'action-1':action}
    settled={**managed,'unresolved':{}}
    root,_=ledger(tmp_path,entries=[
        (0,'action_intent',{'phase':'worker_running','managed':managed},{'action':action}),
        (10,'action_returned',{'managed':settled},{'action':{**action,'disposition':'returned-success'}}),
        (20,'worker_finished',{'phase':'ready'}, {})])
    result=report(root,['fixture'])
    assert result['timing']['observed_active_s']==20
    assert result['timing']['fault_affected_s']==0
    assert result['runs'][0]['action_intents']==result['runs'][0]['action_returns']==1


def test_oversized_record_is_rejected_before_qualification(tmp_path):
    root,_=ledger(tmp_path,entries=[(0,'created',{}, {'oversized':'x'*(2*1024*1024)})])
    with pytest.raises(ValueError,match='bound|capacity'):report(root,['fixture'])


def test_actual_coordinator_host_check_receipt_and_task_projection_link(managed):
    """Use real coordinator/SQLite projection; reviewer verdict remains synthetic."""
    from dream.core.endurance_report import build_endurance_report
    from dream.core.task_checkpoints import ManagedTaskCoordinator
    from test_task_checkpoints import evidence
    previous, verifier_key = managed
    host_key=object()
    coordinator=ManagedTaskCoordinator(previous.journal,previous.tasks,
        verification_authority=verifier_key,recovery_check_authority=host_key)
    coordinator.attach()
    coordinator.journal.record('fixture_worker_started',phase='worker_running')
    action=coordinator.begin_action('fixture_observation',{})
    coordinator.finish_action(action,'returned-success',{})
    invoked=[]
    def actual_check():
        invoked.append(1)
        return (coordinator.journal.path/'progress.md').read_text()=='correct'
    assert coordinator.execute_check('fixture-acceptance',actual_check,authority=host_key)
    receipt=coordinator.record_verification(authority=verifier_key,verdict='PASS',
        inspected=evidence(coordinator),assessment={'kind':'independent_model_review','fixture':'synthetic verdict'})
    task=coordinator.complete_task(receipt['id'])
    coordinator.journal.record('outcome',status='done',phase='ready')
    root,run_id=coordinator.journal.path.parent,coordinator.journal.run_id
    coordinator.journal.close()
    result=build_endurance_report(root,[run_id],now=datetime.now(timezone.utc)+timedelta(seconds=1),
        task_lookup=coordinator.tasks.get,acceptance_checks={run_id:[hashed('fixture-acceptance')]})
    assert invoked==[1] and task['managed_receipt']==receipt['id']
    assert result['accepted_work_count']==1
    assert result['runs'][0]['completion']['receipt_linked']
    assert result['runs'][0]['completion']['task_projection']=='linked'
    assert result['runs'][0]['model_review_receipts']==1
    assert result['runs'][0]['host_checks']=={'passed':1,'failed':0}
    assert all(not gate['qualified'] for gate in result['gates'])
