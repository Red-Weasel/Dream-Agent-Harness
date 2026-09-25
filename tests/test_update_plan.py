"""The phased plan (owner design, DREAM-083): update_plan writes PLAN.md and the
plan panel; a phase marked done needs a summary; the backend compacts once at the
phase boundary so the next phase starts lean."""
import json

import pytest

from dream.core.backends import openai_compat
from dream.tools.context import ToolContext, set_context
from dream.tools.project import PHASE_DONE, _LAST_PLAN, update_plan
from test_schema_deferral import _FakeClient, _backend, _text_round, _tool_round

pytestmark = pytest.mark.asyncio


def _plan(first="in_progress", summary=""):
    return {"title": "Falcon 9", "phases": [
        {"name": "Build", "status": first, "summary": summary,
         "steps": [{"name": "rocket.js", "status": "done" if first == "done" else "in_progress"}]},
        {"name": "Polish", "status": "pending", "steps": [{"name": "sweep", "status": "pending"}]}]}


@pytest.fixture
def ws(tmp_path):
    emitted = []
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=tmp_path, emit=emitted.append))
    _LAST_PLAN.clear()
    return tmp_path, emitted


async def test_the_plan_lands_in_plan_md_and_the_panel(ws):
    tmp, emitted = ws
    out = await update_plan.handler(_plan())
    assert not out.get("is_error")
    md = (tmp / "PLAN.md").read_text()
    assert "# Plan: Falcon 9" in md and "## ◐ 1. Build" in md and "- ○ sweep" in md
    assert emitted[-1].kind == "plan" and emitted[-1].data["phases"][0]["name"] == "Build"


async def test_a_finished_phase_needs_a_summary_and_announces_the_boundary(ws):
    tmp, _ = ws
    await update_plan.handler(_plan())
    refused = await update_plan.handler(_plan(first="done"))
    assert refused.get("is_error") and "summary" in refused["content"][0]["text"]
    ok = await update_plan.handler(_plan(first="done", summary="rocket.js done; plume next"))
    assert ok["content"][0]["text"].startswith(PHASE_DONE)
    assert "> **Summary:** rocket.js done; plume next" in (tmp / "PLAN.md").read_text()


async def test_the_backend_compacts_once_at_a_phase_boundary(ws):
    tmp, _ = ws
    from dream.tools import registry  # noqa: F401  (tool objects are registered)
    from test_compaction import _tool
    b = _backend([update_plan], n_ctx=16384)
    for k in range(12):
        b.messages.append({"role": "user", "content": f"step {k} " + "w" * 1500 + f"\n\n[id:m{k + 1:04d}]"})
        b.messages.append({"role": "assistant", "content": "did it " + "v" * 1500})
    b._msg_seq = 12
    await update_plan.handler(_plan())            # the plan exists before this turn
    b._client = _FakeClient([
        _tool_round("update_plan", json.dumps(_plan(first="done", summary="Build done; polish next"))),
        _text_round("next phase"),
    ])
    before = openai_compat._est_tokens(b.messages)
    events = [e async for e in b.ask("continue")]
    notes = [e.data for e in events if e.kind == "system" and "Phase complete" in str(e.data)]
    assert len(notes) == 1
    assert len(b._client.payloads) == 2
    assert openai_compat._est_tokens(b._client.payloads[1]["messages"]) < before / 2


# 2026-09-25 03:06 (fix list #93): the schema required `steps` on EVERY phase, so a finished phase described only by its
# summary was refused twice ("phases.0: missing required field steps. Nothing ran.") before the model complied.
from dream.core.tool_validation import validate_arguments


def _summary_only_plan():
    return {"title": "Falcon 9", "phases": [
        {"name": "Reference", "status": "done", "summary": "seven photos reviewed"},
        {"name": "Build", "status": "in_progress", "steps": [{"name": "rocket.js", "status": "in_progress"}]}]}


def test_a_phase_with_a_summary_needs_no_steps_list():
    assert validate_arguments(update_plan.input_schema, _summary_only_plan()) is None


def test_a_phase_without_steps_or_summary_still_validates_as_an_empty_phase():
    plan = {"phases": [{"name": "Later", "status": "pending"}]}
    assert validate_arguments(update_plan.input_schema, plan) is None


async def test_a_summary_only_done_phase_is_written_with_zero_steps(ws):
    tmp, emitted = ws
    out = await update_plan.handler(_summary_only_plan())
    assert not out.get("is_error"), out
    md = (tmp / "PLAN.md").read_text()
    assert "## ● 1. Reference" in md and "> **Summary:** seven photos reviewed" in md
    assert emitted[-1].data["phases"][0]["steps"] == []


def test_the_usage_error_names_steps_as_optional():
    import asyncio
    out = asyncio.run(update_plan.handler({"phases": []}))
    assert out.get("is_error") and "steps optional" in out["content"][0]["text"]
