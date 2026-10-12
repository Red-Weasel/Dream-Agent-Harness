"""Synthetic managed-task boundaries; no providers or models."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from dream.core.run_state import RunState, digest, inspect_run
from dream.core.task_checkpoints import ManagedTaskCoordinator, bind, current, wrap_tool
from dream.memory.store import MemoryStore
from dream.memory.tasks import TaskStore


@pytest.fixture
def managed(tmp_path):
    store = MemoryStore(tmp_path / 'tasks.db')
    tasks = TaskStore(store)
    journal = RunState(tmp_path / 'runs')
    contract = '- evidence is correct'
    (journal.path / 'contract.md').write_text('# Contract\n\n' + contract + '\n')
    (journal.path / 'progress.md').write_text('correct')
    journal.record('created', goal='full goal', contract=contract,
                   contract_sha256=digest((journal.path / 'contract.md').read_text()),
                   worker_workspace=str(tmp_path), status='running', uncertain=False)
    key = object()
    coordinator = ManagedTaskCoordinator(journal, tasks, verification_authority=key)
    coordinator.attach(standing_rules='full standing rules')
    yield coordinator, key
    journal.close()
    store.close()


def evidence(c):
    return {str(c.journal.path / name): digest((c.journal.path / name).read_text())
            for name in ('contract.md', 'progress.md')}


def verified(c, key):
    return c.record_verification(authority=key, verdict='PASS', inspected=evidence(c),
                                 assessment={'kind': 'independent_model_review'})


def test_model_cannot_complete_move_or_recontract_managed_task(managed):
    c, key = managed
    t = c.task
    row = c.tasks.update(t['id'], status='done', notes='I am done')
    assert row['status'] == 'active' and row['managed_state'] == 'review_requested'
    for kw in ({'project': 'elsewhere'}, {'title': 'easier'}):
        with pytest.raises(ValueError, match='managed'):
            c.tasks.update(t['id'], **kw)
    with pytest.raises(TypeError):
        c.tasks.update(t['id'], managed_receipt='fake')
    with pytest.raises(PermissionError):
        c.record_verification(authority=object(), verdict='PASS', inspected=evidence(c), assessment={})
    assert not c.boundary_eligibility()['eligible']
    receipt = verified(c, key)
    assert c.complete_task(receipt['id'])['status'] == 'done'
    assert c.complete_task(receipt['id'])['status'] == 'done'


def test_unresolved_action_survives_restart_and_never_replays(managed):
    c, key = managed
    action = c.begin_action('write', {'path': 'fixture'})
    assert c.task['status'] == 'active'
    with pytest.raises(ValueError, match='unresolved'):
        verified(c, key)
    c.journal.close()
    restarted = RunState(c.journal.path.parent, c.journal.run_id)
    try:
        resumed = ManagedTaskCoordinator(restarted, c.tasks, verification_authority=key)
        resumed.attach()
        assert action in resumed.unresolved_actions
        assert inspect_run(restarted.path.parent, restarted.run_id)['requires_reconciliation']
        resumed.reconcile_action(action, note='Owner inspected the fixture effect; do not replay.')
        receipt = verified(resumed, key)
        assert resumed.complete_task(receipt['id'])['status'] == 'done'
    finally:
        restarted.close()


def test_receipts_reject_stale_evidence_and_new_actions(managed):
    c, key = managed
    receipt = verified(c, key)
    (c.journal.path / 'progress.md').write_text('changed')
    with pytest.raises(ValueError, match='evidence'):
        c.complete_task(receipt['id'])
    receipt = verified(c, key)
    action = c.begin_action('read', {})
    c.finish_action(action, 'returned-success', {'content': []})
    with pytest.raises(ValueError, match='receipt'):
        c.complete_task(receipt['id'])


def test_boundary_preserves_contract_goal_rules_and_requires_settled_ack(managed):
    c, key = managed
    receipt = verified(c, key)
    c.complete_task(receipt['id'])
    token = c.prepare_boundary(receipt['id'])
    assert token == c.prepare_boundary(receipt['id'])
    capsule = token['capsule']
    assert capsule['goal'] == 'full goal'
    assert capsule['contract'] == '- evidence is correct'
    assert capsule['standing_rules'] == 'full standing rules'
    assert capsule['unresolved_actions'] == {}
    with pytest.raises(ValueError):
        c.acknowledge_boundary(token['id'], generation=token['generation'], settled=False)
    assert c.acknowledge_boundary(token['id'], generation=token['generation'], settled=True)['status'] == 'acknowledged'
    assert c.acknowledge_boundary(token['id'], generation=token['generation'], settled=True)['status'] == 'acknowledged'


async def test_actual_handler_intent_receipt_and_default_context(managed):
    c, key = managed
    calls = []
    from claude_agent_sdk import SdkMcpTool
    async def handler(args):
        assert c.unresolved_actions
        calls.append(args)
        return {'content': [{'type': 'text', 'text': 'saved'}]}
    tool = wrap_tool(SdkMcpTool(name='write_fixture', description='fixture', input_schema={}, handler=handler))
    with bind(c):
        await tool.handler({'secret': 'must only be hashed'})
    assert current() is None
    assert len(calls) == 1 and not c.unresolved_actions
    assert c.task['status'] != 'done'
    assert 'must only be hashed' not in (c.journal.path / 'ledger.jsonl').read_text()
    async def plain(args):
        return {'content': []}
    assert await wrap_tool(SdkMcpTool(name='plain', description='', input_schema={}, handler=plain)).handler({}) == {'content': []}


async def test_failed_intent_prevents_handler_entry(managed, monkeypatch):
    c, key = managed
    from claude_agent_sdk import SdkMcpTool
    calls = []
    async def handler(args):
        calls.append(1)
        return {}
    def fail(*a, **k):
        raise OSError('disk failed before intent')
    monkeypatch.setattr(c.journal, 'record', fail)
    with bind(c), pytest.raises(OSError):
        await wrap_tool(SdkMcpTool(name='write', description='', input_schema={}, handler=handler)).handler({})
    assert calls == []


@pytest.mark.parametrize('failure_boundary', ['intent_snapshot', 'returned_append', 'returned_snapshot'])
async def test_crash_boundaries_preserve_honest_recovery(managed, monkeypatch, failure_boundary):
    c, key = managed
    from claude_agent_sdk import SdkMcpTool
    from dream.core import run_state
    calls = []
    async def handler(args):
        calls.append(1)
        return {'content': []}
    real_record = c.journal.record
    real_snapshot = run_state.atomic_write
    def record(event, **kwargs):
        if event == 'action_returned' and failure_boundary == 'returned_append':
            raise OSError('fixture before return receipt')
        return real_record(event, **kwargs)
    def snapshot(path, text):
        state = json.loads(text) if path.name == 'state.json' else {}
        managed_state = state.get('managed', {})
        if ((failure_boundary == 'intent_snapshot' and managed_state.get('unresolved'))
                or (failure_boundary == 'returned_snapshot' and managed_state.get('recent'))):
            raise OSError('fixture snapshot failed after commit')
        return real_snapshot(path, text)
    monkeypatch.setattr(c.journal, 'record', record)
    monkeypatch.setattr(run_state, 'atomic_write', snapshot)
    with bind(c), pytest.raises(OSError):
        await wrap_tool(SdkMcpTool(name='effect', description='', input_schema={}, handler=handler)).handler({})
    assert len(calls) == (0 if failure_boundary == 'intent_snapshot' else 1)
    c.journal.close()
    reopened = RunState(c.journal.path.parent, c.journal.run_id)
    try:
        assert bool(reopened.state['managed']['unresolved']) == (failure_boundary != 'returned_snapshot')
        assert reopened.state['managed']['completed_receipt'] is None
    finally:
        reopened.close()


def test_duplicate_finish_conflict_and_wrong_scope_receipts(managed):
    c, key = managed
    action = c.begin_action('read', {})
    receipt = c.finish_action(action, 'returned-success', {'v': 1})
    seq = c.journal.seq
    assert c.finish_action(action, 'returned-success', {'v': 1}) == receipt
    assert c.journal.seq == seq
    with pytest.raises(ValueError):
        c.finish_action(action, 'returned-success', {'v': 2})
    receipt = verified(c, key)
    with pytest.raises(ValueError, match='receipt'):
        c.complete_task('forged-receipt')
    c.tasks._conn.execute('UPDATE tasks SET project=? WHERE id=?', ('wrong-scope', c.task['id']))
    c.tasks._conn.commit()
    with pytest.raises(ValueError, match='scope'):
        c.complete_task(receipt['id'])


def test_evidence_scope_symlink_and_contract_changes_rejected(managed, tmp_path):
    c, key = managed
    inspected = evidence(c)
    outside = tmp_path.parent / ('outside-' + c.journal.run_id)
    outside.write_text('evidence')
    try:
        inspected[str(outside)] = digest('evidence')
        with pytest.raises(ValueError, match='scope'):
            c.record_verification(authority=key, verdict='PASS', inspected=inspected, assessment={})
    finally:
        outside.unlink()
    (c.journal.path / 'contract.md').write_text('- easier contract')
    with pytest.raises(ValueError, match='contract'):
        verified(c, key)


def test_projection_failure_retries_existing_committed_receipt(managed, monkeypatch):
    c, key = managed
    receipt = verified(c, key)
    real = c.tasks._complete_managed
    def fail(*a, **k):
        raise OSError('sqlite commit unavailable')
    with monkeypatch.context() as patch:
        patch.setattr(c.tasks, '_complete_managed', fail)
        with pytest.raises(OSError):
            c.complete_task(receipt['id'])
    assert c._state['completed_receipt'] == receipt['id']
    assert c.task['status'] == 'active'
    seq = c.journal.seq
    assert c.complete_task(receipt['id'])['status'] == 'done'
    assert c.journal.seq == seq


def test_boundary_snapshot_failures_are_restart_recoverable(managed, monkeypatch):
    c, key = managed
    receipt = verified(c, key)
    c.complete_task(receipt['id'])
    from dream.core import run_state
    def fail(*a):
        raise OSError('snapshot interrupted')
    with monkeypatch.context() as patch:
        patch.setattr(run_state, 'atomic_write', fail)
        with pytest.raises(OSError):
            c.prepare_boundary(receipt['id'])
    token = c.prepare_boundary(receipt['id'])
    with monkeypatch.context() as patch:
        patch.setattr(run_state, 'atomic_write', fail)
        with pytest.raises(OSError):
            c.acknowledge_boundary(token['id'], generation=token['generation'], settled=True)
    c.journal.close()
    reopened = RunState(c.journal.path.parent, c.journal.run_id)
    try:
        resumed = ManagedTaskCoordinator(reopened, c.tasks, verification_authority=key)
        assert resumed.acknowledge_boundary(token['id'], generation=token['generation'], settled=True)['status'] == 'acknowledged'
        assert reopened.state['managed']['generation'] == 1
    finally:
        reopened.close()


def test_streaming_ledger_over_old_limit_and_capacity_fail_stop(tmp_path, monkeypatch):
    from dream.core import run_state
    journal = RunState(tmp_path / 'runs')
    journal.record('created', goal='original goal', status='running')
    # Real >2 MiB ledger, without quadratic repeated full-state payloads.
    for i in range(36):
        journal.record('tool_event', incremental=True, data={'observation': 'x' * 65536}, counter=i)
    assert (journal.path / 'ledger.jsonl').stat().st_size > 2 * 1024 * 1024
    observed = inspect_run(tmp_path / 'runs', journal.run_id)
    assert observed['state']['counter'] == 35 and observed['state']['goal'] == 'original goal'
    journal.close()
    resumed = RunState(tmp_path / 'runs', journal.run_id)
    try:
        assert resumed.state['counter'] == 35
        size = (resumed.path / 'ledger.jsonl').stat().st_size
        monkeypatch.setattr(run_state, 'MAX_LEDGER_BYTES', size + 10)
        with pytest.raises(OSError, match='capacity'):
            resumed.record('exceeds_cap', incremental=True)
        assert (resumed.path / 'ledger.jsonl').stat().st_size == size
        with pytest.raises(RuntimeError, match='failed'):
            resumed.record('cannot_retry')
    finally:
        resumed.close()


def test_malformed_delta_and_oversized_record_rejected(tmp_path, monkeypatch):
    from dream.core import run_state
    journal = RunState(tmp_path / 'runs')
    journal.record('created', goal='original goal')
    monkeypatch.setattr(run_state, 'MAX_RECORD_BYTES', 200)
    with pytest.raises(ValueError, match='capacity'):
        journal.record('too_large', data={'x': 'x' * 200})
    journal.close()
    with (journal.path / 'ledger.jsonl').open('a') as stream:
        stream.write(json.dumps({'version': 2, 'seq': 2, 'at': '', 'event': 'bad', 'data': {}, 'changes': []}) + '\n')
    with pytest.raises(ValueError, match='delta'):
        RunState(tmp_path / 'runs', journal.run_id)


@pytest.mark.parametrize('field,value', [('unresolved', []), ('version', 2), ('revision', True), ('task_id', 0), ('receipt', 'forged')])
def test_malformed_managed_state_fails_closed_on_restart(managed, field, value):
    c, key = managed
    state = c._state
    state[field] = value
    c.journal.record('malformed_fixture', incremental=True, managed=state)
    c.journal.close()
    with pytest.raises(ValueError, match='managed'):
        RunState(c.journal.path.parent, c.journal.run_id)


async def test_task_tool_cannot_forge_done_or_change_contract(managed, monkeypatch, tmp_path):
    from dream import config
    from dream.tools.context import ToolContext, bind_context
    from dream.tools.task_tools import task_update
    c, key = managed
    # Fixture explicitly files the task in this workspace before invoking the
    # normal project-aware tool, so a wrong-project refusal cannot mask a bug.
    from dream.memory.project import project_key
    owner = project_key(tmp_path)
    c.tasks._store.project = owner
    c.tasks._conn.execute('UPDATE tasks SET project=? WHERE id=?', (owner, c.task['id']))
    c.tasks._conn.commit()
    state = c._state
    state['project'] = owner
    c.journal.record('fixture_scope', managed=state)
    monkeypatch.setattr(config, 'THREADS_FILE', tmp_path / 'THREADS.md')
    context = ToolContext(store=c.tasks._store, working=None, browser=None,
                          session_id='fixture', workspace=tmp_path, tasks=c.tasks)
    with bind_context(context):
        result = await task_update.handler({'id': c.task['id'], 'status': 'done',
                                            'managed_receipt': 'forged', 'notes': 'supplemental'})
        assert not result.get('is_error') and 'Review requested' in result['content'][0]['text']
        assert c.task['status'] == 'active' and c.task['managed_receipt'] == ''
        changed = await task_update.handler({'id': c.task['id'], 'title': 'easier definition'})
        assert changed['is_error']
        moved = await task_update.handler({'id': c.task['id'], 'project': 'user'})
        assert moved['is_error'] and c.task['project'] == owner
        malformed = await task_update.handler({'id': True, 'status': 'done'})
        assert malformed['is_error']
        # Protected-field-only input has no permitted mutation.
        forged = await task_update.handler({'id': c.task['id'], 'managed_receipt': 'forged'})
        assert forged['is_error']
    assert c.task['notes'] == 'supplemental'


async def test_handler_cancellation_is_unresolved_not_success(managed):
    from claude_agent_sdk import SdkMcpTool
    c, key = managed
    async def handler(args):
        raise asyncio.CancelledError
    with bind(c), pytest.raises(asyncio.CancelledError):
        await wrap_tool(SdkMcpTool(name='cancel', description='', input_schema={}, handler=handler)).handler({})
    assert next(iter(c.unresolved_actions.values()))['disposition'] == 'interrupted'
    with pytest.raises(ValueError, match='unresolved'):
        verified(c, key)


def test_boundary_rejects_wrong_generation_and_evidence_changed_after_prepare(managed):
    c, key = managed
    receipt = verified(c, key)
    c.complete_task(receipt['id'])
    token = c.prepare_boundary(receipt['id'])
    with pytest.raises(ValueError, match='matching'):
        c.acknowledge_boundary(token['id'], generation=token['generation'] + 1, settled=True)
    (c.journal.path / 'progress.md').write_text('different after preparation')
    with pytest.raises(ValueError, match='evidence'):
        c.acknowledge_boundary(token['id'], generation=token['generation'], settled=True)
    assert c._state['generation'] == 0
