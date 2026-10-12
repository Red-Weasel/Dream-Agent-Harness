"""DREAM-253: Dream NEVER sends SIGKILL to a MachX engine or supervisor.

A forced termination landed in GPU work in flight and left bad GPU pages and an unkillable process. The engine's
own orderly stop (SIGTERM, or the front's /admin/shutdown) takes 0.3-1.1 s on the measured runs. So every path that
stops an engine asks once, waits a bounded time, and then says truthfully whether the engine is gone:

* machx.stop()            -- a plain `ie serve` and a supervisor (`ie supervise`)
* engine_guard.main()     -- the owner-death watchdog, with and without the kernel's parent-death SIGTERM
* startup.stop_owned()    -- the desktop bootstrap's exact child, and run()'s cleanup around it

Nothing here starts a process, sends a signal, opens a socket or sleeps: pids, pidfds, the clock, Popen and the HTTP
transport are fakes installed on the module under test. A signal to anything but the fake engine is a failure, and
`no_real_process_signal_or_network` below fails any test that reaches the real thing.

These tests are model-free fixtures: they say what Dream sends and reports, not how a real engine, GPU or kernel
behaves during a live shutdown.
"""
import asyncio
import json
import os
import select
import signal
import socket
import subprocess
import sys
import time
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import dream.config as config
import dream.local.engine_guard as guard
from dream.desktop import startup
from dream.local import machx
from lease_isolation import isolated_load_lock_dir  # noqa: F401  (the launch lock under tmp_path)

PID = 4_190_001                 # a fake: os/signal are replaced on the modules under test, so it is never signalled
PLAIN = ("./build/src/ie", "serve", "/models/fixture.gguf", "--host", "127.0.0.1", "--port", "11435")
SUPERVISOR = ("./build/src/ie", "supervise", "--config", "/layouts/two.json", "--host", "127.0.0.1")


@pytest.fixture(autouse=True)
def no_real_process_signal_or_network(monkeypatch):
    """Every test in this file: the real process spawn, signal senders, pidfd_open and socket connect refuse and are
    counted. The count is checked at teardown too, because production code may swallow the refusal
    (machx._signal_group catches Exception)."""
    reached = []

    def refuse(what):
        def refused(*args, **kwargs):
            reached.append((what, args))
            raise AssertionError(f"a DREAM-253 fixture test reached the real {what}{args!r}")
        return refused
    monkeypatch.setattr(subprocess, "Popen", refuse("subprocess.Popen"))
    for name in ("kill", "killpg", "pidfd_open", "posix_spawn", "system"):
        monkeypatch.setattr(os, name, refuse(f"os.{name}"))
    monkeypatch.setattr(signal, "pidfd_send_signal", refuse("signal.pidfd_send_signal"))
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, refuse(f"socket.{name}"))
    yield
    assert reached == [], f"real process/signal/network calls were made: {reached}"


class _Proxy:
    """A module with some names replaced; every other name is the real module's."""

    def __init__(self, real, **replaced):
        self._real = real
        self.__dict__.update(replaced)

    def __getattr__(self, name):
        return getattr(self._real, name)


class Clock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        assert seconds >= 0
        self.t += seconds


class FakeEngine:
    """An engine process that exists only here. `exit_after`: seconds from its orderly-stop request to its exit
    (None: it never exits, the SIGTERM-resistant engine). SIGKILL is recorded and, like the unkillable process of
    the handoff, does not make it exit."""

    def __init__(self, clock, *, exit_after=None, cmdline=PLAIN, exited=False):
        self.clock, self.pid, self.exit_after, self.cmdline = clock, PID, exit_after, cmdline
        self.exited, self.asked_at = exited, None
        self.signals, self.strays = [], []

    def alive(self):
        if self.exited:
            return False
        return self.asked_at is None or self.exit_after is None or self.clock.t < self.asked_at + self.exit_after

    def ask(self):                                   # the orderly stop: SIGTERM or /admin/shutdown
        if self.asked_at is None:
            self.asked_at = self.clock.t

    def deliver(self, sig):
        self.signals.append(sig)
        if sig == signal.SIGTERM:
            self.ask()

    # --- what the module under test sees as `os` ---
    def kill(self, pid, sig):
        if pid != self.pid:
            self.strays.append((pid, sig))
            raise ProcessLookupError(pid)
        if not self.alive():
            raise ProcessLookupError(pid)
        if sig != 0:
            self.deliver(sig)

    def getpgid(self, pid):
        if pid != self.pid or not self.alive():
            raise ProcessLookupError(pid)
        return self.pid                              # its own session and group (start_new_session=True)

    def killpg(self, pgid, sig):
        if pgid != self.pid:
            self.strays.append((pgid, sig))
            raise ProcessLookupError(pgid)
        self.deliver(sig)


