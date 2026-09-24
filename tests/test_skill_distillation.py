"""The distillation trigger.

Phase B gave Dream skill tools; nothing ever asked it to write one. Consolidation
is the moment it should — but only when the session earned it, and never at the
cost of the data-safety gating that decides what gets retired.
"""

import re
import tempfile
from pathlib import Path

import pytest

import dream.config as config
from dream.core.backends.base import Event
from dream.memory.store import MemoryStore


def _store(**kw) -> MemoryStore:
    store = MemoryStore(Path(tempfile.mkdtemp()) / "t.db", **kw)
    # These stand in for sessions of an Engine() in its default workspace, and consolidation
    # stays in that workspace's project (DREAM-108), so the store is scoped as the Engine's is.
    from dream.memory.project import project_key
    store.project = project_key(config.ROOT)
    return store


class FakeBackend:
    """Yields a fixed event script and records the prompt it was asked."""

    def __init__(self, events):
        self.events = events
        self.prompt = None

    async def ask(self, prompt):
        self.prompt = prompt
        for ev in self.events:
            yield ev


@pytest.fixture
def mirror_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SEMANTIC_DIR", tmp_path / "sem")
    monkeypatch.setattr(config, "PROCEDURAL_DIR", tmp_path / "proc")
    monkeypatch.setattr(config, "EPISODIC_DIR", tmp_path / "ep")
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    for d in (config.SEMANTIC_DIR, config.PROCEDURAL_DIR, config.EPISODIC_DIR,
              config.SESSIONS_DIR):
        d.mkdir()
    return tmp_path


def _engine_with(store, backend):
    from dream.core.engine import Engine

    e = Engine()
    e.store = store
    e.backend = backend
    from dream.tools.context import ToolContext
    e._tool_context = ToolContext(store, None, None, e.session_id)
    return e


def _conflicted_store(embedder, monkeypatch):
    monkeypatch.setattr(config, "RECONCILE_THRESHOLD", 0.70)
    monkeypatch.setattr(config, "MERGE_THRESHOLD", 0.995)
    s = _store(embedder=embedder)
    s.upsert_memory(
        "semantic", "Theme", "Alex prefers dark mode in every application.", slug="a"
    )
    s.upsert_memory(
        "semantic", "Theme now", "Alex now prefers light mode in every application.",
        slug="b",
    )
    assert s.find_conflicts()
    return s


_STEP_RE = re.compile(r"^(\d+)\.")


def _step_blocks(prompt: str) -> list[str]:
    """The numbered steps, each with its continuation lines. Sub-lines inside a step
    are indented, so a bare line-start digit is always a new step."""
    blocks: list[str] = []
    for line in prompt.splitlines():
        if _STEP_RE.match(line):
            blocks.append(line)
        elif blocks:
            blocks[-1] += "\n" + line
    return blocks


def _step_numbers(prompt: str) -> list[int]:
    return [int(_STEP_RE.match(b).group(1)) for b in _step_blocks(prompt)]


def _skill_step(prompt: str) -> str:
    hits = [b for b in _step_blocks(prompt) if "skill_save" in b]
    assert len(hits) == 1, f"expected exactly one distillation step, got {len(hits)}"
    return hits[0]


async def _prompt_from(store) -> str:
    backend = FakeBackend([Event("assistant_done", "done.\nSUMMARY: ok")])
    await _engine_with(store, backend).consolidate()
    return backend.prompt


# --- the trigger exists -------------------------------------------------------


async def test_consolidation_asks_for_one_skill(mirror_dirs):
    prompt = await _prompt_from(_store())
    step = _skill_step(prompt)
    assert "skill_save" in step


async def test_distillation_sets_a_bar(mirror_dirs):
    """A harness that manufactures a skill every session poisons its own memory:
    the step must name what qualifies AND refuse the rest explicitly."""
    step = _skill_step(await _prompt_from(_store())).lower()
    for earned in ("dead end", "corrected", "reuse"):
        assert earned in step, f"missing the bar: {earned!r}"
    assert "trivia" in step
    assert "obvious" in step


async def test_distillation_prefers_patching_an_existing_skill(mirror_dirs):
    """Near-identical duplicate procedures are the failure mode here — it must
    look before it writes, and amend rather than fork."""
    step = _skill_step(await _prompt_from(_store()))
    assert "skill_list" in step and "skill_patch" in step
    assert step.index("skill_list") < step.index("skill_save")


# --- the existing prompt is undisturbed ---------------------------------------


async def test_existing_steps_survive_the_new_one(mirror_dirs):
    prompt = await _prompt_from(_store())
    assert "read_notes" in prompt
    assert "remember()" in prompt and "recall()" in prompt
    assert "salience" in prompt
    # Phase 11 (Gate 11 blocking finding 1): the step that used to order a hand
    # edit of THREADS.md now names the task tools. The file is generated, and a
    # hand edit is erased on the next boot — so the prompt must NOT point at it.
    assert "task_add" in prompt and "task_update" in prompt
    assert str(config.THREADS_FILE) not in prompt
    assert "SUMMARY:" in prompt


async def test_numbering_is_sequential_without_conflicts_or_strays(mirror_dirs):
    nums = _step_numbers(await _prompt_from(_store()))
    assert nums == list(range(1, len(nums) + 1))


