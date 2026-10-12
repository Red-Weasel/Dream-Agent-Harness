"""Provider sign-in: the fixed commands, and the small in-memory request record (DREAM-246).

`/login <provider>` in a session and the Sign in section of Council both end here. Nothing in this module runs a
process, calls a model, reads a credential or a configuration file, or touches the network. It answers three things:

1. Which official command signs in to a provider. The table below is fixed. A caller picks a provider by name and
   gets that provider's command; there is no way to add a flag, a URL, an environment variable, a token or another
   executable. The executable is the file the shared lookup (cli_discovery.locate_command) finds.

2. What environment that command gets in the desktop's separate sign-in window (login_env, launch_plan).

3. Which sign-in window the desktop has been asked to open (LoginRequests). A request is an id and a provider name
   held in memory. The desktop window reads it from /api/desktop and answers with one fixed status word. No token,
   credential, browser address or terminal output is ever part of a request or an answer.

What is never claimed: that a CLI is the genuine one, that an account is signed in, or that sign-in worked. A
"launched" answer means a window with the command started. A "closed" answer means it ended. Neither says anything
about the account.
"""
from __future__ import annotations

import os
import re
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Mapping

from .cli_discovery import locate_command, node_child_env


@dataclass(frozen=True)
class LoginTarget:
    key: str                  # the canonical provider name
    label: str                # what a person reads
    cli: str                  # a cli_discovery.KNOWN_CLIS name, never a path
    args: tuple[str, ...]     # the fixed arguments of the official sign-in command
    login_only: bool          # True when Dream has no agent for this provider: sign-in is all that is offered
    note: str

    @property
    def command(self) -> str:
        """The command as a person would type it in their own terminal."""
        return ' '.join((self.cli, *self.args))


# The official commands, as each CLI's own help gives them. `gemini` and `agy` have no sign-in subcommand: started
# with no arguments they open normally and ask the user to sign in when that is needed. Dream sends them no prompt.
TARGETS: tuple[LoginTarget, ...] = (
    LoginTarget('anthropic', 'Claude', 'claude', ('auth', 'login'), False,
                'Runs Claude Code\'s own sign-in.'),
    LoginTarget('codex', 'ChatGPT (Codex)', 'codex', ('login',), False,
                'Runs the Codex CLI\'s own sign-in.'),
    LoginTarget('grok', 'Grok', 'grok', ('login', '--oauth'), False,
                'Runs the Grok CLI\'s own browser sign-in.'),
    LoginTarget('antigravity', 'Gemini (Antigravity)', 'agy', (), False,
                'Opens the Antigravity CLI for its native browser sign-in. CLI sign-in is separate from desktop sign-in. Close the window afterwards.'),
)
ALIASES: Mapping[str, str] = {'claude': 'anthropic', 'chatgpt': 'codex', 'agy': 'antigravity', 'gemini': 'antigravity'}
_BY_KEY = {target.key: target for target in TARGETS}
TARGET_KEYS: tuple[str, ...] = tuple(_BY_KEY)

_MAX_NAME = 32


def resolve(name: object) -> LoginTarget:
    """The sign-in target for a provider name or one of its aliases. Anything else is refused with ValueError,
    and the refused text is never echoed back."""
    if not isinstance(name, str) or len(name) > _MAX_NAME:
        raise ValueError('Unknown sign-in provider. Choose one of: ' + ', '.join(TARGET_KEYS) + '.')
    key = name.strip().lower()
    target = _BY_KEY.get(ALIASES.get(key, key))
    if target is None:
        raise ValueError('Unknown sign-in provider. Choose one of: ' + ', '.join(TARGET_KEYS) + '.')
    return target


def targets_view() -> list[dict]:
    """The five providers as plain data for a page."""
    return [{'key': t.key, 'label': t.label, 'command': t.command, 'login_only': t.login_only, 'note': t.note}
            for t in TARGETS]


def manual_text(target: LoginTarget) -> str:
    """What to tell someone when no sign-in window can be opened for them."""
    return (f'To sign in to {target.label}, open a separate terminal of your own and run:  {target.command}  '
            'Do not type it into this Dream session. Dream does not run it here and does not check whether you '
            'are signed in.')


# --- the environment and argv of the sign-in window's command ------------------------------------------------------

# Set by the desktop for its own web views or for a Dream session; a provider CLI is neither.
_STRIP_NAMES = frozenset({'WEBKIT_FORCE_SANDBOX', 'GST_REGISTRY'})
_STRIP_PREFIX = 'DREAM_'


