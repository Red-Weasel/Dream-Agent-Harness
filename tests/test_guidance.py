"""Guidance a local model actually needs, and the tool feedback that feeds it.

A frontier model improvises around a bad tool result. A small local one does not — it
repeats. So the guidance and the tool output are load-bearing here in a way they
aren't on a hosted model, and both are pinned.

Every case below traces to something that really happened on this machine, not to a
hypothesis about what a model might do.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import dream.config as config
from dream.core import system_prompt
from dream.memory.store import MemoryStore
from dream.tools.native import _describe_run


@pytest.fixture(autouse=True)
def _packaged_skills(monkeypatch):
    """Guidance checks must not depend on the developer's external skill installs."""
    from dream import plugins
    from dream.tools import installed_skill_tools

    monkeypatch.setenv("DREAM_SKILL_DIRS", str(Path(__file__).resolve().parents[1] / "skills"))
    monkeypatch.setattr(plugins, "_LOADED", [])
    monkeypatch.setattr(installed_skill_tools, "_CACHE", None)
    monkeypatch.setattr(installed_skill_tools, "_WARNINGS", [])


def _prompt(tmp_path):
    return system_prompt.build_system_prompt(MemoryStore(tmp_path / "t.db"), "s")


# --- run_bash explains its own silence ---------------------------------------


def test_an_empty_result_says_it_is_empty():
    """`(exit 0)` followed by a blank line is indistinguishable from success."""
    out = _describe_run("grep nothing file", 0, "")
    assert "no output" in out


def test_an_empty_result_forbids_the_identical_retry():
    """The exact loop that cost a turn: five identical calls on an empty result."""
    out = _describe_run("grep nothing file", 0, "")
    assert "Do NOT repeat this exact command" in out


def test_suppressed_stderr_is_named_as_the_reason():
    """The RavenX case: `cat /etc/firmware 2>/dev/null` on a path that doesn't exist.
    The model had no way to see that an error had been thrown away."""
    out = _describe_run("cat /etc/firmware 2>/dev/null", 0, "")
    assert "2>/dev/null" in out
    assert "thrown away" in out or "discarded" in out


def test_suppression_is_detected_with_odd_spacing():
    assert "thrown away" in _describe_run("cat x 2> /dev/null", 0, "")
    assert "thrown away" in _describe_run("cat x 2>&-", 0, "")


def test_a_clean_empty_result_does_not_blame_a_redirect_that_is_not_there():
    out = _describe_run("grep nothing file", 0, "")
    assert "2>/dev/null" not in out


def test_a_failure_with_output_is_marked_as_a_failure():
    out = _describe_run("ls /nope", 2, "ls: cannot access '/nope'")
    assert "FAILED" in out
    assert "cannot access" in out


def test_a_failure_tells_the_model_not_to_retry_unchanged():
    out = _describe_run("ls /nope", 2, "ls: cannot access '/nope'")
    assert "fail the same way" in out


def test_a_silent_failure_points_at_the_discarded_stream():
    out = _describe_run("somecmd", 3, "")
    assert "exit 3" in out and "discarded" in out


def test_a_normal_result_is_left_alone():
    """The happy path must not grow a lecture — it is the common case."""
    out = _describe_run("echo hi", 0, "hi")
    assert out == "(exit 0)\nhi"


def test_the_tool_description_warns_against_suppressing_errors():
    from dream.tools.native import run_bash

    assert "2>/dev/null" in run_bash.description


# --- the system prompt carries the playbook ----------------------------------


def test_the_prompt_has_a_recovery_section(tmp_path):
    assert "## When something doesn't work" in _prompt(tmp_path)


def test_the_prompt_forbids_the_identical_retry(tmp_path):
    p = _prompt(tmp_path)
    assert "identical call" in p


def test_the_prompt_asks_for_partial_findings_over_silence(tmp_path):
    """The behaviour salvage depends on: a cut-short turn should still report."""
    p = _prompt(tmp_path)
    assert "partial answer" in p.lower()
    assert "tools removed" in p


def test_the_prompt_forbids_inventing_results(tmp_path):
    p = _prompt(tmp_path)
    assert "Never invent" in p
    assert "you don't have it" in p


def test_the_prompt_routes_between_the_two_kinds_of_skill(tmp_path):
    """Preserve imported packages by default while allowing requested maintenance."""
    p = _prompt(tmp_path)
    assert "skill_open" in p and "skill_save" in p
    assert "Preserve imported packages unless the user's task calls for changing them" in p
    assert "cannot patch them" not in p


def test_the_prompt_states_the_library_replace_rule(tmp_path):
    """The one Library mistake that costs real work."""
    p = _prompt(tmp_path)
    assert "library_replace" in p
    assert "SAME id" in p


def test_the_prompt_lists_every_subagent(tmp_path):
    p = _prompt(tmp_path)
    for name in ("researcher", "coder", "explorer", "reviewer", "debugger", "librarian"):
        assert name in p


