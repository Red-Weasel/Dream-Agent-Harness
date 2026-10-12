"""A Gemini light from its own terminal (the owner, 2026-10-11): Antigravity has no sign-in status command, so its
light turns green when one of its DeepSleep panes has reached Dream through its own tools (the board or a link)
within the last fifteen minutes -- an agent running there proves the account works. A Dream workflow terminal's
completed request already counted."""
import time

from starlette.testclient import TestClient

from dream.gui.bus import EventBus
from dream.gui.deepsleep import Node, layout
from dream.gui.server import StudioServer


def server(tmp_path, monkeypatch):
    from dream import config
    from dream.core import provider_accounts
    monkeypatch.setattr(config, 'MEMORY_DIR', tmp_path / 'memory')

    async def unknown(**kwargs):
        return [{'provider': key, 'status': 'unknown', 'detail': 'No status command.', 'checked_at': ''}
                for key in ('anthropic', 'codex', 'grok', 'antigravity')]
    monkeypatch.setattr(provider_accounts, 'inspect_accounts', unknown)
    (tmp_path / 'ws').mkdir()
    srv = StudioServer(EventBus(), session={'workspace': str(tmp_path / 'ws'), 'session_id': 's1'})
    pane = Node('gem1', 'antigravity', '', 'Gemini', layout({}), state='ready', profile='native')
    srv._board().nodes['gem1'] = pane
    return srv, pane


def statuses(client, srv):
    srv._account_checked = 0
    rows = client.get('/api/accounts', headers={'X-Dream-Token': srv.token}).json()['accounts']
    return {row['provider']: row['status'] for row in rows}


def test_a_gemini_pane_that_used_its_tools_turns_the_light_green(tmp_path, monkeypatch):
    srv, pane = server(tmp_path, monkeypatch)
    with TestClient(srv.app, base_url='http://127.0.0.1') as client:
        assert statuses(client, srv)['antigravity'] == 'unknown'                 # running, but no proof yet
        reply = client.post('/api/deepsleep/bridge', headers={'X-Dream-Token': pane.bridge_token}, json={'operation': 'list'})
        assert reply.status_code == 200
        assert statuses(client, srv)['antigravity'] == 'signed_in'
        assert statuses(client, srv)['grok'] == 'unknown'                        # only that provider's light
        pane.tools_used_at = time.monotonic() - 901                              # fifteen minutes on: amber again
        assert statuses(client, srv)['antigravity'] == 'unknown'


def test_a_refused_key_proves_nothing(tmp_path, monkeypatch):
    srv, pane = server(tmp_path, monkeypatch)
    with TestClient(srv.app, base_url='http://127.0.0.1') as client:
        assert client.post('/api/deepsleep/bridge', headers={'X-Dream-Token': 'not-the-key-0123456789'},
                           json={'operation': 'list'}).status_code == 401
        assert statuses(client, srv)['antigravity'] == 'unknown'
