"""Durable worker journal contracts with pure, exclusively owned tmp fixtures."""
from copy import deepcopy
import importlib
import importlib.util
from pathlib import Path
import time

import pytest


SESSION = 'checkpoint-fixture'
RUN = 'a' * 32
BINDING = {'task': 'fixture-task', 'parent': 'fixture-parent', 'tools': ['read_file']}
SNAPSHOT = {'history': ['snapshot-sentinel-48fba'], 'rules': 'Original rules', 'spent': 2}


def api():
    name = 'dream.core.worker_checkpoints'
    assert importlib.util.find_spec(name) is not None, 'Worker checkpoint module is absent.'
    module = importlib.import_module(name)
    journal = getattr(module, 'WorkerJournal', None)
    error = getattr(module, 'WorkerCheckpointError', None)
    assert callable(journal), 'WorkerJournal API is absent.'
    assert isinstance(error, type) and issubclass(error, Exception), 'Typed checkpoint error is absent.'
    return journal, error


def create(journal, *, max_rounds=3):
    journal.create(deepcopy(BINDING), deepcopy(SNAPSHOT),
                   deadline_ns=time.time_ns() + 60_000_000_000, max_rounds=max_rounds)
    return journal.inspect()


def denied(error, code):
    return pytest.raises(error, match=None) if code is None else _Denied(error, code)


class _Denied:
    def __init__(self, error, code):
        self.context = pytest.raises(error)
        self.code = code

    def __enter__(self):
        self.info = self.context.__enter__()
        return self.info

    def __exit__(self, *exception):
        result = self.context.__exit__(*exception)
        assert self.info.value.code == self.code
        return result


def test_checkpoint_api_is_explicit():
    api()


def test_fresh_object_restores_exact_snapshot_binding_and_request_debt(tmp_path):
    Journal, _ = api()
    with Journal(tmp_path, SESSION, RUN, create=True) as journal:
        before = create(journal)
        journal.request_started(now_ns=before['last_clock_ns'] + 1)
        updated = {**SNAPSHOT, 'history': ['Settled reply'], 'spent': 3}
        journal.settle(updated)
        saved = journal.inspect()
        assert saved['requests'] == 1 and saved['pending'] is None
        assert saved['snapshot_receipt']
    with Journal(tmp_path, SESSION, RUN) as restored:
        restored.resume(deepcopy(BINDING), now_ns=saved['last_clock_ns'] + 1)
        state = restored.inspect()
        assert state['binding'] == BINDING and state['snapshot'] == updated
        assert state['requests'] == 1 and state['max_rounds'] == 3
        assert state['deadline_ns'] == before['deadline_ns']
        assert state['created_ns'] == before['created_ns']


def test_inspection_mutation_cannot_change_persisted_snapshot(tmp_path):
    Journal, _ = api()
    with Journal(tmp_path, SESSION, RUN, create=True) as journal:
        create(journal)
        state = journal.inspect()
        state['snapshot']['history'].append('Not saved')
        state['binding']['tools'].append('run_bash')
        assert journal.inspect()['snapshot'] == SNAPSHOT
        assert journal.inspect()['binding'] == BINDING


def test_exclusive_driver_lock_refuses_concurrent_object_then_releases(tmp_path):
    Journal, Error = api()
    with Journal(tmp_path, SESSION, RUN, create=True) as journal:
        create(journal)
        with denied(Error, 'locked'):
            with Journal(tmp_path, SESSION, RUN):
                pytest.fail('A concurrent driver entered the journal.')
    with Journal(tmp_path, SESSION, RUN) as restored:
        assert restored.inspect()['snapshot'] == SNAPSHOT


@pytest.mark.parametrize('tool_state', ['none', 'started', 'finished'])
def test_request_and_tool_receipt_without_settled_snapshot_cannot_resume(tmp_path, tool_state):
    Journal, Error = api()
    with Journal(tmp_path, SESSION, RUN, create=True) as journal:
        state = create(journal)
        journal.request_started(now_ns=state['last_clock_ns'] + 1)
        if tool_state != 'none':
            journal.tool_started('call-fixture')
        if tool_state == 'finished':
            journal.tool_finished('call-fixture', 'b' * 64)
        pending = journal.inspect()
        assert pending['pending'] is not None
    with Journal(tmp_path, SESSION, RUN) as restored:
        with denied(Error, 'unsettled'):
            restored.resume(BINDING, now_ns=pending['last_clock_ns'] + 1)
        with denied(Error, 'unsettled'):
            restored.request_started(now_ns=pending['last_clock_ns'] + 1)
        assert restored.inspect()['requests'] == 1


