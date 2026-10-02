"""DREAM-213: an Opus lead with local helpers -- `delegate_local` runs a Claude-led session's sub-tasks on the local
engine's workers, all in one tool call, as full Nested cards.

Build step 1 (the lead's condition): the local workers' machinery runs outside a local lead's turn.

Scripted transports only: no engine, no model, no GPU, no Claude.
"""
import asyncio
import copy
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from anyio import CancelScope

from dream.core import policy
from dream.core.backends.anthropic import AnthropicBackend
from dream.core.local_helpers import LocalHelpers
from dream.core.providers import Provider
from dream.core.subagents import LocalSubagentSpec
from lease_isolation import isolated_lease_dir  # noqa: F401  (every lease of these tests under tmp_path)
from test_local_subagents import _PostResp, _sub_final
from test_parallel_subagents import _backend_for


class _Workers:
    """The local engine's side: each worker's plain reply after a hold, counting how many are in flight at once."""

    def __init__(self, hold=0.05):
        self.hold, self.inflight, self.peak, self.posted = hold, 0, 0, []

    async def post(self, url, json=None):
        self.posted.append(copy.deepcopy(json))
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        try:
            await asyncio.sleep(self.hold)
            return _PostResp(_sub_final("did " + json["messages"][-1]["content"]))
        finally:
            self.inflight -= 1


def _helper(slots, events):
    """A MachX backend whose engine serves `slots` lanes, with a view's emitter, and no lead turn."""
    backend = _backend_for("machx")
    backend._server_props = {"default_generation_settings": {"n_ctx": 32768}, "total_slots": slots}
    backend._server_props_model = backend.model
    backend._background_emit = events.append
    backend._client = _Workers()
    return backend


def _cards(events):
    cards = {}
    for event in events:
        if event.kind == "agent_activity" and event.data["kind"] == "status":
            cards.setdefault(event.data["run_id"], []).append(event.data["status"])
    return cards


async def test_local_workers_run_outside_a_lead_turn():
    """_bounded_task (a lane, a card, the task tool) and _run_subagent run on a backend nothing ever called ask() on,
    so a Claude lead's tool call can drive them. Five tasks on four lanes: four at once and one queued, each returns
    its own result, every card ends completed, and the `lanes` event says the counts."""
    events = []
    helper = _helper(4, events)
    results = await asyncio.gather(*(
        helper._bounded_task({"subagent_type": "worker", "prompt": f"part {i}"}, {}, CancelScope()) for i in range(5)))
    assert [tuple(result[:2]) for result in results] == [(f"did part {i}", False) for i in range(5)]
    assert helper._client.peak == 4
    cards = _cards(events)
    assert len(cards) == 5 and all(statuses[-1] == "completed" for statuses in cards.values())
    assert sum("queued" in statuses for statuses in cards.values()) == 1
    lanes = [event.data for event in events if event.kind == "lanes"]
    assert max(state["busy"] for state in lanes) == 4 and lanes[-1] == {"served": 4, "busy": 0, "queued": 0}
    assert not getattr(helper, "_turn_active", False) and helper._workers == {}
    assert tuple(await helper._run_subagent("worker", "alone")) == ("did alone", False)


# --- delegate_local ----------------------------------------------------------------------------------------------

class _LocalEngine:
    """A local engine on loopback: /v1/models lists `models`, /props says `slots` lanes, and each chat request is
    answered with `reply(its prompt)` (streamed when asked), with usage, after `hold` seconds -- or, with `gated`,
    once `gate` opens. `inflight`/`peak` count the chat requests being answered."""

    def __init__(self, models=("qwen-local",), slots=4, hold=0.1, gated=False, reply=None):
        self.models, self.slots, self.hold, self.reply = list(models), slots, hold, reply or (lambda p: f"did {p}")
        self.gate = threading.Event()
        if not gated:
            self.gate.set()
        self.lock = threading.Lock()
        self.inflight = self.peak = self.chats = 0
        self.asked = []                                   # the model of each chat request
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _EngineHandler)
        self.server.daemon_threads = True
        self.server.engine = self
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.gate.set()
        self.server.shutdown()
        self.server.server_close()