# ------------------------------------------------------------------------------------------------ machx.stop()

class Front:
    """The engine front's HTTP transport: counts POST /admin/shutdown, refuses anything else."""

    def __init__(self, engine, *, answers=True):
        self.engine, self.answers, self.posts = engine, answers, []

    def post(self, url, **kwargs):
        self.posts.append(url)
        if not self.answers:
            raise httpx.ConnectError("connection refused")
        assert url.endswith("/admin/shutdown")
        self.engine.ask()
        return SimpleNamespace(status_code=200)

    def refuse(self, *args, **kwargs):
        raise AssertionError(f"stop() made an HTTP request it has no reason to make: {args}")


@pytest.fixture
def world(monkeypatch, tmp_path):
    """machx with a fake process table, clock and HTTP transport; returns (engine, front, clock, pid_file)."""
    def build(*, exit_after=None, cmdline=PLAIN, exited=False, front_answers=True, record=True):
        clock = Clock()
        engine = FakeEngine(clock, exit_after=exit_after, cmdline=cmdline, exited=exited)
        front = Front(engine, answers=front_answers)
        monkeypatch.setattr(config, "VAR_DIR", tmp_path)
        monkeypatch.setattr(machx, "os", _Proxy(machx.os, kill=engine.kill, killpg=engine.killpg,
                                                getpgid=engine.getpgid))
        monkeypatch.setattr(machx, "time", _Proxy(time, monotonic=clock.monotonic, sleep=clock.sleep))
        monkeypatch.setattr(machx, "httpx", _Proxy(httpx, post=front.post, get=front.refuse, request=front.refuse))
        monkeypatch.setattr(machx, "_zombie", lambda pid: False)                 # no /proc entry for a fake pid
        monkeypatch.setattr(machx, "_cmdline", lambda pid: list(engine.cmdline) if pid == PID else None)
        pid_file = tmp_path / "machx.pid"
        if record:
            pid_file.write_text(str(PID), encoding="utf-8")
        return engine, front, clock, pid_file
    return build


def test_stop_of_a_plain_engine_that_exits_on_sigterm_succeeds(world):
    engine, front, clock, pid_file = world(exit_after=0.5)
    assert machx.stop(timeout=120) is True
    assert engine.signals == [signal.SIGTERM] and engine.strays == []
    assert not engine.alive() and not pid_file.exists()
    assert clock.t < 2.0                                              # left as soon as the engine did


def test_stop_with_nothing_recorded_or_a_stale_record_signals_nothing(world):
    engine, front, clock, pid_file = world(record=False)
    assert machx.stop() is False
    assert engine.signals == [] and engine.strays == [] and front.posts == []

    engine, front, clock, pid_file = world(exited=True)               # the recorded process is gone
    assert machx.stop() is False
    assert not pid_file.exists()                                      # a stale record is cleaned up, not trusted
    assert engine.signals == [] and engine.strays == [] and front.posts == []


def test_stop_never_sigkills_a_plain_engine_that_outlasts_the_wait(world):
    """Escalation 1 of 3: machx.stop() SIGKILLed the plain engine's process group after `timeout`."""
    engine, front, clock, pid_file = world(exit_after=None)
    machx.stop(timeout=5)
    assert signal.SIGKILL not in engine.signals
    assert engine.signals == [signal.SIGTERM]                         # asked once; no retry, nothing stronger
    assert engine.strays == []


def test_stop_reports_a_plain_engine_still_running_and_keeps_its_record(world):
    """The false success: stop() returned True and deleted machx.pid with the engine still alive."""
    engine, front, clock, pid_file = world(exit_after=None)
    assert machx.stop(timeout=5) is False                             # not stopped: say so
    assert engine.alive()
    assert pid_file.read_text(encoding="utf-8").strip() == str(PID)   # the record a later stop needs
    assert machx.left_running() == PID
    assert 5.0 <= clock.t <= 6.0                                      # waited the given time, and no longer

    engine.exited = True                                              # it finishes its stop later
    assert machx.stop(timeout=5) is False                             # nothing left to stop ...
    assert not pid_file.exists()                                      # ... and now the record goes
    assert engine.signals == [signal.SIGTERM]


