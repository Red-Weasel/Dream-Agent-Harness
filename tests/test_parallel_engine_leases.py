"""DREAM-151: the local inference lease lets as many requests for one (endpoint, model) run at once as the server says
it runs -- its GET /props `total_slots` (MachX: `ie serve --parallel N`) -- instead of one.

- One slot (every server before lanes, and `--parallel 1`) is today's lease byte for byte: the same lock and record
  files, the same records, the same waits and refusals.
- More slots: slot 0 keeps today's files, slot n > 0 adds `<key>-n.lock` / `<key>-n.json`. A request takes a free slot;
  a slot left "uncertain" by a cut-off request is skipped while another is free, and only a lease whose every slot is
  left over refuses (DREAM-110's settle and Controls' confirm-idle still clear them, one exact record at a time); a
  dead owner's slot is reconciled as fix #53 does.
- The backend reads the slot count from the server's own /props (read at connect; a routed model's from its
  /props?model=), so one Dream session's sub-agents and a second Dream on the same engine run side by side.

The engine here is a real HTTP server on an ephemeral loopback port (never the owner's 11435/11440/11441); the lease
directory is always a temporary one, never /tmp/dream-inference-<uid>.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from lease_isolation import isolated_lease_dir  # noqa: F401  (DREAM-205: leases under tmp_path, never the live folder)

from dream import config
from dream.core import inference_coordination
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.inference_coordination import CoordinationError, EndpointCoordinator
from dream.core.profiles import PROFILES, session_vision
from dream.core.providers import Provider

MODEL = "MiMo-V2.6-Flash-RL"
ENDPOINT = "loopback:19151"


def _files(root):
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


def _record(coordinator, key=None):
    return json.loads((coordinator.root / ((key or coordinator.key) + ".json")).read_text())


def _dead_pid() -> int:
    gone = subprocess.Popen(["true"])
    gone.wait()
    return gone.pid


def _seed(coordinator, key, record):
    coordinator.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(coordinator.root / (key + ".json"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(record, f)


# --- one slot: today's lease, byte for byte -------------------------------------------------------------------

async def test_one_slot_keeps_todays_files_and_records(tmp_path):
    root = tmp_path / "leases"
    c = EndpointCoordinator(ENDPOINT, root=root, model=MODEL)
    assert c.slots == 1
    async with c.request():
        running = _record(c)
        assert list(running) == ["schema_version", "state", "request_id", "pid", "started_at"]
        assert running["state"] == "running" and running["pid"] == os.getpid()
        assert _files(root) == sorted([c.key + ".json", c.key + ".lock"])
    assert (root / (c.key + ".json")).read_bytes() == b'{"schema_version": 1, "state": "idle", "request_id": null}'
    assert _files(root) == sorted([c.key + ".json", c.key + ".lock"])
    assert c.status() == {"schema_version": 1, "state": "idle", "request_id": None, "enabled": True,
                          "waiting": False, "can_reconcile": False}


async def test_one_slot_still_runs_one_request_at_a_time(tmp_path):
    a = EndpointCoordinator(ENDPOINT, root=tmp_path / "leases", model=MODEL)
    b = EndpointCoordinator(ENDPOINT, root=tmp_path / "leases", model=MODEL)
    events = []
    async with a.request():
        with pytest.raises(CoordinationError, match="Timed out"):
            async with b.request(timeout=0.3, on_event=events.append):
                pytest.fail("two requests on a one-slot lease")
    assert [e["state"] for e in events] == ["waiting"]


@pytest.mark.parametrize("reported, logged", [(17, True), (10_000, True), (16, False), (1, False), ("17", False),
                                              (0, False), (None, False)])
def test_a_slot_count_above_dreams_sixteen_is_one_and_said_once(caplog, reported, logged):
    """DREAM-201 gate follow-up 4: an engine reporting more slots than MAX_SLOTS (an engine Dream does not know, or a
    foreign server) is read as 1 -- one request at a time, DREAM-151's rule -- but no longer silently: a warning
    names the count once per process (lease_slots, the rule the backend's _served_slots should share)."""
    import logging
    from pathlib import Path
    from dream.core.inference_coordination import lease_slots, _slots_warned
    _slots_warned.clear()
    expected = reported if reported in (16, 1) else 1
    with caplog.at_level(logging.WARNING, logger="dream.core.inference_coordination"):
        assert lease_slots(reported) == expected
        assert lease_slots(reported) == expected
    warnings = [r for r in caplog.records if "more than Dream's 16" in r.getMessage()]
    assert (len(warnings) == 1) is logged, [r.getMessage() for r in caplog.records]
    if logged:
        assert f"reports {reported} slots" in warnings[0].getMessage() and "using 1" in warnings[0].getMessage()
        assert warnings[0].levelno == logging.WARNING
    c = EndpointCoordinator(ENDPOINT, root=Path("/nonexistent-never-opened"), model=MODEL, slots=reported)
    assert c.capacity == expected


def test_a_leases_capacity_is_read_through_lease_slots(caplog, monkeypatch):
    """DREAM-205 gate KL2 (its mutation M11): a lease reads its slot count through lease_slots, not a copy of the rule,
    so a count above 16 set on a lease is warned about by the lease itself -- once."""
    import logging
    from pathlib import Path
    from dream.core import inference_coordination
    monkeypatch.setattr(inference_coordination, "_slots_warned", set())
    c = EndpointCoordinator(ENDPOINT, root=Path("/nonexistent-never-opened"), model=MODEL, slots=23)
    with caplog.at_level(logging.WARNING, logger="dream.core.inference_coordination"):
        assert c.capacity == 1 and c.capacity == 1
    assert [r.getMessage() for r in caplog.records if "more than Dream's 16" in r.getMessage()] == [
        "the engine reports 23 slots, more than Dream's 16; using 1 (one request at a time)"]


@pytest.mark.parametrize("slots", [0, -1, None, "2", 2.0, True, 17, 10_000])
async def test_a_slot_count_that_is_not_a_sane_integer_is_one(tmp_path, slots):
    a = EndpointCoordinator(ENDPOINT, root=tmp_path / "leases", model=MODEL, slots=slots)
    b = EndpointCoordinator(ENDPOINT, root=tmp_path / "leases", model=MODEL, slots=slots)
    async with a.request():
        with pytest.raises(CoordinationError, match="Timed out"):
            async with b.request(timeout=0.2):
                pytest.fail("an unusable slot count opened a second slot")
    assert _files(tmp_path / "leases") == sorted([a.key + ".json", a.key + ".lock"])


# --- more slots: requests for one model overlap ----------------------------------------------------------------

async def test_two_slots_let_two_requests_for_one_model_overlap_and_a_third_waits(tmp_path):
    root = tmp_path / "leases"
    a, b, c = (EndpointCoordinator(ENDPOINT, root=root, model=MODEL, slots=2) for _ in range(3))
    events = []
    async with a.request():
        started = time.monotonic()
        async with b.request(timeout=2, on_event=events.append):
            assert time.monotonic() - started < 0.5
            assert _record(a)["state"] == "running" and _record(a, a.key + "-1")["state"] == "running"
            assert _record(a)["request_id"] != _record(a, a.key + "-1")["request_id"]
            waited = []
            with pytest.raises(CoordinationError, match="Timed out"):
                async with c.request(timeout=0.3, on_event=waited.append):
                    pytest.fail("a third request on a two-slot lease")
            assert [e["state"] for e in waited] == ["waiting"]
            status = a.status()
            assert status["slots"] == 2 and status["running"] == 2 and status["state"] == "running"
    assert [e["state"] for e in events] == ["running"]                  # b never waited
    assert _files(root) == sorted([a.key + ".json", a.key + ".lock", a.key + "-1.json", a.key + "-1.lock"])
    assert a.status()["state"] == "idle" and a.status()["running"] == 0


async def test_sixteen_slots_let_sixteen_requests_overlap_and_a_seventeenth_waits(tmp_path):
    """DREAM-201: the engine serves up to 16 requests at once (`ie serve --parallel 16`), so a lease sized from its
    /props total_slots 16 admits 16 requests for one model together -- slot 0 with today's files and slots 1-15 with
    `<key>-n.*` -- and the 17th waits."""
    from contextlib import AsyncExitStack
    root = tmp_path / "leases"
    holders = [EndpointCoordinator(ENDPOINT, root=root, model=MODEL, slots=16) for _ in range(16)]
    seventeenth = EndpointCoordinator(ENDPOINT, root=root, model=MODEL, slots=16)
    assert holders[0].capacity == 16
    async with AsyncExitStack() as stack:
        started = time.monotonic()
        for holder in holders:
            await stack.enter_async_context(holder.request(timeout=2))
        assert time.monotonic() - started < 2                          # none of the 16 waited for another
        first = holders[0]
        records = [_record(first)] + [_record(first, first.key + f"-{n}") for n in range(1, 16)]
        assert all(r["state"] == "running" for r in records)
        assert len({r["request_id"] for r in records}) == 16
        status = first.status()
        assert status["slots"] == 16 and status["running"] == 16 and status["left_over"] == 0
        waited = []
        with pytest.raises(CoordinationError, match="Timed out"):
            async with seventeenth.request(timeout=0.3, on_event=waited.append):
                pytest.fail("a 17th request on a 16-slot lease")
        assert [e["state"] for e in waited] == ["waiting"]
    names = _files(root)
    assert len(names) == 32 and first.key + ".json" in names and first.key + "-15.lock" in names
    assert first.status()["state"] == "idle" and first.status()["running"] == 0


async def test_one_coordinator_serves_two_concurrent_requests(tmp_path):
    """The backend keeps one coordinator per (endpoint, model): its sub-agents share it."""
    c = EndpointCoordinator(ENDPOINT, root=tmp_path / "leases", model=MODEL, slots=2)
    inside, peak = 0, 0

    async def one():
        nonlocal inside, peak
        async with c.request(timeout=2):
            inside += 1
            peak = max(peak, inside)
            await asyncio.sleep(0.2)
            inside -= 1
    await asyncio.gather(one(), one())
    assert peak == 2
    c.slots = 1
    await asyncio.gather(one(), one())
    assert peak == 2 and c.status()["state"] == "idle"


async def test_a_one_slot_client_and_a_two_slot_client_share_slot_zero(tmp_path):
    """An older Dream (or a session that has not read the server's slots) uses slot 0 only: the two never run more
    requests than the server has slots."""
    root = tmp_path / "leases"
    old = EndpointCoordinator(ENDPOINT, root=root, model=MODEL)
    new = EndpointCoordinator(ENDPOINT, root=root, model=MODEL, slots=2)
    third = EndpointCoordinator(ENDPOINT, root=root, model=MODEL, slots=2)
    async with old.request():
        async with new.request(timeout=1):
            assert _record(new, new.key + "-1")["state"] == "running"
            with pytest.raises(CoordinationError, match="Timed out"):
                async with third.request(timeout=0.2):
                    pytest.fail("more requests than slots")


async def test_slots_are_kept_per_model(tmp_path):
    root = tmp_path / "leases"
    a = EndpointCoordinator(ENDPOINT, root=root, model="a", slots=2)
    b = EndpointCoordinator(ENDPOINT, root=root, model="b", slots=1)
    async with a.request(), a.request(timeout=1):
        async with b.request(timeout=1):
            assert b.status()["state"] == "running"
    assert a.key != b.key and not any(name.startswith(b.key + "-") for name in _files(root))


# --- fences: cut-off and dead-owner records --------------------------------------------------------------------

async def test_an_uncertain_slot_is_skipped_while_another_is_free_and_reconciled_by_its_id(tmp_path):
    root = tmp_path / "leases"
    c = EndpointCoordinator(ENDPOINT, root=root, model=MODEL, slots=2)
    with pytest.raises(asyncio.CancelledError):
        async with c.request():
            raise asyncio.CancelledError()
    assert _record(c)["state"] == "uncertain"
    events = []
    async with c.request(timeout=1, on_event=events.append):              # slot 1: no refusal, no wait
        assert _record(c, c.key + "-1")["state"] == "running"
    assert [e["state"] for e in events] == ["running"]
    status = c.status()
    assert status["state"] == "uncertain" and status["can_reconcile"] is True and status["slots"] == 2
    with pytest.raises(CoordinationError):
        c.reconcile("0" * 32, confirmed_idle=True)
    c.reconcile(status["request_id"], confirmed_idle=True)
    assert c.status()["state"] == "idle" and _record(c)["state"] == "idle"


async def test_a_lease_whose_every_slot_is_left_over_refuses_as_today(tmp_path):
    root = tmp_path / "leases"
    c = EndpointCoordinator(ENDPOINT, root=root, model=MODEL, slots=2)
    for _ in range(2):
        with pytest.raises(asyncio.CancelledError):
            async with c.request():
                raise asyncio.CancelledError()
    assert _record(c)["state"] == _record(c, c.key + "-1")["state"] == "uncertain"
    with pytest.raises(CoordinationError, match="uncertain"):
        async with c.request(timeout=1):
            pytest.fail("replayed after two cut-off requests")
    first = c.status()["request_id"]
    c.reconcile(first, confirmed_idle=True)
    assert c.status()["state"] == "uncertain" and c.status()["request_id"] != first     # the other one is shown next
    async with c.request(timeout=1):
        pass


async def test_a_live_holder_on_one_slot_and_a_cut_off_record_on_the_other_means_wait(tmp_path):
    root = tmp_path / "leases"
    c = EndpointCoordinator(ENDPOINT, root=root, model=MODEL, slots=2)
    holder = EndpointCoordinator(ENDPOINT, root=root, model=MODEL, slots=2)
    with pytest.raises(asyncio.CancelledError):
        async with c.request():
            raise asyncio.CancelledError()
    async with holder.request():                                           # slot 1: the live request
        with pytest.raises(CoordinationError, match="Timed out"):          # waits for it, never refuses at once
            async with c.request(timeout=0.3):
                pytest.fail("overlap")
    async with c.request(timeout=1):                                       # the live one ended: slot 1 again
        assert _record(c, c.key + "-1")["state"] == "running"


async def test_a_dead_owners_record_on_any_slot_is_reconciled_as_today(tmp_path):
    root = tmp_path / "leases"
    started = time.time() - 600
    c = EndpointCoordinator(ENDPOINT, root=root, model=MODEL, slots=2, server_started_at=lambda: started + 60)
    record = {"schema_version": 1, "state": "running", "request_id": "ab" * 16, "pid": _dead_pid(), "started_at": started}
    _seed(c, c.key, record)
    _seed(c, c.key + "-1", {**record, "request_id": "cd" * 16})
    events = []
    async with c.request(on_event=events.append):
        pass
    assert [e["state"] for e in events] == ["reconciled", "running"]
    async with c.request(on_event=events.append), c.request(timeout=1, on_event=events.append):
        pass
    assert _record(c)["state"] == _record(c, c.key + "-1")["state"] == "idle"
    assert [e["state"] for e in events].count("reconciled") == 2      # slot 0 first, slot 1 when it was needed


# --- the backend: slots from the server's /props --------------------------------------------------------------

class _LanesServer(ThreadingHTTPServer):
    # DREAM-201: 16 sub-agents connect at once; the default listen backlog of 5 dropped some of them under the
    # harness's idle scheduling (a sub-agent's "ReadError"), so the fake engine queues 64 like a real server.
    request_queue_size = 64


class LanesEngine:
    """A MachX `ie serve` stand-in on an ephemeral loopback port: GET /v1/models, /props (`total_slots`), /health and
    POST /v1/chat/completions. A chat request is held `hold` seconds so overlapping ones are seen; `peak` is the most
    chat requests the server had in flight at once. Streamed requests follow `lead` (a list of replies: text or tool
    calls), plain ones answer `side_text`."""

    def __init__(self, slots, *, hold=0.4, vision=None, health_delay=0.0):
        self.slots, self.hold, self.vision, self.health_delay = slots, hold, vision, health_delay
        self.refuse_images = None       # DREAM-155: "sse" (MiMo's stream error) or "400" (vision_not_ready)
        self.lead: list = []
        self.side_text = "found it"
        self.inflight = self.peak = 0
        self.bodies: list[dict] = []
        self.gets: list[str] = []
        self.lock = threading.Lock()
        self.server = _LanesServer(("127.0.0.1", 0), _LanesHandler)
        self.server.daemon_threads = True
        self.server.engine = self
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def props(self):
        body = {"default_generation_settings": {"n_ctx": 32768}, "prompt_cache_slots": 1}
        if self.slots is not None:
            body["total_slots"] = self.slots
        if self.vision is not None:
            body["vision"] = self.vision
        return body


class _LanesHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, status, raw, kind="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        engine = self.server.engine
        with engine.lock:
            engine.gets.append(self.path)
        if self.path == "/v1/models":
            body = {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "local"}]}
        elif self.path == "/props":
            body = engine.props()
        elif self.path == "/health":
            time.sleep(engine.health_delay)
            body = {"status": "ok", "inflight": engine.inflight, "queued": 0, "parallel": engine.slots or 1,
                    "max_queue": 8}
        else:
            return self._send(404, b'{"error": {"message": "not found"}}')
        self._send(200, json.dumps(body).encode())

    def do_POST(self):
        engine = self.server.engine
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        if engine.refuse_images and "image_url" in json.dumps(payload.get("messages")):
            with engine.lock:
                engine.bodies.append(payload)
            if engine.refuse_images == "400":
                return self._send(400, json.dumps({"error": {
                    "message": "vision is not ready: mimo_v2 serves images at --parallel 1 only",
                    "type": "invalid_request_error", "code": "vision_not_ready"}}).encode())
            error = {"error": {"message": "error: mimo_v2 image input: images are served at --parallel 1 only (P4 B4)",
                               "type": "server_error"}}
            return self._send(200, b"data: " + json.dumps(error).encode() + b"\n\ndata: [DONE]\n\n",
                              "text/event-stream")
        with engine.lock:
            engine.bodies.append(payload)
            engine.inflight += 1
            engine.peak = max(engine.peak, engine.inflight)
            reply = engine.lead.pop(0) if payload.get("stream") and engine.lead else None
        try:
            time.sleep(engine.hold)
            usage = {"prompt_tokens": 100, "completion_tokens": 5}
            if not payload.get("stream"):
                message = {"role": "assistant", "content": engine.side_text}
                self._send(200, json.dumps({"choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                                            "usage": usage}).encode())
                return
            reply = reply or {"text": "done"}
            if reply.get("calls"):
                calls = [{"index": i, "id": f"call{i}", "type": "function",
                          "function": {"name": name, "arguments": json.dumps(args)}}
                         for i, (name, args) in enumerate(reply["calls"])]
                chunks = [{"choices": [{"index": 0, "delta": {"tool_calls": calls}, "finish_reason": None}]},
                          {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}]
            else:
                chunks = [{"choices": [{"index": 0, "delta": {"content": reply["text"]}, "finish_reason": "stop"}]}]
            chunks.append({"choices": [], "usage": usage})
            raw = b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"
            self._send(200, raw, "text/event-stream")
        finally:
            with engine.lock:
                engine.inflight -= 1


@pytest.fixture
def lanes_engine():
    made = []

    def make(slots, **kw):
        engine = LanesEngine(slots, **kw)
        made.append(engine)
        return engine
    yield make
    for engine in made:
        engine.close()


@pytest.fixture
def leases(tmp_path, monkeypatch):
    """Every EndpointCoordinator the backend makes lives under tmp_path (never /tmp/dream-inference-<uid>)."""
    leases_root = tmp_path / "leases"

    class Local(EndpointCoordinator):
        def __init__(self, endpoint, *, root=None, **kw):
            super().__init__(endpoint, root=leases_root if root is None else root, **kw)
    monkeypatch.setattr(inference_coordination, "EndpointCoordinator", Local)
    return leases_root


RESEARCHER = SimpleNamespace(name="researcher", description="research", prompt="RESEARCHER PROMPT", tool_names=())


def _backend(engine, *, profile=None, subagents=True):
    provider = Provider(key="machx", label="MachX", kind="openai", base_url=engine.url, default_api_key="not-needed")
    return OpenAICompatBackend(provider=provider, model=MODEL, system_prompt="SYSTEM", tools=[], permission_cb=None,
                               subagents={"researcher": RESEARCHER} if subagents else None, profile=profile)


@pytest.fixture(autouse=True)
def _output(monkeypatch):
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_OVERRIDE", None, raising=False)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS", 4096)
    monkeypatch.delenv("DREAM_MAX_TOKENS", raising=False)


async def test_the_backend_sizes_the_models_lease_from_the_servers_total_slots(lanes_engine, leases):
    engine = lanes_engine(2)
    b = _backend(engine)
    assert b._coordinator().slots == 1                          # nothing read yet: one at a time
    await b.connect()
    assert engine.gets == ["/v1/models", "/props"]               # no new request at connect
    assert b._coordinator().slots == 2 and b._coordinator(MODEL) is b._coordinator()
    b._server_props = {**b._server_props, "total_slots": 1}
    assert b._coordinator().slots == 1
    for unusable in (None, 0, "2", 2.5, True, 100):
        b._server_props = {"default_generation_settings": {"n_ctx": 32768}, "total_slots": unusable}
        assert b._coordinator().slots == 1, unusable
    # A routed model's slots come from its own /props?model= answer (DREAM-144), never the lead's.
    b._server_props = engine.props()
    b._routed_props_cache["small"] = {"default_generation_settings": {"n_ctx": 8192}, "total_slots": 3}
    assert b._coordinator("small").slots == 3 and b._coordinator().slots == 2
    b._routed_props_cache["small"] = None
    assert b._coordinator("small").slots == 1
    await b.disconnect()


@pytest.mark.parametrize("slots, overlap", [(2, True), (1, False), (None, False)])
async def test_two_subagent_requests_for_the_same_model_overlap_only_when_the_server_has_two_slots(
        lanes_engine, leases, slots, overlap):
    engine = lanes_engine(slots)
    b = _backend(engine)
    await b.connect()
    started = time.monotonic()
    results = await asyncio.gather(b._run_subagent("researcher", "look at a"), b._run_subagent("researcher", "look at b"))
    elapsed = time.monotonic() - started
    assert results == [("found it", False), ("found it", False)]
    assert [p["model"] for p in engine.bodies] == [MODEL, MODEL]
    assert engine.peak == (2 if overlap else 1)
    assert (elapsed < 2 * engine.hold) if overlap else (elapsed >= 2 * engine.hold)
    names = _files(leases)
    key = b._coordinator().key
    assert (key + "-1.json" in names) is overlap and key + ".json" in names
    await b.disconnect()


async def test_a_second_dream_on_the_same_engine_runs_beside_the_first(lanes_engine, leases):
    """Two Dream processes attached to one engine: the second's request runs while the first's holds a slot."""
    engine = lanes_engine(2)
    b = _backend(engine)
    await b.connect()
    holder = EndpointCoordinator(f"loopback:{engine.port}", root=leases, model=MODEL, slots=2)
    async with holder.request():
        started = time.monotonic()
        assert await b._run_subagent("researcher", "look") == ("found it", False)
        assert time.monotonic() - started < 2 * engine.hold
    await b.disconnect()


# --- one step's sub-agents run together on an engine with lanes -------------------------------------------------

def test_a_local_engine_with_lanes_lets_one_steps_tasks_go_out_together(lanes_engine):
    engine = lanes_engine(2)
    b = _backend(engine)
    calls = [{"name": "task", "id": f"c{i}", "args": '{"subagent_type": "researcher", "prompt": "x"}'} for i in range(2)]
    assert not b.concurrent_tasks() and b._task_batches(calls) == {}         # before the server said anything
    b._server_props = engine.props()
    assert b.concurrent_tasks() and set(b._task_batches(calls)[0]) == {0, 1}
    b._server_props = {**engine.props(), "total_slots": 1}
    assert not b.concurrent_tasks()


async def _one_step(engine, profile, calls, refused=0):
    """One lead reply holding `calls` task calls, then the final answer: the sub-agents' results in order. The last
    `refused` calls are past the owner's worker cap (DREAM-197): answered in their place, never sent."""
    engine.lead = [{"calls": [("task", {"subagent_type": "researcher", "prompt": f"part {i}"}) for i in range(calls)]},
                   {"text": "all done"}]
    b = _backend(engine, profile=profile)
    await b.connect()
    events = [event async for event in b.ask("research the parts")]
    await b.disconnect()
    results = [e.data for e in events if e.kind == "tool_result"]
    ran = calls - refused
    assert [r["content"] for r in results[:ran]] == ["found it"] * ran and not any(r["is_error"] for r in results[:ran])
    assert len(results) == calls and all(r["is_error"] and "This task call was not run" in r["content"]
                                         for r in results[ran:])
    assert len([p for p in engine.bodies if not p.get("stream")]) == ran
    return engine.peak


# (lanes the server reports, Parallel workers, task calls in one reply, the most sub-agent requests in flight)
@pytest.mark.parametrize("slots, workers, calls, peak", [
    (2, 1, 2, 2),        # the owner's case: Parallel workers 1, two lanes -- they run together, no second setting
    (2, 4, 3, 2),        # the lanes bound it, not Parallel workers
    (3, 1, 3, 3),
    (1, 4, 2, 1),        # one lane: one at a time, as today
    (None, 4, 2, 1),
])
async def test_one_steps_task_calls_run_as_many_at_once_as_the_engine_has_lanes(lanes_engine, leases, slots, workers,
                                                                              calls, peak):
    from dataclasses import replace
    assert await _one_step(lanes_engine(slots), replace(PROFILES["balanced"], max_parallel=workers), calls) == peak


@pytest.mark.parametrize("calls, peak, refused", [(16, 15, 1), (17, 15, 2)])
async def test_sixteen_lanes_run_fifteen_subagents_of_one_reply_together(lanes_engine, leases, calls, peak, refused):
    """DREAM-201: on an engine reporting 16 lanes, the `task` calls of one reply are in flight at once with the owner's
    Parallel workers left at 1. DREAM-197 caps them at the owner's nested.max_workers, at most 15 (sixteen agents with
    the orchestrator): 15 run together and the calls past the cap are answered without running. The fake engine
    holds each request 3 s: starting 15 sub-agents takes the harness a while, and a shorter hold under-counts the peak
    (an artifact of the fake, seen at 0.4 s)."""
    from dataclasses import replace
    from dream.core.profiles import settings_path
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "nested": {"max_workers": 15}}))
    profile = replace(PROFILES["balanced"], max_parallel=1)
    assert await _one_step(lanes_engine(16, hold=3.0), profile, calls, refused=refused) == peak


