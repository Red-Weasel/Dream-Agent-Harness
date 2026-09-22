"""Process skills ported from the Claude Code workflows the owner uses most (2026-09-21
usage census: brainstorming, systematic debugging, the alpha-omega gated build loop,
frontend design). They ship as curated Dream skills and select implicitly."""
from __future__ import annotations
import pytest
import dream.config as config
from dream.skills import loader
from dream.skills.selection import select_for_task

NEW = {"brainstorming", "debugging", "gated-build", "frontend-design"}


@pytest.fixture(scope="module")
def curated():
    skills, warnings = loader.discover(config.bundled_skill_dirs())
    assert not warnings, warnings
    return skills


def test_the_four_process_skills_ship_as_curated_packages(curated):
    by_name = {s.name: s for s in curated}
    assert NEW <= set(by_name)
    for name in NEW:
        assert by_name[name].curated, name
        assert by_name[name].description


@pytest.mark.parametrize("prompt,skill", [
    ("Let's design how the export feature should work before we build it — what approach do you recommend?", "brainstorming"),
    ("Brainstorm a plan for the new onboarding flow with me", "brainstorming"),
    ("The nightly job fails intermittently; find the root cause before changing anything", "debugging"),
    ("Debug why this test fails only on the second run", "debugging"),
    ("Run this as a gated build: phases with a fresh verifier at each checkpoint", "gated-build"),
    ("Run an alpha omega loop on the importer rewrite", "gated-build"),
    ("Design a distinctive landing page with strong typography and a real visual identity", "frontend-design"),
])
def test_process_skills_select_implicitly(curated, prompt, skill):
    guidance = select_for_task(prompt, curated)
    assert skill in guidance.names, guidance.names
    assert len(guidance.names) <= 2


@pytest.mark.parametrize("prompt", ["Hello!", "What is the capital of France?", "Thanks, that helped."])
def test_small_talk_still_selects_nothing(curated, prompt):
    assert not select_for_task(prompt, curated).names
