"""DREAM-123: durable build notes that survive compaction. A 6-hour Blender build (2026-09-25) re-learned the same
facts after each compaction and after a restart (Python names do not persist between Blender calls; this Blender
has no denoiser). update_plan keeps a short "## Lessons" section in PLAN.md; a plan update without `lessons` keeps
them, [] clears them; a compaction, a Fresh start's handoff and /resume's excerpt carry them verbatim; both prompt
texts ask the model to record such a fact."""
import json

import pytest

from dream.core import handoff, system_prompt
from dream.core.tool_validation import validate_arguments
from dream.tools import project
from dream.tools.context import ToolContext, bind_context, set_context
from dream.tools.project import _LAST_PLAN, update_plan
from dream.tui import app as app_mod
from test_schema_deferral import _FakeClient, _backend, _text_round, _tool_round

NAMES = "Python names do not persist between Blender calls: import the helpers in every call."
DENOISE = "This Blender has no denoiser: render without it."


def _plan(status="in_progress", summary="", **extra):
    return {"title": "Camaro", "phases": [
        {"name": "Body", "status": status, "summary": summary, "steps": [{"name": "loft", "status": "in_progress"}]},
        {"name": "Render", "status": "pending"}], **extra}


def _context(tmp_path):
    return ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                       session_id="t", workspace=tmp_path, emit=None)


@pytest.fixture
def ws(tmp_path):
    set_context(_context(tmp_path))
    _LAST_PLAN.clear()
    return tmp_path


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_the_schema_offers_lessons_as_a_list_of_strings():
    prop = update_plan.input_schema["properties"]["lessons"]
    assert prop["type"] == "array" and prop["items"] == {"type": "string"}
    assert "re-learn" in update_plan.description and "lessons" in update_plan.description
    assert validate_arguments(update_plan.input_schema, _plan(lessons=[NAMES])) is None
    assert validate_arguments(update_plan.input_schema, _plan(lessons=[3])) is not None


def test_plan_md_has_a_lessons_section_after_the_status_line(ws):
    out = _run(update_plan.handler(_plan(lessons=[NAMES, DENOISE])))
    assert not out.get("is_error"), out
    md = (ws / "PLAN.md").read_text()
    assert f"## Lessons\n- {NAMES}\n- {DENOISE}\n" in md
    assert md.index("Status:") < md.index("## Lessons") < md.index("## ◐ 1. Body")
    assert project.plan_lessons(ws / "PLAN.md") == [NAMES, DENOISE]


def test_an_update_without_lessons_keeps_them_and_an_empty_list_clears_them(ws):
    _run(update_plan.handler(_plan(lessons=[NAMES, DENOISE])))
    _LAST_PLAN.clear()                                  # a restart: the file alone carries them
    _run(update_plan.handler(_plan(status="done", summary="body built")))
    md = (ws / "PLAN.md").read_text()
    assert f"- {NAMES}" in md and f"- {DENOISE}" in md and "## ● 1. Body" in md
    _run(update_plan.handler(_plan(status="done", summary="body built", lessons=[])))
    md = (ws / "PLAN.md").read_text()
    assert "## Lessons" not in md and NAMES not in md
    assert project.plan_lessons(ws / "PLAN.md") == []


def test_the_caps_hold_newest_win(ws):
    many = [f"lesson {i} " + "x" * 400 for i in range(project.LESSONS_MAX + 5)]
    out = _run(update_plan.handler(_plan(lessons=many + ["  ", ""])))
    kept = project.plan_lessons(ws / "PLAN.md")
    assert len(kept) == project.LESSONS_MAX
    assert kept[0].startswith("lesson 5 ") and kept[-1].startswith(f"lesson {project.LESSONS_MAX + 4} ")
    assert all(len(k) <= project.LESSON_CHARS for k in kept) and kept[0].endswith("…")
    assert "5 older" in out["content"][0]["text"]


def test_a_hand_edited_plan_without_lessons_reads_as_none(tmp_path):
    (tmp_path / "PLAN.md").write_text("# Plan\n\nStatus: x\n\n## ● 1. Body\n- ● loft\n", encoding="utf-8")
    assert project.plan_lessons(tmp_path / "PLAN.md") == []
    assert project.plan_lessons(tmp_path / "missing.md") == []