async def test_with_the_owners_saved_parallel_workers_of_one_two_lanes_still_run_two(lanes_engine, leases):
    """The owner's runtime-settings.json holds overrides.max_parallel 1; the local profile resolves to it."""
    from dream.core.profiles import resolve_profile, settings_path
    from dream.core.providers import get_provider
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "profile": "auto", "overrides": {"max_parallel": 1}}))
    profile = resolve_profile(get_provider("machx"), None, model=MODEL)
    assert profile.max_parallel == 1
    assert await _one_step(lanes_engine(2), profile, 2) == 2
    assert await _one_step(lanes_engine(1), profile, 2) == 1


# --- what the model is told -------------------------------------------------------------------------------------

# The `task` tool's sentence at one lane -- DREAM-197 put the owner's worker cap in it (7 unless set) -- and the
# delegating section's sentence as it was before DREAM-151: one lane keeps it.
ONE_LANE = "Up to 7 subagents per reply; one runs at a time on this engine, the rest queue."
TODAYS_TASK = ("Delegate a scoped, self-contained subtask to a specialist subagent that runs in its OWN isolated "
               "context on this engine and returns a summary. " + ONE_LANE + " Use this "
               "to keep your own context clean on repetitive or research-heavy work — the subagent does the messy "
               "gathering and hands back only the result.\nAvailable subagents:\n- researcher: research")
