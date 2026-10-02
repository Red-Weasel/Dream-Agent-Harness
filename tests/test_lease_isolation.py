"""DREAM-205: no test touches the owner's live inference-lease folder, `/tmp/dream-inference-<uid>`.

The leak this guards against: a test that builds a backend against a fake engine on a loopback port and sends a
request takes the product's lease -- `EndpointCoordinator(endpoint, model=...)`, no root -- in the live folder, reads
the records of the Dream running on this computer and rewrites its own (a harness run's "INFERENCE-LEASE FOLDER
CHANGED"; 2026-09-29 21:30, a `<key>.json` rewritten there by a test run). With 16 lanes and concurrent test runs
that can make the live Dream think a lane is free or taken.

The product's resolver `inference_coordination.lease_root` gives (1) `DREAM_INFERENCE_LEASE_DIR` when set, (2) a
private per-process folder under pytest, (3) today's path everywhere else; the fixture in `lease_isolation` (imported
here as every lease-using test module does) sets (1) to the test's tmp_path. Each guard below first checks the root a
lease would use, so on a tree without the resolver it fails before writing a byte into the live folder, and then runs
the lease and compares the live folder's listing and mtimes before and after.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.inference_coordination import CoordinationError, EndpointCoordinator
from dream.core.providers import Provider
from lease_isolation import (LEASE_DIR_ENV, REAL_LEASE_DIR, assert_private_lease_root, isolated_lease_dir,  # noqa: F401
                             per_process_name, pid_namespace, process_start, real_lease_listing)
from test_parallel_engine_leases import LanesEngine

MODEL = "guard-model"
REPO = Path(__file__).resolve().parents[1]


def _lease_files(root: Path, key: str) -> list[str]:
    return sorted(p.name for p in root.iterdir() if p.name.startswith(key)) if root.exists() else []


async def test_a_lease_made_the_products_way_stays_out_of_the_live_folder(isolated_lease_dir):
    """The product's own construction -- no root -- under pytest, with the module fixture: the lease's files go
    under the test's folder and the live folder is byte for byte as it was (listing and mtimes)."""
    before = real_lease_listing()
    lease = EndpointCoordinator("loopback:59999", model=MODEL)               # as openai_compat._coordinator does
    assert_private_lease_root(lease.root, inside=isolated_lease_dir)       # before the lease is taken
    assert lease.root == isolated_lease_dir
    async with lease.request():
        assert lease.status()["state"] == "running"
    assert lease.status()["state"] == "idle"
    assert _lease_files(isolated_lease_dir, lease.key) == [lease.key + ".json", lease.key + ".lock"]
    assert real_lease_listing() == before


async def test_a_backends_lease_on_a_fake_engine_stays_out_of_the_live_folder(isolated_lease_dir):
    """The leaking class itself: a backend on a fake engine at an ephemeral loopback port, no coordination override,
    one request sent. Its lease is keyed sha256("loopback:<port>\\nmodel=<model>") and lives under the test's folder;
    the live folder is untouched."""
    before = real_lease_listing()
    engine = LanesEngine(1)
    try:
        provider = Provider(key="machx", label="MachX", kind="openai", base_url=engine.url, default_api_key="x")
        backend = OpenAICompatBackend(provider=provider, model=MODEL, system_prompt="SYSTEM", tools=[],
                                      permission_cb=None, subagents=None, profile=None)
        lease = backend._coordinator()
        assert lease is not None
        assert_private_lease_root(lease.root, inside=isolated_lease_dir)   # before the backend connects or asks
        await backend.connect()
        engine.lead = [{"text": "hello"}]
        events = [event async for event in backend.ask("hi")]
        assert not [e for e in events if e.kind == "error"], events
        assert backend.coordination_status()["state"] == "idle"
        assert _lease_files(isolated_lease_dir, lease.key) == [lease.key + ".json", lease.key + ".lock"]
        await backend.disconnect()
    finally:
        engine.close()
    assert real_lease_listing() == before


