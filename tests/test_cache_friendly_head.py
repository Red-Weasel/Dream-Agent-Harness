"""DREAM-112, fix list #38: the prompt's head (the system text, then the tools) is what a prefix-cached
local engine must see unchanged, or it re-reads the whole conversation.

- Every request records a short hash of its head on the runtime meter (usage event `head_hash`).
- The salvage at a round limit keeps the lead's head: live 2026-09-21 00:58 its tool-less payload re-read
  33,077 tokens with 0 cached. That request, not a live-state rewrite, was fix #38's evidence.
- Open work and top-of-mind memory that change mid-session reach the model in a note at the tail; on a
  cache-sensitive engine the head changes only at a compaction boundary, where the history after it is
  re-read anyway. A backend that is not cache-sensitive re-renders the head's wake-up instead.

FakeEngine below is the `ie serve` stand-in the other test_cache_friendly_* files share (tests only: it
never opens a socket).
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
from types import SimpleNamespace

import pytest

from dream.core import system_prompt
from dream.core.backends import openai_compat
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.memory import project as project_memory
from dream.memory.store import MemoryStore
from dream.memory.tasks import TaskStore
from dream.tools.context import ToolContext, bind_context


def head_of(payload: dict) -> str:
    """The test's own fingerprint of a request's head, independent of Dream's."""
    messages = payload["messages"]
    system = messages[0].get("content") if messages and messages[0].get("role") == "system" else None
    blob = json.dumps([system, payload.get("tools") or []], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


class FakeEngine:
    """Counts a prompt as a server would: its text at `ratio` tokens per four characters (a number, or a
    function of the text, so code and prose can differ), each image at `image_tokens` (a number, or a
    function of the image's URL, so images can differ), `per_message` tokens of template around each
    message. Keeps ONE live conversation the way MiMo's engine does: a prompt reuses the live state's common
    prefix only while that state runs at most `rewind` tokens past it (mimo26_servable), else nothing is
    cached. The engine renders no message `name`."""

    def __init__(self, *, lead=(), side=(), props=None, ratio=1.0, image_tokens=700, per_message=4,
                 rewind=2048):
        self.lead, self.side = list(lead), list(side)
        self.props = dict(props or {})
        self.ratio, self.image_tokens, self.per_message, self.rewind = ratio, image_tokens, per_message, rewind
        self.live: list[tuple[str, int]] = []
        self.live_extra = 0
        self.requests: list[dict] = []
        self._ids = itertools.count(1)

    def _text(self, text: str) -> int:
        ratio = self.ratio(text) if callable(self.ratio) else self.ratio
        return math.ceil(len(text) * ratio / 4)

    def _image(self, url: str) -> int:
        return self.image_tokens(url) if callable(self.image_tokens) else self.image_tokens

    def _message_tokens(self, m: dict) -> int:
        content, tokens = m.get("content") or "", 0
        if isinstance(content, list):
            for part in content:
                if part.get("type") == "image_url":
                    tokens += self._image(part["image_url"]["url"])
                else:
                    tokens += self._text(str(part.get("text") or ""))
        else:
            tokens += self._text(content)
        tokens += self._text(m.get("reasoning_content") or "")
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            tokens += self._text((fn.get("name") or "") + (fn.get("arguments") or ""))
        return tokens + self.per_message

    def _blocks(self, payload: dict) -> list[tuple[str, int]]:
        messages = payload["messages"]
        system = messages[0] if messages and messages[0].get("role") == "system" else None
        head = json.dumps([system and system.get("content"), payload.get("tools") or []], sort_keys=True)
        blocks = [("head" + head, self._text(head))]
        for m in messages[1 if system else 0:]:
            blocks.append((json.dumps({k: v for k, v in m.items() if k != "name"}, sort_keys=True),
                           self._message_tokens(m)))
        return blocks

    def serve(self, payload: dict, kind: str) -> tuple[dict, dict]:
        blocks = self._blocks(payload)
        total = sum(tokens for _, tokens in blocks)
        common = 0
        for (key, tokens), (live_key, _) in zip(blocks, self.live):
            if key != live_key:
                break
            common += tokens
        state = sum(tokens for _, tokens in self.live) + self.live_extra
        cached = common if state - common <= self.rewind else 0
        script = self.lead if kind == "lead" else self.side
        reply = script.pop(0) if script else {"text": "done"}
        if callable(reply):
            reply = reply(payload)
        completion = max(1, math.ceil(len(json.dumps(reply)) / 4))
        self.live, self.live_extra = blocks, completion
        self.requests.append({"kind": kind, "prompt_tokens": total, "cached": cached, "head": head_of(payload),
                              "messages": copy.deepcopy(payload["messages"]),
                              "tools": copy.deepcopy(payload.get("tools")),
                              "tool_choice": payload.get("tool_choice")})
        return reply, {"prompt_tokens": total, "completion_tokens": completion,
                       "prompt_tokens_details": {"cached_tokens": cached}}

    def calls_of(self, reply: dict) -> list[dict]:
        return [{"id": f"call{next(self._ids)}", "type": "function",
                 "function": {"name": name, "arguments": json.dumps(args)}}
                for name, args in reply.get("calls", ())]


def _sse(obj) -> str:
    return "data: " + json.dumps(obj)


class _Lines:
    def __init__(self, lines):
        self.status_code = 200
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""


class _Stream:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return _Lines(self._lines)

    async def __aexit__(self, *exc):
        return False


class _Json:
    def __init__(self, body):
        self.status_code = 200
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class FakeClient:
    """What the backend's httpx client is asked for: a streamed lead request, a plain side request,
    and /props or /health."""

    def __init__(self, engine: FakeEngine):
        self.engine = engine

    def stream(self, method, url, json=None):
        reply, usage = self.engine.serve(json, "lead")
        lines = []
        if reply.get("reasoning"):
            lines.append(_sse({"choices": [{"delta": {"reasoning_content": reply["reasoning"]},
                                            "finish_reason": None}]}))
        if reply.get("calls"):
            calls = [{"index": i, **call} for i, call in enumerate(self.engine.calls_of(reply))]
            lines.append(_sse({"choices": [{"delta": {"tool_calls": calls}, "finish_reason": None}]}))
            lines.append(_sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}))
        else:
            lines.append(_sse({"choices": [{"delta": {"content": reply.get("text", "")}, "finish_reason": None}]}))
            lines.append(_sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}))
        lines.append(_sse({"choices": [], "usage": usage}))
        lines.append("data: [DONE]")
        return _Stream(lines)

    async def post(self, url, json=None):
        reply, usage = self.engine.serve(json, "side")
        calls = self.engine.calls_of(reply)
        message = {"role": "assistant", "content": reply.get("text"), "tool_calls": calls}
        if reply.get("reasoning"):
            message["reasoning_content"] = reply["reasoning"]
        return _Json({"choices": [{"message": message, "finish_reason": "tool_calls" if calls else "stop"}],
                      "usage": usage})

    async def get(self, url, timeout=None):
        if url.endswith("/props"):
            return _Json(self.engine.props)
        return _Json({"status": "ok", "inflight": 0, "queued": 0})