def test_the_stop_message_does_not_claim_freed_gpu_memory_for_a_plain_engine_still_running(world):
    from dream.local import launcher
    engine, front, clock, pid_file = world(exit_after=None)
    text = launcher._stop_message(machx.stop(timeout=5))
    assert "GPU memory freed" not in text
    assert str(PID) in text and not text.startswith("MachX was already down")


def test_stop_of_a_supervisor_asks_its_front_and_waits(world):
    engine, front, clock, pid_file = world(exit_after=30.0, cmdline=SUPERVISOR)
    assert machx.stop(timeout=5, supervisor_timeout=600) is True      # its own wait, not the plain engine's
    assert len(front.posts) == 1 and engine.signals == [] and engine.strays == []
    assert not pid_file.exists() and 30.0 <= clock.t < 32.0


@pytest.mark.parametrize("front_answers, expected", [(True, []), (False, [signal.SIGTERM])])
def test_a_supervisor_that_outlasts_the_wait_is_left_running_with_its_record(world, front_answers, expected):
    """Already so before DREAM-253, and it stays so: never killed, False, the pid file kept."""
    engine, front, clock, pid_file = world(exit_after=None, cmdline=SUPERVISOR, front_answers=front_answers)
    assert machx.stop(timeout=5, supervisor_timeout=20) is False
    assert engine.signals == expected and engine.strays == []
    assert len(front.posts) == 1
    assert engine.alive() and pid_file.read_text(encoding="utf-8").strip() == str(PID)
    assert machx.left_running() == PID
    assert 20.0 <= clock.t <= 21.0


# --------------------------------------------------------------------------------- engine_guard: the watchdog

OWNER_FD, ENGINE_FD = 71, 72        # fakes: select and signal are replaced on engine_guard, no descriptor is used


class Watched:
    """What the watchdog sees through its two pidfds. The engine's fd reads ready once it has exited."""

    def __init__(self, *, owner_dead, exit_after=None, kernel_term=False, exited=False):
        self.clock = Clock()
        self.engine = FakeEngine(self.clock, exit_after=exit_after, exited=exited)
        self.owner_dead, self.waits = owner_dead, []
        if kernel_term:                              # PR_SET_PDEATHSIG: the kernel sent SIGTERM as the owner died
            self.engine.ask()

    def readable(self, fd):
        return self.owner_dead if fd == OWNER_FD else not self.engine.alive()

    def poll(self):
        return _Poller(self)

    def pidfd_send_signal(self, fd, sig, *args):
        if fd != ENGINE_FD:
            self.engine.strays.append((fd, sig))
            return
        if not self.engine.alive():
            raise ProcessLookupError(fd)
        self.engine.deliver(sig)

    def stray(self, target, sig):                    # os.kill / os.killpg have no business in the watchdog
        self.engine.strays.append((target, sig))


class _Poller:
    def __init__(self, watched):
        self.watched, self.fds = watched, []

    def register(self, fd, mask):
        self.fds.append(fd)

    def poll(self, timeout=None):
        w = self.watched
        ready = [(fd, select.POLLIN) for fd in self.fds if w.readable(fd)]
        if ready:
            return ready
        assert timeout is not None and timeout >= 0, "the watchdog would wait without a bound"
        seconds = timeout / 1000
        w.waits.append(seconds)
        engine = w.engine
        if ENGINE_FD in self.fds and engine.asked_at is not None and engine.exit_after is not None:
            exit_at = engine.asked_at + engine.exit_after
            if exit_at <= w.clock.t + seconds:       # it exits during this wait
                w.clock.t = max(w.clock.t, exit_at)
                return [(ENGINE_FD, select.POLLIN)]
        w.clock.t += seconds
        return []


def watchdog(monkeypatch, watched, *, grace, send_term):
    monkeypatch.setattr(guard, "select", _Proxy(select, poll=watched.poll))
    monkeypatch.setattr(guard, "signal", _Proxy(signal, pidfd_send_signal=watched.pidfd_send_signal))
    monkeypatch.setattr(guard, "os", _Proxy(guard.os, kill=watched.stray, killpg=watched.stray))
    monkeypatch.setattr(guard, "time", _Proxy(time, monotonic=watched.clock.monotonic, sleep=watched.clock.sleep))
    return guard.main([str(OWNER_FD), str(ENGINE_FD), str(grace), "1" if send_term else "0", "1000", str(PID)])


def test_the_watchdog_leaves_without_a_signal_when_the_engine_exits_first(monkeypatch, capsys):
    watched = Watched(owner_dead=False, exited=True)
    assert watchdog(monkeypatch, watched, grace=120.0, send_term=True) == 0
    assert watched.engine.signals == [] and watched.engine.strays == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("send_term, expected", [(True, [signal.SIGTERM]), (False, [])])
