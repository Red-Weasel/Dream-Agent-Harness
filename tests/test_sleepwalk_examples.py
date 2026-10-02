"""Sleepwalk phase 5 (DREAM-162): the published example connectors are valid, send only to the configured
destination (a fake HTTP server here), and are never loaded by Dream unless installed as a trusted plugin."""
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from dream import config, plugins
from dream.sleepwalk import connectors

EXAMPLES = Path(connectors.__file__).parent.parent / 'examples'
SEEN = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        SEEN.append((self.path, dict(self.headers), self.rfile.read(int(self.headers['Content-Length']))))
        self.send_response(204 if 'discord' in self.path else 200)
        self.end_headers()


def load(name):
    spec = importlib.util.spec_from_file_location(f'sleepwalk_example_{name}', EXAMPLES / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def server():
    http = ThreadingHTTPServer(('127.0.0.1', 0), Fake)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    SEEN.clear()
    yield f'http://127.0.0.1:{http.server_address[1]}'
    http.shutdown()


@pytest.mark.parametrize('name,cfg,check', [
    ('pushover', {'app_token': 'app-fixture', 'user_key': 'user-fixture'}, lambda body: b'user=user-fixture' in body),
    ('ntfy', {'topic': 'private-topic'}, lambda body: body == b'All done.'),
    ('slack', {}, lambda body: json.loads(body)['text'].startswith('*Sleepwalk: Notes*')),
    ('discord', {}, lambda body: json.loads(body)['allowed_mentions'] == {'parse': []})])
def test_examples_send_to_the_configured_place_only(server, monkeypatch, name, cfg, check):
    module = load(name)
    assert connectors._valid(module) and module.CONNECTOR['kinds'] == ['notify']
    assert all(f.get('secret') for f in module.CONNECTOR['fields'] if f['name'] != 'server')
    if name == 'pushover':
        monkeypatch.setattr(module, 'API', server + '/1/messages.json')
    cfg = {**cfg, 'server': server, 'webhook_url': f'{server}/hook/{name}'}
    module.send(cfg, 'Notes', 'All done.')
    path, headers, body = SEEN[-1]
    assert check(body) and (path == '/private-topic' if name == 'ntfy' else True)


def test_examples_are_not_loaded_unless_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'PLUGINS_DIR', tmp_path / 'plugins')
    monkeypatch.setattr(plugins, '_LOADED', [])
    assert set(connectors.discover()) == {'gmail', 'gcal', 'telegram'}
    assert sorted(p.stem for p in EXAMPLES.glob('*.py')) == ['discord', 'ntfy', 'pushover', 'slack']