TODAYS_DELEGATING = "Subagents run one at a time on this engine, not in parallel."


def _task_description(b):
    [schema] = [s for s in b.tool_schemas if s["function"]["name"] == "task"]
    return schema["function"]["description"]


@pytest.mark.parametrize("slots", [1, None])
async def test_the_task_tool_is_todays_at_one_lane(lanes_engine, leases, slots):
    engine = lanes_engine(slots)
    b = _backend(engine)
    before = json.dumps(b.tool_schemas, sort_keys=True)
    assert _task_description(b) == TODAYS_TASK
    await b.connect()
    assert json.dumps(b.tool_schemas, sort_keys=True) == before
    await b.disconnect()


async def test_the_task_tool_names_the_lanes_and_the_request_carries_it(lanes_engine, leases):
    engine = lanes_engine(2)
    b = _backend(engine)
    assert _task_description(b) == TODAYS_TASK                            # before the server has said anything
    await b.connect()
    text = _task_description(b)
    assert text == TODAYS_TASK.replace(ONE_LANE, "Up to 7 subagents per reply; 2 run at once on this engine, the rest "
                                                 "queue.")
    engine.lead = [{"text": "ok"}]
    [event async for event in b.ask("hello")]
    [sent] = [t for t in engine.bodies[0]["tools"] if t["function"]["name"] == "task"]
    assert sent["function"]["description"] == text
    await b.disconnect()