class Meter:
    """The runtime meter's surface the backend calls: every usage it reports, and every record."""

    def __init__(self):
        self.usages: list[tuple[str, dict]] = []
        self.records: list[tuple[str, dict]] = []

    def record(self, event, **fields):
        self.records.append((event, fields))

    def check(self):
        return None

    def usage(self, usage, phase="lead"):
        self.usages.append((phase, dict(usage)))

    def before_tool(self, *a, **k):
        return None


def tool(name, text="ok", *, images=0, calls=None):
    """A fake tool. Its images differ per call (their data names the call), so an engine can price them apart."""
    async def handler(args):
        if calls is not None:
            calls.append((name, args))
        blocks = [{"type": "text", "text": text(args) if callable(text) else text}]
        tag = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()[:12]
        blocks += [{"type": "image", "mimeType": "image/png", "data": f"iVBORw0KGgo{tag}{i}"} for i in range(images)]
        return {"content": blocks}
    return SimpleNamespace(name=name, description=f"{name} tool",
                           input_schema={"type": "object", "properties": {}}, handler=handler)


def backend(engine, *, tools=(), subagents=None, model="MiMo-V2.6-Flash-RL-UNCENSORED", n_ctx=200_000,
            system="SYSTEM PROMPT", key="machx", base_url="http://engine.test/v1", multimodal=False, profile=None):
    provider = SimpleNamespace(key=key, label="MachX", base_url=base_url, multimodal=multimodal,
                               api_key=lambda: "n")
    b = OpenAICompatBackend(provider=provider, model=model, system_prompt=system, tools=list(tools),
                            permission_cb=None, subagents=subagents, profile=profile)
    b.n_ctx = n_ctx
    b._client = FakeClient(engine)
    return b


