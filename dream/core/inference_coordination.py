"""Per-user local endpoint leases; no network, hardware probes or process signals.

The lock orders cooperating Dream clients. Persisted uncertainty is deliberate:
closing an HTTP client does not establish that its server stopped computation.
"""
from __future__ import annotations

import asyncio
import atexit
from contextlib import asynccontextmanager
import fcntl
import hashlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time
from urllib.parse import urlsplit
from uuid import uuid4


class CoordinationError(RuntimeError):
    pass


class StreamProtocolError(ValueError):
    """Response chunks cannot establish one consistent selected generation."""


def primary_choice(data):
    """Select choice zero consistently, including legacy single-choice chunks."""
    if not isinstance(data, dict):
        raise StreamProtocolError('Invalid response chunk object.')
    choices = data.get('choices') or []
    if not isinstance(choices, list):
        raise StreamProtocolError('Invalid response choices.')
    selected = None
    for position, choice in enumerate(choices):
        if not isinstance(choice, dict):
            raise StreamProtocolError('Invalid response choice.')
        index = choice.get('index', position)
        if type(index) is not int or index < 0:
            raise StreamProtocolError('Invalid response choice index.')
        if index == 0:
            if selected is not None:
                raise StreamProtocolError('Duplicate selected response choice.')
            selected = choice
    return selected


class CompletionStream:
    """Observe protocol completion without consuming or buffering ahead of the UI."""
    def __init__(self, response):
        self.response = response
        self.complete = False
        self.finish_reason = None

    def __getattr__(self, name):
        return getattr(self.response, name)

    async def aiter_lines(self):
        async for line in self.response.aiter_lines():
            if line.startswith('data:'):
                value = line[5:].strip()
                if value == '[DONE]':
                    self.complete = True
                else:
                    try:
                        data = json.loads(value)
                    except ValueError as exc:
                        raise StreamProtocolError('Invalid JSON response chunk.') from exc
                    choice = primary_choice(data)
                    self.complete = self.complete or bool(data.get('error'))
                    if choice is not None:
                        reason = choice.get('finish_reason')
                        delta = choice.get('delta') or {}
                        if not isinstance(delta, dict) or (reason is not None and
                                (not isinstance(reason, str) or not reason)):
                            raise StreamProtocolError('Invalid selected response state.')
                        generated = any(delta.get(key) for key in
                                        ('content', 'reasoning_content', 'tool_calls', 'function_call'))
                        if self.finish_reason is not None and (generated or
                                (reason is not None and reason != self.finish_reason)):
                            raise StreamProtocolError('Response changed after its selected generation finished; no collected tool calls ran.')
                        if reason is not None:
                            self.finish_reason = reason
                            self.complete = True
            yield line


def local_endpoint(url: str) -> str | None:
    """Canonical loopback listener identity, excluding credentials and API paths."""
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower().rstrip('.')
        local = host == 'localhost' or host.endswith('.localhost')
        try:
            local = local or ipaddress.ip_address(host).is_loopback or host == '0.0.0.0'
        except ValueError:
            pass
        if parsed.scheme not in {'http', 'https'} or not local:
            return None
        return f"loopback:{parsed.port or (443 if parsed.scheme == 'https' else 80)}"
    except (ValueError, TypeError):
        return None


# DREAM-151: the most slots one lease opens (MachX's --parallel is 1-16, the engine's kMaxParallel; DREAM-201: Dream's
# own setting now takes the whole range too). A slot count that is not an integer from 1 to this is read as 1: one
# request at a time, as before.
MAX_SLOTS = 16
_slots_warned: set[int] = set()


def lease_slots(reported) -> int:
    """How many slots a server's reported count opens (DREAM-151's rule): the count when it is an integer from 1 to
    MAX_SLOTS, else 1. DREAM-201 gate follow-up: a count above MAX_SLOTS -- an engine Dream does not know, or a
    foreign server -- still reads as 1 (one request at a time is the behaviour every rule here was built for), but
    it is said once per process, as a logging warning (on stderr: Dream installs no log handler), rather than
    silently."""
    if type(reported) is int and 1 <= reported <= MAX_SLOTS:
        return reported
    if type(reported) is int and reported > MAX_SLOTS and reported not in _slots_warned:
        _slots_warned.add(reported)
        logging.getLogger(__name__).warning("the engine reports %d slots, more than Dream's %d; using 1 (one request "
                                            "at a time)", reported, MAX_SLOTS)
    return 1

