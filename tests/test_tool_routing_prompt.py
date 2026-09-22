"""The model has to be TOLD which tools skip approval and which edit path to use:
on 2026-09-21 it read every file through run_bash (gated) and edited with sed -i."""
from __future__ import annotations
import dream.config as config
from dream.core.system_prompt import build_system_prompt
from dream.memory.store import MemoryStore
from dream.tools import native
from dream.tools.files import FILE_TOOLS


def _prompt(tmp_path, monkeypatch):
    for name in ("INSTRUCTIONS_FILE", "IDENTITY_FILE", "THREADS_FILE"):
        monkeypatch.setattr(config, name, tmp_path / f"{name}.md")
    return build_system_prompt(MemoryStore(tmp_path / "dream.db"), "sess-routing")


def test_prompt_names_the_ungated_read_tools(tmp_path, monkeypatch):
    prompt = _prompt(tmp_path, monkeypatch)
    section = prompt[prompt.index("## Files and shell"):]
    for tool in ("read_file", "list_dir", "grep"):
        assert tool in section
    assert "without approval" in section
    assert "run_bash" in section and "asks" in section


def test_prompt_forbids_shell_edits(tmp_path, monkeypatch):
    section = _prompt(tmp_path, monkeypatch).split("## Files and shell", 1)[1]
    assert "str_replace_edit" in section and "sed -i" in section and "heredoc" in section


def test_tool_descriptions_carry_the_approval_note():
    grep_tool = next(t for t in FILE_TOOLS if t.name == "grep")
    for t in (native.read_file, native.list_dir, grep_tool):
        assert "no approval" in t.description.lower(), t.name
    assert "asks for approval" in native.run_bash.description and "read_file" in native.run_bash.description
