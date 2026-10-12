"""Account evidence stays separate from availability, membership and raw secrets."""
import asyncio
import json
from datetime import datetime

import pytest

from dream.core import provider_accounts as accounts
from dream.core.execution import ProcessResult


@pytest.fixture
def native(monkeypatch):
    calls = []
    result = [ProcessResult(0, b'')]
    monkeypatch.setattr(accounts, 'locate_command', lambda name: '/installed/' + name)
    monkeypatch.setattr(accounts, 'node_child_env', lambda *args: None)

    async def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if isinstance(result[0], BaseException):
            raise result[0]
        return result[0]

    monkeypatch.setattr(accounts, 'run_owned', run)
    return calls, result


@pytest.mark.asyncio
async def test_claude_real_status_shape_and_only_public_fields(native):
    calls, result = native
    result[0] = ProcessResult(0, json.dumps({'loggedIn': True, 'authMethod': 'claude.ai',
                                           'email': 'example@example.com', 'accessToken': 'secret',
                                           'orgId': 'private', 'subscriptionType': 'max'}).encode())
    view = await accounts.inspect_account('claude')
    assert view['provider'] == 'anthropic' and view['status'] == 'signed_in'
    assert view['username'] == 'example@example.com'
    assert datetime.fromisoformat(view['checked_at']).utcoffset().total_seconds() == 0
    assert set(view) == {'provider', 'status', 'username', 'detail', 'checked_at'}
    assert 'secret' not in json.dumps(view) and 'private' not in json.dumps(view)
    assert calls[0][0] == ('/installed/claude', 'auth', 'status', '--json')


@pytest.mark.asyncio
@pytest.mark.parametrize('code,body,status', [
    (0, b'Logged in using ChatGPT\n', 'signed_in'),
    (0, b'Logged in using an API key - sk-secret\n', 'signed_in'),
    (1, b'Not logged in\n', 'signed_out'),
    (0, b'Not logged in\n', 'signed_out'),
    (1, b'Logged in using ChatGPT\n', 'unknown'),
    (1, b'Error: token expired; secret=sk-secret\n', 'unknown'),
    (0, b'Unexpected secret=sk-secret\n', 'unknown'),
])
async def test_codex_status_and_expired_failure_never_leak_output(native, code, body, status):
    calls, result = native
    result[0] = ProcessResult(code, body)
    view = await accounts.inspect_account('codex')
    assert view['status'] == status and 'username' not in view
    assert 'sk-secret' not in json.dumps(view)
    assert calls[0][0] == ('/installed/codex', 'login', 'status')


@pytest.mark.asyncio
@pytest.mark.parametrize('body,code,status', [
    (b'{"loggedIn":false}', 1, 'signed_out'),
    (b'{"loggedIn":true}', 1, 'unknown'),
    (b'{"loggedIn":"true"}', 0, 'unknown'),
    (b'{"loggedIn":1}', 0, 'unknown'),
    (b'null', 0, 'unknown'),
    (b'not JSON secret', 0, 'unknown'),
])
async def test_claude_requires_boolean_native_success(native, body, code, status):
    _, result = native
    result[0] = ProcessResult(code, body)
    assert (await accounts.inspect_account('anthropic'))['status'] == status


@pytest.mark.asyncio
@pytest.mark.parametrize('email', ['Bearer secret', '<script>@evil', 'a\n@evil', 'a' * 65 + '@example.com', {'token': 'secret'}])
async def test_invalid_identity_not_echoed(native, email):
    _, result = native
    result[0] = ProcessResult(0, json.dumps({'loggedIn': True, 'email': email}).encode())
    assert 'username' not in await accounts.inspect_account('claude')


@pytest.mark.asyncio
@pytest.mark.parametrize('provider,canonical', [('gemini', 'antigravity'), ('agy', 'antigravity')])
async def test_unsupported_status_is_unknown_without_launch_or_credential_read(native, monkeypatch, provider, canonical):
    calls, _ = native
    monkeypatch.setattr(accounts.Path, 'read_text', lambda *args, **kwargs: pytest.fail('must not inspect credentials'))
    view = await accounts.inspect_account(provider)
    assert view['provider'] == canonical and view['status'] == 'unknown'
    assert not calls


