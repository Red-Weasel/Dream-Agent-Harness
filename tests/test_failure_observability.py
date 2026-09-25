"""Fix #64 (DREAM-110): a server-side failure keeps its words.

2026-09-24 12:59: the owner's turn ended `server_error`. The engine had sent the reason inside
the SSE stream and Dream showed it, but the runtime log kept only the exception type and the
transcript only `turn_status: error`, so nobody could say afterwards what had failed. Every
server-side failure now leaves its code and message in the runtime log (`request_failure`
and `turn_result.failure`), and the text the owner saw becomes one transcript entry (role
`error`) that survives a restart.

The server here is a real HTTP server on an ephemeral loopback port that speaks the engine's
error shapes (src/server/openai_proto.cpp error_json). Nothing talks to the owner's engine.
"""
from __future__ import annotations

import json
import socket
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dream import config

ERROR_FIELDS = ("code", "message")


# --- a scriptable OpenAI-compatible engine -----------------------------------------------------

class FakeEngine:
    """An `ie serve` stand-in: POST /v1/chat/completions follows a script, /health and /props
    answer like the engine. `inflight` counts chat requests the way the engine's admission slot
    does: from the request's start until its handler returns."""

    def __init__(self):
        self.script = []                 # one step per chat request; the last one repeats
        self.requests = []               # (method, path) in arrival order
        self.inflight = 0
        self.queued = 0
        self.health = None               # None = the engine's own report; or a callable(engine) -> (status, body)
        self.lock = threading.Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.daemon_threads = True
        self.server.engine = self
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/v1"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def record(self, method, path):
        with self.lock:
            self.requests.append((method, path))

    def chats(self):
        return sum(1 for method, path in self.requests if method == "POST")

    def health_report(self):
        if self.health is not None:
            return self.health(self)
        return 200, {"status": "ok", "inflight": self.inflight, "queued": self.queued,
                     "parallel": 1, "max_queue": 8}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _json(self, status, body):
        raw = json.dumps(body).encode() if not isinstance(body, bytes) else body
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        engine = self.server.engine
        engine.record("GET", self.path)
        if self.path == "/health":
            report = engine.health_report()
            if report is None:            # a server that drops the health check on the floor
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            self._json(*report)
        elif self.path == "/props":
            self._json(200, {"default_generation_settings": {"n_ctx": 32768}, "total_slots": 1})
        else:
            self._json(404, {"error": {"message": "not found", "type": "not_found"}})

    def do_POST(self):
        engine = self.server.engine
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        engine.record("POST", self.path)
        with engine.lock:
            step = engine.script[min(engine.chats() - 1, len(engine.script) - 1)]
            engine.inflight += 1
        try:
            step(self)
        finally:
            with engine.lock:
                engine.inflight -= 1

    # response helpers used by scripts
    def sse(self, *chunks, done=True):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
        if done:
            self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def text_reply(text="done"):
    return lambda h: h.sse({"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": "stop"}]})


def sse_error(message, type_="server_error", code=None):
    error = {"message": message, "type": type_}
    if code:
        error["code"] = code
    return lambda h: h.sse({"error": error})


def http_error(status, body):
    return lambda h: h._json(status, body)


def broken_chunked_stream(h):
    """Headers and one chunk, then the connection drops mid-body (an engine that died)."""
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream")
    h.send_header("Transfer-Encoding", "chunked")
    h.end_headers()
    line = b'data: {"choices":[{"index":0,"delta":{"content":"par"},"finish_reason":null}]}\n\n'
    h.wfile.write(f"{len(line):x}\r\n".encode() + line + b"\r\n")
    h.wfile.flush()
    h.close_connection = True
    h.connection.shutdown(socket.SHUT_RDWR)


@pytest.fixture
def engine_server():
    server = FakeEngine()
    try:
        yield server
    finally:
        server.close()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- a real Engine turn over the real backend ----------------------------------------------------

