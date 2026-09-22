"""Small curated guidance and deliberate external discovery; no model dependency."""
from dataclasses import replace
import json
from pathlib import Path
import tomllib

import pytest

from dream import config, plugins
from dream.skills import loader
from dream.skills.selection import MAX_GUIDANCE_CHARS, select_for_task
from dream.tools import installed_skill_tools

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def curated(monkeypatch):
    monkeypatch.delenv('DREAM_SKILL_DIRS', raising=False)
    monkeypatch.delenv('DREAM_EXTERNAL_SKILLS', raising=False)
    monkeypatch.setattr(plugins, '_LOADED', [])
    monkeypatch.setattr(installed_skill_tools, '_CACHE', None)
    skills, warnings = loader.discover([ROOT/'skills'/name for name in config.CURATED_SKILLS])
    assert not warnings
    assert all(skill.curated for skill in skills)
    return skills


def make_skill(root, name, *, filename='SKILL.md', text='Apply the specific workflow.', description='Useful external workflow.'):
    folder = root/name
    folder.mkdir(parents=True)
    (folder/filename).write_text(f'---\nname: {name}\ndescription: {description}\n---\n\n{text}')
    return folder


def test_default_catalog_is_small_and_all_workflows_are_packaged(curated):
    assert {s.name for s in curated} == set(config.CURATED_SKILLS)
    assert len(curated) == 14
    # 10 workflows fit in 16k; the four process skills added 2026-09-22 (brainstorming,
    # debugging, gated-build, frontend-design) bring the curated set to 14 at ~2.1k each.
    assert sum(len(s.manifest.read_text()) for s in curated) < 25000
    assert len('\n'.join(loader.index_lines(curated))) < 1600
    project = tomllib.loads((ROOT/'pyproject.toml').read_text())
    wheel = project['tool']['hatch']['build']['targets']['wheel']
    selected = set(wheel['only-include'])
    assert selected == {'dream', 'skills/illuminati-handshake',
                        *(f'skills/{skill.name}' for skill in curated)}
    mapping = wheel['sources']
    assert mapping == {'skills': 'dream/resources/skills'}
    for skill in curated:
        source = Path('skills') / skill.name
        assert source.as_posix() in selected
        installed = Path(mapping[source.parts[0]]) / source.relative_to(source.parts[0])
        assert installed == Path('dream/resources/skills') / skill.name
    assert 'skills/illuminati-handshake' in selected
    assert 'illuminati-handshake' not in {s.name for s in curated}


def test_relocated_dream_root_retains_checkout_or_wheel_catalog(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'ROOT', tmp_path/'session')
    monkeypatch.setattr(config, 'PKG_DIR', ROOT/'dream')
    assert ROOT/'skills'/'coding' in config.bundled_skill_dirs()
    package = tmp_path/'installed'/'dream'
    monkeypatch.setattr(config, 'PKG_DIR', package)
    assert package/'resources'/'skills'/'coding' in config.bundled_skill_dirs()


def test_default_roots_do_not_discover_global_skills_or_optional_package(monkeypatch, tmp_path, curated):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.delenv('CODEX_HOME', raising=False)
    make_skill(tmp_path/'.codex'/'skills', 'global-codex')
    make_skill(tmp_path/'.agents'/'skills', 'global-agent')
    found, warnings = loader.discover(config.skill_dirs())
    assert {s.name for s in found} == set(config.CURATED_SKILLS)
    assert not warnings


def test_external_roots_require_opt_in_and_stay_out_of_wake_index(monkeypatch, tmp_path, curated):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.delenv('CODEX_HOME', raising=False)
    make_skill(tmp_path/'.codex'/'skills', 'global-workflow', filename='Skill.md')
    monkeypatch.setenv('DREAM_EXTERNAL_SKILLS', '1')
    found = installed_skill_tools.installed(refresh=True)
    assert 'global-workflow' in {s.name for s in found}
    assert all('global-workflow' not in line for line in installed_skill_tools.index_lines())
    explicit = select_for_task('Use the global-workflow skill. Help with this request.', found)
    assert explicit.names == ('global-workflow',)
    assert 'Apply the specific workflow' in explicit.text
    assert not explicit.tools
    assert not select_for_task('Useful external workflow.', found).names


def test_explicit_file_roots_override_preserves_enabled_dream_plugins(monkeypatch, tmp_path):
    explicit = make_skill(tmp_path/'chosen', 'only-this')
    plugin_root = make_skill(tmp_path/'plugin', 'plugin-skill')
    monkeypatch.setattr(plugins, '_LOADED', [])
    monkeypatch.setattr(plugins, 'skill_dirs', lambda: [plugin_root.parent])
    monkeypatch.setenv('DREAM_EXTERNAL_SKILLS', '1')
    monkeypatch.setenv('DREAM_SKILL_DIRS', str(explicit.parent))
    monkeypatch.setattr(installed_skill_tools, '_CACHE', None)
    assert {s.name for s in installed_skill_tools.installed()} == {'only-this', 'plugin-skill'}
    monkeypatch.setenv('DREAM_SKILL_DIRS', '')
    assert {s.name for s in installed_skill_tools.installed(refresh=True)} == {'plugin-skill'}


