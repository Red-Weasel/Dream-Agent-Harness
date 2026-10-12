"""Provider sign-in (DREAM-246): fixed commands, the request record, the Studio gate and /login.

No model, no network beyond loopback, no real provider CLI: every "installed CLI" is a file in a temporary HOME.
"""
from __future__ import annotations

import os
import stat
from types import SimpleNamespace

import httpx
import pytest

from dream.core import provider_login as pl
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A HOME with nothing installed and a PATH that leads nowhere."""
    empty = tmp_path / 'empty-path'
    empty.mkdir()
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex-home'))
    monkeypatch.setenv('PATH', str(empty))
    monkeypatch.delenv('NVM_DIR', raising=False)
    return tmp_path


def install(folder, name, text='#!/bin/sh\nexit 0\n'):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


# --- 1. the fixed table -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize('name, key, command', [
    ('anthropic', 'anthropic', 'claude auth login'), ('claude', 'anthropic', 'claude auth login'),
    ('codex', 'codex', 'codex login'), ('chatgpt', 'codex', 'codex login'),
    ('grok', 'grok', 'grok login --oauth'),
    ('gemini', 'antigravity', 'agy'),
    ('antigravity', 'antigravity', 'agy'), ('agy', 'antigravity', 'agy'),
    ('  Claude ', 'anthropic', 'claude auth login'),
])
def test_names_and_aliases_select_the_official_command(name, key, command):
    target = pl.resolve(name)
    assert (target.key, target.command) == (key, command)


def test_the_table_is_exactly_four_native_agent_providers():
    assert pl.TARGET_KEYS == ('anthropic', 'codex', 'grok', 'antigravity')
    assert [t.key for t in pl.TARGETS if t.login_only] == []
    assert 'CLI sign-in is separate from desktop sign-in' in pl.resolve('agy').note


@pytest.mark.parametrize('name', [
    '', 'openai', 'claude auth login', 'claude --dangerously-skip-permissions', '/usr/bin/claude', '../claude',
    'claude;id', 'claude\nid', 'grok --device', 'x' * 33, None, 7, ['claude'], {'provider': 'claude'}, b'claude',
])
def test_anything_else_is_refused_and_not_echoed(name):
    with pytest.raises(ValueError) as refused:
        pl.resolve(name)
    if isinstance(name, str) and name.strip() and len(name) > 6:
        assert name.strip() not in str(refused.value)


def test_plan_runs_the_found_file_with_the_fixed_arguments_only(home):
    assert pl.launch_plan(pl.resolve('claude'), dict(os.environ)) is None      # not installed: no plan, no guess
    found = install(home / '.local' / 'bin', 'claude')
    plan = pl.launch_plan(pl.resolve('claude'), dict(os.environ))
    assert plan.argv == (str(found), 'auth', 'login')
    install(home / '.grok' / 'bin', 'grok')
    assert pl.launch_plan(pl.resolve('grok'), {}).argv == (str(home / '.grok' / 'bin' / 'grok'), 'login', '--oauth')
    install(home / '.local' / 'bin', 'agy')
    assert pl.launch_plan(pl.resolve('antigravity'), {}).argv == (str(home / '.local' / 'bin' / 'agy'),)


def test_plan_refuses_a_target_that_is_not_from_the_table(home):
    install(home / '.local' / 'bin', 'claude')
    forged = pl.LoginTarget('anthropic', 'Claude', 'claude', ('--version',), False, '')
    with pytest.raises(ValueError):
        pl.launch_plan(forged, {})


def test_nvm_install_gets_its_own_node_folder_in_front_of_the_child_path_only(home):
    folder = home / '.nvm' / 'versions' / 'node' / 'v22.3.0' / 'bin'
    found = install(folder, 'agy', '#!/usr/bin/env node\n')
    install(folder, 'node')
    before = os.environ['PATH']
    env = {'PATH': before, 'HOME': str(home)}
    plan = pl.launch_plan(pl.resolve('gemini'), env)
    assert plan.argv == (str(found),)
    assert plan.env['PATH'] == str(folder) + os.pathsep + before
    assert env == {'PATH': before, 'HOME': str(home)} and os.environ['PATH'] == before


def test_login_env_strips_dream_state_and_keeps_what_a_browser_sign_in_needs(tmp_path):
    repo = str(tmp_path / 'repo')
    parent = {
        'HOME': '/home/u', 'PATH': '/usr/bin', 'DISPLAY': ':0', 'WAYLAND_DISPLAY': 'wayland-0',
        'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/1000/bus', 'XDG_RUNTIME_DIR': '/run/user/1000',
        'BROWSER': 'firefox', 'GNOME_KEYRING_CONTROL': '/run/user/1000/keyring', 'LANG': 'en_US.UTF-8',
        'DREAM_GUI': '1', 'DREAM_DESKTOP_SESSION_FILE': '/tmp/x/session.json', 'DREAM_ANYTHING_ELSE': 'y',
        'WEBKIT_FORCE_SANDBOX': '0', 'GST_REGISTRY': '/tmp/x/reg', 'CRASHVAR': '/tmp/x/crash.log',
        'PYTHONPATH': os.pathsep.join([repo + '/', '/opt/mine', repo]), 'TERM': 'dumb', 'BAD=NAME': 'x', 'N': 3,
    }
    snapshot = dict(parent)
    child = pl.login_env(parent, repo_root=repo, strip=('CRASHVAR',))
    assert parent == snapshot
    for kept in ('HOME', 'PATH', 'DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'XDG_RUNTIME_DIR',
                 'BROWSER', 'GNOME_KEYRING_CONTROL', 'LANG'):
        assert child[kept] == parent[kept]
    assert not [name for name in child if name.startswith('DREAM_')]
    assert not {'WEBKIT_FORCE_SANDBOX', 'GST_REGISTRY', 'CRASHVAR', 'BAD=NAME', 'N'} & set(child)
    assert child['PYTHONPATH'] == '/opt/mine'                     # the user's own entry survives, Dream's does not
    assert (child['TERM'], child['COLORTERM']) == ('xterm-256color', 'truecolor')
    assert 'PYTHONPATH' not in pl.login_env({'PYTHONPATH': repo}, repo_root=repo)
    assert pl.login_env({'PYTHONPATH': '/opt/mine'})['PYTHONPATH'] == '/opt/mine'


# --- 3. the request record ----------------------------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def broker(desktop=True):
    clock, ids = Clock(), iter(f'{n:016x}' for n in range(0xa1, 0xff))
    return pl.LoginRequests(desktop, clock=clock, new_id=lambda: next(ids)), clock


def test_a_request_is_an_id_and_a_provider_and_is_offered_until_answered():
    requests, _ = broker()
    assert requests.desktop_view() is None
    asked = requests.request('claude')
    assert (asked['status'], asked['provider'], asked['command']) == ('queued', 'anthropic', 'claude auth login')
    assert requests.desktop_view() == {'id': asked['id'], 'provider': 'anthropic'}
    assert requests.desktop_view() == {'id': asked['id'], 'provider': 'anthropic'}     # polling does not consume it
    assert requests.request('anthropic')['id'] == asked['id']                          # asking again: same request
    with pytest.raises(ValueError, match='still waiting'):
        requests.request('codex')                                                      # one waiting at a time
    launched = requests.ack(asked['id'], 'anthropic', 'launched')
    assert launched['status'] == 'launched' and requests.desktop_view() is None
    assert requests.ack(asked['id'], 'anthropic', 'launched') == launched              # a repeat changes nothing
    assert requests.request('codex')['status'] == 'queued'       # an open window does not block the next request


@pytest.mark.parametrize('status', pl.ACK_STATUSES + ('queued', 'expired', 'replaced'))
def test_no_status_ever_claims_the_account_is_signed_in(status):
    text = pl._message(status, pl.resolve('claude')).lower()
    for claim in ('signed in to', 'you are now', 'success', 'logged in', 'authenticated'):
        assert claim not in text
    requests, _ = broker()
    asked = requests.request('claude')
    view = requests.ack(asked['id'], 'anthropic', status) if status in pl.ACK_STATUSES else asked
    assert view['sign_in_checked'] is False and set(view) == {
        'desktop', 'id', 'provider', 'label', 'command', 'status', 'message', 'sign_in_checked'}


def test_answers_must_match_id_and_provider_and_cannot_contradict():
    requests, _ = broker()
    asked = requests.request('grok')
    for bad in (('0' * 16, 'grok', 'launched'), (asked['id'], 'codex', 'launched'), (asked['id'], 'grok', 'signed-in'),
                (asked['id'], 'claude', 'launched'), (asked['id'].upper(), 'grok', 'launched'), (None, 'grok', 'closed'),
                (asked['id'], 'grok', None), (asked['id'] + '0', 'grok', 'launched')):
        with pytest.raises(ValueError):
            requests.ack(*bad)
    assert requests.status(asked['id'])['request']['status'] == 'queued'
    assert requests.ack(asked['id'], 'grok', 'failed')['status'] == 'failed'
    with pytest.raises(ValueError, match='already recorded'):
        requests.ack(asked['id'], 'grok', 'launched')
    second = requests.request('grok')
    assert second['id'] != asked['id']
    requests.ack(second['id'], 'grok', 'launched')
    assert requests.ack(second['id'], 'grok', 'closed')['status'] == 'closed'
    assert requests.ack(second['id'], 'grok', 'launched')['status'] == 'closed'        # the late earlier answer
    with pytest.raises(ValueError):
        requests.ack(second['id'], 'grok', 'failed')


def test_an_unanswered_request_expires_and_says_the_outcome_is_unknown():
    requests, clock = broker()
    asked = requests.request('gemini')
    clock.now += pl.QUEUE_SECONDS - 0.01
    assert requests.desktop_view() is not None
    clock.now += 0.02
    assert requests.desktop_view() is None
    expired = requests.status(asked['id'])['request']
    assert expired['status'] == 'expired' and 'not known' in expired['message']
    fresh = requests.request('gemini')
    assert fresh['id'] != asked['id'] and fresh['status'] == 'queued'
    # The window did open after all: what happened is recorded rather than hidden.
    assert requests.ack(asked['id'], 'antigravity', 'launched')['status'] == 'launched'


def test_only_a_bounded_number_of_requests_is_remembered():
    requests, _ = broker()
    ids = []
    for _ in range(pl.KEEP_RECORDS + 3):
        ids.append(requests.request('codex')['id'])
        requests.ack(ids[-1], 'codex', 'busy')
    assert requests.status(ids[0])['request'] is None
    assert requests.status(ids[-1])['request']['status'] == 'busy'
    with pytest.raises(ValueError):
        requests.ack(ids[0], 'codex', 'busy')


def test_an_open_window_is_never_forgotten_however_many_later_requests_are_refused_as_busy():
    requests, clock = broker()
    first = requests.request('claude')
    requests.ack(first['id'], 'anthropic', 'launched')
    later = []
    for n in range(pl.KEEP_RECORDS * 3):                           # far more than the finished history holds
        asked = requests.request(('codex', 'grok', 'gemini')[n % 3])
        later.append(asked)
        assert requests.desktop_view() == {'id': asked['id'], 'provider': asked['provider']}
        if n % 4 == 3:
            clock.now += pl.QUEUE_SECONDS + 1                      # some are never answered at all
            assert requests.status(asked['id'])['request']['status'] == 'expired'
        else:
            assert requests.ack(asked['id'], asked['provider'], 'busy')['status'] == 'busy'
        assert len(requests._records) <= pl.KEEP_RECORDS + 2       # one open, one waiting, the finished history
        assert requests.status(first['id'])['request']['status'] == 'launched'
    whole = requests.status()
    assert whole['open']['id'] == first['id'] and whole['open']['status'] == 'launched'
    assert whole['request']['id'] == later[-1]['id']               # the newest request is still the default answer
    assert requests.status(later[0]['id'])['request'] is None      # only finished requests were forgotten
    waiting = requests.request('codex')                            # and one more is waiting when the window closes
    closed = requests.ack(first['id'], 'anthropic', 'closed')
    assert closed['status'] == 'closed' and closed['sign_in_checked'] is False
    assert requests.ack(first['id'], 'anthropic', 'closed') == closed          # the repeat still matches
    assert requests.status()['open'] is None
    assert requests.desktop_view() == {'id': waiting['id'], 'provider': 'codex'}
    assert requests.ack(waiting['id'], 'codex', 'launched')['status'] == 'launched'
    assert requests.status()['open']['id'] == waiting['id']


def test_only_one_window_is_ever_on_record_as_open():
    requests, clock = broker()
    opened = []
    for _ in range(pl.KEEP_RECORDS * 3):                           # a desktop that says "launched" every time
        asked = requests.request('grok')
        assert requests.ack(asked['id'], 'grok', 'launched')['status'] == 'launched'
        opened.append(asked['id'])
        assert [r['id'] for r in requests._records.values() if r['status'] == 'launched'] == [asked['id']]
        assert len(requests._records) <= pl.KEEP_RECORDS + 2
    before = requests.status(opened[-2])['request']
    assert before['status'] == 'replaced' and 'taken to have closed' in before['message']
    assert before['sign_in_checked'] is False
    assert requests.ack(opened[-2], 'grok', 'launched')['status'] == 'replaced'       # the late repeat
    with pytest.raises(ValueError, match='already recorded'):
        requests.ack(opened[-2], 'grok', 'failed')
    assert requests.ack(opened[-2], 'grok', 'closed')['status'] == 'closed'           # its own answer, late
    assert requests.status()['open']['id'] == opened[-1]

    # An unanswered request whose window is reported open only after a newer one was: the newer one stays the open one.
    requests, clock = broker()
    old = requests.request('gemini')
    clock.now += pl.QUEUE_SECONDS + 1
    new = requests.request('codex')
    requests.ack(new['id'], 'codex', 'launched')
    assert requests.ack(old['id'], 'antigravity', 'launched')['status'] == 'replaced'
    assert requests.status()['open']['id'] == new['id']
    assert requests.ack(old['id'], 'antigravity', 'closed')['status'] == 'closed'
    assert requests.ack(new['id'], 'codex', 'closed')['status'] == 'closed'


def test_a_refused_request_does_not_touch_the_open_window_or_the_waiting_request():
    requests, _ = broker()
    first = requests.request('claude')
    requests.ack(first['id'], 'anthropic', 'launched')
    waiting = requests.request('codex')
    for _ in range(pl.KEEP_RECORDS * 3):
        with pytest.raises(ValueError, match='still waiting'):
            requests.request('grok')                               # a second waiting request is refused
        with pytest.raises(ValueError, match='Unknown sign-in provider'):
            requests.request('grok --device')
        assert requests.request('chatgpt')['id'] == waiting['id']  # the same one, not another
    assert [(r['id'], r['status']) for r in requests._records.values()] == [
        (first['id'], 'launched'), (waiting['id'], 'queued')]
    assert requests.ack(first['id'], 'anthropic', 'closed')['status'] == 'closed'


# --- the desktop window's own memory of what it was asked (desktop.protocol.LoginTracker) -------------------------

def test_the_window_keeps_an_open_request_until_its_closed_answer_is_delivered():
    from dream.desktop import protocol
    tracker, binding = protocol.LoginTracker(), (41, ('http://127.0.0.1:1', 't'))
    ids = [f'{n:016x}' for n in range(1, protocol._LOGIN_REMEMBERED * 3)]

    def ask(request_id, provider):
        return tracker.take(binding, {'login_request': {'id': request_id, 'provider': provider}})

    assert ask(ids[0], 'anthropic') == (ids[0], 'anthropic')
    assert tracker.report(binding, ids[0], 'anthropic', 'launched')
    tracker.sent(binding, ids[0], 'launched')                      # delivered, and the window is still open
    for request_id in ids[1:]:                                     # every later request is answered busy
        assert ask(request_id, 'codex') == (request_id, 'codex')
        assert ask(request_id, 'codex') is None
        assert tracker.report(binding, request_id, 'codex', 'busy')
        assert tracker.due() == [(request_id, 'codex', 'busy')]
        tracker.sent(binding, request_id, 'busy')
        assert len(tracker._handled) <= protocol._LOGIN_REMEMBERED
    assert ask(ids[0], 'anthropic') is None                        # still known: never opened a second time
    assert not tracker.report(binding, ids[1], 'codex', 'closed')  # a settled, forgotten id is dropped
    assert tracker.report(binding, ids[0], 'anthropic', 'closed') is True       # the answer is still owed
    assert tracker.due() == [(ids[0], 'anthropic', 'closed')]
    tracker.sent(binding, ids[0], 'closed')
    assert tracker.due() == [] and ids[0] not in tracker._open


def test_the_window_takes_no_new_request_while_too_many_answers_are_undelivered():
    from dream.desktop import protocol
    tracker, binding = protocol.LoginTracker(), (42, ('http://127.0.0.1:1', 't'))
    ids = [f'{n:016x}' for n in range(1, protocol._LOGIN_REMEMBERED + 3)]
    for request_id in ids[:protocol._LOGIN_REMEMBERED]:            # the session never accepts an answer
        assert tracker.take(binding, {'login_request': {'id': request_id, 'provider': 'grok'}})
        assert tracker.report(binding, request_id, 'grok', 'busy')
    extra = {'login_request': {'id': ids[-1], 'provider': 'grok'}}
    assert tracker.take(binding, extra) is None                    # nothing is opened that could not be answered
    assert len(tracker.due()) == protocol._LOGIN_REMEMBERED and len(tracker._handled) == protocol._LOGIN_REMEMBERED
    tracker.sent(binding, ids[0], 'busy')
    assert tracker.take(binding, extra) == (ids[-1], 'grok')       # taken once an answer got through
    assert len(tracker._handled) <= protocol._LOGIN_REMEMBERED


def test_without_a_desktop_window_nothing_is_queued_and_the_manual_command_is_given():
    requests, _ = broker(desktop=False)
    answer = requests.request('chatgpt')
    assert (answer['status'], answer['desktop'], answer['id']) == ('manual', False, None)
    assert 'codex login' in answer['message'] and 'separate terminal' in answer['message']
    assert requests.desktop_view() is None and requests.status()['request'] is None


@pytest.mark.parametrize('payload', [
    {'action': 'provider_login'},
    {'action': 'provider_login', 'provider': 'claude', 'args': ['--help']},
    {'action': 'provider_login', 'provider': 'claude', 'command': 'sh'},
    {'action': 'provider_login', 'provider': 'claude', 'env': {'A': 'b'}},
    {'action': 'provider_login', 'provider': 'claude', 'token': 'secret-value'},
    {'action': 'provider_login', 'provider': 'claude', 'url': 'https://example.test'},
    {'action': 'provider_login', 'provider': 'claude', 'cwd': '/'},
    {'action': 'provider_login', 'provider': ['claude']},
    {'action': 'provider_login', 'provider': 'sh -c id'},
    {'action': 'provider_login_status', 'id': 'nope'},
    {'action': 'provider_login_status', 'provider': 'claude'},
    {'action': 'provider_login_ack', 'id': '0' * 16, 'provider': 'anthropic'},
    {'action': 'provider_login_ack', 'id': '0' * 16, 'provider': 'anthropic', 'status': 'launched', 'output': 'x'},
    {'action': 'provider_login_other'},
])
def test_each_action_takes_exactly_its_fields_and_refusal_queues_nothing(payload):
    requests, _ = broker()
    with pytest.raises(ValueError) as refused:
        requests.control(payload)
    assert 'secret-value' not in str(refused.value)
    assert requests.desktop_view() is None and requests.status()['request'] is None


# --- 2. the Studio gate -------------------------------------------------------------------------------------------------

@pytest.fixture
async def studio(tmp_path, monkeypatch):
    monkeypatch.setenv('DREAM_DESKTOP_SESSION_FILE', str(tmp_path / 'session.json'))
    seen = []
    server = StudioServer(EventBus(), session={'session_id': 'one', 'model': 'scripted'},
                          on_control=lambda payload: seen.append(payload) or {'handled': True})
    await server.start()
    base = f'http://127.0.0.1:{server.port}'
    try:
        async with httpx.AsyncClient(trust_env=False, base_url=base) as client:
            yield server, client, seen, base
    finally:
        await server.stop()


async def test_sign_in_actions_sit_behind_the_token_and_origin_and_never_reach_the_session(studio):
    server, client, seen, base = studio
    ask = {'action': 'provider_login', 'provider': 'claude'}
    assert (await client.post('/api/control', json=ask)).status_code == 401
    assert (await client.post('/api/control', json=ask, headers={'X-Dream-Token': 'wrong'})).status_code == 401
    assert (await client.post('/api/control?token=' + server.token, json=ask)).status_code == 401
    crossed = await client.post('/api/control', json=ask,
                                headers={'X-Dream-Token': server.token, 'Origin': 'http://evil.test'})
    assert crossed.status_code == 403
    assert server.login.desktop_view() is None
    auth = {'X-Dream-Token': server.token, 'Origin': base}
    extra = await client.post('/api/control', json={**ask, 'args': ['--help']}, headers=auth)
    assert extra.status_code == 400 and server.login.desktop_view() is None

    asked = (await client.post('/api/control', json=ask, headers=auth)).json()['result']
    assert asked['status'] == 'queued' and asked['sign_in_checked'] is False
    assert (await client.get('/api/desktop')).status_code == 401
    desktop = (await client.get('/api/desktop', headers={'X-Dream-Token': server.token})).json()
    assert desktop['login_request'] == {'id': asked['id'], 'provider': 'anthropic'}

    ack = {'action': 'provider_login_ack', 'id': asked['id'], 'provider': 'anthropic', 'status': 'launched'}
    assert (await client.post('/api/control', json=ack)).status_code == 401
    assert (await client.post('/api/control', json={**ack, 'provider': 'codex'}, headers=auth)).status_code == 400
    assert (await client.post('/api/control', json=ack, headers=auth)).json()['result']['status'] == 'launched'
    assert (await client.post('/api/control', json=ack, headers=auth)).json()['result']['status'] == 'launched'
    desktop = (await client.get('/api/desktop', headers={'X-Dream-Token': server.token})).json()
    assert desktop['login_request'] is None
    status = (await client.post('/api/control', json={'action': 'provider_login_status', 'id': asked['id']},
                                headers=auth)).json()['result']
    assert status['request']['status'] == 'launched' and len(status['targets']) == 4

    assert seen == []                                    # no sign-in action was handed to the session
    other = await client.post('/api/control', json={'action': 'permission_mode_status'}, headers=auth)
    assert other.json() == {'ok': True, 'result': {'handled': True}} and len(seen) == 1


async def test_sign_in_is_answered_while_the_session_control_is_busy_or_absent(tmp_path, monkeypatch):
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    server = StudioServer(EventBus(), session={})
    await server.start()
    try:
        async with httpx.AsyncClient(trust_env=False, base_url=f'http://127.0.0.1:{server.port}') as client:
            auth = {'X-Dream-Token': server.token}
            answer = await client.post('/api/control', json={'action': 'provider_login', 'provider': 'agy'},
                                       headers=auth)
            assert answer.json()['result']['status'] == 'manual' and 'agy' in answer.json()['result']['message']
            assert (await client.post('/api/control', json={'action': 'council_status'},
                                      headers=auth)).status_code == 503
            assert (await client.get('/api/desktop', headers=auth)).json()['login_request'] is None
    finally:
        await server.stop()


# --- 2. /login ------------------------------------------------------------------------------------------------------------

class NoModel:
    """An engine whose every use is an error: /login must not touch it."""
    store = None

    def __getattr__(self, name):
        raise AssertionError(f'/login used the engine: {name}')


class Console:
    def __init__(self):
        self.lines = []

    def print(self, text, **_):
        self.lines.append(str(text))


async def slash(line, studio=None):
    from dream.tui.app import App
    console = Console()

    async def no_turn(*_args, **_kwargs):
        raise AssertionError('/login started a turn')

    app = SimpleNamespace(engine=NoModel(), renderer=SimpleNamespace(console=console), studio=studio,
                          _run_turn=no_turn, _runtime_control=no_turn)
    assert await App._command(app, line) is False
    return console.lines


async def test_slash_login_in_a_plain_terminal_gives_the_manual_command_and_runs_nothing(home, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: (_ for _ in ()).throw(AssertionError('spawned')))
    lines = await slash('/login chatgpt')
    assert len(lines) == 1 and 'codex login' in lines[0] and 'separate terminal' in lines[0]
    assert 'does not check' in lines[0]
    listing = await slash('/login')
    assert listing[0].startswith('usage: /login') and len(listing) == 5
    assert any('agy' in line for line in listing)
    assert not any('sign-in only' in line for line in listing)
    refused = await slash('/login claude --dangerously-skip-permissions')
    assert len(refused) == 1 and refused[0].startswith('Unknown sign-in provider')


async def test_slash_login_in_the_desktop_makes_the_same_request_as_the_button(tmp_path, monkeypatch):
    monkeypatch.setenv('DREAM_DESKTOP_SESSION_FILE', str(tmp_path / 'session.json'))
    server = StudioServer(EventBus(), session={})                 # never started: the request needs no network
    lines = await slash('/login agy', server)
    assert 'Opening a separate sign-in window for Gemini (Antigravity)' in lines[0]
    waiting = server.login.desktop_view()
    assert waiting['provider'] == 'antigravity'
    assert server.login.control({'action': 'provider_login', 'provider': 'antigravity'})['id'] == waiting['id']
    assert 'still waiting' in (await slash('/login grok', server))[0]
    assert (await slash('/login nonsense', server))[0].startswith('Unknown sign-in provider')
    assert server.login.desktop_view() == waiting


# --- 2. /login typed into a Studio prompt box (POST /api/prompt) ------------------------------------------------------

class Recording(EventBus):
    """The session's bus, keeping every event published on it."""

    def __init__(self):
        super().__init__()
        self.seen = []

    def publish(self, event):
        self.seen.append(event)
        return super().publish(event)


