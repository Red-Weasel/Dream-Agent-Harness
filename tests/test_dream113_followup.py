"""DREAM-113 follow-up: the gate's four notes and fix list #76.

1. project_outline read the Understand map with Path.read_text after a check that resolved it: a .ua folder
   (or the map file) swapped for a link out between the check and the read showed the outside map. The
   map and the domain graph are now read through page_server's pinned open, like every scan read.
2. Three prompts Dream writes itself were logged as the owner's: /resume's priming prompt, /learn analyze's
   and the Council work wrapper; the follow-up's re-check found a fourth, a guided task's prompt. They carry
   their origin now; the Fresh start handoff lists a Council task and a guided task's goal in the owner's own
   words. A static scan lists every place a user-role turn is logged and every place a prompt reaches the
   Engine, so a new unmarked one fails here.
3. The owner's turns are logged exactly as before: no marker and no new keyword.
4. A map edge whose endpoint is a list or an object made the outline raise TypeError; it falls back to the
   scan and says why.
5. #76: the chat pane showed the progress guard's notes as the owner's corrections ("Correction saved…"),
   and the project library's handoff draft could quote a guard note as the latest user request.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import re
import shutil
from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

import dream
from dream.core import handoff, turn_origin
from dream.core.backends.base import Event
from dream.core.steering import SteeringInbox
from dream.memory import project as project_memory
from dream.memory.store import MemoryStore
from dream.memory.tasks import TaskStore
from dream.memory.working import WorkingMemory
from dream.tools.context import ToolContext
from test_desktop_companion import studio  # noqa: F401
from test_fresh_start import live  # noqa: F401
from test_project_outline import _outline_module, outline_text, small_project, write_map, ws  # noqa: F401


# --- a real Engine on a backend that answers each prompt once ------------------------------------------


async def _one_reply(prompt):
    yield Event("assistant_done", "ok")
    yield Event("result", {"is_error": False, "subtype": "success"})


@pytest.fixture
def real(tmp_path, monkeypatch):
    """The Engine that logs the transcript (Engine._log_user_turn), bound to a store; its backend answers each
    prompt with one reply."""
    from dream import config
    from dream.core.engine import Engine
    from dream.tools import installed_skill_tools

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
    engine.backend = type("Recording", (), {"prepare_turn": lambda self, tools: None,
                                            "ask": lambda self, prompt: _one_reply(prompt)})()
    engine._started = True
    yield engine
    engine.store.close()


def turn_runner(engine):
    """App._ask as the Engine sees it: `_run_turn` runs the turn as its own task and `_stream` hands the prompt to
    ask_chat (a guided task's to ask)."""
    async def _ask(prompt, *, workflow=None):
        async def stream():
            async for _ in (engine.ask_chat(prompt) if workflow is None else engine.ask(prompt)):
                pass
            return True
        return await asyncio.ensure_future(stream())
    return _ask


def user_turns(engine):
    return [t for t in engine.store.session_turns(engine.session_id, limit=1000) if t["role"] == "user"]


# --- 1. the map is read through the pinned open ----------------------------------------------------------


@pytest.mark.parametrize("swap", ["folder", "file"])
async def test_a_map_swapped_for_a_link_after_the_check_is_not_read(ws, tmp_path, monkeypatch, swap):
    """The gate's probe: _find_map checks that the map stays inside the workspace, then .ua (or the map file) is
    swapped for a link to another project's map before it is read."""
    mod = _outline_module()
    small_project(ws)
    write_map(ws)
    elsewhere = tmp_path / "elsewhere"
    write_map(elsewhere)
    outside = elsewhere / ".ua" / "knowledge-graph.json"
    graph = json.loads(outside.read_text())
    graph["layers"][0]["name"] = "OUTSIDE-LAYER"
    outside.write_text(json.dumps(graph))
    checked = mod._find_map

    def racing(root):
        found = checked(root)
        if swap == "folder":
            shutil.rmtree(ws / ".ua")
            (ws / ".ua").symlink_to(elsewhere / ".ua", target_is_directory=True)
        else:
            (ws / ".ua" / "knowledge-graph.json").unlink()
            (ws / ".ua" / "knowledge-graph.json").symlink_to(outside)
        return found

    monkeypatch.setattr(mod, "_find_map", racing)
    text = await outline_text()
    assert "OUTSIDE-LAYER" not in text and "Core simulation" not in text
    head = text.splitlines()[0]
    assert ("could not read the Understand map (.ua/knowledge-graph.json: a link or a non-folder on its path, "
            "not followed)") in head
    assert "so a quick scan" in head and "app/scene.py" in text


async def test_the_domain_graph_is_read_through_the_pinned_open_too(ws, tmp_path, monkeypatch):
    mod = _outline_module()
    small_project(ws)
    write_map(ws)
    elsewhere = tmp_path / "elsewhere"
    write_map(elsewhere)
    outside = elsewhere / ".ua" / "domain-graph.json"
    graph = json.loads(outside.read_text())
    graph["nodes"][0]["name"] = "OUTSIDE-DOMAIN"
    outside.write_text(json.dumps(graph))
    checked = mod._find_map

    def racing(root):
        found = checked(root)
        (ws / ".ua" / "domain-graph.json").unlink()
        (ws / ".ua" / "domain-graph.json").symlink_to(outside)
        return found

    monkeypatch.setattr(mod, "_find_map", racing)
    text = await outline_text()
    assert "OUTSIDE-DOMAIN" not in text
    assert "Core simulation" in text and "Domains" not in text      # the map's own layers, no domains


# --- 4. a map edge with a non-string endpoint --------------------------------------------------------------


@pytest.mark.parametrize("end,value", [("source", ["file:app/scene.py"]), ("target", {"id": "file:app/physics.py"})])
async def test_a_map_edge_with_a_non_string_endpoint_falls_back_to_the_scan(ws, end, value):
    small_project(ws)
    write_map(ws)
    p = ws / ".ua" / "knowledge-graph.json"
    graph = json.loads(p.read_text())
    graph["edges"][4][end] = value
    p.write_text(json.dumps(graph))
    text = await outline_text()
    head = text.splitlines()[0]
    assert "could not read the Understand map (.ua/knowledge-graph.json: map edge 4 has a non-string endpoint)" in head
    assert "so a quick scan" in head and "app/scene.py (42 lines)" in text


async def test_a_domain_edge_with_a_non_string_endpoint_leaves_the_domains_out(ws):
    small_project(ws)
    write_map(ws)
    p = ws / ".ua" / "domain-graph.json"
    graph = json.loads(p.read_text())
    graph["edges"][0]["target"] = ["flow:liftoff"]
    p.write_text(json.dumps(graph))
    text = await outline_text()
    assert "Core simulation" in text and "Domains" not in text      # the map outline, as for any unreadable domain file


# --- 2. /resume, /learn analyze and Council work mark their prompts -----------------------------------------


async def test_resume_marks_its_priming_prompt_and_the_owners_next_message_is_not_marked(real):
    from dream.tui.app import App

    real.store.start_session("old-session", "Earlier work")
    real.store.add_turn("old-session", "user", "Build the launch page")
    real.store.add_turn("old-session", "assistant", "The page is up.")
    app = App.__new__(App)
    app.engine = real
    app.renderer = SimpleNamespace(console=Console(record=True))
    app._ask = turn_runner(real)
    await app._resume("old-session")
    await app._ask("Now fix the landing burn")                         # the owner's next message
    assert [(t["content"][:41], t["tool_name"]) for t in user_turns(real)] == [
        ("[context restored from a previous session", "dream:resume"), ("Now fix the landing burn", None)]
    asked, _, generated = handoff.transcript(real.store, real.session_id)
    assert asked == ["Now fix the landing burn"] and generated == 1


async def test_learn_analyze_marks_its_prompt(real, monkeypatch):
    from dream.tui import learn_cmd

    monkeypatch.setattr(learn_cmd.demos, "read", lambda identifier: {"status": "ready", "name": "Export flow"})
    errors = []
    app = SimpleNamespace(engine=real, _ask=turn_runner(real),
                          renderer=SimpleNamespace(system=errors.append, info=errors.append, error=errors.append))
    await learn_cmd.command(app, "analyze demo-1")
    assert errors == []
    (turn,) = user_turns(real)
    assert turn["content"].startswith("Analyze my demonstration demo-1 (Export flow).")
    assert turn["tool_name"] == "dream:learn"


@pytest.fixture
def council(tmp_path, real):
    """CouncilControls with two members; each member's work turn goes to the real Engine (tests/test_council_work.py's
    worker, with the Engine logging)."""
    from dream.core.moe import MoeConfig
    from dream.tui.council import CouncilControls

    class Worker(CouncilControls):
        pass

    obj = Worker()
    obj.workspace = tmp_path
    obj.bus = SimpleNamespace(publish=lambda ev: None)
    obj.renderer = SimpleNamespace(system=lambda text: None)
    obj.engine = SimpleNamespace(_moe=MoeConfig("machx", ["codex", "gemini"]), model="local", effort=None,
                                 provider=SimpleNamespace(key="machx"), provider_label="Local")
    obj.interrupted = False

    async def configure(cfg, model=None):
        obj.engine._moe = cfg
        obj.engine.provider = SimpleNamespace(key=cfg.orchestrator)
        obj.engine.provider_label = cfg.orchestrator
        obj.engine.model = model

    obj.engine.configure_council = configure
    obj._ask = turn_runner(real)
    return obj


async def test_council_work_marks_its_wrapper_and_the_handoff_lists_the_owners_question(council, real):
    """After Dream's wrapper (about 360 characters) only 130-145 characters of the owner's question survived the
    handoff's 500-character clip; the handoff now lists the question itself, once for the whole relay."""
    question = "Rework the landing burn: " + "keep the booster's timing table intact, " * 9 + "and END-OF-QUESTION."
    real.working.log_turn("user", "Build the launch page")
    await council._work_council(question)
    turns = user_turns(real)
    assert [t["tool_name"] for t in turns] == [None, "dream:council", "dream:council"]    # one per member
    assert all(t["content"].endswith("User task:\n" + question) for t in turns[1:])
    asked, _, generated = handoff.transcript(real.store, real.session_id)
    assert asked == ["Build the launch page", question] and generated == 2
    text = handoff.compose(handoff.Parts(asked=asked, generated=generated), handoff.FileLedger(), [])
    assert "2. " + question in text                                   # whole: under the clip
    assert "You are taking an active work turn" not in text


async def test_a_guided_task_prompt_is_marked_and_the_handoff_lists_the_owners_goal(real, monkeypatch):
    """The follow-up's re-check: a guided task's prompt (workflows/service.py) is Dream's text around the owner's goal
    -- "Guided task <id>, attempt N.", "User goal: …", then Dream's sources, format and save-path instructions -- and
    App.run sent it unmarked, so the handoff listed the wrapper as the owner's words. The owner's typed line through
    the same loop stays unmarked. A goal longer than the handoff's read keeps its start."""
    from dream.tui.app import App, QueuedPrompt
    from dream.workflows.service import WorkflowService

    service = WorkflowService(real.workspace)
    short = "Write the launch report.\nCover the landing burn timing and what the " + "telemetry shows, " * 12 + "END."
    long = "Summarise every booster flight so far. " + "Keep the timing table and name each source. " * 70
    tasks = [service.create("report", {"goal": goal, "sources": ""}) for goal in (short, long)]
    inputs = ["Build the launch page",
              *(QueuedPrompt(t["prompt"], t["id"], t["attempt"], t["version"], str(real.workspace.resolve()))
                for t in tasks)]

    async def next_input():
        if not inputs:
            raise EOFError
        return inputs.pop(0)

    async def nothing():
        return None

    app = App.__new__(App)
    app.engine, app._ask, app._next_input = real, turn_runner(real), next_input
    app.start = app._shutdown = nothing
    app.renderer = SimpleNamespace(sep=lambda: None)
    monkeypatch.setattr(app, "_sigint_cancels", lambda task: contextlib.nullcontext(), raising=False)
    await app.run()
    turns = user_turns(real)
    assert [t["tool_name"] for t in turns] == [None, "dream:guided", "dream:guided"]
    assert turns[1]["content"].startswith(f"Guided task {tasks[0]['id']}, attempt 1.\nUser goal: {short}\n")
    asked, _, generated = handoff.transcript(real.store, real.session_id)
    assert asked[:2] == ["Build the launch page", short] and generated == 2
    assert len(asked) == 3 and long.startswith(asked[2]) and len(asked[2]) > 1_000
    text = handoff.compose(handoff.Parts(asked=asked, generated=generated), handoff.FileLedger(), [])
    assert "Save the final artifact" not in text and "Guided task" not in text


async def test_a_long_council_task_is_listed_once_though_the_members_labels_differ(council, real):
    """The transcript read keeps a user turn's first 2,000 characters, and each member's wrapper opens with its own
    label: a long task comes back as two prefixes of different lengths. It is still one request."""
    question = "Rework the landing burn. " + "Keep the booster's timing table intact and log every change. " * 60
    council.engine._moe = replace(council.engine._moe, advisors=["codex", "a-member-with-a-much-longer-name"])
    await council._work_council(question)
    asked, _, generated = handoff.transcript(real.store, real.session_id)
    assert len(asked) == 1 and question.startswith(asked[0]) and generated == 2


# --- 3. the owner's turns are logged as before -------------------------------------------------------------


def spy_on(monkeypatch, working):
    calls = []
    original = working.log_turn

    def log_turn(*args, **kwargs):
        calls.append((args, dict(kwargs)))
        return original(*args, **kwargs)

    monkeypatch.setattr(working, "log_turn", log_turn)
    return calls


async def test_the_owners_chat_turns_are_logged_with_no_marker_and_no_new_keyword(real, monkeypatch):
    """The record's "no new keyword" claim, pinned here (tests/test_council_context.py's version fails in its
    fixture before it runs): the owner's prompt reaches log_turn as ("user", prompt) and nothing else."""
    calls = spy_on(monkeypatch, real.working)
    [ev async for ev in real.ask_chat("ordinary input")]
    [ev async for ev in real.ask("a second message")]
    assert [c for c in calls if c[0][:1] == ("user",)] == [(("user", "ordinary input"), {}),
                                                           (("user", "a second message"), {})]
    assert [t["tool_name"] for t in user_turns(real)] == [None, None]


async def test_the_owners_steering_correction_is_logged_with_no_marker(real, monkeypatch, tmp_path):
    calls = spy_on(monkeypatch, real.working)
    inbox = SteeringInbox(tmp_path / "receipts.json", real.session_id, 1, real.working.log_turn)
    await inbox.submit("Use blue for the booster", "a" * 32)
    ((args, kwargs),) = calls
    assert args == ("user", "Use blue for the booster") and set(kwargs) == {"on_commit"}
    assert "origin" not in inbox.receipts["a" * 32]


# --- 2. every place a user turn is logged, and every place a prompt reaches the Engine ----------------------

PACKAGE = Path(dream.__file__).resolve().parent

# (module, function, what it calls): the origin constant the site sets (turn_origin.<NAME>), or None where the text
# is not Dream's: the owner's typed line, or a relay of the prompt it was given.
PROMPT_SITES = Counter({
    ("dream/tui/app.py", "App.run", "self._ask", None): 1,                        # the owner's line
    ("dream/tui/app.py", "App.run", "self._ask", "GUIDED"): 1,                    # a guided task's prompt
    ("dream/tui/app.py", "App._stream", "self.engine.ask_chat", None): 1,         # relays App._ask's prompt
    ("dream/tui/app.py", "App._stream", "self.engine.ask", None): 1,
    ("dream/core/loop.py", "AutonomousLoop._drive.produce", "self.engine.ask", "LOOP"): 1,
    ("dream/tui/app.py", "App._review.ask", "self.engine.ask", "REVIEW"): 1,
    ("dream/tui/app.py", "App._resume", "self._ask", "RESUME"): 1,
    ("dream/tui/learn_cmd.py", "command", "app._ask", "LEARN"): 1,
    ("dream/tui/council.py", "CouncilControls._work_council", "self._ask", "COUNCIL"): 1,
})
# Calls that write a user-role turn, each able to carry the marker (a tool_name keyword or ** of one).
USER_LOG_SITES = Counter({
    ("dream/core/engine.py", "Engine._log_user_turn", "self.working.log_turn"): 2,
    ("dream/core/steering.py", "SteeringInbox._submit.capture", "self.log_turn"): 1,
})
# The Engine's calls of _log_user_turn, each passing the origin read on the event loop.
LOG_USER_TURN_CALLS = Counter({("dream/core/engine.py", "Engine.ask"): 1, ("dream/core/engine.py", "Engine._ask"): 1})
# Calls that pass a role through: WorkingMemory.log_turn to the store, and the Engine's chat records.
PASS_THROUGH = Counter({("dream/memory/working.py", "WorkingMemory.log_turn", "self.store.add_turn"): 1,
                        ("dream/core/engine.py", "Engine._persist_chat_turn", "self.working.log_turn"): 1})


def _dotted(node) -> str:
    return ast.unparse(node) if isinstance(node, (ast.Name, ast.Attribute)) else ""


def _origin(node) -> str | None:
    """X for `turn_origin.generated(turn_origin.X)` or `turn_origin.current.set(turn_origin.X)`."""
    arg = node.args[0] if isinstance(node, ast.Call) and node.args else None
    return arg.attr if isinstance(arg, ast.Attribute) and _dotted(arg.value) == "turn_origin" else None


def _own_calls(function):
    """The calls in a function's own body, not in the functions it defines."""
    todo = list(function.body)
    while todo:
        node = todo.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Call):
            yield node
        todo.extend(ast.iter_child_nodes(node))