# DREAM-205: where the leases live. The live Dream's folder is /tmp/dream-inference-<uid>; a test that took a lease
# there read and rewrote the records of the Dream running on this computer.
LEASE_DIR_ENV = 'DREAM_INFERENCE_LEASE_DIR'
_pytest_lease_dir: Path | None = None
_per_process_paths: set[Path] = set()      # the per-process folders this process resolved: _make_folder makes them


def lease_root() -> Path:
    """The directory this process keeps its inference leases in (DREAM-205). `DREAM_INFERENCE_LEASE_DIR`, when set,
    names it (an absolute path; it is opened with the same rules as the default one: owned by this user, mode 0700,
    no symlink). Otherwise `/tmp/dream-inference-<uid>` -- except in a test run (`PYTEST_CURRENT_TEST` or
    `PYTEST_VERSION` in the environment), where it is a private folder of this process under the system temp
    directory, made on its first use and removed on a normal exit, so that no test can read or change the leases of a
    Dream running on this computer."""
    override = os.environ.get(LEASE_DIR_ENV)
    if override:
        if not os.path.isabs(override):
            raise CoordinationError(f'{LEASE_DIR_ENV} must be an absolute path.')
        return Path(override)
    if _under_pytest():
        return _pytest_lease_root()
    return Path('/tmp') / f'dream-inference-{os.getuid()}'


def _under_pytest() -> bool:
    """Whether this process is a test run: PYTEST_CURRENT_TEST (set only while a test runs: not at import time, not in
    a thread that outlives its test) or PYTEST_VERSION (set by pytest for its whole session) in the environment; a
    child process started with the test's environment inherits both. The pytest module being loaded is no sign (DREAM-205
    gate): Dream runs custom and plugin tools in its own process, and a tool may import pytest. A child process a test
    spawns with a scrubbed environment has neither, and must be given DREAM_INFERENCE_LEASE_DIR explicitly."""
    return bool(os.environ.get('PYTEST_CURRENT_TEST') or os.environ.get('PYTEST_VERSION'))


def _pytest_lease_root() -> Path:
    global _pytest_lease_dir
    if _pytest_lease_dir is None:
        _pytest_lease_dir = _per_process_folder('dream-inference')
        logging.getLogger(__name__).warning('inference leases: under pytest without %s, this process uses %s',
                                            LEASE_DIR_ENV, _pytest_lease_dir)
    return _pytest_lease_dir


def _per_process_folder(prefix: str) -> Path:
    """<tempdir>/<prefix>-<uid>-pytest-<pid>-<start>-<pid namespace>: a test process's private folder (the leases',
    DREAM-205; the launch lock's, DREAM-206), resolved here and made on its first use (_make_folder). <start> is this
    process's start time, so a later process given the same pid never takes a folder an earlier one left (a killed
    run's, holding a "running" record). <pid namespace> keeps apart processes started together in separate PID
    namespaces, which share the pid and often the start tick (DREAM-206 gate, round 3). Both come from /proc; without
    it a ValueError names the variables to set -- never a name by the pid alone. On every call, before the path is
    handed out, it is checked to be exactly that name under the system temp directory. DREAM-206 gate: the removal
    used to be registered when the path was computed, so a planted folder was refused and then removed anyway, and a
    broken path rule would have removed whatever it named."""
    try:
        identity = _process_identity()
    except OSError as exc:
        raise ValueError(f'This test process cannot read /proc ({exc}), so it has no private folder of its own for its '
                         f'inference leases and model launch lock: set {LEASE_DIR_ENV} and DREAM_MACHX_LOAD_LOCK_DIR '
                         'to absolute paths.') from exc
    path = Path(tempfile.gettempdir()) / f'{prefix}-{os.getuid()}-pytest-{os.getpid()}-{identity}'
    if (path.name != f'{prefix}-{os.getuid()}-pytest-{os.getpid()}-{identity}'
            or path.parent != Path(tempfile.gettempdir())):
        raise RuntimeError(f'{path} is not this test process\'s own folder; it is not used')
    _per_process_paths.add(path)
    return path


