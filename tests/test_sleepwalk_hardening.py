"""Sleepwalk fix round 4 (DREAM-163): gate 4 and the Codex security review. Fakes only: no model, account or network."""
import asyncio
import logging
import os
import sys
import threading
import time
import types
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

import pytest

from dream import config, extensions, plugins
from dream.core import council_config, moe
from dream.core.cli_review import CODEX_ISOLATION, CODEX_SIGN_IN, runner_env
from dream.sleepwalk import connectors, runner, schedule, store
from dream.sleepwalk.connectors import gcal, gmail
from dream.sleepwalk.scheduler import Scheduler

NY = ZoneInfo('America/New_York')
CHOICES = [{'key': k, 'label': k, 'available': True, 'efforts': [], 'models': []} for k in ('codex', 'xai')]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'ROOT', tmp_path / 'sleepwalk')
    monkeypatch.setattr(council_config, 'provider_choices', lambda **_: CHOICES)
    monkeypatch.setattr(config, 'PLUGINS_DIR', tmp_path / 'plugins')
    monkeypatch.setattr(plugins, '_LOADED', [])
    ring = types.ModuleType('keyring')
    ring.items = {}
    ring.get_password = lambda service, key: ring.items.get(key)
    ring.set_password = lambda service, key, value: ring.items.__setitem__(key, value)
    monkeypatch.setitem(sys.modules, 'keyring', ring)
    return ring


def automation(**extra):
    return store.save({'title': 'Notes', 'icon': 'sun', 'instructions': 'Say hello.',
                       'triggers': [{'every': 'day', 'at': '07:30'}], 'runner': {'provider': 'codex'}, **extra})


def ics(*events):
    return '\r\n'.join(['BEGIN:VCALENDAR', *[line for e in events for line in ('BEGIN:VEVENT', *e, 'END:VEVENT')],
                        'END:VCALENDAR', ''])


# B: hostile calendars -------------------------------------------------------------------------------------------
def test_a_hostile_repeat_rule_is_bounded():
    feed = ics(['UID:x', 'DTSTART:19700101T000000Z', 'RRULE:FREQ=SECONDLY', 'SUMMARY:Every second'],
               *[[f'UID:d{i}', 'DTSTART:19000101T090000Z', 'RRULE:FREQ=DAILY', f'SUMMARY:Daily {i}'] for i in range(2000)],
               ['UID:ok', 'DTSTART:20260925T130000Z', 'SUMMARY:Real event'])
    start = datetime(2026, 9, 25, tzinfo=NY)
    began = time.monotonic()
    found, unread = gcal.events(feed, start, start + timedelta(days=1))
    assert time.monotonic() - began < 10 and unread >= 1 and [e[2] for e in found][-1:] == ['Real event']


