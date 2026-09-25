"""Fixes #72 and #34 (DREAM-110): a cut-off request no longer bounces the next one.

#72, 2026-09-24 13:19:06: the owner stopped a turn, typed "continue", and it failed 46 ms later
with CoordinationError. The stopped request's lease was "uncertain" while the engine was still
aborting it ("client disconnected mid-generation — aborted after 102 tok").
#34, twice on 2026-09-20: a mid-generation disconnect left the lease "uncertain", and every
later request was refused in ~0.25 s until a human confirmed idle in Controls. The engine's own
/health already said inflight=0.

Now the next request waits up to a bound (default 20 s) for the engine to report nothing in
flight, clears that exact record through the lease's own reconcile (non-blocking lock, same
request id) and proceeds. Anything short of HTTP 200 + status "ok" + inflight 0 + queued 0
keeps the refusal, now with what holds the lease and what to do.
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
import uuid
from pathlib import Path

import pytest

from dream.core.backends import openai_compat
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.inference_coordination import EndpointCoordinator
from dream.core.profiles import PROFILES
from dream.core.providers import Provider
from dream.telemetry.runtime import RunMeter
from test_failure_observability import FakeEngine, engine_server, text_reply  # noqa: F401

REPO = Path(__file__).resolve().parents[1]


async def local_backend(tmp_path, server):
    provider = Provider(key="machx", label="MachX", kind="openai", base_url=server.url,
                        default_api_key="not-needed")
    backend = OpenAICompatBackend(provider=provider, model="fixture", system_prompt="SYSTEM",
                                  tools=[], permission_cb=None)
    backend._coordination_override = EndpointCoordinator(f"loopback:{server.port}", root=tmp_path / "coord")
    backend.runtime_meter = RunMeter("fixture", 1, PROFILES["balanced"], tmp_path / "runtime.jsonl")
    await backend.connect()
    return backend


def runtime_events(tmp_path, kind):
    path = tmp_path / "runtime.jsonl"
    if not path.exists():
        return []
    return [row for row in map(json.loads, path.read_text().splitlines()) if row["event"] == kind]


def lease_file(coordinator):
    return coordinator.root / (coordinator.key + ".json")


def seed(coordinator, state, *, pid=None, age_s=600.0, request_id=None):
    coordinator.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    record = {"schema_version": 1, "state": state, "request_id": request_id or uuid.uuid4().hex,
              "pid": os.getpid() if pid is None else pid, "started_at": time.time() - age_s}
    if state == "uncertain":
        record["updated_at"] = time.time() - age_s + 1
    fd = os.open(lease_file(coordinator), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(record, f)
    return record


def dead_pid():
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


async def turn(backend, prompt="continue"):
    events = [event async for event in backend.ask(prompt)]
    errors = [event.data for event in events if event.kind == "error"]
    result = next(event.data for event in events if event.kind == "result")
    return errors, result


# --- #72: stop, then "continue" ---------------------------------------------------------------

def held_stream(release_after_disconnect_s):
    """First token, then hold the request open until the client goes away; the engine then
    takes a moment to notice and abort before its admission slot frees (inflight -> 0)."""
    def step(h):
        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream")
        h.send_header("Connection", "close")
        h.end_headers()
        h.wfile.write(b'data: {"choices":[{"index":0,"delta":{"content":"working"},"finish_reason":null}]}\n\n')
        h.wfile.flush()
        h.connection.settimeout(0.05)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if h.connection.recv(1) == b"":
                    break                      # the client hung up
            except TimeoutError:
                continue
            except OSError:
                break
        h.server.engine.disconnected_at = time.monotonic()
        time.sleep(release_after_disconnect_s)     # the engine is still finishing its abort
        h.close_connection = True
    return step


async def test_continue_right_after_stop_waits_for_the_engine_and_runs(tmp_path, engine_server):
    engine_server.script = [held_stream(2.0), text_reply("continuing")]
    backend = await local_backend(tmp_path, engine_server)
    try:
        events = backend.ask("write the report")
        async for event in events:
            if event.kind == "text_delta":
                break
        await backend.interrupt()
        with pytest.raises(asyncio.CancelledError):
            await anext(events)
        await events.aclose()
        interrupted = backend.coordination_status()
        assert interrupted["state"] == "uncertain"
        started = time.monotonic()
        errors, result = await turn(backend, "continue")
        waited = time.monotonic() - started
        assert errors == []
        assert result["subtype"] == "success"
        assert engine_server.chats() == 2
        assert 1.0 <= waited < 15.0, waited          # it waited for the abort, not for the full bound
        line, = runtime_events(tmp_path, "lease_reconciled")
        assert line["request_id"] == interrupted["request_id"]
        assert line["inflight"] == 0 and line["queued"] == 0
        assert backend.coordination_status()["state"] == "idle"
    finally:
        await backend.disconnect()


async def test_lease_still_busy_after_the_default_bound_fails_naming_holder_and_action(tmp_path, engine_server):
    """Default bound (20 s): the refusal comes within bound + 2 s and says what holds the lease."""
    engine_server.health = lambda e: (200, {"status": "ok", "inflight": 1, "queued": 0})
    backend = await local_backend(tmp_path, engine_server)
    record = seed(backend._coordinator(), "uncertain", age_s=5)
    try:
        started = time.monotonic()
        errors, result = await turn(backend)
        waited = time.monotonic() - started
        assert 20.0 <= waited <= 22.0, waited
        assert result["is_error"] and result["subtype"] == "request_failed"
        text, = errors
        assert record["request_id"][:8] in text                  # what holds it
        assert f"pid {os.getpid()}" in text
        assert "1 request" in text and "in flight" in text       # what the engine said
        assert "was not sent" in text
        assert "Controls" in text                                 # what to do
        assert engine_server.chats() == 0
        assert backend.coordination_status()["request_id"] == record["request_id"]
        assert openai_compat._LEASE_WAIT_S == 20.0               # the default bound (DREAM_LEASE_WAIT_S unset)
    finally:
        await backend.disconnect()


async def test_a_lease_cleared_as_the_bound_runs_out_still_lets_the_request_run(tmp_path, engine_server, monkeypatch):
    """The engine's idle report arrives after the bound has passed: the record is cleared, so the
    request goes; the bound limits waiting, it does not undo a clear."""
    monkeypatch.setattr(openai_compat, "_LEASE_WAIT_S", 0.5, raising=False)
    engine_server.script = [text_reply("made it")]
    calls = []

    def health(engine):
        calls.append(1)
        if len(calls) == 1:
            return 200, {"status": "ok", "inflight": 1, "queued": 0}
        time.sleep(0.6)                                   # answers after the 0.5 s bound
        return 200, {"status": "ok", "inflight": 0, "queued": 0}

    engine_server.health = health
    backend = await local_backend(tmp_path, engine_server)
    seed(backend._coordinator(), "uncertain")
    try:
        errors, result = await turn(backend)
        assert errors == [] and result["subtype"] == "success"
        assert len(calls) == 2
        assert len(runtime_events(tmp_path, "lease_reconciled")) == 1
    finally:
        await backend.disconnect()


async def test_a_live_request_releasing_the_lease_after_two_seconds_lets_the_next_turn_run(tmp_path, engine_server):
    """The other half of "a lease still being released": a live holder, waited for as before."""
    engine_server.script = [text_reply("after the other request")]
    backend = await local_backend(tmp_path, engine_server)
    other = EndpointCoordinator(f"loopback:{engine_server.port}", root=tmp_path / "coord")
    entered = asyncio.Event()

    async def hold():
        async with other.request():
            entered.set()
            await asyncio.sleep(2.0)

    holder = asyncio.create_task(hold())
    try:
        await entered.wait()
        started = time.monotonic()
        errors, result = await turn(backend)
        assert errors == [] and result["subtype"] == "success"
        assert time.monotonic() - started >= 1.5
        await holder
    finally:
        holder.cancel()
        await asyncio.gather(holder, return_exceptions=True)
        await backend.disconnect()


# --- #34: a disconnect strands the lease ---------------------------------------------------------

async def test_stranded_lease_is_cleared_when_the_engine_reports_idle(tmp_path, engine_server):
    engine_server.script = [text_reply("hello again")]
    backend = await local_backend(tmp_path, engine_server)
    record = seed(backend._coordinator(), "uncertain", age_s=3600)
    try:
        started = time.monotonic()
        errors, result = await turn(backend)
        assert errors == [] and result["subtype"] == "success"
        assert time.monotonic() - started < 3.0               # no waiting: the engine was idle
        line, = runtime_events(tmp_path, "lease_reconciled")  # one line
        assert line["request_id"] == record["request_id"] and line["was"] == "uncertain"
        assert ("GET", "/health") in engine_server.requests
        assert backend.coordination_status()["state"] == "idle"
    finally:
        await backend.disconnect()


async def test_running_lease_of_a_dead_owner_is_cleared_when_the_engine_reports_idle(tmp_path, engine_server):
    """A crashed Dream leaves "running"; with the engine idle it clears without a restart (cf. #53)."""
    engine_server.script = [text_reply("fine")]
    backend = await local_backend(tmp_path, engine_server)
    record = seed(backend._coordinator(), "running", pid=dead_pid(), age_s=600)
    try:
        errors, result = await turn(backend)
        assert errors == [] and result["subtype"] == "success"
        line, = runtime_events(tmp_path, "lease_reconciled")
        assert line["request_id"] == record["request_id"] and line["was"] == "running"
    finally:
        await backend.disconnect()


REFUSED = {
    "inflight": (lambda e: (200, {"status": "ok", "inflight": 1, "queued": 0}), "1 request", True),
    "queued": (lambda e: (200, {"status": "ok", "inflight": 0, "queued": 1}), "1 queued", True),
    "unhealthy": (lambda e: (503, {"status": "unhealthy", "reason": "device lost", "inflight": 0, "queued": 0}),
                  "unhealthy", False),
    "stopping": (lambda e: (503, {"status": "stopping", "inflight": 0, "queued": 0}), "stopping", False),
    "unreachable": (lambda e: None, "did not answer", False),
    "unreported": (lambda e: (200, {"status": "ok"}), "does not report", False),
    "not json": (lambda e: (200, b"<html>ok</html>"), "did not answer", False),
}


@pytest.mark.parametrize("case", list(REFUSED))
async def test_the_refusal_stands_unless_the_engine_reports_idle(case, tmp_path, engine_server, monkeypatch):
    health, said, waits = REFUSED[case]
    monkeypatch.setattr(openai_compat, "_LEASE_WAIT_S", 1.0, raising=False)
    engine_server.health = health
    backend = await local_backend(tmp_path, engine_server)
    record = seed(backend._coordinator(), "uncertain", age_s=120)
    try:
        started = time.monotonic()
        errors, result = await turn(backend)
        waited = time.monotonic() - started
        assert result["is_error"] and result["subtype"] == "request_failed"
        text, = errors
        assert "uncertain" in text and "was not sent" in text
        assert said in text, text
        assert record["request_id"][:8] in text
        if waits:
            assert 1.0 <= waited < 3.5, waited      # busy: waited the bound, then refused
        else:
            assert waited < 1.0, waited             # nothing to wait for: refused at once
        assert engine_server.chats() == 0
        status = backend.coordination_status()
        assert status["state"] == "uncertain" and status["request_id"] == record["request_id"]
        assert not runtime_events(tmp_path, "lease_reconciled")
    finally:
        await backend.disconnect()


async def test_no_idle_signal_without_a_health_check(tmp_path, engine_server):
    """A client that cannot ask /health cannot confirm idle: refused at once, as before."""
    backend = await local_backend(tmp_path, engine_server)
    seed(backend._coordinator(), "uncertain")
    real = backend._client
    class StreamOnly:
        def stream(self, *args, **kwargs):
            raise AssertionError("no request may be sent")
    backend._client = StreamOnly()
    try:
        started = time.monotonic()
        errors, _ = await turn(backend)
        assert time.monotonic() - started < 1.0
        assert "uncertain" in errors[0]
    finally:
        backend._client = real
        await backend.disconnect()


# --- races: the record changes between the health check and the reconcile ----------------------

async def test_a_record_replaced_after_the_health_check_is_only_cleared_after_its_own_check(tmp_path, engine_server):
    """Another client clears A and leaves its own uncertain C while our check is in flight:
    reconciling A must fail (request id changed), and C is cleared only after a fresh idle report."""
    engine_server.script = [text_reply("done")]
    backend = await local_backend(tmp_path, engine_server)
    coordinator = backend._coordinator()
    a = seed(coordinator, "uncertain")
    c_id = uuid.uuid4().hex
    calls = []

    def health(engine):
        calls.append(time.monotonic())
        if len(calls) == 1:
            seed(coordinator, "uncertain", request_id=c_id, age_s=1)
        return 200, {"status": "ok", "inflight": 0, "queued": 0}

    engine_server.health = health
    try:
        errors, result = await turn(backend)
        assert errors == [] and result["subtype"] == "success"
        assert len(calls) == 2
        line, = runtime_events(tmp_path, "lease_reconciled")
        assert line["request_id"] == c_id != a["request_id"]
    finally:
        await backend.disconnect()


async def test_a_live_request_that_took_the_lease_meanwhile_is_waited_for_never_cleared(tmp_path, engine_server):
    """Another Dream request takes the lock and starts B during our check: the reconcile of A
    is refused by the lock, B's record is left alone, and our turn runs after B releases."""
    engine_server.script = [text_reply("done")]
    backend = await local_backend(tmp_path, engine_server)
    coordinator = backend._coordinator()
    seed(coordinator, "uncertain")
    b_id = uuid.uuid4().hex
    seen_by_b = []
    released = threading.Event()

    def other_request():
        fd = os.open(coordinator.root / (coordinator.key + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        seed(coordinator, "running", request_id=b_id, age_s=0)
        holding.set()
        time.sleep(1.5)
        seen_by_b.append(json.loads(lease_file(coordinator).read_text()))
        with open(lease_file(coordinator), "w") as f:
            json.dump({"schema_version": 1, "state": "idle", "request_id": None}, f)
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        released.set()

    holding = threading.Event()

    def health(engine):
        if not holding.is_set():
            threading.Thread(target=other_request, daemon=True).start()
            holding.wait(5)
        return 200, {"status": "ok", "inflight": 0, "queued": 0}

    engine_server.health = health
    try:
        errors, result = await turn(backend)
        assert errors == [] and result["subtype"] == "success"
        assert released.is_set()
        assert seen_by_b[0]["request_id"] == b_id and seen_by_b[0]["state"] == "running"
        assert not runtime_events(tmp_path, "lease_reconciled")
    finally:
        await backend.disconnect()


async def test_stop_while_waiting_for_the_engine_cancels_the_wait(tmp_path, engine_server, monkeypatch):
    engine_server.health = lambda e: (200, {"status": "ok", "inflight": 1, "queued": 0})
    backend = await local_backend(tmp_path, engine_server)
    seed(backend._coordinator(), "uncertain")

    async def consume():
        return [event async for event in backend.ask("continue")]

    task = asyncio.create_task(consume())
    try:
        await asyncio.sleep(0.6)
        assert not task.done()
        await backend.interrupt()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert engine_server.chats() == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await backend.disconnect()


# --- the DREAM-110 gate's notes ----------------------------------------------------------------------

LEASE_WAIT_AT_IMPORT = textwrap.dedent(r'''
    import sys
    sys.path.insert(0, sys.argv[1])
    from dream.core.backends import openai_compat
    print(repr(openai_compat._LEASE_WAIT_S))
''')


@pytest.mark.parametrize("raw,expected,warned", [
    (None, 20.0, False), ("0", 0.0, False), ("3.5", 3.5, False), ("600", 600.0, False),
    ("nan", 20.0, True), ("inf", 20.0, True), ("-inf", 20.0, True), ("abc", 20.0, True),
    ("-5", 20.0, True), ("601", 20.0, True), ("", 20.0, True),
])
def test_dream_lease_wait_s_is_a_bounded_number_or_the_default_with_one_warning(raw, expected, warned):
    """Gate note 1: "nan"/"inf" unbounded the wait, "-5" switched it off, "abc" stopped Dream from starting
    (the module failed to import). Seconds from 0 to 600 are taken; anything else is the default, said once."""
    env = {key: value for key, value in os.environ.items() if key != "DREAM_LEASE_WAIT_S"}
    if raw is not None:
        env["DREAM_LEASE_WAIT_S"] = raw
    out = subprocess.run([sys.executable, "-c", LEASE_WAIT_AT_IMPORT, str(REPO)], env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    assert float(out.stdout.strip()) == expected
    warnings = [line for line in out.stderr.splitlines() if "DREAM_LEASE_WAIT_S" in line]
    assert len(warnings) == (1 if warned else 0), out.stderr


def system_notices(events):
    return [event.data for event in events if event.kind == "system"]


async def test_the_wait_for_the_engine_is_announced_and_so_is_its_end(tmp_path, engine_server):
    """Gate note 4: the up-to-20 s wait after a Stop is no longer a blank pause. One system line when it
    starts and one when it ends, through the Engine's event funnel (what enable_background_filing wires)."""
    engine_server.script = [held_stream(2.0), text_reply("continuing")]
    backend = await local_backend(tmp_path, engine_server)
    notices = []
    backend._background_emit = notices.append
    try:
        events = backend.ask("write the report")
        async for event in events:
            if event.kind == "text_delta":
                break
        await backend.interrupt()
        with pytest.raises(asyncio.CancelledError):
            await anext(events)
        await events.aclose()
        interrupted = backend.coordination_status()
        assert system_notices(notices) == []
        errors, result = await turn(backend, "continue")
        assert errors == [] and result["subtype"] == "success"
        said = system_notices(notices)
        assert len(said) == 2, said
        assert said[0].startswith("Waiting") and interrupted["request_id"][:8] in said[0]
        assert "in flight" in said[0]
        assert "sending" in said[1]
    finally:
        await backend.disconnect()


async def test_a_wait_that_runs_out_is_announced_before_the_refusal(tmp_path, engine_server, monkeypatch):
    monkeypatch.setattr(openai_compat, "_LEASE_WAIT_S", 1.0, raising=False)
    engine_server.health = lambda e: (200, {"status": "ok", "inflight": 1, "queued": 0})
    backend = await local_backend(tmp_path, engine_server)
    seed(backend._coordinator(), "uncertain")
    timeline = []
    backend._background_emit = timeline.append
    try:
        async for event in backend.ask("continue"):
            timeline.append(event)
        kinds = [event.kind for event in timeline if event.kind in ("system", "error")]
        assert kinds == ["system", "system", "error"], kinds    # both lines, then the refusal
        start, end = system_notices(timeline)
        assert start.startswith("Waiting")
        assert end.startswith("Stopped waiting") and "not sent" in end
        assert engine_server.chats() == 0
    finally:
        await backend.disconnect()


async def test_a_stop_during_the_wait_is_announced_as_its_third_ending(tmp_path, engine_server):
    """DREAM-113 (the coordinator's note): a Stop during the wait showed "Waiting up to 20 s..." and never an
    end line, because the cancellation skipped both endings. It is the third: stopped by you, not sent."""
    engine_server.health = lambda e: (200, {"status": "ok", "inflight": 1, "queued": 0})
    backend = await local_backend(tmp_path, engine_server)
    seed(backend._coordinator(), "uncertain")
    notices = []
    backend._background_emit = notices.append

    async def consume():
        return [event async for event in backend.ask("continue")]

    task = asyncio.create_task(consume())
    try:
        for _ in range(100):                       # until the wait has been announced
            if system_notices(notices):
                break
            await asyncio.sleep(0.05)
        assert [line.split()[0] for line in system_notices(notices)] == ["Waiting"]
        await backend.interrupt()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        said = system_notices(notices)
        assert said[1:] == ["Stopped by you while waiting; this request was not sent."], said
        assert engine_server.chats() == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await backend.disconnect()


async def test_a_stop_before_any_wait_was_announced_says_nothing(tmp_path, engine_server):
    """The third ending belongs to a wait the owner saw start: stopped during the first /health question,
    before anything was announced, the request ends silently as before."""
    def slow_health(engine):
        time.sleep(1.0)
        return 200, {"status": "ok", "inflight": 1, "queued": 0}

    engine_server.health = slow_health
    backend = await local_backend(tmp_path, engine_server)
    seed(backend._coordinator(), "uncertain")
    notices = []
    backend._background_emit = notices.append

    async def consume():
        return [event async for event in backend.ask("continue")]

    task = asyncio.create_task(consume())
    try:
        await asyncio.sleep(0.3)                  # inside the first /health question
        await backend.interrupt()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        assert system_notices(notices) == []
        assert engine_server.chats() == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await backend.disconnect()


async def test_no_wait_means_no_notice(tmp_path, engine_server):
    """#34's case: the engine is idle at once, so nothing is announced (the runtime log keeps the clear)."""
    engine_server.script = [text_reply("fine")]
    backend = await local_backend(tmp_path, engine_server)
    seed(backend._coordinator(), "uncertain")
    notices = []
    backend._background_emit = notices.append
    try:
        errors, result = await turn(backend)
        assert errors == [] and result["subtype"] == "success"
        assert system_notices(notices) == []
        assert len(runtime_events(tmp_path, "lease_reconciled")) == 1
    finally:
        await backend.disconnect()


async def test_waiting_for_another_live_request_is_announced_too(tmp_path, engine_server):
    """The lease's own wait for a live Dream request (up to 120 s) was silent as well; its 'waiting'
    event is now wired to the same two lines."""
    engine_server.script = [text_reply("after the other request")]
    backend = await local_backend(tmp_path, engine_server)
    notices = []
    backend._background_emit = notices.append
    other = EndpointCoordinator(f"loopback:{engine_server.port}", root=tmp_path / "coord")
    entered = asyncio.Event()

    async def hold():
        async with other.request():
            entered.set()
            await asyncio.sleep(1.5)

    holder = asyncio.create_task(hold())
    try:
        await entered.wait()
        errors, result = await turn(backend)
        assert errors == [] and result["subtype"] == "success"
        said = system_notices(notices)
        assert len(said) == 2, said
        assert said[0].startswith("Waiting") and "another Dream request" in said[0]
        assert "sending" in said[1]
        await holder
    finally:
        holder.cancel()
        await asyncio.gather(holder, return_exceptions=True)
        await backend.disconnect()


async def test_a_broken_notice_subscriber_never_stops_the_request(tmp_path, engine_server, caplog):
    """The lines are display only: a subscriber that raises is logged and the request still goes."""
    engine_server.script = [text_reply("still sent")]
    reports = []

    def health(engine):
        reports.append(1)
        return 200, {"status": "ok", "inflight": 1 if len(reports) < 3 else 0, "queued": 0}

    engine_server.health = health
    backend = await local_backend(tmp_path, engine_server)
    seed(backend._coordinator(), "uncertain")

    def broken(event):
        raise RuntimeError("renderer fault")

    backend._background_emit = broken
    try:
        errors, result = await turn(backend)
        assert errors == [] and result["subtype"] == "success"
        assert "renderer fault" in caplog.text                 # logged, not swallowed silently
    finally:
        await backend.disconnect()
