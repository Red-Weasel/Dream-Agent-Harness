"""Read-only native account evidence, independent of Council membership.

Only the installed CLI's own statement of its sign-in may report ``signed_in``.
That reports the CLI's authentication state, not a successful model request or
remote token validation. Grok states it on the first line of `grok models`
("You are logged in with ..." / "You are not authenticated."; the owner,
2026-10-11); the model list after it is never read. Antigravity exposes no such
statement; its installation, onboarding, model lists and old Gemini credentials
cannot establish authentication (gui/server.py lights it from its own terminal). No credentials, command output or exception text are
returned, logged or persisted here.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from .cli_discovery import locate_command, node_child_env
from .execution import ExecutionRefused, ProcessResult, run_owned
from .provider_login import TARGET_KEYS, login_env, resolve

_COMMANDS = {'anthropic': ('auth', 'status', '--json'), 'codex': ('login', 'status'), 'grok': ('models',)}
_MAX_OUTPUT = 16_384
_EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9.-]{1,189}")


def _view(provider: str, status: str, detail: str, username: str | None = None) -> dict:
    result = {'provider': provider, 'status': status, 'checked_at': datetime.now(timezone.utc).isoformat(),
              'detail': detail}
    if username is not None:
        result['username'] = username
    return result


def _parse(provider: str, result: ProcessResult) -> dict:
    if result.timed_out:
        return _view(provider, 'unknown', 'Account check timed out. Try again.')
    if result.truncated:
        return _view(provider, 'unknown', 'The CLI account response exceeded the safe output limit.')
    text = result.output.decode('utf-8', errors='replace').strip()
    if provider == 'anthropic':
        try:
            data = json.loads(text)
        except (ValueError, RecursionError):
            data = None
        if isinstance(data, dict) and type(data.get('loggedIn')) is bool:
            if data['loggedIn'] and result.returncode == 0:
                email = data.get('email')
                username = email if isinstance(email, str) and _EMAIL.fullmatch(email) else None
                return _view(provider, 'signed_in', 'Claude reports signed in. Model access has not been tested.', username)
            if not data['loggedIn']:
                return _view(provider, 'signed_out', 'Claude reports signed out. Sign in to continue.')
    elif provider == 'codex':
        # Codex writes this status to stderr (merged by run_owned). It can also
        # print an API-key prefix; never copy that text into the public result.
        lines = text.splitlines()
        if result.returncode == 0 and any(line == 'Logged in using ChatGPT' for line in lines):
            return _view(provider, 'signed_in', 'Codex reports signed in with ChatGPT. Model access has not been tested.')
        if result.returncode == 0 and any(line.startswith('Logged in using an API key') for line in lines):
            return _view(provider, 'signed_in', 'Codex reports an API key configured. Model access has not been tested.')
        if 'Not logged in' in lines:
            return _view(provider, 'signed_out', 'Codex reports signed out. Sign in to continue.')
    elif provider == 'grok':
        first = text.splitlines()[0].strip() if text else ''
        if result.returncode == 0 and first.startswith('You are logged in with ') and first.endswith('.'):
            return _view(provider, 'signed_in', 'Grok reports signed in. Model access has not been tested.')
        if first == 'You are not authenticated.':
            return _view(provider, 'signed_out', 'Grok reports signed out. Sign in to continue.')
    return _view(provider, 'unknown', 'The CLI could not confirm account status. Sign in or retry the check.')


async def inspect_account(provider: object, *, env: Mapping[str, str] | None = None,
                          timeout: float = 5.0) -> dict:
    """Return bounded account evidence for a fixed provider or accepted alias.

    Unknown provider names raise ValueError without echoing input. Cancellation
    propagates after the owned subprocess is reaped. The HTTP caller must apply
    its ordinary authorization and should coalesce/cache concurrent UI refreshes.
    """
    target = resolve(provider)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 15:
        raise ValueError('Account check timeout must be greater than zero and at most 15 seconds.')
    found = locate_command(target.cli)
    if not found:
        return _view(target.key, 'unavailable', f'{target.label} CLI is not installed.')
    args = _COMMANDS.get(target.key)
    if args is None:
        return _view(target.key, 'unknown',
                     f'{target.label} has no sign-in status command. Its light turns green while one of its '
                     'DeepSleep terminals is at work (it used Dream’s tools in the last fifteen minutes)')
    child = login_env(os.environ if env is None else env, repo_root=str(Path(__file__).resolve().parents[2]),
                      strip=('CLAUDECODE', 'NODE_OPTIONS', 'PYTHONSTARTUP', 'LD_PRELOAD', 'LD_LIBRARY_PATH'))
    child['NO_COLOR'] = '1'
    child = node_child_env(target.cli, found, child) or child
    try:
        result = await run_owned((found, *args), cwd=Path('/'), env=child,
                                 timeout=timeout, max_output=_MAX_OUTPUT)
    except (OSError, ExecutionRefused, asyncio.TimeoutError, ValueError):
        return _view(target.key, 'unknown', 'The account check could not run. Try again after checking the CLI installation.')
    return _parse(target.key, result)


async def inspect_accounts(*, env: Mapping[str, str] | None = None, timeout: float = 5.0) -> list[dict]:
    """Check each fixed provider concurrently; never invoke login or generation."""
    return list(await asyncio.gather(*(inspect_account(key, env=env, timeout=timeout) for key in TARGET_KEYS)))
