"""Real PTYs/owned supervisor plus isolated graph authority and receipt checks."""
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from dream.gui.deepsleep import DeepSleep, Node, layout, _request
from dream.gui.server import StudioServer
from dream.gui.bus import EventBus


async def ready_node(board, key):
    n = Node(key, 'codex', '', key, layout({}), state='ready', address=('http://127.0.0.1:1', key))
    board.nodes[key] = n
    return n


@pytest.fixture
def board(tmp_path):
    def request(base, token, payload=None):
        if payload is None:
            return []
        return dict(payload, state='queued', result='', error='')
    return DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:1', child_request=request)


async def test_directed_authority_idempotency_and_cycle(board):
    a, b = await ready_node(board, 'a'), await ready_node(board, 'b')
    with pytest.raises(ValueError, match='Connect'):
        await board.send('b', 'work', source='a')
    board._link('a', 'b', False)
    first = await board.send('b', 'work', source='a', message_id='one', visited=['a'], hops=1)
    assert first['state'] == 'queued'
    assert await board.send('b', 'work', source='a', message_id='one', visited=['a'], hops=1) == first
    with pytest.raises(ValueError, match='different content'):
        await board.send('b', 'changed', source='a', message_id='one', visited=['a'], hops=1)
    with pytest.raises(ValueError, match='cycle'):
        await board.send('b', 'work', visited=['b'])
    with pytest.raises(PermissionError):
        await board.bridge('not-a-token', {'operation': 'list'})
    assert (await board.bridge(a.bridge_token, {'operation': 'list'}))['nodes'][1]['id'] == 'b'
    assert '_envelope' not in json.dumps(board.snapshot())
    assert a.bridge_token not in json.dumps(board.snapshot())


async def test_ordinary_terminal_completion_forwards_once_and_failure_does_not(board):
    a, b = await ready_node(board, 'a'), await ready_node(board, 'b')
    board._link('a', 'b', True)
    owner = dict(id='owner1', source='owner', target='a', root_id='owner1', visited=[], hops=0,
                 state='completed', result='Source-backed answer', error='')
    await board._settle(a, owner)
    await board._settle(a, owner)
    deliveries = [r for r in board.receipts.values() if r['target'] == 'b']
    assert len(deliveries) == 1
    assert deliveries[0]['visited'] == ['a'] and deliveries[0]['hops'] == 1
    await board._settle(a, dict(owner, id='bad', root_id='bad', state='failed', result='partial'))
    assert len([r for r in board.receipts.values() if r['target'] == 'b']) == 1
    await board._settle(a, dict(owner, id='spoof', root_id='spoof', target='b'))
    assert 'spoof' not in board.receipts


async def test_research_join_waits_for_all_and_submits_once(board):
    a, b, c = [await ready_node(board, key) for key in ('a', 'b', 'c')]
    board._link('a', 'c', True)
    board._link('b', 'c', True)
    group = await board.group(['a', 'b'], 'c', 'Research this')
    messages = [dict(board.receipts[key]) for key in group['messages']]
    await board._settle(a, dict(messages[0], state='completed', result='Evidence A'))
    assert group['state'] == 'running'
    assert not any(r['target'] == 'c' for r in board.receipts.values())
    await board._settle(b, dict(messages[1], state='completed', result='Evidence B'))
    assert group['state'] == 'submitted'
    synthesis = [r for r in board.receipts.values() if r['target'] == 'c']
    assert len(synthesis) == 1
    assert 'Evidence A' in synthesis[0]['_envelope']['prompt']
    assert 'Evidence B' in synthesis[0]['_envelope']['prompt']
    await board._join(group['id'])
    assert len([r for r in board.receipts.values() if r['target'] == 'c']) == 1
    await board._settle(c, dict(synthesis[0], state='completed', result='Final synthesis'))
    assert group['state'] == 'completed'


async def test_research_failure_never_submits_synthesis(board):
    a, c = await ready_node(board, 'a'), await ready_node(board, 'c')
    board._link('a', 'c', True)
    group = await board.group(['a'], 'c', 'Research this')
    await board._settle(a, dict(board.receipts[group['messages'][0]], state='failed', error='Check failed'))
    assert group['state'] == 'failed'
    assert not any(r['target'] == 'c' for r in board.receipts.values())


