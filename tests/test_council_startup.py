"""Council launch contracts using CPU fixtures, without provider/model calls."""
from dataclasses import asdict
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from rich.console import Console

from dream.core import moe
from dream.desktop import startup
from dream.local import machx
from dream.tui import picker


@pytest.fixture
def saved(monkeypatch, tmp_path):
    monkeypatch.setattr(moe, 'CONFIG_PATH', tmp_path / 'council.json')
    cfg = moe.MoeConfig('codex', ['gemini'], max_concurrency=1,
                        timeout_seconds=147, legal_review=True,
                        advisor_models={'gemini': 'chosen-advisor'}, orchestrator_effort='high')
    moe.save_config(cfg)
    return cfg


@pytest.mark.asyncio
async def test_boot_passes_exact_council_and_main_model_without_loading(monkeypatch, tmp_path, saved):
    from dream.tui import app
    constructor = Mock(return_value=SimpleNamespace(run=AsyncMock()))
    monkeypatch.setattr(app, 'App', constructor)
    monkeypatch.setattr(machx, 'serve', Mock(side_effect=AssertionError('Unexpected load')))
    await startup.run(dict(workspace=str(tmp_path), council=asdict(saved),
                           choice=dict(provider='codex', kind='provider', model='chosen-main')),
                      tmp_path / 'status.json')
    assert constructor.call_args.kwargs['moe'] == saved
    assert constructor.call_args.kwargs['model'] == 'chosen-main'


@pytest.mark.asyncio
async def test_conflicting_council_rejected_before_attach_probe(monkeypatch, tmp_path, saved):
    probe = Mock(side_effect=AssertionError('Council must validate before probes'))
    monkeypatch.setattr(machx, 'served_model_id', probe)
    with pytest.raises(ValueError, match='orchestrator'):
        await startup.run(dict(workspace=str(tmp_path), council=asdict(saved),
                               choice=dict(provider='machx', kind='attach', model='loaded')),
                          tmp_path / 'status.json')
    probe.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('model', [25, 'bad\x7fmodel', 'bad\nmodel', 'x' * 257])
async def test_invalid_main_model_is_rejected_before_session(monkeypatch, tmp_path, model):
    from dream.tui import app
    constructor = Mock(return_value=SimpleNamespace(run=AsyncMock()))
    monkeypatch.setattr(app, 'App', constructor)
    with pytest.raises(ValueError, match='[Mm]odel'):
        await startup.run(dict(workspace=str(tmp_path), choice=dict(kind='provider', provider='codex', model=model)),
                          tmp_path / 'status.json')
    constructor.assert_not_called()


def providers():
    return [picker.ProviderRow(key, key.title(), 'fixture', True, '')
            for key in ('codex', 'gemini', 'anthropic')]


@pytest.mark.asyncio
async def test_terminal_reuses_all_saved_advanced_fields(monkeypatch, saved):
    monkeypatch.setattr(picker, '_ask', AsyncMock(return_value='y'))
    result = await picker.moe_role_screen(Console(file=StringIO()), Mock(), providers())
    assert result == saved


@pytest.mark.asyncio
async def test_terminal_advisors_require_explicit_choice(monkeypatch, tmp_path):
    monkeypatch.setattr(moe, 'CONFIG_PATH', tmp_path / 'council.json')
    monkeypatch.setattr(picker, '_ask', AsyncMock(side_effect=['1', 'n', 'n', 'y']))
    result = await picker.moe_role_screen(Console(file=StringIO()), Mock(), providers())
    assert result.advisors == ['anthropic']
    assert moe.load_config() == result


@pytest.mark.asyncio
async def test_terminal_edit_preserves_advanced_settings(monkeypatch, saved):
    monkeypatch.setattr(picker, '_ask', AsyncMock(side_effect=['n', '1', 'n', 'y', 'n']))
    result = await picker.moe_role_screen(Console(file=StringIO()), Mock(), providers())
    assert result == saved


@pytest.mark.asyncio
async def test_terminal_disabling_advisor_removes_its_effort(monkeypatch, saved):
    from dataclasses import replace
    moe.save_config(replace(saved, advisors=['gemini', 'anthropic'],
                            orchestrator_effort='high', advisor_efforts={'anthropic': 'low'}))
    monkeypatch.setattr(picker, '_ask', AsyncMock(side_effect=['n', '1', 'n', 'y', 'n']))
    result = await picker.moe_role_screen(Console(file=StringIO()), Mock(), providers())
    assert result.advisors == ['gemini']
    assert result.advisor_efforts == {}
    assert result.orchestrator_effort == 'high'


@pytest.mark.asyncio
async def test_terminal_new_main_uses_its_default_effort(monkeypatch, saved):
    from dataclasses import replace
    moe.save_config(replace(saved, orchestrator_effort='high'))
    monkeypatch.setattr(picker, '_ask', AsyncMock(side_effect=['n', '2', 'y', 'n', 'n']))
    result = await picker.moe_role_screen(Console(file=StringIO()), Mock(), providers())
    assert result.orchestrator == 'gemini'
    assert result.orchestrator_effort is None