def login_env(env: Mapping[str, str], *, repo_root: str | None = None,
              strip: tuple[str, ...] = ()) -> dict[str, str]:
    """A separate copy of `env` for a provider's sign-in command. `env` itself is not modified.

    Removed: every variable whose name starts with DREAM_ (the session file, the GUI switch, crash-log paths and the
    like), the two variables the desktop sets for its own web views, and any name listed in `strip`. From PYTHONPATH,
    only the entries that are `repo_root` (the folder Dream puts there for its own session) are removed; entries the
    user had are kept, and the variable is dropped when nothing is left.

    Kept: everything else, including HOME, PATH, the display, D-Bus, keyring and browser variables that an official
    browser sign-in needs. TERM and COLORTERM are set for the terminal the command runs in. Nothing is added from
    a request.
    """
    child: dict[str, str] = {}
    for name, value in env.items():
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        if not name or '=' in name or '\x00' in name or '\x00' in value:
            continue
        if name in _STRIP_NAMES or name in strip or name.startswith(_STRIP_PREFIX):
            continue
        child[name] = value
    if repo_root and 'PYTHONPATH' in child:
        ours = os.path.normpath(repo_root)
        kept = [part for part in child['PYTHONPATH'].split(os.pathsep)
                if part and os.path.normpath(part) != ours]
        if kept:
            child['PYTHONPATH'] = os.pathsep.join(kept)
        else:
            del child['PYTHONPATH']
    child['TERM'] = 'xterm-256color'
    child['COLORTERM'] = 'truecolor'
    return child


@dataclass(frozen=True)
class LaunchPlan:
    target: LoginTarget
    argv: tuple[str, ...]     # the found file, then the target's fixed arguments. Nothing else.
    env: dict[str, str]


def launch_plan(target: LoginTarget, env: Mapping[str, str], *, repo_root: str | None = None,
                strip: tuple[str, ...] = ()) -> LaunchPlan | None:
    """What the sign-in window runs for `target`, or None when its CLI is not installed.

    The file is the one the shared lookup finds for the fixed CLI name. When that file is an nvm-style node script
    whose folder holds its node, that one folder is put in front of the child's PATH (cli_discovery.node_child_env);
    otherwise the environment is login_env's copy unchanged.
    """
    if not isinstance(target, LoginTarget) or _BY_KEY.get(target.key) is not target:
        raise ValueError('Unknown sign-in provider.')
    found = locate_command(target.cli)
    if not found:
        return None
    child = login_env(env, repo_root=repo_root, strip=strip)
    child = node_child_env(target.cli, found, child) or child
    return LaunchPlan(target, (found, *target.args), child)


# --- the request record ---------------------------------------------------------------------------------------------

REQUEST_ID = re.compile(r'[0-9a-f]{16}')
# What the desktop window may answer. Each is about the window, never about the account.
ACK_STATUSES = ('launched', 'failed', 'unavailable', 'busy', 'closed')
LOGIN_ACTIONS = frozenset({'provider_login', 'provider_login_status', 'provider_login_ack'})
# How long a request waits for the desktop window to pick it up. The window polls about every 0.7 s.
QUEUE_SECONDS = 20.0
# Finished requests remembered so a repeated answer or a late status question still matches. A request that is
# waiting ('queued') or whose window is open ('launched') is not counted here and is never forgotten: there is at
# most one of each, so the whole record is at most KEEP_RECORDS + 2 entries.
KEEP_RECORDS = 8
_LIVE = frozenset({'queued', 'launched'})

_NOT_CHECKED = 'Dream did not check whether you are signed in.'


def _message(status: str, target: LoginTarget) -> str:
    if status == 'queued':
        return f'Opening a separate sign-in window for {target.label}…'
    if status == 'launched':
        return (f'A separate sign-in window is open for {target.label}. Finish there or in your browser, then '
                f'close that window. {_NOT_CHECKED}')
    if status == 'closed':
        return f'The {target.label} sign-in window has closed. {_NOT_CHECKED}'
    if status == 'unavailable':
        return (f'The {target.cli} command was not found on this computer. Install it first, then try again. '
                f'{_NOT_CHECKED}')
    if status == 'busy':
        return 'Another sign-in window is already open. Finish or close it, then try again.'
    if status == 'expired':
        return ('The desktop window did not answer in time, so it is not known whether a sign-in window opened. '
                f'Look for one; if there is none, try again or run  {target.command}  in a separate terminal.')
    if status == 'replaced':
        return (f'The desktop window reported a different sign-in window open, so the {target.label} one is taken '
                f'to have closed. {_NOT_CHECKED}')
    return (f'The sign-in window could not start. You can run  {target.command}  in a separate terminal of your '
            f'own instead. {_NOT_CHECKED}')


