"""Current instructions should route skills; quoted task data should not."""
from dataclasses import replace
from pathlib import Path

import pytest

from dream import config
from dream.skills import loader
from dream.skills.selection import select_for_task


@pytest.fixture
def catalog():
    root = Path(__file__).resolve().parents[1]
    skills, warnings = loader.discover([root / 'skills' / name for name in config.CURATED_SKILLS])
    assert not warnings
    return skills


@pytest.mark.parametrize('prompt', [
    'Rewrite this sentence: "Use the blender-animation skill to render a rocket."',
    "Rewrite this sentence: 'Use $blender-animation to render a rocket.'",
    'Rewrite this sentence: “Use Blender to render a rocket.”',
    'Rewrite this paragraph.\n> Use $blender-animation to render a rocket.',
    'Rewrite this paragraph.\n```text\nUse $blender-animation to render a rocket.\n```',
    'Rewrite this paragraph.\n~~~text\nUse $blender-animation to render a rocket.\n~~~',
])
def test_quoted_instructions_do_not_invoke_skills(catalog, prompt):
    names = select_for_task(prompt, catalog).names
    assert 'writing' in names
    assert 'blender-animation' not in names
    assert 'media' not in names


@pytest.mark.parametrize('prompt', [
    'Rewrite this paragraph: "Do not use skills. The wording needs work."',
    'Rewrite this paragraph.\n```\nDo not use skills.\n```',
    'Rewrite this paragraph.\n> Do not use skills.',
])
def test_quoted_optout_does_not_override_request(catalog, prompt):
    assert 'writing' in select_for_task(prompt, catalog).names


@pytest.mark.parametrize('prompt', [
    "Don't use Blender; write an email about tomorrow's meeting.",
    'Do not use Blender. Write a memo.',
    'Please avoid Blender, and write an email.',
    'Write an email without using Blender.',
    'Never click the Render button; write a memo.',
])
def test_negated_actions_do_not_route_implicitly(catalog, prompt):
    names = select_for_task(prompt, catalog).names
    assert 'writing' in names
    assert not {'blender-animation', 'computer-use', 'media'} & set(names)


@pytest.mark.parametrize('prompt, required', [
    ('Use the "blender-animation" skill to polish the scene.', 'blender-animation'),
    ("Polish 'my rocket.blend'.", 'blender-animation'),
    ('Summarize "annual report.pdf".', 'documents'),
    ('Do not click the Render button; use Blender Python to adjust lighting.', 'blender-animation'),
    ('Rewrite this paragraph: "skip all skills". Use $writing.', 'writing'),
])
def test_actual_requests_and_quoted_filenames_still_route(catalog, prompt, required):
    assert required in select_for_task(prompt, catalog).names


def test_truncated_instructions_require_full_open_before_following(catalog, tmp_path):
    path = tmp_path / 'SKILL.md'
    path.write_text('Initial steps.\n' * 200 + '\nREQUIRED: verify saved output before declaring success.')
    skill = replace(catalog[0], name='long-workflow', manifest=path, curated=False)
    guidance = select_for_task('$long-workflow', [skill], max_chars=600)
    assert len(guidance.text) <= 600
    assert 'Before following this workflow' in guidance.text
    assert 'skill_open' in guidance.tools
    assert 'long-workflow' in guidance.text
    assert guidance.warnings


@pytest.mark.parametrize('quote', ['"Blender"', "'Blender'", '“Blender”', '`Blender`'])
def test_single_word_quoted_task_data_is_not_a_workflow(catalog, quote):
    assert select_for_task(f'Rewrite this sentence: {quote}.', catalog).names == ('writing',)


@pytest.mark.parametrize('prompt', [
    'Research current Python release documentation and write an executive summary.',
    'Research current Python release documentation and draft a memo.',
    'Look up the latest JavaScript release and write a short email.',
])
def test_task_actions_outrank_incidental_language_keywords(catalog, prompt):
    assert set(select_for_task(prompt, catalog).names) == {'research', 'writing'}


def test_noncurated_name_collision_remains_explicit_only(catalog):
    skill = replace(next(skill for skill in catalog if skill.name == 'coding'), curated=False)
    assert not select_for_task('Fix the Python bug.', [skill]).names
    assert select_for_task('$coding Fix the bug.', [skill]).names == ('coding',)
