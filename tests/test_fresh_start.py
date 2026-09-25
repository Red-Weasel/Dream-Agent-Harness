"""DREAM-113, fix list #33: a Fresh start the owner triggers.

Three times on 2026-09-24 the model investigated for hours, wrote nothing, and started writing minutes
after a compaction cleared its context. The plan tracker already compacts at a phase boundary
(DREAM-083); now the owner can do it deliberately, with the Fresh start control in the chat pane or
/fresh. The conversation becomes ONE handoff -- the owner's requests, Dream's last reply, the files
written or edited this session and the open tasks -- through the existing compaction
(_compact_messages to nothing, elided bodies saved as working notes, then _compacted(), which refreshes
the frozen prompt head once on a cache-sensitive engine, DREAM-112). Never mid-turn: the backend
refuses, the app refuses while anything runs, and the control is disabled.
"""
from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace

import httpx
import pytest
from rich.console import Console

from dream.core import system_prompt
from dream.core.backends.base import Event
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.memory import project as project_memory
from dream.memory.store import MemoryStore
from dream.memory.tasks import TaskStore
from dream.memory.working import WorkingMemory
from dream.tools.context import ToolContext, bind_context
from dream.tools.files import str_replace_edit
from dream.tools.native import write_file
from test_cache_friendly_head import FakeEngine, Meter, backend, tool



