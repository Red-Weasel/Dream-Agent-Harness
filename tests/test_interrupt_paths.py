"""Ctrl-C must interrupt the TURN, never the session.

The whole class of bugs here has one root: ``asyncio.run`` installs its own
SIGINT handler that cancels the **main** task. A Ctrl-C mid-await therefore
arrives as ``CancelledError``, not ``KeyboardInterrupt`` — so ``App.run``'s
``except KeyboardInterrupt`` never fires and the cancellation unwinds straight
through the ``finally`` that consolidates memory and unloads the GPUs. Verified
on this interpreter by ``test_asyncio_run_turns_sigint_into_cancellation``.

The two headline tests spawn a real subprocess and send a real SIGINT, because
the defect lives in interpreter/runtime plumbing that no mock reproduces. The
rest drive the App in-process and call the signal handler directly (delivering
SIGINT to the pytest process itself would take the suite down with it).
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import select
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
from types import SimpleNamespace

import pty
import pytest

from dream.telemetry.meter import InferenceMeter
from dream.tui.app import App

# --- the mechanism, stated as an executable fact ------------------------------

_MECHANISM = """
import asyncio, os, signal, threading, time

def fire():
    time.sleep(0.3); os.kill(os.getpid(), signal.SIGINT)

async def main():
    try:
        threading.Thread(target=fire, daemon=True).start()
        await asyncio.sleep(5)
    except KeyboardInterrupt:
        print("KEYBOARDINTERRUPT")
    except asyncio.CancelledError:
        print("CANCELLED")
        raise
    finally:
        print("FINALLY-RAN")

try:
    asyncio.run(main())
except BaseException as e:
    print(f"ESCAPED-{type(e).__name__}")
