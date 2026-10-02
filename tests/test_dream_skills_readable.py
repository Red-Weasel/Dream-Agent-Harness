"""run_bash's sandbox shows Dream's own skills folder READ-ONLY as `$DREAM_SKILLS` (the owner's decision, 2026-10-01).

Live 2026-10-01 15:28 (workspace "00 -- jarvis bots"): the understand skill told the model to copy its bundled glue.py
into the workspace. skill_file returned the 98 KB file shortened ("[... 73921 chars elided]"), so write_file saved a
broken file; run_bash could not see Dream's skills folder, so `cat` gave nothing and its redirect left a 0-byte file.
The owner's rule stays: the shell writes only inside the workspace. It may now READ Dream's skills, so a skill copies
or runs its bundled files with the shell, whole.
"""
from __future__ import annotations

import asyncio
import hashlib
import shutil
from pathlib import Path

import pytest

import dream.config as config
from dream.core import skill_runtime

SKILLS = (config.ROOT / "skills").resolve()
GLUE = SKILLS / "understand" / "glue.py"


def _sh(command: str, workspace: Path):
    """One run_bash command as the model's run_bash runs it: the policy in auto mode, then the real sandbox."""
    from dream.core.execution import ExecutionContext, ExecutionScope, execute_bash
    result, contained = asyncio.run(execute_bash(command, ExecutionContext(ExecutionScope(Path(workspace)), mode="auto")))
    assert contained
    return result


def _needs_sandbox():
    if shutil.which("bwrap") is None or not GLUE.is_file():
        pytest.skip("needs bubblewrap and Dream's understand skill")


def test_dreams_skills_folder_is_a_read_only_script_root():
    assert SKILLS.is_dir()
    assert SKILLS in skill_runtime.script_roots()
    assert skill_runtime.script_env()["DREAM_SKILLS"] == str(SKILLS)


def test_the_shell_reads_a_bundled_skill_file_whole_and_copies_it_byte_for_byte(tmp_path):
    _needs_sandbox()
    size = _sh('wc -c < "$DREAM_SKILLS/understand/glue.py"', tmp_path)
    assert size.returncode == 0 and int(size.output.decode().strip()) == GLUE.stat().st_size > 50_000
    copy = _sh('mkdir -p .ua/tmp && cp "$DREAM_SKILLS/understand/glue.py" .ua/tmp/ua_glue.py', tmp_path)
    assert copy.returncode == 0, copy.output
    copied = tmp_path / ".ua" / "tmp" / "ua_glue.py"
    assert hashlib.sha256(copied.read_bytes()).digest() == hashlib.sha256(GLUE.read_bytes()).digest()


def test_the_shell_cannot_write_into_dreams_skills(tmp_path):
    _needs_sandbox()
    before = sorted(p.name for p in SKILLS.iterdir())
    result = _sh('touch "$DREAM_SKILLS/written-by-the-shell" || echo REFUSED', tmp_path)
    assert b"REFUSED" in result.output
    assert sorted(p.name for p in SKILLS.iterdir()) == before


def test_a_skills_folder_inside_the_workspace_stays_writable(tmp_path, monkeypatch):
    """Working on Dream itself: its skills folder is part of the workspace, writable as before -- the read-only mount
    of a script root must not cover a folder inside the workspace."""
    _needs_sandbox()
    inner = tmp_path / "skills"
    (inner / "understand").mkdir(parents=True)
    monkeypatch.setattr(skill_runtime, "_dream_skill_roots", lambda: (inner.resolve(),))
    result = _sh("touch skills/understand/edited-by-the-shell && echo WROTE", tmp_path)
    assert b"WROTE" in result.output, result.output
    assert (inner / "understand" / "edited-by-the-shell").exists()


@pytest.mark.parametrize("skill", ["understand", "understand-domain"])
def test_the_glue_fallback_copies_the_real_file_with_the_shell(skill):
    text = (SKILLS / skill / "SKILL.md").read_text()
    assert 'cp "$DREAM_SKILLS/understand/glue.py" .ua/tmp/ua_glue.py' in text
    assert "write_file its text" not in text            # never rebuilt from skill_file's shortened text
