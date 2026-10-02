"""One local engine launch per user and port across Dream entry points.

The server inherits the lock descriptor. Closing the launcher's copy cannot
release a still-running child's lease; never unlink or explicitly unlock it.
No process signals, endpoint requests, or hardware checks occur here.
"""
from contextlib import contextmanager
import asyncio
import fcntl
import logging
import os
from pathlib import Path
import stat
import threading

_local = threading.local()
# DREAM-206: where the lock lives, resolved as the inference leases are (DREAM-205). Test runs took the owner's
# /tmp/dream-machx-load-<uid>-<port>.lock: two concurrent runs refused each other's launch, and a test could block the
# owner's own model load.
LOCK_DIR_ENV = 'DREAM_MACHX_LOAD_LOCK_DIR'
_pytest_lock_dir: Path | None = None


def lock_root() -> Path:
    """The directory the launch lock lives in (DREAM-206). `DREAM_MACHX_LOAD_LOCK_DIR`, when set, names it (an
    absolute path; like the lease folder, a directory of this user's with mode 0700, made so when missing).
    Otherwise /tmp -- except in a test run (the lease resolver's signal: PYTEST_CURRENT_TEST or PYTEST_VERSION in
    the environment), where it is a private folder of this process under the system temp directory, made on its first
    use and removed on a normal exit, so that no test takes or waits on the lock of a Dream running on this computer,
    nor two test runs on each other's."""
    override = os.environ.get(LOCK_DIR_ENV)
    if override:
        if not os.path.isabs(override):
            raise ValueError(f'{LOCK_DIR_ENV} must be an absolute path.')
        return Path(override)
    from ..core.inference_coordination import _under_pytest
    if _under_pytest():
        return _pytest_lock_root()
    return Path('/tmp')


def _pytest_lock_root() -> Path:
    global _pytest_lock_dir
    if _pytest_lock_dir is None:
        from ..core.inference_coordination import _per_process_folder    # resolved here; made on its first use
        _pytest_lock_dir = _per_process_folder('dream-machx-load')
        logging.getLogger(__name__).warning('local model launch lock: under pytest without %s, this process uses %s',
                                            LOCK_DIR_ENV, _pytest_lock_dir)
    return _pytest_lock_dir


@contextmanager
def load_lock(port: int, *, root: Path | None = None):
    root = lock_root() if root is None else root
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
        if root != Path('/tmp'):          # a folder of this user's alone, as the lease folder is (DREAM-206)
            from ..core.inference_coordination import _make_folder      # a per-process folder: made here, on first use
            _make_folder(root)
            info = os.lstat(root)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError(f'Unsafe local model launch lock folder {root}: it must be a directory of yours '
                                 'with mode 0700.')
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
