"""Small display-only worker records, shared by live events and reconnect history."""
from __future__ import annotations

import math
import re
from itertools import islice

# A data URL's payload, with the line breaks a wrapped one has: a continuation line must be 40+ characters, so the
# words after the payload ("The answer is 42.") are not taken for more of it (gate P2 follow-up).
_MEDIA = re.compile(r'data:[^\s,]{0,150};base64,\s*[A-Za-z0-9+/=]+(?:\s+[A-Za-z0-9+/=]{40,})*', re.IGNORECASE)
# An unbroken run of base64 or base64url characters (the gate P2 follow-up added - and _: a 400-character
# base64url token showed whole), and a MIME-wrapped block: four or more lines that are nothing but 60+ such
# characters, as a mailer wraps an attachment (no data: prefix, so _MEDIA does not see it). The block starts at the
# start of a line (gate P3 round 1): unanchored, a long run with no newline was tried from every position, quadratic.
_UNBROKEN = re.compile(r'[A-Za-z0-9+/=_-]{256,}')
_WRAPPED = re.compile(r'(?m)^(?:[A-Za-z0-9+/=]{60,}\r?\n){4,}')
_KINDS = {'status', 'request', 'response', 'thinking_report', 'tool_use', 'tool_result', 'text_delta', 'thinking_delta'}


def _text(value, limit):
    if not isinstance(value, str):
        return ''
    # Redact the whole value, then bound it: a payload longer than the display limit must not hide the words
    # after it (gate P2 follow-up: a 13,000-character image before "The answer is 42." showed only the marker).
    value = _MEDIA.sub('[Media payload omitted]', value)
    value = _WRAPPED.sub('[Encoded block omitted]', value)
    value = _UNBROKEN.sub('[Long unbroken value omitted]', value)
    return value[:limit] + ('\n[Display truncated]' if len(value) > limit else '')


def _count(value):
    """A non-negative int (never a bool, a float or an absurd number); None otherwise."""
    return value if type(value) is int and 0 <= value <= 10 ** 12 else None


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
        # A worker's reply (ADR-068, DREAM-189) gets the room its reasoning has; a streamed piece of either
        # (DREAM-190) is cut at the size its coalescer sends; the lead holds the whole of it.
        cap = (4000 if row['kind'] in {'text_delta', 'thinking_delta'}
               else 12000 if row['kind'] in {'thinking_report', 'response'} else 1000)
        row['text'] = _text(value['text'], cap)
    # Worker facts (DREAM-189): the model a run's requests go to, the server's counts and wall time of a response,
    # the run's context fill {used, window}. Only well-formed numbers; nothing is filled in for a missing one.
    if isinstance(value.get('model'), str) and value['model']:
        row['model'] = _text(value['model'], 200)
    duration = _count(value.get('duration_ms'))
    if duration is not None:
        row['duration_ms'] = duration
    usage = value.get('usage')
    if isinstance(usage, dict):
        counts = {key: usage[key] for key in ('prompt_tokens', 'completion_tokens') if _count(usage.get(key)) is not None}
        if counts:
            row['usage'] = counts
    context = value.get('context')
    if isinstance(context, dict) and all(_count(context.get(key)) is not None for key in ('used', 'window')):
        row['context'] = {'used': context['used'], 'window': context['window']}
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