async def local_session(tmp_path, base_url, *, port):
    """A started Engine whose backend is the real OpenAI-compatible backend on `base_url`,
    with its inference lease in a private directory (never /tmp/dream-inference-<uid>)."""
    from dream.core.backends.openai_compat import OpenAICompatBackend
    from dream.core.engine import Engine
    from dream.core.inference_coordination import EndpointCoordinator
    from dream.core.providers import Provider
    from dream.memory.store import MemoryStore
    from dream.memory.working import WorkingMemory
    from dream.tools.context import ToolContext

    provider = Provider(key="machx", label="MachX", kind="openai", base_url=base_url,
                        default_api_key="not-needed")
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    engine = Engine(provider=provider, model="fixture", workspace=workspace)
    engine.store = MemoryStore(config.DB_PATH)
    engine.store.project = engine.project
    engine.store.start_session(engine.session_id)
    engine.working = WorkingMemory(engine.store, engine.session_id)
    engine._tool_context = ToolContext(engine.store, engine.working, None, engine.session_id,
                                       workspace=workspace)
    backend = OpenAICompatBackend(provider=provider, model="fixture", system_prompt="SYSTEM",
                                  tools=[], permission_cb=None, profile=engine.profile)
    backend._coordination_override = EndpointCoordinator(f"loopback:{port}", root=tmp_path / "coord")
    await backend.connect()
    engine.backend = backend
    engine._started = True
    return engine


def runtime_events(engine, kind):
    path = config.LOG_DIR / "runtime" / f"{engine.session_id}.jsonl"
    return [row for row in map(json.loads, path.read_text().splitlines()) if row["event"] == kind]


def transcript(engine):
    return [json.loads(line) for line in engine.working.log_path.read_text().splitlines()]


async def failed_turn(engine, prompt="please do the thing"):
    events = [event async for event in engine.ask_chat(prompt)]
    shown = [event.data for event in events if event.kind == "error"]
    result = next(event.data for event in events if event.kind == "result")
    return shown, result


async def close(engine):
    await engine.backend.disconnect()
    engine.store.close()


def assert_failure_kept(engine, shown, result, *, code, message):
    """The runtime log, the transcript and the database all keep what happened."""
    assert len(shown) == 1, shown
    assert result["is_error"] is True
    failure = result["failure"]
    assert failure["code"] == code
    assert failure["message"] == message
    assert len(failure["message"]) <= 1000
    request_failure, = runtime_events(engine, "request_failure")
    turn_result, = runtime_events(engine, "turn_result")
    for row in (request_failure, turn_result["failure"]):
        assert row["code"] == code, row
        assert row["message"] == message, row
    # One transcript entry, in the JSONL and in the database, with exactly the text shown.
    errors = [row for row in transcript(engine) if row["role"] == "error"]
    assert [row["content"] for row in errors] == shown
    stored = [turn for turn in engine.store.session_turns(engine.session_id) if turn["role"] == "error"]
    assert [turn["content"] for turn in stored] == shown


# --- the failures ------------------------------------------------------------------------------

async def test_sse_error_event_keeps_code_and_message(tmp_path, engine_server):
    """The 12:59 shape: HTTP 200, then the engine's error object inside the stream."""
    engine_server.script = [sse_error("error: out of device memory on card 1 (forward: UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY)")]
    engine = await local_session(tmp_path, engine_server.url, port=engine_server.port)
    try:
        shown, result = await failed_turn(engine)
        assert result["subtype"] == "server_error"
        assert shown == ["MachX server_error: error: out of device memory on card 1 "
                         "(forward: UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY)"]
        assert_failure_kept(engine, shown, result, code="server_error",
                            message="error: out of device memory on card 1 "
                                    "(forward: UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY)")
    finally:
        await close(engine)


async def test_http_4xx_json_error_keeps_the_servers_code_and_message(tmp_path, engine_server):
    engine_server.script = [http_error(400, {"error": {"message": "reasoning_effort 'max' is not supported by this load",
                                                       "type": "invalid_request_error", "code": "invalid_effort"}})]
    engine = await local_session(tmp_path, engine_server.url, port=engine_server.port)
    try:
        shown, result = await failed_turn(engine)
        assert result["subtype"] == "request_failed"
        assert shown[0].startswith("MachX HTTP 400")
        assert_failure_kept(engine, shown, result, code="invalid_effort",
                            message="reasoning_effort 'max' is not supported by this load")
        assert engine.backend.coordination_status()["state"] == "idle"   # a completed rejection
    finally:
        await close(engine)


async def test_http_5xx_json_error_keeps_the_servers_words_and_shows_them(tmp_path, engine_server):
    """A local 5xx fences the lease as before; what the engine said is no longer dropped."""
    engine_server.script = [http_error(503, {"error": {"message": "engine unavailable: device lost (card 1)",
                                                       "type": "server_error", "code": "device_lost"}})]
    engine = await local_session(tmp_path, engine_server.url, port=engine_server.port)
    try:
        shown, result = await failed_turn(engine)
        assert "upstream outcome is uncertain" in shown[0]
        assert "engine unavailable: device lost (card 1)" in shown[0]
        assert_failure_kept(engine, shown, result, code="device_lost",
                            message="engine unavailable: device lost (card 1)")
        assert engine.backend.coordination_status()["state"] == "uncertain"
    finally:
        await close(engine)


