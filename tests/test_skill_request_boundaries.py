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


@pytest.fixture
def namespaced(catalog, tmp_path):
    """Two synthetic skills named the way a plugin's namespace alias is (plugin:skill). No plugin files:
    a curated FileSkill with its name and manifest replaced, explicit-only like any non-curated skill."""
    skills = []
    for name in ('plugin:skill', 'synthetic:workflow'):
        path = tmp_path / (name.replace(':', '_') + '.md')
        path.write_text(f'Synthetic steps for {name}.\n', encoding='utf-8')
        skills.append(replace(catalog[0], name=name, manifest=path, curated=False))
    return skills


@pytest.mark.parametrize('prompt, expected', [
    ('Use the "plugin:skill" skill to tidy the notes.', 'plugin:skill'),
    ("Use the 'plugin:skill' skill to tidy the notes.", 'plugin:skill'),
    ('Use the “plugin:skill” skill to tidy the notes.', 'plugin:skill'),
    ('Use the ‘plugin:skill’ skill to tidy the notes.', 'plugin:skill'),
    ('Use the `plugin:skill` skill to tidy the notes.', 'plugin:skill'),
    ('Use skill "synthetic:workflow" to tidy the notes.', 'synthetic:workflow'),
    ('Use skill “synthetic:workflow” to tidy the notes.', 'synthetic:workflow'),
    ('Use skill `synthetic:workflow` to tidy the notes.', 'synthetic:workflow'),
    ('Please run the installed skill "synthetic:workflow" on the notes.', 'synthetic:workflow'),
])
def test_a_quoted_namespaced_skill_name_is_an_explicit_request(namespaced, prompt, expected):
    guidance = select_for_task(prompt, namespaced)
    assert guidance.names == (expected,)
    assert guidance.matched == ((expected, 'explicit'),)
    assert f'### {expected}\nSynthetic steps for {expected}.' in guidance.text


@pytest.mark.parametrize('prompt', [
    # the quotation is a whole instruction: task data, whatever it names
    'Rewrite this sentence: "Use the plugin:skill skill to tidy the notes."',
    'Rewrite this sentence: "Use the `plugin:skill` skill to tidy the notes."',
    'Rewrite this sentence: “Use the ‘plugin:skill’ skill to tidy the notes.”',
    "Rewrite this sentence: 'Use $plugin:skill to tidy the notes.'",
    'Rewrite this sentence: `Use skill synthetic:workflow to tidy the notes.`',
    # a quoted name outside an invocation is task data too
    'Rename the heading "plugin:skill" in the notes.',
    # a quoted name with anything but name segments is not a skill argument
    'Use the "plugin:skill now" skill to tidy the notes.',
    'Use the "plugin: skill" skill to tidy the notes.',
    # a negated invocation selects nothing
    'Do not use the "plugin:skill" skill.',
    "Don't use the `plugin:skill` skill for this.",
    'Never use skill “synthetic:workflow” here.',
])
def test_quoted_instructions_and_negations_do_not_invoke_a_namespaced_skill(namespaced, prompt):
    assert select_for_task(prompt, namespaced).names == ()


def test_a_negated_namespaced_skill_leaves_the_one_asked_for(namespaced):
    prompt = 'Do not use the "plugin:skill" skill; use the "synthetic:workflow" skill instead.'
    assert select_for_task(prompt, namespaced).names == ('synthetic:workflow',)


@pytest.fixture
def colliding(catalog, tmp_path):
    """Three synthetic skills whose names are prefixes of one another at a colon. Each must be selectable
    alone: a request for one names neither its shorter prefix nor a longer name."""
    skills = []
    for name in ('plugin', 'plugin:skill', 'plugin:skill:child'):
        path = tmp_path / ('collide_' + name.replace(':', '_') + '.md')
        path.write_text(f'Collision steps for {name}.\n', encoding='utf-8')
        skills.append(replace(catalog[0], name=name, manifest=path, curated=False))
    return skills


@pytest.mark.parametrize('prompt, expected', [
    # $name
    ('$plugin Tidy the notes.', 'plugin'),
    ('$plugin:skill Tidy the notes.', 'plugin:skill'),
    ('$plugin:skill:child Tidy the notes.', 'plugin:skill:child'),
    ('Tidy the notes with $plugin:skill.', 'plugin:skill'),
    # a colon that does not carry on into a name segment is punctuation
    ('$plugin: tidy the notes.', 'plugin'),
    ('$plugin:skill: tidy the notes.', 'plugin:skill'),
    # unquoted
    ('Use the plugin skill to tidy the notes.', 'plugin'),
    ('Use the plugin:skill skill to tidy the notes.', 'plugin:skill'),
    ('Use the plugin:skill:child skill to tidy the notes.', 'plugin:skill:child'),
    ('Use skill plugin to tidy the notes.', 'plugin'),
    ('Use skill plugin:skill to tidy the notes.', 'plugin:skill'),
    ('Use skill plugin:skill:child to tidy the notes.', 'plugin:skill:child'),
    # quoted
    ('Use the "plugin" skill to tidy the notes.', 'plugin'),
    ('Use the "plugin:skill" skill to tidy the notes.', 'plugin:skill'),
    ('Use the `plugin:skill:child` skill to tidy the notes.', 'plugin:skill:child'),
    ('Use skill "plugin" to tidy the notes.', 'plugin'),
    ('Use skill "plugin:skill" to tidy the notes.', 'plugin:skill'),
    ('Use skill “plugin:skill” to tidy the notes.', 'plugin:skill'),
    ('Please run the installed skill "plugin:skill:child" on the notes.', 'plugin:skill:child'),
])
def test_a_name_that_prefixes_another_selects_only_the_one_requested(colliding, prompt, expected):
    guidance = select_for_task(prompt, colliding)
    assert guidance.names == (expected,)
    assert guidance.matched == ((expected, 'explicit'),)
    assert f'### {expected}\nCollision steps for {expected}.' in guidance.text


@pytest.mark.parametrize('prompt', [
    # a longer name nobody installed is not a request for any of its prefixes
    '$plugin:skill:child:extra Tidy the notes.',
    '$plugin:other Tidy the notes.',
    'Use the plugin:skill:child:extra skill to tidy the notes.',
    'Use skill plugin:skill:child:extra to tidy the notes.',
    'Use the "plugin:skill:child:extra" skill to tidy the notes.',
    'Use skill "plugin:skill:child:extra" to tidy the notes.',
    'Use skill `plugin:other` to tidy the notes.',
    # quoted whole instructions and negations stay what they were
    'Rewrite this sentence: "Use $plugin:skill to tidy the notes."',
    'Rewrite this sentence: "Use skill plugin:skill to tidy the notes."',
    'Do not use $plugin:skill here.',
    'Never use skill "plugin:skill" here.',
])
def test_a_longer_unknown_name_a_quotation_or_a_negation_selects_no_colliding_skill(colliding, prompt):
    assert select_for_task(prompt, colliding).names == ()


def test_noncurated_name_collision_remains_explicit_only(catalog):
    skill = replace(next(skill for skill in catalog if skill.name == 'coding'), curated=False)
    assert not select_for_task('Fix the Python bug.', [skill]).names
    assert select_for_task('$coding Fix the bug.', [skill]).names == ('coding',)