class LoginRequests:
    """The sign-in window requests of one Studio server, in memory only.

    At most one request waits for the desktop window at a time. Whether a sign-in window is already open is the
    desktop window's own knowledge: it answers "busy" for a second one. `desktop` says whether this session has a
    desktop window that could open one at all; without it a request creates nothing and returns the manual command.

    What is kept, and for how long:

    - one 'queued' request at most (request() refuses a second), until it is answered or its wait runs out;
    - one 'launched' request at most, until the desktop window answers 'closed' for it. However many later requests
      are made and answered 'busy', this one stays, so its 'closed' answer always finds it. The desktop opens one
      window at a time; if it ever reports a second one launched, the one asked for earlier is marked 'replaced'
      (it can still be answered 'closed'), so there is never a growing set of open windows on record;
    - the KEEP_RECORDS most recent finished requests ('failed', 'unavailable', 'busy', 'closed', 'expired',
      'replaced'). Only these are ever forgotten, oldest first.

    'expired' means the outcome is unknown, not that the request was withdrawn: a late answer for a request that
    is still remembered is recorded.
    """

    def __init__(self, desktop: bool, *, clock: Callable[[], float] = time.monotonic,
                 new_id: Callable[[], str] = lambda: secrets.token_hex(8)) -> None:
        self.desktop = bool(desktop)
        self._clock = clock
        self._new_id = new_id
        self._records: OrderedDict[str, dict] = OrderedDict()
        self._sequence = 0

    # -- reading

    def _expire(self) -> None:
        now = self._clock()
        for record in self._records.values():
            if record['status'] == 'queued' and now - record['at'] >= QUEUE_SECONDS:
                record['status'] = 'expired'
        self._trim()

    def _trim(self) -> None:
        """Forget the oldest finished requests beyond KEEP_RECORDS. A waiting or open one is never forgotten."""
        finished = [request_id for request_id, record in self._records.items() if record['status'] not in _LIVE]
        for request_id in finished[:max(0, len(finished) - KEEP_RECORDS)]:
            del self._records[request_id]

    def _open(self) -> dict | None:
        """The request whose sign-in window the desktop last reported open and has not reported closed."""
        for record in self._records.values():
            if record['status'] == 'launched':
                return record
        return None

    def _view(self, record: dict) -> dict:
        target = _BY_KEY[record['provider']]
        return {'desktop': True, 'id': record['id'], 'provider': target.key, 'label': target.label,
                'command': target.command, 'status': record['status'],
                'message': _message(record['status'], target), 'sign_in_checked': False}

    def desktop_view(self) -> dict | None:
        """The one waiting request for the desktop window: its id and provider and nothing else."""
        self._expire()
        for record in self._records.values():
            if record['status'] == 'queued':
                return {'id': record['id'], 'provider': record['provider']}
        return None

    def status(self, request_id: object = None) -> dict:
        self._expire()
        record = None
        if request_id is not None:
            if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
                raise ValueError('That is not a sign-in request id.')
            record = self._records.get(request_id)
        elif self._records:
            record = max(self._records.values(), key=lambda item: item['order'])      # the newest request asked
        opened = self._open()
        # 'open' is the window the desktop last reported open, whichever request was asked about: a page can say
        # "close that one first" without guessing. It is the desktop's report, not a check of any account.
        return {'desktop': self.desktop, 'targets': targets_view(),
                'request': self._view(record) if record else None,
                'open': self._view(opened) if opened else None, 'sign_in_checked': False}

    # -- asking

    def request(self, provider: object) -> dict:
        """Ask the desktop window to open the sign-in window for one provider. Refused names raise ValueError
        before anything is recorded. Asking again for the provider that is already waiting returns that request."""
        target = resolve(provider)
        if not self.desktop:
            return {'desktop': False, 'id': None, 'provider': target.key, 'label': target.label,
                    'command': target.command, 'status': 'manual',
                    'message': 'This session is not running in the Dream desktop window, so no sign-in window can '
                               'be opened from here. ' + manual_text(target), 'sign_in_checked': False}
        self._expire()
        for record in self._records.values():
            if record['status'] == 'queued':
                if record['provider'] == target.key:
                    return self._view(record)
                raise ValueError('Another sign-in request is still waiting for the desktop window. '
                                 'Try again in a few seconds.')
        request_id = self._new_id()
        if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id) or request_id in self._records:
            raise ValueError('Could not create a sign-in request. Try again.')
        self._sequence += 1
        record = {'id': request_id, 'provider': target.key, 'status': 'queued', 'at': self._clock(),
                  'order': self._sequence}
        self._records[request_id] = record
        self._trim()                          # only finished requests; the open window's record is not touched
        return self._view(record)

    # -- the desktop window's answer

    def ack(self, request_id: object, provider: object, status: object) -> dict:
        """Record the desktop window's answer for exactly one request. The id and the provider must both match.
        The same answer again changes nothing and returns the same record. An answer that contradicts what is
        already recorded is refused."""
        if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
            raise ValueError('That is not a sign-in request id.')
        if not isinstance(provider, str) or provider not in _BY_KEY:
            raise ValueError('Unknown sign-in provider.')
        if not isinstance(status, str) or status not in ACK_STATUSES:
            raise ValueError('Unknown sign-in window status.')
        self._expire()
        record = self._records.get(request_id)
        if record is None or record['provider'] != provider:
            raise ValueError('No sign-in request matches that id and provider.')
        current = record['status']
        if current in ('queued', 'expired'):
            # A window that opened after the wait ran out did open: record what happened.
            if status == 'launched':
                self._launched(record)
            else:
                record['status'] = status
        elif current in ('launched', 'replaced') and status == 'closed':
            record['status'] = 'closed'
        elif current == status or (current in ('closed', 'replaced') and status == 'launched'):
            pass                              # a repeat, or the earlier answer arriving late
        else:
            raise ValueError('That answer does not match what is already recorded for this sign-in request.')
        if record['status'] != current and record['status'] not in _LIVE:
            self._finished(record)
        self._trim()
        return self._view(record)

    def _finished(self, record: dict) -> None:
        """The finished history is kept by when a request finished, not by when it was asked: a window that was
        open for a long time and has just closed is the newest finished request, not the first one forgotten."""
        self._records.move_to_end(record['id'])

    def _launched(self, record: dict) -> None:
        """The desktop reported this request's window open. It opens one window at a time, so a window already on
        record as open must have closed without that answer having arrived yet: of the two, the request asked for
        later is the open one and the other is 'replaced'. Its own 'closed' answer is still accepted afterwards."""
        other = self._open()
        if other is None or other is record:
            record['status'] = 'launched'
        elif other['order'] < record['order']:
            other['status'], record['status'] = 'replaced', 'launched'
            self._finished(other)
        else:
            record['status'] = 'replaced'

    # -- the three control actions, with exact fields

    def control(self, payload: Mapping) -> dict:
        """One of LOGIN_ACTIONS from /api/control. Each action takes exactly its own fields; anything extra is
        refused before a request is made, so no flag, path, URL or secret can ride along."""
        action = payload.get('action')
        fields = set(payload)
        if action == 'provider_login':
            if fields != {'action', 'provider'}:
                raise ValueError('A sign-in request takes a provider name and nothing else.')
            return self.request(payload['provider'])
        if action == 'provider_login_status':
            if not fields <= {'action', 'id'}:
                raise ValueError('A sign-in status question takes a request id and nothing else.')
            return self.status(payload.get('id'))
        if action == 'provider_login_ack':
            if fields != {'action', 'id', 'provider', 'status'}:
                raise ValueError('A sign-in window answer takes an id, a provider and a status and nothing else.')
            return self.ack(payload['id'], payload['provider'], payload['status'])
        raise ValueError('Unknown sign-in action.')