@pytest.mark.asyncio
async def test_terminal_preserves_same_provider_advisor_model(monkeypatch, saved):
    from dataclasses import replace
    config = replace(saved, advisors=['codex'], advisor_models={'codex': 'independent-model'},
                     advisor_efforts={'codex': 'low'})
    moe.save_config(config)
    monkeypatch.setattr(picker, '_ask', AsyncMock(side_effect=['n', '1', 'y', 'n', 'n']))
    result = await picker.moe_role_screen(Console(file=StringIO()), Mock(), providers())
    assert result == config


@pytest.mark.asyncio
@pytest.mark.parametrize('answer, advisors', [('', []), ('y', ['anthropic'])])
async def test_single_provider_council_requires_explicit_advisor_opt_in(monkeypatch, tmp_path, answer, advisors):
    monkeypatch.setattr(moe, 'CONFIG_PATH', tmp_path / 'council.json')
    monkeypatch.setattr(picker, '_cli_installed', lambda name: name == 'claude')
    rows = picker.detect_providers()
    assert next(row for row in rows if row.key == 'moe').ready
    monkeypatch.setattr(picker, '_ask', AsyncMock(side_effect=['1', answer]))
    result = await picker.moe_role_screen(Console(file=StringIO()), Mock(), rows)
    assert result.orchestrator == 'anthropic'
    assert result.advisors == advisors