class Session:
    """What a session gives Studio for messages. `forbid` makes any delivery to it an error as well as a record."""

    def __init__(self, forbid=True):
        self.forbid, self.prompts, self.steers = forbid, [], []

    def on_prompt(self, text):
        self.prompts.append(text)
        if self.forbid:
            raise AssertionError('/login reached the message queue')

    def on_steer(self, text, identifier, target):
        self.steers.append((text, identifier, target))
        if self.forbid:
            raise AssertionError('/login reached live steering')
        return {'status': 'pending', 'id': identifier}


@pytest.fixture
def prompt_box(tmp_path, monkeypatch, home):
    """A factory: a started Studio server (in the desktop or not, with a session or with no model chosen) and an
    HTTP client for it. HOME and CODEX_HOME are temporary and no CLI is installed."""
    import contextlib

    @contextlib.asynccontextmanager
    async def opened(*, desktop=True, session=None, model=True):
        if desktop:
            monkeypatch.setenv('DREAM_DESKTOP_SESSION_FILE', str(tmp_path / 'session.json'))
        else:
            monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
        session = session or Session()
        bus = Recording()
        server = StudioServer(bus, session={'session_id': 'one', 'model': 'scripted'},
                              **({'on_prompt': session.on_prompt, 'on_steer': session.on_steer} if model else {}))
        await server.start()
        bus.seen.clear()                      # only what the requests of a test publish is counted
        base = f'http://127.0.0.1:{server.port}'
        try:
            async with httpx.AsyncClient(trust_env=False, base_url=base) as client:
                yield SimpleNamespace(server=server, client=client, session=session, bus=bus, base=base,
                                      auth={'X-Dream-Token': server.token, 'Origin': base})
        finally:
            await server.stop()
    return opened


