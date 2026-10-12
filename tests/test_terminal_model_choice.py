"""Owner selection and cancellation are separate from provider generation."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from dream.tui.app import App


def app():
    instance = App.__new__(App)
    instance.provider = 'anthropic'
    instance.model = 'custom-current'
    instance.engine = SimpleNamespace(model='custom-current', store=None)
    instance.renderer = SimpleNamespace(console=SimpleNamespace(print=lambda *args: None))
    return instance


async def test_bare_model_uses_selection_and_persists_only_success():
    instance = app()
    instance._choose_terminal_model = AsyncMock(return_value='claude-opus-5-5')
    async def set_model(name):
        instance.engine.model = name
    instance.engine.set_model = AsyncMock(side_effect=set_model)
    await instance._command('/model')
    instance.engine.set_model.assert_awaited_once_with('claude-opus-5-5')
    assert instance.model == instance.engine.model == 'claude-opus-5-5'


@pytest.mark.parametrize('selection', [None, 'unavailable-model'])
async def test_cancel_or_failure_does_not_change_model(selection):
    instance = app()
    instance._choose_terminal_model = AsyncMock(return_value=selection)
    instance.engine.set_model = AsyncMock(side_effect=ValueError('unavailable'))
    await instance._command('/model')
    assert instance.model == instance.engine.model == 'custom-current'
    if selection is None:
        instance.engine.set_model.assert_not_awaited()


async def test_model_dialog_has_offline_provider_options_and_escape(monkeypatch):
    instance = app()
    captured = {}
    async def run():
        return None
    def make(**kwargs):
        from prompt_toolkit.key_binding import KeyBindings
        captured.update(kwargs)
        captured['dialog'] = SimpleNamespace(key_bindings=KeyBindings(), run_async=run)
        return captured['dialog']
    monkeypatch.setattr('prompt_toolkit.shortcuts.radiolist_dialog', make)
    assert await instance._choose_terminal_model() is None
    assert captured['values'][0][0] == 'custom-current'
    assert not any(key.startswith('gpt-') for key, label in captured['values'])
    assert captured['default'] == 'custom-current'
    from prompt_toolkit.keys import Keys
    assert captured['dialog'].key_bindings.get_bindings_for_keys((Keys.Escape,))
