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


# Bound individual allocations and the complete stream independently. Frequent
# action/event records use version-2 top-level deltas; old full-state ledgers
# remain readable. Hitting either ceiling stops writes visibly, never discards
# history. This is a capacity bound, not a 12-hour endurance claim.
MAX_RECORD_BYTES = 2 * 1024 * 1024
MAX_LEDGER_BYTES = 512 * 1024 * 1024


def _bounded_lines(source):
    total = 0
    while True:
        line = source.readline(MAX_RECORD_BYTES + 1)
        if not line:
            return
        size = len(line.encode('utf-8')) if isinstance(line, str) else len(line)
        total += size
        if size > MAX_RECORD_BYTES or total > MAX_LEDGER_BYTES:
            raise ValueError('Run ledger exceeds bounded reader capacity')
        yield line.decode('utf-8') if isinstance(line, bytes) else line


RUN_PHASES = frozenset({'contract_needed', 'contract_running', 'ready', 'worker_running', 'review_pending'})
RUN_STATUSES = frozenset({'running', 'done', 'unverified', 'need_input', 'budget', 'stopped', 'error'})


def validate_child_delegations(state: dict) -> None:
    """Validate host ownership records; absence preserves older run journals."""
    managed = state.get('managed', {})
    children = managed.get('children', {})
    def invalid():
        raise ValueError('Invalid managed child delegation')
    if type(children) is not dict or len(children) > 64:
        invalid()
    workers = set()
    fields = {'version', 'delegation_id', 'parent_run_id', 'task_id', 'project', 'revision',
              'contract_sha256', 'worker_id', 'session_id', 'call_id',
              'arguments_sha256', 'execution_sha256', 'parent_scope_sha256'}
    for identity, record in children.items():
        if (not isinstance(identity, str) or re.fullmatch(r'[0-9a-f]{32}', identity) is None
                or type(record) is not dict or set(record) != {'binding', 'status', 'completion'}):
            invalid()
        binding = record['binding']
        if (type(binding) is not dict or set(binding) != fields
                or type(binding['version']) is not int or binding['version'] != 1
                or binding['delegation_id'] != identity
                or binding['parent_run_id'] != state.get('run_id')):
            invalid()
        for key in ('task_id', 'project', 'revision', 'contract_sha256'):
            if type(binding[key]) is not type(managed[key]) or binding[key] != managed[key]:
                invalid()
        worker = binding['worker_id']
        if (not isinstance(worker, str) or re.fullmatch(r'[0-9a-f]{32}', worker) is None
                or worker in workers):
            invalid()
        workers.add(worker)
        for key in ('session_id', 'call_id'):
            if not isinstance(binding[key], str) or not binding[key].strip() or len(binding[key]) > 256:
                invalid()
        for key in ('arguments_sha256', 'execution_sha256', 'parent_scope_sha256'):
            if not isinstance(binding[key], str) or re.fullmatch(r'[0-9a-f]{64}', binding[key]) is None:
                invalid()
        if record['status'] not in ('reserved', 'returned'):
            invalid()
        completion = record['completion']
        if record['status'] == 'reserved':
            if completion is not None:
                invalid()
            continue
        if (type(completion) is not dict
                or set(completion) != {'completion_seq', 'outcome', 'snapshot_receipt', 'result_sha256'}
                or type(completion['completion_seq']) is not int or completion['completion_seq'] < 1
                or completion['outcome'] not in ('completed', 'incomplete')
                or not isinstance(completion['result_sha256'], str)
                or re.fullmatch(r'[0-9a-f]{64}', completion['result_sha256']) is None):
            invalid()
        receipt = completion['snapshot_receipt']
        fields_receipt = {'version', 'handle', 'sha256', 'full_bytes', 'workspace_key',
                          'session_key', 'run_key', 'call_key'}
        if (type(receipt) is not dict or set(receipt) != fields_receipt
                or type(receipt['version']) is not int or receipt['version'] != 1
                or type(receipt['full_bytes']) is not int or not 0 <= receipt['full_bytes'] <= 8 * 1024 * 1024
                or not isinstance(receipt['handle'], str)):
            invalid()
        match = re.fullmatch(r'dream-artifact:v1:([0-9a-f]{64}):([0-9a-f]{64}):'
                             r'([0-9a-f]{64}):([0-9a-f]{64}):([0-9a-f]{64}):(0|[1-9][0-9]{0,9})', receipt['handle'])
        if (match is None or receipt['full_bytes'] != int(match[6])
                or any(receipt[k] != v for k, v in zip(
                    ('workspace_key', 'session_key', 'run_key', 'call_key', 'sha256'), match.groups()[:5]))
                or receipt['session_key'] != digest(binding['session_id'])
                or receipt['run_key'] != digest(worker)):
            invalid()
    for action in (*managed.get('unresolved', {}).values(), *managed.get('recent', [])):
        if isinstance(action, dict) and 'delegation_id' in action:
            identity = action['delegation_id']
            if not isinstance(identity, str) or identity not in children:
                invalid()