class _Sites(ast.NodeVisitor):
    def __init__(self, module: str):
        self.module, self.scope, self.marks = module, [], []
        self.prompts: list[tuple] = []
        self.user_logs: list[tuple] = []
        self.log_user_turn: list[tuple] = []
        self.roles: list[tuple] = []           # (function, callee, role node) of every turn-writing call

    def _function(self):
        return ".".join(name for name, _ in self.scope)

    def _enter(self, node, own=None):
        self.scope.append((node.name, own))
        self.generic_visit(node)
        self.scope.pop()

    def visit_ClassDef(self, node):
        self._enter(node)

    def visit_FunctionDef(self, node):
        # A function may mark its own context: turn_origin.current.set(turn_origin.X) in its own body.
        own = [call for call in _own_calls(node) if _dotted(call.func) == "turn_origin.current.set"]
        self._enter(node, _origin(own[0]) if own else None)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_With(self, node):
        marks = [_origin(item.context_expr) for item in node.items
                 if isinstance(item.context_expr, ast.Call) and _dotted(item.context_expr.func) == "turn_origin.generated"]
        self.marks.extend(marks)
        self.generic_visit(node)
        del self.marks[len(self.marks) - len(marks):]

    visit_AsyncWith = visit_With

    def _mark(self):
        own = self.scope[-1][1] if self.scope else None
        return self.marks[-1] if self.marks else own

    def visit_Attribute(self, node):
        name = _dotted(node)
        if (name.endswith(("engine.ask", "engine.ask_chat"))
                or (name in ("self._ask", "app._ask") and self.module.startswith("dream/tui/"))):
            self.prompts.append((self.module, self._function(), name, self._mark()))
        self.generic_visit(node)

    def visit_Call(self, node):
        callee, first = _dotted(node.func), _dotted(node.args[0]) if node.args else ""
        # log_turn(role, …), add_turn(session, role, …), or either handed to a thread: in_thread(x.log_turn, role, …)
        for name, args in ((callee, node.args), (first, node.args[1:])):
            if name.endswith("log_turn") or name.endswith("add_turn"):
                role = args[1 if name.endswith("add_turn") else 0] if len(args) > (1 if name.endswith("add_turn") else 0) else None
                self.roles.append((self.module, self._function(), name, role))
                if isinstance(role, ast.Constant) and role.value == "user":
                    marked = any(k.arg in (None, "tool_name") for k in node.keywords)
                    self.user_logs.append((self.module, self._function(), name, marked))
            if name.endswith("_log_user_turn"):
                origin = args[2] if len(args) == 3 else None
                passes = isinstance(origin, ast.Call) and _dotted(origin.func) == "turn_origin.current.get"
                self.log_user_turn.append((self.module, self._function(), passes))
        self.generic_visit(node)


