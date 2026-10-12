"""The boot picker: provider detection + rendering. The interactive loop is
smoke-tested via pty; here we pin the pure status logic and that rendering runs.

DREAM-246: a row reports installation only. Every test uses a temporary HOME and a
replaced PATH lookup, so no real install, sign-in or CLI configuration is looked at."""

from __future__ import annotations

import io
import shutil

import pytest
from rich.console import Console

from dream.tui import picker

CLI_ROWS = {"anthropic": "claude", "codex": "codex", "grok": "grok", "antigravity": "agy"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An empty HOME (and CODEX_HOME under it, so no real Codex home is read) with none of
    the provider CLIs on PATH."""
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("CODEX_HOME", str(root / ".codex"))
    monkeypatch.delenv("NVM_DIR", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    return root


def install(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    target.write_text("#!/bin/sh\nexit 0\n")
    target.chmod(0o755)
    return target


def rows_by_key():
    return {r.key: r for r in picker.detect_providers()}


def claims_sign_in(row) -> bool:
    text = f"{row.status} {row.note}".lower()
    return "signed in" in text or "phase" in text


def test_provider_rows_structure_when_nothing_installed(home):
    rows = picker.detect_providers()
    assert [r.key for r in rows] == ["anthropic", "codex", "grok", "antigravity", "moe"]
    by = {r.key: r for r in rows}
    for key in CLI_ROWS:
        assert by[key].ready is False and by[key].status == "not installed"
        assert "install" in by[key].note
    # With nothing installed, Dream MoE can't form a council.
    assert by["moe"].ready is False
    assert sum(r.ready for r in rows) == 0
    assert not any(claims_sign_in(r) for r in rows)


def test_installed_clis_are_selectable_without_any_sign_in_file(home):
    """A keyring sign-in leaves no auth.json or .credentials.json. An installed CLI is
    still offered, and the row says sign-in was not checked rather than guessing."""
    install(home / ".local" / "bin", "claude")
    install(home / ".nvm" / "versions" / "node" / "v20.11.1" / "bin", "codex")
    install(home / ".grok" / "bin", "grok")
    install(home / ".local" / "bin", "agy")
    by = rows_by_key()
    for key in CLI_ROWS:
        assert by[key].ready is True, key
        assert by[key].status.startswith("installed")
        assert "sign-in not checked" in by[key].note
    assert by["moe"].ready is True and by["moe"].status.startswith("4 ")
    assert not any(claims_sign_in(r) for r in by.values())


def test_a_sign_in_file_without_the_cli_makes_nothing_selectable(home):
    for folder, name in ((".codex", "auth.json"), (".grok", "auth.json"), (".claude", ".credentials.json")):
        (home / folder).mkdir()
        (home / folder / name).write_text('{"synthetic":"fixture-only"}')
    by = rows_by_key()
    assert not any(r.ready for r in by.values())
    assert all(by[key].status == "not installed" for key in CLI_ROWS)


def test_sign_in_files_do_not_change_an_installed_row(home):
    install(home / ".grok" / "bin", "grok")
    without = rows_by_key()["grok"]
    (home / ".grok" / "auth.json").write_text('{"synthetic":"fixture-only"}')
    assert rows_by_key()["grok"] == without
    assert without.ready is True and not claims_sign_in(without)


def test_a_cli_on_path_is_installed_not_signed_in(home, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "/bin/claude" if name == "claude" else None)
    by = rows_by_key()
    assert by["anthropic"].ready is True and by["anthropic"].status == "installed"
    assert not claims_sign_in(by["anthropic"])
    assert by["codex"].ready is False and by["grok"].ready is False and by["antigravity"].ready is False
    # The council count is installed providers, one here.
    assert by["moe"].ready is True and by["moe"].status.startswith("1 ")


def test_codex_model_is_a_display_detail_that_decides_nothing(home):
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text('model = "fixture-model"\n')
    assert rows_by_key()["codex"].ready is False            # configuration is not installation
    install(home / ".local" / "bin", "codex")
    row = rows_by_key()["codex"]
    assert row.ready is True and "fixture-model" in row.status
    assert not claims_sign_in(row)


def test_render_picker_runs_without_models(home):
    console = Console(file=io.StringIO(), force_terminal=False, width=80)
    picker.render_picker(console, picker.detect_providers(), [])
    out = console.file.getvalue()
    assert "Claude" in out and "Dream MoE" in out
    assert "signed in" not in out and "Phase" not in out
