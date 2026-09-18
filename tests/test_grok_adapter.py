"""Grok (xAI) CLI adapter tests — mirrors tests/test_cli_agent.py.

Two layers: (1) ``GrokAdapter.translate`` is a pure function of one decoded
``--output-format streaming-json`` object plus a per-turn ``state`` dict (used to
accumulate the ``text`` deltas into the terminal ``assistant_done``); it is driven
here over recorded fixtures and asserted against exact Event sequences. (2) One
subprocess-level ``ask()`` test spawns a tiny fake ``grok`` shell script (which
records its argv and cats a fixture) to confirm the backend streams events and
captures the ``sessionId`` from the ``end`` event for a resume. The real grok
binary is never invoked and no auth/network is touched.

The streaming-json schema (text/thought/end/error, + max_turns_reached /
auto_compact_*) was verified from grok's bundled headless-mode doc and from the
binary's own ``src/headless.rs`` emitter strings.
"""

from __future__ import annotations

import json
from pathlib import Path

from dream.core.backends.cli_agent import CliAgentBackend
from dream.core.backends.grok_adapter import GrokAdapter

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> list[dict]:
    text = (FIXTURES / name).read_text()
    return [json.loads(ln) for ln in text.splitlines() if ln.strip()]


def _run_fixture(name: str) -> tuple[list, str | None]:
    """Feed every object of a fixture through the adapter, threading one state dict
    (deltas accumulate there), returning (events, session)."""
    adapter = GrokAdapter()
    state: dict = {}
    events: list = []
    session: str | None = None
    for obj in _load(name):
        sid = adapter.capture_session(obj)
        if sid:
            session = sid
        events.extend(adapter.translate(obj, state))
    return events, session


# --- pure translate() over the fixtures --------------------------------------


def test_translate_simple_fixture():
    events, session = _run_fixture("grok_simple.jsonl")

    # Two text deltas stream live, then a single accumulated assistant_done, then
    # the terminal result. sessionId is captured from the end event.
    assert [e.kind for e in events] == [
        "text_delta", "text_delta", "assistant_done", "result",
    ]
    assert session == "00000000-0000-4000-8000-000000000009"

    assert events[0].data == "po"
    assert events[1].data == "ng"
    assert events[2].data == "pong"  # accumulated from the deltas

    result = events[3].data
    assert result["is_error"] is False
    assert result["subtype"] == "success"
    assert result["usage"] == {}          # streaming-json end carries no usage
    assert result["total_cost_usd"] is None


def test_translate_thinking_fixture():
    events, session = _run_fixture("grok_thinking.jsonl")

    assert [e.kind for e in events] == [
        "thinking_delta", "text_delta", "text_delta", "assistant_done", "result",
    ]
    assert session == "00000000-0000-4000-8000-000000000010"
    assert events[0].data == "The user said ping. I'll reply pong."
    assert events[3].data == "I'll run `ls -a` and report. The directory is empty."
    assert events[4].data["subtype"] == "success"


def test_translate_maxturns_fixture():
    events, session = _run_fixture("grok_maxturns.jsonl")

    # max_turns_reached becomes a system note; the truncated turn still flushes its
    # partial text as assistant_done and reports a non-success (but not error)
    # subtype so consolidation treats it as incomplete.
    assert [e.kind for e in events] == [
        "text_delta", "system", "assistant_done", "result",
    ]
    assert session == "00000000-0000-4000-8000-000000000008"
    assert events[1].data == "grok: maximum turns reached"
    assert events[2].data == "Working on it"

    result = events[3].data
    assert result["is_error"] is False
    assert result["subtype"] == "MaxTurns"  # non-success → treated as incomplete


def test_translate_error_fixture():
    events, session = _run_fixture("grok_error.jsonl")
    assert [e.kind for e in events] == ["error"]
    assert "model temporarily unavailable" in events[0].data
    assert session is None  # a failed start carries no resume id


# --- translate() edge cases --------------------------------------------------