async def test_login_from_the_prompt_box_is_answered_while_a_turn_runs_and_reaches_no_model(prompt_box):
    async with prompt_box() as s:                                  # every delivery to the session is an error here
        queued = await s.client.post('/api/prompt', json={'prompt': '/login claude', 'attachments': []},
                                     headers=s.auth)
        assert queued.status_code == 200 and queued.headers['cache-control'] == 'no-store'
        answer = queued.json()
        assert set(answer) == {'ok', 'command', 'result'} and answer['ok'] is True and answer['command'] == 'login'
        result = answer['result']
        assert (result['status'], result['provider'], result['sign_in_checked']) == ('queued', 'anthropic', False)
        assert s.server.login.desktop_view() == {'id': result['id'], 'provider': 'anthropic'}

        # The Steer button's payload, as a page sends it during a turn: the same request, and no steering receipt.
        steered = await s.client.post('/api/prompt', headers=s.auth, json={
            'prompt': '  /LOGIN   Claude  ', 'attachments': [], 'delivery': 'steer', 'steering_id': 'ab' * 16,
            'steering_target': 'turn-7', 'read_twice': True, 'artifact': 'file:notes.md'})
        assert steered.status_code == 200
        assert set(steered.json()) == {'ok', 'command', 'result'} and steered.json()['result']['id'] == result['id']

        listing = (await s.client.post('/api/prompt', json={'prompt': '/login'}, headers=s.auth)).json()
        assert listing['command'] == 'login' and listing['result']['status'] == 'help'
        assert listing['result']['message'].startswith('usage: /login')
        assert [t['key'] for t in listing['result']['targets']] == list(pl.TARGET_KEYS)
        assert listing['result']['sign_in_checked'] is False
        assert s.server.login.desktop_view() == {'id': result['id'], 'provider': 'anthropic'}     # help asks nothing

        # The desktop window answers; the same record the Council button reads now says so.
        s.server.login.ack(result['id'], 'anthropic', 'unavailable')
        status = (await s.client.post('/api/control', json={'action': 'provider_login_status', 'id': result['id']},
                                      headers=s.auth)).json()['result']['request']
        assert status['status'] == 'unavailable' and 'was not found' in status['message']

        assert s.session.prompts == [] and s.session.steers == []  # nothing was queued and nothing was steered
        assert s.bus.seen == []                                    # no user message, no turn, no event at all