async def test_sixteen_lanes_size_the_lease_and_the_task_tool_alike(lanes_engine, leases):
    """DREAM-201: an engine reporting /props total_slots 16 gives 16 everywhere the backend reads lanes: the lease's
    slots, subagent_lanes(), concurrent_tasks() and the `task` tool's sentence."""
    engine = lanes_engine(16)
    b = _backend(engine)
    await b.connect()
    assert b._coordinator().slots == 16 and b._coordinator().capacity == 16
    assert b.subagent_lanes() == 16 and b.concurrent_tasks()
    assert _task_description(b) == TODAYS_TASK.replace(ONE_LANE, "Up to 7 subagents per reply, all running at once on "
                                                                 "this engine.")      # the cap (7) is below 16 lanes
    await b.disconnect()


async def test_seventeen_lanes_read_as_one_through_the_backend_and_are_warned_about(lanes_engine, leases, caplog,
                                                                                   monkeypatch):
    """DREAM-201 gate follow-up 4 on the connect path (the DREAM-205 builder's ask): the backend's _served_slots reads
    /props total_slots through lease_slots, so an engine reporting 17 -- one more than Dream's 16 -- is one request at a
    time everywhere the backend reads lanes, as before, and the count is no longer dropped silently: warned once."""
    import logging
    monkeypatch.setattr(inference_coordination, "_slots_warned", set())
    engine = lanes_engine(17)
    b = _backend(engine)
    with caplog.at_level(logging.WARNING, logger="dream.core.inference_coordination"):
        await b.connect()
        assert b._coordinator().slots == 1 and b._coordinator().capacity == 1
        assert b.subagent_lanes() == 1 and not b.concurrent_tasks()
    assert _task_description(b) == TODAYS_TASK
    assert [r.getMessage() for r in caplog.records if "more than Dream's 16" in r.getMessage()] == [
        "the engine reports 17 slots, more than Dream's 16; using 1 (one request at a time)"]
    await b.disconnect()