def test_end_without_text_emits_only_result():
    adapter = GrokAdapter()
    state: dict = {}
    events = adapter.translate(
        {"type": "end", "stopReason": "EndTurn", "sessionId": "s"}, state
    )
    assert [e.kind for e in events] == ["result"]  # no assistant_done when empty
    assert events[0].data["subtype"] == "success"


def test_empty_text_delta_is_dropped():
    adapter = GrokAdapter()
    state: dict = {}
    assert adapter.translate({"type": "text", "data": ""}, state) == []
    # a following non-empty end still yields just the result (nothing accumulated)
    assert [e.kind for e in adapter.translate({"type": "end"}, state)] == ["result"]


def test_auto_compact_started_is_dropped_but_failure_surfaces():
    adapter = GrokAdapter()
    assert adapter.translate({"type": "auto_compact_started"}, {}) == []
    assert adapter.translate({"type": "auto_compact_completed"}, {}) == []
    (ev,) = adapter.translate({"type": "auto_compact_failed"}, {})
    assert ev.kind == "system" and "auto compact failed" in ev.data


def test_unknown_event_type_renders_nothing():
    adapter = GrokAdapter()
    assert adapter.translate({"type": "totally_new_event", "x": 1}, {}) == []
    assert adapter.translate({"type": "turn_started"}, {}) == []


def test_error_event_message_fallbacks():
    adapter = GrokAdapter()
    # `message` preferred, then `data`, then a generic fallback.
    assert adapter.translate({"type": "error", "message": "boom"}, {})[0].data == "boom"
    assert adapter.translate({"type": "error", "data": "splat"}, {})[0].data == "splat"
    assert adapter.translate({"type": "error"}, {})[0].data == "grok error"


def test_capture_session_only_from_session_id_field():
    adapter = GrokAdapter()
    assert adapter.capture_session({"type": "end", "sessionId": "abc"}) == "abc"
    assert adapter.capture_session({"type": "text", "data": "hi"}) is None
    assert adapter.capture_session({"type": "end", "sessionId": ""}) is None


# --- argv ---------------------------------------------------------------------


def test_argv_first_turn_and_resume():
    adapter = GrokAdapter()
    # read-only posture (Dream plan/ask) → grok plan mode.
    first = adapter.argv("hello", cwd="/work", resume_id=None, sandbox="read-only")
    assert first == [
        "grok", "-p", "hello", "--output-format", "streaming-json",
        "--cwd", "/work", "--permission-mode", "plan",
    ]

    # workspace-write posture (accept-edits/auto/default) → bypassPermissions, and
    # a resume id threads through as `-r`.
    resumed = adapter.argv("again", cwd="/work", resume_id="sess-9", sandbox="workspace-write")
    assert resumed == [
        "grok", "-p", "again", "--output-format", "streaming-json",
        "--cwd", "/work", "-r", "sess-9", "--permission-mode", "bypassPermissions",
    ]


def test_argv_unknown_sandbox_defaults_to_bypass():
    adapter = GrokAdapter()
    argv = adapter.argv("x", cwd="/w", resume_id=None, sandbox="something-else")
    assert argv[-2:] == ["--permission-mode", "bypassPermissions"]


def test_argv_accepts_mcp_config_kwarg_but_ignores_it():
    adapter = GrokAdapter()
    cfg = {"name": "dream", "command": "/venv/py", "args": ["-m", "dream.mcp"],
           "env": {"DREAM_SESSION_ID": "s1"}}
    with_cfg = adapter.argv("hi", cwd="/w", resume_id=None, sandbox="read-only", mcp_config=cfg)
    without = adapter.argv("hi", cwd="/w", resume_id=None, sandbox="read-only")
    # grok registers MCP persistently (mcp add), not per-invocation → argv is
    # identical whether or not an mcp_config is supplied.
    assert with_cfg == without
    assert "dream" not in " ".join(with_cfg)


# --- MCP registration (persistent, not session-scoped) -----------------------