@pytest.mark.parametrize('operation', ['resume', 'request_started'])
@pytest.mark.parametrize('fault', ['expired', 'clock_backwards'])
def test_deadline_and_clock_cannot_be_renewed_by_new_driver(tmp_path, operation, fault):
    Journal, Error = api()
    with Journal(tmp_path, SESSION, RUN, create=True) as journal:
        saved = create(journal)
    now = saved['deadline_ns'] + 1 if fault == 'expired' else saved['last_clock_ns'] - 1
    with Journal(tmp_path, SESSION, RUN) as restored:
        with denied(Error, fault):
            if operation == 'resume':
                restored.resume(BINDING, now_ns=now)
            else:
                restored.request_started(now_ns=now)
        assert restored.inspect()['deadline_ns'] == saved['deadline_ns']
        assert restored.inspect()['requests'] == 0


def test_exhausted_rounds_remain_exhausted_after_restore(tmp_path):
    Journal, Error = api()
    with Journal(tmp_path, SESSION, RUN, create=True) as journal:
        state = create(journal, max_rounds=1)
        journal.request_started(now_ns=state['last_clock_ns'] + 1)
        journal.settle(SNAPSHOT)
        saved = journal.inspect()
    with Journal(tmp_path, SESSION, RUN) as restored:
        with denied(Error, 'round_limit'):
            restored.resume(BINDING, now_ns=saved['last_clock_ns'] + 1)
        with denied(Error, 'round_limit'):
            restored.request_started(now_ns=saved['last_clock_ns'] + 1)
        assert restored.inspect()['requests'] == 1


@pytest.mark.parametrize('control,code', [('paused', 'paused'), ('stopped', 'stopped')])
def test_persisted_controls_refuse_fresh_driver_admission(tmp_path, control, code):
    Journal, Error = api()
    with Journal(tmp_path, SESSION, RUN, create=True) as journal:
        create(journal)
        journal.control(control)
        saved = journal.inspect()
    with Journal(tmp_path, SESSION, RUN) as restored:
        assert restored.inspect()['control'] == control
        with denied(Error, code):
            restored.resume(BINDING, now_ns=saved['last_clock_ns'] + 1)
        with denied(Error, code):
            restored.request_started(now_ns=saved['last_clock_ns'] + 1)


def test_ordered_messages_survive_restore_and_only_consumed_ids_disappear(tmp_path):
    Journal, _ = api()
    with Journal(tmp_path, SESSION, RUN, create=True) as journal:
        create(journal)
        first = journal.message('First owner message')
        second = journal.message('Second owner message')
        assert isinstance(first, str) and len(first) == 32 and int(first, 16) >= 0
        assert first != second
    with Journal(tmp_path, SESSION, RUN) as restored:
        assert restored.inspect()['messages'] == [
            {'id': first, 'text': 'First owner message'}, {'id': second, 'text': 'Second owner message'}]
        restored.settle(SNAPSHOT, consumed_messages=(first,))
    with Journal(tmp_path, SESSION, RUN) as restored:
        assert restored.inspect()['messages'] == [{'id': second, 'text': 'Second owner message'}]


def test_unconsumed_owner_message_cannot_be_marked_completed(tmp_path):
    Journal, Error = api()
    with Journal(tmp_path, SESSION, RUN, create=True) as journal:
        create(journal)
        identity = journal.message('New instruction must be addressed before completion')
        before = journal.inspect()
        with denied(Error, 'pending_messages'):
            journal.finish('completed', SNAPSHOT)
        assert journal.inspect() == before
        journal.finish('incomplete', SNAPSHOT)
    with Journal(tmp_path, SESSION, RUN) as restored:
        assert restored.inspect()['outcome'] == 'incomplete'
        assert restored.inspect()['messages'] == [{'id': identity,
            'text': 'New instruction must be addressed before completion'}]


def test_completed_ledger_with_pending_message_refuses_reopen(tmp_path):
    import hashlib
    import json
    from dream.core.worker_checkpoints import _encode
    Journal, Error = api()
    with Journal(tmp_path, SESSION, RUN, create=True) as journal:
        create(journal)
        journal.finish('completed', SNAPSHOT)
        ledger = journal.path / 'ledger.jsonl'
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    rows[-1]['state']['messages'] = [{'id': 'b' * 32, 'text': 'Unconsumed correction'}]
    rows[-1].pop('sha256')
    rows[-1]['sha256'] = hashlib.sha256(_encode(rows[-1])).hexdigest()
    ledger.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    with denied(Error, 'invalid_state'):
        with Journal(tmp_path, SESSION, RUN):
            pass