class _EngineHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _json(self, body, status=200):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        engine = self.server.engine
        if self.path == "/v1/models":
            self._json({"object": "list", "data": [{"id": m, "object": "model", "root": m} for m in engine.models]})
        elif self.path.startswith("/props"):
            self._json({"default_generation_settings": {"n_ctx": 32768}, "total_slots": engine.slots})
        elif self.path == "/health":
            self._json({"status": "ok", "inflight": engine.inflight, "queued": 0, "parallel": engine.slots})
        else:
            self._json({"error": {"message": "not found"}}, 404)

    def do_POST(self):
        engine = self.server.engine
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        with engine.lock:
            engine.chats += 1
            engine.inflight += 1
            engine.peak = max(engine.peak, engine.inflight)
            engine.asked.append(body.get("model"))
        try:
            engine.gate.wait(30)
            time.sleep(engine.hold)
            text = engine.reply(body["messages"][-1]["content"])
            usage = {"prompt_tokens": 7, "completion_tokens": 3}
            if body.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                chunk = {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": "stop"}], "usage": usage}
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\ndata: [DONE]\n\n")
                self.wfile.flush()
            else:
                self._json({"id": "fixture", "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": text, "tool_calls": []}}], "usage": usage})
        except OSError:
            pass                                          # the helper let go of this request (a stop, the interrupt)
        finally:
            with engine.lock:
                engine.inflight -= 1


WORKER = {"worker": LocalSubagentSpec(name="worker", description="does work", prompt="You are a worker.", tool_names=())}


def _helpers(url, events=None):
    provider = Provider(key="machx", label="MachX", kind="openai", base_url=url, default_api_key="not-needed")
    return LocalHelpers(tools=[], permission_cb=None, emit=None if events is None else events.append,
                        provider=provider, subagents=WORKER)


def _tasks(n, subagent_type="worker"):
    return [{"subagent_type": subagent_type, "prompt": f"part {i + 1}"} for i in range(n)]


def _sections(text):
    header, *sections = text.split("\n\n### ")
    return header, [(section.split("\n", 1)[0], section.split("\n", 1)[1] if "\n" in section else "")
                    for section in sections]


async def _until(check, tries=400):
    for _ in range(tries):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("never happened")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_delegate_local_runs_every_task_at_once_and_returns_them_together():
    """Four tasks on an engine with four lanes: all four at once, each a full worker card that ends completed, the
    `lanes` events saying the counts; the lead gets one result naming the model, with each task's answer in order."""
    engine, events = _LocalEngine(slots=4, hold=0.2), []
    try:
        text, failed = await _helpers(engine.url, events).delegate(_tasks(4))
    finally:
        engine.close()
    assert not failed and engine.peak == 4
    header, sections = _sections(text)
    assert header.startswith("[Dream] delegate_local ran 4 of 4 tasks on the local engine's qwen-local (4 lanes)")
    assert [title for title, _ in sections] == [f"Task {i} · worker · completed" for i in range(1, 5)]
    assert [body for _, body in sections] == [f"did part {i}" for i in range(1, 5)]
    cards = _cards(events)
    assert len(cards) == 4 and all(statuses[-1] == "completed" for statuses in cards.values())
    assert [event.data for event in events if event.kind == "lanes"][-1] == {"served": 4, "busy": 0, "queued": 0}


async def test_the_owner_pauses_and_stops_a_helper_worker_through_the_claude_lead():
    """While the call runs, /api/control reaches the helper's workers through the Claude backend: Pause all pauses
    them, Resume lets one go on, and Stop ends one alone -- the lead's result says it was stopped by the owner, and the
    others complete. A run id that is no helper's still gets DREAM-212's answer."""
    engine, events = _LocalEngine(slots=4, gated=True), []
    helpers = _helpers(engine.url, events)
    lead = AnthropicBackend(system_prompt="s", mcp_server={}, preapproved_tool_ids=[], agents=None,
                            permission_cb=None, model="claude-lead")
    lead.local_helpers = helpers
    try:
        call = asyncio.create_task(helpers.delegate(_tasks(3)))
        await _until(lambda: engine.inflight == 3)
        runs = list(helpers.running[0]._workers)
        paused = lead.worker_control("agents_pause_all")["runs"]
        assert sorted(run["run_id"] for run in paused) == sorted(runs) and {run["state"] for run in paused} == {"pausing"}
        assert lead.worker_control("agent_resume", runs[0])["state"] == "running"
        for run_id in runs[1:]:
            lead.worker_control("agent_resume", run_id)
        stopped = lead.worker_control("agent_stop", runs[1])
        assert stopped["state"] == "stopping" and stopped["changed"] is True
        with pytest.raises(ValueError, match="No running worker has run id 'claude-x'"):
            lead.worker_control("agent_stop", "claude-x")
        engine.gate.set()
        text, failed = await call
    finally:
        engine.close()
    _, sections = _sections(text)
    assert not failed and sum(title.endswith("· completed") for title, _ in sections) == 2
    assert any(title.endswith("· not completed") and "was stopped by the owner" in body for title, body in sections)
    assert _cards(events)[runs[1]][-1] == "interrupted" and helpers.running == []