def _scan():
    sites = []
    for path in sorted(PACKAGE.rglob("*.py")):
        module = path.relative_to(PACKAGE.parent).as_posix()
        visitor = _Sites(module)
        visitor.visit(ast.parse(path.read_text("utf-8")))
        sites.append(visitor)
    return sites


def test_every_place_a_prompt_reaches_the_engine_is_the_owners_a_relay_or_marked():
    """A prompt Dream writes itself must reach the Engine inside `turn_origin.generated(...)` (or a task that set
    turn_origin.current), so it is logged as Dream's. A new place fails here until it is marked and listed."""
    found = Counter(site for v in _scan() for site in v.prompts)
    assert found == PROMPT_SITES, (
        "A prompt reaches the Engine at a place this test does not know, or a known one lost its marker. If Dream "
        "wrote the prompt, send it inside turn_origin.generated(<origin>) (dream/core/turn_origin.py); then list it. "
        f"New or changed: {sorted((found - PROMPT_SITES).elements())}; missing: {sorted((PROMPT_SITES - found).elements())}")


def test_every_place_a_user_turn_is_logged_can_carry_the_marker():
    """Every call that writes a user-role turn: the Engine's _log_user_turn (a prompt, marked by the context
    variable) and the steering inbox (the owner's corrections, and the guard's notes with their origin)."""
    scans = _scan()
    logs = [site for v in scans for site in v.user_logs]
    assert all(marked for *_, marked in logs), logs
    assert Counter(site[:3] for site in logs) == USER_LOG_SITES, (
        "A user-role turn is logged at a place this test does not know. A turn Dream wrote must carry its origin in "
        f"tool_name (dream/core/turn_origin.py); then list the place here. Found: {sorted(logs)}")
    calls = Counter((m, f) for v in scans for m, f, passes in v.log_user_turn if passes)
    assert calls == LOG_USER_TURN_CALLS and sum(len(v.log_user_turn) for v in scans) == 2, \
        [s for v in scans for s in v.log_user_turn]
    # A role that is not a constant: only the known pass-throughs, and no caller hands them "user".
    dynamic = Counter((m, f, name) for v in scans for m, f, name, role in v.roles if not isinstance(role, ast.Constant))
    assert dynamic == PASS_THROUGH, dynamic
    chat_roles = {node.args[0].value for path in PACKAGE.rglob("*.py")
                  for node in ast.walk(ast.parse(path.read_text("utf-8")))
                  if isinstance(node, ast.Call) and _dotted(node.func).endswith("_persist_chat_turn")}
    assert chat_roles == {"assistant_partial", "turn_status"}
    inserts = [p.relative_to(PACKAGE.parent).as_posix() for p in PACKAGE.rglob("*.py")
               if re.search(r"INSERT\s+INTO\s+turns\s*\(", p.read_text("utf-8"))]
    assert inserts == ["dream/memory/store.py"]                        # MemoryStore.add_turn alone


