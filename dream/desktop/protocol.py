"""Small standard-library boundary between GTK and the harness process."""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.error import HTTPError
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


# --- provider sign-in windows (DREAM-246) ---------------------------------------------------------------------------
# The session asks for a sign-in window with an id and a provider name in /api/desktop; the window answers with one
# status word. These are the only shapes accepted in either direction. No command, path, flag, URL, token or
# terminal output is carried: the provider name selects a fixed command inside this process (core.provider_login).

LOGIN_ID = re.compile(r'[0-9a-f]{16}')
LOGIN_PROVIDERS = ('anthropic', 'codex', 'grok', 'gemini', 'antigravity')
LOGIN_STATUSES = ('launched', 'failed', 'unavailable', 'busy', 'closed')
_LOGIN_FINAL = frozenset({'failed', 'unavailable', 'busy', 'closed'})   # every status except 'launched'
_LOGIN_REMEMBERED = 32


def login_request(state) -> tuple[str, str] | None:
    """The (id, provider) a session status asks a sign-in window for, or None. Exactly those two fields, an id of
    the expected form and one of the five canonical provider names; any other shape asks for nothing."""
    item = state.get('login_request') if isinstance(state, dict) else None
    if not isinstance(item, dict) or set(item) != {'id', 'provider'}:
        return None
    request_id, provider = item['id'], item['provider']
    if not isinstance(request_id, str) or not LOGIN_ID.fullmatch(request_id):
        return None
    if not isinstance(provider, str) or provider not in LOGIN_PROVIDERS:
        return None
    return request_id, provider


class LoginTracker:
    """Which sign-in requests this window has already acted on, and which answers it still owes.

    Everything is bound to one session (`binding`: the session process and its Studio address). When the binding
    changes, the handled ids and the unsent answers of the old session are forgotten, so an answer is never sent
    to a different session than the one that asked. Used from the GTK main loop only.

    An id stays "open" from the moment it is taken until a final answer for it ('failed', 'unavailable', 'busy' or
    'closed') has been delivered. An open id is never forgotten, however many later requests arrive: the window it
    opened can still report 'closed', and that answer is still owed. Only ids whose final answer was delivered are
    forgotten, oldest first, beyond _LOGIN_REMEMBERED. If that many ids are open at once (answers are not getting
    through), no new request is taken until one of them is settled: nothing is opened that could not be answered for.
    """

    def __init__(self) -> None:
        self.binding = None
        self._handled: dict[str, str] = {}
        self._outbox: dict[str, tuple[str, str]] = {}
        self._open: set[str] = set()

    def _bind(self, binding) -> None:
        if binding != self.binding:
            self.binding = binding
            self._handled = {}
            self._outbox = {}
            self._open = set()

    def take(self, binding, state) -> tuple[str, str] | None:
        """A request to act on now, at most once per id for this session."""
        self._bind(binding)
        request = login_request(state)
        if request is None or request[0] in self._handled:
            return None
        if len(self._open) >= _LOGIN_REMEMBERED:
            return None                       # left waiting in the session; it is taken once an answer gets through
        self._handled[request[0]] = request[1]
        self._open.add(request[0])
        settled = [request_id for request_id in self._handled if request_id not in self._open]
        for request_id in settled[:max(0, len(self._handled) - _LOGIN_REMEMBERED)]:
            del self._handled[request_id]     # settled ids have nothing left in the outbox
        return request

    def report(self, binding, request_id: str, provider: str, status: str) -> bool:
        """Queue an answer for a request this session made. An answer for another session, an id this window
        never took, a different provider or an unknown status is dropped."""
        if (binding != self.binding or self._handled.get(request_id) != provider
                or status not in LOGIN_STATUSES):
            return False
        self._outbox[request_id] = (provider, status)
        self._open.add(request_id)            # owed an answer again, so it is kept until that answer is delivered
        return True

    def due(self) -> list[tuple[str, str, str]]:
        return [(request_id, provider, status) for request_id, (provider, status) in self._outbox.items()]

    def sent(self, binding, request_id: str, status: str) -> None:
        """An answer was delivered (or definitely refused). A newer answer for the same id stays queued. Once the
        delivered answer is a final one the id is settled and may later be forgotten."""
        if binding == self.binding and self._outbox.get(request_id, ('', ''))[1] == status:
            del self._outbox[request_id]
            if status in _LOGIN_FINAL:
                self._open.discard(request_id)


