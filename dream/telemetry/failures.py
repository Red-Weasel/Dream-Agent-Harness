"""Bounded message signals, not root-cause attribution. Never retain input text."""
from __future__ import annotations


def _text(value):
    if isinstance(value, str):
        return value[:8192].lower()
    if isinstance(value, list):
        return ' '.join(item['text'][:2048] for item in value[:4]
                        if isinstance(item, dict) and item.get('type') == 'text'
                        and isinstance(item.get('text'), str)).lower()
    return ''


def failure_signal(kind, data):
    """Return one fixed category for an explicit failure event, or None.

    Error/result pairs count as two observations, not two unique incidents.
    Unknown flags and successful messages containing error words are excluded.
    """
    payload = data if isinstance(data, dict) else {}
    if kind == 'tool_result':
        if payload.get('is_error') is not True:
            return None
        text = _text(payload.get('content'))
    elif kind == 'error':
        text = _text(payload.get('message', payload.get('error'))) if isinstance(data, dict) else _text(data)
    elif kind == 'result':
        subtype = payload.get('subtype')
        if isinstance(subtype, str) and subtype in {'interrupted', 'cancelled'}:
            return 'interrupted'
        if payload.get('is_error') is not True:
            return None
        text = _text(payload.get('error'))
    else:
        return None
    if kind in {'error', 'result'}:
        if any(signal in text for signal in ('rate limit', 'rate_limit', 'http 429', 'status 429')):
            return 'provider_rate_limit'
        if any(signal in text for signal in ('connection reset', 'connection refused',
                'connection error', 'connecterror', 'readtimeout', 'timed out', 'stream disconnected')):
            return 'provider_connection'
    for category, signals in (
        ('arguments', ('invalid arguments', 'schema validation', 'validation error', 'unknown tool')),
        ('sandbox', ('sandbox unavailable', 'sandbox is unavailable', 'sandbox denied',
                     'outside workspace', 'permission denied', 'declined by the user')),
        ('dependency', ('error while loading shared libraries', 'command not found', 'modulenotfounderror',
                        'no module named', 'required inspection tools unavailable')),
        ('workspace', ('no such file or directory', 'filenotfounderror')),
        ('preview', ('preview failed', 'media decode', 'unsupported codec', 'preview unavailable')),
    ):
        if any(signal in text for signal in signals):
            return category
    return 'unknown'