def test_the_fresh_start_handoff_carries_the_lessons_verbatim(ws):
    _run(update_plan.handler(_plan(lessons=[NAMES, DENOISE])))
    parts = handoff.gather(_context(ws))
    text = handoff.compose(parts, handoff.FileLedger(), [])
    assert "## Lessons" in text and f"- {NAMES}" in text and f"- {DENOISE}" in text
    assert text.index(NAMES) < text.index("## To recover detail")


def test_the_resume_excerpt_carries_the_lessons(ws):
    _run(update_plan.handler(_plan(lessons=[NAMES, DENOISE])))
    shown = app_mod._plan_excerpt(ws / "PLAN.md")
    assert shown[0].startswith("Status:")
    assert shown[1:] == ["## Lessons", f"- {NAMES}", f"- {DENOISE}", "## ◐ 1. Body", "## ○ 2. Render"]


@pytest.mark.asyncio
async def test_a_phase_boundary_compaction_hands_the_lessons_to_the_model(tmp_path):
    set_context(_context(tmp_path))
    _LAST_PLAN.clear()
    b = _backend([update_plan], n_ctx=16384)
    for k in range(12):
        b.messages.append({"role": "user", "content": f"step {k} " + "w" * 1500 + f"\n\n[id:m{k + 1:04d}]"})
        b.messages.append({"role": "assistant", "content": "did it " + "v" * 1500})
    b._msg_seq = 12
    await update_plan.handler(_plan(lessons=[NAMES, DENOISE]))
    b._client = _FakeClient([
        _tool_round("update_plan", json.dumps(_plan(status="done", summary="Body done; render next"))),
        _text_round("next phase"),
    ])
    with bind_context(_context(tmp_path)):
        events = [e async for e in b.ask("continue")]
    assert [e for e in events if e.kind == "system" and "Phase complete" in str(e.data)]
    sent = b._client.payloads[1]["messages"]
    notes = [m for m in sent if m.get("name") == "dream_recovery_instruction" and NAMES in str(m.get("content"))]
    assert len(notes) == 1 and DENOISE in notes[0]["content"] and "not from the user" in notes[0]["content"]
    # after the compacted history, before the next request: nothing but the note follows the tool result
    assert sent[-1] is notes[0] and sent[-2]["role"] == "tool"


def test_a_window_fill_compaction_hands_the_lessons_to_the_model(tmp_path):
    (tmp_path / "PLAN.md").write_text(f"# Plan\n\nStatus: x\n\n## Lessons\n- {NAMES}\n\n## ◐ 1. Body\n",
                                      encoding="utf-8")
    b = _backend([], n_ctx=8192)
    for k in range(20):
        b.messages.append({"role": "user", "content": f"step {k} " + "w" * 1500 + f"\n\n[id:m{k + 1:04d}]"})
        b.messages.append({"role": "assistant", "content": "did it " + "v" * 1500})
    with bind_context(_context(tmp_path)):
        events = b._maybe_compact()
    assert any("compacted context" in str(e.data) for e in events)
    assert b.messages[-1].get("name") == "dream_recovery_instruction" and NAMES in b.messages[-1]["content"]


def test_no_lessons_no_note(tmp_path):
    b = _backend([], n_ctx=8192)
    for k in range(20):
        b.messages.append({"role": "user", "content": f"step {k} " + "w" * 1500 + f"\n\n[id:m{k + 1:04d}]"})
        b.messages.append({"role": "assistant", "content": "did it " + "v" * 1500})
    with bind_context(_context(tmp_path)):
        b._maybe_compact()
    assert not any(m.get("name") == "dream_recovery_instruction" for m in b.messages)


@pytest.mark.parametrize("text", [system_prompt.BASE, system_prompt.COMPACT_BASE],
                         ids=["BASE", "COMPACT_BASE"])
def test_both_prompts_ask_for_a_lesson_after_a_changed_approach_works(text):
    flat = " ".join(text.replace("\\\n", "").split())
    assert "lesson" in flat and "`lessons`" in flat and "update_plan" in flat


