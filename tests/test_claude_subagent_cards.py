"""DREAM-212: a Claude-led session's sub-agents as Nested cards, read-only.

The Claude SDK runs Dream's sub-agents itself (its Task tool; newer CLIs name it Agent) and streams their messages with
parent_tool_use_id = the lead's call. AnthropicBackend used to drop them. Now each becomes agent_activity rows on the
Nested contract (docs/nested-dream.md), through the Engine's emitter, and never enters the lead's own events. A card
ends with the lead's result for its call, or with the turn. Dream cannot stop, pause or message a worker the SDK runs,
and says so.

Every stream here is scripted from the installed SDK's dataclasses and fed by a fake client: nothing calls the SDK, a
model or the network.
"""
import asyncio
import os
import socket
import subprocess
from contextlib import aclosing
from types import SimpleNamespace

import pytest
from claude_agent_sdk import (AssistantMessage, ResultMessage, StreamEvent, TaskNotificationMessage, TaskStartedMessage,
                              TaskUpdatedMessage, TextBlock, ThinkingBlock, ToolResultBlock, ToolUseBlock, UserMessage)

from dream.core import policy
from dream.core.backends import anthropic as anthropic_module
from dream.core.backends import openai_compat
from dream.core.backends.anthropic import AnthropicBackend
from dream.core.backends.base import Event

REFUSAL = "This worker runs inside the Claude SDK; Dream cannot stop, pause or message it."


@pytest.fixture(autouse=True)
def nothing_external(monkeypatch):
    """No socket, process or model: these tests only feed scripted messages."""
    def denied(*args, **kwargs):
        raise AssertionError("forbidden socket/process")
    for obj, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"), (subprocess.Popen, "__init__"),
                      (os, "system"), (asyncio, "create_subprocess_exec"), (asyncio, "create_subprocess_shell")):
        monkeypatch.setattr(obj, name, denied)
    # One coalescing policy with the OpenAI-compatible workers (DREAM-190); a still clock makes the pieces
    # deterministic: they go out when a response closes, or at 400 characters.
    monkeypatch.setattr(openai_compat._DeltaCoalescer, "clock", staticmethod(lambda: 0.0))


class ScriptedClient:
    """The SDK client's side of one turn: receive_response yields the script's messages in order; a callable in the
    script runs at that point instead (a check made mid-turn, or an await that holds the turn there)."""

    def __init__(self, script):
        self.script = script

    async def query(self, prompt):
        pass

    async def receive_response(self):
        for item in self.script:
            if callable(item):
                done = item()
                if asyncio.iscoroutine(done):
                    await done
                continue
            yield item

    async def interrupt(self):
        pass


def claude(script):
    """A Claude backend on a scripted turn -> (backend, the agent_activity rows it emits)."""
    events = []
    backend = AnthropicBackend(system_prompt="fixture", mcp_server={}, preapproved_tool_ids=[], agents=None,
                               permission_cb=None, model="claude-lead", cwd="/tmp", activity_emit=events.append)
    backend.client = ScriptedClient(script)
    return backend, events


def rows_of(events, parent=None):
    assert all(event.kind == "agent_activity" for event in events)
    rows = [event.data for event in events]
    return rows if parent is None else [row for row in rows if row["run_id"] == f"claude-{parent}"]


async def lead_events(backend):
    async with aclosing(backend.ask("go")) as events:
        return [event async for event in events]


# --- the SDK's messages, scripted ---------------------------------------------------------------------------------

def launch(*calls):
    """The lead's reply that starts sub-agents: (tool use id, subagent_type or None, tool name) each."""
    return AssistantMessage(content=[ToolUseBlock(id=p, name=name, input={
        "description": "fixture", "prompt": "fixture work", **({"subagent_type": agent} if agent else {})})
        for p, agent, name in calls], model="claude-lead")


def streamed(p, event):
    return StreamEvent(uuid="u", session_id="s", event=event, parent_tool_use_id=p)


def message_start(p, message_id, model="claude-sub"):
    return streamed(p, {"type": "message_start", "message": {
        "id": message_id, "model": model, "usage": {"input_tokens": 100, "cache_read_input_tokens": 20}}})


def delta(p, kind, text):
    return streamed(p, {"type": "content_block_delta", "index": 0,
                        "delta": {"type": kind, "text" if kind == "text_delta" else "thinking": text}})


def message_delta(p, output_tokens):
    return streamed(p, {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                        "usage": {"output_tokens": output_tokens}})


def said(p, message_id, *blocks, model="claude-sub", usage=None):
    return AssistantMessage(content=list(blocks), model=model, parent_tool_use_id=p, message_id=message_id,
                            usage=usage)


def results(p, *blocks):
    return UserMessage(content=list(blocks), parent_tool_use_id=p)


def lead_gets(p, text, is_error=False):
    return UserMessage(content=[ToolResultBlock(tool_use_id=p, content=text, is_error=is_error)])


