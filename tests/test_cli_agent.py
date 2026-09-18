"""Module A — CLI-agent backend.

Two layers: (1) ``CodexAdapter.translate`` is a pure function of one decoded stream
object, driven here over the synthetic ``codex exec --json`` fixtures and asserted
against the exact Event sequences the plan specifies; (2) one subprocess-level
``ask()`` test that spawns a tiny fake ``codex`` shell script (which just cats a
fixture) to confirm the backend streams the events and captures the thread_id for a
resume. The real codex binary is never invoked.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from dream.core.backends import cli_agent
from dream.core.backends.cli_agent import CliAgentBackend, CodexAdapter

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> list[dict]:
    text = (FIXTURES / name).read_text()
    return [json.loads(ln) for ln in text.splitlines() if ln.strip()]


def _run_fixture(name: str) -> tuple[list, str | None]:
    """Feed every object of a fixture through the adapter, returning (events, session)."""
    adapter = CodexAdapter()
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
    events, session = _run_fixture("codex_simple.jsonl")

    assert [e.kind for e in events] == ["assistant_done", "result"]
    # thread.started captured, not rendered; the skills-budget error dropped.
    assert session == "00000000-0000-4000-8000-000000000002"

    assert events[0].data == "pong"

    result = events[1].data
    assert result["is_error"] is False
    assert result["subtype"] == "success"
    assert result["total_cost_usd"] is None
    usage = result["usage"]
    assert usage["prompt_tokens"] == 20411      # mapped from input_tokens
    assert usage["completion_tokens"] == 5       # mapped from output_tokens
    assert usage["input_tokens"] == 20411        # raw kept alongside
    assert usage["cached_input_tokens"] == 8960


def test_translate_toolcall_fixture():
    events, session = _run_fixture("codex_toolcall.jsonl")

    assert [e.kind for e in events] == [
        "assistant_done", "tool_use", "tool_result", "assistant_done", "result",
    ]
    assert session == "00000000-0000-4000-8000-000000000003"

    assert events[0].data == "I'll run `ls -a` once in the current directory and report the output."

    tool_use = events[1].data
    assert tool_use["name"] == "shell"
    assert tool_use["input"]["command"] == "/bin/bash -lc 'ls -a'"
    assert tool_use["id"] == "item_2"

    tool_result = events[2].data
    assert tool_result["name"] == "shell"
    assert tool_result["content"] == ".\n..\n"
    assert tool_result["is_error"] is False

    assert events[3].data == "The directory contains only `.` and `..` — no files."
    assert events[4].kind == "result"
    assert events[4].data["usage"]["completion_tokens"] == 141


# --- translate() edge cases --------------------------------------------------


def test_thread_started_and_turn_started_render_nothing():
    adapter = CodexAdapter()
    assert adapter.translate({"type": "thread.started", "thread_id": "x"}, {}) == []
    assert adapter.translate({"type": "turn.started"}, {}) == []


def test_skills_budget_error_is_dropped():
    adapter = CodexAdapter()
    obj = {"type": "item.completed", "item": {
        "type": "error",
        "message": "Skill descriptions were shortened to fit the 2% skills context budget.",
    }}
    assert adapter.translate(obj, {}) == []


def test_other_error_becomes_system_event():
    adapter = CodexAdapter()
    obj = {"type": "item.completed", "item": {"type": "error", "message": "sandbox denied write"}}
    events = adapter.translate(obj, {})
    assert [e.kind for e in events] == ["system"]
    assert events[0].data == "sandbox denied write"


def test_command_nonzero_exit_is_error():
    adapter = CodexAdapter()
    obj = {"type": "item.completed", "item": {
        "type": "command_execution", "command": "false",
        "aggregated_output": "boom", "exit_code": 1, "status": "completed",
    }}
    (event,) = adapter.translate(obj, {})
    assert event.kind == "tool_result"
    assert event.data["is_error"] is True
    assert event.data["content"] == "boom"


# --- MCP tool calls (Dream's own tools reaching codex over the stdio server) ---


def test_mcp_tool_call_fixture_translates_to_dream_tool_events():
    events, session = _run_fixture("codex_mcp_toolcall.jsonl")
    kinds = [e.kind for e in events]
    assert kinds == ["tool_use", "tool_result", "assistant_done", "result"]
    tu, tr = events[0], events[1]
    assert tu.data["name"] == "recall" and tu.data["input"] == {"query": "the user"}
    assert tr.data["name"] == "recall" and tr.data["is_error"] is False
    assert "Synthetic fixture: example project uses four-space indentation" in tr.data["content"]
    assert session == "00000000-0000-4000-8000-000000000001"


def test_mcp_tool_call_error_is_flagged():
    adapter = CodexAdapter()
    obj = {"type": "item.completed", "item": {
        "id": "x", "type": "mcp_tool_call", "server": "dream", "tool": "remember",
        "arguments": {}, "result": None, "error": "bad args", "status": "completed",
    }}
    (ev,) = adapter.translate(obj, {})
    assert ev.kind == "tool_result" and ev.data["is_error"] is True
    assert "bad args" in ev.data["content"]


def test_argv_injects_session_scoped_mcp_overrides():
    adapter = CodexAdapter()
    cfg = {"name": "dream", "command": "/venv/py", "args": ["-m", "dream.mcp"],
           "env": {"DREAM_SESSION_ID": "s1"}}
    argv = adapter.argv("hi", cwd="/w", resume_id=None, sandbox="read-only", mcp_config=cfg)
    joined = " ".join(argv)
    assert '-c mcp_servers.dream.command="/venv/py"' in joined
    assert 'mcp_servers.dream.args=["-m", "dream.mcp"]' in joined
    assert 'mcp_servers.dream.env={DREAM_SESSION_ID="s1"}' in joined
    assert argv[-1] == "hi"  # the prompt stays the final positional arg
    # No config → no overrides (unchanged argv).
    assert "-c" not in adapter.argv("hi", cwd="/w", resume_id=None, sandbox="read-only")


# --- argv ---------------------------------------------------------------------


def test_argv_first_turn_and_resume():
    adapter = CodexAdapter()
    first = adapter.argv("hello", cwd="/work", resume_id=None, sandbox="read-only")
    assert first == [
        "codex", "exec", "--json", "--skip-git-repo-check",
        "--sandbox", "read-only", "-C", "/work", "hello",
    ]

    # Options belong to `exec` and must come BEFORE the subcommand. This test
    # previously asserted the reverse and so encoded a real bug: every turn past
    # the first died with "unexpected argument '--sandbox' found", because
    # `codex exec resume` accepts no sandbox flag. First turns passed, so the
    # suite stayed green while the backend was unusable for multi-turn work.
    resumed = adapter.argv("again", cwd="/work", resume_id="tid-9", sandbox="workspace-write")
    assert resumed == [
        "codex", "exec", "--json", "--skip-git-repo-check",
        "--sandbox", "workspace-write", "-C", "/work", "resume", "tid-9", "again",
    ]
    # The flags must precede the subcommand, whatever else changes.
    assert resumed.index("--sandbox") < resumed.index("resume")


# --- subprocess-level ask() against a fake codex ------------------------------


def _write_fake_codex(tmp_path, fixture: Path, argsdir: Path) -> Path:
    """A stand-in 'codex' that records its argv and cats a fixture — never the real CLI."""
    argsdir.mkdir(parents=True, exist_ok=True)
    fake = tmp_path / "codex"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'DIR="{argsdir}"\n'
        'n=$(ls "$DIR" | wc -l)\n'
        'printf "%s" "$*" > "$DIR/call_$n"\n'
        f'cat "{fixture}"\n'
    )
    fake.chmod(0o755)
    return fake


async def test_ask_subprocess_streams_events_and_captures_thread_id(tmp_path, monkeypatch):
    fixture = FIXTURES / "codex_simple.jsonl"
    argsdir = tmp_path / "calls"
    fake = _write_fake_codex(tmp_path, fixture, argsdir)

    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "cmd", str(fake))  # absolute path; never the real codex
    backend = CliAgentBackend(adapter, system_prompt="SYSTEM-PROMPT", cwd=str(tmp_path))
    await backend.connect()

    # First turn: streams the fixture's events, captures the resume id.
    turn1 = [e async for e in backend.ask("ping")]
    assert [e.kind for e in turn1] == ["assistant_done", "result"]
    assert turn1[0].data == "pong"
    assert backend._session_id == "00000000-0000-4000-8000-000000000002"

    # Second turn: resumes the captured session.
    turn2 = [e async for e in backend.ask("again")]
    assert [e.kind for e in turn2] == ["assistant_done", "result"]

    call0 = (argsdir / "call_0").read_text()
    call1 = (argsdir / "call_1").read_text()
    # First turn prepends the system prompt and does not resume.
    assert "resume" not in call0
    assert "SYSTEM-PROMPT" in call0
    # Second turn resumes with the captured thread id and drops the system prompt.
    assert "resume" in call1
    assert "00000000-0000-4000-8000-000000000002" in call1
    assert "SYSTEM-PROMPT" not in call1

    await backend.disconnect()


async def test_ask_uses_sandbox_getter(tmp_path, monkeypatch):
    fixture = FIXTURES / "codex_simple.jsonl"
    argsdir = tmp_path / "calls"
    fake = _write_fake_codex(tmp_path, fixture, argsdir)

    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "cmd", str(fake))
    backend = CliAgentBackend(
        adapter, system_prompt="S", cwd=str(tmp_path),
        sandbox_getter=lambda: "read-only",
    )
    await backend.connect()

    _ = [e async for e in backend.ask("ping")]
    call0 = (argsdir / "call_0").read_text()
    assert "--sandbox read-only" in call0

    await backend.disconnect()


async def test_ask_nonzero_exit_without_result_yields_error(tmp_path, monkeypatch):
    fake = tmp_path / "codex"
    fake.write_text("#!/usr/bin/env bash\necho 'auth expired' 1>&2\nexit 3\n")
    fake.chmod(0o755)

    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "cmd", str(fake))
    backend = CliAgentBackend(adapter, system_prompt="S", cwd=str(tmp_path))
    await backend.connect()

    events = [e async for e in backend.ask("ping")]
    assert [e.kind for e in events] == ["error"]
    assert "auth expired" in events[0].data
    assert "exited 3" in events[0].data

    await backend.disconnect()


async def test_stderr_is_bounded_and_keeps_the_end(tmp_path, monkeypatch):
    """A crash-looping CLI can spew megabytes to stderr; only the tail is ever
    shown, so only a bounded tail may be HELD. The trim must keep the end —
    that's where the actual failure reason lands."""
    fake = tmp_path / "codex"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "for i in $(seq 1 20000); do echo 'noise noise noise' 1>&2; done\n"
        "echo 'FINAL-REASON: auth expired' 1>&2\n"
        "exit 7\n"
    )
    fake.chmod(0o755)

    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "cmd", str(fake))
    backend = CliAgentBackend(adapter, system_prompt="S", cwd=str(tmp_path))
    await backend.connect()

    events = [e async for e in backend.ask("ping")]
    assert [e.kind for e in events] == ["error"]
    assert "FINAL-REASON: auth expired" in events[0].data
    await backend.disconnect()


async def test_abort_kills_a_cli_that_ignores_sigterm(tmp_path, monkeypatch):
    """The abort path used to terminate() and then wait() forever — a CLI that
    shrugs off SIGTERM hung the whole session on Ctrl-C. terminate must
    escalate to kill after the grace period."""
    fake = tmp_path / "codex"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "trap '' TERM\n"
        'echo \'{"type":"item.completed","item":{"type":"agent_message","text":"hi"}}\'\n'
        "sleep 60\n"
    )
    fake.chmod(0o755)

    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "cmd", str(fake))
    monkeypatch.setattr(cli_agent, "_TERM_GRACE_S", 0.2)
    backend = CliAgentBackend(adapter, system_prompt="S", cwd=str(tmp_path))
    await backend.connect()

    gen = backend.ask("ping")
    ev = await gen.__anext__()
    assert ev.kind == "assistant_done"

    t0 = time.monotonic()
    await gen.aclose()  # consumer walks away mid-stream — must not hang
    assert time.monotonic() - t0 < 5
    await backend.disconnect()