@pytest.fixture
def onboarding_class(monkeypatch):
    """Import native controller logic with GTK replaced, without opening a display."""
    import importlib.util
    import sys
    from pathlib import Path
    from types import ModuleType
    gi = ModuleType('gi.repository')
    gi.Gtk = SimpleNamespace(Box=object, ComboBoxText=type('ComboBoxText', (), {}))
    gi.GLib = gi.Pango = SimpleNamespace()
    browser = ModuleType('dream.desktop.browser')
    browser.label = lambda *args: None
    monkeypatch.setitem(sys.modules, 'gi', ModuleType('gi'))
    monkeypatch.setitem(sys.modules, 'gi.repository', gi)
    monkeypatch.setitem(sys.modules, 'dream.desktop.browser', browser)
    spec = importlib.util.spec_from_file_location('dream.desktop._onboarding_fixture',
                                                Path(startup.__file__).with_name('onboarding.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Onboarding


def test_native_launch_carries_main_and_only_enabled_advisors(onboarding_class, saved, tmp_path):
    panel = onboarding_class.__new__(onboarding_class)
    panel.owner = SimpleNamespace(running=False, cwd=str(tmp_path), launch_selection=Mock())
    panel.engines = SimpleNamespace(get_active=lambda: 0)
    panel.choices = [dict(provider='codex', kind='provider', model=None)]
    panel.folder = SimpleNamespace(get_filename=lambda: str(tmp_path))
    panel.main_model = SimpleNamespace(get_text=lambda: ' chosen-main ')
    panel.main_effort = SimpleNamespace(get_active_id=lambda: 'high')
    panel.advisor_effort_fields = {}
    panel.saved_council = asdict(saved)
    panel.advisor_fields = {
        'gemini': (SimpleNamespace(get_active=lambda: True),
                   SimpleNamespace(get_text=lambda: 'chosen-advisor')),
        'anthropic': (SimpleNamespace(get_active=lambda: False),
                      SimpleNamespace(get_text=lambda: 'unused-model')),
    }
    panel.message = Mock()
    panel.launch()
    request = panel.owner.launch_selection.call_args.args[0]
    assert request['choice']['model'] == 'chosen-main'
    assert request['council'] == asdict(saved)
    assert panel.choices[0]['model'] is None


@pytest.mark.asyncio
async def test_same_provider_main_and_advisor_keep_distinct_models(onboarding_class, monkeypatch, tmp_path):
    from dream.tui import app
    panel = onboarding_class.__new__(onboarding_class)
    panel.owner = SimpleNamespace(running=False, cwd=str(tmp_path), launch_selection=Mock())
    panel.engines = SimpleNamespace(get_active=lambda: 0)
    panel.choices = [dict(provider='codex', kind='provider', model=None)]
    panel.folder = SimpleNamespace(get_filename=lambda: str(tmp_path))
    panel.main_model = SimpleNamespace(get_text=lambda: 'main-model')
    panel.main_effort = SimpleNamespace(get_active_id=lambda: 'high')
    panel.saved_council = {}
    panel.council_choices = [dict(key='codex', available=True, efforts=['low', 'high'])]
    state = {'enabled': True}
    toggle = SimpleNamespace(get_active=lambda: state['enabled'],
                             set_active=lambda active: state.update(enabled=active), set_sensitive=Mock())
    panel.advisor_fields = {'codex': (toggle, SimpleNamespace(get_text=lambda: 'independent-model', set_sensitive=Mock()))}
    panel.advisor_effort_fields = {'codex': SimpleNamespace(get_active_id=lambda: 'low', set_sensitive=Mock())}
    panel.council = Mock()
    panel.message = Mock()
    panel.advisors_changed()
    panel.launch()
    request = panel.owner.launch_selection.call_args.args[0]
    assert request['council']['advisors'] == ['codex']
    constructor = Mock(return_value=SimpleNamespace(run=AsyncMock()))
    monkeypatch.setattr(app, 'App', constructor)
    await startup.run(request, tmp_path / 'status.json')
    assert constructor.call_args.kwargs['model'] == 'main-model'
    council = constructor.call_args.kwargs['moe']
    assert council.advisor_models == {'codex': 'independent-model'}
    assert council.orchestrator_effort == 'high'
    assert council.advisor_efforts == {'codex': 'low'}


def test_native_launch_passes_main_and_enabled_advisor_effort(onboarding_class, tmp_path):
    panel = onboarding_class.__new__(onboarding_class)
    panel.owner = SimpleNamespace(running=False, cwd=str(tmp_path), launch_selection=Mock())
    panel.engines = SimpleNamespace(get_active=lambda: 0)
    panel.choices = [dict(provider='codex', kind='provider', model=None)]
    panel.folder = SimpleNamespace(get_filename=lambda: str(tmp_path))
    panel.main_model = SimpleNamespace(get_text=lambda: '')
    panel.main_effort = SimpleNamespace(get_active_id=lambda: 'high')
    panel.saved_council = {}
    panel.advisor_fields = {
        'anthropic': (SimpleNamespace(get_active=lambda: True), SimpleNamespace(get_text=lambda: '')),
        'gemini': (SimpleNamespace(get_active=lambda: False), SimpleNamespace(get_text=lambda: '')),
    }
    panel.advisor_effort_fields = {
        'anthropic': SimpleNamespace(get_active_id=lambda: 'low'),
        'gemini': SimpleNamespace(get_active_id=lambda: ''),
    }
    panel.message = Mock()
    panel.launch()
    config = panel.owner.launch_selection.call_args.args[0]['council']
    assert config['orchestrator_effort'] == 'high'
    assert config['advisor_efforts'] == {'anthropic': 'low'}


def test_native_local_launch_preserves_model_settings_and_effort(onboarding_class, tmp_path):
    panel = onboarding_class.__new__(onboarding_class)
    panel.owner = SimpleNamespace(running=False, cwd=str(tmp_path), launch_selection=Mock())
    panel.engines = SimpleNamespace(get_active=lambda: 0)
    panel.choices = [dict(provider='machx', kind='local', path='/fixture.gguf')]
    panel.folder = SimpleNamespace(get_filename=lambda: str(tmp_path))
    panel.main_model = SimpleNamespace(get_text=lambda: 'must-not-override-weights')
    panel.main_effort = SimpleNamespace(get_active_id=lambda: '')
    panel.saved_council = dict(max_concurrency=1, timeout_seconds=147, legal_review=True)
    panel.advisor_fields = {}
    panel.advisor_effort_fields = {}
    panel.settings = dict(identity='fixture-key', revision='fixture-revision')
    panel.fields = {
        'ctx': (SimpleNamespace(get_text=lambda: '32768'), dict(kind='int')),
        'gpus': (SimpleNamespace(get_text=lambda: '2'), dict(kind='int')),
        'reasoning_effort': (SimpleNamespace(get_text=lambda: 'high'), dict(kind='enum')),
    }
    panel.message = Mock()
    panel.launch()
    request = panel.owner.launch_selection.call_args.args[0]
    assert request['choice'] == dict(provider='machx', kind='local', path='/fixture.gguf')
    assert request['selection'] == dict(ctx=32768, gpus=2, options={'reasoning_effort': 'high'})
    assert request['identity'] == 'fixture-key'
    assert request['revision'] == 'fixture-revision'
    assert request['council']['orchestrator_effort'] is None
    assert request['council']['max_concurrency'] == 1


def test_catalog_returns_saved_advanced_settings_and_advisor_metadata(monkeypatch, saved):
    monkeypatch.setattr(machx, 'served_model_id', lambda: 'existing')
    monkeypatch.setattr(machx, 'available', lambda: False)
    monkeypatch.setattr(picker, 'detect_providers', providers)
    data = startup.catalog()
    assert data['choices'][0]['kind'] == 'attach'
    assert data['saved_council'] == asdict(saved)
    assert {'codex', 'anthropic', 'gemini', 'machx'} <= {c['key'] for c in data['council_choices']}
    assert all(isinstance(c['available'], bool) and c['note'] for c in data['council_choices'])