# --- 5a. the chat pane shows the guard's notes as Dream's, not as the owner's corrections ------------------


async def test_the_guards_steering_receipts_carry_their_origin(tmp_path):
    seen = []
    inbox = SteeringInbox(tmp_path / "receipts.json", "s", 1, lambda *a, **k: k["on_commit"](7), emit=seen.append)
    await inbox.submit("Use blue for the booster", "a" * 32)
    await inbox.submit("[Dream progress guard] 12 read-only steps.", "b" * 32, origin=turn_origin.PROGRESS_GUARD)
    assert "origin" not in seen[0] and seen[1]["origin"] == "dream:progress_guard"
    saved = json.loads((tmp_path / "receipts.json").read_text())["receipts"]
    assert [r.get("origin") for r in saved] == [None, "dream:progress_guard"]


async def test_the_engine_emits_the_guards_notes_as_dreams(live):
    """The gate's scenario (3 requests, then 60 read-only steps: 9 guard notes) through the real Engine: every
    steering notice the pane receives for a guard note says whose it is."""
    events = []
    live.emit = events.append
    for ask in ["Build the launch page", "Check the scene graph", "Fix the landing burn timing"]:
        [ev async for ev in live.ask_chat(ask)]
    receipts = [ev.data for ev in events if ev.kind == "steering"]
    assert len({r["id"] for r in receipts}) == 9
    assert {r.get("origin") for r in receipts} == {"dream:progress_guard"}