async def turn(b, prompt="go"):
    return [ev async for ev in b.ask(prompt)]


# --- #38a: the head hash, and the salvage that re-read everything --------------------------------

async def test_every_request_records_its_head_hash_on_the_meter(monkeypatch):
    monkeypatch.setattr(openai_compat, "_MAX_TOOL_ROUNDS", 2)
    engine = FakeEngine(lead=[{"calls": [("probe", {"n": 1})]}, {"calls": [("probe", {"n": 2})]}],
                        side=[{"text": "Found two files."}])
    b = backend(engine, tools=[tool("probe", lambda a: f"probe {a['n']}")])
    b.runtime_meter = Meter()
    await turn(b)
    b.messages[0] = {"role": "system", "content": "SYSTEM PROMPT, rewritten"}   # a head that did change
    engine.lead.append({"text": "ok"})
    await turn(b)
    assert [phase for phase, _ in b.runtime_meter.usages] == ["lead", "lead", "recovery", "lead"]
    hashes = [usage["head_hash"] for _, usage in b.runtime_meter.usages]
    assert all(len(h) == 12 and int(h, 16) >= 0 for h in hashes)
    # One hash per distinct head actually sent: the meter names the head of each request.
    pairs = {(h, sent["head"]) for h, sent in zip(hashes, engine.requests)}
    assert len(pairs) == len({h for h, _ in pairs}) == len({s for _, s in pairs}) == 2
    assert hashes[0] == hashes[1] == hashes[2] != hashes[3]


def test_the_run_meter_writes_head_hash_on_the_usage_event(tmp_path):
    from dream.telemetry.runtime import RunMeter
    meter = RunMeter("s", 1, SimpleNamespace(max_run_tools=10, max_run_tokens=None, max_run_seconds=None),
                     path=tmp_path / "run.jsonl")
    meter.usage({"prompt_tokens": 5, "completion_tokens": 1, "head_hash": "0123456789ab"}, phase="recovery")
    meter.usage({"prompt_tokens": 5, "completion_tokens": 1})
    first, second = [json.loads(line) for line in (tmp_path / "run.jsonl").read_text().splitlines()]
    assert first["event"] == "usage" and first["phase"] == "recovery" and first["head_hash"] == "0123456789ab"
    assert "head_hash" not in second


async def test_the_salvage_at_the_round_limit_extends_the_cached_conversation(monkeypatch):
    """The 09-21 00:58 miss: the recovery request dropped the tools, so its head was not the lead's and
    the engine re-read everything. It now carries the lead's tools and continues the cached prefix."""
    monkeypatch.setattr(openai_compat, "_MAX_TOOL_ROUNDS", 2)
    engine = FakeEngine(lead=[{"calls": [("probe", {"n": 1})]}, {"calls": [("probe", {"n": 2})]}],
                        side=[{"text": "Found two files."}])
    b = backend(engine, tools=[tool("probe", lambda a: "x" * 4000 + str(a["n"]))])
    events = await turn(b)
    lead, recovery = engine.requests[-2], engine.requests[-1]
    assert recovery["kind"] == "side" and "Found two files." in [e.data for e in events if e.kind == "assistant_done"]
    assert recovery["head"] == lead["head"]
    assert recovery["cached"] >= lead["prompt_tokens"]