def _process_identity() -> str:
    """<start>-<pid namespace>, from /proc: this process's start time (/proc/self/stat field 22, clock ticks after boot)
    and its PID namespace (the inode of /proc/self/ns/pid). With the pid, they name one process."""
    with open('/proc/self/stat') as stat_file:
        start = stat_file.read().rsplit(')', 1)[1].split()[19]
    return f'{start}-{os.stat("/proc/self/ns/pid").st_ino}'


def _make_folder(path: Path) -> None:
    """A lease or launch-lock folder at its use: made 0700 where it is missing. A per-process folder is made here, on its
    first use, not when it is resolved (DREAM-206 gate, round 3: a child killed before its first lease left an empty
    folder for good), by this process's own mkdir, and only then is its removal at a normal exit registered: of the
    folder it made (its device and inode), and only in this process, not in a forked child that inherits the handler.
    One already there was made at an earlier use, its removal registered then, or by someone else: it is never removed
    here, and the caller's checks refuse it unless it is this user's private folder."""
    if path not in _per_process_paths:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        return
    made = os.lstat(path)
    atexit.register(_remove_made_folder, path, os.getpid(), made.st_dev, made.st_ino)


def _remove_made_folder(path: Path, maker: int, dev: int, ino: int) -> None:
    """The exit removal of a per-process folder: only in the process that made it, and only while `path` is a directory
    with the device and inode recorded at mkdir (not a link, nor a folder put there while the made one still exists).
    Known limit (DREAM-206 gate, round 3): if the made folder is deleted mid-run and another is made at this exact name,
    the file system may give it the freed inode, and it is removed too. That takes deleting this process's folder and
    making one under its pid, start time and namespace; nothing does."""
    if os.getpid() != maker:
        return
    try:
        info = os.lstat(path)
    except OSError:
        return
    if stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == (dev, ino):
        shutil.rmtree(path, ignore_errors=True)


def owner_alive(record) -> bool:
    """Whether the Dream process a record names still runs (a signal-0 probe; another user's process counts as alive)."""
    pid = record.get('pid')
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def left_over(record) -> bool:
    """A record no live Dream request stands behind (DREAM-151 names it for a slot): a cut-off request's
    ("uncertain"), or "running" for an owner that is gone."""
    return record.get('state') == 'uncertain' or (record.get('state') == 'running' and not owner_alive(record))