def test_mcp_register_and_deregister_argv():
    adapter = GrokAdapter()
    reg = adapter.mcp_register_argv(
        "dream", {"DREAM_SESSION_ID": "s1", "DREAM_DB": "/d.db"},
        ["/venv/py", "-m", "dream.mcp"],
    )
    assert reg == [
        "grok", "mcp", "add", "dream",
        "--env", "DREAM_SESSION_ID=s1", "--env", "DREAM_DB=/d.db",
        "--", "/venv/py", "-m", "dream.mcp",
    ]
    assert adapter.mcp_deregister_argv("dream") == ["grok", "mcp", "remove", "dream"]


# --- subprocess-level ask() against a fake grok ------------------------------


def _write_fake_grok(tmp_path, fixture: Path, argsdir: Path) -> Path:
    """A stand-in 'grok' that records its argv and cats a fixture — never the real CLI."""
    argsdir.mkdir(parents=True, exist_ok=True)
    fake = tmp_path / "grok"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'DIR="{argsdir}"\n'
        'n=$(ls "$DIR" | wc -l)\n'
        'printf "%s" "$*" > "$DIR/call_$n"\n'
        f'cat "{fixture}"\n'
    )
    fake.chmod(0o755)
    return fake


async def test_ask_subprocess_streams_events_and_captures_session_id(tmp_path, monkeypatch):
    fixture = FIXTURES / "grok_simple.jsonl"
    argsdir = tmp_path / "calls"
    fake = _write_fake_grok(tmp_path, fixture, argsdir)

    adapter = GrokAdapter()
    monkeypatch.setattr(adapter, "cmd", str(fake))  # absolute path; never the real grok
    backend = CliAgentBackend(adapter, system_prompt="SYSTEM-PROMPT", cwd=str(tmp_path))
    await backend.connect()

    # First turn: streams the fixture's events, captures the resume id from `end`.
    turn1 = [e async for e in backend.ask("ping")]
    assert [e.kind for e in turn1] == [
        "text_delta", "text_delta", "assistant_done", "result",
    ]
    assert turn1[2].data == "pong"
    assert backend._session_id == "00000000-0000-4000-8000-000000000009"

    # Second turn: resumes the captured session.
    turn2 = [e async for e in backend.ask("again")]
    assert [e.kind for e in turn2][-1] == "result"

    call0 = (argsdir / "call_0").read_text()
    call1 = (argsdir / "call_1").read_text()
    # First turn prepends the system prompt into the -p value and does not resume.
    assert "-r " not in call0
    assert "SYSTEM-PROMPT" in call0
    assert "--output-format streaming-json" in call0
    # Second turn resumes with the captured session id and drops the system prompt.
    assert "-r 00000000-0000-4000-8000-000000000009" in call1
    assert "SYSTEM-PROMPT" not in call1

    await backend.disconnect()


async def test_ask_uses_sandbox_getter_for_permission_mode(tmp_path, monkeypatch):
    fixture = FIXTURES / "grok_simple.jsonl"
    argsdir = tmp_path / "calls"
    fake = _write_fake_grok(tmp_path, fixture, argsdir)

    adapter = GrokAdapter()
    monkeypatch.setattr(adapter, "cmd", str(fake))
    backend = CliAgentBackend(
        adapter, system_prompt="S", cwd=str(tmp_path),
        sandbox_getter=lambda: "read-only",
    )
    await backend.connect()

    _ = [e async for e in backend.ask("ping")]
    call0 = (argsdir / "call_0").read_text()
    assert "--permission-mode plan" in call0

    await backend.disconnect()


async def test_ask_nonzero_exit_without_result_yields_error(tmp_path, monkeypatch):
    fake = tmp_path / "grok"
    fake.write_text("#!/usr/bin/env bash\necho 'auth expired' 1>&2\nexit 1\n")
    fake.chmod(0o755)

    adapter = GrokAdapter()
    monkeypatch.setattr(adapter, "cmd", str(fake))
    backend = CliAgentBackend(adapter, system_prompt="S", cwd=str(tmp_path))
    await backend.connect()

    events = [e async for e in backend.ask("ping")]
    assert [e.kind for e in events] == ["error"]
    assert "auth expired" in events[0].data
    assert "exited 1" in events[0].data

    await backend.disconnect()