def turn_done():
    return ResultMessage(subtype="success", duration_ms=5, duration_api_ms=4, is_error=False, num_turns=2,
                         session_id="s", terminal_reason="completed")


def shape(rows):
    return [(row["kind"], row.get("status"), row.get("request_index")) for row in rows]


# --- the cards ----------------------------------------------------------------------------------------------------

async def test_two_sub_agents_at_once_each_get_a_card_that_ends():
    """Two sub-agents run at once (their messages interleave), each with a tool call of its own, streamed: each gets
    one card -- run id from the lead's call, agent from its subagent_type -- with the contract's rows in order: started,
    then per request its request row, deltas, response and tool rows, then completed when the lead gets its result.
    The model is on every row from the first request on (not the opening row); the pieces of a round concatenate to its
    response text; tool rows pair by id. None of it enters the lead's own events."""
    a, b = "toolu_A1", "toolu_B2"
    backend, events = claude([
        streamed(None, {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Delegating."}}),
        launch((a, "explorer", "Task"), (b, "coder", "Agent")),
        message_start(a, "ma1"), delta(a, "thinking_delta", "Plan: "), delta(a, "text_delta", "Reading "),
        message_start(b, "mb1"), delta(b, "text_delta", "Writing it."),
        delta(a, "text_delta", "the file."),
        said(a, "ma1", ThinkingBlock("Plan: ", "sig"), TextBlock("Reading the file.")),
        said(a, "ma1", ToolUseBlock("t1", "Read", {"file_path": "a.py"}), usage={"output_tokens": 30}),
        message_delta(a, 30),
        said(b, "mb1", TextBlock("Writing it."), ToolUseBlock("t2", "Write", {"file_path": "b.py", "content": "x"})),
        results(a, ToolResultBlock("t1", "print(1)")),
        results(b, ToolResultBlock("t2", "Declined by the user.", is_error=True)),
        message_start(a, "ma2"), delta(a, "text_delta", "It prints 1."), said(a, "ma2", TextBlock("It prints 1.")),
        message_start(b, "mb2"), delta(b, "text_delta", "Could not write."), said(b, "mb2", TextBlock("Could not write.")),
        lead_gets(a, "It prints 1."), lead_gets(b, "Could not write."),
        AssistantMessage(content=[TextBlock("Both are back.")], model="claude-lead"),
        turn_done(),
    ])
    timeline, lead = [], []
    backend.activity_emit = lambda event: (events.append(event), timeline.append(("row", event.data)))
    async with aclosing(backend.ask("go")) as stream:
        async for event in stream:
            lead.append(event)
            timeline.append(("lead", event))
    first, second = rows_of(events, a), rows_of(events, b)
    assert shape(first) == [
        ("status", "running", None), ("request", "awaiting_response", 1), ("thinking_delta", None, 1),
        ("text_delta", None, 1), ("response", "received", 1), ("tool_use", "requested", None),
        ("tool_result", "returned", None), ("request", "awaiting_response", 2), ("text_delta", None, 2),
        ("response", "received", 2), ("status", "completed", None)]
    assert shape(second) == [
        ("status", "running", None), ("request", "awaiting_response", 1), ("text_delta", None, 1),
        ("response", "received", 1), ("tool_use", "requested", None), ("tool_result", "failed", None),
        ("request", "awaiting_response", 2), ("text_delta", None, 2), ("response", "received", 2),
        ("status", "completed", None)]
    assert {row["agent"] for row in first} == {"explorer"} and {row["agent"] for row in second} == {"coder"}
    assert first[0]["text"] == "Subagent started." and "model" not in first[0] and "context" not in first[0]
    assert all(row["model"] == "claude-sub" for row in first[1:] + second[1:])
    assert first[-1]["text"] == "Subagent finished; its result was returned to the lead agent."
    for rows in (first, second):
        for index in (1, 2):
            pieces = "".join(row["text"] for row in rows if row["kind"] == "text_delta" and row["request_index"] == index)
            response, = (row for row in rows if row["kind"] == "response" and row["request_index"] == index)
            assert pieces == response["text"] and isinstance(response["duration_ms"], int)
    assert next(row for row in first if row["kind"] == "thinking_delta")["text"] == "Plan: "
    assert first[4]["usage"] == {"prompt_tokens": 120, "completion_tokens": 30}          # input + cache reads; output
    use, result = first[5], first[6]
    assert use["data"] == {"id": f"claude-{a}:1:0:t1", "name": "Read", "input": {"file_path": "a.py"}}
    assert result["data"] == {"id": f"claude-{a}:1:0:t1", "name": "Read", "content": "print(1)", "is_error": False}
    assert second[5]["data"]["is_error"] is True and second[5]["data"]["id"] == second[4]["data"]["id"]
    assert "thinking_report" not in {row["kind"] for row in first}                      # streamed: deltas only
    # The lead's own events are as before: its text, its two calls and their results, its answer; no worker's words.
    kinds = [(event.kind, (event.data or {}).get("id") if isinstance(event.data, dict) else event.data) for event in lead]
    assert kinds[:5] == [("text_delta", "Delegating."), ("tool_use", a), ("tool_use", b), ("tool_result", a),
                         ("tool_result", b)]
    assert ("assistant_done", "Both are back.") in kinds and kinds[-1][0] == "result"
    assert not any("Reading" in str(event.data) or "Writing" in str(event.data) for event in lead)
    for p in (a, b):          # each card has ended when the lead reads that call's result
        ended = next(i for i, (who, item) in enumerate(timeline) if who == "row" and item["run_id"] == f"claude-{p}"
                     and item["kind"] == "status" and item["status"] == "completed")
        read = next(i for i, (who, item) in enumerate(timeline) if who == "lead" and item.kind == "tool_result"
                    and item.data["id"] == p)
        assert ended < read


async def test_a_sub_agent_whose_call_fails_ends_failed_with_what_the_lead_got():
    """The lead's result for the call is an error: the card ends failed and says what the lead got."""
    p = "toolu_F"
    backend, events = claude([launch((p, "explorer", "Task")), said(p, "m1", TextBlock("Trying.")),
                              lead_gets(p, "Agent failed: the API returned 529.", is_error=True), turn_done()])
    await lead_events(backend)
    rows = rows_of(events, p)
    assert shape(rows)[-1] == ("status", "failed", None)
    assert rows[-1]["text"] == "Subagent returned a failed or incomplete result: Agent failed: the API returned 529."


async def test_a_launch_the_sdk_refuses_still_gets_a_card_that_says_why():
    """The lead's call to the Agent tool (Task's newer name) is refused before any sub-agent message comes: its card,
    opened at the launch, ends failed with the refusal the lead got. The lead's other call in that reply (Read) is no
    launch and gets no card."""
    p, read = "toolu_D", "toolu_R"
    backend, events = claude([launch((p, "explorer", "Agent"), (read, None, "Read")),
                              lead_gets(p, "Declined by the user.", is_error=True), lead_gets(read, "a file"),
                              turn_done()])
    await lead_events(backend)
    rows = rows_of(events, p)
    assert shape(rows) == [("status", "running", None), ("status", "failed", None)] and rows[0]["agent"] == "explorer"
    assert rows[1]["text"] == "Subagent returned a failed or incomplete result: Declined by the user."
    assert rows_of(events, read) == [] and len(rows_of(events)) == 2


async def test_a_turn_cancelled_mid_way_ends_every_card_interrupted():
    """The owner stops the turn while one sub-agent is streaming and another has only been launched: both cards end
    interrupted, and the pieces already received go out first, so they still add up to what was streamed."""
    a, b = "toolu_C1", "toolu_C2"
    reached, never = asyncio.Event(), asyncio.Event()

    async def hold():
        reached.set()
        await never.wait()
    backend, events = claude([launch((a, "explorer", "Task"), (b, "coder", "Task")), message_start(a, "m1"),
                              delta(a, "text_delta", "Half an ans"), hold, turn_done()])
    turn = asyncio.create_task(lead_events(backend))
    await reached.wait()
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn
    first, second = rows_of(events, a), rows_of(events, b)
    assert shape(first) == [("status", "running", None), ("request", "awaiting_response", 1),
                            ("text_delta", None, 1), ("status", "interrupted", None)]
    assert first[2]["text"] == "Half an ans"
    assert shape(second) == [("status", "running", None), ("status", "interrupted", None)]
    assert first[-1]["text"] == "Subagent observation was interrupted. In-flight effects may need checking."
    with pytest.raises(ValueError, match=f"No running worker has run id 'claude-{a}'"):
        backend.worker_control("agent_stop", f"claude-{a}")             # ended with the turn


async def test_a_turn_closed_by_its_reader_ends_every_card_interrupted():
    """The Engine closes the turn's events early (a tool budget, an error): the card still ends, as interrupted."""
    p = "toolu_G"
    backend, events = claude([launch((p, "explorer", "Task")), said(p, "m1", TextBlock("Working.")),
                              lead_gets(p, "done"), turn_done()])
    async with aclosing(backend.ask("go")) as lead:
        async for event in lead:
            if event.kind == "tool_use":
                break
    assert shape(rows_of(events, p))[-1] == ("status", "interrupted", None)


async def test_a_turn_that_ends_before_a_sub_agents_result_fails_its_card():
    """The turn's result comes while a card is still open (no result for its call): what it said is shown, its tool call
    whose result never came is of unknown outcome, and it ends failed and says why."""
    p = "toolu_E"
    backend, events = claude([launch((p, "explorer", "Task")),
                              said(p, "m1", TextBlock("Still going."), ToolUseBlock("t9", "Glob", {"pattern": "*"})),
                              turn_done()])
    await lead_events(backend)
    rows = rows_of(events, p)
    assert shape(rows) == [("status", "running", None), ("request", "awaiting_response", 1),
                           ("response", "received", 1), ("tool_use", "requested", None),
                           ("tool_result", "failed", None), ("status", "failed", None)]
    assert rows[4]["data"] == {"id": rows[3]["data"]["id"], "name": "Glob", "is_error": True,
                               "content": "Tool observation interrupted; outcome is unknown."}
    assert rows[-1]["text"] == "Subagent returned no result: the lead's turn ended before it came back."


async def test_an_unstreamed_sub_agent_gets_its_rounds_from_its_messages():
    """Without stream events a round is one message id (the SDK sends one message per content block); its reasoning
    is a thinking_report after the response, as on the plain path. The Agent tool is a launch too, and a call with no
    subagent_type is shown as "subagent"."""
    p = "toolu_U"
    backend, events = claude([
        launch((p, None, "Agent")),
        said(p, "m1", ThinkingBlock("Check the tests first.", "sig"), usage={"input_tokens": 50, "output_tokens": 9}),
        said(p, "m1", ToolUseBlock("t1", "Grep", {"pattern": "def test"}), usage={"input_tokens": 50, "output_tokens": 12}),
        results(p, ToolResultBlock("t1", [{"type": "text", "text": "tests/a.py:1"}])),
        said(p, "m2", TextBlock("One test file.")),
        lead_gets(p, "One test file."), turn_done()])
    await lead_events(backend)
    rows = rows_of(events, p)
    assert shape(rows) == [("status", "running", None), ("request", "awaiting_response", 1),
                           ("response", "received", 1), ("thinking_report", None, None),
                           ("tool_use", "requested", None), ("tool_result", "returned", None),
                           ("request", "awaiting_response", 2), ("response", "received", 2),
                           ("status", "completed", None)]
    assert {row["agent"] for row in rows} == {"subagent"}
    assert rows[2]["usage"] == {"prompt_tokens": 50, "completion_tokens": 12} and "text" not in rows[2]
    assert rows[3]["text"] == "Check the tests first." and rows[5]["data"]["content"] == "tests/a.py:1"
    assert rows[7]["text"] == "One test file."


async def test_worker_controls_on_a_claude_sub_agent_are_refused_with_the_reason():
    """Dream shows the SDK's sub-agents but cannot stop, pause, resume or message them: while one runs, each control
    is refused with the reason, after the checks the other backends make first (the contract's order); once the turn is
    over it is a finished run. Lanes stay unreported on Claude (no lanes_status)."""
    p = "toolu_W"
    refusals = {}

    def controls():
        run_id = f"claude-{p}"                                          # derived from the lead's call
        for action in ("agent_stop", "agent_pause", "agent_resume", "agent_message"):
            with pytest.raises(ValueError) as refused:
                backend.worker_control(action, run_id, "a note")
            refusals[action] = str(refused.value)
        with pytest.raises(ValueError) as refused:
            backend.worker_control("agents_pause_all")
        refusals["agents_pause_all"] = str(refused.value)
        with pytest.raises(ValueError, match="Choose a worker: run_id must be the run id its activity rows carry."):
            backend.worker_control("agent_stop", "")
        with pytest.raises(ValueError, match="No running worker has run id 'claude-other'"):
            backend.worker_control("agent_stop", "claude-other")
        with pytest.raises(ValueError, match="Unknown worker action"):
            backend.worker_control("agent_kill", run_id)
    backend, events = claude([launch((p, "explorer", "Task")), controls, lead_gets(p, "ok"), turn_done()])
    await lead_events(backend)
    assert {action: text for action, text in refusals.items() if action != "agents_pause_all"} == {
        action: REFUSAL for action in ("agent_stop", "agent_pause", "agent_resume", "agent_message")}
    assert refusals["agents_pause_all"] == ("These workers run inside the Claude SDK; Dream cannot stop, pause or "
                                            "message them.")
    with pytest.raises(ValueError, match=f"No running worker has run id 'claude-{p}' in this session"):
        backend.worker_control("agent_stop", f"claude-{p}")
    with pytest.raises(ValueError, match="No worker is running in this session; there is nothing to pause."):
        backend.worker_control("agents_pause_all")
    assert getattr(backend, "lanes_status", None) is None
    assert [row["status"] for row in rows_of(events, p)] == ["running", "completed"]


async def test_the_engine_gives_a_claude_backend_the_emitter_its_workers_use(tmp_path):
    """The Engine hands the Claude backend its background emitter (what the OpenAI-compatible workers' rows go
    through), so a view gets the cards; with no emitter at all -- a headless run, or an Engine built without __init__
    -- it gets none and emits nothing."""
    from dream.core.engine import Engine
    from dream.mcp_client import McpClients
    from dream.memory.store import MemoryStore
    from dream.memory.working import WorkingMemory
    from dream.tools.context import ToolContext

    async def backend_of(emit, *, no_emit_attribute=False):
        engine = Engine(provider="anthropic", model="fixture", workspace=tmp_path, emit=emit)
        if no_emit_attribute:
            del engine.emit
        engine.store = MemoryStore(tmp_path / f"{engine.session_id}-{no_emit_attribute}.db")
        try:
            engine.store.start_session(engine.session_id)
            engine.working = WorkingMemory(engine.store, engine.session_id)
            engine._tool_context = ToolContext(engine.store, engine.working, None, engine.session_id,
                                               workspace=tmp_path)
            engine._mcp = McpClients()
            engine._built_tools = {"tools": [], "server": None, "exempt_tool_ids": [], "names": [], "warnings": []}
            return engine, await engine._create_backend()
        finally:
            engine.store.close()

    seen = []
    engine, with_view = await backend_of(seen.append)
    assert isinstance(with_view, AnthropicBackend) and with_view.activity_emit == engine._background_event
    with_view.activity_emit(Event("agent_activity", {}))
    assert [event.kind for event in seen] == ["agent_activity"]
    assert (await backend_of(None))[1].activity_emit is None
    assert (await backend_of(None, no_emit_attribute=True))[1].activity_emit is None


# --- the DREAM-212 gate, round 1: the bundled CLI's shapes, timing, faults ---------------------------------------

BACKGROUND = "Running in the background; the lead was told it started and goes on."


def launched_in_background(p, agent_id):
    """The bundled CLI's receipt for an Agent call it runs in the background (its default): at once, not the result."""
    return UserMessage(content=[ToolResultBlock(tool_use_id=p, content=(
        "Async agent launched successfully. The agent is working in the background. You will be notified "
        "automatically when it completes."))], tool_use_result={
        "isAsync": True, "status": "async_launched", "agentId": agent_id, "description": "fixture",
        "prompt": "fixture work", "outputFile": "/fixture/output", "canReadOutputFile": False})


def task_started(p, task_id):
    return TaskStartedMessage(subtype="task_started", data={}, task_id=task_id, description="fixture", uuid="u",
                              session_id="s", tool_use_id=p, task_type="local_agent")


def task_notified(p, task_id, status, summary):
    return TaskNotificationMessage(subtype="task_notification", data={}, task_id=task_id, status=status,
                                   output_file="/fixture/output", summary=summary, uuid="u", session_id="s",
                                   tool_use_id=p)


def task_updated(task_id, status):
    return TaskUpdatedMessage(subtype="task_updated", data={}, task_id=task_id, patch={"status": status}, status=status)


async def test_a_background_launch_keeps_its_card_open_until_the_sdk_says_it_ended():
    """(Gate finding 1) The bundled CLI runs a sub-agent in the background unless told otherwise, and the lead's result
    for the call is then at once a receipt (tool_use_result status async_launched), not the sub-agent's result. The
    card says so and stays open -- its frames still count -- and ends when the SDK's task notification for that call
    comes: here completed, with its summary. The lead's own events are as they were."""
    p = "toolu_BG"
    backend, events = claude([launch((p, "explorer", "Agent")), task_started(p, "a1"), launched_in_background(p, "a1"),
                              AssistantMessage(content=[TextBlock("It works in the background.")], model="claude-lead"),
                              said(p, "m1", TextBlock("Found it.")), task_notified(p, "a1", "completed", "Found 3 issues."),
                              turn_done()])
    lead = await lead_events(backend)
    rows = rows_of(events, p)
    assert shape(rows) == [("status", "running", None), ("status", "running", None),
                           ("request", "awaiting_response", 1), ("response", "received", 1), ("status", "completed", None)]
    assert rows[1]["text"] == BACKGROUND and rows[-1]["text"] == "Subagent finished in the background: Found 3 issues."
    assert [event.kind for event in lead] == ["tool_use", "tool_result", "assistant_done", "result"]


async def test_a_background_sub_agent_still_running_when_the_turn_ends_is_said_to_be_so():
    """The turn ends before the SDK reports the background sub-agent done: its card ends as unknown, saying why."""
    p = "toolu_BG2"
    backend, events = claude([launch((p, "explorer", "Agent")), launched_in_background(p, "a2"), turn_done()])
    await lead_events(backend)
    rows = rows_of(events, p)
    assert shape(rows) == [("status", "running", None), ("status", "running", None), ("status", "unknown", None)]
    assert rows[-1]["text"] == "Still running in the background when the lead's turn ended; Dream shows no more of it."


@pytest.mark.parametrize("end, status, text", [
    (lambda p: task_updated("a3", "killed"), "interrupted", "Subagent was stopped in the background."),
    (lambda p: task_notified(p, "a3", "stopped", ""), "interrupted", "Subagent was stopped in the background."),
    (lambda p: task_notified(p, "a3", "failed", "The API returned 529."), "failed",
     "Subagent returned a failed or incomplete result: The API returned 529."),
], ids=["updated-killed", "notified-stopped", "notified-failed"])
async def test_the_sdk_ending_a_background_sub_agent_ends_its_card(end, status, text):
    """A task update (the task id is the receipt's agentId) or a task notification (the call's id) ends the card."""
    p = "toolu_BG3"
    backend, events = claude([launch((p, "explorer", "Agent")), launched_in_background(p, "a3"), end(p), turn_done()])
    await lead_events(backend)
    rows = rows_of(events, p)
    assert len(rows) == 3 and (rows[-1]["status"], rows[-1]["text"]) == (status, text)


async def test_a_reply_and_its_tool_rows_go_out_when_its_tool_call_comes(monkeypatch):
    """(Gate finding 2) The bundled CLI sends a sub-agent's reply one content block at a time, with no stream events.
    The response row and the tool rows go out when the reply's first tool call arrives, not when the tool has run; the
    reply's duration runs from its request to its last block; the next request row goes out when the tool results go
    back. (Before, all of it waited for the results and the tool's runtime counted as the reply's.)"""
    now = [100.0]
    monkeypatch.setattr(anthropic_module, "time", SimpleNamespace(monotonic=lambda: now[0]))

    def at(t):
        return lambda: now.__setitem__(0, t)
    p = "toolu_T"
    backend, events = claude([
        launch((p, "explorer", "Agent")),                                        # its first request: 100.0
        at(100.4), said(p, "m1", TextBlock("Checking."), usage={"input_tokens": 50, "output_tokens": 5}),
        said(p, "m1", ToolUseBlock("t1", "Grep", {"pattern": "x"}), usage={"input_tokens": 50, "output_tokens": 20}),
        at(105.4), results(p, ToolResultBlock("t1", "a.py:1")),                # the tool ran five seconds
        at(105.7), said(p, "m2", TextBlock("Done.")),
        at(106.0), lead_gets(p, "Done."), turn_done()])
    timeline = []
    backend.activity_emit = lambda event: (events.append(event), timeline.append((now[0], event.data)))
    await lead_events(backend)
    assert [(t, row["kind"], row.get("request_index")) for t, row in timeline] == [
        (100.0, "status", None), (100.4, "request", 1), (100.4, "response", 1), (100.4, "tool_use", None),
        (105.4, "tool_result", None), (105.4, "request", 2), (106.0, "response", 2), (106.0, "status", None)]
    first, second = (row for t, row in timeline if row["kind"] == "response")
    assert first["duration_ms"] == 400 and first["usage"] == {"prompt_tokens": 50, "completion_tokens": 20}
    assert first["text"] == "Checking." and second["duration_ms"] == 300 and second["text"] == "Done."


def _lead_view(events):
    return [(event.kind, event.data) for event in events]


def _fault_script(p):
    return [launch((p, "explorer", "Agent")), task_started(p, "f1"),
            said(p, "m1", TextBlock("Looking."), ToolUseBlock("t1", "Read", {"file_path": "a"})),
            results(p, ToolResultBlock("t1", "a")), lead_gets(p, "ok"),
            AssistantMessage(content=[TextBlock("Lead continues.")], model="claude-lead"), turn_done()]


async def test_a_sub_agent_result_the_cards_cannot_read_leaves_the_leads_turn_alone():
    """(Gate finding 3, PB3a) A sub-agent tool result with a null text item makes the mapping raise: that update is
    logged and dropped, and the lead's events are exactly those of the same turn without the sub-agent's frames."""
    p = "toolu_N"
    script = _fault_script(p)
    script[3] = results(p, ToolResultBlock("t1", [{"type": "text", "text": None}]))
    without_frames = [item for item in script if getattr(item, "parent_tool_use_id", None) is None]
    backend, _ = claude(script)
    baseline, _ = claude(without_frames)
    got = await lead_events(backend)
    assert _lead_view(got) == _lead_view(await lead_events(baseline))
    assert ("assistant_done", "Lead continues.") in _lead_view(got)


@pytest.mark.parametrize("where", ["__init__", "message", "tool_use", "tool_result", "system", "close", "emit"])
async def test_a_fault_anywhere_in_the_cards_leaves_the_leads_events_as_they_are(monkeypatch, where):
    """A fault injected in the cards' construction, any of their updates, their closing or the emitter: the lead's
    events are exactly the fault-free turn's."""
    p = "toolu_I"
    healthy, _ = claude(_fault_script(p))
    expected = _lead_view(await lead_events(healthy))

    def boom(*args, **kwargs):
        raise RuntimeError("injected")
    backend, _ = claude(_fault_script(p))
    if where == "emit":
        backend.activity_emit = boom
    else:
        monkeypatch.setattr(anthropic_module._SubagentCards, where, boom)
    assert _lead_view(await lead_events(backend)) == expected


async def test_a_fault_while_the_cards_close_leaves_the_owners_stop_a_cancellation(monkeypatch):
    """(PB3c) The owner stops the turn and the cards' closing faults: the turn still ends with CancelledError."""
    def boom(*args, **kwargs):
        raise RuntimeError("injected")
    monkeypatch.setattr(anthropic_module._SubagentCards, "close", boom)
    reached, never = asyncio.Event(), asyncio.Event()

    async def hold():
        reached.set()
        await never.wait()
    backend, _ = claude([launch(("toolu_S", "explorer", "Agent")), hold, turn_done()])
    turn = asyncio.create_task(lead_events(backend))
    await reached.wait()
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn


async def test_a_bare_agent_launch_and_any_call_naming_a_subagent_type_get_a_card():
    """The bundled CLI's launch tool is Agent, and its subagent_type is optional: a bare Agent call refused before any
    frame gets a card ("subagent") that ends with the refusal. Any call that names a subagent_type is a launch too;
    a Read is not."""
    bare, other, read = "toolu_Bare", "toolu_Other", "toolu_Read"
    backend, events = claude([
        AssistantMessage(content=[ToolUseBlock(id=bare, name="Agent", input={"prompt": "look"}),
                                  ToolUseBlock(id=other, name="Delegate", input={"subagent_type": "coder", "prompt": "x"}),
                                  ToolUseBlock(id=read, name="Read", input={"file_path": "a"})], model="claude-lead"),
        lead_gets(bare, "Declined by the user.", is_error=True), lead_gets(other, "done"), lead_gets(read, "a"),
        turn_done()])
    await lead_events(backend)
    assert shape(rows_of(events, bare)) == [("status", "running", None), ("status", "failed", None)]
    assert {row["agent"] for row in rows_of(events, bare)} == {"subagent"}
    assert [row["agent"] for row in rows_of(events, other)] == ["coder", "coder"] and rows_of(events, read) == []


async def test_the_cards_rows_are_bounded_and_redacted():
    """Every row goes through the display contract's bounds: a long reply, a credential in a tool input, a long result."""
    p = "toolu_Bound"
    reply, output = "word " * 4_000, "a line of output\n" * 2_000
    backend, events = claude([launch((p, "explorer", "Task")),
                              said(p, "m1", TextBlock(reply), ToolUseBlock("t1", "call", {"api_key": "sk-x", "q": "z"})),
                              results(p, ToolResultBlock("t1", output)), lead_gets(p, "ok"), turn_done()])
    await lead_events(backend)
    rows = rows_of(events, p)
    response = next(row for row in rows if row["kind"] == "response")
    use = next(row for row in rows if row["kind"] == "tool_use")
    result = next(row for row in rows if row["kind"] == "tool_result")
    assert response["text"] == reply[:12_000] + "\n[Display truncated]"
    assert use["data"]["input"] == {"api_key": "[Credential omitted]", "q": "z"}
    assert result["data"]["content"] == output[:20_000] + "\n[Display truncated]"


async def test_a_nested_launch_is_named_and_ordered_by_its_launch():
    """(Gate finding 4) A sub-agent that launches its own: the inner card opens when the outer's call comes, after the
    outer's response and tool_use rows, with the inner call's subagent_type. A card its frames opened before any launch
    named it takes the name when the launch comes."""
    outer, inner, early = "toolu_Outer", "toolu_Inner", "toolu_Early"
    backend, events = claude([
        launch((outer, "planner", "Agent")),
        said(outer, "m1", ToolUseBlock(inner, "Agent", {"subagent_type": "checker", "prompt": "check"})),
        said(inner, "n1", TextBlock("Checked.")),
        said(early, "e1", TextBlock("First.")),                                  # frames before any launch named it
        said(outer, "m1", ToolUseBlock(early, "Agent", {"subagent_type": "reader", "prompt": "read"})),
        results(outer, ToolResultBlock(inner, "Checked."), ToolResultBlock(early, "Read.")),
        said(outer, "m2", TextBlock("Planned.")), lead_gets(outer, "Planned."), turn_done()])
    await lead_events(backend)
    order = [(row["run_id"], row["kind"]) for row in rows_of(events)]
    assert order.index((f"claude-{outer}", "tool_use")) < order.index((f"claude-{inner}", "status"))
    assert order.index((f"claude-{outer}", "response")) < order.index((f"claude-{inner}", "status"))
    assert {row["agent"] for row in rows_of(events, inner)} == {"checker"}
    assert [row["agent"] for row in rows_of(events, early)][-1] == "reader"
    assert rows_of(events, early)[-1]["status"] == "completed"


def test_the_agent_tool_is_a_delegating_built_in(tmp_path):
    """The bundled CLI's launch tool is Agent (Task its older name): both are delegating built-ins, asked for in ask
    mode and refused in plan mode, as Task was."""
    assert {"Task", "Agent"} <= policy.builtin_names() and policy.capability("Agent") == policy.MUTATING
    assert policy.decide("Agent", {"prompt": "x"}, "plan", tmp_path)[0] == "deny"
    assert policy.decide("Agent", {"prompt": "x"}, "ask", tmp_path)[0] == "ask"


async def test_a_subscriber_failing_on_one_row_loses_that_row_alone():
    """The emitter is the view's: a subscriber failing on one row (here the first response row) loses that row alone.
    The rest of that update -- the tool call it ended with -- and the card's later rows still come, and it ends."""
    p = "toolu_Sub"
    rows, failed = [], []

    def emit(event):
        if event.data["kind"] == "response" and not failed:
            failed.append(event.data)
            raise RuntimeError("the subscriber failed")
        rows.append(event.data)
    backend, _ = claude([launch((p, "explorer", "Task")),
                         said(p, "m1", TextBlock("Look."), ToolUseBlock("t1", "Read", {"file_path": "a"})),
                         results(p, ToolResultBlock("t1", "a")), said(p, "m2", TextBlock("Done.")),
                         lead_gets(p, "Done."), turn_done()])
    backend.activity_emit = emit
    await lead_events(backend)
    assert [row["kind"] for row in rows] == ["status", "request", "tool_use", "tool_result", "request", "response",
                                             "status"]
    assert rows[2]["data"]["id"] == rows[3]["data"]["id"] and rows[-1]["status"] == "completed"


def test_claude_sessions_run_their_sub_agents_in_the_foreground(tmp_path):
    """The lead's decision after the DREAM-212 gate: Dream's Claude sessions set CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1
    in the SDK's environment, so the bundled CLI runs a sub-agent inside the lead's turn, as a local lead's workers
    run (Dream reads a turn up to the SDK's first result). The main session and Dream-posture consultations share
    the builder that sets it; Claude Code's own posture (claude_code_options) is not Dream's to change here."""
    backend = AnthropicBackend(system_prompt="s", mcp_server={}, preapproved_tool_ids=[], agents=None,
                               permission_cb=None, model="m", cwd=str(tmp_path))
    assert backend._build_options().env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
    consult = anthropic_module.consult_options(system_prompt="s", cwd=str(tmp_path), mode="ask", model=None,
                                               effort=None)
    assert consult.env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"


# --- the DREAM-212 gate, round 2: known limits ----------------------------------------------------------------------

async def test_a_card_whose_reply_cannot_be_shown_still_ends_and_so_do_the_others():
    """(Round 2, KL1) A fault while one card's reply is shown -- a malformed text block -- costs that reply only: at the
    lead's result that card still ends, and at the turn's end every other open card still gets its terminal row."""
    bad, good, late = "toolu_Bad", "toolu_Good", "toolu_Late"
    backend, events = claude([launch((bad, "explorer", "Agent"), (good, "coder", "Agent"), (late, "reader", "Agent")),
                              said(bad, "m1", TextBlock(None)), said(good, "m2", TextBlock("Fine.")),
                              said(late, "m3", TextBlock(None)), lead_gets(late, "done"), turn_done()])
    await lead_events(backend)
    assert [row["status"] for row in rows_of(events, late) if row["kind"] == "status"] == ["running", "completed"]
    for p in (bad, good):
        assert rows_of(events, p)[-1]["status"] == "failed", p
    assert any(row["kind"] == "response" and row.get("text") == "Fine." for row in rows_of(events, good))


@pytest.mark.parametrize("text, result, background", [
    ("Async agent launched successfully. The agent is working in the background.", {"status": "completed"}, False),
    ("Launched.", {"status": "async_launched", "agentId": "s1"}, True),
    ("Async agent launched successfully. The agent is working in the background.", None, True),
], ids=["text-but-structured-completed", "structured-only", "text-only"])
async def test_the_structured_result_decides_a_background_launch_and_the_text_only_without_one(text, result,
                                                                                                background):
    """(Round 2, KL2 and KL3) The lead message's tool_use_result says whether the call went to the background; the
    CLI's receipt text counts only when there is no such result."""
    p = "toolu_R"
    answer = UserMessage(content=[ToolResultBlock(tool_use_id=p, content=text)], tool_use_result=result)
    backend, events = claude([launch((p, "explorer", "Agent")), answer, turn_done()])
    await lead_events(backend)
    rows = rows_of(events, p)
    assert (rows[1].get("text") == BACKGROUND) is background
    assert rows[-1]["status"] == ("unknown" if background else "completed")


def test_claude_codes_own_posture_keeps_its_background_tasks(tmp_path):
    """(Round 2, KL3) The lead's decision covers the paths built by sdk_options only: Claude Code's own posture
    (claude_code_options) is not given the flag."""
    options = anthropic_module.claude_code_options(system_prompt="s", cwd=str(tmp_path), mode="ask", model=None,
                                                   effort=None)
    assert "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS" not in options.env