async def test_the_leads_interrupt_ends_every_helper_card(monkeypatch):
    """The owner stops the Claude turn while the helpers work (the CLI cancels the tool call): each worker is stopped
    through its own cancel scope, as _bounded_task asks (a Task.cancel could cut its transport's shielded cleanup),
    every card has ended interrupted before the helper is let go (DREAM-213 gate, round 1), and the cancellation
    reaches the caller."""
    from dream.core import local_helpers
    scopes, at_disconnect = [], []

    def scope():
        made = CancelScope()
        scopes.append(made)
        return made
    monkeypatch.setattr(local_helpers, "CancelScope", scope)
    engine, events = _LocalEngine(slots=4, gated=True), []
    helpers = _helpers(engine.url, events)
    helper_for = helpers._helper

    def watched(model):
        helper = helper_for(model)
        disconnect = helper.disconnect

        async def let_go():
            at_disconnect.append(_cards(events))
            await disconnect()
        helper.disconnect = let_go
        return helper
    helpers._helper = watched
    try:
        call = asyncio.create_task(helpers.delegate(_tasks(3)))
        await _until(lambda: engine.inflight == 3)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
    finally:
        engine.close()
    cards, = at_disconnect
    assert len(cards) == 3 and all(statuses[-1] == "interrupted" for statuses in cards.values())
    assert len(scopes) == 3 and all(made.cancelled_caught for made in scopes)
    assert helpers.running == []


async def test_tasks_past_the_worker_cap_are_answered_in_place(monkeypatch):
    """The owner's worker limit (nested.max_workers) bounds one call as it bounds one reply: five tasks with a limit
    of three run three, and one line under the header names the others without a card (DREAM-213 gate, round 1: one
    line for them all, not a section each)."""
    from dream.core import settings
    monkeypatch.setattr(settings, "nested_max_workers", lambda: 3)
    engine, events = _LocalEngine(slots=4), []
    helpers = _helpers(engine.url, events)
    try:
        text, failed = await helpers.delegate(_tasks(5))
        one_over, _ = await helpers.delegate(_tasks(4))
    finally:
        engine.close()
    header, sections = _sections(text)
    assert not failed and engine.chats == 6 and len(_cards(events)) == 6
    assert header.startswith("[Dream] delegate_local ran 3 of 5 tasks")
    assert header.split("\n")[1:] == ["Tasks 4-5 were not run: the worker limit is 3 per call."]
    assert [title for title, _ in sections] == [f"Task {i} · worker · completed" for i in (1, 2, 3)]
    assert _sections(one_over)[0].split("\n")[1:] == ["Task 4 was not run: the worker limit is 3 per call."]


async def test_two_hundred_tasks_reach_dream_and_stay_within_the_limit(monkeypatch):
    """(DREAM-213 gate, round 1) Two hundred tasks with a limit of fifteen reach Dream through the SDK's own tool path
    (claude_agent_sdk's run_tool checks the arguments against the tool's schema first): the schema sets no maxItems,
    whose jsonschema error would echo every prompt and never name the limit (the lead's decision). The whole result --
    header, headings, cut notes -- takes at most 60,000 characters, and the tasks that ran share nearly all of it:
    every reply far past its share, it comes back as the one not-run line and fifteen replies, each cut with a note
    saying how much was cut."""
    import mcp.types as types
    from claude_agent_sdk import create_sdk_mcp_server
    from dream.core import settings
    from dream.tools import delegate_tools
    from dream.tools.context import ToolContext, bind_context
    monkeypatch.setattr(settings, "nested_max_workers", lambda: 15)
    engine = _LocalEngine(slots=4, hold=0.0, reply=lambda prompt: "y" * 50_000)
    server = create_sdk_mcp_server(name="dream", tools=[
        delegate_tools.delegate_local_tool(model="qwen-local", cap=15, names=["worker"])])["instance"]
    context = ToolContext(None, None, None, "s")
    context.local_helpers = _helpers(engine.url)
    call = types.CallToolRequest(method="tools/call", params=types.CallToolRequestParams(
        name="delegate_local", arguments={"tasks": _tasks(200)}))
    try:
        with bind_context(context):
            result = (await server.request_handlers[types.CallToolRequest](call)).root
    finally:
        engine.close()
    (content,) = result.content
    text = content.text
    header, sections = _sections(text)
    assert not result.isError and engine.chats == 15 and 59_900 < len(text) <= 60_000
    assert header.split("\n")[1:] == ["Tasks 16-200 were not run: the worker limit is 15 per call."]
    assert [title for title, _ in sections] == [f"Task {i} · worker · completed" for i in range(1, 16)]
    for _, body in sections:
        kept = body.count("y")
        assert body == "y" * kept + f"\n[… {50_000 - kept:,} more characters cut; see this worker's card in Nested " \
                                    "Dream]"


