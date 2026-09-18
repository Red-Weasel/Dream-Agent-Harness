"""Council configuration is strict and discovery never contacts a model."""
from dataclasses import asdict
import importlib
import pytest
from dream.core import moe


def module():
    return importlib.import_module('dream.core.council_config')


def test_config_preserves_models_limits_and_empty_council():
    raw = dict(orchestrator='machx', advisors=['codex'], advisor_models={'codex': 'chosen-model'},
               max_concurrency=2, timeout_seconds=42.5, legal_review=True,
               orchestrator_effort=None, advisor_efforts={})
    assert asdict(module().parse_config(raw)) == raw
    assert module().parse_config({'orchestrator': 'machx', 'advisors': []}).advisors == []
    cfg = module().parse_config(raw)
    raw['advisors'].clear()
    raw['advisor_models'].clear()
    assert cfg.advisors == ['codex'] and cfg.advisor_models == {'codex': 'chosen-model'}


@pytest.mark.parametrize('change', [
    {'orchestrator': 'unknown'}, {'advisors': ['codex', 'codex']},
    {'advisors': ['unknown']}, {'advisors': 'codex'}, {'advisors': [False]},
    {'max_concurrency': True}, {'max_concurrency': 1.5}, {'max_concurrency': 0},
    {'timeout_seconds': float('nan')}, {'timeout_seconds': float('inf')},
    {'timeout_seconds': '90'}, {'timeout_seconds': 3601}, {'legal_review': 'false'},
    {'advisor_models': {'gemini': 'not-selected'}}, {'advisor_models': {'codex': ''}},
    {'advisor_models': {'codex': 'bad\nmodel'}}, {'extra': 1},
])
def test_invalid_config_rejected(change):
    with pytest.raises(ValueError):
        module().parse_config({'orchestrator': 'machx', 'advisors': ['codex'], **change})


def test_persisted_invalid_config_does_not_coerce(tmp_path, monkeypatch):
    p = tmp_path / 'moe.json'
    p.write_text('{"orchestrator":"machx","advisors":["codex"],"legal_review":"false"}')
    monkeypatch.setattr(moe, 'CONFIG_PATH', p)
    assert moe.load_config() is None


def test_choices_report_only_local_prerequisites(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setattr('shutil.which', lambda binary: None)
    choices = {item['key']: item for item in module().provider_choices()}
    assert choices['machx']['available'] is True
    assert choices['codex']['available'] is False
    assert choices['openai']['available'] is False
    assert 'not' in choices['machx']['note'].lower()
    assert set(choices['machx']) == {'key', 'label', 'available', 'note', 'efforts', 'effort_note', 'models', 'model_note'}


def test_effort_settings_preserve_supported_native_levels():
    cfg = module().parse_config(dict(orchestrator='anthropic', advisors=['codex'],
        orchestrator_effort='max', advisor_efforts={'codex': 'xhigh'}))
    assert cfg.orchestrator_effort == 'max' and cfg.advisor_efforts == {'codex': 'xhigh'}
    for field in ({'orchestrator_effort': 'ultra'}, {'advisor_efforts': {'codex': 'invented'}},
                  {'advisor_efforts': {'grok': 'high'}}):
        with pytest.raises(ValueError):
            module().parse_config(dict(orchestrator='anthropic', advisors=['codex'], **field))


def test_huge_timeout_is_validation_error():
    with pytest.raises(ValueError):
        module().parse_config({'orchestrator': 'machx', 'advisors': [], 'timeout_seconds': 10 ** 500})