def test_the_override_names_the_folder_and_must_be_an_absolute_path(monkeypatch, tmp_path):
    from dream.core.inference_coordination import LEASE_DIR_ENV as product_name, lease_root
    assert product_name == LEASE_DIR_ENV
    monkeypatch.setenv(LEASE_DIR_ENV, str(tmp_path / "elsewhere"))
    assert lease_root() == tmp_path / "elsewhere"
    assert EndpointCoordinator("loopback:59999").root == tmp_path / "elsewhere"
    for bad in ("relative/leases", "leases", "./here"):         # (an environment value cannot hold NUL)
        monkeypatch.setenv(LEASE_DIR_ENV, bad)
        with pytest.raises(CoordinationError, match="absolute path"):
            lease_root()
        with pytest.raises(CoordinationError, match="absolute path"):
            EndpointCoordinator("loopback:59999")
    monkeypatch.setenv(LEASE_DIR_ENV, "")                                   # empty = not set
    assert lease_root() != tmp_path / "elsewhere"


def test_outside_pytest_the_default_is_todays_path(monkeypatch):
    """Dream itself (not a test run, nothing set) keeps /tmp/dream-inference-<uid>: the path is computed here, never
    opened. `_under_pytest` is replaced because pytest's PYTEST_VERSION is in this process's environment for good."""
    from dream.core import inference_coordination
    from dream.core.inference_coordination import lease_root
    monkeypatch.delenv(LEASE_DIR_ENV, raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert lease_root() != REAL_LEASE_DIR                 # PYTEST_VERSION (the whole session) keeps a test run private
    monkeypatch.setattr(inference_coordination, "_under_pytest", lambda: False)
    assert lease_root() == REAL_LEASE_DIR == Path("/tmp") / f"dream-inference-{os.getuid()}"
    assert EndpointCoordinator("loopback:59999", model=MODEL).root == REAL_LEASE_DIR


async def test_under_pytest_with_nothing_set_the_default_is_a_private_folder_of_this_process(monkeypatch):
    """The rail for a test module that does not import the fixture: under pytest the default root is a per-process
    folder under the system temp directory, never the live one; it is created private (0700) on first use. It holds
    with PYTEST_CURRENT_TEST gone too (import time, a thread outliving its test): PYTEST_VERSION, which pytest sets for
    the whole session, is the signal then."""
    from dream.core.inference_coordination import lease_root
    monkeypatch.delenv(LEASE_DIR_ENV, raising=False)
    assert os.environ.get("PYTEST_CURRENT_TEST") and os.environ.get("PYTEST_VERSION")
    before = real_lease_listing()
    root = lease_root()
    assert_private_lease_root(root, inside=Path(tempfile.gettempdir()))     # before any lease is taken
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    assert lease_root() == root
    assert root != REAL_LEASE_DIR and root.parent == Path(tempfile.gettempdir()) and str(os.getpid()) in root.name
    assert root.name.startswith(f"dream-inference-{os.getuid()}-")
    assert lease_root() == root                                             # one folder per process
    lease = EndpointCoordinator("loopback:59998", model=MODEL)
    assert lease.root == root
    async with lease.request():
        pass
    assert stat.S_IMODE(root.stat().st_mode) == 0o700 and root.stat().st_uid == os.getuid()
    assert _lease_files(root, lease.key) == [lease.key + ".json", lease.key + ".lock"]
    assert real_lease_listing() == before


def test_the_fixture_gives_each_test_its_own_empty_folder(isolated_lease_dir, tmp_path):
    from dream.core.inference_coordination import lease_root
    assert isolated_lease_dir == tmp_path / "inference-leases" and lease_root() == isolated_lease_dir
    assert not isolated_lease_dir.exists() or not any(isolated_lease_dir.iterdir())


# --- the DREAM-205 gate, round 1 (KL3, KL4) ------------------------------------------------------------------------

def _child(code: str, env: dict) -> list[str]:
    """Run `code` in a fresh interpreter that imports Dream from this checkout -> its stdout lines."""
    done = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(REPO)!r})\n" + code], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, (done.returncode, done.stdout[-2000:], done.stderr[-2000:])
    return done.stdout.split()