@pytest.fixture
def session(tmp_path):
    """A real store with this session's transcript, tasks and working notes, a system prompt built the way
    the Engine builds it, and the session's tool context to bind (the Engine binds it around a turn)."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    store = MemoryStore(tmp_path / "t.db")
    store.project = project_memory.project_key(workspace)
    store.start_session("s")
    working = WorkingMemory(store, "s")
    tasks = TaskStore(store)
    tasks.add("Ship the falcon deck")
    context = ToolContext(store=store, working=working, browser=None,  # type: ignore[arg-type]
                          session_id="s", workspace=workspace, emit=None, tasks=tasks)
    yield SimpleNamespace(store=store, working=working, tasks=tasks, context=context, workspace=workspace,
                          prompt=lambda: system_prompt.build_system_prompt(store, "s", workspace=workspace))
    store.close()


async def owner_turn(s, b, prompt):
    """One turn the way the Engine runs it: the owner's words go to the transcript, the backend runs bound
    to the session, and each reply joins the transcript."""
    s.working.log_turn("user", prompt)
    with bind_context(s.context):
        events = [ev async for ev in b.ask(prompt)]
    for ev in events:
        if ev.kind == "assistant_done":
            s.working.log_turn("assistant", ev.data)
    return events


def fresh(s, b):
    with bind_context(s.context):
        return b.fresh_start()


def build_session(s, *, key="machx", base_url="http://engine.test/v1"):
    engine = FakeEngine(lead=[
        {"calls": [("write_file", {"path": "index.html", "content": "<h1>v1</h1>"})]},
        {"calls": [("str_replace_edit", {"path": "index.html", "old_string": "v1", "new_string": "v2"})]},
        {"calls": [("probe", {})]},
        {"text": "The launch page is written; the booster landing is next."},
        {"text": "Found it: the landing burn starts 2 s late."},
    ])
    b = backend(engine, tools=[write_file, str_replace_edit, tool("probe", "OLD-TOOL-RESULT " * 50)],
                system=s.prompt(), key=key, base_url=base_url)
    b.runtime_meter = Meter()
    return engine, b


async def test_a_fresh_start_leaves_the_head_and_one_handoff_and_nothing_older(session):
    engine, b = build_session(session)
    await owner_turn(session, b, "Build the launch page")
    await owner_turn(session, b, "Why does the booster land late?")
    assert len(b.messages) > 6 and "OLD-TOOL-RESULT" in json.dumps(b.messages)
    head_before = b.messages[0]["content"]

    events = fresh(session, b)

    assert [m["role"] for m in b.messages] == ["system", "user"]              # nothing older
    handoff = b.messages[1]
    assert handoff["name"] == "dream_fresh_start"
    text = handoff["content"]
    assert text.startswith("[Dream, not from the user:")
    assert "Build the launch page" in text and "Why does the booster land late?" in text   # the summary
    assert text.index("Build the launch page") < text.index("Why does the booster land late?")
    assert "Found it: the landing burn starts 2 s late." in text                           # where it left off
    assert "- index.html — wrote, edited" in text                                          # the manifest
    assert "#1 Ship the falcon deck [open]" in text                                         # open tasks
    assert 'read_session(id="s")' in text and "read_notes" in text                         # recovery
    assert "OLD-TOOL-RESULT" not in json.dumps(b.messages)
    assert b.messages[0]["content"] == head_before                            # nothing changed: the same head
    (line,) = [e.data for e in events if e.kind == "system"]
    assert line.startswith("Fresh start:") and "handoff" in line
    (name, fields), = [(n, f) for n, f in b.runtime_meter.records if n == "fresh_start"]
    assert fields["messages"] >= 6 and fields["files"] == 1 and fields["notes"] >= 1
    saved = session.working.notes()
    assert any("OLD-TOOL-RESULT" in n["note"] for n in saved)                 # the elided bodies went to notes

    await owner_turn(session, b, "continue")                                  # the next turn starts from it
    sent = engine.requests[-1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "user"]
    assert sent[1]["content"] == text and sent[2]["content"].startswith("continue")


async def test_the_head_is_refreshed_exactly_once_on_a_cache_sensitive_engine(session, monkeypatch):
    _, b = build_session(session)
    await owner_turn(session, b, "Build the launch page")
    session.tasks.add("Fix the landing burn")                                 # changed since the head was built
    calls = []
    real = system_prompt.refresh_wake

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(system_prompt, "refresh_wake", counting)
    fresh(session, b)
    assert len(calls) == 1
    assert "- #2 Fix the landing burn [open]" in b.messages[0]["content"]      # the head caught up at the boundary
    assert b._last_prompt_tokens == 0                                         # the stale count is gone


async def test_a_backend_that_is_not_cache_sensitive_keeps_its_head_until_the_next_message(session, monkeypatch):
    _, b = build_session(session, key="openai", base_url="https://api.example.com/v1")
    await owner_turn(session, b, "Build the launch page")
    calls = []
    monkeypatch.setattr(system_prompt, "refresh_wake", lambda *a, **k: calls.append(1))
    fresh(session, b)
    assert calls == [] and [m["role"] for m in b.messages] == ["system", "user"]


async def test_a_fresh_start_is_refused_mid_turn(session):
    seen = {}
    b = None

    async def handler(args):
        try:
            with bind_context(session.context):
                b.fresh_start()
        except ValueError as exc:
            seen["refused"] = str(exc)
        return {"content": [{"type": "text", "text": "probed"}]}

    engine = FakeEngine(lead=[{"calls": [("probe", {})]}, {"text": "done"}, {"text": "later"}])
    b = backend(engine, tools=[SimpleNamespace(name="probe", description="probe tool",
                                               input_schema={"type": "object", "properties": {}}, handler=handler)],
                system=session.prompt())
    await owner_turn(session, b, "probe it")
    assert "between turns" in seen["refused"]
    assert any(m.get("role") == "tool" for m in b.messages), "the turn was not compacted"
    assert b._turn_active is False
    fresh(session, b)                                                          # after the turn it runs
    assert [m["role"] for m in b.messages] == ["system", "user"]


async def test_an_empty_conversation_has_nothing_to_hand_off(session):
    _, b = build_session(session)
    events = fresh(session, b)
    assert [m["role"] for m in b.messages] == ["system"]
    assert "nothing to hand off" in events[0].data


async def test_the_manifest_records_successful_changes_only(session):
    engine = FakeEngine(lead=[
        {"calls": [("write_file", {"path": "a.txt", "content": "one"})]},
        {"calls": [("str_replace_edit", {"path": "a.txt", "old_string": "absent", "new_string": "x"})]},  # fails
        {"calls": [("str_replace_edit", {"path": "missing.txt", "old_string": "x", "new_string": "y"})]},  # fails
        {"calls": [("write_file", {"path": "a.txt", "content": " two", "append": True})]},
        {"text": "done"},
    ])
    b = backend(engine, tools=[write_file, str_replace_edit], system=session.prompt())
    await owner_turn(session, b, "write it")
    fresh(session, b)
    text = b.messages[1]["content"]
    assert "- a.txt — wrote, appended" in text                                # the two failed edits left no trace
    assert "missing.txt" not in text and "a.txt — wrote, edited" not in text


def test_the_ledger_is_bounded_and_keeps_the_newest(tmp_path):
    from dream.core.handoff import FileLedger

    ledger = FileLedger(limit=3)
    for name in ("a", "b", "c", "d"):
        ledger.record("write_file", {"path": f"{name}.txt"}, tmp_path)
    ledger.record("str_replace_edit", {"path": "b.txt"}, tmp_path)            # an old one, touched again
    assert len(ledger) == 3
    assert ledger.lines() == ["- b.txt — wrote, edited", "- d.txt — wrote", "- c.txt — wrote"]
    ledger = FileLedger()
    for name in ("c", "d"):
        ledger.record("write_file", {"path": f"{name}.txt"}, tmp_path)
    ledger.record("copy_files", {"files": [{"src": "d.txt", "dest": "e/d.txt", "move": True},
                                           {"src": "c.txt", "dest": "f.txt"}]}, tmp_path)
    ledger.record("delete_file", {"paths": ["c.txt"]}, tmp_path)
    assert ledger.lines() == ["- c.txt — wrote, deleted", "- f.txt — copied", "- d.txt — wrote, moved away",
                              "- e/d.txt — moved"]
    outside = (tmp_path.parent / "outside.txt").resolve()
    ledger.record("write_file", {"path": str(outside)}, tmp_path)
    assert ledger.lines()[0] == f"- {outside} — wrote"                        # outside the workspace: its full path
    for junk in ({"path": 3}, {"paths": "x"}, {"files": ["x"]}, {}):
        ledger.record("delete_file", junk, tmp_path)
        ledger.record("copy_files", junk, tmp_path)
    ledger.record("run_bash", {"command": "echo hi > shell.txt"}, tmp_path)   # a shell write is not tracked
    assert "shell.txt" not in "\n".join(ledger.lines())


async def test_a_long_session_keeps_the_first_requests_and_the_latest(session):
    _, b = build_session(session)
    for i in range(14):
        session.working.log_turn("user", f"request number {i:02d}")
    session.working.log_turn("assistant", "the last reply")
    b.messages.append({"role": "user", "content": "x"})
    fresh(session, b)
    text = b.messages[1]["content"]
    assert "request number 00" in text and "request number 01" in text and "request number 13" in text
    assert "request number 05" not in text and "more requests in between" in text
    assert len(text) <= 16_000


# --- the Engine: a backend without one says /new ------------------------------------------------------


def test_an_engine_whose_backend_has_no_fresh_start_says_use_new():
    from dream.core.engine import Engine

    engine = Engine.__new__(Engine)
    engine.backend = SimpleNamespace()                                        # e.g. the Claude SDK's own context
    engine._tool_context = None
    with pytest.raises(ValueError, match="/new"):
        engine.fresh_start()


async def test_the_engine_binds_the_session_context_for_the_backend(session):
    from dream.core.backends import openai_compat
    from dream.core.engine import Engine

    _, b = build_session(session)
    await owner_turn(session, b, "Build the launch page")
    engine = Engine.__new__(Engine)
    engine.backend = b
    engine._tool_context = session.context
    bound = {}
    real = b.fresh_start

    def spy():
        bound["context"] = openai_compat._bound_context()
        return real()

    b.fresh_start = spy
    events = engine.fresh_start()
    assert bound["context"] is session.context
    assert events and events[0].data.startswith("Fresh start:")


# --- the app: the control refuses while anything runs; /fresh -------------------------------------------


@pytest.fixture
def app(tmp_path):
    from dream.tui.app import App

    obj = App.__new__(App)
    obj.workspace = tmp_path
    obj._gui_prompts = asyncio.Queue(maxsize=32)
    obj._deferred_gui_prompt = None
    obj._council_pending = None
    obj._accepting_input = True
    obj._interrupt_target = None
    obj._active_loop = None
    obj.studio = None
    obj.bus = EventBus()
    shown = []
    obj.renderer = SimpleNamespace(console=Console(record=True), system=shown.append, error=shown.append)
    obj.friction = SimpleNamespace(record=lambda *a: None)
    obj.meter = SimpleNamespace(feed=lambda *a: None)
    calls = []

    def fresh_start():
        calls.append(1)
        return [Event("system", "Fresh start: the conversation is now a handoff.")]

    obj.engine = SimpleNamespace(fresh_start=fresh_start, store=None)
    obj.shown, obj.calls = shown, calls
    return obj


async def test_the_control_refuses_while_dream_is_busy_and_runs_when_idle(app):
    app._accepting_input = False                                              # a turn (or a command) is running
    with pytest.raises(ValueError, match="idle"):
        await app._runtime_control({"action": "fresh_start"})
    app._accepting_input = True
    app._gui_prompts.put_nowait("a queued message")                           # queued input waits its turn
    with pytest.raises(ValueError, match="idle"):
        await app._runtime_control({"action": "fresh_start"})
    app._gui_prompts.get_nowait()
    assert app.calls == []
    result = await app._runtime_control({"action": "fresh_start"})
    assert app.calls == [1] and result["notices"] == ["Fresh start: the conversation is now a handoff."]
    assert "Fresh start: the conversation is now a handoff." in app.shown                    # the terminal
    events = app.bus.conversation.snapshot()["events"]
    assert {"kind": "system", "data": "Fresh start: the conversation is now a handoff."} in events   # Studio


async def test_the_route_refuses_mid_turn_and_answers_between_turns(app):
    srv = StudioServer(app.bus, on_control=app._runtime_control)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://test",
                                 headers={"X-Dream-Token": srv.token}) as client:
        app._accepting_input = False
        r = await client.post("/api/control", json={"action": "fresh_start"})
        assert r.status_code == 400 and "idle" in r.json()["error"] and app.calls == []
        app._accepting_input = True
        r = await client.post("/api/control", json={"action": "fresh_start"})
        assert r.status_code == 200 and r.json()["result"]["notices"] and app.calls == [1]


async def test_slash_fresh_in_the_terminal_and_the_use_new_answer(app):
    assert await app._command("/fresh") is False
    assert app.calls == [1] and "Fresh start: the conversation is now a handoff." in app.shown

    def unsupported():
        raise ValueError("Fresh start is not available here. Use /new to start a new session.")

    app.engine.fresh_start = unsupported
    await app._command("/fresh")
    assert any("/new" in line for line in app.shown)


def test_help_lists_fresh():
    from dream.tui.app import HELP

    assert "/fresh" in HELP


# --- the chat pane --------------------------------------------------------------------------------------


SHOTS = os.environ.get("DREAM_FRESH_SHOTS")


@pytest.mark.parametrize("layout", ["", "&companion=1"])
async def test_the_chat_pane_control_appears_waits_for_the_turn_and_hits_the_route(tmp_path, monkeypatch, layout):
    from playwright.async_api import async_playwright, expect

    monkeypatch.delenv("DREAM_DESKTOP_SESSION_FILE", raising=False)
    controls, prompts = [], []
    state = {"busy": False}

    def on_control(payload):
        controls.append(payload)
        if payload.get("action") != "fresh_start":
            return {}
        if state["busy"]:
            raise ValueError("Fresh start waits until Dream is idle.")
        srv.bus.publish(Event("system", "Fresh start: the conversation is now a handoff."))
        return {"notices": ["Fresh start: the conversation is now a handoff."]}

    srv = StudioServer(EventBus(), on_prompt=prompts.append, on_control=on_control,
                       session={"workspace": str(tmp_path), "model": "fixture"})
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--disable-gpu"])
            page = await browser.new_page(viewport={"width": 1100, "height": 800})
            page.set_default_timeout(4000)
            await page.goto(url + layout)
            control = page.locator("#fresh-start")
            await expect(control).to_be_visible()
            await expect(control).to_be_enabled()
            if SHOTS:
                await page.screenshot(path=os.path.join(SHOTS, f"fresh-idle{layout and '-companion'}.png"))
            srv.bus.publish(Event("turn_start", {}))                          # a turn runs: the control waits
            await expect(control).to_be_disabled()
            if SHOTS:
                await page.screenshot(path=os.path.join(SHOTS, f"fresh-busy{layout and '-companion'}.png"))
            srv.bus.publish(Event("turn_end", {}))
            await expect(control).to_be_enabled()
            await control.click()
            await expect(page.locator("#stream .sys", has_text="Fresh start: the conversation")).to_have_count(1)
            assert [c for c in controls if c.get("action") == "fresh_start"] == [{"action": "fresh_start"}]
            await page.locator("#input").fill("/fresh")                       # the slash command, same route
            await page.locator("#input").press("Enter")
            await expect(page.locator("#stream .sys", has_text="Fresh start: the conversation")).to_have_count(2)
            await expect(page.locator("#input")).to_have_value("")
            state["busy"] = True                                              # the route refuses mid-turn
            await page.locator("#input").fill("/fresh")
            await page.locator("#input").press("Enter")
            await expect(page.locator("#stream .sys", has_text="waits until Dream is idle")).to_have_count(1)
            assert len([c for c in controls if c.get("action") == "fresh_start"]) == 3
            assert prompts == []                                              # never sent to the model
            await browser.close()
    finally:
        await srv.stop()


# --- the owner's words only (DREAM-113 gate, B1) -------------------------------------------------------
# The transcript logs Dream's own prompts as role "user" too: the progress guard's notes (through the
# steering inbox), the autonomous loop's prompts and /review's. In the gate's run -- 3 requests, then 60
# read-only steps, so 9 guard notes -- the handoff's newest "request" was a guard note and the owner's
# latest was hidden among "3 more requests in between".


@pytest.fixture
def live(tmp_path, monkeypatch):
    """A real Engine with the real steering inbox and progress guard, on the real HTTP backend and a fake
    engine: the path the owner's chat takes."""
    from pathlib import Path as _Path

    from dream import config
    from dream.core.engine import Engine
    from dream.tools import installed_skill_tools
    from dream.tools.native import read_file

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setenv("DREAM_SKILL_DIRS", str(_Path(__file__).resolve().parents[1] / "skills"))
    monkeypatch.setattr(installed_skill_tools, "_CACHE", None)
    monkeypatch.delenv("DREAM_PROGRESS_GUARD_AFTER", raising=False)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    for i in range(60):
        (workspace / f"f{i:02d}.md").write_text(f"# note {i}\n")
    engine = Engine(provider="openai", workspace=workspace, profile="lean")
    engine.store = MemoryStore(tmp_path / "memory.db")
    engine.store.project = project_memory.project_key(workspace)
    engine.store.start_session(engine.session_id)
    engine.working = WorkingMemory(engine.store, engine.session_id)
    engine._tool_context = ToolContext(engine.store, engine.working, None, engine.session_id,  # type: ignore[arg-type]
                                       workspace=workspace, tasks=TaskStore(engine.store))
    fake = FakeEngine(lead=[{"text": "The page is up."}, {"text": "Checked the scene."},
                            *({"calls": [("read_file", {"path": f"f{i:02d}.md"})]} for i in range(60)),
                            {"text": "The burn starts 2 s late."}])
    engine.backend = backend(fake, tools=[read_file], system="SYSTEM PROMPT")
    engine._started = True
    yield engine
    engine.store.close()


