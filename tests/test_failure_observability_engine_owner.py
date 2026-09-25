"""Fix #51 (DREAM-110): the engine never outlives the Dream process that owns it.

After a desktop crash, `ie serve` (~200 GiB pinned RAM, both GPUs) ran on for 33 minutes with no
owner: the session process that launched it died without reaching its `finally: stop_owned`, and
the engine lives in its own session (start_new_session), where no terminal hangup reaches it.

The engine is now tied to the process that spawned it and owns its lifecycle: the desktop's
`python -m dream.desktop.startup run` session process, or the terminal `dream local` process.
PR_SET_PDEATHSIG delivers SIGTERM (the engine's orderly stop) when that process dies by any means,
and a small watchdog SIGKILLs the engine if it is still alive after a grace period (default 10 s).
`--keep-hot` stays untied.

Every test uses real processes and a stand-in engine (`python -c "time.sleep(600)"`); no engine,
GPU or model is involved, and the launch lock, port and DREAM_ROOT are private to the test.
"""
from __future__ import annotations

import os
import select
import signal
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# The stand-in engine: sleeps for ten minutes; STUBBORN ignores SIGTERM like a wedged teardown.
SLEEPER = "import time; time.sleep(600)"
STUBBORN = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)"

# An owner process that uses the real spawn helper directly.
OWNER = textwrap.dedent(r'''
    import os, subprocess, sys, threading, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    from dream.local import engine_guard
    mode, child, log = sys.argv[2], sys.argv[3], Path(sys.argv[4])

    def spawn():
        return engine_guard.spawn([sys.executable, "-c", child], keep_hot=(mode == "keep_hot"), log_path=log,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    if mode == "thread":
        box = []
        worker = threading.Thread(target=lambda: box.append(spawn()))
        worker.start()
        worker.join()                            # the spawning thread is gone from here on
        proc = box[0]
    else:
        proc = spawn()
    watchdog = getattr(proc, "dream_watchdog", None)
    print(proc.pid, watchdog.pid if watchdog else 0, flush=True)
    if mode == "exit":
        sys.exit(0)                              # a normal interpreter exit that never stops the engine
    if mode == "crash":
        import ctypes
        ctypes.string_at(0)                      # SIGSEGV, like the 2026-09-23 session crash
    if mode == "stop":
        sys.stdin.readline()
        proc.terminate()
        proc.wait(10)
        print("stopped", flush=True)
    time.sleep(600)
''')

# An owner process that launches through the real machx.serve (bash -lc 'source scripts/env.sh && exec ./build/src/ie serve ...').
MACHX_OWNER = textwrap.dedent(r'''
    import sys, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    from dream.local import machx
    extra = {"keep_hot": True} if sys.argv[2] == "keep_hot" else {}
    proc = machx.serve(Path("stand-in.gguf"), **extra)
    watchdog = getattr(proc, "dream_watchdog", None)
    print(proc.pid, watchdog.pid if watchdog else 0, flush=True)
    time.sleep(600)
''')

# A terminal emulator stand-in: runs its command as the session leader of a new pty, like VTE.
WINDOW = textwrap.dedent(r'''
    import os, pty, signal, sys
    pid, master = pty.fork()
    if pid == 0:
        signal.signal(signal.SIGHUP, signal.SIG_DFL)
        os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
    line = b""
    while not line.endswith(b"\n"):
        line += os.read(master, 1)
    sys.stdout.write(f"{line.decode().strip()} {pid}\n")     # engine, watchdog, session process
    sys.stdout.flush()
    os.read(0, 1)                                # hold the terminal open until killed
''')


def gone_within(fd, seconds):
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    return bool(poller.poll(int(seconds * 1000)))


def ignores_sigterm(pid, within=5.0):
    """Wait until the stand-in has installed SIG_IGN for SIGTERM (bit 14 of SigIgn in /proc)."""
    import time
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("SigIgn:") and int(line.split()[1], 16) & (1 << (signal.SIGTERM - 1)):
                return True
        time.sleep(0.02)
    return False


def kill(fd):
    if fd is None:
        return
    try:
        signal.pidfd_send_signal(fd, signal.SIGKILL)
    except ProcessLookupError:
        pass


class Owner:
    """A real owner process; its children are watched through pidfds opened while it holds them."""

    def __init__(self, argv, *, env=None, stdin=None):
        self.proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stdin=stdin, text=True,
                                     env={**os.environ, **(env or {})})
        fields = self.proc.stdout.readline().split()
        assert fields, "the owner did not report its engine"
        self.engine_pid = int(fields[0])
        self.engine = os.pidfd_open(self.engine_pid)
        self.watchdog = os.pidfd_open(int(fields[1])) if len(fields) > 1 and int(fields[1]) else None
        self.session = os.pidfd_open(int(fields[2])) if len(fields) > 2 else None

    def cleanup(self):
        kill(self.engine)
        kill(self.watchdog)
        kill(self.session)
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(5)


@pytest.fixture
def owners():
    started = []

    def start(argv, **kwargs):
        owner = Owner(argv, **kwargs)
        started.append(owner)
        return owner

    yield start
    for owner in started:
        owner.cleanup()


