"""Fix list #85 (1), DREAM-117: a reply cut at the output ceiling with no completed tool call no longer ends the turn.

Live 2026-09-24 (runtime log 20260924-230103-dfda, turns 1 and 2): the model's reply ran 16,384 tokens in 22 minutes
and hit the output ceiling inside one huge write_file call. No call completed, so Dream ended the turn ("Response
incomplete (length); no collected tool calls ran. Send new instructions to continue.", turn_status incomplete) and the
owner typed "continue" by hand -- twice, 49 + 22 minutes. Dream now writes a short notice of its own (the reply reached
the N-token ceiling with no completed call and was discarded; continue in smaller pieces) and asks again within the
same turn, at most openai_compat._LENGTH_CONTINUATIONS times. In the owner's chat the notice travels the steering inbox
like the progress guard's notes, so the transcript logs it as Dream's (tool_name dream:length, core/turn_origin.py) and
the chat pane shows it as Dream's note, never as the owner's words; each continuation is a runtime-log event.
"""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest

from dream import config
from dream.core import turn_origin
from dream.core.backends import openai_compat
from dream.core.profiles import PROFILES
from dream.core.steering import SteeringInbox
from dream.telemetry.runtime import RunMeter
from test_cache_friendly_head import tool
from test_schema_deferral import _FakeClient, _backend, _sse, _text_round, _tool_round

pytestmark = pytest.mark.asyncio

# The body of the call that did not fit: it must never reach the history or the transcript.
PARTIAL = "import bpy\\nfor i in range(400):\\n    bpy.ops.mesh.primitive_cube_add(location=(i, 0, 0))"
GUI = Path(__file__).resolve().parents[1] / "dream" / "gui" / "static" / "index.html"
INCOMPLETE = ("Response incomplete (length) after 2 automatic continuations; no collected tool calls ran. "
              "Send new instructions to continue.")


def _cut_round(text="Writing the whole script now.", truncated=None, complete=None):
    """A stream cut at the output ceiling inside a write_file call, as the engine streams it: optional prose, a
    completed call first when `complete` gives its arguments, the cut call's partial arguments, then finish_reason
    length (naming the cut call as MachX does when `truncated` is set)."""
    lines = []
    if text:
        lines.append(_sse({"choices": [{"delta": {"content": text}, "finish_reason": None}]}))
    index = 0
    if complete is not None:
        lines.append(_sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c0", "function": {
            "name": "write_file", "arguments": json.dumps(complete)}}]}, "finish_reason": None}]}))
        index = 1
    lines.append(_sse({"choices": [{"delta": {"tool_calls": [{"index": index, "id": f"c{index}", "function": {
        "name": "write_file", "arguments": '{"path": "car.py", "content": "' + PARTIAL}}]}, "finish_reason": None}]}))
    end = {"delta": {}, "finish_reason": "length"}
    if truncated:
        end["truncated_tool_call"] = {"name": truncated}
    lines.append(_sse({"choices": [end]}))
    lines.append("data: [DONE]")
    return lines


def _chat_backend(tmp_path, rounds, rows, receipts, tools=(), failing=False):
    """The backend as the owner's chat runs it: a steering inbox whose transcript writes land in `rows` and whose
    receipts land in `receipts`, and a real run meter writing the runtime log. `failing`: the transcript write of a
    dream:length row raises, as a full disk would."""
    def log_turn(role, content, tool_name=None, on_commit=None):
        if failing and tool_name == "dream:length":
            raise OSError("no space left on device")
        rows.append({"role": role, "content": content, "tool_name": tool_name})
        if on_commit:
            on_commit(len(rows))

    b = _backend(tools, n_ctx=65536)
    b._client = _FakeClient(rounds)
    b.steering_inbox = SteeringInbox(tmp_path / "receipts.json", "s", 1, log_turn, emit=receipts.append)
    b.runtime_meter = RunMeter("s", 1, PROFILES["lean"], tmp_path / "runtime.jsonl")
    return b