def usage_lines() -> list[str]:
    """What a bare `/login` prints: the usage line and the five providers."""
    lines = ['usage: /login <provider>   opens the provider\'s own sign-in in a separate window']
    lines += [f'  {t.key:<12} {t.command:<18} {t.label}' + ('  (sign-in only)' if t.login_only else '')
              for t in TARGETS]
    return lines


def prompt_command(text: object) -> str | None:
    """The argument of a `/login` typed into a prompt box, or None when the text is not that command.

    The first word must be exactly `/login` (any letter case): `/loginx`, `/log in` and a message that merely
    mentions /login are ordinary text. Everything after the word is returned as one argument, untouched; resolve()
    accepts a provider name there and refuses anything else, so no flag or second word is ever acted on."""
    if not isinstance(text, str):
        return None
    words = text.strip().split(None, 1)
    if not words or words[0].lower() != '/login':
        return None
    return words[1] if len(words) > 1 else ''


def prompt_result(arg: str, ask: Callable[[str], dict]) -> dict:
    """The answer to `/login [provider]` typed into a Studio prompt box. Bare, it is the usage text and the
    provider list and asks for nothing. With a provider it is exactly what `ask` (LoginRequests.request) returns.
    Anything else raises ValueError before `ask` is called, and the refused text is not in the error."""
    if not arg.strip():
        return {'status': 'help', 'message': '\n'.join(usage_lines()), 'targets': targets_view(),
                'sign_in_checked': False}
    return ask(resolve(arg).key)


def slash_lines(arg: str, studio: object = None) -> list[str]:
    """The lines `/login [provider]` prints. With a Studio server (`studio.login_request`) inside the desktop
    window it makes the same request the Council Sign in button makes; otherwise it gives the manual command. It
    never runs anything in the terminal the session lives in."""
    if not arg.strip():
        return usage_lines()
    try:
        target = resolve(arg)
        ask = getattr(studio, 'login_request', None)
        if not callable(ask):
            return [manual_text(target)]
        return [str(ask(target.key)['message'])]
    except ValueError as exc:
        return [str(exc)]
