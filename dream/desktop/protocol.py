"""Small standard-library boundary between GTK and the harness process."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener


def session_address(path: Path, child_pid: int | None = None) -> tuple[str, str] | None:
    """Only accept the current child's token-guarded loopback discovery record."""
    try:
        if path.stat().st_size > 8192:
            return None
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or (child_pid and data.get('pid') != child_pid):
            return None
        url = urlsplit(data['url'])
        # The token rides in the fragment (DREAM-187).
        token = (parse_qs(url.fragment).get('token') or [''])[0]
        if (url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port
                or url.username or url.password or not token or len(token) > 256):
            return None
        return urlunsplit((url.scheme, url.netloc, '', '', '')), token
    except (OSError, ValueError, KeyError, TypeError):
        return None


def session_status(base: str, token: str) -> dict:
    # Never send session credentials through a user-configured HTTP proxy.
    request = Request(base + '/api/desktop', headers={'x-dream-token': token})
    with build_opener(ProxyHandler({})).open(request, timeout=2) as response:
        body = response.read(65537)
        if len(body) > 65536:
            raise ValueError('Session status response is too large')
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError('Invalid session status')
        return data


def browser_address(value: str) -> str:
    """Normalize typed URLs, refusing executable and local-file schemes."""
    value = value.strip()
    if not value:
        raise ValueError('Enter a website address or a local development URL.')
    if any(ord(c) < 32 for c in value) or ' ' in value:
        raise ValueError('Enter a URL, for example https://example.com or localhost:3000.')
    if '://' not in value:
        first = value.split('/')[0]
        if first.startswith(('localhost', '127.0.0.1', '[::1]')):
            value = 'http://' + value
        elif ':' in first:
            raise ValueError('Only http and https addresses can be opened.')
        else:
            value = 'https://' + value
    url = urlsplit(value)
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
        raise ValueError('Only http and https addresses without embedded credentials can be opened.')
    try:
        url.port
    except ValueError:
        raise ValueError('The address contains an invalid port.') from None
    return value


def refill(current, old, new):
    """DREAM-179: the fields a mode switch re-seeds: those still at the old mode's recommended value (never a user's edit)."""
    return {key: value for key, value in new.items() if key in current and current[key] == old.get(key)}


def recommended_card(modes, notes, active=None):
    """The load screen's Recommended settings card (the owner's plan of 2026-10-01) as plain data, for the GTK screen
    to lay out: {title, source, modes: [{key, name, when, active, rows: [[label, value]], unset}], effort: [[level,
    text]], notes: [text]}. `modes` is model_defaults.recommended_modes' block, None when the model card gives nothing
    (the card says so); `notes` are the load screen's notes, which the card shows instead of small lines under the
    buttons; `active` is the mode the Thinking / Instruct switch is on (default: the block's own)."""
    card = {'title': 'Recommended settings', 'source': '', 'modes': [], 'effort': [], 'empty': '',
            'notes': [str(note) for note in notes or () if str(note).strip()]}
    if not modes:
        card['empty'] = ("This model's card gives no recommended settings, so the fields show the engine's own "
                         "defaults.")
        return card
    source = str(modes.get('source') or '').removeprefix('https://').removeprefix('http://')
    card['source'] = 'From the model card' + (f' · {source}' if source else '')
    active = active or modes.get('active') or 'thinking'
    for key in ('thinking', 'instruct'):
        mode = modes.get(key)
        if not mode:
            continue
        name = str(mode.get('label') or key.title())
        if key == 'thinking' and not modes.get('instruct'):
            name += ' (always on)'
        card['modes'].append({'key': key, 'name': name, 'when': str(mode.get('when') or ''), 'active': key == active,
                              'rows': [list(row) for row in mode.get('rows') or ()],
                              'unset': list(mode.get('unset') or ())})
    card['effort'] = [[str(level), str(text)] for level, text in (modes.get('effort') or {}).items()]
    if modes.get('note'):
        card['notes'].insert(0, str(modes['note']))
    return card
