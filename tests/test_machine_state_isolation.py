"""DREAM-206: no test, and no process a test starts, takes, waits on or changes the owner's machine-wide Dream state.

The local model launch lock (dream/local/load_lock.py) was /tmp/dream-machx-load-<uid>-<port>.lock for every process.
Two concurrent test runs refused each other's launch ("Another Dream window is loading or using a local model on this
port", harness log lease-iso-concurrent-b), and a plain pytest run, on the default port 11435, could hold the lock of the
owner's own model load. `load_lock.lock_root` now resolves its folder as `inference_coordination.lease_root` resolves the
leases' (DREAM-205): DREAM_MACHX_LOAD_LOCK_DIR when set; in a test run a private folder of the process; /tmp otherwise.
Each lock guard makes the product's attempt to open a lock file in the owner's lock folder (OWNERS_LOCK_DIR, resolved)
fail the test before the file is created, locked or waited on, so on a tree without the resolver it fails without
touching the owner's lock.

The inference-lease folder (/tmp/dream-inference-<uid>, DREAM-205's `lease_root`): the Dream processes the suite's tests
start that take a lease -- the TUI drivers of test_interrupt_paths and test_pty_e2e, the only ones in the suite (a traced
full run, DREAM-205 gate record) -- start with the test's environment, so they inherit its isolation: the test's
DREAM_INFERENCE_LEASE_DIR when set, else PYTEST_CURRENT_TEST and PYTEST_VERSION, which keep a child's leases in a
private folder of its own. A child started with a scrubbed environment has neither and must be given the variable.
The guard's child takes its lease only once its root, resolved, is exactly the private one expected (and neither the
live folder nor inside it), and the live folder's listing and mtimes are compared before and after.
"""
from __future__ import annotations

import fcntl
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from dream.desktop import startup
from dream.local import load_lock, machx
from lease_isolation import (LEASE_DIR_ENV, LOAD_LOCK_DIR_ENV, REAL_LEASE_DIR, isolated_lease_dir,  # noqa: F401
                             per_process_name, real_lease_listing)

REPO = Path(__file__).resolve().parents[1]
OWNERS_PORT = 11435                                   # the owner's engine port: its lock is the one to keep
OWNERS_LOCK_DIR = Path("/tmp")                        # where Dream itself keeps the launch lock
PREFIX = f"dream-machx-load-{os.getuid()}-"
# The Dream processes these tests start import Dream from this tree and must leave no bytecode in it. A child whose
# environment lacked PYTHONDONTWRITEBYTECODE (the scrubbed one always does) wrote 52 .pyc files there.
_NO_BYTECODE = {"PYTHONDONTWRITEBYTECODE": "1"}


# --- the local model launch lock ------------------------------------------------------------------------------------

def owners_lock(port: int = OWNERS_PORT) -> Path:
    return OWNERS_LOCK_DIR / f"{PREFIX}{port}.lock"


def in_owners_lock_dir(path) -> bool:
    """Whether `path` is a launch lock file in the owner's lock folder, its folder resolved so that no `..` or link can
    alias it (DREAM-205 gate, round 2)."""
    path = Path(os.fsdecode(path))
    return Path(os.path.realpath(path.parent)) == Path(os.path.realpath(OWNERS_LOCK_DIR)) and path.name.startswith(PREFIX)


def lock_state(path: Path):
    """The owner's lock file as it is now -- (inode, mtime_ns) -- or None when there is none."""
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    return info.st_ino, info.st_mtime_ns


class _Watched:
    """load_lock's view of `os`: the real module, except that opening a lock file in the owner's lock folder fails the
    test (before the file is created or locked), and every lock path opened is recorded."""

    def __init__(self):
        self.opened: list[Path] = []

    def __getattr__(self, name):
        return getattr(os, name)

    def open(self, path, *args, **kwargs):
        path = Path(os.fsdecode(path))
        if in_owners_lock_dir(path):
            raise AssertionError(f"the product would open the owner's launch lock {path}")
        self.opened.append(path)
        return os.open(path, *args, **kwargs)


@pytest.fixture
def watched(monkeypatch):
    view = _Watched()
    monkeypatch.setattr(load_lock, "os", view)
    before = lock_state(owners_lock())
    yield view
    assert lock_state(owners_lock()) == before, "the owner's launch lock file changed"