@pytest.mark.parametrize('prompt, required', [
    ('Fix the checkout button bug and add a regression test.', {'coding'}),
    ('Research the latest release documentation and compare providers.', {'research'}),
    ('Rewrite this email to sound calm and direct.', {'writing'}),
    ('Summarize pages 11-20 of report.pdf.', {'documents'}),
    ('Create a PowerPoint slide deck from this brief.', {'documents'}),
    ('Clean this CSV and calculate revenue totals by region.', {'data-analysis'}),
    ('Make a 15-second product animation from these photos.', {'media'}),
    ('Update the saved plan in my Library.', {'library'}),
    ('The export tool timed out; recover without duplicating the job.', {'verifying'}),
    ('Research the current standards and write an executive summary memo.', {'research','writing'}),
])
def test_realistic_task_selection(curated, prompt, required):
    guidance = select_for_task(prompt, curated)
    assert required <= set(guidance.names)
    assert len(guidance.names) <= 2
    assert len(guidance.text) <= MAX_GUIDANCE_CHARS
    assert len(guidance.tools) <= 8


@pytest.mark.parametrize('prompt', ['Hello!', 'Thanks, that helped.', 'What is 2 + 2?',
                                    'What is the capital of France?', 'Tell me a joke.'])
def test_simple_conversation_adds_no_workflow(curated, prompt):
    assert not select_for_task(prompt, curated).text


def test_explicit_name_beats_more_numerous_implicit_matches(curated):
    prompt = 'Use the writing skill. Debug this Python API codebase and inspect the failing regression test.'
    guidance = select_for_task(prompt, curated)
    assert guidance.names[0] == 'writing'
    assert len(guidance.names) == 2
    assert select_for_task('$library Please help me with this.', curated).names == ('library',)


@pytest.mark.parametrize('prompt', ["Don't use skills; fix this Python bug.",
                                    'Do not use any skills. Research the latest release.',
                                    'Handle this without skills: create a PDF.',
                                    'Skip skills and calculate CSV totals.'])
def test_explicit_global_skill_opt_out(curated, prompt):
    assert not select_for_task(prompt, curated).text


@pytest.mark.parametrize('prompt', ['Do not use the coding skill to fix this bug.',
                                    "Don't use $coding to debug this Python test.",
                                    'Fix this bug without the coding skill.'])
def test_negated_named_skill_is_not_restored_by_keyword_routing(curated, prompt):
    assert 'coding' not in select_for_task(prompt, curated).names


def test_disabled_skill_cannot_be_selected_even_explicitly(curated, monkeypatch):
    monkeypatch.setattr(loader, 'enabled', lambda skill: skill.name != 'coding')
    assert 'coding' not in select_for_task('$coding Fix the Python bug.', curated).names


def test_duplicate_catalog_entries_do_not_repeat_workflows(curated):
    guidance = select_for_task('$coding Fix the Python bug.', curated + curated)
    assert guidance.names.count('coding') == 1


@pytest.mark.parametrize('budget', [0, 80, 250, 600, 1300, 4000, 100000])
def test_guidance_obeys_context_budget_without_reference_expansion(curated, budget):
    guidance = select_for_task('$coding $documents Build a website and PDF.', curated, max_chars=budget)
    assert len(guidance.text) <= min(budget, MAX_GUIDANCE_CHARS)
    assert len(guidance.names) <= 2
    assert 'const TWEAK_DEFAULTS' not in guidance.text


def test_unreadable_skill_reports_failure_without_success_name(curated, tmp_path):
    missing = replace(curated[0], name='missing', manifest=tmp_path/'absent.md')
    guidance = select_for_task('$missing', [missing])
    assert not guidance.names and not guidance.text
    assert guidance.warnings and 'could not be read' in guidance.warnings[0]


def test_curated_capabilities_name_real_tools_and_references_exist(curated):
    from dream.tools import files, native, registry
    tools = {t.name for t in registry._BASE_TOOLS + native.NATIVE_TOOLS + files.FILE_TOOLS}
    import re
    for skill in curated:
        assert skill.capabilities
        assert set(skill.capabilities) <= tools
        for text_path in [skill.manifest, *skill.root.glob('references/*.md')]:
            for relative in re.findall(r'\]\(([^)]+)\)', text_path.read_text()):
                assert (text_path.parent/relative).is_file(), (text_path, relative)
        metadata = json.loads((skill.root/'manifest.json').read_text())
        assert metadata['portable'] is True


def test_case_variant_manifest_is_discovered_once_with_canonical_precedence(tmp_path):
    folder = make_skill(tmp_path, 'oddly-cased', filename='Skill.md')
    skills, _ = loader.discover([tmp_path])
    assert len(skills) == 1 and skills[0].manifest.name == 'Skill.md'
    (folder/'SKILL.md').write_text('---\nname: canonical\ndescription: preferred\n---\nbody')
    skills, _ = loader.discover([tmp_path])
    assert [s.name for s in skills] == ['canonical']


@pytest.mark.parametrize('prompt', [
    'Build an animated rocketship taking off',
    'Create animations of a rocket launching',
    'Help with animating the rocket scene',
])
def test_animation_word_forms_load_media_workflow(curated, prompt):
    guidance = select_for_task(prompt, curated)
    assert 'media' in guidance.names
    assert 'media_read' in guidance.tools
    assert len(guidance.text) <= MAX_GUIDANCE_CHARS