def _continuations(path):
    if not path.exists():
        return []
    return [e for e in map(json.loads, path.read_text().splitlines()) if e["event"] == "length_continuation"]


# --- (a) two cuts, then a normal reply -----------------------------------------------------------------


async def test_two_length_stops_are_continued_and_the_turn_ends_normally(tmp_path):
    rows, receipts = [], []
    b = _chat_backend(tmp_path, [_cut_round(), _cut_round(), _text_round("Done, in three parts.")], rows, receipts)
    events = [ev async for ev in b.ask("Build the car")]
    assert events[-1].kind == "result" and events[-1].data["subtype"] == "success"
    assert not any(e.kind == "error" for e in events)
    payloads = b._client.payloads
    assert len(payloads) == 3
    ceilings = [p["max_tokens"] for p in payloads[:2]]                      # what each cut request was sent with
    notices = [r for r in rows if r["role"] == "user"]
    assert [r["tool_name"] for r in notices] == ["dream:length", "dream:length"]
    assert all(turn_origin.is_generated(r["tool_name"], r["content"]) for r in notices)
    for r, ceiling in zip(notices, ceilings):
        assert f"{ceiling:,}-token" in r["content"] and "smaller pieces" in r["content"]
    assert PARTIAL not in json.dumps(rows) and PARTIAL not in json.dumps(b.messages)   # the cut call is gone
    for n, p in enumerate(payloads[1:], 1):     # each re-ask ends with the new notice, named as fix #14's was; the
        last = p["messages"][-1]                # earlier one stays in the history like any prior message
        assert last["role"] == "user" and last["name"] == "dream_recovery_instruction" and "smaller pieces" in last["content"]
        assert sum("[Dream] Your last reply" in m["content"] for m in p["messages"] if m["role"] == "user") == n
    assert [(e["attempt"], e["ceiling"]) for e in _continuations(tmp_path / "runtime.jsonl")] == list(enumerate(ceilings, 1))
    systems = [e.data for e in events if e.kind == "system"]
    assert sum("automatic continuation 1 of 2" in s for s in systems) == 1
    assert sum("automatic continuation 2 of 2" in s for s in systems) == 1


# --- (b) three cuts: the turn ends as today, naming the continuations ----------------------------------


async def test_the_third_length_stop_ends_the_turn_and_names_the_two_continuations(tmp_path):
    assert openai_compat._LENGTH_CONTINUATIONS == 2
    rows, receipts = [], []
    b = _chat_backend(tmp_path, [_cut_round()], rows, receipts)              # the last script repeats: cut, cut, cut
    events = [ev async for ev in b.ask("Build the car")]
    assert len(b._client.payloads) == 3
    assert [e.data for e in events if e.kind == "error"] == [INCOMPLETE]
    assert events[-1].data["subtype"] == "length" and events[-1].data["is_error"] is True
    assert len([r for r in rows if r["role"] == "user"]) == 2
    assert len(_continuations(tmp_path / "runtime.jsonl")) == 2
    assert PARTIAL not in json.dumps(rows) and PARTIAL not in json.dumps(b.messages)


# --- (c) completed tool calls -----------------------------------------------------------------------------


async def test_a_length_stop_after_completed_tool_rounds_is_continued_and_the_rounds_stand(tmp_path):
    """The live shape (turn 1: 17 tool calls, then the cut reply): the earlier calls ran and stay; the cut reply is
    continued like any other."""
    calls = []
    rows, receipts = [], []
    b = _chat_backend(tmp_path, [_tool_round("probe", "{}"), _cut_round(), _text_round("done")], rows, receipts,
                      tools=[tool("probe", "probe ok", calls=calls)])
    events = [ev async for ev in b.ask("Build the car")]
    assert calls == [("probe", {})]                                            # ran once, before the cut
    assert [e.kind for e in events if e.kind in ("tool_use", "tool_result")] == ["tool_use", "tool_result"]
    assert events[-1].data["subtype"] == "success" and len(b._client.payloads) == 3
    assert len(_continuations(tmp_path / "runtime.jsonl")) == 1