def requests_section(text):
    return text.split("## What the owner asked", 1)[1].split("## Where you left off", 1)[0]


async def test_the_progress_guards_notes_are_not_the_owners_requests(live):
    asks = ["Build the launch page", "Check the scene graph", "Fix the landing burn timing"]
    for ask in asks:
        [ev async for ev in live.ask_chat(ask)]
    live.fresh_start()
    section = requests_section(live.backend.messages[1]["content"])
    listed = [line.split(". ", 1)[1] for line in section.splitlines() if line[:1].isdigit()]
    assert listed == asks, listed                                 # the owner's three, the latest last
    assert "[Dream progress guard]" not in section and "more requests in between" not in section
    assert "9 prompts Dream wrote itself" in section
    notes = [t for t in live.store.session_turns(live.session_id, limit=1000)
             if t["role"] == "user" and t["content"].startswith("[Dream progress guard]")]
    assert len(notes) == 9                                        # 60 read-only steps: notes at 12, 18, ... 60
    assert {t["tool_name"] for t in notes} == {"dream:progress_guard"}      # marked where they are logged


async def test_an_owner_correction_through_the_steering_inbox_stays_the_owners(session):
    from dream.core.steering import SteeringInbox

    inbox = SteeringInbox(session.workspace / "receipts.json", "s", 1, session.working.log_turn)
    session.working.log_turn("user", "Build the launch page")
    await inbox.submit("Use blue for the booster", "a" * 32)
    from dream.core import handoff
    asked, _, generated = handoff.transcript(session.store, "s")
    assert asked == ["Build the launch page", "Use blue for the booster"] and generated == 0