async def test_login_from_the_prompt_box_works_with_no_model_chosen_and_outside_the_desktop(prompt_box):
    async with prompt_box(desktop=False, model=False) as s:
        answer = await s.client.post('/api/prompt', json={'prompt': '/login agy', 'attachments': []}, headers=s.auth)
        assert answer.status_code == 200 and answer.json()['command'] == 'login'
        result = answer.json()['result']
        assert (result['status'], result['id'], result['sign_in_checked']) == ('manual', None, False)
        assert 'separate terminal' in result['message'] and 'agy' in result['message']
        assert s.server.login.desktop_view() is None
        # Everything else about the route is as it was with no model chosen.
        assert (await s.client.post('/api/prompt', json={'prompt': 'hello'}, headers=s.auth)).status_code == 503
        assert (await s.client.post('/api/prompt', json={'prompt': '/loginx'}, headers=s.auth)).status_code == 503
        assert (await s.client.post('/api/prompt', content=b'{', headers=s.auth)).status_code == 503
        assert s.bus.seen == []


async def test_login_from_the_prompt_box_sits_behind_the_token_and_the_page_boundary(prompt_box):
    async with prompt_box() as s:
        ask = {'prompt': '/login claude', 'attachments': []}
        token = {'X-Dream-Token': s.server.token}
        assert (await s.client.post('/api/prompt', json=ask)).status_code == 401
        assert (await s.client.post('/api/prompt', json=ask, headers={'X-Dream-Token': 'wrong'})).status_code == 401
        assert (await s.client.post('/api/prompt?token=' + s.server.token, json=ask)).status_code == 401
        for headers in ({**token, 'Origin': 'http://evil.test'}, {**token, 'Origin': 'http://127.0.0.1:1'},
                        {**token, 'Sec-Fetch-Site': 'cross-site'}, {**token, 'Host': 'evil.test'}):
            assert (await s.client.post('/api/prompt', json=ask, headers=headers)).status_code == 403
        assert s.server.login.desktop_view() is None and s.server.login.status()['request'] is None
        assert s.session.prompts == [] and s.session.steers == [] and s.bus.seen == []


