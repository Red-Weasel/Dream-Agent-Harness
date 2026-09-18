"""Own transaction receipts and transcript ordering on disposable real SQLite."""
import json
import sqlite3
from pathlib import Path

import pytest

from dream import config
from dream.memory.store import MemoryStore
from dream.memory.working import WorkingMemory


@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'SESSIONS_DIR', tmp_path / 'sessions')
    store = MemoryStore(tmp_path / 'turns.db')
    store.start_session('session')
    working = WorkingMemory(store, 'session')
    yield store, working
    store.close()


class ConnectionFault:
    """Inject acknowledgment/cleanup faults while retaining real SQL effects."""
    def __init__(self, conn, *, commit_error=None, rollback_error=None, committed=False, begin_error=None):
        self.conn = conn
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.committed = committed
        self.begin_error = begin_error
        self.commits = 0
        self.rollbacks = 0

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def execute(self, statement, *args):
        if statement == 'BEGIN' and self.begin_error:
            raise self.begin_error
        return self.conn.execute(statement, *args)

    def commit(self):
        self.commits += 1
        if self.committed or self.commit_error is None:
            self.conn.commit()
        if self.commit_error:
            raise self.commit_error

    def rollback(self):
        self.rollbacks += 1
        if self.rollback_error:
            raise self.rollback_error
        self.conn.rollback()


def test_receipt_is_own_cursor_after_commit_despite_other_table_insert(memory, monkeypatch):
    store, _ = memory
    conn = store._conn
    proxy = ConnectionFault(conn)
    commit = proxy.commit
    def commit_then_sql():
        commit()
        conn.execute("INSERT INTO working_notes(id,session_id,note,ts) VALUES(77,'session','note','fixture')")
        conn.commit()
    proxy.commit = commit_then_sql
    monkeypatch.setattr(store, '_conn', proxy)
    receipt = store.add_turn('session', 'user', 'exact own turn')
    assert type(receipt) is int and receipt == 1
    assert conn.execute('SELECT last_insert_rowid()').fetchone()[0] == 77
    with sqlite3.connect(store.db_path) as other:
        assert other.execute('SELECT content FROM turns WHERE id=?', (receipt,)).fetchone() == ('exact own turn',)
        assert other.execute('SELECT turn_count FROM sessions').fetchone() == (1,)


def test_active_transaction_is_rejected_without_mutating_committing_or_rolling_back(memory, monkeypatch):
    store, _ = memory
    conn = store._conn
    conn.execute("UPDATE sessions SET title='unrelated pending change'")
    proxy = ConnectionFault(conn)
    monkeypatch.setattr(store, '_conn', proxy)
    with pytest.raises(RuntimeError, match='transaction'):
        store.add_turn('session', 'user', 'must not write')
    assert conn.in_transaction
    assert proxy.commits == proxy.rollbacks == 0
    assert conn.execute('SELECT COUNT(*) FROM turns').fetchone()[0] == 0
    assert conn.execute('SELECT title FROM sessions').fetchone()[0] == 'unrelated pending change'
    with sqlite3.connect(store.db_path) as other:
        assert other.execute('SELECT title FROM sessions').fetchone()[0] is None
    conn.rollback()


@pytest.mark.parametrize('operation', ['INSERT', 'UPDATE'])
@pytest.mark.parametrize('action', ['ABORT', 'ROLLBACK'])
def test_statement_failure_cleans_owned_transaction_before_later_write(memory, operation, action):
    store, working = memory
    table = 'turns' if operation == 'INSERT' else 'sessions'
    store._conn.execute(f"CREATE TRIGGER turn_fault BEFORE {operation} ON {table} BEGIN SELECT RAISE({action}, 'owned statement failed'); END")
    delivered = []
    with pytest.raises(sqlite3.IntegrityError, match='owned statement failed'):
        working.log_turn('user', 'failed own turn', on_commit=delivered.append)
    assert delivered == [] and not working.log_path.exists()
    assert not store._conn.in_transaction
    assert store._conn.execute('SELECT COUNT(*) FROM turns').fetchone()[0] == 0
    assert store._conn.execute('SELECT COUNT(*) FROM turns_fts').fetchone()[0] == 0
    assert store._conn.execute('SELECT turn_count FROM sessions').fetchone()[0] == 0
    store._conn.execute('DROP TRIGGER turn_fault')
    assert store.add_turn('session', 'user', 'later valid turn') == 1
    assert [r['content'] for r in store.session_turns('session')] == ['later valid turn']


