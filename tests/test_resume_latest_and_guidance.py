"""DREAM-120 (fix list #91 and #92), two gate rounds.

#91: /resume showed a session's FIRST turns as its "recent exchange" (`session_turns` reads from the start), so on
2026-09-24 the restored model began a 123-row session's work again from its opening exchange. The priming prompt now
carries the session's LAST turns (and the owner's last request when tool records fill them), the workspace's real state
-- PLAN.md's status line and phases, the newest files the session's file tools wrote or edited, rebuilt from the
transcript through DREAM-113's FileLedger -- naming the folder when the session ran in another project.
#92: the task-guidance selector picked frontend-design for that restore prompt (a modelling plan uses its words:
"distinctive", "aesthetic", "look and feel"). A RESUME priming prompt selects nothing. The gate's first round found that
the live-Blender server is registered at every desktop session start (Engine.start), so a rule keyed on the blender__*
tools being OFFERED narrowed frontend-design in every session; the rule keys on Blender being USED -- a blender__* call
made this session (Engine.tools_used) or Blender / a .blend file named in the request -- and a session that merely
offers the tools selects exactly as pristine. The task_guidance event says what matched.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from rich.console import Console

from dream import config
from dream.core import turn_origin
from dream.core.backends.base import Event
from dream.memory import project as project_memory
from dream.memory.store import MemoryStore
from dream.skills import loader
from dream.skills.selection import select_for_task
from dream.tui import app as app_mod
from test_task_guidance_integration import engine  # noqa: F401

BLENDER_TOOLS = ("blender__get_scene_info", "blender__execute_blender_code", "read_file", "write_file")  # offered at every desktop session
BLENDER_CALL = "blender__execute_blender_code"
# The shape of the 2026-09-24 restore prompt: a modelling session's opening exchange, in the selector's
# frontend-design words, about no web page.
MODELLING_RESTORE = (
    "[context restored from a previous session — continue from here, don't re-answer]\n"
    "Resuming session 20260924-2101-ab12 (123 turns).\n"
    "Recent exchange:\n"
    "- user: Here are the reference photos of the vehicle. Model it with a distinctive aesthetic: the look and feel "
    "of the reference, a restrained color palette, clean typography on the badges.\n"
    "- assistant: Plan: 1) analyse the reference photos 2) block out the body 3) refine the shell 4) materials.\n"
    "- user: Go ahead with phase 1.\n\nReady to continue."
)
MODELLING_EXCHANGE = MODELLING_RESTORE.split("\n", 1)[1]     # the same words as the owner's own message
LANDING_PAGE = "Design a distinctive landing page with strong typography and a real visual identity"
# The gate's eight web-page prompts (its full strings) with what pristine selects for each: a session that merely
# OFFERS the Blender tools must select exactly this. Each carries a frontend-design word and no page word, so each
# exercises the rule.
OFFERED_ONLY = [
    ("Redesign the pricing page with a distinctive look and feel", ("frontend-design",)),
    ("Give our homepage a stronger visual identity", ("frontend-design",)),
    ("Build a portfolio site with distinctive typography.", ("coding", "frontend-design")),
    ("Restyle the front-end with a new color palette.", ("frontend-design",)),
    ("Design the About page with better typography.", ("frontend-design",)),
    ("Pick a color palette and typography for the docs site", ("frontend-design",)),
    ("The dashboard needs a redesign: it looks like a template", ("frontend-design",)),
    ("Make the signup flow feel distinctive", ("frontend-design",)),
]
PLAN = ("# Plan: the shell\n\n"
        "Status: ● done · ◐ in progress · ○ not started — updated 2026-09-24 23:10 by Dream\n\n"
        "## ● 1. Reference analysis\n- ● measure the references\n\n> **Summary:** proportions fixed\n\n"
        "## ◐ 2. Body blockout\n- ● hull\n- ○ cabin\n\n"
        "## ○ 3. Materials\n- ○ paint\n")


@pytest.fixture(scope="module")
def curated():
    skills, warnings = loader.discover(config.bundled_skill_dirs())
    assert not warnings, warnings
    return skills


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path / "memory.db")
    yield s
    s.close()


def rows(store, session_id, n):
    """A session of n rows, 'row 1' .. 'row n', user and assistant alternating."""
    store.start_session(session_id, "Earlier work")
    for i in range(1, n + 1):
        store.add_turn(session_id, "user" if i % 2 else "assistant", f"row {i}")


def resume_app(store, workspace):
    """App._resume's App: the store, the workspace, a recording console, and `_ask` replaced by a recorder of the
    prompt and the origin it was sent under."""
    app = app_mod.App.__new__(app_mod.App)
    app.engine = SimpleNamespace(store=store, session_id="current", workspace=workspace)
    app.renderer = SimpleNamespace(console=Console(record=True))
    sent = []

    async def _ask(prompt, **_):
        sent.append((prompt, turn_origin.current.get()))
        return True
    app._ask = _ask
    return app, sent


class BlenderThenReplies:
    """A backend whose first turn makes one Blender call (`call`: the tool's name as the backend reports it); every turn
    ends with one reply."""

    def __init__(self, call=BLENDER_CALL):
        self.prompts, self.prepared, self.turns, self.call = [], [], 0, call

    def prepare_turn(self, tools):
        self.prepared.append(tuple(tools))

    async def ask(self, prompt):
        self.prompts.append(prompt)
        self.turns += 1
        if self.turns == 1:
            yield Event("tool_use", {"name": self.call, "input": {"code": "import bpy"}})
            yield Event("tool_result", {"name": self.call, "content": "ok"})
        yield Event("assistant_done", "ok")
        yield Event("result", {"is_error": False})


# --- #91: the latest turns and the workspace's state --------------------------------------------------------


def test_session_turns_latest_gives_the_last_n_rows_oldest_first(store):
    rows(store, "s", 50)
    assert [t["content"] for t in store.session_turns("s", limit=10, latest=True)] == [f"row {i}" for i in range(41, 51)]
    # the default keeps its first-N meaning: engine._transcript_digest reads the session's opening with it
    assert [t["content"] for t in store.session_turns("s", limit=10)] == [f"row {i}" for i in range(1, 11)]
    assert len(store.session_turns("s", latest=True)) == 50


async def test_resume_shows_the_sessions_last_ten_rows_in_order_under_its_marker(store, tmp_path):
    rows(store, "old", 50)
    app, sent = resume_app(store, tmp_path)
    await app._resume("old")
    ((prompt, origin),) = sent
    shown = [line for line in prompt.splitlines() if line.startswith(("- user: row", "- assistant: row"))]
    assert [line.split("row ")[1] for line in shown] == [str(i) for i in range(41, 51)]
    assert "- user: row 1\n" not in prompt and "row 40" not in prompt
    assert f"Latest exchange (the session's last {app_mod.RESUME_TURNS} turns, oldest first):" in prompt
    assert app_mod.RESUME_TURNS == 10
    assert prompt.startswith("[context restored from a previous session") and origin == turn_origin.RESUME


async def test_resume_states_the_plan_and_the_newest_files(store, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "PLAN.md").write_text(PLAN, encoding="utf-8")
    store.start_session("old", "Earlier work")
    store.add_turn("old", "user", "Model the shell")
    for i in range(1, 16):                       # 15 writes: only the newest RESUME_FILES are listed
        store.add_turn("old", "tool_use", json.dumps({"path": f"parts/part{i:02d}.py", "content": "x"}), "write_file")
    store.add_turn("old", "tool_use", json.dumps({"path": "parts/part15.py", "old_string": "x", "new_string": "y"}),
                   "str_replace_edit")
    # a long write_file: the Engine logs json.dumps(input)[:2000], so the row is not JSON any more
    store.add_turn("old", "tool_use", json.dumps({"path": "build/assemble.py", "content": "#" * 5000})[:2000], "write_file")
    store.add_turn("old", "tool_use", json.dumps({"command": "ls"}), "run_bash")
    store.add_turn("old", "tool_result", "ok", "run_bash")
    store.add_turn("old", "assistant", "Assembled.")
    app, sent = resume_app(store, ws)
    await app._resume("old")
    ((prompt, _),) = sent
    assert "PLAN.md in the workspace (" in prompt
    assert "Status: ● done · ◐ in progress · ○ not started — updated 2026-09-24 23:10 by Dream" in prompt
    assert "## ● 1. Reference analysis" in prompt and "## ◐ 2. Body blockout" in prompt and "## ○ 3. Materials" in prompt
    assert "measure the references" not in prompt and "Summary" not in prompt      # steps and summaries stay in the file
    assert "wrote or edited in the workspace," in prompt
    files = [line for line in prompt.splitlines() if line.startswith("- ") and " — " in line]
    assert files[:3] == ["- build/assemble.py — wrote", "- parts/part15.py — wrote, edited", "- parts/part14.py — wrote"]
    assert len(files) == app_mod.RESUME_FILES == 12
    assert "part03.py" not in prompt and "part01.py" not in prompt
    assert prompt.index("PLAN.md") < prompt.index("- build/assemble.py") < prompt.index("Assembled.")


async def test_resume_without_a_plan_or_writes_says_nothing_of_them(store, tmp_path):
    rows(store, "old", 4)
    app, sent = resume_app(store, tmp_path)
    await app._resume("old")
    ((prompt, _),) = sent
    assert "PLAN.md" not in prompt and "wrote or edited" not in prompt and "last 4 turns" in prompt


async def test_resume_of_another_projects_session_names_that_projects_folder(store, tmp_path):
    """`/resume <id>` may name a session of another project (DREAM-108 keys): its PLAN.md is the one Dream recorded a
    workspace for, and the prompt names that folder -- the model's tools resolve against THIS workspace, so a bare
    relative path would point at the wrong files (the gate's note)."""
    here, there = tmp_path / "here", tmp_path / "there"
    here.mkdir()
    there.mkdir()
    (there / "PLAN.md").write_text("Status: ● done · ◐ in progress · ○ not started — updated 2026-09-24 by Dream\n"
                                   "## ◐ 1. Over there\n", encoding="utf-8")
    (here / "PLAN.md").write_text("Status: here\n## ○ 1. Over here\n", encoding="utf-8")
    key = project_memory.register(there)
    store.start_session("far", "Earlier work", project=key)
    store.add_turn("far", "user", "Carry on")
    store.add_turn("far", "tool_use", json.dumps({"path": "parts/wing.py", "content": "x"}), "write_file")
    app, sent = resume_app(store, here)
    await app._resume("far")
    ((prompt, _),) = sent
    folder = there.resolve()
    assert f"PLAN.md in {folder} (" in prompt and "## ◐ 1. Over there" in prompt and "Over here" not in prompt
    assert f"wrote or edited in {folder}," in prompt and f"- {folder}/parts/wing.py — wrote" in prompt


def test_the_plan_excerpt_counts_phases_not_file_lines(tmp_path):
    plan = ("Status: ● done · ◐ in progress · ○ not started — updated 2026-09-24 by Dream\n"
            + "".join(f"## ○ {i}. Phase {i}\n- ○ a step\n" for i in range(1, 15)))
    (tmp_path / "PLAN.md").write_text(plan, encoding="utf-8")
    shown = app_mod._plan_excerpt(tmp_path / "PLAN.md")
    assert shown[0].startswith("Status:") and shown[1] == "## ○ 1. Phase 1" and shown[12] == "## ○ 12. Phase 12"
    assert shown[13] == "(+2 more phases)" and len(shown) == 14
    assert app_mod._plan_excerpt(tmp_path / "missing.md") == []


async def test_resume_keeps_the_owners_last_request_when_tool_rows_fill_the_latest_turns(store, tmp_path):
    """The gate's note: the latest turns are the last ten rows of any role, so in a tool-heavy session the owner's last
    request falls outside them; it is put first. A prompt Dream wrote (a marked progress-guard note) is not it."""
    store.start_session("busy", "Earlier work")
    store.add_turn("busy", "user", "Model the shell")
    store.add_turn("busy", "user", "[Dream progress guard] act now", "dream:progress_guard")
    for i in range(12):
        store.add_turn("busy", "tool_use", json.dumps({"code": f"step {i}"}), BLENDER_CALL)
        store.add_turn("busy", "tool_result", "ok", BLENDER_CALL)
    app, sent = resume_app(store, tmp_path)
    await app._resume("busy")
    ((prompt, _),) = sent
    exchange = prompt.split("Latest exchange (", 1)[1]
    assert exchange.startswith("the owner's last request, then the session's last 10 turns, oldest first):\n"
                               "- user: Model the shell\n- tool_use: ")
    assert "progress guard" not in prompt and exchange.count("\n- ") == 11


async def test_the_owners_request_is_found_only_within_the_scanned_rows(store, tmp_path):
    """RESUME_SCAN rows are read back: a request older than that is absent and the header does not claim it."""
    store.start_session("deep", "Earlier work")
    store.add_turn("deep", "user", "Model the shell")
    for i in range(app_mod.RESUME_SCAN + 5):
        store.add_turn("deep", "tool_use", json.dumps({"code": f"step {i}"}), BLENDER_CALL)
    app, sent = resume_app(store, tmp_path)
    await app._resume("deep")
    ((prompt, _),) = sent
    assert "Model the shell" not in prompt and "Latest exchange (the session's last 10 turns, oldest first):" in prompt


# --- #92: the selector on a restore prompt, and in Blender work ---------------------------------------------------


def _guidance_events(engine):
    log = config.LOG_DIR / "runtime" / f"{engine.session_id}.jsonl"
    if not log.is_file():
        return []
    return [e for e in map(json.loads, log.read_text().splitlines()) if e["event"] == "task_guidance"]


async def _drain(events):
    return [e async for e in events]


async def test_the_restore_prompt_selects_no_guidance(engine):
    engine.tool_names = list(BLENDER_TOOLS)
    with turn_origin.generated(turn_origin.RESUME):
        events = await _drain(engine.ask(MODELLING_RESTORE))
    assert engine.backend.prompts == [MODELLING_RESTORE] and engine.backend.prepared == [()]
    assert not any(e.kind == "system" and "workflow" in str(e.data).lower() for e in events)
    assert _guidance_events(engine) == []
    # the control: the same words as the owner's own message do select (nothing of Blender used or named yet)
    await _drain(engine.ask(MODELLING_RESTORE))
    assert [e["skills"] for e in _guidance_events(engine)] == [["frontend-design"]]


@pytest.mark.parametrize("prompt,expected", OFFERED_ONLY)
async def test_a_session_that_merely_offers_blender_tools_selects_as_pristine(engine, prompt, expected):
    """The gate's finding: Engine.start registers the live-Blender server at every desktop session start, so a rule
    keyed on the tools being OFFERED narrowed frontend-design in every session."""
    engine.tool_names = list(BLENDER_TOOLS)
    await _drain(engine.ask(prompt))
    assert [tuple(e["skills"]) for e in _guidance_events(engine)] == ([expected] if expected else [])


@pytest.mark.parametrize("call", [BLENDER_CALL, "mcp__dream__" + BLENDER_CALL])
async def test_after_a_blender_call_this_session_the_modelling_words_select_no_frontend_design(engine, call):
    """`call`: the raw name the owner's MiMo path emits, and the `mcp__dream__<name>` id of the Anthropic/SDK path
    (config.tool_id) -- the gate's note: both count as Blender use."""
    engine.backend = BlenderThenReplies(call)
    engine.tool_names = list(BLENDER_TOOLS)
    await _drain(engine.ask("Set up the scene."))                  # turn 1 makes the blender call
    assert engine.tools_used == {call}
    await _drain(engine.ask(MODELLING_EXCHANGE))                   # the words that alone select frontend-design
    assert not any("frontend-design" in e["skills"] for e in _guidance_events(engine))
    assert engine.backend.prepared[-1] == ()


def test_the_selector_narrows_frontend_design_only_in_blender_work(curated):
    for prompt, expected in OFFERED_ONLY:                           # nothing used, nothing named: exactly pristine
        assert select_for_task(prompt, curated, used=()).names == expected, prompt
    assert select_for_task(MODELLING_EXCHANGE, curated).names == ("frontend-design",)            # the misfire's words
    assert select_for_task(MODELLING_EXCHANGE, curated, used=(BLENDER_CALL,)).names == ()          # after a blender call
    assert select_for_task(MODELLING_EXCHANGE, curated, used=("mcp__dream__" + BLENDER_CALL,)).names == ()   # SDK tool id
    assert select_for_task(MODELLING_EXCHANGE, curated, used=("read_file", "run_bash")).names == ("frontend-design",)
    names = select_for_task(MODELLING_EXCHANGE + " Do it in Blender.", curated).names             # named in the text
    assert "blender-animation" in names and "frontend-design" not in names
    assert "frontend-design" not in select_for_task("Polish the look and feel of scene.blend", curated).names


def test_a_real_front_end_prompt_still_selects_frontend_design_in_blender_work(curated):
    used = (BLENDER_CALL,)
    assert "frontend-design" in select_for_task(LANDING_PAGE, curated, used=used).names
    assert "frontend-design" in select_for_task("Redesign the hero section of our website", curated, used=used).names
    assert "frontend-design" in select_for_task("$frontend-design " + MODELLING_EXCHANGE, curated, used=used).names  # explicit
    assert "frontend-design" in select_for_task("A landing page in Blender's style: distinctive typography", curated).names


def test_the_rules_edges_in_blender_work(curated):
    """The gate's notes, pinned: a web request that names Blender is narrowed by text (pristine selected frontend-design
    beside blender-animation); after one Blender call every web prompt without a page word loses frontend-design for
    the rest of the session, while a page word or an explicit request still selects it."""
    names = select_for_task("Redesign my Blender portfolio site with distinctive typography.", curated).names
    assert "frontend-design" not in names and "blender-animation" in names
    for prompt, _ in OFFERED_ONLY:
        assert "frontend-design" not in select_for_task(prompt, curated, used=(BLENDER_CALL,)).names, prompt
    assert "frontend-design" in select_for_task(LANDING_PAGE, curated, used=(BLENDER_CALL,)).names
    assert "frontend-design" in select_for_task("$frontend-design " + OFFERED_ONLY[0][0], curated, used=(BLENDER_CALL,)).names


async def test_the_task_guidance_event_says_what_matched(engine):
    await _drain(engine.ask(LANDING_PAGE))
    await _drain(engine.ask("Use the coding skill to fix the sorting bug in the project."))
    matched = [e["matched"] for e in _guidance_events(engine)]
    assert matched[0] == {"frontend-design": "distinctive"}
    assert matched[1]["coding"] == "explicit"