def test_a_process_that_only_imports_pytest_keeps_the_live_path():
    """KL3: Dream runs custom and plugin tools in its own process (tools/registry), and a tool may import pytest. That
    is no test run: a process with the pytest module loaded but neither PYTEST_CURRENT_TEST nor PYTEST_VERSION in its
    environment keeps /tmp/dream-inference-<uid> -- computed here, never opened, so the live folder is not touched."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_CURRENT_TEST", "PYTEST_VERSION", LEASE_DIR_ENV)}
    out = _child("import pytest\n"
                 "from dream.core import inference_coordination as ic\n"
                 "print('pytest' in sys.modules, ic._under_pytest(), ic.lease_root(), ic.EndpointCoordinator('loopback:59996').root)",
                 env)
    assert out == ["True", "False", str(REAL_LEASE_DIR), str(REAL_LEASE_DIR)]


def test_the_per_process_folder_is_removed_on_a_normal_exit():
    """KL4: a test process with nothing set takes its leases in a folder of its own, removed when it exits normally
    (atexit; a killed run leaves it). A child test process takes a lease the product's way and exits: its folder held
    the lease while it ran and is gone after; the live folder is as it was. The child takes the lease only once its
    root, resolved, is exactly <tempdir>/dream-inference-<uid>-pytest-<its pid>-<its start time>-<its PID namespace>
    -- neither the live folder nor inside it -- and exits 3 otherwise (DREAM-205 gate, round 2): a broken rail fails
    the test before a byte is written."""
    before = real_lease_listing()
    env = {k: v for k, v in os.environ.items() if k != LEASE_DIR_ENV}      # PYTEST_VERSION comes with it: a test run
    out = _child("import asyncio, os, tempfile\n"
                 "from dream.core.inference_coordination import EndpointCoordinator\n"
                 "lease = EndpointCoordinator('loopback:59995', model='exit-model')\n"
                 "real, live = os.path.realpath(lease.root), os.path.realpath(" + repr(str(REAL_LEASE_DIR)) + ")\n"
                 "start = open('/proc/self/stat').read().rsplit(')', 1)[1].split()[19]\n"
                 "ns = os.stat('/proc/self/ns/pid').st_ino\n"
                 "if (os.path.basename(real) != f'dream-inference-{os.getuid()}-pytest-{os.getpid()}-{start}-{ns}'\n"
                 "        or os.path.dirname(real) != os.path.realpath(tempfile.gettempdir())\n"
                 "        or real == live or real.startswith(live + os.sep)):\n"
                 "    print('REFUSED', lease.root)\n"
                 "    raise SystemExit(3)\n"
                 "async def take():\n"
                 "    async with lease.request():\n"
                 "        pass\n"
                 "asyncio.run(take())\n"
                 "print(os.getpid(), start, ns, lease.root,\n"
                 "      sorted(p.name for p in lease.root.iterdir()) == [lease.key + '.json', lease.key + '.lock'])",
                 env)
    pid, start, ns, root, held = out
    assert Path(root).name == f"dream-inference-{os.getuid()}-pytest-{pid}-{start}-{ns}" and held == "True", out
    assert not Path(root).exists(), f"{root} outlived its process"
    assert real_lease_listing() == before


# --- the per-process folder's exit cleanup: only a folder this process made (DREAM-206 gate) -----------------------

@pytest.mark.parametrize("already_there", [None, 0o755, 0o700], ids=["made-here", "planted-0755", "left-0700"])
def test_the_lease_folders_exit_cleanup_is_registered_only_for_a_folder_this_process_made(monkeypatch, tmp_path,
                                                                                          already_there):
    """The per-process lease folder is made by this process (0700) on its first use, and only then given its exit
    cleanup, for exactly <tempdir>/dream-inference-<uid>-pytest-<pid>-<start>-<pid namespace>. Resolving the root, or
    making a coordinator, makes nothing (DREAM-206 gate, round 3: a child killed before its first lease left an empty
    folder for good). A folder already at that path -- planted (0755) or left by an earlier process (0700) -- gets no
    cleanup: a planted one is refused by the coordinator, and both stay as they were. (Before the gate's fix the
    cleanup was registered when the path was first computed, whatever it named.)"""
    import atexit
    from dream.core import inference_coordination
    registered = []
    monkeypatch.setattr(inference_coordination, "_pytest_lease_dir", None)
    monkeypatch.setattr(atexit, "register", lambda *args: registered.append(args))    # wherever it is registered
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.delenv(LEASE_DIR_ENV, raising=False)
    folder = temp / per_process_name("dream-inference")
    if already_there:
        folder.mkdir()
        folder.chmod(already_there)
        (folder / "not-ours").touch()
    lease = EndpointCoordinator("loopback:59994", model=MODEL)
    assert inference_coordination.lease_root() == lease.root == folder
    if already_there is None:
        assert list(temp.iterdir()) == [] and registered == []                  # resolved, not made
        os.close(lease._directory())                                            # its first use
        made = os.lstat(folder)
        assert stat.S_IMODE(made.st_mode) == 0o700 and len(registered) == 1
        assert registered[0][1:] == (folder, os.getpid(), made.st_dev, made.st_ino)  # this folder, in this process
        os.close(lease._directory())
        assert inference_coordination.lease_root() == folder and len(registered) == 1
        return
    if already_there == 0o755:
        with pytest.raises(CoordinationError):
            lease._directory()
    else:
        os.close(lease._directory())                                            # the user's own private folder: used
    assert registered == []
    assert sorted(p.name for p in folder.iterdir()) == ["not-ours"] and stat.S_IMODE(folder.stat().st_mode) == already_there


# --- the DREAM-206 gate, round 2: pid reuse, fork, the exact folder --------------------------------------------------

@pytest.fixture
def fresh_lease_process(monkeypatch, tmp_path):
    """inference_coordination as in a test process that has not made its per-process lease folder yet, whose system
    temp directory is this test's own folder; the exit removals it registers are recorded instead -> (temp, records)."""
    import atexit
    from dream.core import inference_coordination
    registered = []
    monkeypatch.setattr(inference_coordination, "_pytest_lease_dir", None)
    monkeypatch.setattr(atexit, "register", lambda *args: registered.append(args))
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.delenv(LEASE_DIR_ENV, raising=False)
    return temp, registered


