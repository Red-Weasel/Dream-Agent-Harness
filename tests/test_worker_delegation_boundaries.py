"""Parent ownership failure and process boundaries, with no model or engine."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from dream.core import run_state
from dream.core.run_state import RunState
from dream.core.task_checkpoints import ManagedTaskCoordinator
from dream.core.task_checkpoints import bind
from dream.core.worker_checkpoints import WorkerJournal
from test_worker_delegations import parent, reserve, reopen, child, terminal, EXECUTION, DENIED
from test_managed_loop import worker, loop


def test_legacy_parent_without_children_remains_usable(parent):
    state = parent.c._state
    del state['children']
    parent.c.journal.record('legacy_projection', managed=state)
    c = reopen(parent)
    assert c.pending_children == {} and c.actions_admitted
    binding = reserve(parent)
    assert binding['delegation_id'] in c.pending_children


@pytest.mark.parametrize('damage', ['foreign_parent', 'foreign_task', 'bool_revision',
                                  'bad_worker', 'non_mapping', 'missing_binding', 'fake_completion'])
def test_malformed_child_authority_rejected_on_parent_reopen(parent, damage):
    binding = reserve(parent)
    state = parent.c._state
    record = state['children'][binding['delegation_id']]
    if damage == 'foreign_parent':
        record['binding']['parent_run_id'] = 'b' * 32
    elif damage == 'foreign_task':
        record['binding']['task_id'] += 1
    elif damage == 'bool_revision':
        record['binding']['revision'] = True
    elif damage == 'bad_worker':
        record['binding']['worker_id'] = 'invalid'
    elif damage == 'non_mapping':
        state['children'] = []
    elif damage == 'missing_binding':
        del record['binding']
    else:
        record.update(status='returned', completion={})
    # Host fixture writes malformed bytes deliberately; reader must not repair.
    parent.c.journal.record('damaged_fixture', managed=state)
    parent.c.journal.close()
    with pytest.raises(ValueError, match='child'):
        RunState(parent.c.journal.path.parent, parent.c.journal.run_id)


def test_parent_record_snapshot_failure_recovers_the_committed_receipt(parent, monkeypatch):
    binding = reserve(parent)
    with child(parent, binding) as journal:
        terminal(journal, binding)
        real_write = run_state.atomic_write
        def fail_snapshot(path, text):
            if path.name == 'state.json' and json.loads(text)['managed']['children'][binding['delegation_id']]['status'] == 'returned':
                raise OSError('parent projection failed after ledger commit')
            return real_write(path, text)
        with monkeypatch.context() as patch:
            patch.setattr(run_state, 'atomic_write', fail_snapshot)
            with pytest.raises(OSError):
                parent.c.record_child_return(binding, journal)
        assert not parent.c.actions_admitted and not journal.inspect()['acknowledged']
    c = reopen(parent)
    before = c.journal.seq
    with WorkerJournal(parent.root, binding['session_id'], binding['worker_id']) as journal:
        receipt = c.record_child_return(binding, journal)
        assert receipt['completion_seq'] == journal.inspect()['completion_seq']
        assert c.journal.seq == before and journal.inspect()['requests'] == 0


async def test_escaped_child_context_cannot_enter_handler_after_lease_exit(parent):
    binding = reserve(parent)
    ready = asyncio.Event()
    observed = []
    async def escaped():
        await ready.wait()
        observed.append(parent.c.actions_admitted)
        with pytest.raises(PermissionError):
            parent.c.begin_action('write_file', {})
    with parent.c.claim_child(binding, execution_binding=EXECUTION):
        escaped_task = asyncio.create_task(escaped())
    ready.set()
    await asyncio.wait_for(escaped_task, 2)
    assert observed == [False]


async def test_owner_resume_note_does_not_erase_child_or_dispatch_lead(worker, tmp_path):
    journal = RunState(tmp_path / 'runs')
    contract = '- original criterion'
    (journal.path / 'contract.md').write_text(contract)
    (journal.path / 'progress.md').write_text('original work')
    journal.record('created', goal='goal', contract=contract, contract_sha256=run_state.digest(contract),
                   worker_workspace=str(tmp_path), phase='ready', iterations=0,
                   status='running', uncertain=False, result=None)
    c = ManagedTaskCoordinator(journal, worker.tasks)
    c.attach()
    binding = c.begin_child(worker_id='a' * 32, session_id='fixture', call_id='call',
                           arguments={}, execution_binding=EXECUTION)
    run_id = journal.run_id
    journal.close()
    driver = loop(worker, tmp_path)
    result = await asyncio.wait_for(driver.run('goal', resume_run_id=run_id,
                            resume_note='continue with the original task'), 5)
    assert result.status == 'need_input' and worker.calls == 0
    assert binding['delegation_id'] in driver.checkpoints.pending_children


@pytest.mark.parametrize('window', ['terminal', 'parent_receipt', 'child_ack'])
def test_owned_process_death_redelivers_without_worker_execution(parent, window):
    binding = reserve(parent)
    with child(parent, binding) as journal:
        journal.create(binding, {}, deadline_ns=__import__('time').time_ns() + 60_000_000_000, max_rounds=3)
    original = parent.c.journal
    original.close()
    script = '''
import json,os,sys
from pathlib import Path
from dream.core.run_state import RunState
from dream.core.task_checkpoints import ManagedTaskCoordinator
from dream.core.worker_checkpoints import WorkerJournal
from dream.memory.store import MemoryStore
from dream.memory.tasks import TaskStore
root,run,binding,window=Path(sys.argv[1]),sys.argv[2],json.loads(sys.argv[3]),sys.argv[4]
store=MemoryStore(root/'tasks.db')
parent=RunState(root/'runs',run)
c=ManagedTaskCoordinator(parent,TaskStore(store));c.attach()
with WorkerJournal(root,binding['session_id'],binding['worker_id']) as child:
 seq=child.finish('completed',{'parent_result':{'text':'original terminal result','is_error':False}})
 if window!='terminal':c.record_child_return(binding,child)
 if window=='child_ack':child.acknowledge(seq)
 os._exit(23)
'''
    process = subprocess.run([sys.executable, '-c', script, str(parent.root), original.run_id,
                              json.dumps(binding), window], cwd=Path(__file__).resolve().parents[1],
                             capture_output=True, text=True, timeout=15)
    assert process.returncode == 23, process.stderr
    c = reopen(parent)
    with WorkerJournal(parent.root, binding['session_id'], binding['worker_id']) as journal:
        c.record_child_return(binding, journal)
        seq = c.journal.seq
        c.record_child_return(binding, journal)
        assert c.journal.seq == seq
        assert journal.inspect()['snapshot']['parent_result']['text'] == 'original terminal result'
        assert journal.inspect()['requests'] == 0
        journal.acknowledge(journal.inspect()['completion_seq'])
    assert c.pending_children == {}


def test_child_lease_cannot_ignore_an_existing_unknown_handler(parent):
    binding = reserve(parent)
    with parent.c.claim_child(binding, execution_binding=EXECUTION):
        identity = parent.c.begin_action('write_file', {})
        parent.c.finish_action(identity, 'interrupted')
    c = reopen(parent)
    with pytest.raises(DENIED):
        with c.claim_child(binding, execution_binding=EXECUTION):
            pytest.fail('Uncertain effect was bypassed')
    assert identity in c.unresolved_actions


@pytest.mark.parametrize('field', ['goal', 'standing_rules'])
def test_original_parent_instructions_cannot_drift_before_child_claim(parent, field):
    binding = reserve(parent)
    parent.c.journal.record('changed_parent_instructions', **{field: 'replacement instructions'})
    with pytest.raises(DENIED):
        with parent.c.claim_child(binding, execution_binding=EXECUTION):
            pytest.fail('Changed parent instructions admitted')


def test_bound_live_parent_turn_can_reserve_without_reconciling_a_crash(parent):
    parent.c.journal.record('live_worker_turn', phase='worker_running', uncertain=True)
    with bind(parent.c):
        binding = reserve(parent)
        with parent.c.claim_child(binding, execution_binding=EXECUTION):
            assert parent.c.actions_admitted
    assert binding['delegation_id'] in parent.c.pending_children


def test_unbound_interrupted_parent_cannot_claim_child(parent):
    binding = reserve(parent)
    parent.c.journal.record('interrupted_turn', phase='worker_running', uncertain=True)
    with pytest.raises(DENIED):
        with parent.c.claim_child(binding, execution_binding=EXECUTION):
            pytest.fail('Interrupted parent claim admitted')


def test_actual_terminal_result_after_stop_is_bookkeeping_without_readmission(parent):
    binding = reserve(parent)
    with child(parent, binding) as journal:
        terminal(journal, binding)
        parent.c.stop()
        receipt = parent.c.record_child_return(binding, journal)
        assert receipt['outcome'] == 'completed'
        assert not parent.c.actions_admitted and parent.c.task['status'] != 'done'
        assert not journal.inspect()['acknowledged']


def test_failed_parent_cannot_retry_delivery_in_same_driver(parent, monkeypatch):
    binding = reserve(parent)
    with child(parent, binding) as journal:
        terminal(journal, binding)
        with monkeypatch.context() as patch:
            patch.setattr(parent.c.journal, 'record', lambda *a, **k: (_ for _ in ()).throw(OSError('write failed')))
            with pytest.raises(OSError):
                parent.c.record_child_return(binding, journal)
        with pytest.raises(DENIED):
            parent.c.record_child_return(binding, journal)
        assert not journal.inspect()['acknowledged']
