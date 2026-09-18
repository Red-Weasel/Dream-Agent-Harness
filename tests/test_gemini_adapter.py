"""GeminiAdapter — same two-layer pattern as ``test_cli_agent.py``.

(1) ``GeminiAdapter.translate`` is a pure function of one decoded ``stream-json``
object, driven here over recorded ``gemini … --output-format stream-json`` fixtures and
asserted against exact Event sequences; (2) one subprocess-level ``ask()`` test that
spawns a tiny fake ``gemini`` shell script (which cats a fixture) to confirm the backend
streams events and captures the session_id for a resume. The real gemini binary is never
invoked. Event shapes come from the gemini-cli source (packages/core/src/output/types.ts
+ packages/cli/src/nonInteractiveCli.ts).
"""

from __future__ import annotations

import json
from pathlib import Path

from dream.core.backends.cli_agent import CliAgentBackend
from dream.core.backends.gemini_adapter import GeminiAdapter

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> list[dict]:
    text = (FIXTURES / name).read_text()
    return [json.loads(ln) for ln in text.splitlines() if ln.strip()]


def _run_fixture(name: str) -> tuple[list, str | None]:
    """Feed every object of a fixture through the adapter, returning (events, session)."""
    adapter = GeminiAdapter()
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
    events, session = _run_fixture("gemini_simple.jsonl")

    # two assistant deltas paint live, then flush as one assistant_done, then result.
    assert [e.kind for e in events] == [
        "text_delta", "text_delta", "assistant_done", "result",
    ]
    # init captured, not rendered; user echo dropped.
    assert session == "00000000-0000-4000-8000-000000000005"

    assert events[0].data == "po"
    assert events[1].data == "ng"
    assert events[2].data == "pong"  # accumulated + stripped

    result = events[3].data
    assert result["is_error"] is False
    assert result["subtype"] == "success"
    assert result["total_cost_usd"] is None
    usage = result["usage"]
    assert usage["prompt_tokens"] == 1500       # mapped from input_tokens
    assert usage["completion_tokens"] == 20      # mapped from output_tokens
    assert usage["input_tokens"] == 1500         # raw kept alongside
    assert usage["cached"] == 512
    assert usage["total_tokens"] == 1520


def test_translate_toolcall_fixture():
    events, session = _run_fixture("gemini_toolcall.jsonl")

    assert [e.kind for e in events] == [
        "text_delta", "text_delta", "assistant_done",
        "tool_use", "tool_result",
        "text_delta", "assistant_done", "result",
    ]
    assert session == "00000000-0000-4000-8000-000000000006"

    # assistant text flushed just before the tool call
    assert events[2].data == "I'll run `ls -a`."

    tool_use = events[3].data
    assert tool_use["name"] == "Shell"
    assert tool_use["input"]["command"] == "ls -a"
    assert tool_use["id"] == "shell-1"

    tool_result = events[4].data
    # name correlated back from the tool_use (tool_result carries only tool_id)
    assert tool_result["name"] == "Shell"
    assert tool_result["id"] == tool_use["id"]
    assert tool_result["content"] == ".\n..\n"
    assert tool_result["is_error"] is False

    assert events[6].data == "Only `.` and `..` exist."
    assert events[7].kind == "result"
    assert events[7].data["usage"]["completion_tokens"] == 210
    assert events[7].data["usage"]["tool_calls"] == 1


def test_translate_error_fixture():
    events, session = _run_fixture("gemini_error.jsonl")

    # assistant delta, flush at the error boundary, the error note, then the failed result.
    assert [e.kind for e in events] == [
        "text_delta", "assistant_done", "system", "result",
    ]
    assert session == "00000000-0000-4000-8000-000000000004"

    assert events[1].data == "working on it"
    assert events[2].data == "Loop detected, stopping execution"

    result = events[3].data
    assert result["is_error"] is True
    assert result["subtype"] == "error"
    assert result["error"] == "Maximum session turns exceeded"


# --- translate() edge cases --------------------------------------------------


def test_init_and_user_message_render_nothing():
    adapter = GeminiAdapter()
    assert adapter.translate(
        {"type": "init", "session_id": "x", "model": "gemini-2.5-pro"}, {}
    ) == []
    assert adapter.translate({"type": "message", "role": "user", "content": "hi"}, {}) == []
    # an unknown event type is ignored
    assert adapter.translate({"type": "heartbeat"}, {}) == []


def test_capture_session_only_from_init():
    adapter = GeminiAdapter()
    assert adapter.capture_session(
        {"type": "init", "session_id": "sess-9", "model": "m"}
    ) == "sess-9"
    assert adapter.capture_session({"type": "result", "status": "success"}) is None
    # an init with no/blank id yields nothing rather than a falsy resume token
    assert adapter.capture_session({"type": "init", "session_id": ""}) is None


def test_assistant_delta_accumulates_then_flushes():
    adapter = GeminiAdapter()
    state: dict = {}
    assert [e.kind for e in adapter.translate(
        {"type": "message", "role": "assistant", "content": "a", "delta": True}, state)] == ["text_delta"]
    # each delta paints live but no assistant_done flushes yet
    assert [e.kind for e in adapter.translate(
        {"type": "message", "role": "assistant", "content": "b", "delta": True}, state)] == ["text_delta"]
    # nothing flushes until a boundary event; the result forces the flush.
    events = adapter.translate({"type": "result", "status": "success", "stats": {}}, state)
    assert [e.kind for e in events] == ["assistant_done", "result"]
    assert events[0].data == "ab"