async def test_older_transcripts_are_told_by_the_text_of_dreams_prompts(session):
    from dream.core import handoff, loop, review
    for text in ("Build the launch page",
                 "[Dream progress guard] 12 consecutive read-only steps and no change to a project file this turn.",
                 loop._CONTRACT_ASK.format(goal="ship it"),
                 loop._SEED.format(goal="ship it", contract="- done", workspace="/w", worker_workspace="/w"),
                 loop._CONTINUE, loop._NUDGE,
                 review._PROMPT.split("\n", 1)[0],
                 "Why does the progress guard keep firing? AUTONOMOUS MODE is off."):
        session.working.log_turn("user", text)                  # no marker: a transcript from before it
    asked, _, generated = handoff.transcript(session.store, "s")
    assert asked == ["Build the launch page", "Why does the progress guard keep firing? AUTONOMOUS MODE is off."]
    assert generated == 6


async def test_a_loop_prompt_is_marked_where_it_is_logged_and_left_out(live):
    from dream.core import handoff
    from dream.core.loop import AutonomousLoop

    live.backend = type("Recording", (), {
        "prepare_turn": lambda self, tools: None,
        "ask": lambda self, prompt: _one_reply(prompt)})()
    [ev async for ev in live.ask("Plan the landing sequence")]
    runner = AutonomousLoop(live, evaluate=False, state_dir=live.workspace / "loop-state")
    await runner._drive("AUTONOMOUS MODE — before doing any work, define what DONE means.\n\nGoal:\n    x")
    users = [t for t in live.store.session_turns(live.session_id) if t["role"] == "user"]
    assert [t["tool_name"] for t in users] == [None, "dream:loop"]
    asked, _, generated = handoff.transcript(live.store, live.session_id)
    assert asked == ["Plan the landing sequence"] and generated == 1