class Slow(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        if self.path == '/big':
            self.wfile.write(b'X' * 2_000_000)
        else:
            for _ in range(40):
                self.wfile.write(b'BEGIN:VCALENDAR\r\n')
                self.wfile.flush()
                time.sleep(0.1)


def test_the_download_has_a_size_cap_and_a_deadline(monkeypatch):
    server = ThreadingHTTPServer(('127.0.0.1', 0), Slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    monkeypatch.setattr(gcal, 'MAX_BYTES', 1_000_000)
    monkeypatch.setattr(gcal, 'DEADLINE', 1)
    try:
        for path in ('/big', '/drip'):
            began = time.monotonic()
            with pytest.raises(RuntimeError, match='over 1 MB or took over 1 s'):
                gcal.fetch({'ical_url': base + path})
            assert time.monotonic() - began < 3
    finally:
        server.shutdown()


async def test_context_per_connector_is_capped(home):
    module = types.ModuleType('long')
    module.CONNECTOR = {'id': 'long', 'label': 'Long', 'kinds': ['context'], 'fields': []}
    module.fetch = lambda cfg: 'x' * 50_000
    text, problems = await connectors.gather({'long': module}, ['long'])
    assert problems == [] and len(text) < connectors.CONTEXT_CAP + 100 and text.endswith('the rest was not included]')


def test_httpx_request_logs_are_dropped_inside_connector_calls(home, caplog):
    module = types.ModuleType('loud')
    module.CONNECTOR = {'id': 'loud', 'label': 'Loud', 'kinds': ['context'], 'fields': []}
    module.fetch = lambda cfg: logging.getLogger('httpx').info('GET https://example.invalid/botSECRET') or 'ok'
    with caplog.at_level(logging.INFO, logger='httpx'):
        asyncio.run(connectors.gather({'loud': module}, ['loud']))
        assert 'SECRET' not in caplog.text
        logging.getLogger('httpx').info('outside Sleepwalk')
        assert 'outside Sleepwalk' in caplog.text


# C: no secrets in the runners' environment -------------------------------------------------------------------------
async def test_isolated_runners_get_an_allowlisted_environment(monkeypatch):
    from dream.core import cli_review
    from dream.core.backends import anthropic
    from dream.core.providers import get_provider
    for name, value in [('DREAM_SLEEPWALK_GMAIL_APP_PASSWORD', 'pw'), ('GITHUB_PAT', 'p'), ('DATABASE_URL', 'u'),
                        ('OPENAI_API_KEY', 'sign-in'), ('OPENAI_ORG_NOTE', 'x'), ('ANTHROPIC_API_KEY', 'sign-in'),
                        ('ANTHROPIC_OTHER', 'x'), ('LC_ALL', 'C.UTF-8'), ('XDG_RUNTIME_DIR', '/run/user/1')]:
        monkeypatch.setenv(name, value)
    env = runner_env(CODEX_SIGN_IN)
    assert set(env) <= {'PATH', 'HOME', 'USER', 'LOGNAME', 'SHELL', 'LANG', 'LANGUAGE', 'TERM', 'TMPDIR', 'TZ',
                        *CODEX_SIGN_IN} | {k for k in env if k.startswith(('LC_', 'XDG_'))}
    assert env['OPENAI_API_KEY'] == 'sign-in' and 'OPENAI_ORG_NOTE' not in env and 'GITHUB_PAT' not in env
    assert 'shell_environment_policy.inherit="core"' in CODEX_ISOLATION
    seen = {}

    async def isolation(executable, cwd):
        return list(CODEX_ISOLATION)

    def prepare(self):
        self.executable, self.argv = 'codex', ['codex', 'exec', '-']

    async def run(self, prompt):
        seen['c'] = self
        return 'ok'

    async def stop(self):
        return None
    monkeypatch.setattr(cli_review, 'codex_isolation', isolation)
    monkeypatch.setattr(cli_review.CLIConsultation, 'prepare', prepare)
    monkeypatch.setattr(cli_review.CLIConsultation, 'run', run)
    monkeypatch.setattr(cli_review.CLIConsultation, '_stop', stop)
    await moe._consult_cli(get_provider('codex'), 'q', cwd='/', isolate=True)
    assert seen['c'].env == runner_env(CODEX_SIGN_IN)
    await moe._consult_cli(get_provider('codex'), 'q', cwd='/')
    assert seen['c'].env is None                                                    # the Council: unchanged
    claude = anthropic.claude_code_options(system_prompt='S', cwd='/', mode='ask', model=None, effort=None,
                                           owner_settings=False).env
    blanked = {k for k, v in claude.items() if v == ''}
    assert {'DREAM_SLEEPWALK_GMAIL_APP_PASSWORD', 'GITHUB_PAT', 'DATABASE_URL', 'ANTHROPIC_OTHER'} <= blanked
    assert not blanked & {'ANTHROPIC_API_KEY', 'PATH', 'HOME', 'LC_ALL', 'CLAUDE_CODE_ENTRYPOINT'}
    council = anthropic.claude_code_options(system_prompt='S', cwd='/', mode='ask', model=None, effort=None).env
    assert set(council) == {'CLAUDE_AGENT_SDK_CLIENT_APP'}


# E: a claim re-reads the automation under the lock ---------------------------------------------------------------
def test_a_claim_never_runs_a_turned_off_or_edited_automation(home):
    auto = automation()
    data = store._read()[0]
    data['automations'][0]['last_slot'] = datetime(2026, 9, 24, 7, 30, tzinfo=NY).isoformat()
    store._write(store.ROOT / 'automations.json', data)
    slot, now = datetime(2026, 9, 25, 7, 30, tzinfo=NY), datetime(2026, 9, 25, 7, 31, tzinfo=NY)
    stale = store.load()[0]
    store.save({**stale, 'enabled': False})                                # turned off after the tick read it
    assert store.claim(auto['id'], slot, schedule.GRACE, 'Scheduled', now) is None
    store.save({**stale, 'enabled': True, 'instructions': 'Edited.'})      # a save moves last_slot to now
    assert store.claim(auto['id'], slot, schedule.GRACE, 'Scheduled', now) is None
    data = store._read()[0]
    data['automations'][0]['last_slot'] = datetime(2026, 9, 24, 7, 30, tzinfo=NY).isoformat()
    store._write(store.ROOT / 'automations.json', data)
    fresh = store.claim(auto['id'], slot, schedule.GRACE, 'Scheduled', now)
    assert fresh['instructions'] == 'Edited.' and fresh['pending'] == ['Scheduled']


# pending, F13 --------------------------------------------------------------------------------------------------
async def test_pending_marks_are_per_run_and_survive_a_save(home, monkeypatch):
    auto = automation()
    data = store._read()[0]
    data['automations'][0]['pending'] = ['Scheduled Fri 07:30', 'Scheduled Fri 08:30']
    store._write(store.ROOT / 'automations.json', data)
    store.save({**store.load()[0], 'title': 'Renamed'})
    assert store.load()[0]['pending'] == ['Scheduled Fri 07:30', 'Scheduled Fri 08:30']
    runner.note(store.load()[0], 'Scheduled Fri 08:30', 'SKIPPED', 'still going')
    assert store.load()[0]['pending'] == ['Scheduled Fri 07:30']

    async def crash(*_a, **_k):
        raise RuntimeError('disk full')
    monkeypatch.setattr(runner, 'run', crash)
    await Scheduler()._run(store.load()[0], 'Scheduled Fri 07:30')
    assert store.load()[0]['pending'] == [] and store.runs()[0]['status'] == 'FAILED'
    assert 'disk full' in store.read_run(auto['id'], store.runs()[0]['run'])['error']


# attachments -----------------------------------------------------------------------------------------------------
async def test_attachments_never_follow_links_or_crash_and_quote_bytes(home, monkeypatch, tmp_path):
    seen = {}

    async def consult(provider, question, context='', cwd=None, **kw):
        from pathlib import Path
        seen.update(question=question, files=sorted(p.name for p in Path(cwd).iterdir()))
        return 'Done.'
    monkeypatch.setattr(moe, 'consult_advisor', consult)
    auto = automation()
    folder = store.attachments(auto['id'])
    for i in range(10):
        store.attach(auto['id'], f'wide{i}.txt', ('é' * 20_000).encode())
    (tmp_path / 'outside.txt').write_text('PRIVATE')
    (folder / 'link.txt').symlink_to(tmp_path / 'outside.txt')
    (folder / 'sub').mkdir()
    record = await runner.run(store.get(auto['id']))
    assert record['status'] == 'OK' and 'PRIVATE' not in seen['question'] and 'link.txt' not in seen['files']
    assert any('link.txt skipped' in p for p in record['problems']) and any('sub skipped' in p for p in record['problems'])
    assert len(seen['question'].encode()) < 64 * 1024 + 2_000


# F7, F8, F11, F12, F15 -------------------------------------------------------------------------------------------
def test_a_plugin_tool_and_connector_of_the_same_name_are_both_reviewable(home, tmp_path):
    root = tmp_path / 'plugins' / 'extra'
    (root / 'tools').mkdir(parents=True)
    (root / 'sleepwalk').mkdir()
    (root / 'plugin.yaml').write_text('name: extra\n')
    (root / 'tools' / 'same.py').write_text('X = 1\n')
    (root / 'sleepwalk' / 'same.py').write_text('Y = 2\n')
    plugins.load()
    assert extensions.review_module('tool:plugin/extra/same')['source'] == 'X = 1\n'
    assert extensions.review_module('tool:plugin/extra/sleepwalk/same')['source'] == 'Y = 2\n'


def test_every_n_hours_starts_at_its_time_on_its_day():
    late = {'every': 'hours', 'at': '22:00', 'n': 4, 'from': '2026-09-25'}
    assert schedule.next_slot(late, datetime(2026, 9, 25, 15, 0, tzinfo=NY)) == datetime(2026, 9, 25, 22, 0, tzinfo=NY)
    assert schedule.latest_slot(late, datetime(2026, 9, 25, 21, 0, tzinfo=NY)) is None
    future = {**late, 'from': '2030-01-01'}
    assert schedule.next_slot(future, datetime(2026, 9, 25, 15, 0, tzinfo=NY)) is None   # nothing before its start


def test_setup_values_are_checked_and_subjects_are_one_line(home, monkeypatch):
    found = connectors.discover()
    with pytest.raises(ValueError, match='Gmail address'):
        connectors.save(found, 'gmail', {'address': 'a@example.invalid, b@example.invalid'})
    with pytest.raises(ValueError, match='Bot token'):
        connectors.save(found, 'telegram', {'bot_token': 'abc\x07def'})
    connectors.save(found, 'telegram', {'bot_token': 'TOKEN123\n', 'chat_id': '4242'})
    assert home.items['telegram.bot_token'] == 'TOKEN123'
    with pytest.raises(ValueError, match='one line'):
        automation(title='A\r\nBcc: x@example.invalid')
    sent = []

    class SMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, *a):
            pass

        def send_message(self, message, to_addrs):
            sent.append((message['Subject'], to_addrs))
    monkeypatch.setattr(gmail.smtplib, 'SMTP_SSL', SMTP)
    gmail.send({'address': 'owner@example.invalid', 'app_password': 'p', 'smtp_host': 'h'}, 'A\r\nBcc: x', 'body')
    assert sent == [('Sleepwalk: A Bcc: x', ['owner@example.invalid'])]


# fix round 5 ------------------------------------------------------------------------------------------------------
def readers():
    """This test process's own calendar reader children that are still alive."""
    found = []
    for proc in filter(str.isdigit, os.listdir('/proc')):
        try:
            parent = int(open(f'/proc/{proc}/stat').read().rsplit(')', 1)[1].split()[1])
            if parent == os.getpid() and b'dream.sleepwalk.connectors.gcal' in open(f'/proc/{proc}/cmdline', 'rb').read():
                found.append(proc)
        except (OSError, IndexError, ValueError):
            pass
    return found


class Hostile(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == '/headers':                          # headers, one byte at a time
            for byte in b'HTTP/1.1 200 OK\r\nX-Slow: ' + b'a' * 200:
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
                time.sleep(0.5)
            return
        self.send_response(200)
        if self.path == '/gzip':                             # compressed, dripping empty blocks
            self.send_header('Content-Encoding', 'gzip')
            self.end_headers()
            for _ in range(100):
                self.wfile.write(b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00')
                self.wfile.flush()
                time.sleep(0.2)
            return
        self.end_headers()
        rules = {'/never': ['RRULE:FREQ=MINUTELY;BYMONTH=2;BYMONTHDAY=30'] * 200,
                 '/interval': ['RRULE:FREQ=DAILY;INTERVAL=0;BYMONTH=2']}[self.path]
        self.wfile.write(ics(*[[f'UID:h{i}', 'DTSTART:20260101T000000Z', rule, 'SUMMARY:Hostile'] for i, rule in enumerate(rules)],
                             ['UID:ok', 'DTSTART:20260925T130000Z', 'SUMMARY:Real event']).encode())


@pytest.mark.parametrize('path,outcome', [('/never', 'counted'), ('/interval', 'counted'),
                                          ('/headers', 'stopped'), ('/gzip', 'compressed')])
def test_hostile_feeds_leave_nothing_running(monkeypatch, path, outcome):
    server = ThreadingHTTPServer(('127.0.0.1', 0), Hostile)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(gcal, 'WALL', 4)
    monkeypatch.setattr(gcal, 'EXPAND_SECONDS', 1)
    threads, began = threading.active_count(), time.monotonic()
    try:
        if outcome == 'counted':
            text = gcal.fetch({'ical_url': f'http://127.0.0.1:{server.server_address[1]}{path}'},
                              moment=datetime(2026, 9, 25, 8, 0, tzinfo=NY))
            assert 'Real event' in text and 'could not be read' in text          # counted, not silently dropped
        else:
            with pytest.raises(RuntimeError, match='took over 4 s' if outcome == 'stopped' else 'compressed'):
                gcal.fetch({'ical_url': f'http://127.0.0.1:{server.server_address[1]}{path}'})
        assert time.monotonic() - began < 8
        assert readers() == [] and threading.active_count() <= threads + 1      # the server's own thread only
    finally:
        server.shutdown()


def test_unit_values_are_refused_not_escaped_and_home_is_percent_h(tmp_path, monkeypatch):
    from dream.sleepwalk import background
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    (bin_dir / 'systemctl').write_text('#!/bin/sh\nexit 0\n')
    (bin_dir / 'systemctl').chmod(0o755)
    home = tmp_path / 'home'
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(home / '.config'))
    monkeypatch.setattr(sys, 'executable', str(home / 'dream' / 'python'))
    monkeypatch.setattr(config, 'ROOT', home / 'dream')
    monkeypatch.setenv('PATH', f"{bin_dir}:{home}/.local/bin:/usr/bin")
    monkeypatch.delenv('DREAM_EXTENSION_SETTINGS', raising=False)
    background.turn_on()
    service = (home / '.config' / 'systemd' / 'user' / 'dream-sleepwalk.service').read_text()
    assert 'Environment="PATH=' + str(bin_dir) + ':%h/.local/bin:/usr/bin"' in service
    assert 'Environment="DREAM_ROOT=%h/dream"' in service and str(home) not in service.replace(str(bin_dir), '')
    for bad in ['a"b', 'a%b', 'a\\b', 'a$b', 'a\nb']:
        monkeypatch.setattr(config, 'ROOT', home / bad)
        with pytest.raises(ValueError, match='character a systemd unit would interpret'):
            background.turn_on()


async def test_run_due_needs_an_active_login_session(home, tmp_path, monkeypatch, capsys):
    from dream.sleepwalk import background
    from dream.sleepwalk.scheduler import run_due
    calls = []

    async def consult(*_a, **_k):
        calls.append(1)
        return 'Hello.'
    monkeypatch.setattr(moe, 'consult_advisor', consult)
    automation()
    data = store._read()[0]
    data['automations'][0]['last_slot'] = datetime(2026, 9, 24, 7, 30, tzinfo=NY).isoformat()
    store._write(store.ROOT / 'automations.json', data)
    for state, runs in (('online', 0), ('', 0), ('active', 1)):
        monkeypatch.setattr(background, '_logind', lambda prop, state=state: state if prop == 'State' else 'no')
        await run_due(datetime(2026, 9, 25, 7, 31, tzinfo=NY))
        assert len(calls) == runs
    assert 'no active login session' in capsys.readouterr().out


def test_attachment_folder_links_and_legacy_pending(home, tmp_path):
    auto = automation()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'secret.txt').write_text('PRIVATE')
    folder = store.attachments(auto['id'])
    folder.parent.mkdir(parents=True, exist_ok=True)
    folder.symlink_to(outside)
    notes = []
    assert runner._attach(auto['id'], tmp_path, notes) == '' and 'link' in notes[0]
    data = store._read()[0]
    data['automations'][0]['pending'] = 'Scheduled Fri 07:30'           # written by an older version
    store._write(store.ROOT / 'automations.json', data)
    assert store.pending_of(store.load()[0]) == ['Scheduled Fri 07:30']
    runner.note(store.load()[0], 'Scheduled Fri 07:30', 'FAILED', 'Interrupted')
    assert store.load()[0]['pending'] == []


# fix round 6 ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize('bad', [['RRULE:FREQ=SECONDLY;INTERVAL=0;BYMONTH=2;BYMONTHDAY=30'],
                                 ['RRULE:FREQ=MINUTELY;INTERVAL=0;BYMONTH=2'], ['RRULE:FREQ=YEARLY;BYEASTER=400'],
                                 ['RECURRENCE-ID:garbage'], ['RECURRENCE-ID;VALUE=DATE:2026-09-27']])