def test_owner_death_stops_an_engine_that_honours_sigterm(monkeypatch, capsys, send_term, expected):
    """Parent-death SIGTERM is kept: the watchdog's own when the kernel's was not armed, the kernel's otherwise."""
    watched = Watched(owner_dead=True, exit_after=0.6, kernel_term=not send_term)
    assert watchdog(monkeypatch, watched, grace=120.0, send_term=send_term) == 0
    assert watched.engine.signals == expected and watched.engine.strays == []
    out = capsys.readouterr().out
    assert f"engine pid {PID} stopped" in out and "owner pid 1000 exited" in out
    assert watched.clock.t < 2.0                                      # left with the engine, not after the grace


@pytest.mark.parametrize("grace", [2.0, 600.0], ids=["plain-engine-grace", "supervisor-grace"])
@pytest.mark.parametrize("send_term, expected", [(True, [signal.SIGTERM]), (False, [])],
                         ids=["watchdog-sends-term", "kernel-armed-term"])
def test_owner_death_never_sigkills_an_engine_that_outlasts_the_grace(monkeypatch, capsys, grace, send_term,
                                                                     expected):
    """Escalation 2 of 3: the watchdog SIGKILLed whatever it guarded -- a plain engine after 120 s and a supervisor
    after 600 s (whose children a kill strands on their cards) -- and then left as if that had worked."""
    watched = Watched(owner_dead=True, exit_after=None, kernel_term=not send_term)
    status = watchdog(monkeypatch, watched, grace=grace, send_term=send_term)
    assert signal.SIGKILL not in watched.engine.signals
    assert watched.engine.signals == expected and watched.engine.strays == []
    assert watched.engine.alive()
    assert sum(watched.waits) <= grace + 0.5 and watched.clock.t <= grace + 0.5   # the given grace, then it leaves
    out = capsys.readouterr().out
    assert "sent SIGKILL" not in out
    assert f"engine pid {PID} stopped" not in out                     # no false "stopped"
    assert f"engine pid {PID}" in out and "still running" in out      # the incomplete stop is in the engine log
    assert status != 0                                                # and in the watchdog's own exit status


def test_the_grace_setting_keeps_its_name_and_default(monkeypatch):
    monkeypatch.delenv("DREAM_ENGINE_KILL_GRACE_S", raising=False)
    assert guard.GRACE_ENV == "DREAM_ENGINE_KILL_GRACE_S" and guard._grace_s(None) == 120.0
    monkeypatch.setenv("DREAM_ENGINE_KILL_GRACE_S", "7.5")
    assert guard._grace_s(None) == 7.5
    assert machx.SUPERVISOR_STOP_S == 600.0


@pytest.mark.parametrize("keep_hot", [True, False])
def test_spawn_ties_an_engine_to_its_owner_unless_keep_hot(monkeypatch, keep_hot):
    """keep_hot and the parent-death signal are untouched by DREAM-253: no real process, a fake Popen."""
    spawned, watches = [], []

    def popen(command, **kwargs):
        spawned.append((command, kwargs))
        return SimpleNamespace(pid=PID)

    def watch(proc, **kwargs):
        watches.append((proc.pid, kwargs))
        return "watchdog"
    monkeypatch.setattr(guard, "subprocess", _Proxy(subprocess, Popen=popen))
    monkeypatch.setattr(guard, "_watch", watch)
    proc = guard.spawn(["ie", "serve"], keep_hot=keep_hot, log_path=None, grace_s=600.0, start_new_session=True)
    [(command, kwargs)] = spawned
    assert command == ["ie", "serve"] and kwargs["start_new_session"] is True
    if keep_hot:
        assert kwargs["preexec_fn"] is None and watches == [] and proc.dream_watchdog is None
    else:
        assert callable(kwargs["preexec_fn"])                         # SIGTERM armed for the owner's death
        assert watches == [(PID, dict(term=False, log_path=None, grace_s=600.0))]
        assert proc.dream_watchdog == "watchdog"


# -------------------------------------------------------------------- desktop startup: the bootstrap's own child

