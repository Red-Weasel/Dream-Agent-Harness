"""Sleepwalk phase 3 (DREAM-160): credentials, the connector registry, Gmail / Google Calendar / Telegram against fake
IMAP, SMTP and HTTP servers on 127.0.0.1, attachments, and a run that reads context and notifies. No real account,
address or credential appears here; the fixtures use example.invalid names."""
import base64
import imaplib
import json
import smtplib
import socketserver
import sys
import threading
import types
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

import httpx
import pytest

from dream import config, extensions, plugins
from dream.core import council_config, moe
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.sleepwalk import connectors, credentials, runner, store
from dream.sleepwalk.connectors import gcal, gmail, telegram

NY = ZoneInfo('America/New_York')
ADDRESS = 'owner@example.invalid'
CHOICES = [{'key': 'codex', 'label': 'Codex', 'available': True, 'efforts': [], 'models': []}]


class FakeKeyring(types.ModuleType):
    def __init__(self):
        super().__init__('keyring')
        self.items = {}

    def get_password(self, service, key):
        return self.items.get((service, key))

    def set_password(self, service, key, value):
        self.items[(service, key)] = value


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'ROOT', tmp_path / 'sleepwalk')
    monkeypatch.setattr('dream.sleepwalk.background.state', lambda: 'off')   # never ask the real systemctl
    monkeypatch.setattr('dream.sleepwalk.background._logind', lambda prop: '')   # or the real logind
    monkeypatch.setattr(council_config, 'provider_choices', lambda **_: CHOICES)
    monkeypatch.setattr(config, 'PLUGINS_DIR', tmp_path / 'plugins')
    monkeypatch.setattr(plugins, '_LOADED', [])
    ring = FakeKeyring()
    monkeypatch.setitem(sys.modules, 'keyring', ring)
    return ring


def serve(handler_class):
    server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), handler_class)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# --- fake IMAP: just enough of RFC 3501 for login, EXAMINE, SEARCH and FETCH ------------------------------------
IMAP_LOG = []
HEADERS = [b'From: Library <news@example.invalid>\r\nSubject: Opening hours\r\nDate: Fri, 25 Sep 2026 09:00:00 -0400\r\n\r\n',
           b'From: =?utf-8?q?Caf=C3=A9?= <hello@example.invalid>\r\nSubject: Weekly menu\r\nDate: Fri, 25 Sep 2026 10:00:00 -0400\r\n\r\n']


class FakeIMAP(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b'* OK fake IMAP ready\r\n')
        for line in self.rfile:
            tag, command, *rest = line.decode().rstrip('\r\n').split(' ', 2)
            IMAP_LOG.append(' '.join([command.upper(), *rest]))
            out = b''
            if command.upper() == 'CAPABILITY':
                out = b'* CAPABILITY IMAP4rev1 AUTH=PLAIN\r\n'
            elif command.upper() in ('EXAMINE', 'SELECT'):
                out = b'* 2 EXISTS\r\n'
            elif command.upper() == 'SEARCH':
                out = b'* SEARCH 1 2\r\n'
            elif command.upper() == 'FETCH':
                number = int(rest[0].split(' ')[0])
                body = HEADERS[number - 1]
                out = (f'* {number} FETCH (BODY[HEADER.FIELDS (FROM SUBJECT DATE)] {{{len(body)}}}\r\n'.encode()
                       + body + b')\r\n')
            elif command.upper() == 'LOGOUT':
                out = b'* BYE\r\n'
            self.wfile.write(out + f'{tag} OK done\r\n'.encode())
            if command.upper() == 'LOGOUT':
                return


SMTP_LOG = {}