@pytest.mark.parametrize('synthesis_state', ['completed', 'refused'])
async def test_immediate_synthesis_receipt_settles_group(board, synthesis_state):
    from dream.gui.deepsleep import ChildRefused
    a, c = await ready_node(board, 'a'), await ready_node(board, 'c')
    board._link('a', 'c', False)
    group = await board.group(['a'], 'c', 'Research this')
    def instant(base, token, payload=None):
        if synthesis_state == 'refused':
            raise ChildRefused('Target queue refused delivery.')
        return dict(payload, state='completed', result='Checked synthesis', error='')
    board._request = instant
    await board._settle(a, dict(board.receipts[group['messages'][0]], state='completed', result='Evidence'))
    assert group['state'] == ('completed' if synthesis_state == 'completed' else 'failed')
    assert group['synthesis_message'] in board.receipts


@pytest.mark.parametrize('role', ['research', 'synthesis'])
@pytest.mark.parametrize('exit_path', ['waiter', 'close'])
async def test_terminal_exit_settles_group_and_pending_receipt(board, role, exit_path):
    from types import SimpleNamespace
    a, c = await ready_node(board, 'a'), await ready_node(board, 'c')
    board._link('a', 'c', False)
    group = await board.group(['a'], 'c', 'Research this')
    if role == 'synthesis':
        await board._settle(a, dict(board.receipts[group['messages'][0]], state='completed', result='Evidence'))
        assert group['state'] == 'submitted'
        n, message_id = c, group['synthesis_message']
    else:
        n, message_id = a, group['messages'][0]
    # A process can already be reaped when Close races its scheduled waiter.
    n.process = SimpleNamespace(returncode=1, wait=AsyncMock(return_value=1))
    await (board._wait(n) if exit_path == 'waiter' else board.close(n))
    assert board.receipts[message_id]['state'] == 'interrupted'
    assert group['state'] == ('failed' if role == 'research' else 'interrupted')
    if role == 'research':
        assert not any(r['target'] == 'c' for r in board.receipts.values())


async def test_restart_refused_until_close_cleanup_finishes_even_if_waiter_reaps(board, monkeypatch):
    from types import SimpleNamespace
    n = await ready_node(board, 'one')
    n.process = SimpleNamespace(returncode=1, wait=AsyncMock(return_value=1))
    launcher = board._launcher = AsyncMock()
    entered, release = asyncio.Event(), asyncio.Event()
    original, calls = board._interrupt_receipts, 0
    async def held_cleanup(node):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
        await original(node)
    monkeypatch.setattr(board, '_interrupt_receipts', held_cleanup)
    closing = asyncio.create_task(board.close(n))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await board._wait(n)
        with pytest.raises(ValueError, match='already running|closing'):
            await board.start(n)
        launcher.assert_not_awaited()
    finally:
        release.set()
        await closing
    assert n.state == 'exited'


async def test_cancelled_close_caller_keeps_one_owned_cleanup_for_second_caller(board, monkeypatch):
    n = await ready_node(board, 'one')
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    async def held_cleanup(node):
        nonlocal calls
        calls += 1
        node.state = 'stopping'
        entered.set()
        await release.wait()
        node.state = 'exited'
    monkeypatch.setattr(board, '_close', held_cleanup)
    first = asyncio.create_task(board.close(n))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not n.close_task.done()
        second = asyncio.create_task(board.close(n))
        await asyncio.sleep(0)
        assert calls == 1
    finally:
        release.set()
        if second:
            await second
        await n.close_task
    assert n.state == 'exited' and calls == 1


async def test_uncertain_delivery_is_not_replayed_and_poll_can_reconcile(board):
    a = await ready_node(board, 'a')
    calls = []
    def broken(*args):
        calls.append(args)
        raise OSError('socket closed')
    board._request = broken
    receipt = await board.send('a', 'work', message_id='same')
    assert receipt['state'] == 'uncertain'
    await board.send('a', 'work', message_id='same')
    assert len(calls) == 1
    await board._settle(a, dict(board.receipts['same'], state='completed', result='Actual result'))
    assert board.receipts['same']['state'] == 'completed'