@pytest.fixture
def hermetic(tmp_path, monkeypatch):
    """A started Engine writes only under tmp_path and starts no MCP server (test_vision_readiness's pattern)."""
    import dream.core.engine as engine_mod
    from dream.media import blender_live
    from dream.tools import context as tool_context
    data, mem, var = tmp_path / "data", tmp_path / "memory", tmp_path / "var"
    for name, value in dict(SEMANTIC_MEMORY=False, DATA_DIR=data, SESSIONS_DIR=data / "sessions",
                            DB_PATH=data / "dream.db", MEMORY_DIR=mem, SEMANTIC_DIR=mem / "semantic",
                            PROCEDURAL_DIR=mem / "procedural", EPISODIC_DIR=mem / "episodic",
                            IDENTITY_FILE=mem / "IDENTITY.md", THREADS_FILE=mem / "THREADS.md",
                            INSTRUCTIONS_FILE=mem / "INSTRUCTIONS.md", VAR_DIR=var, LOG_DIR=var / "logs",
                            LOOP_DIR=var / "loops", SCREENSHOT_DIR=var / "screenshots",
                            MCP_CONFIG_PATH=tmp_path / "no-mcp.json").items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(engine_mod, "get_browser", lambda: None)
    monkeypatch.setattr(blender_live, "managed_servers", lambda workspace: ([], []))
    monkeypatch.setattr(tool_context, "_CTX", tool_context._CTX)
    workspace = tmp_path / "project"
    workspace.mkdir()
    return workspace


async def _started_engine(engine, workspace):
    from dataclasses import replace
    from dream.core.engine import Engine
    from dream.core.providers import get_provider
    started = Engine(provider=replace(get_provider("machx"), base_url=engine.url), model=MODEL, workspace=workspace)
    await started._start()
    return started


def _delegating(prompt):
    section = prompt[prompt.index("## Delegating (subagents)"):]
    return section[:section.find("\n## ", 3)] if "\n## " in section[3:] else section


@pytest.mark.parametrize("slots", [1, None])
async def test_the_system_prompt_is_todays_at_one_lane(lanes_engine, leases, hermetic, slots):
    started = await _started_engine(lanes_engine(slots), hermetic)
    try:
        prompt = started._assembled_system_prompt
        assert started.backend.messages[0]["content"] == prompt
        assert _delegating(prompt).rstrip().endswith(TODAYS_DELEGATING) and "Up to" not in _delegating(prompt)
        assert ONE_LANE in _task_description(started.backend)
    finally:
        await started._cleanup()


@pytest.mark.parametrize("slots, sentence", [
    (2, "Up to 7 subagents per reply; 2 run at once on this engine, the rest queue."),
    (16, "Up to 7 subagents per reply, all running at once on this engine."),
])
async def test_at_several_lanes_the_system_prompt_leaves_the_number_to_the_task_tool(lanes_engine, leases, hermetic,
                                                                                    slots, sentence):
    """The delegating section names no number above one lane (DREAM-197's gate, finding 2): the `task` tool says how
    many per reply and how many at once, so the prompt stays the same bytes when the lanes or the worker cap change."""
    started = await _started_engine(lanes_engine(slots), hermetic)
    try:
        prompt = started._assembled_system_prompt
        assert started.backend.messages[0]["content"] == prompt
        section = _delegating(prompt)
        assert section.rstrip().endswith("Several subagents can run at the same time on this engine; the `task` tool "
                                          "says how many per reply. Independent subtasks can go out together in one "
                                          "reply.")
        assert TODAYS_DELEGATING not in section and "Up to" not in section
        assert sentence in _task_description(started.backend)
    finally:
        await started._cleanup()


REMOTE, LOOPBACK = "https://api.example.com/v1", "http://127.0.0.1:8080/v1"


@pytest.mark.parametrize("key", ["openai", "xai"])
@pytest.mark.parametrize("url, saved, parallel, sentence, task", [
    pytest.param(REMOTE, None, 4, "Several `task` calls in one reply run at the same time on this provider; "
                 "independent subtasks can go out together.", "4 run at once on this provider",
                 id="remote-frontier-4"),
    pytest.param(REMOTE, 1, 1, "Subagents run one at a time on this provider, not in parallel.",
                 "one runs at a time on this provider", id="remote-saved-1"),
    pytest.param(LOOPBACK, None, 1, "Subagents run one at a time on this engine, not in parallel.",
                 "one runs at a time on this engine", id="loopback-default-1"),
    pytest.param(LOOPBACK, 4, 4, "Subagents run one at a time on this engine, not in parallel.",
                 "one runs at a time on this engine", id="loopback-explicit-4"),
])
def test_a_cloud_keys_delegating_sentence_says_how_its_calls_really_run(hermetic, monkeypatch, key, url, saved,
                                                                        parallel, sentence, task):
    """openai and xai: one reply's `task` calls go out together only to a remote server, up to Parallel workers
    (profile.max_parallel); at a loopback address they run one at a time (OpenAICompatBackend.concurrent_tasks). The
    system prompt follows the `task` tool's source and says it in the same words, "on this provider" or "on this
    engine" (DREAM-197's gate, rounds 2 and 3)."""
    from dataclasses import replace
    from dream.core.engine import Engine
    from dream.core.profiles import settings_path
    from dream.core.providers import get_provider
    from dream.memory.store import MemoryStore
    for name in ("DREAM_PROFILE", "DREAM_MAX_PARALLEL"):
        monkeypatch.delenv(name, raising=False)
    if saved is not None:
        settings_path().parent.mkdir(parents=True, exist_ok=True)
        settings_path().write_text(json.dumps({"version": 1, "overrides": {"max_parallel": saved}}))
    provider = replace(get_provider(key), base_url=url)
    session = Engine(provider=provider, model="fixture", workspace=hermetic)
    assert session.profile.max_parallel == parallel
    session._mcp = SimpleNamespace(servers=[])
    session._session_tools, session._local_subs = {}, {"researcher": RESEARCHER}
    session._built_tools = {"tools": [], "server": {}, "exempt_tool_ids": []}
    session.store = MemoryStore(hermetic / "store.db")
    session.store.start_session(session.session_id)
    try:
        assert _delegating(str(session._system_prompt())).rstrip().endswith(sentence)
    finally:
        session.store.close()
    backend = OpenAICompatBackend(provider=provider, model="fixture", system_prompt="S", tools=[], permission_cb=None,
                                  subagents={"researcher": RESEARCHER}, profile=session.profile)
    assert task in _task_description(backend)


