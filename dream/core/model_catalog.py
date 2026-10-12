"""Offline Council model suggestions, not account access or readiness checks.

Bundled choices reflect requested labels and conventional IDs, not verified
vendor availability, account access or installed CLI capabilities. Native
providers validate effort suggestions. Codex cache metadata narrows requested
efforts when a matching entry exists; missing entries remain suggestions.
Runtime only reads that cache; it never refreshes it or starts a provider.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path

_NATIVE = ['low', 'medium', 'high', 'xhigh', 'max']
_HTTP = ['medium', 'high', 'xhigh']  # Values Dream's existing HTTP adapter forwards.


def _choice(model: str, label: str, efforts: list[str]) -> dict:
    return dict(id=model, label=label, efforts=list(efforts))


_CODEX = [
    _choice(model.lower(), model, _NATIVE) for model in (
        'GPT-6.1-Sol', 'GPT-6-Astra', 'GPT-6-Sol', 'GPT-6-Luna',
        'GPT-5.6-Sol', 'GPT-5.6-Terra', 'GPT-5.6-Luna')
]
_CATALOG = {
    'anthropic': [
        _choice('claude-' + label.lower().replace(' ', '-').replace('.', '-'), label, _NATIVE)
        if label != 'Haiku 4.5' else _choice('claude-haiku-4-5-20251001', label, _NATIVE)
        for label in ('Opus 5.5', 'Fable 5.1', 'Sonnet 5.5', 'Haiku 4.5',
                      'Sonnet 5', 'Opus 5', 'Fable 5', 'Opus 4.8', 'Opus 4.7',
                      'Opus 4.6', 'Sonnet 4.6')
    ],
    'openai': [_choice(model, label, _HTTP) for model, label in (
        ('gpt-6-astra', 'GPT-6-Astra'), ('gpt-5.6-sol', 'GPT-5.6-Sol'),
        ('gpt-5.6-terra', 'GPT-5.6-Terra'), ('gpt-5.6-luna', 'GPT-5.6-Luna'))],
    'xai': [_choice('grok-4.6', 'Grok 4.6', _HTTP)],
    'grok': [_choice(model, label, _NATIVE[:4]) for model, label in (
        ('grok-4.7', 'Grok 4.7'), ('grok-4.7-fast', 'Grok 4.7 Fast'),
        ('grok-4.6', 'Grok 4.6'), ('grok-4.5', 'Grok 4.5'))],
    'antigravity': [
        _choice('gemini-3.8-flash-high', 'Gemini-3.8-Flash · High', _NATIVE),
        _choice('gemini-3.8-flash-medium', 'Gemini-3.8-Flash · Medium', _NATIVE),
        _choice('gemini-3.8-flash-low', 'Gemini-3.8-Flash · Low', _NATIVE),
        _choice('gemini-3.1-pro-high', 'Gemini-3.1 Pro · High', _NATIVE),
        _choice('gemini-3.1-pro-low', 'Gemini-3.1 Pro · Low', _NATIVE),
    ],
    'gemini': [
        _choice('gemini-3.5-flash-lite', 'Gemini-3.5-Flash-Lite', ['off', 'on']),
        _choice('gemini-3.8-flash', 'Gemini-3.8-Flash', ['off', 'on']),
        _choice('gemini-3.1-pro-preview', 'Gemini-3.1 Pro', ['off', 'on']),
    ],
}


def _safe_text(value: object) -> bool:
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= 256
            and all(ord(c) >= 32 and ord(c) != 127 for c in value))


def _cached_codex() -> list[dict]:
    home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    try:
        # Bound reads of local metadata, including malformed caches.
        with (home / 'models_cache.json').open('rb') as stream:
            raw = stream.read(2_000_001)
        if len(raw) > 2_000_000:
            return []
        data = json.loads(raw)
    except (OSError, ValueError):
        return []
    models = data.get('models') if isinstance(data, dict) else None
    if not isinstance(models, list):
        return []
    choices = {}
    for model in models:
        if not isinstance(model, dict):
            continue
        slug = model.get('slug')
        # Requested labels stay visible, but matching capability metadata is
        # authoritative even when the CLI hides that entry from its own picker.
        if model.get('visibility') != 'list' and not any(choice['id'] == slug for choice in _CODEX):
            continue
        if not _safe_text(slug) or slug in choices:
            continue
        label = model.get('display_name')
        levels = model.get('supported_reasoning_levels', [])
        levels = levels if isinstance(levels, list) else []
        efforts = [level for level in [*_NATIVE, 'ultra'] if any(
            isinstance(item, dict) and item.get('effort') == level for item in levels)]
        choices[slug] = _choice(slug, label if _safe_text(label) else slug, efforts)
    return list(choices.values())


def model_choices(provider: str) -> list[dict]:
    """Known models only; callers preserve custom and previously saved IDs."""
    if provider == 'codex':
        cached = {choice['id']: choice for choice in _cached_codex()}
        choices = deepcopy(_CODEX)
        for choice in choices:
            known = cached.pop(choice['id'], None)
            if known is not None:
                choice['efforts'] = [effort for effort in _NATIVE if effort in known['efforts']]
        return choices + list(cached.values())
    return deepcopy(_CATALOG.get(provider, []))


def model_efforts(provider: str, model: str | None) -> list[str] | None:
    """None means unknown; an empty list means no supported effort control."""
    if model:
        for choice in model_choices(provider):
            if choice['id'] == model:
                return choice['efforts']
    return None