async def _one_reply(prompt):
    yield Event("assistant_done", "ok")
    yield Event("result", {"is_error": False, "subtype": "success"})


async def test_a_review_prompt_is_marked_where_it_is_logged(live, monkeypatch, tmp_path):
    from dream.core import review as review_mod
    from dream.tui.app import App

    live.backend = type("Recording", (), {
        "prepare_turn": lambda self, tools: None,
        "ask": lambda self, prompt: _one_reply(prompt)})()

    async def run_review(workspace, ask, **kwargs):
        await ask("You are an independent code reviewer. You did NOT write this change ...")
        return review_mod.ReviewResult("no-changes", (), "", (), False)

    monkeypatch.setattr(review_mod, "run_review", run_review)
    app = App.__new__(App)
    app.engine, app.workspace = live, live.workspace
    app.renderer = SimpleNamespace(console=Console(record=True), system=lambda *a: None, live_begin=lambda *a: None,
                                   live_end=lambda *a: None, working=lambda *a: None)
    app.meter = SimpleNamespace(turn_start=lambda: None, turn_end=lambda: None)
    app._footer = None

    async def run_turn(coro):
        return await coro

    app._run_turn = run_turn
    await app._review("")
    users = [t for t in live.store.session_turns(live.session_id) if t["role"] == "user"]
    assert [t["tool_name"] for t in users] == ["dream:review"]