def test_the_prompt_defers_to_the_engine_on_whether_subagents_are_serial(tmp_path):
    """Phase 13 (Gate 13, observation 1): subagents are serial on the local
    single-flight engine and concurrent on openai/xai, so the always-on BASE tier
    must not claim either — it points at the per-session delegation section the
    Engine appends, which says which one this session got."""
    p = _prompt(tmp_path)
    assert "one at a time" not in p
    assert "depends" in p and "the one to believe" in p


def test_the_prompt_stays_affordable(tmp_path, monkeypatch):
    """It is paid on every request, and a local window is small. This is a budget,
    not a style preference — raise it deliberately or not at all."""
    from dream.skills.loader import FileSkill
    from dream.tools import installed_skill_tools

    skills = [FileSkill(name=f"external-skill-{i:03d}", description="Useful detailed workflow " * 30,
                        root=tmp_path, manifest=tmp_path / "SKILL.md", source="test")
              for i in range(250)]
    monkeypatch.setattr(installed_skill_tools, "_CACHE", skills)
    prompt = _prompt(tmp_path)
    assert len(prompt) < 26_000
    assert not any(skill.name in prompt for skill in skills)
    assert len(installed_skill_tools.index_lines()) == 0
    assert len(installed_skill_tools.installed()) == 250


# --- the recovery skill is installed and findable ----------------------------


def test_the_verification_recovery_workflow_is_installed():
    from dream.skills import loader

    skills, warnings = loader.discover(config.skill_dirs())
    by_name = {s.name: s for s in skills}
    assert "verifying" in by_name, "the recovery playbook is not installed"
    assert not warnings


def test_the_verification_recovery_workflow_is_findable():
    """A skill nothing routes to is a file, not a skill."""
    from dream.skills import loader

    skills, _ = loader.discover(config.skill_dirs())
    for query in ("stuck", "command failed", "interrupted"):
        names = [s.name for s in loader.find(skills, query)]
        assert "verifying" in names, f"not found by {query!r}"


def test_the_library_skill_is_found_by_library_words():
    from dream.skills import loader

    skills, _ = loader.discover(config.skill_dirs())
    assert "library" in [s.name for s in loader.find(skills, "library file")]


# --- malformed tool-call arguments -------------------------------------------


def test_valid_arguments_parse_with_no_complaint():
    from dream.core.backends.openai_compat import _parse_tool_args

    assert _parse_tool_args('{"command": "ls"}') == ({"command": "ls"}, None)


def test_absent_arguments_are_not_an_error():
    from dream.core.backends.openai_compat import _parse_tool_args

    assert _parse_tool_args("") == ({}, None)
    assert _parse_tool_args(None) == ({}, None)


def test_truncated_json_is_reported_rather_than_silently_emptied():
    """Substituting {} makes the tool report a MISSING argument the model actually
    sent — a true sentence about a false situation, which invites an identical retry."""
    from dream.core.backends.openai_compat import _parse_tool_args

    args, err = _parse_tool_args('{"command": "ls -la /somewhere')
    assert args == {}
    assert err is not None
    assert "not valid JSON" in err
    assert "Nothing ran" in err


def test_the_model_is_shown_its_own_broken_output():
    """Seeing the broken text is what makes the next attempt differ from the last."""
    from dream.core.backends.openai_compat import _parse_tool_args

    _, err = _parse_tool_args('{"command": "ls -la /somewhere')
    assert '{"command": "ls -la /somewhere' in err


def test_a_truncation_hint_points_at_over_long_arguments():
    from dream.core.backends.openai_compat import _parse_tool_args

    _, err = _parse_tool_args('{"content": "aaaa')
    assert "cut off" in err and "too long" in err


def test_json_that_is_not_an_object_is_refused():
    from dream.core.backends.openai_compat import _parse_tool_args

    args, err = _parse_tool_args('"just a string"')
    assert args == {} and "must be a JSON object" in err


def test_a_huge_broken_payload_is_clipped_in_the_message():
    """The echo is for recognition, not for re-reading a megabyte of it."""
    from dream.core.backends.openai_compat import _parse_tool_args

    _, err = _parse_tool_args('{"x": "' + "y" * 50_000)
    assert len(err) < 2_000


def test_the_verifying_skill_is_installed_and_findable():
    from dream.skills import loader

    skills, warnings = loader.discover(config.skill_dirs())
    assert not warnings
    assert "verifying" in [s.name for s in skills]
    for query in ("verify", "outcomes", "recover"):
        assert "verifying" in [s.name for s in loader.find(skills, query)], query


def test_the_checkpoint_tools_are_reachable_by_the_model():
    """They were built and tested but registered nowhere — only the test suite could
    reach them, so Dream could never offer its own undo."""
    from dream.core import policy
    from dream.tools import registry

    names = set(registry.build()["names"])
    assert {"checkpoint_list", "checkpoint_restore"} <= names
    assert policy.capability("checkpoint_list") == policy.READONLY
    # restore rewrites files and deletes ones created since the snapshot — gated.
    assert policy.capability("checkpoint_restore") not in policy.AUTO_CAPS


def test_the_prompt_tells_the_model_undo_exists(tmp_path):
    p = _prompt(tmp_path)
    assert "checkpoint_restore" in p