class FakeChild:
    """The Popen of the engine the bootstrap launched. `stops`: whether SIGTERM ends it. `terminate_error`: what
    terminate() raises after the child was seen running (ProcessLookupError: it exited in between, the race)."""

    def __init__(self, *, stops, exited=False, terminate_error=None):
        self.pid, self.stops, self.exited, self.calls = PID, stops, exited, []
        self.terminate_error = terminate_error

    def poll(self):
        return 0 if self.exited else None

    def terminate(self):
        self.calls.append("terminate")
        if self.terminate_error is not None:
            self.exited = isinstance(self.terminate_error, ProcessLookupError)
            raise self.terminate_error
        self.exited = self.stops

    def send_signal(self, sig):
        self.calls.append("kill" if sig == signal.SIGKILL else "terminate" if sig == signal.SIGTERM else f"signal {sig}")
        self.exited = self.exited or (sig == signal.SIGTERM and self.stops)

    def kill(self):
        self.calls.append("kill")                    # recorded; the unkillable process of the handoff stays

    def wait(self, timeout=None):
        self.calls.append(("wait", timeout))
        if self.exited:
            return 0
        assert timeout is not None, "an unbounded wait on an engine that does not exit"
        raise subprocess.TimeoutExpired("ie", timeout)

    @property
    def waited(self):
        return [call[1] for call in self.calls if isinstance(call, tuple)]


def test_stop_owned_leaves_a_child_that_already_exited_alone():
    child = FakeChild(stops=True, exited=True)
    startup.stop_owned(child)
    assert child.calls == []


def test_stop_owned_terminates_its_child_and_waits_for_it():
    child = FakeChild(stops=True)
    startup.stop_owned(child)
    assert child.calls[0] == "terminate" and child.calls.count("terminate") == 1 and "kill" not in child.calls
    assert child.waited and all(type(t) in (int, float) and 0 < t <= 600 for t in child.waited)


def test_stop_owned_never_kills_a_child_that_outlasts_the_wait():
    """Escalation 3 of 3: stop_owned() called proc.kill() ten seconds after proc.terminate()."""
    child = FakeChild(stops=False)
    try:
        startup.stop_owned(child)
    except subprocess.TimeoutExpired:
        pass                                         # judged by the test below; this one is about the kill
    assert "kill" not in child.calls
    assert child.calls.count("terminate") == 1       # asked once, no retry
    assert child.waited and all(type(t) in (int, float) and 0 < t <= 600 for t in child.waited)
    assert sum(child.waited) <= 600                  # a bounded wait


def test_stop_owned_says_whether_its_child_is_gone():
    assert startup.stop_owned(FakeChild(stops=True, exited=True)) is True
    assert startup.stop_owned(FakeChild(stops=True)) is True
    assert startup.stop_owned(FakeChild(stops=False)) is False       # no TimeoutExpired: a result, not an accident


def test_stop_owned_takes_a_child_that_exits_between_the_poll_and_the_signal_as_gone():
    """poll() saw it running, terminate() found no such process: it is gone, and the bounded wait reaps it."""
    child = FakeChild(stops=False, terminate_error=ProcessLookupError(PID))
    assert startup.stop_owned(child) is True
    assert child.calls[0] == "terminate" and child.calls.count("terminate") == 1 and "kill" not in child.calls
    assert child.waited and all(type(t) in (int, float) and 0 < t <= 600 for t in child.waited)


@pytest.fixture
def settings():
    return dict(identity='model-key', revision=None, context_limit=32768, max_gpus=2,
                controls=[dict(name='temperature', choices=[]), dict(name='max_tokens', choices=[])],
                selection=dict(ctx=32768, gpus=2, options={'temperature': .7, 'max_tokens': 4096}))


def local_launch(monkeypatch, tmp_path, settings, child, *, ready):
    """startup.run()'s local path up to machx.serve, as tests/test_desktop_startup.py sets it up; the engine is
    `child`. Returns (request, the machx.stop mock that must stay unused).

    The engine log is this test's own by construction, not by conftest's LOG_DIR redirection: _log_file, log_mark
    and launch_error are replaced here, so no run reads or names the owner's machx.log."""
    from dream.local import model_presets
    path = tmp_path / 'fixture.gguf'
    path.write_bytes(b'fixture')
    log = tmp_path / 'fixture-machx.log'
    monkeypatch.setattr(machx, '_log_file', lambda: log)
    monkeypatch.setattr(machx, 'log_mark', lambda: 0)
    monkeypatch.setattr(machx, 'launch_error', lambda since: None)
    monkeypatch.setattr(startup, 'model_settings', lambda _: settings)
    monkeypatch.setattr(startup, 'launch_preflight', lambda *_: 'fit')
    monkeypatch.setattr(model_presets, 'model_key', lambda _: 'model-key')
    monkeypatch.setattr(machx, 'list_models_detailed', lambda: [SimpleNamespace(path=path, size_gb=1, name='fixture')])
    monkeypatch.setattr(machx, 'is_serving', lambda: False)
    monkeypatch.setattr(machx, 'serve', lambda *a, **k: child)
    monkeypatch.setattr(machx, 'wait_ready', lambda _: ready)
    global_stop = Mock()
    monkeypatch.setattr(machx, 'stop', global_stop)
    request = dict(workspace=str(tmp_path), choice=dict(kind='local', provider='machx', path=str(path)),
                   selection=settings['selection'], identity='model-key', revision=None)
    return request, global_stop


