"""Durable run checkpoints. The fsynced append-only ledger is authoritative.

state.json is an atomic, replaceable view of the latest ledger record. A run lock
is held by its driver, including across awaits; recovery never executes an action.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import io
import tempfile
from datetime import datetime, timezone
from dataclasses import dataclass
from uuid import uuid4


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


RUN_PHASES = frozenset({'contract_needed', 'contract_running', 'ready', 'worker_running', 'review_pending'})
RUN_STATUSES = frozenset({'running', 'done', 'unverified', 'need_input', 'budget', 'stopped', 'error'})


def validate_state_fields(state: dict) -> None:
    """Validate known fields when present, preserving generic partial journals."""
    def invalid(field):
        raise ValueError(f"Invalid run state field: {field}")

    def enum(field, choices, *, nullable=False):
        if field in state:
            value = state[field]
            if nullable and value is None:
                return
            if not isinstance(value, str) or value not in choices:
                invalid(field)

    enum('phase', RUN_PHASES)
    enum('status', RUN_STATUSES)
    enum('worker_status', {'CONTINUE', 'DONE', 'NEED_INPUT'}, nullable=True)
    enum('verdict', {'PASS', 'FAIL', 'UNVERIFIED'})
    for field in ('run_id', 'goal', 'worker_workspace', 'contract', 'next', 'response', 'gaps'):
        if field in state and not isinstance(state[field], str):
            invalid(field)
    if 'iterations' in state and (type(state['iterations']) is not int or state['iterations'] < 0):
        invalid('iterations')
    if 'uncertain' in state and type(state['uncertain']) is not bool:
        invalid('uncertain')
    for field in ('contract_sha256', 'prompt_sha256'):
        if field in state and (not isinstance(state[field], str)
                               or re.fullmatch(r'[0-9a-fA-F]{64}', state[field]) is None):
            invalid(field)
    result = state.get('result')
    if result is not None:
        if not isinstance(result, dict) or set(result) - {'status', 'iterations', 'message', 'workspace', 'run_id'}:
            invalid('result')
        if (not isinstance(result.get('status'), str) or result['status'] not in RUN_STATUSES - {'running'}
                or type(result.get('iterations')) is not int or result['iterations'] < 0):
            invalid('result')
        if 'message' in result and not isinstance(result['message'], str):
            invalid('result.message')
        for field in ('workspace', 'run_id'):
            if result.get(field) is not None and not isinstance(result[field], str):
                invalid('result.' + field)


def _finite_float(value: str) -> float:
    # The writer uses allow_nan=False; reject constants and float overflow before
    # opaque JSON values can enter a loaded state that cannot be written again.
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Non-finite number in run ledger')
    return number


@dataclass(frozen=True)
class ReconciliationNote:
    run_id: str
    seq: int
    at: str
    text: str


def _note_from_record(record: dict) -> ReconciliationNote | None:
    if record['event'] != 'resume_requested':
        return None
    data = record['data']
    if not isinstance(data, dict):
        raise ValueError('Invalid resume_requested note data')
    if 'note' not in data:
        return None
    text = data['note']
    if not isinstance(text, str):
        raise ValueError('Invalid resume_requested note: expected a string')
    if not text.strip():
        return None
    return ReconciliationNote(record['state']['run_id'], record['seq'], record['at'], text)


def _read_ledger(source, run_id: str) -> tuple[dict, int, tuple[ReconciliationNote, ...]]:
    state, seq, notes = {}, 0, ()
    for line in source:
        # Decoded escapes, including unknown keys/values, must
        # remain representable by the existing ledger writer.
        try:
            record = json.loads(line, parse_constant=_finite_float, parse_float=_finite_float)
            json.dumps(record, ensure_ascii=False, allow_nan=False).encode('utf-8')
        except RecursionError as exc:
            raise ValueError('Run ledger exceeds supported nesting depth') from exc
        except UnicodeEncodeError as exc:
            raise ValueError('Run ledger contains text that cannot be encoded as UTF-8') from exc
        if not isinstance(record, dict) or not isinstance(record.get('state'), dict):
            raise ValueError('Invalid run ledger record shape')
        if (not line.endswith('\n') or type(record.get('seq')) is not int
                or record['seq'] != seq + 1):
            raise ValueError('Incomplete or out-of-order run ledger')
        if (not isinstance(record.get('at'), str) or not isinstance(record.get('event'), str)
                or not isinstance(record.get('data'), dict)):
            raise ValueError('Invalid run ledger envelope')
        if record['state'].get('run_id') != run_id:
            raise ValueError('Run ledger identity mismatch')
        validate_state_fields(record['state'])
        note = _note_from_record(record)
        if note is not None:
            notes += (note,)
        seq = record['seq']
        state = record['state']
    if not state:
        raise ValueError('Empty run ledger')
    return state, seq, notes


def inspect_run(root: Path, run_id: str) -> dict:
    """Observe a bounded canonical ledger and its existing kernel lock.

    This never claims, repairs or resumes a run. ``held`` means a lock owner was
    observed, not productive work. ``free`` is fenced during the ledger read by
    a shared lock; ownership can change after this function returns. An absent
    or unreadable lock is unknown, never evidence that a driver has stopped.
    """
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', run_id):
        raise ValueError('Invalid run ID')
    root_fd = run_fd = lock_fd = None
    ownership = 'unknown'
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root_fd = os.open('/', flags)
        for component in Path(os.path.abspath(Path(root).expanduser())).parts[1:]:
            child_fd = os.open(component, flags, dir_fd=root_fd)
            os.close(root_fd)
            root_fd = child_fd
        run_fd = os.open(run_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        try:
            lock_fd = os.open('.lock', os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=run_fd)
            if stat.S_ISREG(os.fstat(lock_fd).st_mode):
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    ownership = 'free'
                except BlockingIOError:
                    ownership = 'held'
        except OSError:
            # A permission error, missing lock or unsupported lock operation
            # cannot establish absence of an owner.
            ownership = 'unknown'
        ledger_fd = os.open('ledger.jsonl', os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=run_fd)
        with os.fdopen(ledger_fd, 'rb') as source:
            info = os.fstat(source.fileno())
            limit = 2 * 1024 * 1024
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ValueError('Run ledger is not a bounded regular file')
            raw = source.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('Run ledger exceeds inspection limit')
        state, seq, _ = _read_ledger(io.StringIO(raw.decode('utf-8')), run_id)
        updated_at = json.loads(raw.splitlines()[-1])['at']
        if lock_fd is not None:
            try:
                opened = os.fstat(lock_fd)
                current = os.stat('.lock', dir_fd=run_fd, follow_symlinks=False)
                if (not stat.S_ISREG(current.st_mode)
                        or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)):
                    ownership = 'unknown'
            except OSError:
                ownership = 'unknown'
    finally:
        for fd in (lock_fd, run_fd, root_fd):
            if fd is not None:
                os.close(fd)
    recorded = state.get('status', 'unknown')
    interrupted_phase = state.get('phase') in {'contract_running', 'worker_running'}
    reconciliation = bool(state.get('uncertain')) or (ownership != 'held' and interrupted_phase)
    status = recorded
    if recorded == 'running':
        status = {'held': 'running', 'free': 'stopped', 'unknown': 'unknown'}[ownership]
    if ownership == 'unknown' and recorded == 'running':
        continuation = 'unknown'
    elif ownership == 'held' and recorded == 'running':
        continuation = 'owned'
    elif reconciliation:
        continuation = 'needs_reconciliation'
    elif recorded == 'running':
        continuation = 'resume_available'
    else:
        continuation = {'done': 'complete', 'need_input': 'waiting_input'}.get(recorded, recorded)
    return {'state': state, 'sequence': seq, 'updated_at': updated_at,
            'recorded_status': recorded, 'status': status, 'ownership': ownership,
            'continuation': continuation, 'requires_reconciliation': reconciliation}


class RunState:
    def __init__(self, root: Path, run_id: str | None = None):
        self.run_id = run_id or uuid4().hex
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', self.run_id):
            raise ValueError('Invalid run ID')
        root = Path(root).expanduser().resolve()
        self.path = root / self.run_id
        if run_id is None:
            self.path.mkdir(mode=0o700, parents=True, exist_ok=False)
        if self.path.is_symlink() or not self.path.is_dir():
            raise ValueError('Run does not exist or is a symlink')
        self._lock = None
        self.state: dict = {}
        self._reconciliation_notes: tuple[ReconciliationNote, ...] = ()
        self.seq = 0
        self.write_error: BaseException | None = None
        lock_fd = os.open(self.path / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        self._lock = os.fdopen(lock_fd, 'a')
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if run_id is not None:
                # Never truncate/repair an uncertain or corrupt ledger implicitly.
                with (self.path / 'ledger.jsonl').open(encoding='utf-8') as source:
                    self.state, self.seq, self._reconciliation_notes = _read_ledger(source, self.run_id)
        except BaseException:
            self.close()
            raise

    @property
    def reconciliation_notes(self) -> tuple[ReconciliationNote, ...]:
        """Read-only ordered projection of canonical resume_requested events."""
        return self._reconciliation_notes

    def record(self, event: str, *, data: dict | None = None, **changes) -> None:
        if self.write_error is not None:
            raise RuntimeError('Cannot append after a failed run ledger write') from self.write_error
        if self._lock is None:
            raise RuntimeError('Cannot append to a closed run ledger')
        state = {**self.state, **changes, 'run_id': self.run_id}
        record = {'seq': self.seq + 1, 'at': datetime.now(timezone.utc).isoformat(),
                  'event': event, 'data': data or {}, 'state': state}
        note = _note_from_record(record)
        projected_notes = self._reconciliation_notes + (note,) if note is not None else self._reconciliation_notes
        encoded = (json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
        fd = out = None
        try:
            fd = os.open(self.path / 'ledger.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            # No buffered bytes may flush during cleanup after an append failure.
            out = os.fdopen(fd, 'wb', buffering=0)
            fd = None  # The stream now owns the descriptor.
            offset = 0
            while offset < len(encoded):
                written = out.write(encoded[offset:])
                if written is None or written <= 0:
                    raise OSError('Run ledger write made no progress')
                offset += written
            out.flush()
            os.fsync(out.fileno())
            # The record is committed even if close or the snapshot fails next.
            self.seq += 1
            self.state = state
            self._reconciliation_notes = projected_notes
        except BaseException as exc:
            self.write_error = exc
            raise
        finally:
            try:
                if out is not None:
                    out.close()
                elif fd is not None:
                    os.close(fd)
            except BaseException as exc:
                if self.write_error is None:
                    self.write_error = exc
                    raise
                # Retain the original append failure if cleanup also failed.
        atomic_write(self.path / 'state.json', json.dumps(state, indent=2) + '\n')

    def close(self) -> None:
        if self._lock is not None:
            self._lock.close()
            self._lock = None
