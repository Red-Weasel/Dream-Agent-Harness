"""Small display-only worker records, shared by live events and reconnect history."""
from __future__ import annotations

import math
import re
from itertools import islice

_MEDIA = re.compile(r'data:[^\s,]{0,150};base64,[A-Za-z0-9+/=\s]+', re.IGNORECASE)
_UNBROKEN = re.compile(r'[A-Za-z0-9+/=]{256,}')
_KINDS = {'status', 'request', 'response', 'thinking_report', 'tool_use', 'tool_result'}


def _text(value, limit):
    if not isinstance(value, str):
        return ''
    # Scan only the bounded prefix. Large binary-looking values are not useful
    # activity text, even if the provider forgot a data-URL prefix.
    value = value[:limit + 512]
    value = _MEDIA.sub('[Media payload omitted]', value)
    value = _UNBROKEN.sub('[Long unbroken value omitted]', value)
    return value[:limit] + ('\n[Display truncated]' if len(value) > limit else '')


def bounded_agent_activity(value):
    """Whitelist the display contract; never retain unused response/image fields."""
    if not isinstance(value, dict) or value.get('kind') not in _KINDS:
        return None
    row = {key: _text(value.get(key), cap) for key, cap in
           (('run_id', 100), ('agent', 100), ('phase', 80), ('kind', 40))}
    if not row['run_id'] or not row['agent']:
        return None
    for key in ('round', 'request_index'):
        number = value.get(key)
        if type(number) is int and 0 <= number <= 10000:
            row[key] = number
    if isinstance(value.get('status'), str):
        row['status'] = _text(value['status'], 80)
    if isinstance(value.get('text'), str):
        row['text'] = _text(value['text'], 12000 if row['kind'] == 'thinking_report' else 1000)
    if row['kind'] == 'thinking_report':
        row['reported_after_response'] = True
    if row['kind'] in {'tool_use', 'tool_result'}:
        data = value.get('data') if isinstance(value.get('data'), dict) else {}
        row['data'] = {key: _text(data.get(key), cap) for key, cap in (('id', 240), ('name', 150))}
        if row['kind'] == 'tool_result':
            row['data']['content'] = _text(data.get('content'), 20000)
            row['data']['is_error'] = bool(data.get('is_error'))
        else:
            remaining = [6000, 128]
            def display(item, depth=0):
                remaining[1] -= 1
                if remaining[1] < 0 or remaining[0] <= 0 or depth > 4:
                    return '[Display truncated]'
                if isinstance(item, str):
                    result = _text(item, min(2000, remaining[0]))
                    remaining[0] -= len(result)
                    return result
                if item is None or type(item) is bool:
                    return item
                if type(item) is int:
                    return item if item.bit_length() <= 256 else '[Oversized number omitted]'
                if type(item) is float:
                    return item if math.isfinite(item) else '[Non-finite number omitted]'
                if isinstance(item, dict):
                    return {_text(key, 100): ('[Credential omitted]' if str(key).lower() in
                            {'password', 'secret', 'api_key', 'authorization', 'access_token'} else display(val, depth + 1))
                            for key, val in islice(item.items(), 32) if isinstance(key, str)}
                if isinstance(item, (list, tuple)):
                    return [display(val, depth + 1) for val in item[:32]]
                return '[Non-text value omitted]'
            row['data']['input'] = display(data.get('input'))
    return row
