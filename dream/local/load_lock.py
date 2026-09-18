"""One local engine launch per user and port across Dream entry points.

The server inherits the lock descriptor. Closing the launcher's copy cannot
release a still-running child's lease; never unlink or explicitly unlock it.
No process signals, endpoint requests, or hardware checks occur here.
"""
from contextlib import contextmanager
import asyncio
import fcntl
import os
from pathlib import Path
import stat
import threading

_local = threading.local()


@contextmanager
def load_lock(port: int, *, root: Path = Path('/tmp')):
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    key = (os.getpid(), id(task), str(root), port)
    held = getattr(_local, 'held', None)
    if held is None:
        held = _local.held = {}
    if key in held:
        yield held[key]
        return
    path = root / f'dream-machx-load-{os.getuid()}-{port}.lock'
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        raise ValueError(f'Cannot safely open local model launch lock at {path}') from exc
    try:
        info = os.fstat(fd)
        if (info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError(f'Unsafe local model launch lock at {path}. Inspect its owner and permissions.')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Another Dream window is loading or using a local model on this port. '
                             'Wait for it to become ready, then Refresh and connect to the running model.') from exc
        held[key] = fd
        try:
            yield fd
        finally:
            held.pop(key, None)
    finally:
        os.close(fd)