async def test_a_complete_call_in_a_length_cut_stream_still_runs_nothing_as_before(tmp_path):
    """Pristine discards every collected call of a generation that ended `length`, complete or not
    (`tool_calls.clear()` under "Valid JSON is not authorization"), and runs none of them. Unchanged: the complete
    call goes with the cut one and nothing runs; the continuation then applies as to any length stop."""
    calls = []
    rows, receipts = [], []
    whole = {"path": "a.py", "content": "print(1)"}
    b = _chat_backend(tmp_path, [_cut_round(complete=whole), _text_round("done")], rows, receipts,
                      tools=[tool("write_file", "written", calls=calls)])
    events = [ev async for ev in b.ask("Build the car")]
    assert calls == []                                                         # as in pristine: nothing ran
    assert not any(e.kind in ("tool_use", "tool_result") for e in events)
    assert not any(m.get("tool_calls") for m in b.messages) and "print(1)" not in json.dumps(b.messages)
    assert events[-1].data["subtype"] == "success" and len(b._client.payloads) == 2


# --- (d) the owner's Stop still stops -------------------------------------------------------------------


async def test_an_owner_stop_right_after_the_notice_stops_the_turn_before_the_re_ask(tmp_path):
    rows, receipts = [], []
    b = _chat_backend(tmp_path, [_cut_round()], rows, receipts)
    events = b.ask("Build the car")
    seen = []
    async for ev in events:
        seen.append(ev)
        if ev.kind == "system" and "automatic continuation 1 of 2" in ev.data:
            await b.interrupt()                                                # the owner's Stop
            break
    with pytest.raises(asyncio.CancelledError):
        await anext(events)
    await events.aclose()
    assert len(b._client.payloads) == 1                                        # the re-ask never went out
    assert not any(e.kind == "result" for e in seen)


# --- (e) the pane: Dream's note, through the existing paths ---------------------------------------------


async def test_the_pane_is_told_whose_note_it_is(tmp_path):
    """No new renderer: the notice's receipts carry origin dream:length, which the pane's steeringNotice (the #76
    path) shows as "Dream's own note, not from you: ..." in the dream-note style; what happened is a `system` line,
    Dream's like every harness line."""
    rows, receipts = [], []
    b = _chat_backend(tmp_path, [_cut_round(), _text_round("done")], rows, receipts)
    events = [ev async for ev in b.ask("Build the car")]
    assert [r["status"] for r in receipts] == ["pending", "included", "submitted"]
    assert {r["origin"] for r in receipts} == {"dream:length"} and all("text" not in r for r in receipts)
    source = GUI.read_text("utf-8")
    assert "receipt.origin.startsWith('dream:')" in source            # every dream: origin is Dream's note
    assert "sysline(`${who}, not from you: ${said}`" in source and '|| "Dream\'s own note"' in source
    told = [e.data for e in events if e.kind == "system" and "automatic continuation" in e.data]
    assert len(told) == 1 and "output ceiling" in told[0] and "nothing ran" in told[0]
    assert "discarded" not in told[0]              # a prose cut stays in the history: the call did not run


# --- the notice reaches the model even when its transcript write fails --------------------------------------