def test_the_desktops_launch_lock_in_a_test_run_is_a_private_one(watched, monkeypatch):
    """The product's own way -- startup.local_load_lock() on the owner's port, nothing set -- in a test run: the lock is
    taken, and really held, in this process's private folder (0700), never /tmp's."""
    monkeypatch.delenv(LOAD_LOCK_DIR_ENV, raising=False)
    monkeypatch.setattr(machx, "PORT", OWNERS_PORT)
    with startup.local_load_lock():
        held, = watched.opened
        assert held.name == f"{PREFIX}{OWNERS_PORT}.lock"
        folder = held.parent
        assert folder.name == per_process_name("dream-machx-load") and not in_owners_lock_dir(held)
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700 and folder.stat().st_uid == os.getuid()
        other = os.open(held, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):                        # it is really held there
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(other)
    assert load_lock.lock_root() == folder                          # one folder per process


def test_the_override_names_the_folder_which_is_private_and_absolute(watched, monkeypatch, tmp_path):
    """DREAM_MACHX_LOAD_LOCK_DIR names the folder, made 0700 when missing; like the lease folder it must be this
    user's with mode 0700, and an absolute path, or no lock is taken."""
    monkeypatch.setenv(LOAD_LOCK_DIR_ENV, str(tmp_path / "locks"))
    with load_lock.load_lock(OWNERS_PORT):
        assert watched.opened == [tmp_path / "locks" / f"{PREFIX}{OWNERS_PORT}.lock"]
        assert stat.S_IMODE((tmp_path / "locks").stat().st_mode) == 0o700
    assert load_lock.lock_root() == tmp_path / "locks"
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o755)
    monkeypatch.setenv(LOAD_LOCK_DIR_ENV, str(shared))
    with pytest.raises(ValueError, match="Unsafe local model launch lock folder"):
        with load_lock.load_lock(OWNERS_PORT):
            pytest.fail("a folder others can read was used")
    assert not any(shared.iterdir())
    for bad in ("relative/locks", "locks", "./here"):
        monkeypatch.setenv(LOAD_LOCK_DIR_ENV, bad)
        with pytest.raises(ValueError, match="absolute path"):
            with load_lock.load_lock(OWNERS_PORT):
                pytest.fail("a relative lock folder was used")
    monkeypatch.setenv(LOAD_LOCK_DIR_ENV, "")                          # empty = not set
    assert load_lock.lock_root() != tmp_path / "locks"