def test_one_bad_event_is_counted_and_the_rest_kept(bad):
    feed = ics(['UID:bad', 'DTSTART:20260101T000000Z', *bad, 'SUMMARY:Bad'],
               ['UID:ok', 'DTSTART:20260925T130000Z', 'SUMMARY:Real event'])
    start = datetime(2026, 9, 25, tzinfo=NY)
    found, unread = gcal.events(feed, start, start + timedelta(days=1))
    assert [e[2] for e in found] == ['Real event'] and unread == 1


class Big(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(ics(['UID:big', 'DTSTART:20260925T000000Z', 'RRULE:FREQ=HOURLY', 'SUMMARY:' + 'x' * 1_000_000],
                             ['UID:bad', 'DTSTART:20260101T000000Z', 'RRULE:FREQ=SECONDLY;INTERVAL=0;BYMONTH=2;BYMONTHDAY=30',
                              'SUMMARY:Bad']).encode())


def test_the_readers_answer_is_capped_and_a_bad_rule_does_not_sink_the_feed():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Big)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        text = gcal.fetch({'ical_url': f'http://127.0.0.1:{server.server_address[1]}/'},
                          moment=datetime(2026, 9, 25, 8, 0, tzinfo=NY))
    finally:
        server.shutdown()
    assert len(text.encode()) <= gcal.OUTPUT and '- 00:00: ' + 'x' * 200 + '\n' in text[:1000].replace('20:00', '00:00')
    assert readers() == []


