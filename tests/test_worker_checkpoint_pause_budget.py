"""Durable pause preserves execution time without renewing worker authority."""
import hashlib
import json

import pytest

from dream.core import worker_checkpoints as checkpoints
from test_worker_checkpoints import BINDING, RUN, SESSION, SNAPSHOT, denied


SECOND = 1_000_000_000


@pytest.fixture
def clock(monkeypatch):
    now = [100 * SECOND]
    monkeypatch.setattr(checkpoints.time, 'time_ns', lambda: now[0])
    return now


def create(journal, clock, *, rounds=3):
    journal.create(BINDING, SNAPSHOT, deadline_ns=clock[0] + 10 * SECOND,
                   max_rounds=rounds)


def test_long_pause_and_reopen_preserve_only_remaining_execution_time(tmp_path, clock):
    with checkpoints.WorkerJournal(tmp_path, SESSION, RUN, create=True) as journal:
        create(journal, clock)
        clock[0] += 3 * SECOND
        journal.request_started(now_ns=clock[0])
        journal.settle(SNAPSHOT)
        message = journal.message('Retain this owner instruction')
        journal.control('paused')
        saved = journal.inspect()
    clock[0] += 100 * SECOND
    with checkpoints.WorkerJournal(tmp_path, SESSION, RUN) as restored:
        with denied(checkpoints.WorkerCheckpointError, 'paused'):
            restored.resume(BINDING, now_ns=clock[0])
        restored.control('running')
        restored.resume(BINDING, now_ns=clock[0])
        state = restored.inspect()
        assert state['deadline_ns'] == clock[0] + 7 * SECOND
        assert state['requests'] == 1 and state['max_rounds'] == 3
        assert state['created_ns'] == saved['created_ns']
        assert state['binding'] == BINDING and state['snapshot'] == SNAPSHOT
        assert state['messages'] == [{'id': message, 'text': 'Retain this owner instruction'}]
    clock[0] += 7 * SECOND
    with checkpoints.WorkerJournal(tmp_path, SESSION, RUN) as restored:
        with denied(checkpoints.WorkerCheckpointError, 'expired'):
            restored.resume(BINDING, now_ns=clock[0])


def test_duplicate_controls_do_not_credit_time_twice(tmp_path, clock):
    with checkpoints.WorkerJournal(tmp_path, SESSION, RUN, create=True) as journal:
        create(journal, clock)
        clock[0] += 2 * SECOND
        journal.control('paused')
        clock[0] += 20 * SECOND
        journal.control('paused')
        clock[0] += 30 * SECOND
        journal.control('running')
        deadline = journal.inspect()['deadline_ns']
        assert deadline == clock[0] + 8 * SECOND
        clock[0] += SECOND
        journal.control('running')
        assert journal.inspect()['deadline_ns'] == deadline


@pytest.mark.parametrize('pending', [False, True])
def test_pause_never_erases_request_debt_or_unsettled_effects(tmp_path, clock, pending):
    with checkpoints.WorkerJournal(tmp_path, SESSION, RUN, create=True) as journal:
        create(journal, clock, rounds=1)
        journal.request_started(now_ns=clock[0])
        if pending:
            journal.tool_started('unfinished-call')
        else:
            journal.settle(SNAPSHOT)
        before = journal.inspect()
        journal.control('paused')
    clock[0] += 100 * SECOND
    with checkpoints.WorkerJournal(tmp_path, SESSION, RUN) as journal:
        journal.control('running')
        for operation in ('resume', 'request_started'):
            with denied(checkpoints.WorkerCheckpointError, 'unsettled' if pending else 'round_limit'):
                if operation == 'resume':
                    journal.resume(BINDING, now_ns=clock[0])
                else:
                    journal.request_started(now_ns=clock[0])
        after = journal.inspect()
        assert after['requests'] == 1 and after['max_rounds'] == 1
        assert after['pending'] == before['pending']


def test_expired_worker_cannot_pause_to_revive(tmp_path, clock):
    with checkpoints.WorkerJournal(tmp_path, SESSION, RUN, create=True) as journal:
        create(journal, clock)
        before = journal.inspect()
        clock[0] += 10 * SECOND
        with denied(checkpoints.WorkerCheckpointError, 'expired'):
            journal.control('paused')
        assert journal.inspect() == before