async def test_the_size_it_reports_is_the_rewritten_historys_not_the_old_counts(live):
    """The old history's count anchors the usual measure; after a wholesale rewrite it priced the handoff
    at 46 tokens in the gate scenario, less than the handoff's own text. The line reports the next
    request as priced from scratch: the head, the handoff and the tools."""
    import re

    for ask in ["Build the launch page", "Check the scene graph", "Fix the landing burn timing"]:
        [ev async for ev in live.ask_chat(ask)]
    meter = Meter()
    live.backend.runtime_meter = meter
    (line,) = [e.data for e in live.fresh_start()]
    before, after = (int(n.replace(",", "")) for n in re.findall(r"about ([\d,]+) tokens", line))
    handoff_chars = len(live.backend.messages[1]["content"])
    assert after >= handoff_chars // 4 and after < before, (after, handoff_chars, line)
    assert after == live.backend._ctx_fill()                     # what the next request will be priced at
    (_, fields), = [(n, f) for n, f in meter.records if n == "fresh_start"]
    assert fields["after"] == after


async def test_a_second_fresh_start_carries_every_earlier_note_pointer(session):
    """B5: two fresh starts in a row. The first handoff named the notes its compaction saved; the second
    compaction kept that handoff whole and the slice dropped it, so its pointer was lost. A note an
    earlier ordinary compaction named in a stub is carried too: nothing the model could reach becomes
    unreachable."""
    _, b = build_session(session)
    await owner_turn(session, b, "Build the launch page")
    fresh(session, b)
    first = b.messages[1]["content"]
    saved = sorted(n["id"] for n in session.working.notes(False))
    assert saved and f"#{saved[0]}" in first
    fresh(session, b)                                                  # again, with no turn between
    second = b.messages[1]["content"]
    assert f"#{saved[0]}" in second and f"#{saved[-1]}" in second, second
    b.messages.append({"role": "assistant", "content": "[elided: probe result, 5000 chars — note #4242]"})
    b.messages.append({"role": "user", "content": "go on, and see note #777 of the spec\n\n[id:m0009]"})
    fresh(session, b)                                                  # a stub from an ordinary compaction
    third = b.messages[1]["content"]
    assert "#4242" in third and f"#{saved[0]}" in third, third
    assert "#777" not in third.split("## To recover detail", 1)[1]   # prose that mentions a note is not a stub


def test_note_ids_are_listed_as_ranges():
    from dream.core.handoff import note_ranges

    assert note_ranges([3, 1, 2, 7, 9, 10, 11]) == "#1–#3, #7, #9–#11"
    assert note_ranges(list(range(1, 200, 2)), shown=3) == "#1, #3, #5 (+97 more)"