def test_a_failed_start_keeps_its_own_error_when_the_child_outlasts_the_wait(monkeypatch, tmp_path, settings, capsys,
                                                                             isolated_load_lock_dir):
    """The cleanup's TimeoutExpired replaced "Model did not become ready": the reason the launch failed was lost."""
    child = FakeChild(stops=False)
    request, global_stop = local_launch(monkeypatch, tmp_path, settings, child, ready=False)
    with pytest.raises(ValueError, match='did not become ready') as failure:
        asyncio.run(startup.run(request, tmp_path / 'status.json'))
    assert "kill" not in child.calls and child.calls.count("terminate") == 1
    global_stop.assert_not_called()                                   # still only the exact child, never the pid file
    told = str(failure.value) + ' '.join(getattr(failure.value, '__notes__', ())) + capsys.readouterr().out
    assert str(PID) in told and 'still running' in told               # the incomplete stop is said, with its pid


def test_a_failed_start_keeps_its_own_error_when_the_child_exits_before_the_signal(monkeypatch, tmp_path, settings,
                                                                                   capsys, isolated_load_lock_dir):
    """The poll -> terminate race in the cleanup: ProcessLookupError must neither replace the readiness error nor
    be reported as an engine still running."""
    child = FakeChild(stops=False, terminate_error=ProcessLookupError(PID))
    request, global_stop = local_launch(monkeypatch, tmp_path, settings, child, ready=False)
    with pytest.raises(ValueError, match='did not become ready') as failure:
        asyncio.run(startup.run(request, tmp_path / 'status.json'))
    assert "kill" not in child.calls and child.calls.count("terminate") == 1
    global_stop.assert_not_called()
    told = str(failure.value) + ' '.join(getattr(failure.value, '__notes__', ())) + capsys.readouterr().out
    assert 'still running' not in told                                # it is gone: nothing false is said
    with startup.local_load_lock():                                   # and the launch lock was released
        pass


def test_a_failed_start_keeps_its_own_error_when_the_cleanup_itself_fails(monkeypatch, tmp_path, settings,
                                                                          isolated_load_lock_dir):
    """Any other failure of the stop is added to the readiness error, never raised in its place."""
    child = FakeChild(stops=False, terminate_error=PermissionError("fixture: not permitted"))
    request, global_stop = local_launch(monkeypatch, tmp_path, settings, child, ready=False)
    with pytest.raises(ValueError, match='did not become ready') as failure:
        asyncio.run(startup.run(request, tmp_path / 'status.json'))
    assert "kill" not in child.calls
    notes = ' '.join(getattr(failure.value, '__notes__', ()))
    assert str(PID) in notes and 'PermissionError' in notes and 'may still be running' in notes
    with startup.local_load_lock():
        pass


def test_the_window_is_told_of_an_engine_still_running_after_a_failed_start(monkeypatch, tmp_path):
    """main() publishes the failure with what the cleanup added to it: the status the window shows."""
    async def run(request, status_path):
        error = ValueError('Model did not become ready.')
        error.add_note(startup._still_running(SimpleNamespace(pid=PID)))
        raise error
    request, status = tmp_path / 'request.json', tmp_path / 'status.json'
    request.write_text('{}')
    monkeypatch.setattr(startup, 'run', run)
    monkeypatch.setattr(sys, 'argv', ['startup', 'run', str(request), str(status)])
    with pytest.raises(SystemExit) as stopped:
        startup.main()
    assert stopped.value.code == 1
    last = json.loads(status.read_text())
    assert last['state'] == 'error' and last['message'].startswith('Model did not become ready.')
    assert str(PID) in last['message'] and 'still running' in last['message']