def validate_state_fields(state: dict) -> None:
    """Validate known fields when present, preserving generic partial journals."""
    def invalid(field):
        raise ValueError(f"Invalid run state field: {field}")

    if state.get('parent_context') is not None:
        from .parent_continuation import validate_reference
        validate_reference(state['parent_context'], run_id=state.get('run_id'))
    if 'parent_context_superseded' in state and type(state['parent_context_superseded']) is not bool:
        invalid('parent_context_superseded')

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
    if 'standing_rules' in state and not isinstance(state['standing_rules'], str):
        invalid('standing_rules')
    if 'managed' in state:
        managed = state['managed']
        required = {'version', 'task_id', 'project', 'revision', 'contract_sha256',
                    'unresolved', 'recent', 'action_revision', 'evidence_chain',
                    'receipt', 'completed_receipt', 'boundary', 'generation'}
        if not isinstance(managed, dict) or not required <= managed.keys():
            invalid('managed')
        if type(managed['version']) is not int or managed['version'] != 1:
            invalid('managed.version')
        for key, minimum in (('task_id', 1), ('revision', 1), ('action_revision', 0), ('generation', 0)):
            if type(managed[key]) is not int or managed[key] < minimum:
                invalid('managed.' + key)
        if not isinstance(managed['project'], str):
            invalid('managed.project')
        for key in ('contract_sha256', 'evidence_chain'):
            if not isinstance(managed[key], str) or re.fullmatch(r'[0-9a-f]{64}', managed[key]) is None:
                invalid('managed.' + key)
        if (not isinstance(managed['unresolved'], dict) or len(managed['unresolved']) > 128
                or not isinstance(managed['recent'], list) or len(managed['recent']) > 16):
            invalid('managed.actions')
        for action_id, action in managed['unresolved'].items():
            if (not isinstance(action, dict) or action.get('id') != action_id
                    or action.get('disposition') not in {'intent', 'error', 'interrupted', 'unknown'}):
                invalid('managed.unresolved')
        for key in ('receipt', 'boundary'):
            if managed[key] is not None and not isinstance(managed[key], dict):
                invalid('managed.' + key)
        if managed['completed_receipt'] is not None and not isinstance(managed['completed_receipt'], str):
            invalid('managed.completed_receipt')
        validate_child_delegations(state)
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


def _read_ledger(source, run_id: str, *, metadata: dict | None = None) -> tuple[dict, int, tuple[ReconciliationNote, ...]]:
    state, seq, notes = {}, 0, ()
    for line in _bounded_lines(source):
        # Decoded escapes, including unknown keys/values, must
        # remain representable by the existing ledger writer.
        try:
            record = json.loads(line, parse_constant=_finite_float, parse_float=_finite_float)
            json.dumps(record, ensure_ascii=False, allow_nan=False).encode('utf-8')
        except RecursionError as exc:
            raise ValueError('Run ledger exceeds supported nesting depth') from exc
        except UnicodeEncodeError as exc:
            raise ValueError('Run ledger contains text that cannot be encoded as UTF-8') from exc
        if not isinstance(record, dict):
            raise ValueError('Invalid run ledger record shape')
        if record.get('version') == 2:
            if not state or not isinstance(record.get('changes'), dict) or 'state' in record:
                raise ValueError('Invalid delta run ledger record')
            record['state'] = {**state, **record['changes']}
        elif 'version' in record:
            raise ValueError('Unsupported run ledger version')
        if not isinstance(record.get('state'), dict):
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
        if metadata is not None:
            metadata['updated_at'] = record['at']
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
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_LEDGER_BYTES:
                raise ValueError('Run ledger is not a bounded regular file')
            metadata = {}
            state, seq, _ = _read_ledger(source, run_id, metadata=metadata)
            updated_at = metadata['updated_at']
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
    pending_children = any(c['status'] == 'reserved' for c in state.get('managed', {}).get('children', {}).values())
    reconciliation = bool(state.get('managed', {}).get('unresolved')) or pending_children or bool(state.get('uncertain')) or (ownership != 'held' and interrupted_phase)
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

    def record(self, event: str, *, data: dict | None = None, incremental: bool = False, **changes) -> None:
        if self.write_error is not None:
            raise RuntimeError('Cannot append after a failed run ledger write') from self.write_error
        if self._lock is None:
            raise RuntimeError('Cannot append to a closed run ledger')
        state = {**self.state, **changes, 'run_id': self.run_id}
        record = {'seq': self.seq + 1, 'at': datetime.now(timezone.utc).isoformat(),
                  'event': event, 'data': data or {}, 'state': state}
        note = _note_from_record(record)
        projected_notes = self._reconciliation_notes + (note,) if note is not None else self._reconciliation_notes
        if incremental and self.seq:
            record = {k: v for k, v in record.items() if k != 'state'}
            record.update(version=2, changes={**changes, 'run_id': self.run_id})
        encoded = (json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
        if len(encoded) > MAX_RECORD_BYTES:
            raise ValueError('Run record exceeds bounded reader capacity')
        fd = out = None
        try:
            fd = os.open(self.path / 'ledger.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            if os.fstat(fd).st_size + len(encoded) > MAX_LEDGER_BYTES:
                raise OSError('Run ledger capacity reached; reconcile/archive before continuing')
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