async def test_a_failed_transcript_write_still_sends_the_notice_with_the_re_ask(tmp_path):
    """Gate probe: the inbox accepts the notice, its transcript write fails, the receipt becomes `retained`, and
    _apply_steering drains only `pending` receipts -- so without the fallback the re-ask went out with no notice
    at all while the pane said the model had been asked to continue."""
    rows, receipts = [], []
    b = _chat_backend(tmp_path, [_cut_round(), _text_round("done")], rows, receipts, failing=True)
    events = [ev async for ev in b.ask("Build the car")]
    assert events[-1].data["subtype"] == "success" and len(b._client.payloads) == 2
    assert [r["status"] for r in receipts] == ["retained"] and "warning" in receipts[0]
    assert [r["role"] for r in rows] == []                                     # nothing reached the transcript
    re_ask = b._client.payloads[1]["messages"]
    assert re_ask[-1]["role"] == "user" and re_ask[-1]["name"] == "dream_recovery_instruction"
    assert "[Dream] Your last reply" in re_ask[-1]["content"]
    assert sum("[Dream] Your last reply" in m["content"] for m in re_ask if m["role"] == "user") == 1
    assert len(_continuations(tmp_path / "runtime.jsonl")) == 1


# --- the filer and the verifier read the owner's request, never the notice -----------------------------------


async def _filed(b, prompt="Build the car"):
    filed = []

    async def file(text):
        filed.append(text)
        return []

    b._finish_filing = file
    events = [ev async for ev in b.ask(prompt)]
    assert events[-1].data["subtype"] == "success"
    (text,) = filed
    return text


def _asked(filer_text):
    return filer_text.split("\n\nDream:", 1)[0]


async def test_the_filer_and_the_verifier_read_the_owners_request_not_the_notice(tmp_path):
    """The notice is Dream's, so the filer's "The user ..." text and the verifier's current_user_request must be
    what pristine gives for the same owner request with no cut; the notice appears in neither."""
    rows, receipts = [], []
    cut = _chat_backend(tmp_path, [_cut_round(), _cut_round(), _text_round("Done, in three parts.")], rows, receipts)
    plain = _chat_backend(tmp_path / "plain", [_text_round("Done, in three parts.")], [], [])
    with_cuts, without = await _filed(cut), await _filed(plain)
    assert _asked(with_cuts) == _asked(without)                               # the owner's request, as pristine
    assert "[Dream]" not in with_cuts and "Build the car" in with_cuts
    prompt = cut._verification_prompt("index.html")
    assert "[Dream] Your last reply" not in prompt and "Build the car" in prompt

    # No inbox (a guided task, a loop): the fallback message must not become "The user: [Dream] ...".
    cut, plain = _backend(n_ctx=65536), _backend(n_ctx=65536)
    cut._client = _FakeClient([_cut_round(), _cut_round(), _text_round("Done, in three parts.")])
    plain._client = _FakeClient([_text_round("Done, in three parts.")])
    with_cuts, without = await _filed(cut), await _filed(plain)
    assert _asked(with_cuts) == _asked(without) and _asked(with_cuts).startswith("The user: Build the car")
    assert "[Dream]" not in with_cuts
    prompt = cut._verification_prompt("index.html")
    assert "[Dream] Your last reply" not in prompt and "Build the car" in prompt


# --- through the real Engine: the transcript, the run log, an interrupted re-ask ---------------------------


@pytest.fixture
def chat(tmp_path, monkeypatch):
    """A real Engine on the real HTTP backend with a scripted client: the owner's chat path (ask_chat: the steering
    inbox, the transcript in SQLite, the run log under config.LOG_DIR)."""
    from dream.core.engine import Engine
    from dream.memory import project as project_memory
    from dream.memory.store import MemoryStore
    from dream.memory.tasks import TaskStore
    from dream.memory.working import WorkingMemory
    from dream.tools import installed_skill_tools
    from dream.tools.context import ToolContext

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setenv("DREAM_SKILL_DIRS", str(Path(__file__).resolve().parents[1] / "skills"))
    monkeypatch.setattr(installed_skill_tools, "_CACHE", None)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    engine = Engine(provider="openai", workspace=workspace, profile="lean")
    engine.store = MemoryStore(tmp_path / "memory.db")
    engine.store.project = project_memory.project_key(workspace)
    engine.store.start_session(engine.session_id)
    engine.working = WorkingMemory(engine.store, engine.session_id)
    engine._tool_context = ToolContext(engine.store, engine.working, None, engine.session_id,  # type: ignore[arg-type]
                                       workspace=workspace, tasks=TaskStore(engine.store))
    engine._started = True

    def scripted(client):
        engine.backend = _backend(n_ctx=65536)
        engine.backend._client = client
        return engine
    yield scripted
    engine.store.close()