def test_tool_result_error_uses_error_message_and_flag():
    adapter = GeminiAdapter()
    state: dict = {}
    adapter.translate({
        "type": "tool_use", "tool_name": "Read", "tool_id": "r1",
        "parameters": {"file_path": "/nope"},
    }, state)
    (ev,) = adapter.translate({
        "type": "tool_result", "tool_id": "r1", "status": "error",
        "error": {"type": "FILE_NOT_FOUND", "message": "File not found"},
    }, state)
    assert ev.kind == "tool_result"
    assert ev.data["name"] == "Read"
    assert ev.data["id"] == "r1"
    assert ev.data["is_error"] is True
    assert ev.data["content"] == "File not found"


def test_tool_result_without_prior_tool_use_falls_back_to_generic_name():
    adapter = GeminiAdapter()
    (ev,) = adapter.translate({
        "type": "tool_result", "tool_id": "orphan", "status": "success", "output": "ok",
    }, {})
    assert ev.data["name"] == "tool"
    assert ev.data["id"] == "orphan"
    assert ev.data["content"] == "ok"


# --- argv ---------------------------------------------------------------------


def test_argv_first_turn_and_resume():
    adapter = GeminiAdapter()
    first = adapter.argv("hello", cwd="/work", resume_id=None, sandbox="read-only")
    assert first == [
        "gemini", "--output-format", "stream-json",
        "--approval-mode", "plan",            # read-only Dream mode → gemini plan
        "--include-directories", "/work",
        "-p", "hello",
    ]

    resumed = adapter.argv("again", cwd="/work", resume_id="sess-9", sandbox="workspace-write")
    assert resumed == [
        "gemini", "--output-format", "stream-json",
        "--approval-mode", "yolo",            # edit-capable mode → gemini yolo
        "--include-directories", "/work",
        "--resume", "sess-9",
        "-p", "again",
    ]


def test_argv_ignores_mcp_config():
    # mcp_config is accepted for interface parity but not injected (gemini reads MCP
    # servers from ~/.gemini/settings.json), so argv is unchanged by it.
    adapter = GeminiAdapter()
    cfg = {"name": "dream", "command": "/venv/py", "args": ["-m", "dream.mcp"], "env": {}}
    with_cfg = adapter.argv("hi", cwd="/w", resume_id=None, sandbox="read-only", mcp_config=cfg)
    without = adapter.argv("hi", cwd="/w", resume_id=None, sandbox="read-only")
    assert with_cfg == without
    assert "dream" not in " ".join(with_cfg)


# --- subprocess-level ask() against a fake gemini -----------------------------


def _write_fake_gemini(tmp_path, fixture: Path, argsdir: Path) -> Path:
    """A stand-in 'gemini' that records its argv and cats a fixture — never the real CLI."""
    argsdir.mkdir(parents=True, exist_ok=True)
    fake = tmp_path / "gemini"
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
    fixture = FIXTURES / "gemini_simple.jsonl"
    argsdir = tmp_path / "calls"
    fake = _write_fake_gemini(tmp_path, fixture, argsdir)

    adapter = GeminiAdapter()
    monkeypatch.setattr(adapter, "cmd", str(fake))  # absolute path; never the real gemini
    backend = CliAgentBackend(adapter, system_prompt="SYSTEM-PROMPT", cwd=str(tmp_path))
    await backend.connect()

    # First turn: streams the fixture's events, captures the resume id.
    turn1 = [e async for e in backend.ask("ping")]
    assert [e.kind for e in turn1] == ["text_delta", "text_delta", "assistant_done", "result"]
    assert turn1[2].data == "pong"
    assert backend._session_id == "00000000-0000-4000-8000-000000000005"

    # Second turn: resumes the captured session.
    turn2 = [e async for e in backend.ask("again")]
    assert [e.kind for e in turn2] == ["text_delta", "text_delta", "assistant_done", "result"]

    call0 = (argsdir / "call_0").read_text()
    call1 = (argsdir / "call_1").read_text()
    # First turn prepends the system prompt and does not resume.
    assert "--resume" not in call0
    assert "SYSTEM-PROMPT" in call0
    # Second turn resumes with the captured session id and drops the system prompt.
    assert "--resume" in call1
    assert "00000000-0000-4000-8000-000000000005" in call1
    assert "SYSTEM-PROMPT" not in call1

    await backend.disconnect()


async def test_ask_uses_sandbox_getter_for_approval_mode(tmp_path, monkeypatch):
    fixture = FIXTURES / "gemini_simple.jsonl"
    argsdir = tmp_path / "calls"
    fake = _write_fake_gemini(tmp_path, fixture, argsdir)

    adapter = GeminiAdapter()
    monkeypatch.setattr(adapter, "cmd", str(fake))
    backend = CliAgentBackend(
        adapter, system_prompt="S", cwd=str(tmp_path),
        sandbox_getter=lambda: "read-only",
    )
    await backend.connect()

    _ = [e async for e in backend.ask("ping")]
    call0 = (argsdir / "call_0").read_text()
    assert "--approval-mode plan" in call0

    await backend.disconnect()


async def test_ask_nonzero_exit_without_result_yields_error(tmp_path, monkeypatch):
    fake = tmp_path / "gemini"
    fake.write_text("#!/usr/bin/env bash\necho 'not signed in' 1>&2\nexit 1\n")
    fake.chmod(0o755)

    adapter = GeminiAdapter()
    monkeypatch.setattr(adapter, "cmd", str(fake))
    backend = CliAgentBackend(adapter, system_prompt="S", cwd=str(tmp_path))
    await backend.connect()

    events = [e async for e in backend.ask("ping")]
    assert [e.kind for e in events] == ["error"]
    assert "not signed in" in events[0].data
    assert "exited 1" in events[0].data

    await backend.disconnect()
