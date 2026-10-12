"""Requested suggestions survive local cache metadata without capability expansion."""
import json

import pytest

from dream.core.model_catalog import model_choices, model_efforts


CODEX = ['GPT-6.1-Sol', 'GPT-6-Astra', 'GPT-6-Sol', 'GPT-6-Luna',
         'GPT-5.6-Sol', 'GPT-5.6-Terra', 'GPT-5.6-Luna']
EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max']


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    return tmp_path / 'codex'


@pytest.mark.parametrize('provider,labels', [
    ('anthropic', ['Opus 5.5', 'Fable 5.1', 'Sonnet 5.5', 'Haiku 4.5', 'Sonnet 5',
                   'Opus 5', 'Fable 5', 'Opus 4.8', 'Opus 4.7', 'Opus 4.6', 'Sonnet 4.6']),
    ('codex', CODEX),
    ('gemini', ['Gemini-3.5-Flash-Lite', 'Gemini-3.8-Flash', 'Gemini-3.1 Pro']),
    ('grok', ['Grok 4.7', 'Grok 4.7 Fast', 'Grok 4.6', 'Grok 4.5']),
])
def test_exact_requested_labels_and_order(provider, labels):
    choices = model_choices(provider)
    assert [choice['label'] for choice in choices] == labels
    assert len({choice['id'] for choice in choices}) == len(labels)


def test_preserves_existing_special_ids_and_requested_efforts():
    assert model_choices('anthropic')[3]['id'] == 'claude-haiku-4-5-20251001'
    assert model_choices('gemini')[-1]['id'] == 'gemini-3.1-pro-preview'
    assert all(choice['efforts'] == ['off', 'on'] for choice in model_choices('gemini'))
    assert all(choice['efforts'] == EFFORTS for choice in model_choices('anthropic'))
    assert all(choice['efforts'] == EFFORTS for choice in model_choices('codex'))


def write_cache(path, models):
    path.mkdir()
    (path / 'models_cache.json').write_text(json.dumps({'models': models}))


def test_cache_merges_requested_models_and_preserves_custom_choices(isolated_cache):
    write_cache(isolated_cache, [
        {'slug': 'custom-model', 'display_name': 'Custom label', 'visibility': 'list',
         'supported_reasoning_levels': [{'effort': 'ultra'}, {'effort': 'high'}]},
        {'slug': 'gpt-6-astra', 'display_name': 'Wrong cached label', 'visibility': 'list',
         'supported_reasoning_levels': [{'effort': 'ultra'}, {'effort': 'high'}, {'effort': 'low'}]},
        {'slug': 'hidden-model', 'visibility': 'hide'},
    ])
    choices = model_choices('codex')
    assert [choice['label'] for choice in choices[:7]] == CODEX
    assert choices[1]['efforts'] == ['low', 'high']
    assert choices[0]['efforts'] == EFFORTS
    assert choices[7:] == [{'id': 'custom-model', 'label': 'Custom label', 'efforts': ['high', 'ultra']}]
    assert model_efforts('codex', 'saved-unlisted-id') is None


def test_known_cache_without_supported_efforts_does_not_expand_capabilities(isolated_cache):
    write_cache(isolated_cache, [{'slug': 'gpt-6-astra', 'visibility': 'list',
                                'supported_reasoning_levels': [{'effort': 'ultra'}]}])
    assert model_efforts('codex', 'gpt-6-astra') == []
    assert len(model_choices('codex')) == 7


@pytest.mark.parametrize('visibility', ['hide', None])
@pytest.mark.parametrize('levels,expected', [([{'effort': 'low'}], ['low']), ([], [])])
def test_hidden_requested_model_still_narrows_effort_metadata(isolated_cache, visibility, levels, expected):
    from dream.core.council_config import validate_effort
    model = {'slug': 'gpt-6-astra', 'supported_reasoning_levels': levels}
    if visibility is not None:
        model['visibility'] = visibility
    write_cache(isolated_cache, [model, {'slug': 'hidden-custom', 'visibility': 'hide',
                                       'supported_reasoning_levels': [{'effort': 'low'}]}])
    choices = model_choices('codex')
    assert [choice['label'] for choice in choices] == CODEX
    assert model_efforts('codex', 'gpt-6-astra') == expected
    with pytest.raises(ValueError, match='not supported'):
        validate_effort('codex', 'max', model='gpt-6-astra')


@pytest.mark.parametrize('provider', ['anthropic', 'codex', 'gemini', 'grok'])
def test_catalog_outputs_are_detached(provider):
    expected = model_choices(provider)
    altered = model_choices(provider)
    altered[0]['label'] = 'local mutation'
    altered[0]['efforts'].append('local-only')
    altered.clear()
    assert model_choices(provider) == expected


@pytest.mark.parametrize('content', ['not json', '[]', '{"models": null}'])
def test_bad_cache_keeps_requested_suggestions(isolated_cache, content):
    isolated_cache.mkdir()
    (isolated_cache / 'models_cache.json').write_text(content)
    assert [choice['label'] for choice in model_choices('codex')] == CODEX