async def test_long_server_message_is_bounded_to_1000_characters(tmp_path, engine_server):
    long = "error: " + "x" * 5000
    engine_server.script = [http_error(400, {"error": {"message": long, "type": "invalid_request_error"}})]
    engine = await local_session(tmp_path, engine_server.url, port=engine_server.port)
    try:
        shown, result = await failed_turn(engine)
        assert_failure_kept(engine, shown, result, code="invalid_request_error", message=long[:1000])
    finally:
        await close(engine)


async def test_transport_failure_after_retries_keeps_code_and_message(tmp_path, monkeypatch):
    """Nothing listens: three connect attempts, then the turn fails with what was shown."""
    from dream.core.backends import openai_compat
    monkeypatch.setattr(openai_compat, "_RETRY_BACKOFF_S", (0.01, 0.01))
    port = free_port()
    engine = await local_session(tmp_path, f"http://127.0.0.1:{port}/v1", port=port)
    try:
        shown, result = await failed_turn(engine)
        assert result["subtype"] == "request_failed"
        message = result["failure"]["message"]
        assert message.startswith("request failed: ConnectError")
        assert_failure_kept(engine, shown, result, code="transport_error", message=message)
        assert shown == [f"MachX {message}"]
    finally:
        await close(engine)


async def test_stream_cut_mid_body_keeps_code_and_message(tmp_path, engine_server, monkeypatch):
    from dream.local import machx
    monkeypatch.setattr(machx, "is_serving", lambda timeout=2.0: True)   # never ask port 11435
    engine_server.script = [broken_chunked_stream]
    engine = await local_session(tmp_path, engine_server.url, port=engine_server.port)
    try:
        shown, result = await failed_turn(engine)
        assert result["subtype"] == "stream_error"
        assert_failure_kept(engine, shown, result, code="transport_error", message=shown[0])
        assert "stream ended early" in shown[0]
    finally:
        await close(engine)


async def test_error_entry_survives_a_restart_and_read_session_shows_it(tmp_path, engine_server):
    engine_server.script = [sse_error("error: the reply repeated itself and was stopped")]
    engine = await local_session(tmp_path, engine_server.url, port=engine_server.port)
    session_id, workspace = engine.session_id, engine.workspace
    try:
        shown, _ = await failed_turn(engine)
    finally:
        await close(engine)
    # A new process: a fresh store on the same database, reading the old session.
    from dream.memory.project import project_key
    from dream.memory.store import MemoryStore
    from dream.memory.working import WorkingMemory
    from dream.tools.context import ToolContext, bind_context
    from dream.tools.memory_tools import read_session
    store = MemoryStore(config.DB_PATH)
    store.project = project_key(workspace)
    store.start_session("reader")
    try:
        with bind_context(ToolContext(store, WorkingMemory(store, "reader"), None, "reader", workspace=workspace)):
            text = (await read_session.handler({"id": session_id}))["content"][0]["text"]
        assert "] error " in text
        assert shown[0] in text
    finally:
        store.close()


async def test_error_entry_does_not_change_the_saved_chat_outcome(tmp_path, engine_server):
    """Recovery reads `turn_status`; the error entry is not later activity that would make it UNKNOWN."""
    from dream.core.chat_recovery import parse_last_ordinary_chat_status
    engine_server.script = [sse_error("error: device lost")]
    engine = await local_session(tmp_path, engine_server.url, port=engine_server.port)
    session_id = engine.session_id
    try:
        await failed_turn(engine)
    finally:
        await close(engine)
    with sqlite3.connect(config.DB_PATH) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute(
            "SELECT id, role, content FROM turns WHERE session_id=? AND role='turn_status' ORDER BY id DESC LIMIT 2",
            (session_id,))]
        latest = db.execute("SELECT max(id) FROM turns WHERE session_id=? AND role IN "
                            "('user','assistant','assistant_partial','tool_use','tool_result')",
                            (session_id,)).fetchone()[0]
    recovery = parse_last_ordinary_chat_status(rows, latest_activity_id=latest)
    assert recovery.state == "error"


async def test_a_successful_turn_writes_no_error_entry(tmp_path, engine_server):
    engine_server.script = [text_reply("all good")]
    engine = await local_session(tmp_path, engine_server.url, port=engine_server.port)
    try:
        events = [event async for event in engine.ask_chat("hello")]
        assert not any(event.kind == "error" for event in events)
        assert not [row for row in transcript(engine) if row["role"] == "error"]
        assert not runtime_events(engine, "request_failure")
    finally:
        await close(engine)
