"""Host-owned worker restart journal (DREAM-260).

Immutable scoped artifacts hold snapshots; a small fsynced hash-chain journal
holds intent, outcomes, controls and consumed budgets. A kernel lock fences
drivers. An unsettled request cannot resume, even when its tools returned: only
a committed snapshot settles it. This module grants no tool permission, verifies
no task completion, and never executes/replays an action. Backend hydration must
validate its own snapshot and current parent authority before calling resume.

Owner-only files and hashes detect accidental corruption, not malicious code
executing as the owner. Storage failures seal this instance. No checkpoint is
repaired by guessing away a truncated record or an uncertain action.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from uuid import uuid4

from .tool_artifacts import ArtifactError, ArtifactStore, _key, _private, _signature

MAX_LEDGER_BYTES = 16 * 1024 * 1024
MAX_RECORD_BYTES = 64 * 1024
MAX_BINDING_BYTES = 16 * 1024
MAX_MESSAGES_BYTES = 32 * 1024
MAX_MESSAGE_CHARS = 8192
_ZERO = '0' * 64
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_RUN = re.compile(r'[0-9a-f]{32}\Z')
_FIELDS = {'scope', 'binding', 'snapshot_receipt', 'requests', 'max_rounds',
           'deadline_ns', 'created_ns', 'last_clock_ns', 'control', 'messages',
           'pending', 'outcome', 'completion_seq', 'acknowledged'}


def protected_write(workspace, raw, *, recursive=False):
    """Dedicated file-tool guard; not containment against same-user shell.

    Check lexical and resolved names, so an alias cannot hide a checkpoint.
    Recursive deletion/copy/move also refuses an existing authority ancestor.
    Normal reports directly under .dream remain ordinary workspace files.
    """
    try:
        joined = Path(workspace) / Path(raw).expanduser()
        # Resolve the original spelling: normalizing '..' first changes its
        # meaning when the preceding component is a symbolic link.
        candidate = Path(os.path.abspath(joined))
        locations = (candidate, joined.resolve())
        roots = (Path(workspace).absolute(), Path(workspace).resolve())
        for location in locations:
            parts = tuple(part.casefold() for part in location.parts)
            if any(parts[i] == '.dream' and parts[i + 1] in ('worker-checkpoints', 'tool-artifacts')
                   for i in range(len(parts) - 1)):
                return True
            if recursive:
                for root in (*roots, location):
                    for namespace in ('worker-checkpoints', 'tool-artifacts'):
                        authority = root / '.dream' / namespace
                        if authority.is_relative_to(location) and authority.exists():
                            return True
        if recursive:
            # A recursive operation can touch a nested project's authority even
            # when its top-level path is ordinary. Bound traversal and refuse
            # unknown/cyclic trees rather than authorizing an incomplete scan.
            todo, seen, count = [(locations[-1], 0)], set(), 0
            while todo:
                path, depth = todo.pop()
                count += 1
                if count > 4096 or depth > 64:
                    return True
                if protected_write(workspace, path):
                    return True
                if path.is_dir():
                    real = path.resolve()
                    if real in seen:
                        return True
                    seen.add(real)
                    with os.scandir(path) as entries:
                        for entry in entries:
                            count += 1
                            if count > 4096:
                                return True
                            todo.append((Path(entry.path), depth + 1))
        return False
    except (OSError, ValueError, TypeError, RuntimeError):
        return True


def protected_copy(workspace, source, destination, *, move=False):
    """Validate every effective copy destination before any batch mutation."""
    try:
        src, dest = Path(source), Path(destination)
        if dest.is_dir() and (move or not src.is_dir()):
            dest = dest / src.name
        todo, count = [(src, dest, 0)], 0
        while todo:
            current, target, depth = todo.pop()
            count += 1
            if (count > 4096 or depth > 64 or protected_write(workspace, current)
                    or protected_write(workspace, target)):
                return True
            # Moving a tree preserves relative links, whose effective targets
            # can change after relocation and invalidate another batch item's
            # preflight. Dedicated tools refuse these ambiguous move plans.
            if move and current.is_symlink():
                return True
            if current.is_dir():
                with os.scandir(current) as entries:
                    for entry in entries:
                        count += 1
                        if count > 4096:
                            return True
                        todo.append((Path(entry.path), target / entry.name, depth + 1))
        return False
    except (OSError, ValueError, TypeError, RuntimeError):
        return True


class WorkerCheckpointError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise WorkerCheckpointError('invalid_state')
    return value


def _plain(value):
    # JSON does not preserve tuple/custom mapping identity. Refuse rather than
    # silently changing a host snapshot or accepting nonfinite budgets.
    todo = [(value, 0)]
    while todo:
        item, depth = todo.pop()
        if depth > 128:
            raise WorkerCheckpointError('invalid_json')
        if type(item) is dict:
            if not all(type(k) is str for k in item):
                raise WorkerCheckpointError('invalid_json')
            todo.extend((v, depth + 1) for v in item.values())
        elif type(item) is list:
            todo.extend((v, depth + 1) for v in item)
        elif type(item) is float:
            if not math.isfinite(item):
                raise WorkerCheckpointError('invalid_json')
        elif type(item) not in (str, int, bool, type(None)):
            raise WorkerCheckpointError('invalid_json')


def _encode(value):
    try:
        _plain(value)
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise WorkerCheckpointError('invalid_json') from None


def _decode(raw):
    def pairs(items):
        row = {}
        for key, value in items:
            if key in row:
                raise WorkerCheckpointError('invalid_json')
            row[key] = value
        return row

    def invalid(_):
        raise WorkerCheckpointError('invalid_json')
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
        _plain(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise WorkerCheckpointError('invalid_json') from None


class WorkerJournal:
    def __init__(self, workspace, session_id, run_id, *, create=False):
        if not isinstance(run_id, str) or _RUN.fullmatch(run_id) is None:
            raise WorkerCheckpointError('invalid_identity')
        try:
            self.store = ArtifactStore(workspace, session_id, run_id)
        except ArtifactError as exc:
            raise WorkerCheckpointError(exc.code) from None
        self.run_id, self._create = run_id, create
        self.path = Path(self.store.workspace) / '.dream' / 'worker-checkpoints' / 'v1' / self.store.session_key / run_id
        self._folder_fd = self._lock_fd = self._ledger_fd = None
        self._state = None
        self._seq, self._chain = 0, _ZERO
        self._ledger_bytes = 0
        self._pause_started_ns = None  # reconstructed only from an explicit pause record
        self._failed = False
        self._scope = {'workspace_key': self.store.workspace_key,
                       'session_key': self.store.session_key, 'run_id': run_id}

    @contextmanager
    def _folder(self, create=False):
        try:
            with self.store._root() as root:
                top = os.fstat(root)
                _private(top, directory=True, forbidden=0o022, fd=root)
                if self.store.workspace_key != _key(f'{self.store.workspace}\0{top.st_dev}\0{top.st_ino}'):
                    raise WorkerCheckpointError('scope')
                fd = os.dup(root)
            try:
                parts = ('.dream', 'worker-checkpoints', 'v1', self.store.session_key, self.run_id)
                for index, part in enumerate(parts):
                    if create:
                        try:
                            os.mkdir(part, 0o700, dir_fd=fd)
                        except FileExistsError:
                            pass
                        else:
                            os.fsync(fd)
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                    os.close(fd)
                    fd = child
                    _private(os.fstat(fd), directory=True, forbidden=0o022 if index == 0 else 0o077, fd=fd)
                yield fd
            finally:
                os.close(fd)
        except WorkerCheckpointError:
            raise
        except ArtifactError as exc:
            raise WorkerCheckpointError(exc.code) from None
        except FileNotFoundError:
            raise WorkerCheckpointError('missing') from None
        except OSError:
            raise WorkerCheckpointError('storage_io') from None

    def __enter__(self):
        if self._folder_fd is not None:
            raise WorkerCheckpointError('locked')
        try:
            with self._folder(create=self._create) as folder:
                self._folder_fd = os.dup(folder)
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
            if self._create:
                flags |= os.O_CREAT
            self._lock_fd = os.open('.lock', flags, 0o600, dir_fd=self._folder_fd)
            _private(os.fstat(self._lock_fd))
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise WorkerCheckpointError('locked') from None
            self._ledger_fd = os.open('ledger.jsonl', flags | os.O_APPEND, 0o600, dir_fd=self._folder_fd)
            _private(os.fstat(self._ledger_fd))
            self._check_paths()
            self._load()
            return self
        except BaseException as exc:
            self.close()
            if isinstance(exc, WorkerCheckpointError):
                raise
            if isinstance(exc, ArtifactError):
                raise WorkerCheckpointError(exc.code) from None
            if isinstance(exc, OSError):
                raise WorkerCheckpointError('storage_io') from None
            raise

    def __exit__(self, *args):
        self.close()

    def close(self):
        for name in ('_ledger_fd', '_lock_fd', '_folder_fd'):
            fd = getattr(self, name)
            if fd is not None:
                os.close(fd)
                setattr(self, name, None)

    def _check_paths(self):
        if self._folder_fd is None or self._failed:
            raise WorkerCheckpointError('storage_failed')
        try:
            with self._folder() as current:
                a, b = os.fstat(current), os.fstat(self._folder_fd)
                if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
                    raise WorkerCheckpointError('scope')
            for name, fd in (('.lock', self._lock_fd), ('ledger.jsonl', self._ledger_fd)):
                opened = os.fstat(fd)
                _private(opened)
                named = os.stat(name, dir_fd=self._folder_fd, follow_symlinks=False)
                if _signature(opened) != _signature(named):
                    raise WorkerCheckpointError('integrity')
        except (OSError, ArtifactError, WorkerCheckpointError) as exc:
            self._failed = True
            raise WorkerCheckpointError(getattr(exc, 'code', 'storage_io')) from None

    def _validate(self, state, seq):
        if type(state) is not dict or set(state) != _FIELDS or state['scope'] != self._scope:
            raise WorkerCheckpointError('scope')
        binding = state['binding']
        if type(binding) is not dict or not binding or len(_encode(binding)) > MAX_BINDING_BYTES:
            raise WorkerCheckpointError('invalid_state')
        receipt = state['snapshot_receipt']
        if (type(receipt) is not dict or self.store._receipt(receipt.get('handle')).metadata() != receipt):
            raise WorkerCheckpointError('invalid_state')
        for name in ('requests', 'max_rounds', 'deadline_ns', 'created_ns', 'last_clock_ns'):
            _integer(state[name], 1 if name != 'requests' else 0)
        if (not state['created_ns'] <= state['last_clock_ns'] < state['deadline_ns']
                or state['requests'] > state['max_rounds']
                or state['control'] not in ('running', 'paused', 'stopped')
                or state['outcome'] not in (None, 'completed', 'incomplete')
                or type(state['acknowledged']) is not bool):
            raise WorkerCheckpointError('invalid_state')
        completion = state['completion_seq']
        if ((state['outcome'] is None) != (completion is None)
                or completion is not None and (type(completion) is not int or not 1 <= completion <= seq)
                or state['acknowledged'] and state['outcome'] is None):
            raise WorkerCheckpointError('invalid_state')
        messages = state['messages']
        if (type(messages) is not list or len(messages) > 32 or len(_encode(messages)) > MAX_MESSAGES_BYTES):
            raise WorkerCheckpointError('invalid_state')
        if state['outcome'] == 'completed' and messages:
            raise WorkerCheckpointError('invalid_state')
        seen = set()
        for message in messages:
            if (type(message) is not dict or set(message) != {'id', 'text'}
                    or not isinstance(message['id'], str) or _RUN.fullmatch(message['id']) is None
                    or message['id'] in seen or not isinstance(message['text'], str)
                    or not message['text'].strip() or len(message['text']) > MAX_MESSAGE_CHARS):
                raise WorkerCheckpointError('invalid_state')
            seen.add(message['id'])
        pending = state['pending']
        if pending is not None:
            if (type(pending) is not dict or set(pending) != {'request', 'tools'}
                    or pending['request'] != state['requests'] or type(pending['tools']) is not dict
                    or len(pending['tools']) > 128 or state['outcome'] == 'completed'):
                raise WorkerCheckpointError('invalid_state')
            for identity, action in pending['tools'].items():
                if (not isinstance(identity, str) or not 1 <= len(identity) <= 200
                        or type(action) is not dict or set(action) != {'state', 'sha256'}
                        or action['state'] not in ('intent', 'returned')
                        or (action['state'] == 'intent' and action['sha256'] is not None)
                        or (action['state'] == 'returned' and (not isinstance(action['sha256'], str)
                            or _HEX.fullmatch(action['sha256']) is None))):
                    raise WorkerCheckpointError('invalid_state')

    def _load(self):
        info = os.fstat(self._ledger_fd)
        if info.st_size > MAX_LEDGER_BYTES:
            raise WorkerCheckpointError('ledger_limit')
        before = _signature(info)
        os.lseek(self._ledger_fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(self._ledger_fd), 'rb') as stream:
            state, seq, chain, total, pause_started = None, 0, _ZERO, 0, None
            while raw := stream.readline(MAX_RECORD_BYTES + 1):
                total += len(raw)
                if total > MAX_LEDGER_BYTES:
                    raise WorkerCheckpointError('ledger_limit')
                if len(raw) > MAX_RECORD_BYTES or not raw.endswith(b'\n'):
                    raise WorkerCheckpointError('corrupt')
                row = _decode(raw)
                if (type(row) is not dict or set(row) != {'kind', 'version', 'seq', 'previous', 'event', 'state', 'sha256'}
                        or row['kind'] != 'dream.worker_checkpoint' or type(row['version']) is not int or row['version'] != 1
                        or type(row['seq']) is not int or row['seq'] != seq + 1 or row['previous'] != chain
                        or row['event'] not in {'created', 'request', 'tool_intent', 'tool_outcome', 'settled',
                                               'message', 'control', 'paused', 'resumed', 'checkpointed',
                                               'finished', 'acknowledged'}):
                    raise WorkerCheckpointError('corrupt')
                digest = row.pop('sha256')
                if digest != hashlib.sha256(_encode(row)).hexdigest():
                    raise WorkerCheckpointError('corrupt')
                self._validate(row['state'], row['seq'])
                new = row['state']
                if state is not None:
                    immutable = ('scope', 'binding', 'max_rounds', 'created_ns')
                    if (any(new[k] != state[k] for k in immutable)
                            or not state['requests'] <= new['requests'] <= state['requests'] + 1
                            or new['last_clock_ns'] < state['last_clock_ns']
                            or state['control'] == 'stopped' and new['control'] != 'stopped'
                            or state['outcome'] is not None and any(new[k] != state[k] for k in _FIELDS - {'acknowledged'})
                            or state['acknowledged'] and not new['acknowledged']):
                        raise WorkerCheckpointError('corrupt')
                    if row['event'] == 'paused':
                        if (state['control'] != 'running' or new['control'] != 'paused'
                                or any(new[k] != state[k] for k in _FIELDS - {'control', 'last_clock_ns'})):
                            raise WorkerCheckpointError('corrupt')
                    if row['event'] == 'checkpointed' and any(
                            new[k] != state[k] for k in _FIELDS - {'snapshot_receipt'}):
                        raise WorkerCheckpointError('corrupt')
                    if row['event'] == 'resumed':
                        if (pause_started is None or state['control'] != 'paused' or new['control'] != 'running'
                                or state['last_clock_ns'] != pause_started
                                or new['deadline_ns'] != state['deadline_ns'] + new['last_clock_ns'] - pause_started
                                or any(new[k] != state[k] for k in _FIELDS - {'control', 'last_clock_ns', 'deadline_ns'})):
                            raise WorkerCheckpointError('corrupt')
                    elif new['deadline_ns'] != state['deadline_ns']:
                        raise WorkerCheckpointError('corrupt')
                elif row['event'] != 'created' or new['requests'] or new['pending'] or new['outcome']:
                    raise WorkerCheckpointError('corrupt')
                if row['event'] == 'paused':
                    pause_started = new['last_clock_ns']
                elif new['control'] != 'paused' or new['outcome'] is not None:
                    pause_started = None
                state, seq, chain = new, row['seq'], digest
        if _signature(os.fstat(self._ledger_fd)) != before:
            raise WorkerCheckpointError('integrity')
        self._state, self._seq, self._chain = state, seq, chain
        self._ledger_bytes = info.st_size
        self._pause_started_ns = pause_started

    def _append(self, event, state):
        try:
            self._check_paths()
            if os.fstat(self._ledger_fd).st_size != self._ledger_bytes:
                raise WorkerCheckpointError('integrity')
            self._validate(state, self._seq + 1)
            row = {'kind': 'dream.worker_checkpoint', 'version': 1, 'seq': self._seq + 1,
                   'previous': self._chain, 'event': event, 'state': state}
            row['sha256'] = hashlib.sha256(_encode(row)).hexdigest()
            raw = _encode(row) + b'\n'
            if len(raw) > MAX_RECORD_BYTES or os.fstat(self._ledger_fd).st_size + len(raw) > MAX_LEDGER_BYTES:
                raise WorkerCheckpointError('ledger_limit')
            offset = 0
            while offset < len(raw):
                size = os.write(self._ledger_fd, raw[offset:])
                if size <= 0:
                    raise WorkerCheckpointError('storage_io')
                offset += size
            os.fsync(self._ledger_fd)
            os.fsync(self._folder_fd)
            self._state, self._seq, self._chain = deepcopy(state), row['seq'], row['sha256']
            self._ledger_bytes += len(raw)
            if event == 'paused':
                self._pause_started_ns = state['last_clock_ns']
            elif state['control'] != 'paused' or state['outcome'] is not None:
                self._pause_started_ns = None
        except BaseException as exc:
            self._failed = True
            if isinstance(exc, WorkerCheckpointError):
                raise
            if isinstance(exc, (OSError, ArtifactError)):
                raise WorkerCheckpointError(getattr(exc, 'code', 'storage_io')) from None
            raise

    def _snapshot(self, snapshot):
        if type(snapshot) is not dict:
            raise WorkerCheckpointError('invalid_state')
        raw = _encode(snapshot)
        try:
            return self.store.save(raw.decode('utf-8'), f'worker-checkpoint-{self._seq + 1}').metadata()
        except ArtifactError as exc:
            self._failed = True
            raise WorkerCheckpointError(exc.code) from None

    def create(self, binding, snapshot, *, deadline_ns, max_rounds):
        self._check_paths()
        if self._state is not None:
            raise WorkerCheckpointError('existing')
        now = time.time_ns()
        _integer(deadline_ns, 1)
        _integer(max_rounds, 1)
        if deadline_ns <= now:
            raise WorkerCheckpointError('expired')
        if type(binding) is not dict or not binding or len(_encode(binding)) > MAX_BINDING_BYTES:
            raise WorkerCheckpointError('invalid_state')
        state = dict(scope=self._scope, binding=deepcopy(binding), snapshot_receipt=self._snapshot(snapshot),
                     requests=0, max_rounds=max_rounds, deadline_ns=deadline_ns,
                     created_ns=now, last_clock_ns=now, control='running', messages=[], pending=None,
                     outcome=None, completion_seq=None, acknowledged=False)
        self._append('created', state)

    def inspect(self):
        self._check_paths()
        if self._state is None:
            raise WorkerCheckpointError('missing')
        state = deepcopy(self._state)
        try:
            state['snapshot'] = _decode(self.store.load_original(state['snapshot_receipt']['handle']))
        except ArtifactError as exc:
            self._failed = True
            raise WorkerCheckpointError(exc.code) from None
        if type(state['snapshot']) is not dict:
            self._failed = True
            raise WorkerCheckpointError('invalid_state')
        return state

    def _admit(self, *, now_ns=None):
        self._check_paths()
        if self._state is None:
            raise WorkerCheckpointError('missing')
        state = deepcopy(self._state)
        if state['outcome'] is not None:
            raise WorkerCheckpointError('terminal')
        if state['control'] != 'running':
            raise WorkerCheckpointError('stopped' if state['control'] == 'stopped' else 'paused')
        now = time.time_ns() if now_ns is None else _integer(now_ns, 1)
        if now < state['last_clock_ns']:
            raise WorkerCheckpointError('clock_backwards')
        if now >= state['deadline_ns']:
            raise WorkerCheckpointError('expired')
        state['last_clock_ns'] = now
        return state

    def resume(self, binding, *, now_ns):
        state = self._admit(now_ns=now_ns)
        if _encode(binding) != _encode(state['binding']):
            raise WorkerCheckpointError('binding_mismatch')
        if state['pending'] is not None:
            raise WorkerCheckpointError('unsettled')
        if state['requests'] >= state['max_rounds']:
            raise WorkerCheckpointError('round_limit')
        # Verify the original before acknowledging this admission. The clock
        # record prevents a subsequent reopen from renewing observed time.
        loaded = self.inspect()
        self._append('control', state)
        loaded['last_clock_ns'] = state['last_clock_ns']
        return loaded

    def request_started(self, *, now_ns):
        state = self._admit(now_ns=now_ns)
        if state['pending'] is not None:
            raise WorkerCheckpointError('unsettled')
        if state['requests'] >= state['max_rounds']:
            raise WorkerCheckpointError('round_limit')
        state['requests'] += 1
        state['pending'] = {'request': state['requests'], 'tools': {}}
        self._append('request', state)
        return state['requests']

    def tool_started(self, identity):
        state = self._admit()
        if (not isinstance(identity, str) or not 1 <= len(identity) <= 200
                or state['pending'] is None or identity in state['pending']['tools']):
            raise WorkerCheckpointError('invalid_state')
        state['pending']['tools'][identity] = {'state': 'intent', 'sha256': None}
        self._append('tool_intent', state)

    def tool_finished(self, identity, sha256):
        # Cleanup may record an outcome after Stop/deadline; it cannot admit
        # further work or erase the Stop. Still require a durable open intent.
        self._check_paths()
        state = deepcopy(self._state)
        if state is not None and state['outcome'] is not None:
            raise WorkerCheckpointError('terminal')
        pending = state['pending'] if state is not None else None
        if (pending is None or identity not in pending['tools']
                or pending['tools'][identity]['state'] != 'intent'
                or not isinstance(sha256, str) or _HEX.fullmatch(sha256) is None):
            raise WorkerCheckpointError('invalid_state')
        pending['tools'][identity] = {'state': 'returned', 'sha256': sha256}
        self._append('tool_outcome', state)

    def settle(self, snapshot, *, consumed_messages=()):
        state = self._admit()
        if state['pending'] is not None and any(a['state'] == 'intent' for a in state['pending']['tools'].values()):
            raise WorkerCheckpointError('unsettled')
        if (type(consumed_messages) not in (tuple, list) or len(set(consumed_messages)) != len(consumed_messages)
                or not set(consumed_messages) <= {m['id'] for m in state['messages']}):
            raise WorkerCheckpointError('invalid_state')
        state['snapshot_receipt'] = self._snapshot(snapshot)
        state['messages'] = [m for m in state['messages'] if m['id'] not in consumed_messages]
        state['pending'] = None
        self._append('settled', state)

    def checkpoint(self, snapshot):
        """Persist observations/controls without clearing an unfinished request."""
        self._check_paths()
        if self._state is None:
            raise WorkerCheckpointError('missing')
        state = deepcopy(self._state)
        if state['outcome'] is not None:
            raise WorkerCheckpointError('terminal')
        state['snapshot_receipt'] = self._snapshot(snapshot)
        self._append('checkpointed', state)

    def message(self, text):
        self._check_paths()
        state = deepcopy(self._state)
        if state is None:
            raise WorkerCheckpointError('missing')
        if state['outcome'] is not None or state['control'] == 'stopped':
            raise WorkerCheckpointError('terminal' if state['outcome'] is not None else 'stopped')
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_MESSAGE_CHARS:
            raise WorkerCheckpointError('invalid_state')
        identity = uuid4().hex
        state['messages'].append({'id': identity, 'text': text})
        if len(state['messages']) > 32 or len(_encode(state['messages'])) > MAX_MESSAGES_BYTES:
            raise WorkerCheckpointError('message_limit')
        self._append('message', state)
        return identity

    def control(self, control):
        """Credit only a recorded explicit pause; never renew work or request debt."""
        self._check_paths()
        state = deepcopy(self._state)
        if state is None or control not in ('running', 'paused', 'stopped'):
            raise WorkerCheckpointError('invalid_state')
        if state['outcome'] is not None:
            raise WorkerCheckpointError('terminal')
        if state['control'] == 'stopped' and control != 'stopped':
            raise WorkerCheckpointError('stopped')
        event = 'control'
        if control != 'stopped':
            now = _integer(time.time_ns(), 1)
            if now < state['last_clock_ns']:
                raise WorkerCheckpointError('clock_backwards')
            if state['control'] == 'running':
                state = self._admit(now_ns=now)
                if control == 'paused':
                    event = 'paused'
            elif control == 'running':
                if self._pause_started_ns is not None:
                    state['deadline_ns'] += now - self._pause_started_ns
                    event = 'resumed'
                elif now >= state['deadline_ns']:
                    # Historical generic controls did not observe a pause start.
                    # Preserve their absolute expiry instead of guessing credit.
                    raise WorkerCheckpointError('expired')
                state['last_clock_ns'] = now
            else:
                return  # a repeated pause cannot move the recorded start
        state['control'] = control
        self._append(event, state)

    def finish(self, outcome, snapshot):
        self._check_paths()
        state = deepcopy(self._state)
        if state is None or outcome not in ('completed', 'incomplete'):
            raise WorkerCheckpointError('invalid_state')
        if state['outcome'] is not None:
            raise WorkerCheckpointError('terminal')
        if outcome == 'completed':
            state = self._admit()
            if state['messages']:
                raise WorkerCheckpointError('pending_messages')
            if state['pending'] is not None and any(a['state'] == 'intent' for a in state['pending']['tools'].values()):
                raise WorkerCheckpointError('unsettled')
            state['pending'] = None
        state['snapshot_receipt'] = self._snapshot(snapshot)
        state['outcome'], state['completion_seq'] = outcome, self._seq + 1
        self._append('finished', state)
        return state['completion_seq']

    def acknowledge(self, seq):
        self._check_paths()
        state = deepcopy(self._state)
        if (state is None or state['outcome'] is None or type(seq) is not int
                or seq != state['completion_seq']):
            raise WorkerCheckpointError('invalid_ack')
        if not state['acknowledged']:
            state['acknowledged'] = True
            self._append('acknowledged', state)
        return seq
