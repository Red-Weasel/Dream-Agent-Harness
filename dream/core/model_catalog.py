"""Offline Council model suggestions, not account access or readiness checks.

Bundled text/code catalogs verified 2026-09-11 against:
https://platform.claude.com/docs/en/models/overview
https://platform.claude.com/docs/en/build-with-claude/effort
https://developers.openai.com/api/docs/models
https://docs.x.ai/developers/models
https://ai.google.dev/gemini-api/docs/models
Codex fallback is a snapshot of installed models_cache.json on that date.
Runtime only reads that cache; it never refreshes it or starts a provider.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path

_NATIVE = ['low', 'medium', 'high', 'xhigh', 'max', 'ultra']
_HTTP = ['medium', 'high', 'xhigh']  # Values Dream's existing HTTP adapter forwards.


def _choice(model: str, label: str, efforts: list[str]) -> dict:
    return dict(id=model, label=label, efforts=list(efforts))


_CODEX = [
    _choice('gpt-6-astra', 'GPT-6 Astra', _NATIVE),
    _choice('gpt-5.6-sol', 'GPT-5.6 Sol', _NATIVE),
    _choice('gpt-5.6-terra', 'GPT-5.6 Terra', _NATIVE),
    _choice('gpt-5.6-luna', 'GPT-5.6 Luna', _NATIVE[:-1]),
    _choice('gpt-5.5', 'GPT-5.5', _NATIVE[:4]),
    _choice('gpt-5.3-codex-spark', 'GPT-5.3 Codex Spark', _NATIVE[:4]),
]
_CATALOG = {
    'anthropic': [
        _choice('claude-fable-5-1', 'Claude Fable 5.1', _NATIVE[:-1]),
        _choice('claude-opus-5', 'Claude Opus 5', _NATIVE[:-1]),
        _choice('claude-sonnet-5', 'Claude Sonnet 5', _NATIVE[:-1]),
        _choice('claude-haiku-4-5-20251001', 'Claude Haiku 4.5', []),
    ],
    'openai': [_choice(m['id'], m['label'], _HTTP) for m in _CODEX[:4]],
    'xai': [_choice('grok-4.6', 'Grok 4.6', _HTTP)],
    'grok': [_choice('grok-4.6', 'Grok 4.6', [])],
    'gemini': [
        _choice('gemini-3.1-pro-preview', 'Gemini 3.1 Pro Preview', []),
        *[_choice(f'gemini-{v}-flash', f'Gemini {v} Flash', [])
          for v in ('3.8', '3.7', '3.6', '3.5')],
        _choice('gemini-3.5-flash-lite', 'Gemini 3.5 Flash Lite', []),
        _choice('gemini-3.1-flash-lite', 'Gemini 3.1 Flash Lite', []),
        _choice('gemini-3-flash-preview', 'Gemini 3 Flash Preview', []),
        *[_choice(f'gemini-2.5-{v}', f'Gemini 2.5 {label}', [])
          for v, label in [('pro', 'Pro'), ('flash', 'Flash'), ('flash-lite', 'Flash Lite')]],
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
        if not isinstance(model, dict) or model.get('visibility') != 'list':
            continue
        slug = model.get('slug')
        if not _safe_text(slug) or slug in choices:
            continue
        label = model.get('display_name')
        levels = model.get('supported_reasoning_levels', [])
        levels = levels if isinstance(levels, list) else []
        efforts = [level for level in _NATIVE if any(
            isinstance(item, dict) and item.get('effort') == level for item in levels)]
        choices[slug] = _choice(slug, label if _safe_text(label) else slug, efforts)
    return list(choices.values())


def model_choices(provider: str) -> list[dict]:
    """Known models only; callers preserve custom and previously saved IDs."""
    if provider == 'codex':
        return _cached_codex() or deepcopy(_CODEX)
    return deepcopy(_CATALOG.get(provider, []))


def model_efforts(provider: str, model: str | None) -> list[str] | None:
    """None means unknown; an empty list means no supported effort control."""
    if model:
        for choice in model_choices(provider):
            if choice['id'] == model:
                return choice['efforts']
    return None
