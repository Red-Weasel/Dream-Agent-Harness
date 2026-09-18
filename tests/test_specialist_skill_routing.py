"""Specialist guidance is discoverable and routed without flooding ordinary tasks."""
import re
import pytest
from dream import config, plugins
from dream.skills import loader
from dream.skills.selection import select_for_task, MAX_GUIDANCE_CHARS

@pytest.fixture
def skills(monkeypatch):
    monkeypatch.delenv('DREAM_SKILL_DIRS', raising=False)
    monkeypatch.delenv('DREAM_EXTERNAL_SKILLS', raising=False)
    monkeypatch.setattr(plugins, '_LOADED', [])
    found, warnings = loader.discover(config.skill_dirs())
    assert not warnings
    return found

@pytest.mark.parametrize('prompt,expected', [
    ('Use the browser to fill this form.', 'computer-use'),
    ('Click the Save button in the open dialog.', 'computer-use'),
    ('Polish the lighting in my existing Blender scene.', 'blender-animation'),
    ('Continue editing launch.blend and preserve the original.', 'blender-animation'),
    ('Use the computer-use skill for this interface.', 'computer-use'),
    ('Use the blender-animation skill for this scene.', 'blender-animation'),
    ('Draw a portrait from this reference in Krita using only its GUI.', 'computer-use'),
    ('Polish my existing drawing in Paint, preserving its style.', 'computer-use'),
    ('Draw this portrait in JS Paint.', 'computer-use'),
    ('In GIMP, repaint the shadows on this portrait.', 'computer-use'),
    ('Use GIMP to retouch this photo.', 'computer-use'),
    ('Please continue polishing my portrait in Krita.', 'computer-use'),
    ('Can you help me draw this person in Krita?', 'computer-use'),
    ('Draw a portrait in MyPaint using its GUI.', 'computer-use'),
    ('Could you retouch the highlights on this painting using Photoshop?', 'computer-use'),
    ('In Krita, please polish the shadows on my existing portrait.', 'computer-use'),
])
def test_specialist_entrypoints_reach_relevant_requests(skills, prompt, expected):
    guidance = select_for_task(prompt, skills)
    assert expected in guidance.names
    assert len(guidance.names) <= 2 and len(guidance.text) <= MAX_GUIDANCE_CHARS
    assert 'references/' in guidance.text

@pytest.mark.parametrize('prompt', ['Hi there', 'What does computer use mean?',
                                  'Render a 3D animation using Three.js.',
                                  'Write an email about our browser market share.',
                                  'Explain how to draw a portrait in Krita.',
                                  'Write a review of painting apps such as Krita.',
                                  'Draw a diagram in Markdown.',
                                  'Polish the paragraph about paint colors.',
                                  'Edit this sentence: I like to draw in Krita.',
                                  'Polish this paragraph about painting in Photoshop.',
                                  'Paint my bedroom with paint that matches the sofa.'])
def test_general_requests_do_not_load_specialist_workflows(skills, prompt):
    assert not {'computer-use','blender-animation'}.intersection(select_for_task(prompt, skills).names)

def test_specialist_disabled_and_opt_out_respected(skills, monkeypatch):
    monkeypatch.setattr(loader, 'enabled', lambda s: s.name != 'computer-use')
    assert 'computer-use' not in select_for_task('$computer-use Click the Save button.', skills).names
    assert not select_for_task('Do not use skills. Polish this Blender scene.', skills).names


def test_combined_blender_gui_workflow_keeps_both_entrypoints_complete(skills):
    guidance = select_for_task('Use the Blender GUI and click the Render button.', skills)
    assert set(guidance.names) == {'computer-use', 'blender-animation'}
    assert len(guidance.text) <= MAX_GUIDANCE_CHARS
    assert 'Workflow shortened' not in guidance.text
    assert 'recorded' in guidance.text and 'demonstration cases' in guidance.text

def test_specialist_entrypoints_and_references_fit_progressive_loading(skills):
    found = {s.name:s for s in skills}
    for name in ('computer-use','blender-animation'):
        skill = found[name]
        text = skill.manifest.read_text()
        body = text.split('---',2)[2].strip()
        assert len(body) <= 2200  # The actual automatic injection boundary.
        for ref in re.findall(r'\]\((references/[^)]+)\)', body):
            assert (skill.root/ref).is_file()