def test_a_failed_start_releases_this_process_launch_lock_as_before(monkeypatch, tmp_path, settings,
                                                                    isolated_load_lock_dir):
    """The lock's lifetime is unchanged: held through the child's cleanup, released by this process after it
    (the engine holds its own inherited copy for as long as it runs)."""
    child = FakeChild(stops=False)
    request, _ = local_launch(monkeypatch, tmp_path, settings, child, ready=False)
    held = []
    real_wait = child.wait

    def wait(timeout=None):
        with pytest.raises(ValueError, match='Another Dream window'):
            with startup.local_load_lock():
                pytest.fail('a second launcher entered during the child cleanup')
        held.append(True)
        return real_wait(timeout)
    child.wait = wait
    with pytest.raises(ValueError, match='did not become ready'):
        asyncio.run(startup.run(request, tmp_path / 'status.json'))
    assert held
    with startup.local_load_lock():
        pass


def test_a_session_that_ends_says_so_when_its_engine_is_still_running(monkeypatch, tmp_path, settings, capsys,
                                                                      isolated_load_lock_dir):
    """After a normal conversation the status the window shows last must not hide an engine that did not stop."""
    from dream.local import model_presets
    from dream.local import settings as local_settings
    from dream.tui import app
    child = FakeChild(stops=False)
    request, global_stop = local_launch(monkeypatch, tmp_path, settings, child, ready=True)
    monkeypatch.setattr(machx, 'served_model_id', lambda: 'fixture')
    monkeypatch.setattr(machx, 'served_lanes', lambda: None)
    monkeypatch.setattr(local_settings, 'lanes_report', lambda *a, **k: 'lanes')
    monkeypatch.setattr(local_settings, 'session_options', lambda *a, **k: nullcontext())
    monkeypatch.setattr(model_presets, 'Presets', lambda: SimpleNamespace(save=lambda *a, **k: None))
    session = SimpleNamespace(run=AsyncMock())
    monkeypatch.setattr(app, 'App', Mock(return_value=session))
    status = tmp_path / 'status.json'
    asyncio.run(startup.run(request, status))
    session.run.assert_awaited_once()
    assert "kill" not in child.calls and child.calls.count("terminate") == 1
    global_stop.assert_not_called()
    last = json.loads(status.read_text())
    assert str(PID) in last['message'] and 'still running' in last['message']
    assert last['message'] in capsys.readouterr().out


# ------------------------------------------------- desktop startup: a cancelled run, through main() to the status

CANCELLED = 'Startup cancelled. You can choose an agent and try again.'     # main()'s status before DREAM-253


def _sigint_cancels_the_task(monkeypatch):
    """Ctrl-C as asyncio.run() handles it, with no signal sent: its SIGINT handler cancels the main task, run()
    sees CancelledError, and asyncio.run() then raises a NEW KeyboardInterrupt (one with no notes). Here the
    session cancels its own task, and the real asyncio.run()'s CancelledError is turned into that new
    KeyboardInterrupt, as asyncio's Runner does after a SIGINT."""
    def run_as_after_sigint(coro):
        try:
            return asyncio.run(coro)
        except asyncio.CancelledError:
            raise KeyboardInterrupt() from None
    monkeypatch.setattr(startup, 'asyncio', _Proxy(asyncio, run=run_as_after_sigint))

    async def session():
        asyncio.current_task().cancel()
        await asyncio.sleep(0)
        pytest.fail('the cancellation did not reach the session')
    return session


def _interrupt_raised_in_the_task(monkeypatch):
    """KeyboardInterrupt raised inside run() itself: the same exception object reaches main()."""
    async def session():
        raise KeyboardInterrupt()
    return session


INTERRUPTS = pytest.mark.parametrize('interrupt', [_sigint_cancels_the_task, _interrupt_raised_in_the_task],
                                     ids=['sigint-cancels-the-task', 'interrupt-raised-in-the-task'])


def cancelled_main(monkeypatch, tmp_path, settings, child, interrupt):
    """startup.main() for a local launch whose engine (`child`) became ready and whose conversation is then
    interrupted: the real run() and its cleanup, the real main() and its status file. Returns (SystemExit code,
    last status, the machx.stop mock that must stay unused)."""
    from dream.local import model_presets
    from dream.local import settings as local_settings
    from dream.tui import app
    request, global_stop = local_launch(monkeypatch, tmp_path, settings, child, ready=True)
    monkeypatch.setattr(machx, 'served_model_id', lambda: 'fixture')
    monkeypatch.setattr(machx, 'served_lanes', lambda: None)
    monkeypatch.setattr(local_settings, 'lanes_report', lambda *a, **k: 'lanes')
    monkeypatch.setattr(local_settings, 'session_options', lambda *a, **k: nullcontext())
    monkeypatch.setattr(model_presets, 'Presets', lambda: SimpleNamespace(save=lambda *a, **k: None))
    monkeypatch.setattr(app, 'App', Mock(return_value=SimpleNamespace(run=interrupt(monkeypatch))))
    request_file, status = tmp_path / 'request.json', tmp_path / 'status.json'
    request_file.write_text(json.dumps(request))
    monkeypatch.setattr(sys, 'argv', ['startup', 'run', str(request_file), str(status)])
    with pytest.raises(SystemExit) as stopped:
        startup.main()
    return stopped.value.code, json.loads(status.read_text()), global_stop


