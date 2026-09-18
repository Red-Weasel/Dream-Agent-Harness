"""Tests for the user's standing instructions: the file primitives and their injection
into the system prompt."""

from pathlib import Path

import dream.config as config
from dream.core import instructions
from dream.core.system_prompt import build_system_prompt
from dream.memory.store import MemoryStore


def _point_at(tmp_path: Path, monkeypatch) -> Path:
    p = tmp_path / "memory" / "INSTRUCTIONS.md"
    monkeypatch.setattr(config, "INSTRUCTIONS_FILE", p)
    return p


# --- primitives ---------------------------------------------------------------


def test_load_empty_when_unset(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch)
    assert instructions.load() == ""
    assert instructions.as_prompt_section() == ""


def test_save_load_round_trip(tmp_path, monkeypatch):
    p = _point_at(tmp_path, monkeypatch)
    instructions.save("Prefer terse answers. Use metric units.")
    assert p.exists()  # parent dir was created
    assert instructions.load() == "Prefer terse answers. Use metric units."


def test_save_overwrites_and_strips(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch)
    instructions.save("first")
    instructions.save("\n  second\n  ")
    assert instructions.load() == "second"


def test_clear_removes_file(tmp_path, monkeypatch):
    p = _point_at(tmp_path, monkeypatch)
    instructions.save("something")
    instructions.clear()
    assert not p.exists()
    assert instructions.load() == ""


def test_clear_is_noop_when_absent(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch)
    instructions.clear()  # must not raise
    assert instructions.load() == ""


def test_as_prompt_section_formatting(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch)
    instructions.save("Call me the user.")
    assert instructions.as_prompt_section() == "\n## The user's instructions\nCall me the user.\n"


# --- injection into the system prompt -----------------------------------------


def test_build_system_prompt_includes_section(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch)
    # keep the wake context hermetic — don't read the live memory/ files
    monkeypatch.setattr(config, "IDENTITY_FILE", tmp_path / "IDENTITY.md")
    monkeypatch.setattr(config, "THREADS_FILE", tmp_path / "THREADS.md")
    instructions.save("Never use emojis.")

    store = MemoryStore(tmp_path / "dream.db")
    prompt = build_system_prompt(store, "sess-1")

    assert "## The user's instructions" in prompt
    assert "Never use emojis." in prompt


def test_build_system_prompt_omits_section_when_unset(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "IDENTITY_FILE", tmp_path / "IDENTITY.md")
    monkeypatch.setattr(config, "THREADS_FILE", tmp_path / "THREADS.md")

    store = MemoryStore(tmp_path / "dream.db")
    prompt = build_system_prompt(store, "sess-1")

    assert "## The user's instructions" not in prompt


def test_system_prompt_names_memory_files_by_absolute_path(tmp_path, monkeypatch):
    # A session run from any workspace must be told the ABSOLUTE memory paths, or
    # the model resorts to `find ~` to locate IDENTITY.md/THREADS.md at consolidation.
    _point_at(tmp_path, monkeypatch)
    mem = tmp_path / "mem"
    monkeypatch.setattr(config, "MEMORY_DIR", mem)
    monkeypatch.setattr(config, "IDENTITY_FILE", mem / "IDENTITY.md")
    monkeypatch.setattr(config, "THREADS_FILE", mem / "THREADS.md")

    store = MemoryStore(tmp_path / "dream.db")
    prompt = build_system_prompt(store, "sess-1")

    assert str(mem / "THREADS.md") in prompt
    assert str(mem / "IDENTITY.md") in prompt
    # no unresolved template tokens leaked into the live prompt
    for token in ("__MEMORY_DIR__", "__IDENTITY_FILE__", "__THREADS_FILE__"):
        assert token not in prompt