class EndpointCoordinator:
    def __init__(self, endpoint: str, *, root: Path | None = None, server_started_at=None, model: str | None = None,
                 slots: int = 1):
        self.root = Path(root) if root is not None else lease_root()        # DREAM-205: the resolver's folder
        # DREAM-144: one lease per (endpoint, model). A supervised layout serves several models on one endpoint,
        # each on its own cards with its own queue, so a request to another model need not wait for this one;
        # requests to the same model still run one at a time. Without a model the key is the endpoint's alone.
        self.model = model
        self.key = hashlib.sha256((endpoint if model is None else f'{endpoint}\nmodel={model}').encode()).hexdigest()
        # DREAM-151: how many requests for this (endpoint, model) may run at once -- the server's own word (MachX's
        # /props total_slots, which the HTTP backend sets before each request). Slot 0 is the lease every earlier
        # version used, byte for byte (<key>.lock, <key>.json); slot n > 0 adds <key>-n.lock and <key>-n.json.
        self.slots = slots
        self.waiting = False
        # Fix #53: a callable returning the serving process's start time (epoch seconds), or None
        # when unknown. A lease left "running" by a DEAD owner for a server that started AFTER the
        # lease cannot describe anything in flight; it is reconciled here instead of refusing the
        # owner's first prompt (2026-09-22 01:36).
        self.server_started_at = server_started_at

    def _capacity(self) -> int:
        return lease_slots(self.slots)

    @property
    def capacity(self) -> int:
        """How many slots this lease opens now (DREAM-151): `slots` when it is an integer from 1 to MAX_SLOTS, else 1."""
        return self._capacity()

    def _slot_key(self, slot: int) -> str:
        return self.key if slot == 0 else f'{self.key}-{slot}'

    def _stale_lease(self, record) -> bool:
        pid = record.get('pid')
        started = record.get('updated_at') or record.get('started_at')
        if not isinstance(pid, int) or not isinstance(started, (int, float)) or not callable(self.server_started_at):
            return False
        try:
            os.kill(pid, 0)
            return False                      # the owner is alive: its request may be in flight
        except ProcessLookupError:
            pass
        except PermissionError:
            return False
        try:
            server = self.server_started_at()
        except Exception:
            return False
        return isinstance(server, (int, float)) and server > started

    def _directory(self):
        try:
            _make_folder(self.root)
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                os.close(fd)
                raise CoordinationError('Unsafe inference coordination directory ownership or permissions.')
            return fd
        except OSError as exc:
            raise CoordinationError('Cannot safely open inference coordination state.') from exc

    def _lock(self, directory, key=None):
        fd = os.open((key or self.key) + '.lock', os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            os.close(fd)
            raise CoordinationError('Unsafe inference coordination lock.')
        return fd

    def _read(self, directory, key=None):
        try:
            fd = os.open((key or self.key) + '.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=directory)
        except FileNotFoundError:
            return {'schema_version': 1, 'state': 'idle', 'request_id': None}
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 4096):
                raise CoordinationError('Unsafe inference coordination record.')
            raw = os.read(fd, 4097)
            record = json.loads(raw)
            if (not isinstance(record, dict) or record.get('schema_version') != 1
                    or record.get('state') not in {'idle', 'running', 'uncertain'}):
                raise ValueError('unsupported record')
            if record['state'] != 'idle' and (
                    not isinstance(record.get('request_id'), str)
                    or len(record['request_id']) != 32
                    or any(c not in '0123456789abcdef' for c in record['request_id'])):
                raise ValueError('invalid request identity')
            return record
        except (ValueError, UnicodeError) as exc:
            raise CoordinationError('Inference coordination state is damaged; inspect it before retrying.') from exc
        finally:
            os.close(fd)

    def _write(self, directory, record, key=None):
        key = key or self.key
        temporary = key + '.' + uuid4().hex + '.tmp'
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(json.dumps(record, allow_nan=False).encode())
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, key + '.json', src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass

    def records(self) -> list[dict]:
        """Every slot's record, slot 0 first (DREAM-151), read without taking any lock."""
        directory = self._directory()
        try:
            return [self._read(directory, self._slot_key(slot)) for slot in range(self._capacity())]
        finally:
            os.close(directory)

    def status(self):
        records = self.records()
        # One slot: its record, as always. Several (DREAM-151): the record Controls can act on first -- a cut-off
        # request's, then one a gone owner left running, then a live request's, else an idle one -- with the number of
        # slots, of live requests (`running`) and of records left over (`left_over`: cut off, or a gone owner's).
        ranked = sorted(records, key=lambda r: (0 if r['state'] == 'uncertain' else 1 if left_over(r)
                                                else 2 if r['state'] == 'running' else 3))
        record = ranked[0] if len(records) > 1 else records[0]
        status = {**record, 'enabled': True, 'waiting': self.waiting,
                  'can_reconcile': record['state'] in {'running', 'uncertain'}}
        if len(records) > 1:
            status.update(slots=len(records), running=sum(r['state'] == 'running' and not left_over(r) for r in records),
                          left_over=sum(left_over(r) for r in records))
        return status

    def _slot_of(self, directory, request_id) -> str:
        """The key of the slot whose record names `request_id`; slot 0's when none does (reconcile then refuses)."""
        for slot in range(1, self._capacity()):
            key = self._slot_key(slot)
            if request_id and self._read(directory, key).get('request_id') == request_id:
                return key
        return self.key

    def reconcile(self, request_id: str, *, confirmed_idle: bool):
        if confirmed_idle is not True:
            raise CoordinationError('Confirm that the server is idle before resuming local requests.')
        directory = self._directory()
        fd = None
        try:
            key = self._slot_of(directory, request_id)
            fd = self._lock(directory, key)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CoordinationError('A Dream request is still active; it cannot be reconciled yet.') from exc
            record = self._read(directory, key)
            if record['state'] not in {'uncertain', 'running'} or not request_id or record.get('request_id') != request_id:
                raise CoordinationError('Request state changed; refresh before confirming server idle.')
            self._write(directory, {'schema_version': 1, 'state': 'idle', 'request_id': None,
                                    'reconciled_at': time.time()}, key)
        finally:
            if fd is not None:
                os.close(fd)
            os.close(directory)
        return self.status()

    def _claim(self, directory, on_event):
        """Take a free slot: (its locked descriptor, its key, None). Otherwise (None, None, left_over): no slot was
        free. `left_over` is None while a live request holds a slot (waiting helps); else every slot is fenced by a
        record a cut-off request left, and it is the first of them. Per slot, today's order exactly: the lock, the
        stale-owner check (fix #53), the record."""
        left_over, busy = None, False
        for slot in range(self._capacity()):
            key = self._slot_key(slot)
            fd = self._lock(directory, key)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                busy = True
                continue
            except BaseException:
                os.close(fd)
                raise
            try:
                previous = self._read(directory, key)
                if previous['state'] != 'idle' and self._stale_lease(previous):
                    self._write(directory, {'schema_version': 1, 'state': 'idle', 'request_id': None,
                                            'reconciled_at': time.time(),
                                            'auto_reconciled': {'dead_pid': previous.get('pid'), 'was': previous['state']}},
                                key)
                    if on_event:
                        on_event({'state': 'reconciled', 'message': 'A previous Dream request was left running by a '
                                  'process that is gone, and the engine has restarted since; the lease was cleared.'})
                    previous = self._read(directory, key)
            except BaseException:
                os.close(fd)
                raise
            if previous['state'] == 'idle':
                return fd, key, None
            os.close(fd)
            left_over = left_over or previous
        return None, None, None if busy else left_over

    @asynccontextmanager
    async def request(self, *, timeout=120.0, on_event=None):
        directory = self._directory()
        fd = None
        started = time.monotonic()
        announced = False       # each waiting request says so once; `waiting` is the coordinator's, for status
        try:
            while True:
                fd, key, previous = self._claim(directory, on_event)
                if fd is not None:
                    break
                if previous is not None:
                    # Every slot is fenced by a cut-off request (one slot: today's refusal). Say WHEN and WHY, and
                    # name the one action that clears it. Twice on 2026-09-20 the owner read the bare refusal as
                    # "the harness hung" -- the turn had in fact been cut off mid-stream hours earlier and every
                    # request since was refused in a quarter of a second.
                    # This module never probes the server. The HTTP backend clears such a record
                    # itself (DREAM-110, openai_compat._enter_lease) once the engine's /health says
                    # status "ok", inflight 0 and queued 0, through reconcile() above: ie serve frees
                    # inflight only after the generation returns, and 09-19's "inflight=0 while
                    # burning nine cores" predates the engine's 09-21 OpenMP block-time fix.
                    when = previous.get('updated_at') or previous.get('started_at')
                    ago = ''
                    if isinstance(when, (int, float)):
                        mins = max(0, int((time.time() - when) // 60))
                        ago = f' {mins} min ago' if mins else ' just now'
                    raise CoordinationError(
                        f'Previous local request outcome is uncertain: it was cut off{ago} and the server was '
                        'never confirmed idle, so this request was not sent. Open Controls and confirm the '
                        'server is idle to clear it.')
                if not announced and on_event:
                    on_event({'state': 'waiting', 'message': 'Waiting for another Dream request on this local engine.'})
                announced = self.waiting = True
                if time.monotonic() - started >= timeout:
                    raise CoordinationError('Timed out waiting for another Dream request; no new request was sent.')
                await asyncio.sleep(.05)
            self.waiting = False
            record = {'schema_version': 1, 'state': 'running', 'request_id': uuid4().hex,
                      'pid': os.getpid(), 'started_at': time.time()}
            self._write(directory, record, key)
            try:
                if on_event:
                    on_event({'state': 'running', 'wait_s': time.monotonic() - started})
                yield
            except BaseException:
                self._write(directory, {**record, 'state': 'uncertain', 'updated_at': time.time()}, key)
                raise
            else:
                self._write(directory, {'schema_version': 1, 'state': 'idle', 'request_id': None}, key)
        except OSError as exc:
            raise CoordinationError('Cannot safely access local request state; no automatic retry is permitted.') from exc
        finally:
            self.waiting = False
            if fd is not None:
                os.close(fd)
            os.close(directory)