def _receipt(identifier, status, origin=None):
    return {"id": identifier, "status": status, "user_turn_id": None, "session_id": "s", "turn": 1,
            "recovery_path": "/tmp/r.json", **({"origin": origin} if origin else {})}


@pytest.mark.parametrize("layout", ["companion", "browser"])
async def test_the_chat_pane_labels_the_guards_notes_as_dreams(studio, tmp_path, layout):
    """Live notices, and a reconnect: the page's hello then carries the snapshot the real app builds
    (App._studio_session_info, the StudioServer session source in tui/app.py) from a real steering inbox that holds an
    owner's correction and a guard note."""
    from playwright.async_api import expect
    from dream.tui.app import App

    srv, page, url, _, errors = studio
    inbox = SteeringInbox(tmp_path / "receipts.json", "s", 1, lambda *a, **k: k["on_commit"](7))
    await inbox.submit("Use blue for the booster", "d" * 32)
    await inbox.submit("[Dream progress guard] 12 read-only steps.", "c" * 32, origin=turn_origin.PROGRESS_GUARD)
    app = App.__new__(App)
    app.engine = SimpleNamespace(_steering_inbox=inbox, session_id="s", model="fixture", effort=None,
                                 vision_status=lambda: {"state": "unreported", "enabled": False})
    app.provider_label, app.workspace = "test", tmp_path
    srv._session_source = app._studio_session_info
    await page.goto(url if layout == "companion" else url.replace("&companion=1", ""))
    notes = page.locator("#stream .sys.dream-note")
    owner = page.locator("#stream .sys:not(.dream-note)", has_text="Correction")
    await expect(notes).to_have_count(1)
    await expect(owner).to_have_count(1)
    for status in ("pending", "included", "submitted"):
        srv.bus.publish(Event("steering", _receipt("a" * 32, status)))                        # the owner's
        srv.bus.publish(Event("steering", _receipt("b" * 32, status, "dream:progress_guard")))  # the guard's
    await expect(notes).to_have_count(4)
    await expect(owner).to_have_count(4)
    for text in await notes.all_inner_texts():
        assert text.startswith("Dream's progress-guard note, not from you:") and "Correction" not in text
    assert await owner.nth(0).inner_text() == ("Correction saved; waiting for the next model request after "
                                               "current tools finish.")
    color = "el => getComputedStyle(el).color"
    assert await notes.nth(0).evaluate(color) != await owner.nth(0).evaluate(color)   # a style of its own
    assert errors == []