async def test_native_bridge_refreshes_host_ancestry_and_blocks_return_cycle(board):
    a, b = await ready_node(board, 'a'), await ready_node(board, 'b')
    board._link('a', 'b', False)
    board._link('b', 'a', False)
    incoming = dict(id='root', source='owner', target='a', root_id='root', visited=[], hops=0,
                    state='running', result='', error='')
    original = board._request
    board._request = lambda base, token, payload=None: {'receipts': [incoming], 'agents': []} if payload is None else original(base, token, payload)
    delivery = await board.bridge(a.bridge_token, {'operation': 'send', 'id': 'child', 'target': 'b', 'prompt': 'work', 'root_id': 'root'})
    assert delivery['root_id'] == 'root' and delivery['visited'] == ['a']
    with pytest.raises(ValueError, match='cycle'):
        await board.bridge(b.bridge_token, {'operation': 'send', 'id': 'return', 'target': 'a', 'prompt': 'work', 'root_id': 'root'})


@pytest.mark.parametrize('answer', ['x' * 65536, '漢' * 20000])
async def test_inbox_previews_bounded_and_exact_answer_retrievable(board, answer):
    a = await ready_node(board, 'a')
    for index in range(128):
        key = 'message' + str(index)
        await board._settle(a, dict(id=key, source='owner', target='a', root_id=key, visited=[], hops=0,
                                    state='completed', result=answer, error=''))
    preview = await board.bridge(a.bridge_token, {'operation': 'inbox'})
    assert len(json.dumps(preview).encode()) < 262144
    assert preview['receipts'][0]['truncated'] is True
    full = await board.bridge(a.bridge_token, {'operation': 'inbox', 'id': 'message0'})
    assert full['receipts'][0]['result'] == answer


async def test_persistence_freezes_workspace_and_never_restarts(tmp_path):
    other = tmp_path / 'other'
    other.mkdir()
    selected = [tmp_path]
    b = DeepSleep(lambda: selected[0], lambda: 'http://127.0.0.1:1')
    await ready_node(b, 'one')
    b.save()
    selected[0] = other
    b.save()
    assert not (other / '.dream').exists()
    restored = DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:1')
    assert restored.nodes['one'].state == 'exited'
    assert restored.nodes['one'].process is None


async def test_endpoints_require_authority_and_scoped_token_cannot_control(board):
    a = await ready_node(board, 'a')
    server = StudioServer(EventBus(), session={'workspace': str(board.workspace_path)})
    server._deepsleep = board
    server.port = 8123
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://127.0.0.1:8123') as client:
        assert (await client.get('/api/deepsleep')).status_code == 401
        assert (await client.get('/api/commands')).status_code == 401
        assert (await client.get('/api/accounts')).status_code == 401
        assert (await client.get('/api/deepsleep', headers={'X-Dream-Token': a.bridge_token})).status_code == 401
        assert (await client.post('/api/deepsleep/bridge', headers={'X-Dream-Token': a.bridge_token}, json={'operation': 'list'})).status_code == 200
        assert (await client.get('/api/deepsleep', headers={'X-Dream-Token': server.token})).status_code == 200


SCRIPT = r'''
import os,json,sys,threading,secrets
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
node=os.environ['DREAM_DEEPSLEEP_NODE_ID'];token=secrets.token_hex(20);records={}
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*a): pass
 def answer(self,data):
  raw=json.dumps({'ok':True,'result':data}).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
 def do_GET(self):
  if self.headers.get('X-Dream-Token')!=token:self.send_error(401);return
  self.answer({'receipts':list(records.values()),'agents':[{'id':'worker','name':'researcher','state':'working'}]})
 def do_POST(self):
  if self.headers.get('X-Dream-Token')!=token:self.send_error(401);return
  data=json.loads(self.rfile.read(int(self.headers['Content-Length'])));records[data['id']]=dict(data,state='completed',result='Checked: '+data['prompt'],error='');self.answer(records[data['id']])
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
path=Path(os.environ['DREAM_DESKTOP_SESSION_FILE']);path.write_text(json.dumps({'pid':os.getpid(),'url':'http://127.0.0.1:'+str(server.server_port)+'/#token='+token}));path.chmod(0o600)
threading.Thread(target=server.serve_forever,daemon=True).start()
print('READY PTY '+node,flush=True)
for line in sys.stdin:
 text=line.strip()
 print('PTY RECEIVED '+text,flush=True)
 if text and not text.startswith('/'):
  key=secrets.token_hex(8);records[key]=dict(id=key,source='owner',target=node,root_id=key,visited=[],hops=0,state='completed',result='Owner: '+text,error='')
'''


