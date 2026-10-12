from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from dream.core.invocations import expand_invocation
from dream.gui.server import StudioServer
from dream.gui.bus import EventBus
from dream.skills.loader import FileSkill
from dream.tools import installed_skill_tools


@pytest.fixture
def skill(tmp_path, monkeypatch):
    package = tmp_path / 'chosen'
    package.mkdir()
    manifest = package / 'SKILL.md'
    manifest.write_text('---\nname: chosen skill\ndescription: Inspect a selected procedure.\n---\nONLY CHOSEN BODY\n')
    skill = FileSkill('chosen skill', 'Inspect a selected procedure.', manifest, package, 'fixture')
    monkeypatch.setattr(installed_skill_tools, 'installed', lambda: [skill])
    return skill


def test_only_explicit_selected_skill_body_loaded_and_task_preserved(skill, monkeypatch):
    from dream.skills import loader
    actual = loader.load_body
    calls = []
    monkeypatch.setattr(loader, 'load_body', lambda s: (calls.append(s), actual(s))[1])
    prompt = expand_invocation('/skill "chosen skill" Inspect "quoted input"\nand verify')
    assert calls == [skill]
    assert 'ONLY CHOSEN BODY' in prompt
    assert 'Inspect "quoted input"\nand verify' in prompt
    assert expand_invocation('Tell me about /skill chosen') == 'Tell me about /skill chosen'
    assert calls == [skill]


@pytest.mark.parametrize('text', ['/skill missing task', '/skill "chosen skill"', '/skill "unterminated'])
def test_missing_disabled_or_invalid_skill_refused(skill, text):
    with pytest.raises(ValueError):
        expand_invocation(text)


async def test_catalog_is_metadata_only_and_submission_executes_selected_skill(skill, monkeypatch):
    from dream.skills import loader
    from dream import plugins
    actual = loader.load_body
    calls = []
    monkeypatch.setattr(loader, 'load_body', lambda s: (calls.append(s), actual(s))[1])
    monkeypatch.setattr(plugins, 'loaded', lambda: [])
    prompts = []
    s = StudioServer(EventBus(), port=8123, on_prompt=prompts.append)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=s.app), base_url='http://127.0.0.1:8123', headers={'X-Dream-Token': s.token}) as client:
        response = await client.get('/api/commands')
        row = next(c for c in response.json()['commands'] if c['kind'] == 'skill')
        assert row['description'] == skill.description and 'ONLY CHOSEN BODY' not in response.text
        assert not calls
        submitted = await client.post('/api/prompt', json={'prompt': '/skill "chosen skill" Inspect the project'})
        assert submitted.status_code == 200
        assert calls == [skill] and 'ONLY CHOSEN BODY' in prompts[0]


async def test_terminal_skill_command_calls_same_expansion(skill):
    from dream.tui.app import App
    app = SimpleNamespace(_ask=AsyncMock(), renderer=SimpleNamespace(error=lambda _: None))
    assert await App._command(app, '/skill "chosen skill" Inspect the project') is False
    assert 'ONLY CHOSEN BODY' in app._ask.call_args.args[0]


def test_enabled_plugin_invocation_and_disabled_refusal(monkeypatch):
    from dream import plugins
    p = SimpleNamespace(name='fixture', description='Research tools.', parts=['tools:2'], enabled=True)
    monkeypatch.setattr(plugins, 'loaded', lambda: [p])
    assert 'Research tools.' in expand_invocation('/plugin fixture Find evidence')
    p.enabled = False
    with pytest.raises(ValueError, match='not enabled'):
        expand_invocation('/plugin fixture Find evidence')