# --- 5b. the project library's handoff draft quotes the owner, not a guard note --------------------------


@pytest.fixture
def archive(tmp_path, monkeypatch):
    from dream import config
    from dream.projects.library import ProjectLibrary

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "private")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "archive.sqlite3")
    store = MemoryStore(config.DB_PATH)
    store.start_session("session-a", "Launch work")
    library = ProjectLibrary()
    project = library.create("Rocket", str(tmp_path), session_id="session-a")
    yield library, project, store
    store.close()


def test_the_handoff_draft_skips_the_prompts_dream_wrote(archive):
    library, project, store = archive
    store.add_turn("session-a", "user", "[context restored from a previous session — continue from here, don't "
                                        "re-answer]\nResuming session old.", "dream:resume")
    store.add_turn("session-a", "user", "Improve the existing rocket. Do not rebuild it.")
    store.add_turn("session-a", "assistant", "I saved launch.blend.")
    store.add_turn("session-a", "user", "Keep the saved asset; fix the fins.")
    store.add_turn("session-a", "user", "[Dream progress guard] 12 consecutive read-only steps.", "dream:progress_guard")
    store.add_turn("session-a", "user", "[Dream progress guard] 18 consecutive read-only steps.")   # an older transcript
    body = library.handoff(project["id"], "session-a")["document"]["content"]
    initial = body.split("### Initial user request", 1)[1].split("###", 1)[0]
    latest = body.split("### Latest user request", 1)[1].split("###", 1)[0]
    assert "Do not rebuild it." in initial and "context restored" not in body
    assert "Keep the saved asset; fix the fins." in latest and "progress guard" not in body