# --- a slot a cut-off request left comes back (the DREAM-151 gate's repro, g151/probe/after_stop.py) --------------

class Recorder:
    """The runtime meter's surface the backend calls (test_cache_friendly_head's Meter): every record kept."""

    def __init__(self):
        self.records = []

    def record(self, event, **fields):
        self.records.append((event, fields))

    def check(self):
        return None

    def usage(self, usage, phase="lead"):
        return None

    def before_tool(self, *a, **k):
        return None


async def _cut_one(b, engine):
    """A sub-agent request cut off mid-generation (a Stop); the engine finishes it on its own."""
    task = asyncio.create_task(b._run_subagent("researcher", "cut"))
    await asyncio.sleep(engine.hold / 2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    for _ in range(200):
        if engine.inflight == 0:
            break
        await asyncio.sleep(0.02)
    assert engine.inflight == 0


async def test_a_slot_a_cut_off_request_left_is_cleared_once_the_engine_is_idle(lanes_engine, leases):
    engine = lanes_engine(2)
    b = _backend(engine)
    b.runtime_meter = Recorder()
    await b.connect()
    fresh = await asyncio.gather(b._run_subagent("researcher", "a"), b._run_subagent("researcher", "b"))
    assert fresh == [("found it", False)] * 2 and engine.peak == 2
    await _cut_one(b, engine)
    assert b.coordination_status()["left_over"] == 1
    for round_ in range(3):
        engine.peak, asked = 0, engine.gets.count("/health")
        results = await asyncio.gather(b._run_subagent("researcher", f"c{round_}"),
                                       b._run_subagent("researcher", f"d{round_}"))
        assert results == [("found it", False)] * 2 and engine.peak == 2, round_        # both lanes again
        assert engine.gets.count("/health") - asked == (1 if round_ == 0 else 0)   # asked once, then never
    status = b.coordination_status()
    assert status["state"] == "idle" and status["left_over"] == 0 and status["running"] == 0
    [(event, fields)] = [r for r in b.runtime_meter.records if r[0] == "lease_reconciled"]
    assert fields["was"] == "uncertain" and fields["inflight"] == 0
    await b.disconnect()


async def test_a_left_over_slot_stays_while_the_engine_is_busy(lanes_engine, leases):
    """Three lanes: a live request in flight, a second one cut off beside it (the engine still runs both). The next
    request takes the free slot, asks /health once, hears requests in flight and clears nothing; once the engine is
    idle, a later request clears it."""
    engine = lanes_engine(3, hold=0.6)
    b = _backend(engine)
    await b.connect()
    live = asyncio.create_task(b._run_subagent("researcher", "live"))
    cut = asyncio.create_task(b._run_subagent("researcher", "cut"))
    for _ in range(100):
        if engine.inflight == 2:
            break
        await asyncio.sleep(0.01)
    cut.cancel()
    await asyncio.gather(cut, return_exceptions=True)
    assert engine.inflight == 2 and b.coordination_status()["left_over"] == 1
    asked = engine.gets.count("/health")
    assert await b._run_subagent("researcher", "next") == ("found it", False)
    assert engine.gets.count("/health") - asked == 1                     # asked once, heard work in flight
    assert b.coordination_status()["left_over"] == 1
    assert await live == ("found it", False)
    for _ in range(200):
        if engine.inflight == 0:
            break
        await asyncio.sleep(0.02)
    await b._run_subagent("researcher", "later")                           # the engine is idle now
    assert b.coordination_status()["left_over"] == 0
    await b.disconnect()


async def test_a_stop_while_dream_asks_the_engine_frees_the_slot_it_took(lanes_engine, leases):
    engine = lanes_engine(2, health_delay=1.0)
    b = _backend(engine)
    await b.connect()
    await _cut_one(b, engine)
    task = asyncio.create_task(b._run_subagent("researcher", "stopped"))
    for _ in range(200):
        if "/health" in engine.gets:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    states = sorted(record["state"] for record in b._coordinator().records())
    assert states == ["idle", "uncertain"]                                 # only the first cut-off request's
    assert not any(p.get("messages", [{}])[-1].get("content") == "stopped" for p in engine.bodies)


async def test_one_lane_never_asks_before_sending(lanes_engine, leases):
    """One slot keeps today's path: a cut-off record there refuses the next request, and DREAM-110's settle asks
    /health then; a request that finds its one slot free asks nothing."""
    engine = lanes_engine(1)
    b = _backend(engine)
    await b.connect()
    await b._run_subagent("researcher", "a")
    assert "/health" not in engine.gets
    await _cut_one(b, engine)
    await b._run_subagent("researcher", "b")
    assert engine.gets.count("/health") == 1 and b.coordination_status()["state"] == "idle"


async def test_status_counts_live_requests_and_left_over_records_apart(tmp_path):
    root = tmp_path / "leases"
    c = EndpointCoordinator(ENDPOINT, root=root, model=MODEL, slots=3)
    record = {"schema_version": 1, "state": "running", "request_id": "ab" * 16, "pid": _dead_pid(),
              "started_at": time.time() - 60}
    _seed(c, c.key + "-2", record)
    async with c.request():                                               # slot 0: a live request
        status = c.status()
        assert status["running"] == 1 and status["left_over"] == 1 and status["slots"] == 3
        assert status["request_id"] == "ab" * 16 and status["can_reconcile"] is True     # the gone owner's first
    with pytest.raises(asyncio.CancelledError):
        async with c.request():
            raise asyncio.CancelledError()
    status = c.status()
    assert status["state"] == "uncertain" and status["left_over"] == 2 and status["running"] == 0


async def test_an_engine_that_now_serves_fewer_lanes_lowers_the_count_but_never_raises_it(lanes_engine, leases):
    engine = lanes_engine(2)
    b = _backend(engine)
    await b.connect()
    generation = b._request_generation()
    assert b._coordinator().slots == 2
    engine.slots = 1                                                        # restarted with --parallel 1
    await b._engine_activity(generation)
    assert b._coordinator().slots == 1 and not b.concurrent_tasks() and b.subagent_lanes() == 1
    engine.slots = 4                                                        # and again with more
    await b._engine_activity(generation)
    assert b._coordinator().slots == 1                                      # a higher count waits for a new connect
    await b.connect()
    assert b._coordinator().slots == 4
    await b.disconnect()


# --- images: a server that runs several lanes takes none --------------------------------------------------------

MACHX = SimpleNamespace(key="machx", multimodal=True)
SEES = {"features": {"vision": True}, "load": [], "sampling": []}        # no architecture: a model Dream cannot name
MIMO_SEES = {**SEES, "architecture": "mimo_v2"}
V41_SEES = {**SEES, "architecture": "deepseek_v41"}


@pytest.mark.parametrize("slots", [None, 1])
def test_images_follow_the_servers_readiness_at_one_lane(slots):
    props = {"vision": {"ready": True, "reason": ""}, **({"total_slots": slots} if slots else {})}
    decision = session_vision(MACHX, None, MODEL, capabilities=SEES, props=props)
    assert decision["enabled"] is True and decision["state"] == "on"


def test_images_are_off_while_the_server_runs_more_than_one_lane():
    props = {"vision": {"ready": True, "reason": ""}, "total_slots": 2}
    decision = session_vision(MACHX, None, MODEL, capabilities=SEES, props=props)
    assert decision["enabled"] is False and decision["state"] == "off"
    assert "2 requests at once" in decision["source"] and "parallel 1" in decision["source"]
    # The owner's own profile switch cannot make the engine take them: it refuses every image at --parallel > 1.
    forced = session_vision(MACHX, SimpleNamespace(vision=True, vision_helper=None), MODEL, capabilities=SEES,
                            props=props)
    assert forced["enabled"] is False and "parallel 1" in forced["source"]
    # A vision helper still describes them (DREAM-098): the images go to that provider, never to this server.
    helper = session_vision(MACHX, SimpleNamespace(vision=None, vision_helper="openai"), MODEL, capabilities=SEES,
                            props=props)
    assert helper["state"] == "borrowed" and "parallel 1" in helper["source"]


async def test_a_connected_backend_with_lanes_offers_no_images(lanes_engine, leases):
    engine = lanes_engine(2, vision={"ready": True, "reason": ""})
    b = _backend(engine)
    b._local_capabilities = SEES
    await b.connect()
    assert b.vision_status()["enabled"] is False and b.provider.multimodal is False
    one = lanes_engine(1, vision={"ready": True, "reason": ""})
    c = _backend(one)
    c._local_capabilities = SEES
    await c.connect()
    assert c.vision_status()["enabled"] is True
    [note] = b._settings_notices
    assert "Images are off" in note and "engine.parallel 1" in note
    assert c._settings_notices == []
    await b.disconnect()
    await c.disconnect()


# --- DREAM-154: images on lanes are MiMo's rule, not DeepSeek-V4.1's ---------------------------------------------

READY = {"ready": True, "reason": ""}
# The engine's follow-up for MiMo at --parallel > 1 (not built yet): /props says not ready, with a reason.
MIMO_NOT_ON_LANES = {"ready": False, "reason": "mimo_v2 image input: images are served at --parallel 1 only"}


def test_v41_keeps_images_on_lanes_when_its_server_says_they_are_ready():
    """V4.1's engine serves an image prompt at --parallel N (it prefills in a serial turn, P4 B6b), so the server's own
    readiness decides there as at one lane -- and the owner's profile switch is honoured."""
    decision = session_vision(MACHX, None, MODEL, capabilities=V41_SEES, props={"vision": READY, "total_slots": 2})
    assert decision == {"state": "on", "enabled": True, "source": "The model server reports image input is ready"}
    off = session_vision(MACHX, None, MODEL, capabilities=V41_SEES,
                         props={"vision": {"ready": False, "reason": "vision is disabled (IE_DS41_VISION=0)"},
                                "total_slots": 3})
    assert off["enabled"] is False and off["source"].endswith("not ready: vision is disabled (IE_DS41_VISION=0)")
    forced = session_vision(MACHX, SimpleNamespace(vision=True, vision_helper=None), MODEL, capabilities=V41_SEES,
                            props={"vision": READY, "total_slots": 2})
    assert forced == {"state": "on", "enabled": True, "source": "Profile setting"}


@pytest.mark.parametrize("answer, says", [(READY, "MachX takes images at parallel 1 only"),
                                          (MIMO_NOT_ON_LANES, "images are served at --parallel 1 only")])
def test_mimo_sends_no_images_on_lanes_against_the_old_and_the_new_engine_answer(answer, says):
    """Today's engine says MiMo's vision is ready at --parallel 2 and then refuses every image: Dream's own rule turns
    them off. The engine's follow-up says not ready, with its reason: the server's word turns them off, the same
    decision. Either way the profile switch cannot send one, and one lane follows the server as before."""
    props = {"vision": answer, "total_slots": 2}
    decision = session_vision(MACHX, None, MODEL, capabilities=MIMO_SEES, props=props)
    assert decision["enabled"] is False and decision["state"] == "off" and says in decision["source"]
    forced = session_vision(MACHX, SimpleNamespace(vision=True, vision_helper=None), MODEL, capabilities=MIMO_SEES,
                            props=props)
    assert forced["enabled"] is False and "parallel 1 only" in forced["source"]
    one = session_vision(MACHX, None, MODEL, capabilities=MIMO_SEES, props={"vision": READY, "total_slots": 1})
    assert one["enabled"] is True


@pytest.mark.parametrize("capabilities", [SEES, {}])
def test_a_model_dream_cannot_name_sends_no_images_on_lanes(capabilities):
    """Without the architecture Dream cannot tell V4.1 from MiMo on an older engine: it keeps DREAM-151's rule."""
    decision = session_vision(MACHX, None, MODEL, capabilities=capabilities, props={"vision": READY, "total_slots": 2})
    assert decision["enabled"] is False and "parallel 1 only" in decision["source"]


@pytest.mark.parametrize("architecture", ["mimo_v2", "qwen4exp", "deepseek4", None])
def test_every_model_but_v41_sends_no_images_on_sixteen_lanes(architecture):
    """DREAM-201: the engine serves images at --parallel 1 only on MiMo-V2.6, Qwen3.8-Flash and DeepSeek-V4-Flash
    (engine README, "Images at --parallel > 1"); DeepSeek-V4.1 serves them on its lanes. The lanes rule is one
    exception (LANE_IMAGE_ARCHITECTURES) against every other architecture, at 16 lanes as at 2: the header's decision
    and the send switch (lanes_refuse_images, shared with the backend) turn images off, whatever the profile says."""
    from dream.core.profiles import lanes_refuse_images
    capabilities = {**SEES, "architecture": architecture} if architecture else SEES
    props = {"vision": READY, "total_slots": 16}
    assert lanes_refuse_images(props, capabilities) == 16
    decision = session_vision(MACHX, None, MODEL, capabilities=capabilities, props=props)
    assert decision["enabled"] is False and "16 requests at once" in decision["source"]
    forced = session_vision(MACHX, SimpleNamespace(vision=True, vision_helper=None), MODEL, capabilities=capabilities,
                            props=props)
    assert forced["enabled"] is False
    assert lanes_refuse_images(props, V41_SEES) is None
    assert session_vision(MACHX, None, MODEL, capabilities=V41_SEES, props=props)["enabled"] is True


async def test_a_connected_v41_backend_with_lanes_keeps_images(lanes_engine, leases):
    engine = lanes_engine(2, vision=READY)
    b = _backend(engine)
    b._local_capabilities = V41_SEES
    await b.connect()
    assert b.vision_status()["enabled"] is True and b.provider.multimodal is True
    [note] = b._settings_notices
    assert note.startswith("Dream's own note: MachX serves 2 lanes") and "Images" not in note
    assert b.subagent_lanes() == 2 and b._coordinator().slots == 2
    await b.disconnect()


@pytest.mark.parametrize("answer", [READY, MIMO_NOT_ON_LANES])
async def test_a_connected_mimo_backend_with_lanes_offers_no_images_whatever_the_engine_answers(lanes_engine, leases,
                                                                                                answer):
    engine = lanes_engine(2, vision=answer)
    b = _backend(engine)
    b._local_capabilities = MIMO_SEES
    await b.connect()
    assert b.vision_status()["enabled"] is False and b.provider.multimodal is False
    [note] = b._settings_notices
    assert "Images are off" in note and "engine.parallel 1" in note
    await b.disconnect()


# --- what the owner is told ------------------------------------------------------------------------------------

@pytest.mark.parametrize("profile", [None, PROFILES["lean"], PROFILES["frontier"]])
async def test_the_connect_note_reports_the_lanes(lanes_engine, leases, profile):
    engine = lanes_engine(2)
    b = _backend(engine, profile=profile)                     # lean: Parallel workers 1; frontier: 4 -- no matter
    await b.connect()
    [note] = [n for n in b._settings_notices if "lanes" in n]
    assert note.startswith("Dream's own note: MachX serves 2 lanes")
    assert "up to 2 sub-agents of one step run at the same time" in note
    assert "Parallel workers" not in note and "max_parallel" not in note
    await b.disconnect()


@pytest.mark.parametrize("slots, cap, at_once", [(16, None, 7), (2, None, 2), (16, 15, 15), (8, 3, 3)])
async def test_the_connect_note_says_the_smaller_of_the_lanes_and_the_worker_cap(lanes_engine, leases, slots, cap,
                                                                                 at_once):
    """DREAM-197's gate, finding 2: sub-agents of one step run at once up to the lanes AND the owner's worker cap
    (nested.max_workers, 7 unless set), and the note names the cap."""
    from dream.core.profiles import settings_path
    if cap is not None:
        settings_path().parent.mkdir(parents=True, exist_ok=True)
        settings_path().write_text(json.dumps({"version": 1, "nested": {"max_workers": cap}}))
    b = _backend(lanes_engine(slots))
    await b.connect()
    [note] = [n for n in b._settings_notices if "lanes" in n]
    assert note.startswith(f"Dream's own note: MachX serves {slots} lanes")
    assert note.endswith(f"up to {slots} of Dream's requests to it run at once, and up to {at_once} sub-agents of one "
                         f"step run at the same time (one step may start up to {cap or 7}: the worker limit, "
                         "nested.max_workers).")
    await b.disconnect()


@pytest.mark.parametrize("slots", [1, None])
async def test_one_lane_adds_no_note(lanes_engine, leases, slots):
    engine = lanes_engine(slots)
    b = _backend(engine)
    await b.connect()
    assert b._settings_notices == []
    await b.disconnect()


def test_the_capability_report_carries_the_servers_concurrency():
    from dream.core.capabilities import capability_report
    report = capability_report(machx_props={"total_slots": 2})
    assert report["concurrency"] == {"known": True, "value": 2, "source": "machx.props.total_slots"}
    assert capability_report(machx_props={})["concurrency"]["known"] is False
    assert "malformed.machx.props.total_slots" in capability_report(machx_props={"total_slots": "2"})["warnings"]


# --- Controls shows the lanes ------------------------------------------------------------------------------------

from test_studio_controls import READ_ONLY_STARTUP_CALLS, controls, open_controls  # noqa: E402,F401


async def test_controls_shows_the_lanes_running_and_left_over(controls):
    from playwright.async_api import expect
    _, page, api, url, errors = controls
    api.runtime['coordination'] = {'enabled': True, 'state': 'uncertain', 'request_id': 'cut-request', 'waiting': False,
                                   'can_reconcile': True, 'slots': 2, 'running': 1, 'left_over': 1}
    await open_controls(page, url)
    await expect(page.locator('#dc-coordination-status')).to_contain_text('lanes: 1 of 2 running, 1 left over')
    await expect(page.locator('#dc-coordination-status')).to_contain_text('request cut-request')
    api.runtime['coordination'] = {'enabled': True, 'state': 'idle', 'request_id': None, 'waiting': False,
                                   'can_reconcile': False}                         # one slot: today's line
    await page.get_by_role('button', name='Refresh controls').click()
    await expect(page.locator('#dc-coordination-status')).to_have_text('State: idle. ')
    assert api.calls == READ_ONLY_STARTUP_CALLS and not errors


# --- DREAM-155: the send switch follows the lanes rule; a refused image leaves the history ------------------------

def _seeing(profile_vision=True):
    from dataclasses import replace
    return replace(PROFILES["lean"], vision=profile_vision)


@pytest.mark.parametrize("capabilities, slots, sends", [(MIMO_SEES, 2, False), (SEES, 2, False), (V41_SEES, 2, True),
                                                        (MIMO_SEES, 1, True)])
async def test_a_profile_that_turns_images_on_cannot_send_them_on_lanes_that_refuse_them(lanes_engine, leases,
                                                                                          capabilities, slots, sends):
    """The DREAM-154 gate's finding 1: a profile `vision: true` set the send switch on MiMo lanes although the header
    said off, and the engine refuses every image there. Now the switch follows the header's lanes rule; V4.1's lanes
    and one lane keep honouring the profile."""
    engine = lanes_engine(slots, vision=READY)
    b = _backend(engine, profile=_seeing())
    b._local_capabilities = capabilities
    await b.connect()
    assert b.provider.multimodal is sends and b.vision_status()["enabled"] is sends
    await b.disconnect()


def _image_history(b):
    b.messages.append({"role": "user", "content": [{"type": "text", "text": "look"}, {
        "type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo=", "detail": "auto"}}]})
    b.messages.append({"role": "assistant", "content": "seen"})


