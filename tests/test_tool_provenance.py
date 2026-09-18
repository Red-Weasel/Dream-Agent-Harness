"""Self-built tools persist WITH provenance: age, usage, and a STALE tag that tells
future-me not to reuse a tool merely because it exists."""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import dream.config as config
from dream import extensions
from dream.memory.store import MemoryStore
from dream.tools import registry
from dream.tools.registry import staleness_note

NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)


def _store() -> MemoryStore:
    return MemoryStore(Path(tempfile.mkdtemp()) / "t.db")


def test_tool_seen_bump_and_stat():
    s = _store()
    s.note_tool_seen("preview_tui", refreshed_at="2026-07-01T00:00:00+00:00")
    s.bump_tool_use("preview_tui")
    s.bump_tool_use("preview_tui")
    st = s.tool_stat("preview_tui")
    assert st["first_seen"] == "2026-07-01T00:00:00+00:00"
    assert st["use_count"] == 2 and st["last_used"] is not None


def test_rewriting_a_tool_resets_its_age():
    s = _store()
    s.note_tool_seen("old_tool", refreshed_at="2026-01-01T00:00:00+00:00")
    # The file was edited months later — a refreshed tool embodies current judgment.
    s.note_tool_seen("old_tool", refreshed_at="2026-07-01T00:00:00+00:00")
    assert s.tool_stat("old_tool")["first_seen"] == "2026-07-01T00:00:00+00:00"
    # An older mtime never regresses the recorded freshness.
    s.note_tool_seen("old_tool", refreshed_at="2025-01-01T00:00:00+00:00")
    assert s.tool_stat("old_tool")["first_seen"] == "2026-07-01T00:00:00+00:00"


def test_staleness_note_fresh_and_stale():
    fresh = {"first_seen": "2026-07-01T00:00:00+00:00",
             "last_used": "2026-07-03T00:00:00+00:00", "use_count": 14}
    n = staleness_note(fresh, stale_days=30, now=NOW)
    assert n == "[self-built 2026-07-01 · used 14× · last 1d ago]"

    stale = {"first_seen": "2026-01-04T00:00:00+00:00",
             "last_used": "2026-02-01T00:00:00+00:00", "use_count": 9}
    n = staleness_note(stale, stale_days=30, now=NOW)
    assert "STALE" in n and "unused in 153d" in n

    never = {"first_seen": "2026-01-04T00:00:00+00:00", "last_used": None, "use_count": 0}
    n = staleness_note(never, stale_days=30, now=NOW)
    assert "STALE" in n and "never used" in n


def test_registry_annotates_custom_tools(tmp_path, monkeypatch):
    (tmp_path / "mytool.py").write_text(
        "from claude_agent_sdk import tool\n"
        "from typing import Any\n"
        "@tool('mytool', 'Does a thing.', {'type': 'object', 'properties': {}})\n"
        "async def mytool(args: dict[str, Any]):\n"
        "    return {'content': [{'type': 'text', 'text': 'done'}]}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CUSTOM_TOOLS_DIR", tmp_path)
    # Approval is explicit and limited to the synthetic module under test.
    review = extensions.review_module("tool:custom/mytool")
    assert review["source"] == (tmp_path / "mytool.py").read_text()
    extensions.trust_module(review["id"], review["sha256"])
    built = registry.build(annotate=lambda name, path: "[self-built TEST-TAG]")
    assert "mytool" in built["custom_names"]
    desc = next(t.description for t in built["tools"] if t.name == "mytool")
    assert desc.count("TEST-TAG") == 1 and desc.startswith("Does a thing.")
    # A second build must not stack tags.
    built = registry.build(annotate=lambda name, path: "[self-built TEST-TAG]")
    desc = next(t.description for t in built["tools"] if t.name == "mytool")
    assert desc.count("TEST-TAG") == 1


def test_preview_tui_renders_svg(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SCREENSHOT_DIR", tmp_path)
    from dream.tools.custom.preview_tui import _render

    svg, _png = _render("stats", 80)  # png depends on chrome; svg must always exist
    assert Path(svg).exists() and Path(svg).stat().st_size > 0