@pytest.mark.parametrize('payload, says', [
    ({'prompt': '/login openai'}, 'Unknown sign-in provider'),
    ({'prompt': '/login claude --dangerously-skip-permissions'}, 'Unknown sign-in provider'),
    ({'prompt': '/login claude sk-secret-value'}, 'Unknown sign-in provider'),
    ({'prompt': '/login claude\nthen delete the folder sk-secret-value'}, 'Unknown sign-in provider'),
    ({'prompt': '/login https://example.test/?sk-secret-value'}, 'Unknown sign-in provider'),
    ({'prompt': '/login ' + 'x' * 5000}, 'Unknown sign-in provider'),
    ({'prompt': '/login claude', 'attachments': ['sk-secret-value']}, 'cannot carry attached files'),
    ({'prompt': '/login claude', 'attachments': 'sk-secret-value'}, 'cannot carry attached files'),
    ({'prompt': '/login', 'attachments': ['sk-secret-value']}, 'cannot carry attached files'),
    ({'prompt': '/login claude', 'env': {'ANTHROPIC_API_KEY': 'sk-secret-value'}}, 'nothing else'),
    ({'prompt': '/login claude', 'url': 'https://example.test/sk-secret-value'}, 'nothing else'),
    ({'prompt': '/login claude', 'token': 'sk-secret-value'}, 'nothing else'),
    ({'prompt': '/login claude', 'args': ['--sk-secret-value']}, 'nothing else'),
    ({'prompt': '/login claude', 'provider': 'codex'}, 'nothing else'),
    ({'prompt': '/login claude', 'delivery': 'sk-secret-value'}, 'Unknown prompt delivery mode'),
])
async def test_a_refused_login_command_asks_nothing_reaches_nothing_and_repeats_nothing(prompt_box, payload, says):
    async with prompt_box() as s:
        refused = await s.client.post('/api/prompt', json=payload, headers=s.auth)
        assert refused.status_code == 400
        assert set(refused.json()) == {'error', 'command'} and refused.json()['command'] == 'login'
        assert says in refused.json()['error']
        for typed in ('sk-secret-value', 'dangerously', 'example.test', 'xxxx', 'delete the folder'):
            assert typed not in refused.text
        assert s.server.login.desktop_view() is None and s.server.login.status()['request'] is None
        assert s.session.prompts == [] and s.session.steers == [] and s.bus.seen == []