@pytest.mark.parametrize("refusal", ["sse", "400"])
async def test_an_image_the_engine_refuses_leaves_the_history_and_the_next_turn_goes_through(lanes_engine, leases,
                                                                                             refusal):
    """Today's MiMo refusal (an SSE error "images are served at --parallel 1 only") and the coming engine's HTTP 400
    `vision_not_ready` are recognised: images are turned off for the connection, the retained image is replaced by a
    note, and the next turn succeeds instead of failing the same way forever."""
    engine = lanes_engine(1, vision=READY)
    engine.refuse_images = refusal
    engine.lead = [{"text": "second"}]
    b = _backend(engine)
    b._local_capabilities = MIMO_SEES
    await b.connect()
    assert b.provider.multimodal is True
    _image_history(b)
    events = [event async for event in b.ask("describe it")]
    [error] = [e.data for e in events if e.kind == "error"]
    assert "engine refused image input" in error and "engine.parallel 1" in error
    assert b.provider.multimodal is False and b.vision_status()["enabled"] is False
    assert "image_url" not in json.dumps(b.messages)
    assert "[Image omitted: this loaded model refused image input. The image was not inspected.]" in json.dumps(
        b.messages)
    events = [event async for event in b.ask("and now?")]
    assert not [e for e in events if e.kind == "error"]
    assert "image_url" not in json.dumps(engine.bodies[-1]["messages"])
    await b.disconnect()


def test_the_refusals_recognised_and_the_ones_that_are_not(lanes_engine):
    from dream.core.backends.openai_compat import _RequestFailed, _ServerGenerationError
    engine = lanes_engine(1)
    for error, known in [
            (_ServerGenerationError({"message": "error: mimo_v2 image input: images are served at --parallel 1 only "
                                                "(P4 B4)"}), True),
            (_ServerGenerationError({"message": "error: image inputs need --parallel 1 (per-request image staging is "
                                                "engine-global, not slot-safe)"}), True),
            (_RequestFailed("HTTP 400", code="vision_not_ready", message="not ready"), True),
            (_ServerGenerationError({"message": "error: this deepseek4 load has no vision sidecar "
                                                "(*-Native.safetensors beside the GGUF, or $IE_DS4_VISION)"}), True),
            (_ServerGenerationError({"message": "error: out of memory"}), False),
            (_RequestFailed("HTTP 400", code="invalid_image", message="not a decodable image"), False)]:
        b = _backend(engine)
        assert bool(b._observe_image_rejection(error)) is known, error
        assert (b._image_rejection_model == MODEL) is known
