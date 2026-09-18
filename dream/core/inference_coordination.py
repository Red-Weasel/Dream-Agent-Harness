"""Per-user local endpoint leases; no network, hardware probes or process signals.

The lock orders cooperating Dream clients. Persisted uncertainty is deliberate:
closing an HTTP client does not establish that its server stopped computation.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import stat
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


class EndpointCoordinator:
    def __init__(self, endpoint: str, *, root: Path | None = None):
        self.root = Path(root) if root is not None else Path('/tmp') / f'dream-inference-{os.getuid()}'
        self.key = hashlib.sha256(endpoint.encode()).hexdigest()
        self.waiting = False

    def _directory(self):
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                os.close(fd)
                raise CoordinationError('Unsafe inference coordination directory ownership or permissions.')
            return fd
        except OSError as exc:
            raise CoordinationError('Cannot safely open inference coordination state.') from exc

    def _lock(self, directory):
        fd = os.open(self.key + '.lock', os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            os.close(fd)
            raise CoordinationError('Unsafe inference coordination lock.')
        return fd

    def _read(self, directory):
        try:
            fd = os.open(self.key + '.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
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

    def _write(self, directory, record):
        temporary = self.key + '.' + uuid4().hex + '.tmp'
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(json.dumps(record, allow_nan=False).encode())
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.key + '.json', src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass

    def status(self):
        directory = self._directory()
        try:
            record = self._read(directory)
            return {**record, 'enabled': True, 'waiting': self.waiting,
                    'can_reconcile': record['state'] in {'running', 'uncertain'}}
        finally:
            os.close(directory)

    def reconcile(self, request_id: str, *, confirmed_idle: bool):
        if confirmed_idle is not True:
            raise CoordinationError('Confirm that the server is idle before resuming local requests.')
        directory = self._directory()
        fd = None
        try:
            fd = self._lock(directory)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CoordinationError('A Dream request is still active; it cannot be reconciled yet.') from exc
            record = self._read(directory)
            if record['state'] not in {'uncertain', 'running'} or not request_id or record.get('request_id') != request_id:
                raise CoordinationError('Request state changed; refresh before confirming server idle.')
            self._write(directory, {'schema_version': 1, 'state': 'idle', 'request_id': None,
                                    'reconciled_at': time.time()})
        finally:
            if fd is not None:
                os.close(fd)
            os.close(directory)
        return self.status()

    @asynccontextmanager
    async def request(self, *, timeout=120.0, on_event=None):
        directory = self._directory()
        fd = None
        started = time.monotonic()
        try:
            fd = self._lock(directory)
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if not self.waiting and on_event:
                        on_event({'state': 'waiting', 'message': 'Waiting for another Dream request on this local engine.'})
                    self.waiting = True
                    if time.monotonic() - started >= timeout:
                        raise CoordinationError('Timed out waiting for another Dream request; no new request was sent.')
                    await asyncio.sleep(.05)
            self.waiting = False
            previous = self._read(directory)
            if previous['state'] != 'idle':
                raise CoordinationError('Previous local request outcome is uncertain. Check the server, then confirm idle in Controls.')
            record = {'schema_version': 1, 'state': 'running', 'request_id': uuid4().hex,
                      'pid': os.getpid(), 'started_at': time.time()}
            self._write(directory, record)
            try:
                if on_event:
                    on_event({'state': 'running', 'wait_s': time.monotonic() - started})
                yield
            except BaseException:
                self._write(directory, {**record, 'state': 'uncertain', 'updated_at': time.time()})
                raise
            else:
                self._write(directory, {'schema_version': 1, 'state': 'idle', 'request_id': None})
        except OSError as exc:
            raise CoordinationError('Cannot safely access local request state; no automatic retry is permitted.') from exc
        finally:
            self.waiting = False
            if fd is not None:
                os.close(fd)
            os.close(directory)
