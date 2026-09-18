"""Shared parsing for central numeric settings; no runtime imports or state I/O."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
import os
from types import MappingProxyType


@dataclass(frozen=True)
class NumericSetting:
    kind: type[int] | type[float]
    default: str
    unlimited: tuple[str, ...] = ()


NUMERIC_SETTINGS = MappingProxyType({
    'DREAM_MEMORY_FILE_MAX': NumericSetting(int, '8000'),
    'DREAM_RERANK_CANDIDATES': NumericSetting(int, '50'),
    'DREAM_SALIENCE_HALFLIFE': NumericSetting(float, '30'),
    'DREAM_ANN_THRESHOLD': NumericSetting(int, '400'),
    'DREAM_CHUNK_THRESHOLD': NumericSetting(int, '1200'),
    'DREAM_CHUNK_SIZE': NumericSetting(int, '600'),
    'DREAM_MERGE_THRESHOLD': NumericSetting(float, '0.93'),
    'DREAM_RECONCILE_THRESHOLD': NumericSetting(float, '0.83'),
    'DREAM_RECONCILE_MAX_CLUSTERS': NumericSetting(int, '4'),
    'DREAM_RECONCILE_EXCERPT': NumericSetting(int, '1200'),
    'DREAM_LINK_RESERVE': NumericSetting(int, '2'),
    'DREAM_AUTOLINK_THRESHOLD': NumericSetting(float, '0.75'),
    'DREAM_AUTOLINK_K': NumericSetting(int, '3'),
    'DREAM_SEARXNG_PORT': NumericSetting(int, '8888'),
    'DREAM_BROWSER_TIMEOUT_MS': NumericSetting(int, '30000'),
    'DREAM_BROWSER_IDLE_S': NumericSetting(int, '300'),
    'DREAM_CTX_WINDOW': NumericSetting(int, '200000'),
    'DREAM_GUI_PORT': NumericSetting(int, '0'),
    'DREAM_TOOL_STALE_DAYS': NumericSetting(int, '30'),
    'DREAM_TOOL_BUDGET': NumericSetting(int, '0', ('0', 'none', 'unlimited', '')),
    'DREAM_MAX_TOKENS': NumericSetting(int, '131072'),
    'DREAM_SUBAGENT_ROUNDS': NumericSetting(int, '60'),
    'DREAM_MAX_TOOL_ROUNDS': NumericSetting(int, '100'),
    'DREAM_FREQUENCY_PENALTY': NumericSetting(float, '0.3'),
    'DREAM_PRESENCE_PENALTY': NumericSetting(float, '0.0'),
    'DREAM_REPETITION_PENALTY': NumericSetting(float, '1.05'),
    'DREAM_HTTP_TIMEOUT_S': NumericSetting(float, '20'),
    'DREAM_LLM_READ_TIMEOUT_S': NumericSetting(float, 'none', ('0', 'none', '')),
})


class NumericEnvironmentError(ValueError):
    """Only a reviewed setting name and fixed guidance, never its supplied value."""
    def __init__(self, name: str):
        spec = NUMERIC_SETTINGS[name]
        self.name = name
        self.code = 'environment_integer_invalid' if spec.kind is int else 'environment_float_invalid'
        expected = 'an integer' if spec.kind is int else 'a finite number'
        suffix = ' or a documented unlimited alias' if spec.unlimited else ''
        super().__init__(f'{name} must be {expected}{suffix}. Unset {name} to use its default.')


def read_numeric(name: str, *, environ: Mapping[str, str] | None = None) -> int | float | None:
    spec = NUMERIC_SETTINGS[name]
    raw = (os.environ if environ is None else environ).get(name, spec.default)
    if not isinstance(raw, str) or len(raw) > 1024:
        raise NumericEnvironmentError(name)
    if raw.lower() in spec.unlimited:
        return None
    try:
        value = spec.kind(raw)
    except (ValueError, OverflowError):
        raise NumericEnvironmentError(name) from None
    if isinstance(value, float) and not math.isfinite(value):
        raise NumericEnvironmentError(name)
    return value


def numeric_environment_errors(*, environ: Mapping[str, str] | None = None) -> list[NumericEnvironmentError]:
    errors = []
    for name in NUMERIC_SETTINGS:
        try:
            read_numeric(name, environ=environ)
        except NumericEnvironmentError as error:
            errors.append(error)
    return errors
