"""fork_verifier_agent on the local backend: directed checks report now; a full
sweep runs when the turn ends, silent on pass, and its findings open the next turn."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_schema_deferral import _FakeClient, _backend, _text_round, _tool_round  # noqa: E402
from dream.core.backends import openai_compat  # noqa: E402
from dream.tools.native import NATIVE_TOOLS  # noqa: E402

pytestmark = pytest.mark.asyncio


def _done_tool():
    from claude_agent_sdk import tool

    @tool("done", "fake done", {"type": "object", "properties": {"path": {"type": "string"}}})
    async def done(args):
        if args.get("path") == "broken.html":
            return {"content": [{"type": "text", "text": "NOT clean"}], "is_error": True}
        return {"content": [{"type": "text", "text": "clean"}]}

    return done


def _subs():
    return {"verifier": SimpleNamespace(description="checks pages", prompt="p", tool_names=["read_file"])}


def _with_verifier(monkeypatch, report="PASS", *, failed=False):
    b = _backend([*NATIVE_TOOLS, _done_tool()], subagents=_subs())
    runs: list[tuple[str, str]] = []

    async def fake_run(sub, prompt):
        runs.append((sub, prompt))
        return report, failed

    monkeypatch.setattr(b, "_run_subagent", fake_run)
    return b, runs


async def test_directed_check_runs_now_and_reports_back(monkeypatch):
    b, runs = _with_verifier(monkeypatch, report="The heading overflows at 1280px.")
    text, bad = await b._exec_tool("fork_verifier_agent", {"task": "check spacing"})
    assert bad and "call `done(path)` first" in text and runs == []
    await b._exec_tool("done", {"path": "deck.html"})
    text, bad = await b._exec_tool("fork_verifier_agent", {"task": "check spacing"})
    assert not bad and "overflows" in text
    assert runs[0][0] == "verifier" and "deck.html" in runs[0][1] and "check spacing" in runs[0][1]
    # a subagent cannot fork the verifier
    text, bad = await b._exec_tool("fork_verifier_agent", {"task": "x"}, allowed={"read_file"})
    assert bad


@pytest.mark.parametrize("report", ["PASS", "pass", "PaSs", " \tPASS\r\n"])
async def test_a_clean_done_schedules_a_sweep_that_is_silent_on_pass(monkeypatch, report):
    b, runs = _with_verifier(monkeypatch, report=report)
    b._client = _FakeClient([_tool_round("done", '{"path": "deck.html"}'), _text_round("shipped")])
    events = [ev async for ev in b.ask("build it")]
    assert runs and "Task-scoped review of deck.html" in runs[0][1]
    assert any(ev.kind == "system" and "verifier: PASS" in str(ev.data) for ev in events)
    assert b._pending_findings is None and b._verify_at_turn_end is None
    # the next turn's prompt is untouched
    b._client = _FakeClient([_text_round("ok")])
    [ev async for ev in b.ask("next")]
    assert b.messages[-2]["content"].startswith("next\n")


@pytest.mark.parametrize("report", [
    "PASS\nThe navigation is broken.",
    "PASSENGER text is clipped.",
    "PASS? I could not inspect the page.",
    "PASS — nothing to report",
    "PASS: the navigation is broken.",
    "```\nPASS\n```",
    "FAIL: the heading overflows.",
    "",
    " \t\r\n",
    "(no output)",
    "paß",
    "paſſ",
], ids=["contradiction", "prefix", "unavailable", "commentary", "colon",
        "fence", "failure", "empty", "whitespace", "no_output", "sharp_s", "long_s"])
async def test_nonstandalone_pass_is_carried_before_the_next_user_request(monkeypatch, report):
    b, runs = _with_verifier(monkeypatch, report=report)
    b._client = _FakeClient([_tool_round("done", '{"path": "deck.html"}'), _text_round("shipped")])
    events = [ev async for ev in b.ask("build it")]
    assert len(runs) == 1 and b._verify_at_turn_end is None
    lines = [str(ev.data) for ev in events if ev.kind == "system"]
    assert not any("verifier: PASS" in line for line in lines)
    assert any("findings on deck.html" in line for line in lines)
    expected = "[verifier findings for deck.html — not from the user]\n" + report.strip()
    assert b._pending_findings == expected

    request = "Stop the page task. Explain what a hash is."
    b._client = _FakeClient([_text_round("A hash is a fixed-size representation.")])
    [ev async for ev in b.ask(request)]
    messages = b._client.payloads[0]["messages"]
    assert messages[-1] == {"role": "user", "content": request + "\n\n[id:m0002]"}
    assert messages[-2] == {"role": "assistant", "name": "dream_verifier_report", "content": expected}
    assert b._pending_findings is None and len(runs) == 1
    assert expected not in b._turn_text(request)


async def test_a_failed_sweep_cannot_pass_even_with_the_standalone_marker(monkeypatch):
    b, runs = _with_verifier(monkeypatch, report="PASS", failed=True)
    b._client = _FakeClient([_tool_round("done", '{"path": "deck.html"}'), _text_round("shipped")])
    events = [ev async for ev in b.ask("build it")]
    assert len(runs) == 1 and b._verify_at_turn_end is None
    lines = [str(ev.data) for ev in events if ev.kind == "system"]
    assert not any("verifier: PASS" in line or "findings on deck.html" in line for line in lines)
    assert any("verifier: could not run" in line for line in lines)
    assert b._pending_findings == (
        "[verifier could not run on deck.html — an error, not a page finding; not from the user]\nPASS"
    )


async def test_findings_open_the_next_turn(monkeypatch):
    b, runs = _with_verifier(monkeypatch, report="1. #cta is off-screen at 1280×800")
    b._client = _FakeClient([_tool_round("done", '{"path": "deck.html"}'), _text_round("shipped")])
    events = [ev async for ev in b.ask("build it")]
    assert any("findings on deck.html" in str(ev.data) for ev in events if ev.kind == "system")
    assert b._pending_findings and "off-screen" in b._pending_findings
    b._client = _FakeClient([_text_round("fixing")])
    [ev async for ev in b.ask("thanks")]
    user = [m for m in b.messages if m.get("role") == "user"][-1]["content"]
    assert user.startswith("thanks\n") and "verifier" not in user
    report = next(m for m in b.messages if m.get("name") == "dream_verifier_report")
    assert report["role"] == "assistant"
    assert report["content"].startswith("[verifier findings for deck.html — not from the user]")
    assert b._pending_findings is None


async def test_a_failed_done_schedules_nothing_and_auto_verify_can_be_off(monkeypatch):
    b, runs = _with_verifier(monkeypatch)
    b._client = _FakeClient([_tool_round("done", '{"path": "broken.html"}'), _text_round("hm")])
    [ev async for ev in b.ask("build it")]
    assert runs == [] and b._last_done_path is None
    monkeypatch.setattr(openai_compat, "_AUTO_VERIFY", False)
    b._client = _FakeClient([_tool_round("done", '{"path": "deck.html"}'), _text_round("ok")])
    [ev async for ev in b.ask("again")]
    assert runs == [] and b._last_done_path == "deck.html"
    # an explicit request still sweeps
    text, bad = await b._exec_tool("fork_verifier_agent", {})
    assert not bad and b._verify_at_turn_end == "deck.html"
    b._client = _FakeClient([_text_round("checking")])
    events = [ev async for ev in b.ask("continue")]
    assert len(runs) == 1 and b._verify_at_turn_end is None
    assert any(ev.kind == "system" and "verifier: PASS" in str(ev.data) for ev in events)


async def test_the_schema_is_offered_only_with_a_verifier_subagent():
    b = _backend(NATIVE_TOOLS)
    b._client = _FakeClient([_text_round()])
    [ev async for ev in b.ask("hi")]
    assert "fork_verifier_agent" not in [s["function"]["name"] for s in b._client.payloads[0]["tools"]]
    b = _backend(NATIVE_TOOLS, subagents=_subs())
    b._client = _FakeClient([_text_round()])
    [ev async for ev in b.ask("hi")]
    names = [s["function"]["name"] for s in b._client.payloads[0]["tools"]]
    assert "fork_verifier_agent" in names and "task" in names


async def test_a_late_error_passes_done_and_the_verifiers_procedure_catches_it(tmp_path, monkeypatch):
    """Phase 7 criterion 7. `done` returns after load + 150 ms; the verifier's own
    steps — show_html, sleep, get_webview_logs — see what landed later."""
    from dream import config
    from dream.gui import preview as preview_mod
    from dream.tools import context as tool_context
    from dream.tools.context import ToolContext, set_context, set_studio
    from dream.tools.files import sleep
    from dream.tools.studio import done, get_webview_logs, show_html

    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=tmp_path, emit=lambda ev: None))
    set_studio(None)
    monkeypatch.setattr(config, "SCREENSHOT_DIR", tmp_path / "shots")
    preview_mod._PREVIEW = None
    try:
        (tmp_path / "late.html").write_text(
            "<!doctype html><p>fine</p><script>setTimeout(() => { throw new Error('late boom') }, 600)</script>")
        res = await done.handler({"path": "late.html"})
        assert not res.get("is_error") and "clean" in res["content"][0]["text"]
        # the verifier's procedure
        await show_html.handler({"path": "late.html"})
        await sleep.handler({"seconds": 1})
        logs = get_webview_logs.handler and (await get_webview_logs.handler({}))["content"][0]["text"]
        assert "late boom" in logs
    finally:
        if preview_mod._PREVIEW is not None:
            await preview_mod._PREVIEW.aclose()
            preview_mod._PREVIEW = None
        tool_context._CTX = None


async def test_a_sweep_still_runs_when_the_loop_guard_ends_the_turn(monkeypatch):
    """Gate 7 observation: the sweep ran only on the natural-end path, so a turn
    cut short by the loop guard carried the queued sweep into the next turn."""
    b, runs = _with_verifier(monkeypatch)
    # the model calls done(deck.html) forever → loop guard ends the turn
    b._client = _FakeClient([_tool_round("done", '{"path": "deck.html"}')])
    events = [ev async for ev in b.ask("build it")]
    assert events[-1].kind == "result" and events[-1].data["subtype"] == "loop_detected"
    assert len(runs) == 1 and "Task-scoped review of deck.html" in runs[0][1]
    assert b._verify_at_turn_end is None
    assert any(ev.kind == "system" and "verifier: PASS" in str(ev.data) for ev in events)


async def test_a_sweep_still_runs_when_the_tool_round_limit_ends_the_turn(monkeypatch):
    b, runs = _with_verifier(monkeypatch)
    monkeypatch.setattr(openai_compat, "_MAX_TOOL_ROUNDS", 2)
    b._client = _FakeClient([_tool_round("done", '{"path": "deck.html"}'),
                             _tool_round("read_file", '{"path": "nope.txt"}')])
    events = [ev async for ev in b.ask("build it")]
    assert events[-1].kind == "result" and events[-1].data["subtype"] == "tool_round_limit"
    assert len(runs) == 1 and b._verify_at_turn_end is None


async def test_a_verifier_that_could_not_run_is_not_reported_as_a_page_finding(monkeypatch):
    """A transport failure inside the sweep must not open the next turn under the
    findings header — the model would read an infrastructure error as a defect."""
    b, runs = _with_verifier(monkeypatch)

    async def broken(sub, prompt):
        runs.append((sub, prompt))
        return "(subagent 'verifier' failed: RuntimeError: boom in post)", True

    monkeypatch.setattr(b, "_run_subagent", broken)
    b._client = _FakeClient([_tool_round("done", '{"path": "deck.html"}'), _text_round("shipped")])
    events = [ev async for ev in b.ask("build it")]
    sys_lines = [str(ev.data) for ev in events if ev.kind == "system"]
    assert any("verifier: could not run" in s and "boom in post" in s for s in sys_lines)
    assert not any("findings on deck.html" in s for s in sys_lines)
    assert b._pending_findings and b._pending_findings.startswith("[verifier could not run on deck.html")
    assert "not a page finding" in b._pending_findings
