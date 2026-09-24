"""DREAM-101: a selected skill drives the turn -- the whole workflow when the window allows -- and skill_open tells the
truth about a directory the run_bash sandbox cannot reach.

Why: live MiMo sessions running the Understand map got a 2,200-char summary of a 36,100-char skill plus "call skill_open
for the rest", then a header naming a directory `ls` could not see, and spent turns re-deriving their environment.
"""
from pathlib import Path

import pytest

from dream.skills import loader
from dream.skills.selection import (MAX_GUIDANCE_CHARS, guidance_budget, select_for_task)
from dream.tools.installed_skill_tools import directory_line

BIG = "\n".join(f"Step {i}: do the {i}th thing carefully, then check it." for i in range(1, 700))   # ~36k chars
SMALL = "\n".join(f"Small step {i}." for i in range(1, 300))                                          # ~5k chars


def make_skill(root: Path, name: str, text: str) -> None:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Useful {name} workflow.\n---\n\n{text}")


@pytest.fixture
def skills(tmp_path, monkeypatch):
    make_skill(tmp_path, "bigmap", BIG)
    make_skill(tmp_path, "tiny", SMALL)
    found, warnings = loader.discover([tmp_path])
    assert not warnings and {s.name for s in found} == {"bigmap", "tiny"}
    monkeypatch.setattr(loader, "enabled", lambda skill: True)       # opt-in is not what is under test
    return found


def test_the_budget_follows_the_window():
    assert guidance_budget(None) == MAX_GUIDANCE_CHARS
    assert guidance_budget(0) == MAX_GUIDANCE_CHARS
    assert guidance_budget(4096) == MAX_GUIDANCE_CHARS            # 15 % of 4k tokens is under the default: the floor holds
    assert guidance_budget(65536) == int(65536 * 4 * 0.15)        # 39,321 chars: a 36k-char skill fits whole
    assert guidance_budget(200_000) == loader.BODY_MAX_CHARS      # capped at the loader's body limit


def test_a_large_window_gets_the_whole_workflow(skills):
    guidance = select_for_task("$bigmap map this repository", skills, max_chars=guidance_budget(200_000))
    assert guidance.names == ("bigmap",)
    assert "Step 699: do the 699th thing carefully" in guidance.text      # the last line of the body made it in
    assert "[Workflow shortened." not in guidance.text
    assert "skill_open" not in guidance.tools and not guidance.warnings


def test_the_default_budget_still_shortens_as_before(skills):
    guidance = select_for_task("$bigmap map this repository", skills)
    assert guidance.names == ("bigmap",)
    assert len(guidance.text) <= MAX_GUIDANCE_CHARS
    assert "[Workflow shortened." in guidance.text
    assert guidance.tools[0] == "skill_open" and any("shortened" in w for w in guidance.warnings)


def test_a_second_skill_keeps_the_short_room(skills):
    guidance = select_for_task("$bigmap $tiny map it and note it", skills, max_chars=guidance_budget(200_000))
    assert guidance.names == ("bigmap", "tiny")
    assert "Step 699:" in guidance.text                            # the first skill is whole
    tiny = guidance.text.split("### tiny", 1)[1]
    assert len(tiny) <= 2200 + 20 and "[Workflow shortened." in tiny  # the second is the summary


def test_the_directory_line_is_honest_about_the_sandbox(skills, tmp_path):
    big = next(s for s in skills if s.name == "bigmap")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    line = directory_line(big, workspace)
    assert str(big.root.resolve()) in line
    assert "run_bash cannot see it" in line and 'skill_file(name="bigmap", path=' in line and "Do not try to cd or ls" in line
    assert "ua_run" not in line                                    # not an understand-anything skill
    # inside the workspace: the plain line, as before
    assert directory_line(big, tmp_path) == f"Directory: {big.root.resolve()}"
    # no live session: the plain line
    assert directory_line(big, None) == f"Directory: {big.root.resolve()}"


def test_the_understand_skill_names_its_runner(tmp_path, monkeypatch):
    make_skill(tmp_path / "understand-anything-plugin" / "skills", "understand", "Phase 0: scan.")
    found, _ = loader.discover([tmp_path / "understand-anything-plugin" / "skills"])
    line = directory_line(found[0], tmp_path / "elsewhere")
    assert "ua_run" in line and "run_bash cannot see it" in line


def test_the_bundled_file_hint_uses_the_tools_real_parameter(skills, tmp_path):
    """skill_file takes `name` + `path`; the hint must name the parameter the model has to send."""
    from dream.tools.installed_skill_tools import _FILE_SCHEMA
    assert "path" in _FILE_SCHEMA["properties"] and "file" not in _FILE_SCHEMA["properties"]
    big = next(s for s in skills if s.name == "bigmap")
    line = directory_line(big, tmp_path / "workspace")
    assert 'skill_file(name="bigmap", path=' in line and ", file=" not in line


def test_a_skill_merely_containing_understand_is_not_given_the_runner_hint(tmp_path):
    make_skill(tmp_path / "misunderstandings" / "skills", "notes", "Write notes.")
    found, _ = loader.discover([tmp_path / "misunderstandings" / "skills"])
    assert "ua_run" not in directory_line(found[0], tmp_path / "elsewhere")
