"""Offline Council catalog and model-specific native effort contracts."""
import json

import pytest

from dream.core.council_config import effort_choices, provider_choices, validate_effort


def test_council_offers_named_frontier_models():
    providers = {p['key']: p for p in provider_choices()}
    assert providers['codex'].get('models'), 'Council needs a model dropdown catalog'
    assert any(m['id'] == 'claude-fable-5-1' for m in providers['anthropic']['models'])
    assert any(m['id'] == 'gpt-6-astra' for m in providers['openai']['models'])
    assert any(m['id'] == 'grok-4.6' for m in providers['grok']['models'])
    assert all('model_note' in p for p in providers.values())


def test_codex_cached_models_and_efforts(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    (tmp_path / 'models_cache.json').write_text(json.dumps({'models': [
        {'slug': 'test-new', 'display_name': 'New model', 'visibility': 'list',
         'supported_reasoning_levels': [{'effort': 'high'}, {'effort': 'ultra'}]},
        {'slug': 'test-hidden', 'visibility': 'hide'},
        {'slug': 'bad\nmodel', 'visibility': 'list'},
        {'slug': 'test-new', 'display_name': 'Duplicate', 'visibility': 'list'},
    ]}))
    from dream.core.model_catalog import model_choices
    choices = model_choices('codex')
    assert choices == [{'id': 'test-new', 'label': 'New model', 'efforts': ['high', 'ultra']}]
    assert effort_choices('codex', model='test-new') == ['high', 'ultra']
    assert validate_effort('codex', 'ultra', model='test-new') == 'ultra'
    with pytest.raises(ValueError):
        validate_effort('codex', 'low', model='test-new')


@pytest.mark.parametrize('contents', ['not json', '[]', '{"models": null}', '{"models": [null, 12]}'])
def test_unusable_cache_has_documented_fallback(tmp_path, monkeypatch, contents):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    (tmp_path / 'models_cache.json').write_text(contents)
    from dream.core.model_catalog import model_choices
    models = {m['id']: m for m in model_choices('codex')}
    assert models['gpt-6-astra']['efforts'][-2:] == ['max', 'ultra']
    assert 'ultra' not in models['gpt-5.6-luna']['efforts']


def test_catalog_results_do_not_share_mutable_state(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    from dream.core.model_catalog import model_choices
    choices = model_choices('codex')
    choices[0]['efforts'].clear()
    assert model_choices('codex')[0]['efforts']
    assert model_choices('machx') == []
    assert model_choices('gemini')[0]['efforts'] == []
