"""Read-only, bounded presentation of an owned Workflow terminal's activity."""
from __future__ import annotations

import asyncio
import json
import math
import time
from urllib.parse import urlsplit
import httpx

LIMIT = 262_144
KINDS = frozenset(('user', 'text_delta', 'thinking_delta', 'assistant_done',
    'tool_use', 'tool_result', 'system', 'error', 'result', 'turn_start', 'turn_end',
    'turn_timing', 'background_work', 'agent_activity', 'council_activity', 'plan'))

DRAWER_ITEMS = 100
DRAWER_SECTION_BYTES = 12_000
_DRAWER_FIELDS = {
    'changes': {'path': 4096},
    'outputs': {'id': 100, 'title': 200, 'type': 40, 'path': 4096, 'shown_by': 40},
    'agents': {'id': 100, 'name': 200, 'model': 200, 'state': 80},
    'sources': {'id': 100, 'name': 200, 'path': 4096},
}
_DRAWER_REASONS = {
    'changes': 'Confirmed file changes are unavailable for this session.',
    'outputs': 'Registered outputs are unavailable for this session.',
    'agents': 'Recorded agents are unavailable for this session.',
    'sources': 'No per-session submitted-attachment ledger is available.',
}


def drawer_metadata(value):
    """Metadata only. Paths and IDs retain their identity; never supply open URLs."""
    value = value if isinstance(value, dict) else {}
    session_id = value.get('session_id')
    result = {'session_id': session_id if isinstance(session_id, str) and len(session_id) <= 256 else ''}
    for name, fields in _DRAWER_FIELDS.items():
        section = value.get(name)
        if not isinstance(section, dict) or section.get('available') is not True or not isinstance(section.get('items'), list):
            result[name] = {'available': False, 'items': [], 'total': 0, 'trimmed': False,
                            'reason': _DRAWER_REASONS[name]}
            continue
        source = section['items']
        total = section.get('total')
        total = max(len(source), total) if type(total) is int and 0 <= total <= 10**12 else len(source)
        trimmed = bool(section.get('trimmed')) or total > len(source) or len(source) > DRAWER_ITEMS
        rows, size = [], 0
        required = ('path',) if name == 'changes' else ('id', 'path') if name in ('outputs', 'sources') else ('id',)
        for item in source[:DRAWER_ITEMS]:
            if not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k]
                                                or len(item[k]) > fields[k] for k in required):
                trimmed = True
                continue
            row = {}
            for key, cap in fields.items():
                text = item.get(key)
                if isinstance(text, str):
                    trimmed |= len(text) > cap
                    row[key] = text[:cap]
            for key in {'outputs': ('version', 'turn', 'bytes'), 'sources': ('size',)}.get(name, ()):
                number = item.get(key)
                if type(number) is int and 0 <= number <= 10**12:
                    row[key] = number
            if name == 'outputs':
                if type(item.get('worker')) is bool:
                    row['worker'] = item['worker']
                at = item.get('at')
                if type(at) in (int, float) and 0 <= at <= 10**12:
                    row['at'] = at
            row_size = len(json.dumps(row, ensure_ascii=True).encode()) + 2
            if size + row_size > DRAWER_SECTION_BYTES:
                trimmed = True
                break
            rows.append(row)
            size += row_size
        result[name] = {'available': True, 'items': rows, 'total': total, 'trimmed': trimmed}
        reason = section.get('reason')
        if isinstance(reason, str):
            result[name]['reason'] = reason[:300]
    return result