@pytest.mark.parametrize('committed', [False, True])
@pytest.mark.parametrize('rollback_fails', [False, True])
def test_commit_failure_never_delivers_receipt_and_preserves_error(memory, monkeypatch, committed, rollback_fails):
    store, working = memory
    conn = store._conn
    original = OSError('commit acknowledgment failed')
    proxy = ConnectionFault(conn, commit_error=original, committed=committed,
                            rollback_error=OSError('rollback cleanup failed') if rollback_fails else None)
    monkeypatch.setattr(store, '_conn', proxy)
    delivered = []
    with pytest.raises(OSError) as caught:
        working.log_turn('user', 'ambiguous turn', on_commit=delivered.append)
    assert caught.value is original
    assert delivered == [] and not working.log_path.exists()
    assert proxy.commits == proxy.rollbacks == 1
    if rollback_fails:
        assert any('rollback cleanup failed' in note for note in original.__notes__)
    else:
        assert not conn.in_transaction
    with sqlite3.connect(store.db_path) as other:
        assert other.execute('SELECT COUNT(*) FROM turns').fetchone()[0] == int(committed)
    conn.rollback()


def test_begin_failure_does_not_claim_transaction_or_attempt_cleanup(memory, monkeypatch):
    store, working = memory
    original = sqlite3.OperationalError('begin refused')
    proxy = ConnectionFault(store._conn, begin_error=original)
    monkeypatch.setattr(store, '_conn', proxy)
    delivered = []
    with pytest.raises(sqlite3.OperationalError) as caught:
        working.log_turn('user', 'never inserted', on_commit=delivered.append)
    assert caught.value is original and delivered == []
    assert proxy.commits == proxy.rollbacks == 0
    assert not proxy.in_transaction and not working.log_path.exists()


def test_callback_observes_committed_row_before_jsonl_and_normal_return_is_none(memory):
    store, working = memory
    received = []
    def receive(turn_id):
        assert not working.log_path.exists()
        with sqlite3.connect(store.db_path) as other:
            assert other.execute('SELECT role,content,tool_name FROM turns WHERE id=?', (turn_id,)).fetchone() == ('tool_result', '界 exact\n text', 'tool')
        received.append(turn_id)
    assert working.log_turn('tool_result', '界 exact\n text', 'tool', on_commit=receive) is None
    assert received == [1]
    assert json.loads(working.log_path.read_text())['content'] == '界 exact\n text'


def test_callback_failure_preserves_error_committed_row_and_skips_jsonl(memory):
    store, working = memory
    original = RuntimeError('receipt consumer failed')
    received = []
    def receive(turn_id):
        received.append(turn_id)
        raise original
    with pytest.raises(RuntimeError) as caught:
        working.log_turn('user', 'committed but incomplete capture', on_commit=receive)
    assert caught.value is original and received == [1]
    assert len(store.session_turns('session')) == 1
    assert not store._conn.in_transaction and not working.log_path.exists()


@pytest.mark.parametrize('receipt', [None, True, 0, -1, '1', 1.0])
def test_invalid_storage_receipt_never_invokes_callback_or_appends(memory, monkeypatch, receipt):
    store, working = memory
    original = store.add_turn
    def missing_return(*args):
        original(*args)
        return receipt
    monkeypatch.setattr(store, 'add_turn', missing_return)
    received = []
    with pytest.raises(RuntimeError, match='receipt'):
        working.log_turn('user', 'committed but invalid receipt', on_commit=received.append)
    assert received == [] and not working.log_path.exists()
    assert len(store.session_turns('session')) == 1


def test_ordinary_positional_call_remains_none_with_legacy_storage_override(memory, monkeypatch):
    store, working = memory
    original = store.add_turn
    def legacy(session_id, role, content, tool_name):
        original(session_id, role, content, tool_name)
    monkeypatch.setattr(store, 'add_turn', legacy)
    assert working.log_turn('tool_result', 'ordinary text', 'tool') is None
    assert json.loads(working.log_path.read_text())['tool'] == 'tool'
    assert store.session_turns('session')[0]['content'] == 'ordinary text'


@pytest.mark.parametrize('stage', ['prepare', 'open', 'write', 'close'])
def test_jsonl_fault_keeps_delivered_source_and_original_error_without_retry(memory, monkeypatch, stage):
    store, working = memory
    original = OSError('transcript ' + stage + ' failed')
    received = []
    attempts = []
    def fail(*args, **kwargs):
        attempts.append(stage)
        raise original
    class FaultFile:
        def __enter__(self):
            return self
        def write(self, text):
            if stage == 'write':
                fail()
        def __exit__(self, *args):
            if stage == 'close':
                fail()
    if stage == 'prepare':
        monkeypatch.setattr('dream.memory.working.datetime', type('FaultTime', (), {'now': fail}))
    elif stage == 'open':
        monkeypatch.setattr(Path, 'open', fail)
    else:
        monkeypatch.setattr(Path, 'open', lambda *args, **kwargs: FaultFile())
    with pytest.raises(OSError) as caught:
        working.log_turn('user', 'exact retained source', on_commit=received.append)
    assert caught.value is original and received == [1] and attempts == [stage]
    assert len(store.session_turns('session')) == 1
    assert not store._conn.in_transaction