@pytest.mark.asyncio
async def test_unavailable_cli_does_not_run(native, monkeypatch):
    calls, _ = native
    monkeypatch.setattr(accounts, 'locate_command', lambda _: None)
    assert (await accounts.inspect_account('codex'))['status'] == 'unavailable'
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize('result', [ProcessResult(0, b'{"loggedIn":true}', timed_out=True),
                                  ProcessResult(0, b'{"loggedIn":true}', truncated=True),
                                  OSError('secret path and credentials')])
async def test_unknown_on_timeout_truncation_or_spawn_error(native, result):
    native[1][0] = result
    view = await accounts.inspect_account('claude')
    assert view['status'] == 'unknown' and 'secret' not in json.dumps(view)


@pytest.mark.asyncio
async def test_status_child_is_fixed_bounded_sanitized_and_outside_workspace(native):
    env = {'HOME': '/home/example', 'PATH': '/bin', 'DREAM_GUI_TOKEN': 'secret',
           'CLAUDECODE': 'nested', 'NODE_OPTIONS': '--require=evil', 'LD_PRELOAD': 'evil',
           'PYTHONPATH': str(accounts.Path(__file__).resolve().parents[1])}
    before = dict(env)
    await accounts.inspect_account('codex', env=env)
    argv, kw = native[0][0]
    assert str(kw['cwd']) == '/' and kw['timeout'] == 5 and kw['max_output'] == 16384
    assert not any(name in kw['env'] for name in ['DREAM_GUI_TOKEN', 'CLAUDECODE', 'NODE_OPTIONS', 'LD_PRELOAD', 'PYTHONPATH'])
    assert env == before


@pytest.mark.asyncio
async def test_cancellation_propagates(native):
    native[1][0] = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await accounts.inspect_account('claude')


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['claude; evil', '../../secret', None, {}, 'x' * 100])
async def test_untrusted_provider_refused_without_echo_or_launch(native, provider):
    with pytest.raises(ValueError, match='Unknown sign-in provider'):
        await accounts.inspect_account(provider)
    assert not native[0]


@pytest.mark.asyncio
@pytest.mark.parametrize('timeout', [0, -1, 16, float('nan'), float('inf'), True, '5'])
async def test_invalid_timeout_refused(native, timeout):
    with pytest.raises(ValueError, match='timeout'):
        await accounts.inspect_account('claude', timeout=timeout)
    assert not native[0]


@pytest.mark.asyncio
async def test_combined_checks_have_no_login_or_generation(native):
    native[1][0] = ProcessResult(0, b'{"loggedIn":true}')
    result = await accounts.inspect_accounts()
    assert [item['provider'] for item in result] == list(accounts.TARGET_KEYS)
    assert [call[0][1:] for call in native[0]] == [('auth', 'status', '--json'), ('login', 'status'), ('models',)]


# Grok (the owner, 2026-10-11): `grok models` opens with the CLI's own statement of its sign-in, which is the
# evidence; the model list after it proves nothing and is never read.
@pytest.mark.asyncio
@pytest.mark.parametrize('code,body,status', [
    (0, b'You are logged in with grok.com.\n\nDefault model: grok-4.7\n\nAvailable models:\n  grok-4.7\n', 'signed_in'),
    (0, b'You are not authenticated.\n\nDefault model: grok-4.6\n', 'signed_out'),
    (0, b'Default model: grok-4.6\nsecret=sk-secret\n', 'unknown'),
    (1, b'You are logged in with grok.com.\n', 'unknown'),
    (0, b'Note: You are logged in with grok.com.\n', 'unknown'),
])
async def test_grok_reads_only_its_own_sign_in_line_and_never_leaks_output(native, code, body, status):
    native[1][0] = ProcessResult(code, body)
    view = await accounts.inspect_account('grok')
    assert view['status'] == status
    assert native[0][-1][0][1:] == ('models',)
    assert 'grok-4' not in json.dumps(view) and 'secret' not in json.dumps(view)
