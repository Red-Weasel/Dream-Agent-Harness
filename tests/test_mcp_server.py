"""Module B — the stdio MCP server that bridges a CLI engine to Dream's memory tools.

These exercise the factored ``dispatch``/``build_context_from_env`` seam directly (no
stdio, no subprocess) so recall/remember run against a tmp DB and really hit the store.
"""

from __future__ import annotations

import pytest

import dream.config as config
from dream.mcp import server as mcp_server


@pytest.fixture
def dream_env(tmp_path, monkeypatch):
    """A tmp DB + a live session, wired through exactly the env the real server reads.

    ``remember`` also mirrors to markdown under ``config.*_DIR`` — redirect those at tmp
    so the test never touches the user's private ``memory/`` tree. Returns the built
    ``ToolContext``.
    """
    for name in ("SEMANTIC_DIR", "PROCEDURAL_DIR", "EPISODIC_DIR"):
        monkeypatch.setattr(config, name, tmp_path / name.lower())
    monkeypatch.setenv("DREAM_DB", str(tmp_path / "dream.db"))
    monkeypatch.setenv("DREAM_SESSION_ID", "sess-mcp-test")
    monkeypatch.setenv("DREAM_WORKSPACE", str(tmp_path))

    context = mcp_server.build_context_from_env()
    context.store.start_session("sess-mcp-test", "mcp test")
    return context


async def test_context_reads_env(dream_env):
    assert dream_env.session_id == "sess-mcp-test"
    assert str(dream_env.workspace).startswith("/")


async def test_remember_then_recall_persists(dream_env):
    context = dream_env

    saved = await mcp_server.dispatch(
        "remember",
        {
            "title": "MCP bridge fact",
            "body": "The Dream MCP server delegates recall and remember to tool handlers over stdio.",
        },
    )
    assert "Remembered" in saved

    # It really hit the store, not just formatted a string.
    assert context.store.all_memories(), "remember should have created a memory row"

    found = await mcp_server.dispatch("recall", {"query": "MCP server delegates recall"})
    assert "No memories found" not in found
    assert "MCP bridge fact" in found


async def test_unknown_tool_returns_clear_error(dream_env):
    out = await mcp_server.dispatch("definitely_not_a_tool", {})
    assert out.startswith("Error:")
    assert "unknown Dream tool" in out
    # It names the tools a caller can actually reach, so the error is actionable.
    assert "recall" in out and "remember" in out


async def test_note_roundtrips_through_working_memory(dream_env):
    noted = await mcp_server.dispatch("note", {"text": "check the sandbox mapping"})
    assert "noted" in noted.lower()

    back = await mcp_server.dispatch("read_notes", {})
    assert "check the sandbox mapping" in back


def test_build_server_exposes_the_expected_tools():
    # Constructing the server registers list_tools/call_tool without error, and the
    # exposed surface is exactly the mind-facing tools (browse/see deliberately absent).
    server = mcp_server.build_server()
    assert server is not None
    names = {t.name for t in mcp_server._EXPOSED}
    assert names == {
        "recall",
        "remember",
        "forget",
        "note",
        "read_notes",
        "recall_sessions",
        "web_search",
    }
    assert "browse" not in names and "see" not in names