def project(snapshot):
    """Keep display data only; truncation is explicit and never affects execution."""
    trimmed = bool(snapshot.get('trimmed'))
    session = drawer_metadata(snapshot.get('session'))
    # Both child and parent projections reserve the same metadata budget.
    session_bytes = len(json.dumps(session, ensure_ascii=True).encode())

    def clean(value, depth=0):
        nonlocal trimmed
        if isinstance(value, str):
            if len(value) > 16000:
                trimmed = True
                return value[:16000] + '\n… Display truncated.'
            return value
        if type(value) is float and not math.isfinite(value):
            trimmed = True
            return None
        if value is None or type(value) in (bool, int, float):
            return value
        if depth >= 5:
            trimmed = True
            return '[Nested display data omitted]'
        if isinstance(value, dict):
            trimmed |= len(value) > 40
            return {str(k)[:100]: clean(v, depth + 1) for k, v in list(value.items())[:40]}
        if isinstance(value, (list, tuple)):
            trimmed |= len(value) > 40
            return [clean(v, depth + 1) for v in value[:40]]
        return clean(str(value), depth + 1)

    source = snapshot.get('events', [])
    if not isinstance(source, list):
        raise ValueError('Unreadable activity record')
    trimmed |= len(source) > 300
    rows, size = [], 0
    for index in range(len(source) - 1, max(-1, len(source) - 301), -1):
        event = source[index]
        if not isinstance(event, dict) or not isinstance(event.get('kind'), str) or event['kind'] not in KINDS:
            continue
        identifier = event.get('id')
        row = {'id': identifier if isinstance(identifier, str) and len(identifier) <= 64 else str(index),
               'kind': event['kind'], 'data': clean(event.get('data'))}
        row_size = len(json.dumps(row, ensure_ascii=True).encode())
        if row_size > 32000:
            row['data'] = '[Large activity details omitted; review the terminal for full output.]'
            row_size = len(json.dumps(row).encode())
            trimmed = True
        if size + row_size > LIMIT - session_bytes - 1024:
            trimmed = True
            break
        rows.append(row)
        size += row_size + 2
    return {'available': True, 'events': list(reversed(rows)), 'trimmed': trimmed, 'session': session}


async def fetch(base, token):
    parsed = urlsplit(base)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
            or parsed.username or parsed.password or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment or not parsed.port):
        raise ValueError('Activity address must be an owned loopback server')
    async with asyncio.timeout(3):
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=3) as client:
            async with client.stream('GET', base.rstrip('/') + '/api/conversation',
                                     headers={'X-Dream-Token': token}) as response:
                response.raise_for_status()
                chunks, total = [], 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > LIMIT:
                        raise ValueError('Activity response exceeds the display limit')
                    chunks.append(chunk)
                raw = b''.join(chunks)
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get('available') is not True:
        raise ValueError('Activity is unavailable')
    return project(value)


def unavailable(reason, generation=''):
    return {'available': False, 'events': [], 'trimmed': False,
            'reason': reason, 'generation': generation}


class ActivityReader:
    """Coalesce readers; one bounded HTTP read per node, at most once a second."""
    def __init__(self, read=fetch):
        self.read = read
        self.entries = {}
        self.tasks = set()

    async def get(self, board, node):
        if node.profile != 'dream':
            return unavailable('Native CLI does not expose structured activity. Use its terminal.')
        if not node.address or node.state != 'ready' or not node.process or node.process.returncode is not None:
            return unavailable('Activity becomes available when this Workflow terminal is ready.')
        binding = (node.launch_generation, node.address, node.process)
        generation = str(node.launch_generation)
        entry = self.entries.get(node.id)
        if not entry or entry['binding'] != binding:
            if entry and entry['task'] and not entry['task'].done():
                entry['task'].cancel()
            # Never retain records for removed nodes.
            self.entries = {key: value for key, value in self.entries.items() if key in board.nodes}
            entry = {'binding': binding, 'task': None, 'checked': 0, 'value': None}
            self.entries[node.id] = entry
        if entry['value'] is not None and time.monotonic() - entry['checked'] < 1:
            result = entry['value']
        else:
            if entry['task'] is None or entry['task'].done():
                async def read():
                    try:
                        value = await self.read(*binding[1])
                    except (OSError, ValueError, TypeError, RecursionError, httpx.HTTPError):
                        value = unavailable('Activity could not be read. The terminal remains available.')
                    entry.update(value=value, checked=time.monotonic())
                    return value
                entry['task'] = asyncio.create_task(read())
                self.tasks.add(entry['task'])
                entry['task'].add_done_callback(self.tasks.discard)
            result = await asyncio.shield(entry['task'])
        if (board.nodes.get(node.id) is not node or
                (node.launch_generation, node.address, node.process) != binding
                or node.state != 'ready' or node.process.returncode is not None):
            return unavailable('The terminal changed while activity was loading.')
        return dict(result, generation=generation)

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.entries.clear()
