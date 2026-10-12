"""Host parent/child ownership contracts with real journals; no inference."""
from copy import deepcopy
import hashlib
import json
import time
from types import SimpleNamespace

import pytest

from dream.core.run_state import RunState, digest, inspect_run
from dream.core.task_checkpoints import ManagedTaskCoordinator
from dream.core.worker_checkpoints import WorkerJournal
from dream.memory.store import MemoryStore
from dream.memory.tasks import TaskStore


EXECUTION = {'task': 'original task', 'rules': 'original rules',
             'tools': ['read_file'], 'output_baseline': 'original digest'}
DENIED = (ValueError, PermissionError)


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


@pytest.fixture
def parent(tmp_path):
    store = MemoryStore(tmp_path / 'tasks.db')
    tasks = TaskStore(store)
    journal = RunState(tmp_path / 'runs')
    contract = '- preserve the original task'
    (journal.path / 'contract.md').write_text(contract)
    (journal.path / 'progress.md').write_text('original evidence')
    journal.record('created', goal='original goal', contract=contract,
                   contract_sha256=digest(contract), worker_workspace=str(tmp_path),
                   status='running', uncertain=False)
    key = object()
    coordinator = ManagedTaskCoordinator(journal, tasks, verification_authority=key)
    coordinator.attach(standing_rules='original rules')
    fixture = SimpleNamespace(c=coordinator, key=key, tasks=tasks, root=tmp_path,
                              journals=[journal])
    yield fixture
    for opened in fixture.journals:
        opened.close()
    store.close()


def reserve(p, **changes):
    kwargs = dict(worker_id='a' * 32, session_id='fixture-session', call_id='same-call',
                  arguments={'prompt': 'original task'}, execution_binding=deepcopy(EXECUTION))
    kwargs.update(changes)
    return p.c.begin_child(**kwargs)


def reopen(p):
    old = p.c.journal
    old.close()
    fresh = RunState(old.path.parent, old.run_id)
    p.journals.append(fresh)
    p.c = ManagedTaskCoordinator(fresh, p.tasks, verification_authority=p.key)
    p.c.attach()
    return p.c


def verified(p):
    return p.c.record_verification(authority=p.key, verdict='PASS',
        inspected={str(p.c.journal.path / name): digest((p.c.journal.path / name).read_text())
                   for name in ('contract.md', 'progress.md')},
        assessment={'kind': 'independent_model_review'})


def child(p, binding, *, workspace=None, session=None, worker_id=None):
    return WorkerJournal(workspace or p.root, session or binding['session_id'],
                         worker_id or binding['worker_id'], create=True)


def terminal(journal, binding, *, outcome='completed', result=None):
    journal.create(binding, {'original': 'snapshot'},
                   deadline_ns=time.time_ns() + 60_000_000_000, max_rounds=3)
    result = result or {'text': 'terminal result', 'is_error': outcome == 'incomplete'}
    seq = journal.finish(outcome, {'parent_result': result})
    return seq, result


def test_reservation_persists_exact_binding_and_pending_copy(parent):
    p = parent
    binding = reserve(p)
    assert set(binding) == {'version', 'delegation_id', 'parent_run_id', 'task_id',
        'project', 'revision', 'contract_sha256', 'worker_id', 'session_id', 'call_id',
        'arguments_sha256', 'execution_sha256', 'parent_scope_sha256'}
    assert binding['version'] == 1 and len(binding['delegation_id']) == 32
    assert binding['parent_run_id'] == p.c.journal.run_id
    assert binding['task_id'] == p.c.task['id']
    assert binding['arguments_sha256'] == sha({'prompt': 'original task'})
    assert binding['execution_sha256'] == sha(EXECUTION)
    pending = deepcopy(p.c.pending_children)
    p.c.pending_children.clear()
    assert p.c.pending_children == pending
    assert reopen(p).pending_children == pending
    assert inspect_run(p.c.journal.path.parent, p.c.journal.run_id)['requires_reconciliation']


@pytest.mark.parametrize('changes', [
    {'worker_id': 'A' * 32}, {'session_id': 'x' * 257}, {'call_id': 'x' * 257},
    {'arguments': {'x': float('nan')}}, {'arguments': {'x': ('tuple',)}},
    {'arguments': {1: 'nonstring key'}}, {'arguments': {'x': 'x' * 65537}},
    {'execution_binding': {}}, {'execution_binding': {'x': 'x' * 8193}},
])
def test_invalid_reservation_grants_no_child_ownership(parent, changes):
    with pytest.raises(DENIED):
        reserve(parent, **changes)
    assert parent.c.pending_children == {}


def test_pending_child_blocks_unrelated_actions_verification_and_new_child(parent):
    receipt = verified(parent)
    reserve(parent)
    assert not parent.c.actions_admitted
    assert not parent.c.boundary_eligibility()['eligible']
    for operation in (lambda: parent.c.begin_action('write_file', {}),
                      lambda: reserve(parent, worker_id='b' * 32),
                      lambda: verified(parent),
                      lambda: parent.c.complete_task(receipt['id']),
                      lambda: parent.c.prepare_boundary(receipt['id'])):
        with pytest.raises(DENIED):
            operation()