def test_outside_a_test_run_the_lock_stays_in_tmp(monkeypatch):
    """Dream itself (not a test run, nothing set) keeps /tmp/dream-machx-load-<uid>-<port>.lock: computed here, never
    opened. `_under_pytest` is replaced because pytest's PYTEST_VERSION is in this process's environment for good."""
    from dream.core import inference_coordination
    monkeypatch.delenv(LOAD_LOCK_DIR_ENV, raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert load_lock.lock_root() != OWNERS_LOCK_DIR                 # PYTEST_VERSION keeps a test run private
    monkeypatch.setattr(inference_coordination, "_under_pytest", lambda: False)
    assert load_lock.lock_root() == OWNERS_LOCK_DIR


TWO_AT_ONCE = textwrap.dedent(r'''
    import os, sys, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    from dream.local import load_lock
    prefix, owners = f"dream-machx-load-{os.getuid()}-", os.path.realpath(sys.argv[5])
    class Watched:
        def __getattr__(self, name):
            return getattr(os, name)
        def open(self, path, *args, **kwargs):       # the owner's lock folder, resolved: refused before the open
            if os.path.realpath(Path(path).parent) == owners and Path(path).name.startswith(prefix):
                print(f"would open the owner's launch lock {path}")
                sys.exit(3)
            print(f"opened {path}", flush=True)
            return os.open(path, *args, **kwargs)
    load_lock.os = Watched()
    mine, theirs = Path(sys.argv[2]), Path(sys.argv[3])
    with load_lock.load_lock(int(sys.argv[4])):
        mine.touch()
        deadline = time.monotonic() + 30
        while not theirs.exists():
            if time.monotonic() > deadline:
                print("the other run never held its lock")
                sys.exit(4)
            time.sleep(0.05)
''')


def test_two_test_runs_at_once_each_hold_their_own_launch_lock(watched, tmp_path):
    """Two test processes at once (lease-iso-concurrent-b): each a test run (its environment the test's, no lock
    folder named), both take the launch lock on the same port at the same time and hold it until the other has it
    too -- each in a private folder of its own, neither in /tmp."""
    env = {k: v for k, v in os.environ.items() if k != LOAD_LOCK_DIR_ENV} | _NO_BYTECODE
    a, b = tmp_path / "a-holds", tmp_path / "b-holds"
    runs = [subprocess.Popen([sys.executable, "-c", TWO_AT_ONCE, str(REPO), str(mine), str(theirs), str(OWNERS_PORT),
                              str(OWNERS_LOCK_DIR)],
                             env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for mine, theirs in ((a, b), (b, a))]
    outputs = [run.communicate(timeout=60)[0] for run in runs]
    assert [run.returncode for run in runs] == [0, 0], outputs
    folders = [Path(out.split("opened ", 1)[1].split()[0]).parent for out in outputs]
    assert folders[0] != folders[1] and all(f.name.startswith(f"{PREFIX}pytest-") for f in folders), folders
    assert not any(f.exists() for f in folders)                     # each removed at its process's normal exit
    notice = f"local model launch lock: under pytest without {LOAD_LOCK_DIR_ENV}, this process uses"
    assert all(notice in out for out in outputs), outputs          # a warning: on stderr, with no handler installed
    assert OWNERS_LOCK_DIR.is_dir()                                 # no child's exit cleanup reached the owner's folder


# --- the per-process folder's exit cleanup: only a folder this process made (DREAM-206 gate) -----------------------

@pytest.fixture
def fresh_process(monkeypatch, tmp_path):
    """load_lock as in a test process that has not made its per-process folder yet, whose system temp directory is a
    folder of this test's: every exit cleanup load_lock registers is recorded instead -> (that temp folder, the
    per-process folder's path, the recorded registrations)."""
    import atexit
    import tempfile
    registered = []
    monkeypatch.setattr(load_lock, "_pytest_lock_dir", None)
    monkeypatch.setattr(atexit, "register", lambda *args: registered.append(args))    # wherever it is registered
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.delenv(LOAD_LOCK_DIR_ENV, raising=False)
    return temp, temp / per_process_name("dream-machx-load"), registered


def test_the_exit_cleanup_is_registered_only_for_the_folder_this_process_made(fresh_process):
    """The per-process folder is made by this process (0700) on its first lock, not when the root is resolved (DREAM-206
    gate, round 3), and only then given its exit cleanup -- once, for exactly
    <tempdir>/dream-machx-load-<uid>-pytest-<pid>-<start>-<pid namespace>, in this process; never for the owner's
    folder or anything that was already there."""
    temp, folder, registered = fresh_process
    assert load_lock.lock_root() == folder
    assert list(temp.iterdir()) == [] and registered == []                      # resolved, not made
    with load_lock.load_lock(OWNERS_PORT):                                      # its first use, in this test's folder
        pass
    made = os.lstat(folder)
    assert stat.S_IMODE(made.st_mode) == 0o700 and made.st_uid == os.getuid()
    assert len(registered) == 1 and registered[0][1:] == (folder, os.getpid(), made.st_dev, made.st_ino)
    assert load_lock.lock_root() == folder and len(registered) == 1
    assert all(args[1] not in (OWNERS_LOCK_DIR, Path("/tmp"), temp) for args in registered)


@pytest.mark.parametrize("mode", [0o755, 0o700], ids=["planted-0755", "left-0700"])
def test_a_per_process_folder_already_there_is_never_removed_at_exit(fresh_process, mode):
    """A folder at the per-process path that this process did not make -- planted by someone (0755), or left by an
    earlier process with the same pid (0700) -- gets no exit cleanup: a planted one is refused, and both stay as they
    were, contents included. (Before the gate's fix a refused folder was still removed at exit.)"""
    temp, folder, registered = fresh_process
    folder.mkdir()
    folder.chmod(mode)
    (folder / "not-ours").touch()
    assert load_lock.lock_root() == folder
    if mode == 0o755:
        with pytest.raises(ValueError, match="Unsafe local model launch lock folder"):
            with load_lock.load_lock(OWNERS_PORT):
                pytest.fail("a folder this process did not make, open to others, was used")
    else:
        with load_lock.load_lock(OWNERS_PORT):                                  # the user's own private folder: used
            pass
    assert registered == []
    assert "not-ours" in {p.name for p in folder.iterdir()} and stat.S_IMODE(folder.stat().st_mode) == mode


# --- the lock folder's checks: a link, another uid; and the engine launch itself (DREAM-206 gate, KL3) ------------

def test_a_linked_lock_folder_is_refused_without_following_the_link(watched, monkeypatch, tmp_path):
    """The folder is checked with lstat: DREAM_MACHX_LOAD_LOCK_DIR naming a link to a private folder of this user's is
    refused, and nothing is made at the link's end."""
    end = tmp_path / "end"
    end.mkdir(mode=0o700)
    (tmp_path / "link").symlink_to(end)
    monkeypatch.setenv(LOAD_LOCK_DIR_ENV, str(tmp_path / "link"))
    with pytest.raises(ValueError, match="Unsafe local model launch lock folder"):
        with load_lock.load_lock(OWNERS_PORT):
            pytest.fail("a linked lock folder was used")
    assert not any(end.iterdir())


def test_a_lock_folder_of_another_uid_is_refused(watched, monkeypatch, tmp_path):
    """A folder that is not this user's (seen here through load_lock's view of os) is refused, whatever its mode."""
    folder = tmp_path / "theirs"
    folder.mkdir(mode=0o700)
    monkeypatch.setenv(LOAD_LOCK_DIR_ENV, str(folder))
    real_lstat = os.lstat

    def lstat(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        if Path(path) != folder:
            return info
        return os.stat_result((info.st_mode, info.st_ino, info.st_dev, info.st_nlink, os.getuid() + 1, info.st_gid,
                               info.st_size, int(info.st_atime), int(info.st_mtime), int(info.st_ctime)))
    monkeypatch.setattr(watched, "lstat", lstat, raising=False)
    with pytest.raises(ValueError, match="Unsafe local model launch lock folder"):
        with load_lock.load_lock(OWNERS_PORT):
            pytest.fail("another user's lock folder was used")
    assert not any(folder.iterdir())


def test_machx_serve_holds_this_processs_own_launch_lock(watched, monkeypatch, tmp_path):
    """The engine launch itself -- machx.serve -> _launch -> load_lock(PORT) -- in a test run, with nothing set: the
    lock it holds while the engine starts is this process's private one (a root hard-coded to /tmp in _launch would
    open the owner's lock, which the watched os refuses first). No engine starts: engine_guard.spawn is stubbed."""
    from types import SimpleNamespace
    from dream.local import engine_guard
    monkeypatch.delenv(LOAD_LOCK_DIR_ENV, raising=False)
    monkeypatch.setenv("DREAM_MACHX_CTX", os.environ.get("DREAM_MACHX_CTX", "8192"))   # serve() sets it; restored
    monkeypatch.setattr(machx, "PORT", OWNERS_PORT)
    monkeypatch.setattr(machx.config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(machx, "_log_file", lambda: tmp_path / "model.log")
    monkeypatch.setattr(machx, "_pid_file", lambda: tmp_path / "model.pid")
    spawned = []
    monkeypatch.setattr(engine_guard, "spawn", lambda command, **kwargs: spawned.append(kwargs) or SimpleNamespace(pid=1))
    machx.serve(Path("stand-in.gguf"), gpus=1, ctx=8192)
    held, = watched.opened
    assert held.name == f"{PREFIX}{OWNERS_PORT}.lock" and not in_owners_lock_dir(held)
    assert held.parent.name == per_process_name("dream-machx-load")
    assert len(spawned) == 1 and len(spawned[0]["pass_fds"]) == 1       # the engine inherits the lock


# --- the inference-lease folder: a Dream process a test starts ------------------------------------------------------

LEASE_CHILD = textwrap.dedent(r'''
    import asyncio, os, sys, tempfile
    sys.path.insert(0, sys.argv[1])
    from dream.core.backends.openai_compat import OpenAICompatBackend
    from dream.core.providers import Provider

    async def main():
        provider = Provider(key="machx", label="MachX", kind="openai", base_url=sys.argv[2], default_api_key="x")
        backend = OpenAICompatBackend(provider=provider, model="child-model", system_prompt="SYSTEM", tools=[],
                                      permission_cb=None, subagents=None, profile=None)
        lease = backend._coordinator()
        print(f"root {lease.root}", flush=True)
        # Before the backend connects or asks: the root, resolved, must be exactly the one expected, and neither the
        # live folder nor inside it; otherwise stop before a byte is written there (DREAM-205 gate, round 2).
        real, live, expected = os.path.realpath(lease.root), os.path.realpath(sys.argv[3]), sys.argv[4]
        if expected == "per-process":               # <tempdir>/dream-inference-<uid>-pytest-<pid>-<start>-<pid ns>
            start = open("/proc/self/stat").read().rsplit(")", 1)[1].split()[19]
            ns = os.stat("/proc/self/ns/pid").st_ino
            ok = (os.path.basename(real) == f"dream-inference-{os.getuid()}-pytest-{os.getpid()}-{start}-{ns}"
                  and os.path.dirname(real) == os.path.realpath(tempfile.gettempdir()))
        else:
            ok = real == os.path.realpath(expected)
        if not ok or real == live or real.startswith(live + os.sep):
            print(f"REFUSED {lease.root}", flush=True)
            sys.exit(3)
        await backend.connect()
        events = [event async for event in backend.ask("hi")]
        await backend.disconnect()
        print(f"errors {[e.data for e in events if e.kind == 'error']}", flush=True)
    asyncio.run(main())
''')


def run_lease_child(engine_url: str, env: dict, expected) -> tuple[Path, str]:
    """A Dream process that sends one request to a local engine, as the TUI drivers a test starts do, once its lease's
    root is `expected` (a folder, or "per-process") -> (the folder its lease used, its output)."""
    done = subprocess.run([sys.executable, "-c", LEASE_CHILD, str(REPO), engine_url, str(REAL_LEASE_DIR), str(expected)],
                          env=env | _NO_BYTECODE, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr[-2000:])
    assert "errors []" in done.stdout, done.stdout
    return Path(done.stdout.split("root ", 1)[1].split("\n", 1)[0]), done.stdout


@pytest.fixture
def engine():
    from test_parallel_engine_leases import LanesEngine
    fake = LanesEngine(1)
    fake.lead = [{"text": "hello"}]
    yield fake
    fake.close()


@pytest.mark.parametrize("environment", ["inherited", "inherited-without-the-variable", "scrubbed-with-the-variable"])
def test_a_dream_process_a_test_starts_keeps_its_lease_out_of_the_live_folder(environment, engine, isolated_lease_dir,
                                                                              tmp_path):
    """A child Dream process that takes the MachX lease. With the test's environment (as the TUI drivers are started),
    its lease is in the test's folder; with that environment but no DREAM_INFERENCE_LEASE_DIR (a harness that sets
    none), in a private folder of its own, removed when it exits (PYTEST_CURRENT_TEST and PYTEST_VERSION came with it);
    with a scrubbed environment, when the test passes the variable, in the test's folder. Each time the live folder is
    as it was (listing and mtimes)."""
    before = real_lease_listing()
    if environment == "scrubbed-with-the-variable":
        env = {"PATH": os.environ["PATH"], "LANG": "C.UTF-8", "HOME": str(tmp_path), LEASE_DIR_ENV: str(isolated_lease_dir)}
    else:
        env = dict(os.environ)
        if environment == "inherited-without-the-variable":
            del env[LEASE_DIR_ENV]
    env["DREAM_ROOT"] = str(tmp_path / "root")                          # its data and logs under tmp_path too
    expected = "per-process" if environment == "inherited-without-the-variable" else isolated_lease_dir
    root, out = run_lease_child(engine.url, env, expected)
    if environment == "inherited-without-the-variable":
        assert root.name.startswith(f"dream-inference-{os.getuid()}-pytest-") and not root.exists(), out
    else:
        assert root == isolated_lease_dir and any(isolated_lease_dir.glob("*.json")), out
    assert real_lease_listing() == before