def helper_owner(tmp_path, mode="sleep", child=SLEEPER):
    return [sys.executable, "-c", OWNER, str(REPO), mode, child, str(tmp_path / "machx.log")]


@pytest.fixture
def engine_env(tmp_path):
    """A private engine directory whose `ie` is the stand-in, a private port and DREAM_ROOT."""
    engine_dir = tmp_path / "engine"
    (engine_dir / "scripts").mkdir(parents=True)
    (engine_dir / "scripts" / "env.sh").write_text("")
    ie = engine_dir / "build" / "src" / "ie"
    ie.parent.mkdir(parents=True)
    ie.write_text(f"#!/bin/sh\nexec {sys.executable} -c '{SLEEPER}'\n")
    ie.chmod(0o755)
    (tmp_path / "home").mkdir()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return {"DREAM_ROOT": str(tmp_path / "root"), "DREAM_MACHX_DIR": str(engine_dir),
            "DREAM_MACHX_PORT": str(port), "HOME": str(tmp_path / "home")}


# --- the owner dies ------------------------------------------------------------------------------

def test_sigkilled_owner_takes_the_engine_down_within_five_seconds(tmp_path, owners):
    owner = owners(helper_owner(tmp_path))
    assert not gone_within(owner.engine, 0.3)            # alive while its owner lives
    owner.proc.send_signal(signal.SIGKILL)
    assert gone_within(owner.engine, 5.0)


def test_owner_exiting_normally_without_stopping_the_engine_takes_it_down(tmp_path, owners):
    owner = owners(helper_owner(tmp_path, "exit"))
    assert owner.proc.wait(10) == 0
    assert gone_within(owner.engine, 5.0)


def test_crashing_owner_takes_the_engine_down(tmp_path, owners):
    owner = owners(helper_owner(tmp_path, "crash"))
    assert owner.proc.wait(10) == -signal.SIGSEGV
    assert gone_within(owner.engine, 5.0)


def test_engine_ignoring_sigterm_is_killed_after_the_grace(tmp_path, owners):
    owner = owners(helper_owner(tmp_path, child=STUBBORN), env={"DREAM_ENGINE_KILL_GRACE_S": "1"})
    assert ignores_sigterm(owner.engine_pid)
    owner.proc.send_signal(signal.SIGKILL)
    assert not gone_within(owner.engine, 0.5)            # SIGTERM alone does not stop this one
    assert gone_within(owner.engine, 4.0)                # the watchdog's SIGKILL does
    assert "SIGKILL" in (tmp_path / "machx.log").read_text()


def test_default_grace_is_ten_seconds(tmp_path, owners):
    owner = owners(helper_owner(tmp_path, child=STUBBORN))
    assert ignores_sigterm(owner.engine_pid)
    owner.proc.send_signal(signal.SIGKILL)
    assert not gone_within(owner.engine, 8.5)
    assert gone_within(owner.engine, 5.0)


def test_spawning_thread_ending_does_not_stop_the_engine(tmp_path, owners):
    """PR_SET_PDEATHSIG follows the spawning THREAD: a worker-thread spawn must not arm it."""
    owner = owners(helper_owner(tmp_path, "thread"))
    assert not gone_within(owner.engine, 1.5)            # the thread ended; the owner and engine live on
    owner.proc.send_signal(signal.SIGKILL)
    assert gone_within(owner.engine, 5.0)                # the watchdog covers this engine instead


# --- while the owner runs, nothing changes --------------------------------------------------------

def test_while_the_owner_runs_its_own_stop_works_and_the_watchdog_leaves(tmp_path, owners):
    owner = owners(helper_owner(tmp_path, "stop"), stdin=subprocess.PIPE)
    assert owner.watchdog is not None
    assert not gone_within(owner.engine, 1.0)
    owner.proc.stdin.write("\n")
    owner.proc.stdin.flush()
    assert owner.proc.stdout.readline().strip() == "stopped"
    assert gone_within(owner.engine, 1.0)
    assert gone_within(owner.watchdog, 3.0)              # no watchdog is left behind
    assert owner.proc.poll() is None                     # and the owner carries on


def test_keep_hot_engine_is_left_running(tmp_path, owners):
    owner = owners(helper_owner(tmp_path, "keep_hot"))
    assert owner.watchdog is None
    owner.proc.send_signal(signal.SIGKILL)
    owner.proc.wait(5)
    assert not gone_within(owner.engine, 2.0)            # deliberately outlives Dream


def test_a_pid_that_is_not_our_child_is_never_watched(tmp_path):
    """The guard arms only on the child it just spawned; any other pid (a fake Popen's, a reused
    one) is noted in the log and left alone -- never watched, never signalled."""
    from types import SimpleNamespace
    sys.path.insert(0, str(REPO))
    from dream.local import engine_guard
    stranger = int(subprocess.run(["sh", "-c", "sleep 60 >/dev/null 2>&1 & echo $!"],
                                  capture_output=True, text=True, check=True).stdout)   # reparented: not our child
    fd = os.pidfd_open(stranger)
    watchdog = None
    try:
        watchdog = engine_guard._watch(SimpleNamespace(pid=stranger), term=True, log_path=tmp_path / "machx.log")
        assert watchdog is None
        assert "is not tied to this process" in (tmp_path / "machx.log").read_text()
    finally:
        if watchdog is not None:
            watchdog.kill()
        kill(fd)