class FakeSMTP(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b'220 fake SMTP\r\n')
        data = None
        for line in self.rfile:
            text = line.decode().rstrip('\r\n')
            if data is not None:
                if text == '.':
                    SMTP_LOG['data'], data = '\n'.join(data), None
                    self.wfile.write(b'250 queued\r\n')
                else:
                    data.append(text)
                continue
            verb = text.split(' ')[0].upper()
            if verb == 'EHLO':
                self.wfile.write(b'250-fake\r\n250 AUTH PLAIN\r\n')
            elif verb == 'AUTH':
                SMTP_LOG['auth'] = base64.b64decode(text.split(' ')[2]).split(b'\0')[1:]
                self.wfile.write(b'235 ok\r\n')
            elif verb == 'RCPT':
                SMTP_LOG.setdefault('rcpt', []).append(text)
                self.wfile.write(b'250 ok\r\n')
            elif verb == 'DATA':
                data = []
                self.wfile.write(b'354 go\r\n')
            elif verb == 'QUIT':
                self.wfile.write(b'221 bye\r\n')
                return
            else:
                self.wfile.write(b'250 ok\r\n')


ICS = '\r\n'.join([
    'BEGIN:VCALENDAR', 'VERSION:2.0',
    'BEGIN:VEVENT', 'UID:a', 'DTSTART;TZID=America/New_York:20260911T090000', 'DTEND;TZID=America/New_York:20260911T093000',
    'RRULE:FREQ=WEEKLY;BYDAY=FR', 'EXDATE;TZID=America/New_York:20261002T090000', 'SUMMARY:Team sync', 'END:VEVENT',
    'BEGIN:VEVENT', 'UID:b', 'DTSTART:20260925T170000Z', 'DTEND:20260925T180000Z', 'SUMMARY:Library visit', 'END:VEVENT',
    'BEGIN:VEVENT', 'UID:c', 'DTSTART;VALUE=DATE:20260926', 'DTEND;VALUE=DATE:20260927', 'SUMMARY:Public holiday',
    'END:VEVENT',
    'BEGIN:VEVENT', 'UID:d', 'DTSTART;TZID=America/New_York:20260904T150000', 'RRULE:FREQ=WEEKLY;BYDAY=FR',
    'SUMMARY:Piano', 'END:VEVENT',
    'BEGIN:VEVENT', 'UID:d', 'RECURRENCE-ID;TZID=America/New_York:20260925T150000',
    'DTSTART;TZID=America/New_York:20260926T110000', 'SUMMARY:Piano (moved)', 'END:VEVENT',
    'BEGIN:VEVENT', 'UID:e', 'DTSTART:not-a-date', 'SUMMARY:Broken', 'END:VEVENT',
    'BEGIN:VEVENT', 'UID:f', 'DTSTART:20260925T120000Z', 'SUMMARY:Long line fol', ' ded here', 'END:VEVENT',
    'END:VCALENDAR', ''])
HTTP_LOG = []