@pytest.mark.parametrize('logout_at,status,consulted', [(0, 'SKIPPED', 0), (1, 'SKIPPED', 1), (99, 'OK', 1)])
async def test_background_runs_recheck_the_login(home, monkeypatch, logout_at, status, consulted):
    from dream.sleepwalk import background
    checks, calls, sent = [], [], []

    def state(prop):
        checks.append(prop)
        return 'active' if len(checks) <= logout_at else 'online'

    async def consult(*_a, **_k):
        calls.append(1)
        return 'Result.'

    async def notify(*args, allowed=None):
        sent.append(args)
        return ['app'], []
    monkeypatch.setattr(background, '_logind', state)
    monkeypatch.setattr(moe, 'consult_advisor', consult)
    monkeypatch.setattr(connectors, 'notify', notify)
    record = await runner.run(automation(notify=['telegram']), 'Scheduled', background=True)
    assert record['status'] == status and len(calls) == consulted and (sent != []) is (status == 'OK')
    assert status == 'OK' or 'logged out' in record['error']


def test_attachments_skip_hard_links_and_a_folder_link(home, tmp_path):
    auto = automation()
    store.attach(auto['id'], 'ok.txt', b'fine')
    outside = tmp_path / 'outside.txt'
    outside.write_text('PRIVATE')
    os.link(outside, store.attachments(auto['id']) / 'hard.txt')
    notes = []
    quoted = runner._attach(auto['id'], tmp_path / 'run', notes) if (tmp_path / 'run').mkdir() is None else ''
    assert 'fine' in quoted and 'PRIVATE' not in quoted and any('hard.txt skipped' in n for n in notes)
    folder = store.attachments(auto['id'])
    folder.rename(tmp_path / 'moved')
    folder.symlink_to(tmp_path / 'moved')                     # the folder swapped for a link
    notes = []
    assert runner._attach(auto['id'], tmp_path / 'run', notes) == '' and 'link' in notes[0]