def test_pause_can_be_explicitly_released_without_resetting_budget(tmp_path):
    Journal, _ = api()
    with Journal(tmp_path, SESSION, RUN, create=True) as journal:
        create(journal)
        journal.control('paused')
        journal.control('running')
        saved = journal.inspect()
        journal.resume(BINDING, now_ns=saved['last_clock_ns'] + 1)
        assert journal.inspect()['control'] == 'running'
        assert journal.inspect()['requests'] == 0
        assert journal.inspect()['deadline_ns'] == saved['deadline_ns']


@pytest.mark.parametrize('outcome', ['completed', 'incomplete'])
def test_terminal_publication_before_ack_survives_and_never_reruns(tmp_path, outcome):
    Journal, Error = api()
    with Journal(tmp_path, SESSION, RUN, create=True) as journal:
        create(journal)
        sequence = journal.finish(outcome, SNAPSHOT)
        state = journal.inspect()
        assert isinstance(sequence, int) and state['completion_seq'] == sequence
        assert state['outcome'] == outcome and state['acknowledged'] is False
    with Journal(tmp_path, SESSION, RUN) as restored:
        with denied(Error, 'terminal'):
            restored.resume(BINDING, now_ns=state['last_clock_ns'] + 1)
        with denied(Error, 'terminal'):
            restored.request_started(now_ns=state['last_clock_ns'] + 1)
        with denied(Error, 'invalid_ack'):
            restored.acknowledge(sequence + 1)
        restored.acknowledge(sequence)
        restored.acknowledge(sequence)
    with Journal(tmp_path, SESSION, RUN) as restored:
        assert restored.inspect()['acknowledged'] is True
        assert restored.inspect()['completion_seq'] == sequence
        assert restored.inspect()['outcome'] == outcome


def test_binding_change_cannot_restore_or_widen_tools(tmp_path):
    Journal, Error = api()
    with Journal(tmp_path, SESSION, RUN, create=True) as journal:
        state = create(journal)
        with denied(Error, 'binding_mismatch'):
            journal.resume({**BINDING, 'tools': ['read_file', 'run_bash']}, now_ns=state['last_clock_ns'] + 1)
        assert journal.inspect()['binding'] == BINDING


def test_foreign_session_cannot_open_original_journal(tmp_path):
    Journal, Error = api()
    with Journal(tmp_path, SESSION, RUN, create=True) as journal:
        create(journal)
    with pytest.raises(Error):
        with Journal(tmp_path, 'foreign-session', RUN) as foreign:
            foreign.resume(BINDING, now_ns=time.time_ns())


@pytest.mark.parametrize('damage', ['truncated', 'malformed'])
def test_damaged_journal_refuses_without_admitting_a_request(tmp_path, damage):
    Journal, Error = api()
    with Journal(tmp_path, SESSION, RUN, create=True) as journal:
        create(journal)
    documents = [p for p in Path(tmp_path).rglob('*')
                 if p.is_file() and p.suffix != '.utf8'
                 and b'"deadline_ns"' in p.read_bytes() and b'"requests"' in p.read_bytes()]
    assert documents, 'Journal must retain its request and deadline state.'
    for document in documents:
        document.write_bytes(document.read_bytes()[:12] if damage == 'truncated' else b'not valid JSON\n')
    with pytest.raises(Error):
        with Journal(tmp_path, SESSION, RUN) as restored:
            restored.resume(BINDING, now_ns=time.time_ns())


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_missing_or_corrupt_snapshot_original_cannot_restore(tmp_path, damage):
    Journal, Error = api()
    with Journal(tmp_path, SESSION, RUN, create=True) as journal:
        create(journal)
    originals = [p for p in Path(tmp_path).rglob('*.utf8')
                 if b'snapshot-sentinel-48fba' in p.read_bytes()]
    assert originals, 'Snapshot must have a retained ArtifactStore original.'
    for original in originals:
        if damage == 'missing':
            original.unlink()
        else:
            original.write_bytes(b'Tampered snapshot original')
    with pytest.raises(Error):
        with Journal(tmp_path, SESSION, RUN) as restored:
            restored.resume(BINDING, now_ns=time.time_ns())
