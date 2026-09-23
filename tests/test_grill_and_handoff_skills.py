"""DREAM-090: grill-me and handoff, Dream-native process skills adapted from Matt Pocock's skills (MIT).

Owner, 2026-09-23: add the aihero.dev skills to Claude Code, then design similar skills for Dream -- definitely grill-me
and handoff. They ship as curated skills: selected by $name or by what the owner types, packaged in the wheel, and
small enough to load unshortened.
"""
from __future__ import annotations

import json
import tomllib

import pytest

import dream.config as config
from dream.skills import loader
from dream.skills.selection import select_for_task

NEW = ("grill-me", "handoff")


@pytest.fixture(scope="module")
def curated():
    skills, warnings = loader.discover(config.bundled_skill_dirs())
    assert not warnings, warnings
    return skills


def test_both_ship_as_curated_packages_and_credit_the_source(curated):
    by_name = {s.name: s for s in curated}
    for name in NEW:
        assert name in config.CURATED_SKILLS
        skill = by_name[name]
        assert skill.curated and skill.description.startswith("Use when")
        manifest = json.loads((skill.root / "manifest.json").read_text())
        assert manifest["format"] == "dream-skill/v1" and manifest["name"] == name
        assert "github.com/mattpocock/skills" in skill.manifest.read_text()   # credit for the idea
    wheel = tomllib.loads((config.ROOT / "pyproject.toml").read_text())["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert {f"skills/{name}" for name in NEW} <= set(wheel["only-include"])


@pytest.mark.parametrize("prompt,skill", [
    ("Grill me on my plan to move session storage to Redis before the launch", "grill-me"),
    ("grill my design for the importer rewrite", "grill-me"),
    ("Stress-test my plan for the billing migration before I commit to it", "grill-me"),
    ("Poke holes in this proposal: we drop the cache and read straight from Postgres", "grill-me"),
    ("$grill-me", "grill-me"),
    ("Write a handoff so a fresh session can pick this up", "handoff"),
    ("Hand this conversation off to a new agent; next it will finish the failing tests", "handoff"),
    ("Create a handoff doc for the next session", "handoff"),
    ("$handoff the next session fixes the parser", "handoff"),
])
def test_selected_by_what_the_owner_types(curated, prompt, skill):
    guidance = select_for_task(prompt, curated)
    assert skill in guidance.names, guidance.names


@pytest.mark.parametrize("prompt", [
    "How long should I grill salmon on each side?",
    "What is a good marinade for grilled chicken?",
    "Our sales-to-support handoff process is slow; draft an email to the VP about fixing it",
    "Thanks, that helped.",
])
def test_ordinary_words_do_not_select_them(curated, prompt):
    names = select_for_task(prompt, curated).names
    assert not set(NEW) & set(names), names


def test_grilling_displaces_brainstorming(curated):
    # brainstorming asks one question per message; grill-me asks the whole frontier per round
    guidance = select_for_task("Grill me on my plan before we build the importer", curated)
    assert "grill-me" in guidance.names and "brainstorming" not in guidance.names


@pytest.mark.parametrize("name", NEW)
def test_each_loads_whole_not_shortened(curated, name):
    guidance = select_for_task(f"${name}", curated)
    assert guidance.names == (name,)
    assert "[Workflow shortened." not in guidance.text and not guidance.warnings
