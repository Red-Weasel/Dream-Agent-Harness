"""Reasoning-effort levels, mapped to each backend's native knob.

One ladder — med / high / xhigh / max — plus ``ultra``, which shares ``max``'s
ceiling and additionally signals a fan-out intent (see :func:`is_ultra`). The
tier constants live here; the engine and backends translate the fragments these
functions return into their own request payloads (integration, not this module).
"""

from __future__ import annotations

LEVELS = ("med", "high", "xhigh", "max", "ultra")

# Spellings folded onto a canonical level by normalize().
_ALIASES = {"medium": "med"}

# OpenAI's reasoning_effort accepts low/medium/high/xhigh; its ladder tops out at
# "xhigh", so both max and ultra clamp there.
_OPENAI = {
    "med": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "xhigh",
    "ultra": "xhigh",
}

# Anthropic extended-thinking budgets, in tokens. ultra shares max's ceiling and
# carries the fan-out intent separately (is_ultra), not a bigger budget.
_THINKING_TOKENS = {
    "med": 4096,
    "high": 8192,
    "xhigh": 16384,
    "max": 32768,
    "ultra": 32768,
}

# Short labels for the status panel.
_LABELS = {
    "med": "medium",
    "high": "high",
    "xhigh": "extra-high",
    "max": "max",
    "ultra": "ultra (fan-out)",
}


def normalize(level: str) -> str | None:
    """Canonicalize a user-typed effort level; None if unrecognized.

    Trims, lowercases, and folds aliases ("medium" → "med").
    """
    key = (level or "").strip().lower()
    key = _ALIASES.get(key, key)
    return key if key in LEVELS else None


def is_ultra(level: str) -> bool:
    """True only for the ``ultra`` tier — the signal to fan out to subagents."""
    return normalize(level) == "ultra"


def _require(level: str) -> str:
    """Normalize or raise — the gate the payload/label helpers share."""
    norm = normalize(level)
    if norm is None:
        raise ValueError(
            f"unknown effort level {level!r}; expected one of {', '.join(LEVELS)}"
        )
    return norm


def for_openai(level: str) -> dict:
    """OpenAI ``reasoning_effort`` payload fragment.

    ``med`` → "medium"; ``max`` and ``ultra`` clamp to "xhigh" (top of the ladder).
    """
    return {"reasoning_effort": _OPENAI[_require(level)]}


def for_anthropic(level: str) -> dict:
    """Anthropic extended-thinking payload fragment (``thinking_budget_tokens``).

    ``ultra`` shares ``max``'s token ceiling.
    """
    return {"thinking_budget_tokens": _THINKING_TOKENS[_require(level)]}


def describe(level: str) -> str:
    """Short human label for the status panel."""
    return _LABELS[_require(level)]