def login_ack(base: str, token: str, request_id: str, provider: str, status: str) -> bool:
    """Tell the session what happened to one sign-in window request. True when the session answered (accepted, or
    refused for good: either way do not send it again). A connection failure raises, and the caller retries.

    Sent like every other call to the session: loopback, the session token in a header, never through a proxy.
    """
    if (not LOGIN_ID.fullmatch(request_id) or provider not in LOGIN_PROVIDERS or status not in LOGIN_STATUSES):
        raise ValueError('Invalid sign-in window answer')
    body = json.dumps({'action': 'provider_login_ack', 'id': request_id, 'provider': provider,
                       'status': status}).encode()
    request = Request(base + '/api/control', data=body, method='POST',
                      headers={'x-dream-token': token, 'Content-Type': 'application/json'})
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=3) as response:
            response.read(65537)
    except HTTPError as exc:
        exc.close()
        if 400 <= exc.code < 500:
            return True                   # the session refused this answer; repeating it cannot help
        raise
    return True


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


def recommended_card(modes, notes, active=None, facts=None):
    """The load screen's Recommended settings card (the owner's plan of 2026-10-01) as plain data, for the GTK screen
    to lay out: {title, source, modes: [{key, name, when, active, rows: [[label, value]], unset}], effort: [[level,
    text]], notes: [text]}. `modes` is model_defaults.recommended_modes' block, None when the model card gives nothing
    (the card says so); `notes` are the load screen's notes, which the card shows instead of small lines under the
    buttons; `active` is the mode the Thinking / Instruct switch is on (default: the block's own)."""
    card = {'title': 'Recommended settings', 'source': '', 'modes': [], 'effort': [], 'empty': '', 'facts': [],
            'notes': [str(note) for note in notes or () if str(note).strip()]}
    if facts is not None:
        # The native Python process has GTK, not the harness's dependencies.
        def tokens(value):
            return f'{value // 1024}K' if value % 1024 == 0 else f'{value:,}'
        limit, selected = facts.get('context_limit'), facts.get('configured_context')
        spec = facts.get('speculative') or {}
        support, enabled = spec.get('supported'), spec.get('enabled')
        state = ('Supported' if support is True else 'Not supported' if support is False else 'Not reported')
        if support is True and type(enabled) is bool:
            state += ' · ' + ('On' if enabled else 'Off')
        elif enabled is True:
            state += ' · requested On'
        card['facts'] = [['Model context limit', tokens(limit) + ' tokens' if type(limit) is int else 'Not reported'],
                         ['Launch context', tokens(selected) + ' tokens' if type(selected) is int else 'Not selected'],
                         ['Speculative decoding', state],
                         ['Draft model', spec.get('draft') or 'Not reported'],
                         ['Draft tokens per step', str(spec['count']) if spec.get('count') is not None else 'Not reported'],
                         ['Image input', 'Supported' if facts.get('vision') is True else 'Not supported' if facts.get('vision') is False else 'Not reported']]
        if facts.get('context_source'):
            card['facts'].insert(1, ['Context source', facts['context_source']])
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


TOOLBAR_SELECTS = frozenset(('provider', 'profile', 'model', 'layout'))
TOOLBAR_INPUTS = frozenset(('name',))
TOOLBAR_BUTTONS = frozenset(('add', 'tile', 'group', 'connections', 'preview', 'refresh', 'characters'))


def toolbar_state(raw: str, session_id: str, workspace: str) -> dict | None:
    """Validate the bounded DOM snapshot for the current session and workspace."""
    if not isinstance(raw, str) or len(raw) > 65536:
        return None
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if (not isinstance(value, dict) or value.get('session_id') != session_id
            or not session_id or value.get('workspace') != workspace
            or value.get('view') != 'deepsleep' or not isinstance(value.get('controls'), dict)):
        return None
    controls = value['controls']
    if not controls.keys() <= TOOLBAR_SELECTS | TOOLBAR_INPUTS | TOOLBAR_BUTTONS:
        return None
    for key, item in controls.items():
        if (not isinstance(item, dict) or type(item.get('disabled')) is not bool
                or not isinstance(item.get('label'), str) or len(item['label']) > 100):
            return None
        if key in TOOLBAR_SELECTS:
            options = item.get('options')
            if (not isinstance(item.get('value'), str) or len(item['value']) > 256
                    or not isinstance(options, list) or len(options) > 64):
                return None
            for option in options:
                if (not isinstance(option, dict) or not isinstance(option.get('value'), str)
                        or len(option['value']) > 256 or not isinstance(option.get('label'), str)
                        or len(option['label']) > 100 or type(option.get('disabled')) is not bool):
                    return None
        elif key in TOOLBAR_INPUTS:
            if not isinstance(item.get('value'), str) or len(item['value']) > 100:
                return None
        elif type(item.get('pressed')) is not bool:
            return None
    return value