async def test_with_no_local_engine_nothing_runs_and_the_lead_is_told():
    url, events = f"http://127.0.0.1:{_free_port()}/v1", []
    text, failed = await _helpers(url, events).delegate(_tasks(2))
    assert failed and events == []
    assert text == (f"[Dream] delegate_local needs a running local engine, and none answers at {url}: start one "
                    "(Local model, or `dream local`), then call it again. Nothing was run.")


async def test_the_model_is_the_one_served_or_the_one_roles_main_names(monkeypatch):
    """One listed model is the one used. Several: the one roles.main.model names, or a refusal that lists them."""
    from dream.local import machx
    engine = _LocalEngine(models=("big", "small"), slots=2)
    try:
        monkeypatch.setattr(machx, "main_role", lambda: None)
        text, failed = await _helpers(engine.url).delegate(_tasks(1))
        assert failed and engine.chats == 0
        assert text == (f"[Dream] The local engine at {engine.url} serves several models (big, small), and "
                        "roles.main.model names none of them: set it to the one the helpers should use, then call "
                        "again. Nothing was run.")
        monkeypatch.setattr(machx, "main_role", lambda: "small")
        text, failed = await _helpers(engine.url).delegate(_tasks(1))
    finally:
        engine.close()
    assert not failed and _sections(text)[0].startswith("[Dream] delegate_local ran 1 of 1 tasks on the local engine's small")
    assert set(engine.asked) == {"small"}


async def test_the_result_is_bounded_and_says_where_the_rest_is():
    """The result takes at most 60,000 characters in all: the tasks that ran share it equally, each cut with a note
    that points at its Nested card (the CLI would cut the whole output at 25,000 tokens, unannounced to the lead's
    task)."""
    engine = _LocalEngine(slots=2, reply=lambda prompt: "x" * 50_000)
    try:
        text, failed = await _helpers(engine.url).delegate(_tasks(2))
    finally:
        engine.close()
    _, sections = _sections(text)
    kept = sections[0][1].count("x")
    note = f"\n[… {50_000 - kept:,} more characters cut; see this worker's card in Nested Dream]"
    assert not failed and 59_900 < len(text) <= 60_000
    assert [body for _, body in sections] == ["x" * kept + note] * 2


async def test_the_helpers_usage_is_recorded_apart_from_the_leads():
    """The local tokens of one call go into the turn's runtime record as their own entry (source delegate_local, the
    model), so a run's local prompt and completion tokens can be read apart from Claude's."""
    recorded = []
    meter = SimpleNamespace(record=lambda kind, **fields: recorded.append((kind, fields)))
    engine = _LocalEngine(slots=2)
    try:
        await _helpers(engine.url).delegate(_tasks(2), meter=meter)
    finally:
        engine.close()
    (kind, fields), = recorded
    assert kind == "delegate_local" and fields["source"] == "delegate_local" and fields["model"] == "qwen-local"
    assert (fields["tasks"], fields["ran"], fields["prompt_tokens"], fields["completion_tokens"]) == (2, 2, 14, 6)
    assert isinstance(fields["wall_s"], float)


def test_delegate_local_is_delegating_and_never_pre_approved(tmp_path):
    """Its workers can write, so it is a delegating tool (a known built-in name no custom tool may take), classified
    as the policy's catch-all: asked for, never pre-approved, and refused in plan mode."""
    assert "delegate_local" in policy.builtin_names() and policy.capability("delegate_local") == policy.MUTATING
    assert policy.capability("delegate_local") not in policy.AUTO_CAPS
    assert policy.decide("delegate_local", {"tasks": []}, "plan", tmp_path)[0] == "deny"
    assert policy.decide("delegate_local", {"tasks": []}, "auto", tmp_path)[0] == "ask"