def test_named_allowlist_and_expanded_unit_paths(tmp_path, monkeypatch):
    from dream.sleepwalk import background
    for name in ('LC_FAKE_SECRET', 'XDG_FAKE_TOKEN'):
        monkeypatch.setenv(name, 'x')
    monkeypatch.setenv('LC_ALL', 'C.UTF-8')
    env = runner_env(CODEX_SIGN_IN)
    assert 'LC_FAKE_SECRET' not in env and 'XDG_FAKE_TOKEN' not in env and env['LC_ALL'] == 'C.UTF-8'
    bin_dir, home = tmp_path / 'bin', tmp_path / 'home'
    bin_dir.mkdir()
    (bin_dir / 'systemctl').write_text('#!/bin/sh\nexit 0\n')
    (bin_dir / 'systemctl').chmod(0o755)
    for name, value in [('HOME', str(home)), ('XDG_CONFIG_HOME', str(home / '.config')), ('PATH', f'{bin_dir}:/usr/bin'),
                        ('DREAM_PLUGINS_DIR', '~/my-plugins')]:
        monkeypatch.setenv(name, value)
    monkeypatch.delenv('DREAM_EXTENSION_SETTINGS', raising=False)
    monkeypatch.setattr(sys, 'executable', str(home / 'dream' / 'python'))
    monkeypatch.setattr(config, 'ROOT', home / 'dream')
    background.turn_on()
    assert 'Environment="DREAM_PLUGINS_DIR=%h/my-plugins"' in (home / '.config/systemd/user/dream-sleepwalk.service').read_text()