# --- the real launch path -------------------------------------------------------------------------

@pytest.mark.parametrize("keep_hot", [False, True])
def test_machx_serve_ties_the_engine_to_its_owner_unless_keep_hot(engine_env, owners, keep_hot):
    owner = owners([sys.executable, "-c", MACHX_OWNER, str(REPO), "keep_hot" if keep_hot else "owned"],
                   env=engine_env)
    assert not gone_within(owner.engine, 0.5)
    owner.proc.send_signal(signal.SIGKILL)
    owner.proc.wait(5)
    if keep_hot:
        assert not gone_within(owner.engine, 2.0)
    else:
        assert gone_within(owner.engine, 5.0)


def test_desktop_window_crash_takes_the_engine_down(engine_env, owners):
    """The desktop runs the session process in a VTE terminal. A window crash hangs that terminal
    up; SIGHUP ends the session process without its `finally`, and the engine now follows it."""
    owner = owners([sys.executable, "-c", WINDOW, "-c", MACHX_OWNER, str(REPO), "owned"],
                   env=engine_env, stdin=subprocess.PIPE)
    assert not gone_within(owner.engine, 0.5)
    owner.proc.send_signal(signal.SIGKILL)               # the window process crashes
    assert gone_within(owner.engine, 5.0)


# --- the DREAM-110 gate's notes ----------------------------------------------------------------------

@pytest.mark.parametrize("keep_hot", [False, True])
async def test_dream_local_hands_keep_hot_to_the_launch(monkeypatch, tmp_path, keep_hot):
    """Gate note 2: `dream local --keep-hot` reaches machx.serve; without it the engine is tied."""
    from io import StringIO
    from types import SimpleNamespace
    from rich.console import Console
    from dream.local import launcher, machx, preflight
    monkeypatch.setenv("DREAM_MODEL_PRESETS", str(tmp_path / "presets.json"))
    path = tmp_path / "m.gguf"
    path.write_bytes(b"fixture")

    async def choose(*args):
        return {"gpus": 1, "ctx": 8192, "options": {}}, None

    calls = []
    monkeypatch.setattr(launcher, "_choose_model_settings", choose)
    monkeypatch.setattr(machx, "capabilities", lambda p: {"supported": True})
    monkeypatch.setattr(preflight, "check_live", lambda *a: SimpleNamespace(should_load=True, reason="fixture"))
    monkeypatch.setattr(machx, "serve", lambda p, **kw: calls.append(kw) or object())
    monkeypatch.setattr(machx, "wait_ready", lambda proc: False)   # stop right after the launch call
    monkeypatch.setattr(machx, "stop", lambda: None)
    renderer = SimpleNamespace(system=lambda _: None, error=lambda _: None)
    await launcher.serve_and_run(Console(file=StringIO()), renderer, "fixture", path, keep_hot=keep_hot)
    assert [call.get("keep_hot") for call in calls] == [keep_hot]


# The owner builds the real preexec function, and the child sleeps before running it; the test kills
# the owner inside that window, so the child arms only after its owner is already gone.
ORPHANED_BEFORE_ARMING = textwrap.dedent(r'''
    import os, subprocess, sys, time
    sys.path.insert(0, sys.argv[1])
    from dream.local import engine_guard
    forked, ran = sys.argv[2], sys.argv[3]
    arm = engine_guard._parent_death_signal()

    def arm_late():
        with open(forked + ".tmp", "w") as f:
            f.write(str(os.getpid()))
        os.rename(forked + ".tmp", forked)       # the child exists and has not armed yet
        time.sleep(1.5)
        arm()

    subprocess.Popen([sys.executable, "-c", f"open({ran!r}, 'w').close(); import time; time.sleep(600)"],
                     preexec_fn=arm_late, start_new_session=True)
    time.sleep(600)
''')


def test_an_owner_that_dies_before_the_child_arms_never_leaves_an_engine(tmp_path):
    """Gate note 3: prctl(PR_SET_PDEATHSIG) arms nothing when the parent is already gone, so the child
    checks its parent after arming; an orphan must exit before it becomes an engine."""
    forked, ran = tmp_path / "forked", tmp_path / "ran"
    owner = subprocess.Popen([sys.executable, "-c", ORPHANED_BEFORE_ARMING, str(REPO), str(forked), str(ran)])
    child = None
    try:
        for _ in range(500):
            if forked.exists():
                break
            select.select([], [], [], 0.01)
        child = os.pidfd_open(int(forked.read_text()))
        owner.send_signal(signal.SIGKILL)
        owner.wait(5)
        assert gone_within(child, 5.0)                 # it exited ...
        assert not ran.exists()                        # ... before the engine command ever ran
    finally:
        kill(child)
        if owner.poll() is None:
            owner.kill()
        owner.wait(5)