def test_claim_admits_only_child_and_records_ordinary_action_identity(parent):
    binding = reserve(parent)
    with parent.c.claim_child(binding, execution_binding=EXECUTION):
        assert parent.c.actions_admitted
        action = parent.c.begin_action('read_file', {'path': 'fixture'})
        assert parent.c.unresolved_actions[action]['delegation_id'] == binding['delegation_id']
        parent.c.finish_action(action, 'returned-success', {'text': 'read'})
        with pytest.raises(DENIED):
            with parent.c.claim_child(binding, execution_binding=EXECUTION):
                pytest.fail('Nested claim admitted')
    assert not parent.c.actions_admitted


@pytest.mark.parametrize('damage', ['binding', 'execution', 'contract'])
def test_claim_rejects_changed_authority(parent, damage):
    binding = reserve(parent)
    execution = deepcopy(EXECUTION)
    if damage == 'binding':
        binding['call_id'] = 'foreign call'
    elif damage == 'execution':
        execution['rules'] = 'replacement rules'
    else:
        (parent.c.journal.path / 'contract.md').write_text('replacement contract')
    with pytest.raises(DENIED):
        with parent.c.claim_child(binding, execution_binding=execution):
            pytest.fail('Changed authority admitted')


@pytest.mark.parametrize('parent_receipt_first', [False, True])
def test_terminal_delivery_across_parent_and_worker_reopen_is_idempotent(parent, parent_receipt_first):
    p = parent
    old_receipt = verified(p)
    binding = reserve(p)
    with child(p, binding) as journal:
        seq, result = terminal(journal, binding)
        expected = {'completion_seq': seq, 'outcome': 'completed',
                    'snapshot_receipt': journal.inspect()['snapshot_receipt'],
                    'result_sha256': sha(result)}
        if parent_receipt_first:
            assert p.c.record_child_return(binding, journal) == expected
        assert not journal.inspect()['acknowledged']
    reopen(p)
    with WorkerJournal(p.root, binding['session_id'], binding['worker_id']) as journal:
        assert p.c.record_child_return(binding, journal) == expected
        assert p.c.record_child_return(binding, journal) == expected
        assert not journal.inspect()['acknowledged']
        journal.acknowledge(seq)
    reopen(p)
    with WorkerJournal(p.root, binding['session_id'], binding['worker_id']) as journal:
        assert p.c.record_child_return(binding, journal) == expected
        assert journal.inspect()['requests'] == 0
    assert p.c.pending_children == {}
    with pytest.raises(DENIED):
        p.c.complete_task(old_receipt['id'])
    with pytest.raises(DENIED):
        with p.c.claim_child(binding, execution_binding=EXECUTION):
            pytest.fail('Returned child readmitted')
    second = reserve(p, worker_id='b' * 32)
    assert second['call_id'] == binding['call_id']
    assert second['delegation_id'] != binding['delegation_id']


def test_worker_identity_cannot_be_reused_after_terminal_return(parent):
    binding = reserve(parent)
    with child(parent, binding) as journal:
        terminal(journal, binding)
        parent.c.record_child_return(binding, journal)
    with pytest.raises(DENIED):
        reserve(parent)


def test_actual_handler_uncertainty_survives_incomplete_child_and_reopen(parent):
    binding = reserve(parent)
    with parent.c.claim_child(binding, execution_binding=EXECUTION):
        action = parent.c.begin_action('write_file', {'path': 'effect'})
        parent.c.finish_action(action, 'error', {'text': 'failed after effect'})
    with child(parent, binding) as journal:
        terminal(journal, binding, outcome='incomplete')
        parent.c.record_child_return(binding, journal)
    reopen(parent)
    assert action in parent.c.unresolved_actions
    assert not parent.c.actions_admitted
    with pytest.raises(DENIED):
        verified(parent)


@pytest.mark.parametrize('damage', ['nonterminal', 'foreign_session', 'wrong_result'])
def test_parent_refuses_unverified_terminal_original(parent, damage):
    binding = reserve(parent)
    with child(parent, binding, session='foreign' if damage == 'foreign_session' else None) as journal:
        if damage == 'nonterminal':
            journal.create(binding, {}, deadline_ns=time.time_ns() + 60_000_000_000, max_rounds=3)
        else:
            terminal(journal, binding, result={'text': 'terminal result', 'is_error': damage == 'wrong_result'})
        with pytest.raises(DENIED):
            parent.c.record_child_return(binding, journal)
        assert parent.c.pending_children


def test_parent_stop_allows_incomplete_bookkeeping_but_no_execution(parent):
    binding = reserve(parent)
    parent.c.stop()
    with child(parent, binding) as journal:
        terminal(journal, binding, outcome='incomplete')
        assert parent.c.record_child_return(binding, journal)['outcome'] == 'incomplete'
    assert not parent.c.actions_admitted
    with pytest.raises(DENIED):
        verified(parent)


def test_failed_parent_record_seals_admission_without_acknowledging_child(parent, monkeypatch):
    binding = reserve(parent)
    with child(parent, binding) as journal:
        terminal(journal, binding)
        def fail_record(*args, **kwargs):
            raise OSError('injected parent storage failure')
        monkeypatch.setattr(parent.c.journal, 'record', fail_record)
        with pytest.raises(OSError):
            parent.c.record_child_return(binding, journal)
        assert not journal.inspect()['acknowledged']
        assert not parent.c.actions_admitted
        with pytest.raises(DENIED):
            parent.c.begin_action('read_file', {})