def test_connector_trust_keeps_the_settings_file_readable_for_older_builds(home, tmp_path):
    import json
    root = tmp_path / 'plugins' / 'extra'
    (root / 'sleepwalk').mkdir(parents=True)
    (root / 'plugin.yaml').write_text('name: extra\n')
    (root / 'sleepwalk' / 'ping.py').write_text('X = 1\n')
    plugins.load()
    review = extensions.review_module('tool:plugin/extra/sleepwalk/ping')
    extensions.trust_module('tool:plugin/extra/sleepwalk/ping', review['sha256'])
    extensions.set_enabled('tool:plugin/extra/sleepwalk.ping', True)          # a stale id from a test build
    saved = json.loads(extensions.settings_path().read_text())
    assert all(key.split(':', 1)[0] in {'skill', 'plugin', 'mcp', 'hook', 'tool'}     # the kinds every build accepts
               for section in ('overrides', 'trusted_modules') for key in saved[section])
    assert 'tool:plugin/extra/sleepwalk.ping' not in {r['id'] for r in extensions.catalog(refresh=True)['extensions']}


# fix round 7 ------------------------------------------------------------------------------------------------------
def test_forty_thousand_bad_moved_occurrences_are_counted_quickly():
    feed = ics(*[[f'UID:j{i}', 'RECURRENCE-ID:x'] for i in range(40_000)], ['UID:ok', 'DTSTART:20260925T130000Z', 'SUMMARY:Real event'])
    start, began = datetime(2026, 9, 25, tzinfo=NY), time.process_time()
    found, unread = gcal.events(feed, start, start + timedelta(days=1))
    assert [e[2] for e in found] == ['Real event'] and unread == 40_000 and time.process_time() - began < 5