def _turns(engine):
    return engine.store.session_turns(engine.session_id, limit=1000)


def _state(turns):
    return json.loads([t for t in turns if t["role"] == "turn_status"][-1]["content"])["state"]


async def test_through_the_engine_the_transcript_marks_the_notices_and_the_run_log_has_the_events(chat):
    engine = chat(_FakeClient([_cut_round(), _cut_round(), _text_round("Done, in three parts.")]))
    emitted = []
    engine.emit = emitted.append
    events = [ev async for ev in engine.ask_chat("Build the car")]
    assert events[-1].data["subtype"] == "success"
    turns = _turns(engine)
    users = [t for t in turns if t["role"] == "user"]
    assert [t["tool_name"] for t in users] == [None, "dream:length", "dream:length"]
    assert users[0]["content"] == "Build the car"                             # the owner's, unmarked as before
    assert not turn_origin.is_generated(users[0]["tool_name"], users[0]["content"])
    assert all(turn_origin.is_generated(t["tool_name"], t["content"]) for t in users[1:])
    assert PARTIAL not in json.dumps([t["content"] for t in turns])
    assert _state(turns) == "success"
    log = config.LOG_DIR / "runtime" / f"{engine.session_id}.jsonl"
    logged = _continuations(log)
    assert [e["attempt"] for e in logged] == [1, 2] and all(e["ceiling"] > 0 for e in logged)
    receipts = [ev.data for ev in emitted if ev.kind == "steering"]
    assert receipts and {r["origin"] for r in receipts} == {"dream:length"}
    assert [r["status"] for r in receipts if r["id"] == receipts[0]["id"]] == ["pending", "included", "submitted"]


class _Held:
    """A response body that never ends: the re-ask is still streaming when the owner's Stop comes."""
    status_code = 200

    async def aiter_lines(self):
        yield _sse({"choices": [{"delta": {"content": "Continuing"}, "finish_reason": None}]})
        await asyncio.Event().wait()

    async def aread(self):
        return b""


class _HeldStream:
    async def __aenter__(self):
        return _Held()

    async def __aexit__(self, *exc):
        return False


class _StopClient(_FakeClient):
    """The first request is cut at the ceiling; the second, the re-ask, never ends on its own."""

    def stream(self, method, url, json=None):
        if self.payloads:
            self.payloads.append(copy.deepcopy(json))
            return _HeldStream()
        return super().stream(method, url, json=json)


async def test_a_stop_during_the_re_ask_through_the_engine_ends_the_turn_as_interrupted(chat):
    engine = chat(_StopClient([_cut_round()]))
    seen = []

    async def consume():
        async for ev in engine.ask_chat("Build the car"):
            seen.append(ev)

    task = asyncio.ensure_future(consume())
    for _ in range(500):                                                       # until the re-ask is streaming
        if any(e.kind == "text_delta" and e.data == "Continuing" for e in seen):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("the re-ask never started")
    await engine.interrupt()                    # the app's Stop: the in-flight generation ...
    task.cancel()                               # ... and the turn's task (App._run_turn)
    with pytest.raises(asyncio.CancelledError):
        await task
    turns = _turns(engine)
    assert _state(turns) == "interrupted"
    assert [t["tool_name"] for t in turns if t["role"] == "user"] == [None, "dream:length"]
    assert not any(e.kind == "result" for e in seen)
    assert len(engine.backend._client.payloads) == 2