async def test_the_tool_answers_from_the_sessions_helpers():
    """The tool runs through the session's LocalHelpers (the Engine sets them on a Claude-led session) with the
    turn's meter; a session without them is told what it has instead."""
    from dream.tools import delegate_tools
    from dream.tools.context import ToolContext, bind_context
    calls = []

    class Helpers:
        async def delegate(self, tasks, *, meter=None):
            calls.append((tasks, meter))
            return "all done", False
    tool = delegate_tools.delegate_local_tool(model="qwen-local", cap=7, names=["worker"])
    assert tool.name == "delegate_local" and "qwen-local" in tool.description and "up to 7" in tool.description
    context = ToolContext(None, None, None, "s")
    with bind_context(context):
        refused = await tool.handler({"tasks": _tasks(1)})
    assert refused["is_error"] and "only in a Claude-led session" in refused["content"][0]["text"]
    context.local_helpers, context.runtime_meter = Helpers(), "the turn's meter"
    with bind_context(context):
        answer = await tool.handler({"tasks": _tasks(1)})
    assert answer == {"content": [{"type": "text", "text": "all done"}]} and calls == [(_tasks(1), "the turn's meter")]


async def test_the_engine_offers_delegate_local_to_a_claude_session_with_the_local_engine(monkeypatch, tmp_path):
    """A Claude-led session on a computer with the local engine (MachX installed) is offered delegate_local, its
    description naming the model the engine serves; no other session is. Once it is among the session's built tools
    (a skill preset may leave it out), the session gets LocalHelpers -- its workers get the other tools, never
    delegate_local itself, the permission callback and the emitter -- the Claude backend gets them for the owner's
    controls, and the system prompt gets one line about it. A lead switched away from Claude is refused."""
    from dream.core import engine as engines
    from dream.core.engine import Engine
    from dream.local import machx
    from dream.mcp_client import McpClients
    from dream.memory.store import MemoryStore
    from dream.memory.working import WorkingMemory
    from dream.tools.context import ToolContext

    monkeypatch.setattr(machx, "available", lambda: True)
    monkeypatch.setattr(machx, "served_model_id", lambda: "qwen-local")
    monkeypatch.setattr(engines.system_prompt, "build_system_prompt",
                        lambda *args, stable_sections, **kwargs: "\n".join(stable_sections))

    def session(provider):
        engine = Engine(provider=provider, model="fixture", workspace=tmp_path, emit=[].append)
        engine.store = MemoryStore(tmp_path / f"{engine.session_id}.db")
        engine.store.start_session(engine.session_id)
        engine.working = WorkingMemory(engine.store, engine.session_id)
        engine._tool_context = ToolContext(engine.store, engine.working, None, engine.session_id, workspace=tmp_path)
        engine._mcp = McpClients()
        return engine

    claude = session("anthropic")
    try:
        tool = await claude._local_helper_tool()
        assert tool.name == "delegate_local" and "the local engine's qwen-local" in tool.description
        read = SimpleNamespace(name="read_file")
        claude._attach_local_helpers({"tools": [read]})                   # a preset left it out: no helpers
        assert claude._tool_context.local_helpers is None
        claude._attach_local_helpers({"tools": [tool, read]})
        helpers = claude._tool_context.local_helpers
        assert isinstance(helpers, LocalHelpers) and helpers.tools == [read]
        assert helpers.permission_cb == claude._can_use_tool and helpers.emit == claude._background_event
        from dream.core.providers import get_provider
        claude.provider = get_provider("machx")                         # the owner switched this session's lead
        text, failed = await helpers.delegate(_tasks(1))
        assert failed and text == ("[Dream] delegate_local serves a Claude lead, and this session's lead is now "
                                   "MachX: delegate with that lead's own tools. Nothing was run.")
        claude.provider = get_provider("anthropic")
        claude._built_tools = {"tools": [], "server": None, "exempt_tool_ids": [], "names": [], "warnings": []}
        backend = await claude._create_backend()
        assert isinstance(backend, AnthropicBackend) and backend.local_helpers is helpers
        assert "## Local helpers\n`delegate_local` runs several scoped sub-tasks at once" in backend.system_prompt
        claude._tool_context.local_helpers = None
        assert "## Local helpers" not in claude._system_prompt()
    finally:
        claude.store.close()
    local = session("machx")
    try:
        assert await local._local_helper_tool() is None
    finally:
        local.store.close()
    monkeypatch.setattr(machx, "available", lambda: False)
    assert await claude._local_helper_tool() is None