async def wait_for(predicate, timeout=8):
    end = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < end, 'Owned terminal did not reach expected state'
        await asyncio.sleep(0.03)


@pytest.fixture
def real_command(tmp_path, monkeypatch):
    script = tmp_path / 'terminal_fixture.py'
    script.write_text(SCRIPT)
    from dream.gui import deepsleep
    original = deepsleep.login_supervised_command
    monkeypatch.setattr(deepsleep, 'login_supervised_command', lambda argv: original([sys.executable, str(script)]))


async def test_actual_pty_resize_input_discovery_link_and_owned_shutdown(tmp_path, real_command):
    b = DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:1')
    try:
        a = await b.create({'provider': 'codex', 'id': 'a'})
        c = await b.create({'provider': 'codex', 'id': 'c'})
        await wait_for(lambda: a.state == c.state == 'ready')
        assert a.process.pid != c.process.pid
        assert a.address != c.address
        assert 'READY PTY' in a.output
        b._link('a', 'c', True)
        b.resize(a, 100, 30)
        import termios, fcntl, struct
        assert struct.unpack('HHHH', fcntl.ioctl(a.master, termios.TIOCGWINSZ, b'\0' * 8))[:2] == (30, 100)
        b.input(a, 'ordinary owner task\n')
        await wait_for(lambda: any(r['target'] == 'c' and r['state'] == 'completed' for r in b.receipts.values()))
        assert 'PTY RECEIVED ordinary owner task' in a.output
        assert a.agents[0]['name'] == 'researcher'
        assert len([r for r in b.receipts.values() if r['target'] == 'c']) == 1
        process = a.process
        await b.action({'action': 'close', 'node': 'a'})
        assert process.returncode is not None
        assert 'a' not in b.nodes and not b.links
    finally:
        await b.stop()
    assert all(n.process.returncode is not None and n.master is None for n in b.nodes.values())


async def test_concurrent_restart_refused_and_prior_generation_settled(tmp_path, real_command):
    b = DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:1')
    a = Node('a', 'codex', '', 'A', layout({}))
    b.nodes['a'] = a
    try:
        starts = await asyncio.gather(b.start(a), b.start(a), return_exceptions=True)
        assert sum(isinstance(r, ValueError) for r in starts) == 1
        await wait_for(lambda: a.state == 'ready')
        old = a.process
        await b.close(a)
        assert old.returncode is not None
        await b.start(a)
        await wait_for(lambda: a.state == 'ready')
        assert a.process is not old and a.master is not None
    finally:
        await b.stop()


async def test_reader_setup_failure_soft_stops_spawned_child(tmp_path, real_command, monkeypatch):
    b = DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:1')
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, 'add_reader', lambda *a: (_ for _ in ()).throw(RuntimeError('fixture reader failure')))
    a = await b.create({'provider': 'codex'})
    assert a.state == 'failed' and a.process.returncode is not None
    assert a.master is None and a.private is None


async def test_cancel_during_spawn_retains_ownership_and_cleans_child(tmp_path, real_command, monkeypatch):
    b = DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:1')
    original = asyncio.create_subprocess_exec
    spawned = asyncio.Event()
    async def delayed(*args, **kwargs):
        process = await original(*args, **kwargs)
        spawned.set()
        await asyncio.sleep(0.08)
        return process
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', delayed)
    task = asyncio.create_task(b.create({'provider': 'codex'}))
    await spawned.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    a = next(iter(b.nodes.values()))
    assert a.process.returncode is not None and a.master is None and a.private is None


async def test_partial_input_write_preserves_suffix(tmp_path, real_command, monkeypatch):
    b = DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:1')
    a = await b.create({'provider': 'codex'})
    try:
        await wait_for(lambda: a.state == 'ready')
        original = os.write
        def partial(fd, data):
            return original(fd, data[:2]) if fd == a.master else original(fd, data)
        monkeypatch.setattr(os, 'write', partial)
        b.input(a, 'all bytes delivered\n')
        await wait_for(lambda: 'PTY RECEIVED all bytes delivered' in a.output)
        assert not a.input_pending
    finally:
        await b.stop()