def test_a_cut_answer_is_marked_and_keeps_its_counts(monkeypatch):
    feed = ics(*[[f'UID:e{i}', 'DTSTART:20260925T130000Z', 'SUMMARY:' + 'y' * 190] for i in range(200)],
               ['UID:bad', 'DTSTART:x', 'SUMMARY:Bad'])
    monkeypatch.setattr(gcal, '_download', lambda url: feed)
    text = gcal._read('http://example.invalid', datetime(2026, 9, 25, 8, 0, tzinfo=NY))
    assert text.endswith('\n[truncated]') and len(text) < gcal.OUTPUT // 4
    assert text.splitlines()[0] == ('Today (Friday 25 September): 200 event(s); Tomorrow (Saturday 26 September): '
                                    '0 event(s); 1 calendar event(s) could not be read')


def test_plugins_dir_expands_the_home_folder(tmp_path):
    import subprocess
    done = subprocess.run([sys.executable, '-c', 'from dream import config; print(config.PLUGINS_DIR)'], text=True,
                          capture_output=True, env={**os.environ, 'HOME': str(tmp_path), 'DREAM_PLUGINS_DIR': '~/extra'})
    assert done.stdout.strip() == str(tmp_path / 'extra')


async def test_each_notification_channel_rechecks_the_login(home, monkeypatch):
    sent = []
    states = iter([True, False])

    async def allowed():
        return next(states)
    first, second = (types.ModuleType(n) for n in ('first', 'second'))
    for module in (first, second):
        module.CONNECTOR = {'id': module.__name__, 'label': module.__name__, 'kinds': ['notify'], 'fields': []}
        module.send = lambda cfg, title, body, name=module.__name__: sent.append(name)
    done, errors = await connectors.notify({'first': first, 'second': second}, ['app', 'first', 'second'], 'T', 'B',
                                           allowed=allowed)
    assert sent == ['first'] and done == ['app', 'first'] and errors == ['second: not sent: you logged out']
