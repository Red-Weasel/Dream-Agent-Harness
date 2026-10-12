"""A tile's own controls reach its own Dream terminal (the owner, 2026-10-10): the permission mode and Handoff the
Chat tab has, sent to that terminal's control route with its key. Only those; a native CLI keeps its own."""
import pytest

from dream.gui.deepsleep import DeepSleep, Node, layout


@pytest.fixture
def board(tmp_path):
    sent = []

    def control(base, token, payload):
        sent.append((base, token, payload))
        if payload['action'] == 'fresh_start':
            raise ValueError('Handoff waits until Dream is idle: let the current turn finish.')
        return {'mode': 'auto', 'modes': ['ask', 'accept', 'auto'], 'labels': {'auto': 'auto mode on'}}
    ds = DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:1', control_request=control)
    for key, profile in (('dream1', 'dream'), ('native1', 'native')):
        node = ds.nodes[key] = Node(key, 'grok', '', key, layout({}), state='ready', profile=profile)
        node.address = ('http://127.0.0.1:7001', 'child-key') if profile == 'dream' else None
    ds.sent = sent
    return ds


async def test_the_mode_goes_to_the_tiles_own_terminal_with_its_own_key(board):
    result = await board.control('dream1', 'permission_mode')
    assert result['labels'][result['mode']] == 'auto mode on'
    assert board.sent == [('http://127.0.0.1:7001', 'child-key', {'action': 'permission_mode'})]


async def test_the_terminals_own_refusal_comes_back_as_said(board):
    with pytest.raises(ValueError, match='waits until Dream is idle'):
        await board.control('dream1', 'fresh_start')


@pytest.mark.parametrize('action', ['interrupt', 'agent_stop', 'project_open', 'council_ask', 'login'])
async def test_nothing_but_the_tiles_controls_is_relayed(board, action):
    with pytest.raises(ValueError, match='Unknown terminal control'):
        await board.control('dream1', action)
    assert board.sent == []


async def test_a_native_cli_or_a_terminal_not_ready_is_refused_plainly(board):
    with pytest.raises(ValueError, match='native CLI'):
        await board.control('native1', 'permission_mode_status')
    board.nodes['dream1'].state = 'exited'
    with pytest.raises(ValueError, match='not ready'):
        await board.control('dream1', 'permission_mode_status')
    assert board.sent == []


def test_the_route_answers_only_the_owners_page(tmp_path):
    from starlette.testclient import TestClient
    from dream.gui.bus import EventBus
    from dream.gui.server import StudioServer
    srv = StudioServer(EventBus(), session={'workspace': str(tmp_path), 'session_id': 's1'})
    with TestClient(srv.app, base_url='http://127.0.0.1') as client:
        assert client.post('/api/deepsleep/control', json={'node': 'x', 'action': 'permission_mode'}).status_code == 401
        refused = client.post('/api/deepsleep/control', headers={'X-Dream-Token': srv.token},
                              json={'node': 'nobody', 'action': 'permission_mode'})
    assert refused.status_code == 400 and 'does not exist' in refused.json()['error']


async def test_a_real_dream_terminal_reports_and_cycles_its_mode(tmp_path, monkeypatch):
    """A real Dream terminal process on a controlled model server (no GPU) answers the tile's mode control."""
    import threading
    from dataclasses import replace
    from http.server import ThreadingHTTPServer
    from dream import config
    from dream.core import providers
    from dream.gui.bus import EventBus
    from dream.gui.server import StudioServer
    from test_deepsleep_live_integration import ModelHTTP, eventually
    http = ThreadingHTTPServer(('127.0.0.1', 0), ModelHTTP)
    http.requests = []
    threading.Thread(target=http.serve_forever, daemon=True).start()
    (tmp_path / 'runtime').mkdir()
    (tmp_path / 'ws').mkdir()
    monkeypatch.setattr(config, 'ROOT', tmp_path / 'runtime')
    monkeypatch.setitem(providers.PROVIDERS, 'machx', replace(providers.PROVIDERS['machx'], base_url=f'http://127.0.0.1:{http.server_port}/v1'))
    s = StudioServer(EventBus(), session={'session_id': 'tile-controls', 'workspace': str(tmp_path / 'ws'), 'provider': 'machx'})
    await s.start()
    try:
        ds = s._board()
        node = await ds.create({'provider': 'machx', 'title': 'Local'})
        await eventually(lambda: node.state == 'ready' and node.address, 40)
        # The prompt asks where the cursor is and gives up on an answer after 2 s; xterm in a tile answers at once.
        await eventually(lambda: '\x1b[6n' in node.output, 20)
        ds.input(node, '\x1b[20;1R')
        before = await ds.control(node.id, 'permission_mode_status')
        after = await ds.control(node.id, 'permission_mode')
        assert before['mode'] in before['modes'] and after['mode'] != before['mode']
        assert after['labels'][after['mode']]
        status = await ds.control(node.id, 'tile_status')
        assert status['mode'] == after['mode'] and status['read_twice'] is False and 'context' in status
        assert (await ds.control(node.id, 'read_twice')) == {'read_twice': True}
        # The prompt box: a full-width line above `you ›` and one below it, atop the status rows. prompt_toolkit
        # draws the rows once the terminal answers its cursor-position question, as xterm does in a tile.
        import re
        await eventually(lambda: 'you' in node.output, 20)
        mark = len(node.output)
        ds.resize(node, 100, 30)
        # A resize erases the prompt and asks again; the rows are drawn once that question is answered. (Answering
        # before the resize is handled was a race: the answer went to the old question and the new one went unanswered.)
        await eventually(lambda: '\x1b[6n' in node.output[mark:], 20)
        ds.input(node, '\x1b[20;1R')
        clean = lambda: re.sub(r'\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07', '\n',
                               re.sub(r'\x1b\[[0-9;]*m', '', node.output[mark:]))   # colours joined, moves split
        await eventually(lambda: any(word in clean() for word in ('auto mode', 'asking', 'accept', 'plan')), 20)
        rows = [line.strip() for line in clean().replace('\r', '\n').split('\n') if line.strip()]
        at = max(i for i, line in enumerate(rows) if 'you' in line)
        # The line under it carries the prompt optimizer's buttons near its right end (2026-10-11).
        assert any(line.startswith('─' * 40) and '[ Optimizer: ' in line for line in rows[at + 1:]), rows[at:at + 8]
    finally:
        await s.stop()
        http.shutdown()
        http.server_close()