async def test_a_salvage_reply_with_text_and_a_tool_call_keeps_the_text_and_never_runs_the_call(monkeypatch):
    """The engine ignores tool_choice "none": a reply may carry a call beside its text. The text is the
    salvage; the call is dropped, never run, and not asked about again."""
    monkeypatch.setattr(openai_compat, "_MAX_TOOL_ROUNDS", 1)
    ran = []
    engine = FakeEngine(lead=[{"calls": [("probe", {"n": 1})]}],
                        side=[{"text": "Found 3 ports.", "calls": [("probe", {"n": 666})]}])
    b = backend(engine, tools=[tool("probe", lambda a: f"probe {a['n']}", calls=ran)])
    events = await turn(b)
    assert [r["kind"] for r in engine.requests] == ["lead", "side"]          # one salvage ask, no retry
    assert ran == [("probe", {"n": 1})]                                     # the lead's call only
    assert [e.data for e in events if e.kind == "assistant_done"] == ["Found 3 ports."]
    assert b.messages[-1] == {"role": "assistant", "content": "Found 3 ports."}


# --- #38b: open work and top-of-mind memory, without rewriting the head ---------------------------

@pytest.fixture
def session(tmp_path):
    """A real store, tasks and a built system prompt, the way the Engine makes them, with the session's
    tool context bound around each turn."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    store = MemoryStore(tmp_path / "t.db")
    store.project = project_memory.project_key(workspace)
    tasks = TaskStore(store)
    tasks.add("Ship the falcon deck")
    context = ToolContext(store=store, working=None, browser=None,  # type: ignore[arg-type]
                          session_id="s", workspace=workspace, emit=None, tasks=tasks)
    yield SimpleNamespace(store=store, tasks=tasks, context=context, workspace=workspace,
                          prompt=lambda: system_prompt.build_system_prompt(store, "s", workspace=workspace))
    store.close()


async def bound_turn(s, b, prompt="go"):
    with bind_context(s.context):
        return await turn(b, prompt)


def sent_after_head(request) -> str:
    return json.dumps(request["messages"][1:])


async def test_a_task_added_mid_session_reaches_the_model_without_touching_the_head(session):
    engine = FakeEngine(lead=[{"text": "hello"}, {"text": "on it"}])
    b = backend(engine, system=session.prompt())
    await bound_turn(session, b, "first")
    session.tasks.add("Fix the camera shake")
    await bound_turn(session, b, "second")
    first, second = engine.requests
    assert first["head"] == second["head"]                     # the head is what it was
    assert second["cached"] >= first["prompt_tokens"]          # so the engine extends its cache
    assert "Fix the camera shake" not in json.dumps(first["messages"])
    assert "Fix the camera shake" in sent_after_head(second)   # and the model still sees the task
    note = next(m for m in second["messages"] if m.get("name") == "dream_live_state")
    assert second["messages"].index(note) == len(second["messages"]) - 2   # at the tail, before the user's message
    assert "+ open work: #2 Fix the camera shake [open]" in note["content"]
    assert "not from the user" in note["content"]


async def test_a_memory_that_becomes_top_of_mind_and_a_closed_task_are_noted_once(session):
    engine = FakeEngine(lead=[{"text": "a"}, {"text": "b"}, {"text": "c"}])
    b = backend(engine, system=session.prompt())
    await bound_turn(session, b, "first")
    session.store.upsert_memory("semantic", "Owner likes terse replies", "Short answers first.", salience=9.0)
    session.tasks.update(1, status="done")
    await bound_turn(session, b, "second")
    await bound_turn(session, b, "third")
    notes = [m["content"] for m in b.messages if m.get("name") == "dream_live_state"]
    assert len(notes) == 1                                     # said once, not every turn
    assert "+ top of mind: [semantic] Owner likes terse replies: Short answers first." in notes[0]
    assert "- no longer listed as open: #1 Ship the falcon deck [open]" in notes[0]
    assert len({r["head"] for r in engine.requests}) == 1


async def test_nothing_changed_adds_nothing(session):
    engine = FakeEngine(lead=[{"text": "a"}, {"text": "b"}])
    b = backend(engine, system=session.prompt())
    await bound_turn(session, b, "first")
    await bound_turn(session, b, "second")
    assert not [m for m in b.messages if m.get("name") == "dream_live_state"]


async def test_the_head_catches_up_at_a_compaction_boundary(session):
    engine = FakeEngine(lead=[{"text": "a"}, {"text": "b"}, {"text": "c"}])
    b = backend(engine, system=session.prompt(), n_ctx=16_384)
    await bound_turn(session, b, "first")
    session.tasks.add("Fix the camera shake")
    await bound_turn(session, b, "second")                     # noted at the tail
    for i in range(12):                                        # then the history fills past the line
        b.messages.append({"role": "user", "content": f"filler {i} " + "w" * 4000})
        b.messages.append({"role": "assistant", "content": "v" * 4000})
    events = await bound_turn(session, b, "third")
    assert any("compacted context" in str(e.data) for e in events if e.kind == "system")
    third = engine.requests[-1]
    assert "- #2 Fix the camera shake [open]" in third["messages"][0]["content"]   # the head shows it now
    assert third["head"] != engine.requests[0]["head"]
    assert sum(m.get("name") == "dream_live_state" for m in third["messages"]) <= 1   # no second note


async def test_a_remote_backend_keeps_its_prompt_byte_for_byte(session):
    """DREAM-112 gate finding 5: a hosted provider's prefix cache is worth keeping too. The OpenAI path sends
    exactly what it sent before DREAM-112: no head rewrite and no note after a task or memory write."""
    engine = FakeEngine(lead=[{"text": "a"}, {"text": "b"}])
    b = backend(engine, system=session.prompt(), key="openai", base_url="https://api.example.com/v1")
    assert not b._cache_sensitive()
    await bound_turn(session, b, "first")
    session.tasks.add("Fix the camera shake")
    session.store.upsert_memory("semantic", "Owner likes terse replies", "Short answers first.", salience=9.0)
    await bound_turn(session, b, "second")
    first, second = engine.requests
    assert second["messages"] == first["messages"] + [{"role": "assistant", "content": "a"},
                                                      {"role": "user", "content": "second\n\n[id:m0002]"}]
    assert second["head"] == first["head"]


def test_cache_sensitive_is_a_local_engine():
    engine = FakeEngine()
    assert backend(engine)._cache_sensitive()                                            # MachX
    assert backend(engine, key="openai", base_url="http://127.0.0.1:8080/v1")._cache_sensitive()
    assert not backend(engine, key="openai", base_url="https://api.openai.com/v1")._cache_sensitive()


def test_the_wake_up_header_stays_unique_so_its_tail_can_be_re_rendered(session, monkeypatch):
    from dream.core import instructions
    monkeypatch.setattr(instructions, "as_prompt_section", lambda: "\n## Mine\nkeep this\n## Waking up\nnot mine")
    text = session.prompt()
    assert text.count(system_prompt.WAKE_HEADER) == 1
    session.tasks.add("Fix the camera shake")
    fresh = system_prompt.refresh_wake(text, session.store, "s", project=session.store.project)
    assert fresh.startswith(text[:text.index(system_prompt.WAKE_HEADER)])
    assert "keep this" in fresh and "- #2 Fix the camera shake [open]" in fresh


# --- the gate's finding 1: the note compares against the state the head was built from ---------------------

from dream.core.profiles import PROFILES  # noqa: E402


@pytest.fixture
def busy(session):
    """Eight top-of-mind memories long enough that the lean profile's 600-token wake budget leaves the
    section out of the prompt, while balanced and frontier show it."""
    for i in range(8):
        session.store.upsert_memory("semantic", f"Standing memory {i} " + "about the falcon build " * 5,
                                    "b" * 180, salience=5.0 + i)
    session.tasks.add("Tune the camera")
    return session


def profiled(s, name, engine, **kw):
    profile = PROFILES[name]
    head = system_prompt.build_system_prompt(s.store, "s", workspace=s.workspace, profile=profile)
    return backend(engine, system=head, profile=profile, **kw), head


def notes(b):
    return [m["content"] for m in b.messages if m.get("name") == "dream_live_state"]


@pytest.mark.parametrize("name", ["lean", "balanced", "frontier"])
async def test_an_unchanged_first_turn_adds_no_note(busy, name):
    b, head = profiled(busy, name, FakeEngine(lead=[{"text": "a"}]))
    shown = "Standing memory 7" in head
    assert shown is (name != "lean")               # lean leaves the section out; the others show it
    await bound_turn(busy, b, "first")
    assert notes(b) == []


@pytest.mark.parametrize("name", ["lean", "balanced", "frontier"])
async def test_a_change_between_building_the_head_and_the_first_turn_is_noted(busy, name):
    """The baseline is the state the head was built from, not the state at the first turn."""
    b, _ = profiled(busy, name, FakeEngine(lead=[{"text": "a"}]))
    busy.tasks.add("Fix the camera shake")
    await bound_turn(busy, b, "first")
    assert [n.splitlines()[1:] for n in notes(b)] == [["+ open work: #3 Fix the camera shake [open]"]]


@pytest.mark.parametrize("name", ["lean", "balanced", "frontier"])
async def test_three_compactions_with_nothing_changed_add_no_note(busy, name):
    engine = FakeEngine(lead=[{"text": f"reply {i}"} for i in range(4)])
    b, _ = profiled(busy, name, engine, n_ctx=32_768)
    await bound_turn(busy, b, "first")
    compactions = 0
    for turn_no in range(3):
        for i in range(12):                        # fill past the line, so the next turn compacts
            b.messages.append({"role": "user", "content": f"filler {turn_no}.{i} " + "w" * 4000})
            b.messages.append({"role": "assistant", "content": "v" * 4000})
        events = await bound_turn(busy, b, f"turn {turn_no}")
        compactions += sum("compacted context" in str(e.data) for e in events if e.kind == "system")
    assert compactions == 3
    assert notes(b) == []


@pytest.mark.parametrize("name", ["lean", "balanced", "frontier"])
async def test_a_change_is_noted_once_and_nothing_else(busy, name):
    b, _ = profiled(busy, name, FakeEngine(lead=[{"text": "a"}, {"text": "b"}, {"text": "c"}]))
    await bound_turn(busy, b, "first")
    busy.tasks.add("Fix the camera shake")
    busy.store.upsert_memory("semantic", "Owner likes terse replies", "Short answers first.", salience=99.0)
    await bound_turn(busy, b, "second")
    await bound_turn(busy, b, "third")
    [note] = notes(b)                              # once: the third turn adds nothing
    # Only the two changes: the standing memory the new one pushed out of the top 8 is not news.
    assert note.splitlines()[1:] == ["+ open work: #3 Fix the camera shake [open]",
                                     "+ top of mind: [semantic] Owner likes terse replies: Short answers first."]


async def test_an_edited_top_memory_is_not_announced_as_new(busy):
    """Top-of-mind memory is compared by id: a memory the model already knows, rewritten, is not news."""
    busy.store.upsert_memory("semantic", "Owner likes terse replies", "Short answers first.", slug="terse",
                             salience=99.0)
    b, _ = profiled(busy, "frontier", FakeEngine(lead=[{"text": "a"}, {"text": "b"}]))
    await bound_turn(busy, b, "first")
    busy.store.upsert_memory("semantic", "Owner likes terse replies", "Short answers, then detail.",
                             slug="terse", salience=99.0)
    await bound_turn(busy, b, "second")
    assert notes(b) == []


async def test_after_a_compaction_the_state_it_was_built_from_is_the_new_baseline(busy):
    """A change noted before a compaction is not noted again after it, under the profile that hides it."""
    engine = FakeEngine(lead=[{"text": f"reply {i}"} for i in range(4)])
    b, _ = profiled(busy, "lean", engine, n_ctx=32_768)
    b.runtime_meter = Meter()
    await bound_turn(busy, b, "first")
    busy.tasks.add("Fix the camera shake")
    await bound_turn(busy, b, "second")
    for i in range(12):
        b.messages.append({"role": "user", "content": f"filler {i} " + "w" * 4000})
        b.messages.append({"role": "assistant", "content": "v" * 4000})
    events = await bound_turn(busy, b, "third")
    assert any("compacted context" in str(e.data) for e in events if e.kind == "system")
    await bound_turn(busy, b, "fourth")
    assert [e for e, _ in b.runtime_meter.records if e == "live_state_note"] == ["live_state_note"]
    assert sum("Fix the camera shake" in json.dumps(r["messages"][1:]) for r in engine.requests) >= 1


# --- the gate's finding 4: the head hash of every request, from the tools JSON ------------------------------

async def test_a_subagent_request_records_its_own_head_hash():
    engine = FakeEngine(lead=[{"text": "done"}], side=[{"calls": [("probe", {"n": 1})]}, {"text": "found it"}])
    scout = SimpleNamespace(name="scout", description="Scout.", prompt="SCOUT PROMPT", tool_names=("probe",))
    b = backend(engine, tools=[tool("probe", "ok")], subagents={"scout": scout})
    b.runtime_meter = Meter()
    await turn(b, "lead first")
    assert await b._run_subagent("scout", "look around") == ("found it", False)
    usages = b.runtime_meter.usages
    assert [phase for phase, _ in usages] == ["lead", "scout", "scout"]
    lead_hash, *scout_hashes = [u.get("head_hash") for _, u in usages]
    assert all(isinstance(h, str) and len(h) == 12 for h in scout_hashes)
    assert scout_hashes[0] == scout_hashes[1] != lead_hash     # its own head: its own system prompt and tools


def test_the_head_hash_covers_the_tools_json_not_only_their_names():
    def payload(description):
        return {"messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "u"}],
                "tools": [{"type": "function", "function": {"name": "probe", "description": description,
                                                            "parameters": {"type": "object", "properties": {}}}}]}
    assert OpenAICompatBackend._head_hash(payload("one")) != OpenAICompatBackend._head_hash(payload("two"))
    assert OpenAICompatBackend._head_hash(payload("one")) == OpenAICompatBackend._head_hash(payload("one"))


# --- gate round 3: the note's baseline moves with the head -------------------------------------------------

async def test_a_change_the_head_took_up_at_a_compaction_is_not_noted_again(session):
    """_refresh_head makes the state it rendered the baseline. A task added mid-turn (after that turn's note was
    decided) reaches the head at the compaction the same turn runs into; the next turn does not note it again."""
    def plan(args):
        session.tasks.add("Fix the camera shake")
        return "planned. " + "w" * 12_000

    # plan's result, then probe's, carry the history past the line: the request after them compacts first.
    engine = FakeEngine(lead=[{"text": "a"}, {"calls": [("plan", {})]}, {"calls": [("probe", {})]}, {"text": "b"},
                              {"text": "c"}])
    b = backend(engine, tools=[tool("plan", plan), tool("probe", "x" * 12_000)], system=session.prompt(),
                n_ctx=12_000)
    await bound_turn(session, b, "first")
    events = await bound_turn(session, b, "second")
    assert any("compacted context" in str(e.data) for e in events if e.kind == "system")
    await bound_turn(session, b, "third")
    third = engine.requests[-1]
    assert "- #2 Fix the camera shake [open]" in third["messages"][0]["content"]    # the head shows it
    assert notes(b) == []                                                          # and no note repeats it


async def test_a_new_system_prompt_is_what_the_model_knows(session):
    """set_system_prompt makes the state the new prompt was built from the baseline. The engine sets it once, right
    after connecting; this pins the method for any later caller: a task the new prompt shows is not noted."""
    engine = FakeEngine(lead=[{"text": "a"}, {"text": "b"}])
    b = backend(engine, system=session.prompt())
    await bound_turn(session, b, "first")
    session.tasks.add("Fix the camera shake")
    b.set_system_prompt(session.prompt())
    await bound_turn(session, b, "second")
    assert "- #2 Fix the camera shake [open]" in engine.requests[-1]["messages"][0]["content"]
    assert notes(b) == []
