"""Pure, explicit capability normalization. Never contacts a provider or loads a model."""
from __future__ import annotations

import re


_FACTS = ('tool_calling', 'vision', 'context_tokens', 'reasoning_levels', 'concurrency', 'cancellation')
_LEVEL = re.compile(r'[a-z][a-z0-9_-]{0,31}\Z')


def _levels(value: object) -> bool:
    return (isinstance(value, list) and len(value) <= 16
            and all(isinstance(v, str) and _LEVEL.fullmatch(v) for v in value)
            and len(set(value)) == len(value))


def capability_report(*, provider_metadata=None, machx_capabilities=None,
                      machx_props=None, settings=None) -> dict:
    """Merge supplied facts; contradictory claims become unknown with a warning.

    Provider metadata uses the six normalized field names. Reasoning levels are
    explicitly ordered least to most effort by the adapter, never sorted here.
    MachX accepts its existing load/sampling, reasoning and /props schema.
    Settings describe configured limits, not proof of server capabilities.
    """
    report = {'schema_version': 1, **{key: {'known': False, 'value': None, 'source': 'unreported'}
                                      for key in _FACTS}, 'configured': {}, 'warnings': []}
    conflicts = set()

    def warn(code):
        if code not in report['warnings']:
            report['warnings'].append(code)

    def mapping(value, source):
        if value is None:
            return {}
        if not isinstance(value, dict):
            warn('malformed.' + source)
            return {}
        return value

    def fact(key, value, source):
        valid = (_levels(value) if key == 'reasoning_levels' else
                 type(value) is int and 0 < value <= 2**53 - 1 if key in ('context_tokens', 'concurrency') else
                 type(value) is bool)
        if not valid:
            warn('malformed.' + source)
            return
        previous = report[key]
        if key in conflicts:
            return
        if previous['known'] and previous['value'] != value:
            conflicts.add(key)
            report[key] = {'known': False, 'value': None, 'source': 'conflicting reports'}
            warn('conflicting.' + key)
        elif not previous['known']:
            report[key] = {'known': True, 'value': list(value) if isinstance(value, list) else value, 'source': source}

    metadata = mapping(provider_metadata, 'provider.metadata')
    for key in _FACTS:
        if key in metadata:
            fact(key, metadata[key], 'provider.metadata.' + key)

    caps = mapping(machx_capabilities, 'machx.capabilities')
    features = mapping(caps.get('features'), 'machx.capabilities.features')
    if 'vision' in features:
        # MachX declares architecture support, not loaded projector readiness.
        fact('vision', features['vision'], 'machx.capabilities.features.vision')
    advertised = set()
    for key in ('load', 'sampling'):
        if key in caps:
            names = caps[key]
            if isinstance(names, list) and len(names) <= 256 and all(isinstance(n, str) for n in names):
                advertised.update(names)
            else:
                warn('malformed.machx.capabilities.' + key)
    reasoning = mapping(caps.get('reasoning'), 'machx.capabilities.reasoning')
    if 'effort_levels' in reasoning:
        levels = reasoning['effort_levels']
        # Match the accepted local request vocabulary. No inferred "medium" or
        # unimplemented "ultra" value reaches local request controls.
        from ..local.settings import BY_NAME
        if not _levels(levels) or any(v not in BY_NAME['reasoning_effort'].choices for v in levels):
            warn('malformed.machx.capabilities.reasoning.effort_levels')
        elif 'reasoning_effort' in advertised:
            fact('reasoning_levels', levels, 'machx.capabilities.reasoning.effort_levels')
        elif levels:
            warn('unadvertised.machx.capabilities.reasoning_effort')

    props = mapping(machx_props, 'machx.props')
    generation = mapping(props.get('default_generation_settings'), 'machx.props.default_generation_settings')
    if 'n_ctx' in generation:
        fact('context_tokens', generation['n_ctx'], 'machx.props.default_generation_settings.n_ctx')

    configured = mapping(settings, 'settings')
    for key in ('context_limit', 'parallel', 'max_parallel', 'output_tokens', 'max_tokens'):
        if key in configured and configured[key] is not None:
            value = configured[key]
            if type(value) is int and 0 < value <= 2**53 - 1:
                report['configured'][key] = value
            else:
                warn('malformed.settings.' + key)
    return report


def ordered_reasoning_levels(report: dict) -> tuple[str, ...]:
    """Return only a validated explicit ladder suitable for PerformanceModes."""
    fact = report.get('reasoning_levels', {}) if isinstance(report, dict) else {}
    if not isinstance(fact, dict) or fact.get('known') is not True or not _levels(fact.get('value')):
        return ()
    return tuple(fact['value'])