class FakeHTTP(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = ICS.encode() if self.path == '/cal.ics' else b'missing'
        self.send_response(200 if self.path == '/cal.ics' else 404)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        HTTP_LOG.append((self.path, payload))
        ok = self.path.startswith('/botGOODTOKEN/')
        self.send_response(200 if ok else 401)
        self.end_headers()
        self.wfile.write(json.dumps({'ok': ok}).encode())


@pytest.fixture
def servers(monkeypatch):
    imap, smtp = serve(FakeIMAP), serve(FakeSMTP)
    http = ThreadingHTTPServer(('127.0.0.1', 0), FakeHTTP)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    monkeypatch.setattr(gmail.imaplib, 'IMAP4_SSL', lambda host, port, timeout: imaplib.IMAP4(host, imap.server_address[1]))
    monkeypatch.setattr(gmail.smtplib, 'SMTP_SSL',
                        lambda host, port, timeout: smtplib.SMTP(host, smtp.server_address[1], timeout=timeout))
    monkeypatch.setattr(telegram, 'API', f'http://127.0.0.1:{http.server_address[1]}')
    IMAP_LOG.clear(), SMTP_LOG.clear(), HTTP_LOG.clear()
    yield f'http://127.0.0.1:{http.server_address[1]}'
    for server in (imap, smtp, http):
        server.shutdown()


def test_credentials_keyring_then_environment(home, monkeypatch):
    credentials.put('gmail', 'app_password', 'fixture-password')
    assert credentials.get('gmail', 'app_password') == 'fixture-password' and credentials.where('gmail', 'app_password') == 'keyring'
    monkeypatch.setenv('DREAM_SLEEPWALK_TELEGRAM_BOT_TOKEN', 'env-token')
    assert credentials.get('telegram', 'bot_token') == 'env-token' and credentials.where('telegram', 'bot_token') == 'environment'
    monkeypatch.setitem(sys.modules, 'keyring', None)                      # the optional package is not installed
    with pytest.raises(ValueError, match='DREAM_SLEEPWALK_GCAL_ICAL_URL'):
        credentials.put('gcal', 'ical_url', 'x')
    assert credentials.get('gmail', 'app_password') is None


def test_gmail_reads_without_marking_and_mails_only_the_owner(home, servers):
    cfg = {'address': ADDRESS, 'app_password': 'fixture-password', 'imap_host': '127.0.0.1', 'smtp_host': '127.0.0.1'}
    text = gmail.fetch(cfg)
    assert text.splitlines()[0].startswith('- Café <hello@example.invalid>: Weekly menu')
    assert 'Library <news@example.invalid>: Opening hours' in text
    commands = [c.split(' ')[0] for c in IMAP_LOG]
    assert 'EXAMINE' in commands and 'SELECT' not in commands and 'STORE' not in commands
    assert all('BODY.PEEK' in c for c in IMAP_LOG if c.startswith('FETCH'))
    gmail.send(cfg, 'Morning notes', 'Hello.')
    assert [r.upper() for r in SMTP_LOG['rcpt']] == [f'RCPT TO:<{ADDRESS.upper()}>'] and 'Subject: Sleepwalk: Morning notes' in SMTP_LOG['data']


def test_calendar_expands_repeats_and_counts_what_it_cannot_read(home, servers):
    text = gcal.fetch({'ical_url': servers + '/cal.ics'}, moment=datetime(2026, 9, 25, 8, 0, tzinfo=NY))
    summary, today, tomorrow = text.split('\nToday (', 1)[0], *text.split('\nToday (', 1)[1].split('\nTomorrow (')
    assert summary == ('Today (Friday 25 September): 3 event(s); Tomorrow (Saturday 26 September): 2 event(s); '
                       '1 calendar event(s) could not be read')
    assert '- 08:00: Long line folded here' in today and '- 09:00: Team sync' in today
    assert '- 13:00: Library visit' in today and 'Piano' not in today           # moved to Saturday
    assert '- All day: Public holiday' in tomorrow and '- 11:00: Piano (moved)' in tomorrow
    later = gcal.fetch({'ical_url': servers + '/cal.ics'}, moment=datetime(2026, 10, 2, 8, 0, tzinfo=NY))
    assert 'Team sync' not in later.split('\nTomorrow (')[0] and '15:00: Piano' in later     # EXDATE honoured
    with pytest.raises(RuntimeError, match='HTTP 404'):
        gcal.fetch({'ical_url': servers + '/other.ics'})


async def test_registry_setup_run_context_and_notification(home, servers, monkeypatch, tmp_path):
    found = connectors.discover()
    assert set(found) == {'gmail', 'gcal', 'telegram'}
    connectors.save(found, 'gcal', {'ical_url': servers + '/cal.ics'})
    connectors.save(found, 'telegram', {'bot_token': 'GOODTOKEN', 'chat_id': '4242'})
    stored = (store.ROOT / 'connectors.json').read_text()
    assert 'GOODTOKEN' not in stored and 'cal.ics' not in stored and '4242' in stored
    rows = {r['id']: r for r in connectors.describe(found)}
    assert rows['telegram']['ready'] and rows['telegram']['fields'][0] == {
        'name': 'bot_token', 'label': 'Bot token (from BotFather)', 'secret': True, 'set': 'keyring'}
    assert not rows['gmail']['ready']
    seen = {}

    async def consult(provider, question, context='', **kw):
        seen['question'] = question
        return 'Your day is calm.'
    monkeypatch.setattr(moe, 'consult_advisor', consult)
    auto = store.save({'title': 'Briefing', 'icon': 'sun', 'instructions': 'Summarise.', 'triggers': [],
                       'runner': {'provider': 'codex'}, 'connectors': ['gcal', 'gmail'], 'notify': ['telegram']})
    record = await runner.run(auto)
    assert record['status'] == 'OK' and record['notified'] == ['app', 'telegram']
    assert "Context from the owner's connectors, read-only" in seen['question']
    assert '## Google Calendar (read-only)\nToday (' in seen['question']            # today's real date: events vary
    assert record['problems'] and record['problems'][0].startswith('gmail:')        # not set up: noted, run goes on
    path, payload = HTTP_LOG[-1]
    assert path == '/botGOODTOKEN/sendMessage' and payload['chat_id'] == '4242' and 'Your day is calm.' in payload['text']
    connectors.save(found, 'telegram', {'bot_token': 'BADTOKEN'})
    record = await runner.run(auto)
    assert record['notified'] == ['app'] and any('HTTP 401' in p for p in record['problems'])
    assert not any('BADTOKEN' in p for p in record['problems'])                    # the token never leaks into a record


def test_plugin_connectors_need_the_owners_trust(home, tmp_path):
    folder = tmp_path / 'plugins' / 'extra' / 'sleepwalk'
    folder.mkdir(parents=True)
    (folder.parent / 'plugin.yaml').write_text('name: extra\n')
    (folder / 'ping.py').write_text('CONNECTOR = {"id": "ping", "label": "Ping", "kinds": ["notify"], "fields": []}\n'
                                    'def send(cfg, title, body):\n    return None\n')
    plugins.load()
    warnings = []
    assert 'ping' not in connectors.discover(warnings) and any('ping.py' in w for w in warnings)
    review = extensions.review_module('tool:plugin/extra/sleepwalk/ping')
    extensions.trust_module('tool:plugin/extra/sleepwalk/ping', review['sha256'])
    assert 'ping' in connectors.discover()


async def test_attachments_routes_and_the_run_folder(home, monkeypatch, tmp_path):
    seen = {}

    async def consult(provider, question, context='', cwd=None, **kw):
        from pathlib import Path
        seen.update(question=question, files=sorted(p.name for p in Path(cwd).iterdir()))
        return 'Read it.'
    monkeypatch.setattr(moe, 'consult_advisor', consult)
    auto = store.save({'title': 'Notes', 'icon': 'sun', 'instructions': 'Use the file.', 'triggers': [],
                       'runner': {'provider': 'codex'}})
    srv = StudioServer(EventBus(), on_prompt=lambda p: None, session={'workspace': str(tmp_path), 'model': 'fixture'})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://127.0.0.1',
                                 headers={'X-Dream-Token': srv.token}) as client:
        ok = await client.post(f"/api/sleepwalk/attach?id={auto['id']}&name=list.md", content=b'# Shopping\nmilk\n')
        assert ok.json() == {'attachments': ['list.md']}
        for bad in ['../x.md', '.hidden', 'a/b.md']:
            assert (await client.post(f"/api/sleepwalk/attach?id={auto['id']}&name={bad}", content=b'x')).status_code == 400
        assert (await client.post('/api/sleepwalk/attach?id=nothere12345&name=a.md', content=b'x')).status_code == 404
        page = (await client.get('/api/sleepwalk')).json()
        assert page['automations'][0]['attachments'] == ['list.md']
        assert {c['id'] for c in page['connectors']} == {'gmail', 'gcal', 'telegram'}
        record = await runner.run(store.get(auto['id']))
        assert record['status'] == 'OK' and seen['files'] == ['list.md'] and '# Shopping\nmilk' in seen['question']
        saved = await client.post('/api/sleepwalk/connectors', json={'id': 'telegram', 'values': {'bot_token': 'T0KEN'}})
        assert 'T0KEN' not in saved.text and home.items[('dream-sleepwalk', 'telegram.bot_token')] == 'T0KEN'
        assert (await client.post('/api/sleepwalk/detach', json={'id': auto['id'], 'name': 'list.md'})).json() == {'attachments': []}
    assert oct(store.attachments(auto['id']).stat().st_mode & 0o777) == '0o700'
