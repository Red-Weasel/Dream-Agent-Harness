"""Strict Council configuration and local prerequisite metadata. No model probes."""
from __future__ import annotations

import math
import shutil
from dataclasses import fields

from .moe import MoeConfig
from .model_catalog import model_choices, model_efforts
from .profiles import shown
from .providers import PROVIDERS


class ConfigError(ValueError):
    """A refusal of a Council configuration that names the entry it is about (DREAM-148): `key` is that entry as it
    may be shown ("advisors", "advisor_models.codex", an unknown field through profiles.shown). The message is the
    same as before, so every caller that shows it is unchanged."""

    def __init__(self, key: str, message: str):
        super().__init__(message)
        self.key = key


def _at(key: str, check, *args, **kwargs):
    """`check(*args, **kwargs)`, whose refusal names `key`."""
    try:
        return check(*args, **kwargs)
    except ValueError as exc:
        raise ConfigError(key, str(exc)) from None


def validate_model(value: object, *, allow_empty: bool = False) -> str:
    # One rule for a model value (DREAM-147): printable text -- str.isprintable() refuses C0 and C1 controls, DEL,
    # format characters (bidi overrides, zero-width joiners), line and paragraph separators and lone surrogates.
    if not isinstance(value, str) or len(value) > 256 or not value.isprintable():
        raise ValueError('Model must be a string of at most 256 characters of printable text (no control, '
                         'invisible or unencodable characters)')
    value = value.strip()
    if not value and not allow_empty:
        raise ValueError('Advisor model must not be empty')
    return value


def parse_config(data: object) -> MoeConfig:
    """Validate a complete selection without checking authentication or connecting. A refusal is a ConfigError
    naming the entry (DREAM-148), so an unusable var/moe.json can be reported by its key (moe.config_problem)."""
    if not isinstance(data, dict):
        raise ValueError('Council config must be an object')
    unknown = set(data) - {f.name for f in fields(MoeConfig)}
    if unknown:
        raise ConfigError(', '.join(sorted(shown(key) for key in unknown)), 'Unknown Council configuration field')
    main = data.get('orchestrator')
    if not isinstance(main, str) or main not in PROVIDERS:
        raise ConfigError('orchestrator', 'Choose a known Council main provider')
    advisors = data.get('advisors')
    if not isinstance(advisors, list) or len(advisors) > 32:
        raise ConfigError('advisors', 'Advisors must be a list of at most 32 provider keys')
    if any(not isinstance(key, str) or key not in PROVIDERS for key in advisors):
        raise ConfigError('advisors', 'Choose known advisor providers')
    if len(set(advisors)) != len(advisors):
        raise ConfigError('advisors', 'Advisors must be unique')
    concurrency = data.get('max_concurrency', 3)
    timeout = data.get('timeout_seconds', 90.0)
    legal = data.get('legal_review', False)
    models = data.get('advisor_models', {})
    if type(concurrency) is not int or not 1 <= concurrency <= 32:
        raise ConfigError('max_concurrency', 'Council concurrency must be an integer from 1 to 32')
    if type(timeout) not in (int, float) or not 0 < timeout <= 3600 or not math.isfinite(timeout):
        raise ConfigError('timeout_seconds', 'Council timeout must be a finite number above 0 and at most 3600 seconds')
    if type(legal) is not bool:
        raise ConfigError('legal_review', 'Legal review must be true or false')
    if not isinstance(models, dict) or any(key not in advisors for key in models):
        raise ConfigError('advisor_models', 'Advisor models must only name selected advisors')
    # The keys are selected advisors by now (known provider keys), so they are safe to name.
    models = {key: _at(f'advisor_models.{key}', validate_model, value) for key, value in models.items()}
    main_effort = data.get('orchestrator_effort')
    efforts = data.get('advisor_efforts', {})
    if not isinstance(efforts, dict) or any(key not in advisors for key in efforts):
        raise ConfigError('advisor_efforts', 'Advisor efforts must only name selected advisors')
    if main_effort is not None:
        _at('orchestrator_effort', validate_effort, main, main_effort, structural_only=True)
    for key, value in efforts.items():
        _at(f'advisor_efforts.{key}', validate_effort, key, value, models.get(key), structural_only=True)
    return MoeConfig(main, list(advisors), concurrency, float(timeout), legal, models,
                     main_effort, dict(efforts))


def provider_choices(*, model=None, capabilities=None) -> list[dict]:
    """Availability means local prerequisites only, never authenticated readiness."""
    choices = []
    for key, provider in PROVIDERS.items():
        if key == 'machx':
            available, note = True, 'Attach to an existing server only; model readiness not checked.'
        elif provider.kind == 'cli':
            available = bool(shutil.which(provider.cli_cmd or key))
            note = 'CLI installed; sign-in not checked.' if available else 'Install the provider CLI; sign-in not checked.'
        elif provider.kind == 'anthropic':
            available, note = True, 'Claude SDK available; sign-in and model readiness not checked.'
        else:
            available = bool(provider.api_key())
            note = 'API key configured; access not checked.' if available else f'Set {provider.api_key_env}; access not checked.'
        levels = effort_choices(key, model=model, capabilities=capabilities if key == 'machx' else None)
        effort_note = ('Captured local model capability; no probe.' if key == 'machx' else
                       'Native adapter effort values; model support is checked by the provider.') if levels else 'Effort is unavailable or not reported for this provider/model.'
        choices.append(dict(key=key, label=provider.label, available=available, note=note,
                            efforts=levels, effort_note=effort_note, models=model_choices(key),
                            model_note=('Codex cache or bundled model suggestions; account access is not checked.'
                                        if key == 'codex' else
                                        'Known model suggestions; account access is not checked. Custom IDs remain supported.')))
    return choices


def effort_choices(provider: str, model=None, capabilities=None) -> list[str]:
    known = model_efforts(provider, model)
    if known is not None:
        return known
    if provider == 'anthropic':
        return ['low', 'medium', 'high', 'xhigh', 'max']
    if provider == 'codex':
        return ['low', 'medium', 'high', 'xhigh', 'max', 'ultra']
    if provider in ('openai', 'xai'):
        return ['medium', 'high', 'xhigh']
    if provider == 'machx':
        from .capabilities import capability_report, ordered_reasoning_levels
        from ..local.settings import read_session_capabilities
        report = capabilities if capabilities is not None else capability_report(
            machx_capabilities=read_session_capabilities(model))
        return list(ordered_reasoning_levels(report))
    return []


def validate_effort(provider: str, value: object, model=None, *, structural_only=False,
                    capabilities=None) -> str:
    # Main model is a separate request field; exact local validation happens at
    # configure time. Persisted config validation never assumes model readiness.
    levels = effort_choices(provider, model, capabilities)
    if structural_only and provider == 'machx':
        from ..local.settings import BY_NAME
        levels = BY_NAME['reasoning_effort'].choices
    if not isinstance(value, str) or value not in levels:
        raise ValueError(f'Effort {value!r} is not supported/reported for {provider} and the selected model')
    return value
