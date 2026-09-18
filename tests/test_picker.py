"""The boot picker: provider detection + rendering. The interactive loop is
smoke-tested via pty; here we pin the pure status logic and that rendering runs."""

from __future__ import annotations

import io

from rich.console import Console

from dream.tui import picker


def test_provider_rows_structure_when_nothing_installed(monkeypatch):
    monkeypatch.setattr(picker.shutil, "which", lambda name: None)
    rows = picker.detect_providers()
    assert [r.key for r in rows] == ["anthropic", "codex", "grok", "gemini", "moe"]
    by = {r.key: r for r in rows}
    assert by["anthropic"].ready is False and by["anthropic"].status == "CLI not found"
    assert by["codex"].status == "not installed" and by["codex"].note == "Phase 2"
    assert by["grok"].status == "not installed"
    assert by["gemini"].status == "not installed"
    # With nothing signed in, Dream MoE can't form a council.
    assert by["moe"].ready is False and "needs an available provider" in by["moe"].status
    # Only Claude is ever selectable in Phase 1.
    assert sum(r.ready for r in rows) == 0  # (nothing installed here)


def test_claude_is_ready_when_its_cli_is_present(monkeypatch):
    monkeypatch.setattr(picker.shutil, "which", lambda name: "/bin/claude" if name == "claude" else None)
    by = {r.key: r for r in picker.detect_providers()}
    assert by["anthropic"].ready is True and by["anthropic"].status == "signed in"
    # Frontier CLIs remain gated to Phase 2 regardless of install state.
    assert by["codex"].ready is False and by["grok"].ready is False


def test_render_picker_runs_without_models(monkeypatch):
    monkeypatch.setattr(picker.shutil, "which", lambda name: None)
    console = Console(file=io.StringIO(), force_terminal=False, width=80)
    picker.render_picker(console, picker.detect_providers(), [])
    out = console.file.getvalue()
    assert "Claude" in out and "Dream MoE" in out