async def test_only_the_exact_login_word_is_the_command_and_other_messages_travel_as_before(prompt_box):
    async with prompt_box(session=Session(forbid=False)) as s:
        ordinary = ['/loginx claude', '/log in claude', 'please /login claude', '@notes.md\n/login claude',
                    '/login-claude', '/logins']
        for text in ordinary:
            sent = await s.client.post('/api/prompt', json={'prompt': text, 'attachments': []}, headers=s.auth)
            assert sent.status_code == 200 and sent.json() == {'ok': True}
        assert s.session.prompts == ordinary and len(s.bus.seen) == len(ordinary)
        assert s.server.login.desktop_view() is None and s.server.login.status()['request'] is None

        # A real correction keeps its receipt id: a /login sent with the same id did not use it up.
        steer = {'attachments': [], 'delivery': 'steer', 'steering_id': 'cd' * 16, 'steering_target': 'turn-1'}
        asked = await s.client.post('/api/prompt', json={**steer, 'prompt': '/login grok'}, headers=s.auth)
        assert asked.json()['command'] == 'login' and 'steering' not in asked.json()
        assert s.session.steers == []
        corrected = await s.client.post('/api/prompt', json={**steer, 'prompt': 'Use the smaller change.'},
                                        headers=s.auth)
        assert corrected.json() == {'ok': True, 'steering': {'status': 'pending', 'id': 'cd' * 16}}
        assert s.session.steers == [('Use the smaller change.', 'cd' * 16, 'turn-1')]
        assert s.session.prompts == ordinary                       # and /login never joined the queue

        # The route's own refusals for ordinary messages are unchanged.
        assert (await s.client.post('/api/prompt', content=b'{', headers=s.auth)).json() == {'error': 'bad json'}
        assert (await s.client.post('/api/prompt', json=['/login claude'], headers=s.auth)).json() == {
            'error': 'body must be an object'}
        assert (await s.client.post('/api/prompt', json={'prompt': 'hi', 'delivery': 'later'},
                                    headers=s.auth)).status_code == 400
        assert (await s.client.post('/api/prompt', json={'prompt': ['/login claude']}, headers=s.auth)).json() == {
            'ok': True}                                            # not text: the ordinary route, as before
        assert s.server.login.desktop_view() == {'id': asked.json()['result']['id'], 'provider': 'grok'}