# --- the gate's round 1 (DREAM-123): one copy of the note, never a compaction that only moves it ----------------------

LESSONS = [f"lesson {i}: " + "fact " * 29 + "end" for i in range(12)]


def _lesson_plan(tmp_path):
    (tmp_path / "PLAN.md").write_text("# Plan\n\nStatus: x\n\n## Lessons\n" + "".join(f"- {x}\n" for x in LESSONS)
                                      + "\n## ◐ 1. Body\n", encoding="utf-8")


def _notes(b):
    return [m for m in b.messages if m.get("name") == "dream_recovery_instruction"]


def _bulk(b, k0):
    for k in range(k0, k0 + 20):
        b.messages.append({"role": "user", "content": f"step {k} " + "w" * 1500 + f"\n\n[id:m{k + 1:04d}]"})
        b.messages.append({"role": "assistant", "content": "did it " + "v" * 1500})


@pytest.mark.parametrize("path", ["window", "phase"])
def test_many_compactions_leave_exactly_one_full_copy(tmp_path, path):
    _lesson_plan(tmp_path)
    b = _backend([], n_ctx=16384)
    with bind_context(_context(tmp_path)):
        for r in range(6):
            _bulk(b, 20 * r)
            if path == "phase":
                b._phase_reset_pending = True
                events = b._phase_reset()
            else:
                events = b._maybe_compact()
            assert events, r
            notes = _notes(b)
            assert len(notes) == 1, (r, [n["content"][:60] for n in notes])
            assert notes[0] is b.messages[-1] and notes[0]["content"].startswith("[Dream, not from the user: the conversation was just compacted.")
            assert all(x in notes[0]["content"] for x in LESSONS)


def test_a_phase_reset_that_would_only_cut_the_old_note_is_no_compaction(tmp_path):
    _lesson_plan(tmp_path)
    b = _backend([], n_ctx=16384)
    # the kept head alone passes the target; and the fill passes _PHASE_RESET_AT, so the cut is tried (DREAM-126)
    b.messages[0] = {"role": "system", "content": "S" * 40_000}
    note = handoff.lessons_note(tmp_path)
    b.messages.append({"role": "user", "name": "dream_recovery_instruction", "content": note})
    for k in range(10):
        b.messages.append({"role": "user" if k % 2 == 0 else "assistant", "content": f"ok {k}"})
    before = [dict(m) for m in b.messages]
    b._last_prompt_tokens = 4152
    b._phase_reset_pending = True
    with bind_context(_context(tmp_path)):
        events = b._phase_reset()
    assert events == [] and b.messages == before and b._last_prompt_tokens == 4152


def test_a_fresh_start_hands_off_the_lessons_once(tmp_path):
    _lesson_plan(tmp_path)
    b = _backend([], n_ctx=16384)
    _bulk(b, 0)
    with bind_context(_context(tmp_path)):
        b._maybe_compact()
        assert len(_notes(b)) == 1
        lifted = b._lift_lessons()
    assert len(lifted) == 1 and not _notes(b)


def test_the_resume_excerpt_shows_hand_edited_lessons_once_and_clipped(tmp_path):
    long = "y" * 190
    (tmp_path / "PLAN.md").write_text(f"# Plan\n\n## Lessons\n- {NAMES}\n- {long}\n", encoding="utf-8")
    shown = app_mod._plan_excerpt(tmp_path / "PLAN.md")
    assert sum(NAMES in line for line in shown) == 1
    assert all(len(line) <= app_mod.RESUME_PLAN_LINE_CHARS for line in shown)
    assert f"- {'y' * 20}" in "\n".join(shown)


@pytest.mark.parametrize("given", [[], ["  ", ""]])
def test_update_plan_says_when_the_lessons_are_cleared(ws, given):
    _run(update_plan.handler(_plan(lessons=[NAMES])))
    out = _run(update_plan.handler(_plan(lessons=given)))
    assert "Lessons cleared" in out["content"][0]["text"]
    assert project.plan_lessons(ws / "PLAN.md") == []