@INTERRUPTS
def test_a_cancelled_run_tells_the_window_of_an_engine_still_running(monkeypatch, tmp_path, settings, capsys,
                                                                     isolated_load_lock_dir, interrupt):
    """The gap: the cleanup printed the incomplete stop, and main() then published only "Startup cancelled. You
    can choose an agent and try again." -- the last status the window shows hid an engine still on the GPUs."""
    child = FakeChild(stops=False)
    code, last, global_stop = cancelled_main(monkeypatch, tmp_path, settings, child, interrupt)
    assert code == 130                                                # the cancellation's own exit status, kept
    assert last['state'] == 'ended' and last['message'].startswith('Startup cancelled.')
    assert str(PID) in last['message'] and 'still running' in last['message']
    assert last['message'].count('still running') == 1                # said once, not once per source
    assert 'try again' not in last['message']                         # not offered while the engine holds the GPUs
    assert last['message'] in capsys.readouterr().out
    assert "kill" not in child.calls and child.calls.count("terminate") == 1      # asked once, nothing stronger
    global_stop.assert_not_called()                                   # only the exact child, never the pid file
    with startup.local_load_lock():                                   # this process's launch lock was released
        pass


@INTERRUPTS
def test_a_cancelled_run_tells_the_window_when_stopping_the_engine_failed(monkeypatch, tmp_path, settings, capsys,
                                                                          isolated_load_lock_dir, interrupt):
    """A cleanup failure during a cancellation was only a note on an exception main() never read: nothing printed,
    nothing published."""
    child = FakeChild(stops=False, terminate_error=PermissionError("fixture: not permitted"))
    code, last, global_stop = cancelled_main(monkeypatch, tmp_path, settings, child, interrupt)
    assert code == 130                                                # still the cancellation, not the cleanup's error
    assert last['state'] == 'ended' and last['message'].startswith('Startup cancelled.')
    assert str(PID) in last['message'] and 'PermissionError' in last['message']
    assert last['message'].count('may still be running') == 1
    assert last['message'] in capsys.readouterr().out
    assert "kill" not in child.calls and child.calls.count("terminate") == 1      # no retry of the failed stop
    global_stop.assert_not_called()
    with startup.local_load_lock():
        pass


@INTERRUPTS
def test_a_cancelled_run_whose_engine_stops_keeps_the_cancelled_status_as_before(monkeypatch, tmp_path, settings,
                                                                                 isolated_load_lock_dir, interrupt):
    """Unchanged by DREAM-253: the engine stopped, so the status is the old one, word for word, and nothing left
    by an earlier run in this process is repeated in it."""
    startup._cleanup_warnings.append('left by an earlier run')
    child = FakeChild(stops=True)
    code, last, global_stop = cancelled_main(monkeypatch, tmp_path, settings, child, interrupt)
    assert code == 130
    assert last['state'] == 'ended' and last['message'] == CANCELLED
    assert "kill" not in child.calls and child.calls.count("terminate") == 1
    global_stop.assert_not_called()
    with startup.local_load_lock():
        pass


def test_an_interrupt_during_the_stop_itself_is_passed_on_with_the_engine_reported_as_unconfirmed(monkeypatch,
                                                                                                  capsys):
    """A second cancellation while the cleanup waits for the engine: the cancellation goes on (it is not swallowed),
    and what Dream does not know -- whether the engine exited -- is recorded for main() and printed."""
    async def interrupted(function, *args):
        raise asyncio.CancelledError()
    monkeypatch.setattr(startup, 'asyncio', _Proxy(asyncio, to_thread=interrupted))
    monkeypatch.setattr(startup, '_cleanup_warnings', [])
    child = FakeChild(stops=False)
    with pytest.raises(asyncio.CancelledError):
        startup._stop_child(child, 'unused-status-path', ValueError('Model did not become ready.')).send(None)
    [warning] = startup._cleanup_warnings
    assert str(PID) in warning and 'may still be running' in warning and 'did not confirm' in warning
    assert warning in capsys.readouterr().out
    assert "kill" not in child.calls