async def test_numbering_is_sequential_with_strays(mirror_dirs):
    s = _store()
    s.start_session("prior-session")
    s.add_note("prior-session", "The user keeps the B70 benchmarks in ~/bench")
    s.end_session("prior-session")
    prompt = await _prompt_from(s)
    assert "<note>" in prompt  # the stray block really is in there
    nums = _step_numbers(prompt)
    assert nums == list(range(1, len(nums) + 1))


async def test_numbering_is_sequential_with_conflicts_and_strays(
    session_embedder, monkeypatch, mirror_dirs
):
    """The conflict step's number is written by hand where the strays step's is
    computed — a step inserted in the wrong place silently renumbers one of them."""
    s = _conflicted_store(session_embedder, monkeypatch)
    s.start_session("prior-session")
    s.add_note("prior-session", "unpromoted durable fact")
    s.end_session("prior-session")
    prompt = await _prompt_from(s)
    assert "<memory>" in prompt and "<note>" in prompt
    nums = _step_numbers(prompt)
    assert nums == list(range(1, len(nums) + 1))
    # Reconciliation is still its own step, and still scoped to the listed slugs.
    assert any("Reconcile these stored memories" in b for b in _step_blocks(prompt))


# --- failure isolation: the skill step cannot cost data ------------------------


async def test_failed_skill_tool_still_retires_notes(mirror_dirs):
    """A skill_save that errors is not a failed dream: the notes were still read
    and folded in, so they must retire exactly as they would have before."""
    s = _store()
    eng = _engine_with(
        s,
        FakeBackend([
            Event("tool_result", {"name": "mcp__dream__read_notes", "is_error": False,
                                  "content": "ok"}),
            Event("tool_result", {"name": "mcp__dream__skill_save", "is_error": True,
                                  "content": "disk full"}),
            Event("assistant_done", "wrote it up.\nSUMMARY: shipped the thing"),
        ]),
    )
    s.start_session(eng.session_id)
    s.add_note(eng.session_id, "durable note from this session")
    summary = await eng.consolidate()

    assert summary == "shipped the thing"
    assert s.session_notes(eng.session_id, only_unconsolidated=True) == []


async def test_failed_skill_tool_still_retires_conflicts(
    session_embedder, monkeypatch, mirror_dirs
):
    s = _conflicted_store(session_embedder, monkeypatch)
    eng = _engine_with(
        s,
        FakeBackend([
            Event("tool_result", {"name": "mcp__dream__skill_patch", "is_error": True,
                                  "content": "no such slug"}),
            Event("assistant_done", "merged.\nSUMMARY: tidied the theme memories"),
        ]),
    )
    await eng.consolidate()
    assert s.find_conflicts() == []


async def test_failed_remember_still_blocks_retirement(
    session_embedder, monkeypatch, mirror_dirs
):
    """The contrast case: skill failures are isolated, memory-mutation failures
    are not. Isolating the wrong tool would silently retire unmerged conflicts."""
    s = _conflicted_store(session_embedder, monkeypatch)
    eng = _engine_with(
        s,
        FakeBackend([
            Event("tool_result", {"name": "mcp__dream__remember", "is_error": True,
                                  "content": "db locked"}),
            Event("assistant_done", "tried.\nSUMMARY: attempted merge"),
        ]),
    )
    await eng.consolidate()
    assert s.find_conflicts()


# --- the tool_use log and the usage bump now share one thread hop --------------


async def test_custom_tool_use_is_logged_and_counted(mirror_dirs):
    """Folding the provenance bump into the transcript write must not drop either:
    the turn still lands with its input and tool name, the counter still moves."""
    from dream.memory.working import WorkingMemory

    s = _store()
    eng = _engine_with(
        s,
        FakeBackend([
            Event("tool_use", {"name": "mcp__dream__preview_tui",
                               "input": {"path": "x"}, "id": "c0"}),
            Event("tool_result", {"name": "mcp__dream__preview_tui", "content": "ok",
                                  "is_error": False}),
        ]),
    )
    s.start_session(eng.session_id)
    eng.working = WorkingMemory(s, eng.session_id)
    eng._started = True
    eng._custom_tool_names = {"preview_tui"}

    async for _ in eng.ask("run it"):
        pass

    uses = [t for t in s.session_turns(eng.session_id, limit=50) if t["role"] == "tool_use"]
    assert len(uses) == 1
    assert uses[0]["tool_name"] == "mcp__dream__preview_tui"
    assert '"path": "x"' in uses[0]["content"]
    assert s.tool_stat("preview_tui")["use_count"] == 1


async def test_builtin_tool_use_is_logged_without_a_bump(mirror_dirs):
    from dream.memory.working import WorkingMemory

    s = _store()
    eng = _engine_with(
        s,
        FakeBackend([Event("tool_use", {"name": "run_bash", "input": {}, "id": "c0"})]),
    )
    s.start_session(eng.session_id)
    eng.working = WorkingMemory(s, eng.session_id)
    eng._started = True

    async for _ in eng.ask("go"):
        pass

    assert [t["role"] for t in s.session_turns(eng.session_id, limit=50)] == [
        "user", "tool_use"
    ]
    assert s.tool_stat("run_bash") is None