"""


def test_asyncio_run_turns_sigint_into_cancellation(tmp_path):
    """The premise every fix in this file rests on. If this ever stops holding,
    the SIGINT handling in App can be deleted and the plain try/except restored."""
    script = tmp_path / "mechanism.py"
    script.write_text(_MECHANISM)
    out = subprocess.run([sys.executable, str(script)], capture_output=True,
                         text=True, timeout=30).stdout
    assert "CANCELLED" in out, out
    assert "KEYBOARDINTERRUPT" not in out, out
    assert "FINALLY-RAN" in out, out  # ...and the shutdown path runs on the way out


# --- helpers for the subprocess tests -----------------------------------------

def _drain(master: int, buf: bytearray, timeout: float, state: dict) -> None:
    end = time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([master], [], [], 0.25)
        if not r:
            continue
        try:
            chunk = os.read(master, 65536)
        except OSError:
            return  # child side closed
        if not chunk:
            return
        buf.extend(chunk)
        # Behave like a real terminal: answer every cursor-position request —
        # prompt_toolkit won't render its bottom toolbar without a CPR reply.
        n = buf.count(b"\x1b[6n", state["cpr_seen_at"])
        state["cpr_seen_at"] = len(buf)
        for _ in range(n):
            try:
                os.write(master, b"\x1b[25;1R")
            except OSError:
                return


def _wait_for(master: int, buf: bytearray, needle: bytes, timeout: float,
              state: dict) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if needle in buf:
            return True
        _drain(master, buf, 0.3, state)
    return needle in buf


_REPL_DRIVER = '''
"""The REAL Dream TUI with only the model swapped for a stream that never ends,
so the parent can interrupt a genuinely in-flight turn."""
import asyncio
import os
import sys

import dream.core.backends.openai_compat as oc
from dream.core.backends.base import Event


async def scripted_ask(self, prompt):
    for _ in range(4000):  # long enough that the turn is still running at SIGINT
        yield Event("text_delta", "STREAMING\\n")
        await asyncio.sleep(0.05)
    yield Event("assistant_done", "done")
    yield Event("result", {"is_error": False, "subtype": "success", "usage": {}})


async def probe_none(self):
    return None


oc.OpenAICompatBackend.ask = scripted_ask
oc.OpenAICompatBackend._probe_n_ctx = probe_none

from dream.tui.app import App


async def main():
    app = App(provider="machx", model="scripted", consolidate_on_exit=False,
              workspace=os.environ["DREAM_ROOT"])
    await app.run()


try:
    asyncio.run(main())
except BaseException as e:
    print(f"\\n<<TORN-DOWN {type(e).__name__}>>", flush=True)
    sys.exit(1)
print("\\n<<CLEAN-EXIT>>", flush=True)
'''


def test_ctrl_c_during_a_turn_keeps_the_repl_alive(tmp_path):
    """Ctrl-C mid-turn: the turn stops, the REPL keeps taking input, and the
    session is NOT consolidated or shut down. Only Ctrl-D ends it."""
    driver = tmp_path / "repl_driver.py"
    driver.write_text(_REPL_DRIVER)
    master, slave = pty.openpty()
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 100, 0, 0))
    env = {**os.environ, "DREAM_ROOT": str(tmp_path), "DREAM_MONITOR": "0",
           "TERM": "xterm-256color"}
    env.pop("DREAM_CONSOLIDATE", None)  # the exit must ask, not silently dream
    proc = subprocess.Popen(
        [sys.executable, str(driver)],
        stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True,
    )
    os.close(slave)
    buf = bytearray()
    state = {"cpr_seen_at": 0}
    try:
        assert _wait_for(master, buf, b"Dream", 45, state), "TUI never booted"
        assert _wait_for(master, buf, b"you", 15, state), "prompt never appeared"
        os.write(master, b"hello\n")
        # An unambiguous marker: "tok" would also match the idle toolbar's
        # "tokens Σ 0.0k" and fire the interrupt before the turn even began.
        assert _wait_for(master, buf, b"STREAMING", 20, state), "turn never streamed"

        # A real SIGINT to the running process — exactly what the terminal
        # delivers on Ctrl-C, and the only faithful way to exercise the
        # asyncio.run handler that owns this signal.
        mark = len(buf)
        os.kill(proc.pid, signal.SIGINT)
        # Drain until the acknowledgement: a live footer repainting at 8 fps keeps
        # the pty seconds ahead of what we've read, so a fixed drain reads backlog.
        assert _wait_for(master, buf, b"interrupted", 30, state), \
            "the interrupt was never acknowledged"

        after = bytes(buf[mark:])
        assert b"commit this session" not in after, "Ctrl-C ran the shutdown path"
        assert b"Goodnight" not in after, "Ctrl-C closed the session"
        assert b"<<TORN-DOWN" not in after, "the session died on Ctrl-C"
        assert proc.poll() is None, "the process exited on Ctrl-C"

        # The REPL is still there and still serving.
        os.write(master, b"/help\n")
        assert _wait_for(master, buf, b"Commands:", 15, state), "REPL stopped responding"

        # Ctrl-D is the deliberate exit — and it still asks before consolidating.
        os.write(master, b"\x04")
        assert _wait_for(master, buf, b"commit this session", 15, state), \
            "Ctrl-D skipped the consolidation question"
        os.write(master, b"n\n")
        assert _wait_for(master, buf, b"<<CLEAN-EXIT>>", 20, state), "no clean exit"
        assert proc.wait(timeout=20) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        os.close(master)


_PERM_DRIVER = '''
"""Ctrl-C while a permission prompt owns stdin must decline the tool, not
unwind the session."""
import asyncio
import os
import signal
import sys
import threading
import time

from dream.core import policy
from dream.tui.app import App


async def main():
    app = App(provider="machx", model="m", workspace=os.environ["DREAM_ROOT"])
    app.mode = "ask"
    tool_input = {"path": os.path.join(os.environ["DREAM_ROOT"], "x.txt")}
    assert policy.decide("write_file", tool_input, app.mode, app.workspace)[0] == "ask"

    def fire():
        time.sleep(1.5)
        os.kill(os.getpid(), signal.SIGINT)

    threading.Thread(target=fire, daemon=True).start()
    try:
        allowed = await app._permission("write_file", tool_input)
    except BaseException as e:
        print(f"<<PERM-RAISED {type(e).__name__}>>", flush=True)
        return 2
    print(f"<<PERM-RESULT {allowed}>>", flush=True)
    return 0


sys.exit(asyncio.run(main()))
'''


def test_ctrl_c_at_a_permission_prompt_declines(tmp_path):
    driver = tmp_path / "perm_driver.py"
    driver.write_text(_PERM_DRIVER)
    env = {**os.environ, "DREAM_ROOT": str(tmp_path), "DREAM_MONITOR": "0"}
    # stdin stays OPEN and empty for the whole run, so the question really blocks
    # until the interrupt. (subprocess.run/communicate close it, and the read
    # would then fall out on an immediate EOF — proving nothing.)
    proc = subprocess.Popen(
        [sys.executable, str(driver)], env=env, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        try:
            returncode = proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
            pytest.fail(f"never exited — the read thread still owns stdin:\n"
                        f"{proc.stdout.read()}")
        out = proc.stdout.read()
    finally:
        proc.stdin.close()
        proc.stdout.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    assert "<<PERM-RESULT False>>" in out, out
    assert "<<PERM-RAISED" not in out, out
    assert returncode == 0, out


# --- in-process: the handler, the turn, the meter ------------------------------

class _StubEngine:
    def __init__(self, stream):
        self.store = None
        self._stream = stream
        self.interrupt_calls = 0

    async def ask(self, prompt):
        async for ev in self._stream():
            yield ev

    async def interrupt(self):
        self.interrupt_calls += 1


def _app(tmp_path) -> App:
    app = App(provider="machx", model="m", workspace=tmp_path)
    app.engine = SimpleNamespace(store=None)
    return app


async def test_interrupting_a_turn_returns_control_and_ends_the_meter(tmp_path):
    app = _app(tmp_path)
    streaming = asyncio.Event()

    async def stream():
        yield SimpleNamespace(kind="text_delta", data="hi")
        streaming.set()
        await asyncio.sleep(30)

    app.engine = _StubEngine(stream)
    turn = asyncio.ensure_future(app._ask("go"))
    await asyncio.wait_for(streaming.wait(), 5)

    app._on_sigint(signal.SIGINT, None)  # exactly what the OS calls
    await asyncio.wait_for(turn, 5)  # must return, not raise

    assert app.interrupted
    assert app.engine.interrupt_calls == 1  # the generation was actually stopped
    assert app.meter.state == "idle"  # the pane stops claiming "decoding"
    assert app.meter.live_tps() is None


async def test_a_real_task_cancellation_still_propagates(tmp_path):
    """Ctrl-C is targeted; a genuine teardown of the REPL task must not be
    swallowed by the same except clause."""
    app = _app(tmp_path)
    entered = asyncio.Event()

    async def stream():
        entered.set()
        await asyncio.sleep(30)
        yield SimpleNamespace(kind="text_delta", data="never")

    app.engine = _StubEngine(stream)
    turn = asyncio.ensure_future(app._ask("go"))
    await asyncio.wait_for(entered.wait(), 5)
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn


async def test_permission_read_declines_when_the_read_is_interrupted(tmp_path):
    app = _app(tmp_path)
    app.mode = "ask"
    reading = threading.Event()

    def never_answers(prompt, stop):
        reading.set()
        stop.wait()  # released only when the read is cancelled
        return None

    app._read_line = never_answers
    perm = asyncio.ensure_future(
        app._permission("write_file", {"path": str(tmp_path / "x.txt")}))
    await asyncio.get_running_loop().run_in_executor(None, reading.wait, 5)
    await asyncio.sleep(0)

    app._on_sigint(signal.SIGINT, None)
    assert await asyncio.wait_for(perm, 5) is False


async def test_sigint_targets_the_read_not_the_turn(tmp_path):
    """While a question owns stdin, Ctrl-C answers the question — the turn it
    belongs to keeps running so the model can be told 'denied'."""
    app = _app(tmp_path)
    app.mode = "ask"
    reading = threading.Event()

    def never_answers(prompt, stop):
        reading.set()
        stop.wait()
        return None

    app._read_line = never_answers
    turn_task = asyncio.ensure_future(asyncio.sleep(30))
    try:
        app._interrupt_target = turn_task
        perm = asyncio.ensure_future(
            app._permission("write_file", {"path": str(tmp_path / "x.txt")}))
        await asyncio.get_running_loop().run_in_executor(None, reading.wait, 5)
        await asyncio.sleep(0)
        app._on_sigint(signal.SIGINT, None)
        assert await asyncio.wait_for(perm, 5) is False
        assert not turn_task.cancelled()  # the turn survived the declined tool
        assert app._interrupt_target is turn_task  # ...and is armed again
    finally:
        turn_task.cancel()


@pytest.mark.parametrize("control", ["studio", "sigint"])
async def test_reconnected_studio_stop_owns_turn_while_sigint_declines_prompt(tmp_path, control):
    app = _app(tmp_path)
    app.studio = SimpleNamespace(client_count=0)
    reading, reader_stopped = threading.Event(), threading.Event()
    continued, finish = asyncio.Event(), asyncio.Event()

    def read_until_stopped(prompt, stop):
        reading.set()
        try:
            stop.wait(5)
            return None
        finally:
            reader_stopped.set()

    async def work():
        await app._read_answer("Approve? ")
        continued.set()
        await finish.wait()
        return "continued"

    app._read_line = read_until_stopped
    turn = asyncio.create_task(app._run_turn(work()))
    try:
        assert await asyncio.to_thread(reading.wait, 2)
        app.studio.client_count = 1
        if control == "studio":
            assert await app._runtime_control({"action": "interrupt"}) == {"interrupted": True}
            assert await asyncio.wait_for(asyncio.shield(turn), 2) is None
            assert app.interrupted
            assert not continued.is_set()
        else:
            app._on_sigint(signal.SIGINT, None)
            await asyncio.wait_for(continued.wait(), 2)
            assert not turn.done()
            finish.set()
            assert await asyncio.wait_for(turn, 2) == "continued"
            assert not app.interrupted
        assert await asyncio.to_thread(reader_stopped.wait, 2)
        assert await app._runtime_control({"action": "interrupt"}) == {"interrupted": False}
        assert await app._run_turn(asyncio.sleep(0, result="next turn")) == "next turn"
        assert not app.interrupted
    finally:
        finish.set()
        turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)


async def test_studio_stop_cancels_outermost_nested_turn(tmp_path):
    app = _app(tmp_path)
    entered, continued = asyncio.Event(), asyncio.Event()

    async def inner():
        entered.set()
        await asyncio.Event().wait()

    async def outer():
        await app._run_turn(inner())
        continued.set()

    turn = asyncio.create_task(app._run_turn(outer()))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert await app._runtime_control({"action": "interrupt"}) == {"interrupted": True}
        await asyncio.wait_for(asyncio.shield(turn), 2)
        assert not continued.is_set()
        assert app.interrupted
        assert await app._runtime_control({"action": "interrupt"}) == {"interrupted": False}
    finally:
        turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)


# --- the status panel must never take the prompt down -------------------------

async def test_raising_status_data_does_not_kill_the_toolbar(tmp_path):
    app = _app(tmp_path)
    app.engine = SimpleNamespace(store=object())

    def boom():
        raise ZeroDivisionError("telemetry edge case")

    app._status_data = boom
    value = app._toolbar().value  # prompt_toolkit does NOT contain this
    assert isinstance(value, str)
    for _ in range(50):
        app._toolbar()
    assert len(app.session_errors) == 1  # recorded once, not once per frame
    assert "ZeroDivisionError" in app.session_errors[0][1]


async def test_raising_status_data_does_not_kill_the_live_footer(tmp_path):
    app = _app(tmp_path)
    app.engine = SimpleNamespace(store=object())

    def boom():
        raise RuntimeError("bad frame")

    app._status_data = boom
    assert app._footer() is not None
    for _ in range(20):
        app._footer()
    assert len(app.session_errors) == 1


# --- meter: an interrupted turn must not report a live rate forever -----------

class _Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def test_turn_end_stops_the_meter():
    clock = _Clock()
    m = InferenceMeter(clock=clock)
    m.turn_start()
    for _ in range(3):
        clock.t += 0.5
        m.feed("text_delta", "x" * 40)
    assert m.state == "decoding" and m.live_tps() is not None
    m.turn_end()
    assert m.state == "idle"
    assert m.live_tps() is None
    assert m.snapshot()["live_tps"] is None


def test_live_rate_goes_quiet_when_the_stream_stalls():
    clock = _Clock()
    m = InferenceMeter(clock=clock)
    m.turn_start()
    for _ in range(3):
        clock.t += 0.5
        m.feed("text_delta", "x" * 40)
    assert m.live_tps() == pytest.approx(20.0)
    clock.t += 10.0  # nothing streamed for well over the sliding window
    assert m.live_tps() is None


# --- per-session structures stay bounded --------------------------------------

def _write_event(app, tid, name):
    ws = str(app.workspace)
    app._render_event(SimpleNamespace(
        kind="tool_use",
        data={"name": "write_file", "input": {"path": f"{ws}/{name}"}, "id": tid}))
    app._render_event(SimpleNamespace(
        kind="tool_result",
        data={"name": "write_file", "content": "ok", "is_error": False, "id": tid}))


def test_file_activity_keeps_counts_without_growing_forever(tmp_path):
    app = _app(tmp_path)
    app.engine = SimpleNamespace(
        last_context_tokens=None, session_tokens=0, total_cost_usd=0.0,
        effort=None, backend=SimpleNamespace(n_ctx=None), store=object())
    for i in range(3000):
        _write_event(app, f"t{i}", f"f{i}.txt")
    assert app._status_data()["created"] == 3000  # the tally is still the truth
    assert len(app.activity["created"]) <= 64  # ...but only a tail is retained
    assert len(app._recent_files) <= 64


async def test_pending_activity_is_cleared_at_the_turn_boundary(tmp_path):
    app = _app(tmp_path)
    ws = str(tmp_path)

    async def stream():
        for i in range(200):  # tool_use events whose results never arrive
            yield SimpleNamespace(
                kind="tool_use",
                data={"name": "write_file", "input": {"path": f"{ws}/f{i}"}, "id": i})

    app.engine = _StubEngine(stream)
    await app._ask("go")
    assert app._pending_activity == {}


# --- the GPU sampler join must not block the event loop -----------------------

class _SlowSampler:
    def __init__(self) -> None:
        self.stopped = False
        self.stop_thread = None

    def snapshot(self):
        return None

    def start(self) -> None:
        pass

    def stop(self) -> None:
        self.stop_thread = threading.current_thread()
        time.sleep(0.25)  # GpuSampler.stop() joins its thread with a 2 s timeout
        self.stopped = True


async def test_monitor_off_does_not_stall_the_event_loop(tmp_path):
    app = _app(tmp_path)
    app.gpu = _SlowSampler()
    app.show_monitor = True
    ticks = 0

    async def tick():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    ticker = asyncio.ensure_future(tick())
    try:
        fake = app.gpu
        await app._command("/monitor off")
    finally:
        ticker.cancel()
    assert fake.stopped and app.gpu is None
    assert fake.stop_thread is not threading.current_thread()
    assert ticks >= 5, f"the loop was blocked for the whole join ({ticks} ticks)"


async def test_shutdown_stops_the_sampler_off_thread(tmp_path):
    app = _app(tmp_path)
    app.gpu = _SlowSampler()
    app.engine = None  # even a half-booted app must release the sampler thread
    fake = app.gpu
    await app._shutdown()
    assert fake.stopped and app.gpu is None
    assert fake.stop_thread is not threading.current_thread()