@pytest.mark.parametrize('control', ['paused', 'running'])
def test_pause_transitions_refuse_backward_clock(tmp_path, clock, control):
    with checkpoints.WorkerJournal(tmp_path, SESSION, RUN, create=True) as journal:
        create(journal, clock)
        clock[0] += SECOND
        journal.control('paused')
        before = journal.inspect()
        clock[0] -= 1
        with denied(checkpoints.WorkerCheckpointError, 'clock_backwards'):
            journal.control(control)
        assert journal.inspect() == before


def test_stop_during_pause_is_final_after_reopen(tmp_path, clock):
    with checkpoints.WorkerJournal(tmp_path, SESSION, RUN, create=True) as journal:
        create(journal, clock)
        journal.control('paused')
        clock[0] += 100 * SECOND
        journal.control('stopped')
    with checkpoints.WorkerJournal(tmp_path, SESSION, RUN) as journal:
        for control in ('running', 'paused'):
            with denied(checkpoints.WorkerCheckpointError, 'stopped'):
                journal.control(control)
        with denied(checkpoints.WorkerCheckpointError, 'stopped'):
            journal.resume(BINDING, now_ns=clock[0])
        assert journal.inspect()['requests'] == 0


@pytest.mark.parametrize('elapsed_seconds', [2, 100])
def test_legacy_generic_pause_never_receives_time_credit(tmp_path, clock, elapsed_seconds):
    with checkpoints.WorkerJournal(tmp_path, SESSION, RUN, create=True) as journal:
        create(journal, clock)
        clock[0] += SECOND
        journal.control('paused')
        original_deadline = journal.inspect()['deadline_ns']
        ledger = journal.path / 'ledger.jsonl'
    # Reproduce the old control event in this exclusively owned fixture. It
    # carries no durable assertion that timeout suspension began at its clock.
    rows = ledger.read_bytes().splitlines(keepends=True)
    row = json.loads(rows[-1])
    row.pop('sha256')
    row['event'] = 'control'
    def encode(value):
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, allow_nan=False).encode('utf-8')
    row['sha256'] = hashlib.sha256(encode(row)).hexdigest()
    ledger.write_bytes(b''.join(rows[:-1]) + encode(row) + b'\n')
    clock[0] += elapsed_seconds * SECOND
    with checkpoints.WorkerJournal(tmp_path, SESSION, RUN) as journal:
        if elapsed_seconds == 100:
            with denied(checkpoints.WorkerCheckpointError, 'expired'):
                journal.control('running')
            assert journal.inspect()['control'] == 'paused'
        else:
            journal.control('running')
            journal.resume(BINDING, now_ns=clock[0])
            assert journal.inspect()['control'] == 'running'
        assert journal.inspect()['deadline_ns'] == original_deadline
        assert journal.inspect()['requests'] == 0


@pytest.mark.parametrize('damage', ['extra_credit', 'cleared_pending', 'changed_messages', 'missing_pause'])
def test_recomputed_resume_record_cannot_invent_time_or_clear_state(tmp_path, clock, damage):
    with checkpoints.WorkerJournal(tmp_path, SESSION, RUN, create=True) as journal:
        create(journal, clock)
        journal.request_started(now_ns=clock[0])
        journal.tool_started('unfinished-call')
        journal.message('Keep the instruction')
        clock[0] += 3 * SECOND
        journal.control('paused')
        clock[0] += 100 * SECOND
        journal.control('running')
        ledger = journal.path / 'ledger.jsonl'
    rows = [json.loads(line) for line in ledger.read_bytes().splitlines()]
    if damage == 'extra_credit':
        rows[-1]['state']['deadline_ns'] += SECOND
    elif damage == 'cleared_pending':
        rows[-1]['state']['pending'] = None
    elif damage == 'changed_messages':
        rows[-1]['state']['messages'] = []
    else:
        rows[-2]['event'] = 'control'
    # Recompute hashes so rejection must come from transition semantics rather
    # than a checksum mismatch. This remains a private synthetic fixture.
    previous = '0' * 64
    encoded = []
    for row in rows:
        row.pop('sha256')
        row['previous'] = previous
        payload = json.dumps(row, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=False, allow_nan=False).encode('utf-8')
        row['sha256'] = previous = hashlib.sha256(payload).hexdigest()
        encoded.append(json.dumps(row, sort_keys=True, separators=(',', ':'),
                                  ensure_ascii=False, allow_nan=False).encode('utf-8') + b'\n')
    ledger.write_bytes(b''.join(encoded))
    with denied(checkpoints.WorkerCheckpointError, 'corrupt'):
        with checkpoints.WorkerJournal(tmp_path, SESSION, RUN):
            pass