async def test_a_leftover_folder_of_an_earlier_process_with_this_pid_is_not_used(fresh_lease_process, tmp_path):
    """(c) Pids are reused (they wrapped in about 25 minutes here). A test process killed mid-request leaves its
    per-process folder holding a "running" record; a later process that gets the same pid must not take that folder
    for its own, or its request is refused as "uncertain" and the folder stays for good. Neither the name without a
    start time nor a same-pid name with an earlier start time is this process's: its request goes through in a folder
    of its own, and the leftovers are left as they were."""
    import json
    import time
    import uuid
    from dream.core import inference_coordination
    temp, _ = fresh_lease_process
    endpoint, model = "loopback:59993", "reuse-model"
    key = EndpointCoordinator(endpoint, root=tmp_path / "never-opened", model=model).key
    gone = subprocess.Popen(["true"])
    gone.wait()
    record = {"schema_version": 1, "state": "running", "request_id": uuid.uuid4().hex, "pid": gone.pid,
              "started_at": time.time() - 600}
    leftovers = [temp / f"dream-inference-{os.getuid()}-pytest-{os.getpid()}",
                 temp / f"dream-inference-{os.getuid()}-pytest-{os.getpid()}-{int(process_start()) - 1}-{pid_namespace()}"]
    for folder in leftovers:
        folder.mkdir(mode=0o700)
        fd = os.open(folder / f"{key}.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as out:
            json.dump(record, out)
    root = inference_coordination.lease_root()
    assert root == temp / per_process_name("dream-inference") and root not in leftovers
    lease = EndpointCoordinator(endpoint, model=model)
    assert_private_lease_root(lease.root, inside=root)
    async with lease.request():                                             # not refused as "uncertain"
        pass
    for folder in leftovers:
        assert json.loads((folder / f"{key}.json").read_text()) == record


def test_a_forked_child_leaves_its_parents_per_process_folders_in_place(tmp_path):
    """(b) Exit handlers are inherited across os.fork(): a raw fork child of a test process that exits normally runs
    them. The removal belongs to the process that made the folders, so the parent's lease and launch-lock folders --
    made by its first lease and first launch lock -- survive the child, and go when the parent itself exits normally."""
    env = {k: v for k, v in os.environ.items() if k not in (LEASE_DIR_ENV, "DREAM_MACHX_LOAD_LOCK_DIR")}
    env["TMPDIR"] = str(tmp_path)
    out = _child("import asyncio, os\n"
                 "from dream.core.inference_coordination import EndpointCoordinator\n"
                 "from dream.local.load_lock import load_lock, lock_root\n"
                 "lease = EndpointCoordinator('loopback:59991', model='fork-model')\n"
                 "async def take():\n"
                 "    async with lease.request():\n"
                 "        pass\n"
                 "asyncio.run(take())\n"
                 "with load_lock(59991):\n"
                 "    pass\n"
                 "folders = [lease.root, lock_root()]\n"
                 "child = os.fork()\n"
                 "if child == 0:\n"
                 "    raise SystemExit(0)                    # a normal exit: the inherited exit handlers run\n"
                 "os.waitpid(child, 0)\n"
                 "print(*folders, *(folder.is_dir() for folder in folders))\n",
                 env)
    lease, lock, lease_kept, lock_kept = out
    assert Path(lease).name.startswith(f"dream-inference-{os.getuid()}-pytest-"), out
    assert Path(lock).name.startswith(f"dream-machx-load-{os.getuid()}-pytest-"), out
    assert (lease_kept, lock_kept) == ("True", "True"), f"the child's exit removed its parent's folders: {out}"
    assert not Path(lease).exists() and not Path(lock).exists()     # the parent's own normal exit removed them


def test_the_exit_removal_takes_only_the_folder_this_process_made(fresh_lease_process, monkeypatch):
    """(a) The registered removal takes that very folder -- its device and inode, recorded at mkdir -- and only in the
    process that made it: run in another process it removes nothing, and a folder that replaced it at the same path
    stays (the made one moved aside, so its inode is still taken); the folder it made is removed."""
    temp, registered = fresh_lease_process
    lease = EndpointCoordinator("loopback:59990", model=MODEL)
    os.close(lease._directory())                                            # its first use makes it
    folder = lease.root
    (removal, *args), = registered
    real_getpid = os.getpid
    monkeypatch.setattr(os, "getpid", lambda: real_getpid() + 1)            # run in another process (a forked child)
    removal(*args)
    monkeypatch.setattr(os, "getpid", real_getpid)
    assert folder.is_dir()
    aside = temp / "made-aside"
    folder.rename(aside)                                                    # the made inode moves aside
    folder.mkdir(mode=0o700)
    (folder / "not-ours").touch()
    removal(*args)
    assert (folder / "not-ours").exists()                                   # the replacement stays
    (folder / "not-ours").unlink()
    folder.rmdir()
    aside.rename(folder)
    removal(*args)
    assert not folder.exists()                                              # the folder it made is removed


def test_a_path_that_is_not_this_processs_own_folder_is_never_made_nor_handed_out(fresh_lease_process, tmp_path):
    """(a) The name check runs on every call, before the path is handed out: a path that is not exactly
    <tempdir>/<prefix>-<uid>-pytest-<pid>-<start>-<pid namespace> -- here a prefix that climbs out of the temp
    directory, a stand-in for a broken path rule -- is refused each time, nothing is made for it and no removal is
    registered. With the check after the mkdir, the first call made the folder and the second, finding it there,
    handed it out unchecked (the DREAM-206 gate's Gap 1)."""
    from dream.core import inference_coordination
    temp, registered = fresh_lease_process
    for _ in range(2):
        with pytest.raises(RuntimeError):
            inference_coordination._per_process_folder("../escaped")
    assert list(tmp_path.glob("escaped*")) == [] and list(temp.iterdir()) == [] and registered == []


# --- the DREAM-206 gate, round 3: PID namespaces, no /proc ------------------------------------------------------------

needs_bwrap = pytest.mark.skipif(shutil.which("bwrap") is None, reason="needs bubblewrap")


def _under_bwrap(flags: list[str], code: str) -> list[str]:
    """The command line that runs `code` in a fresh interpreter importing Dream from this checkout, under bubblewrap
    with `flags` and the file system as it is (the DREAM-206 gate's round-3 probes)."""
    return ["bwrap", "--dev-bind", "/", "/", "--die-with-parent", *flags, sys.executable, "-c",
            f"import sys; sys.path.insert(0, {str(REPO)!r})\n" + code]


@needs_bwrap
def test_processes_in_separate_pid_namespaces_use_separate_folders(tmp_path):
    """(1) Processes started together in separate PID namespaces share a pid and often a start tick (the gate: pid 2 and
    the same tick in 40 of 40 pairs), so the name also carries the PID namespace, the inode of /proc/self/ns/pid. Two
    children, each in a PID namespace of its own and both with one temp directory, take a lease and the launch lock:
    both are pid 2, and each uses folders named for its own namespace, then removes them at exit."""
    temp = tmp_path / "tmp"
    temp.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in (LEASE_DIR_ENV, "DREAM_MACHX_LOAD_LOCK_DIR")}
    env["TMPDIR"] = str(temp)
    code = ("import asyncio, os\n"
            "from dream.core.inference_coordination import EndpointCoordinator\n"
            "from dream.local.load_lock import load_lock, lock_root\n"
            "start = open('/proc/self/stat').read().rsplit(')', 1)[1].split()[19]\n"
            "ns = os.stat('/proc/self/ns/pid').st_ino\n"
            "lease = EndpointCoordinator('loopback:59989', model='namespace-model')\n"
            "async def take():\n"
            "    async with lease.request():\n"
            "        pass\n"
            "asyncio.run(take())\n"
            "with load_lock(59989):\n"
            "    pass\n"
            "print(os.getpid(), start, ns, lease.root.name, lock_root().name)\n")
    children = [subprocess.Popen(_under_bwrap(["--unshare-pid", "--proc", "/proc"], code), env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    outs = []
    for child in children:
        stdout, stderr = child.communicate(timeout=120)
        assert child.returncode == 0, (stdout[-2000:], stderr[-2000:])
        outs.append(stdout.split())
    for pid, start, ns, lease, lock in outs:
        assert pid == "2" and lease == f"dream-inference-{os.getuid()}-pytest-2-{start}-{ns}", outs
        assert lock == f"dream-machx-load-{os.getuid()}-pytest-2-{start}-{ns}", outs
    assert outs[0][2] != outs[1][2] and outs[0][3] != outs[1][3], outs
    assert list(temp.iterdir()) == []


@needs_bwrap
def test_without_proc_a_test_process_is_told_which_folder_variables_to_set(tmp_path):
    """(2) Without a readable /proc (macOS; a container without it) a test process cannot name a folder of its own and
    uses none. Resolving the lease root, making a coordinator, resolving the lock root and taking the launch lock each
    raise a ValueError that names the two variables to set -- the error load_lock's callers handle, never a bare
    OSError, and never a folder named by the pid alone. Nothing is made."""
    temp = tmp_path / "tmp"
    temp.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in (LEASE_DIR_ENV, "DREAM_MACHX_LOAD_LOCK_DIR")}
    env["TMPDIR"] = str(temp)
    code = ("from dream.core.inference_coordination import EndpointCoordinator, lease_root\n"
            "from dream.local.load_lock import load_lock, lock_root\n"
            "def take_lock():\n"
            "    with load_lock(59988):\n"
            "        pass\n"
            "for call in (lease_root, lambda: EndpointCoordinator('loopback:59988'), lock_root, take_lock):\n"
            "    try:\n"
            "        call()\n"
            "        print('no-error -')\n"
            "    except ValueError as exc:\n"
            "        named = 'DREAM_INFERENCE_LEASE_DIR' in str(exc) and 'DREAM_MACHX_LOAD_LOCK_DIR' in str(exc)\n"
            "        print('ValueError', named)\n"
            "    except Exception as exc:\n"
            "        print(type(exc).__name__, '-')\n")
    done = subprocess.run(_under_bwrap(["--tmpfs", "/proc"], code), env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, (done.stdout[-2000:], done.stderr[-2000:])
    assert done.stdout.split() == ["ValueError", "True"] * 4, done.stdout
    assert list(temp.iterdir()) == []
