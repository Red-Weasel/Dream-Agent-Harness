"""Native panes are actual interactive processes, never managed receipt lookalikes."""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from dream.gui import native_terminals as native
from dream.gui.bus import EventBus
from dream.gui.deepsleep import DeepSleep, Node, layout
from dream.gui.server import StudioServer


@pytest.mark.parametrize('provider,exe,workspace_flag,effort_flag', [
    ('anthropic', 'claude', [], ['--effort', 'high']),
    ('codex', 'codex', ['-C'], ['-c', 'model_reasoning_effort="high"']),
    ('grok', 'grok', ['--cwd'], ['--reasoning-effort', 'high']),
    ('antigravity', 'agy', [], ['--effort', 'high']),
])
def test_native_launch_is_interactive_and_strips_parent_authority(tmp_path, monkeypatch, provider, exe, workspace_flag, effort_flag):
    monkeypatch.setattr(native, 'find_cli', lambda name: '/fixture/' + name)
    monkeypatch.setenv('DREAM_DEEPSLEEP_BRIDGE_TOKEN', 'parent-secret')
    monkeypatch.setenv('DREAM_GUI_TOKEN', 'not-a-child-token')
    monkeypatch.setenv('CLAUDECODE', 'nested-session')
    monkeypatch.setenv('NODE_OPTIONS', '--require=untrusted.js')
    argv, env = native.native_launch(provider, 'model;$(not-a-shell)', 'high', tmp_path)
    expected = ['/fixture/' + exe]
    if provider == 'codex':
        expected += ['--no-daemon']
    if workspace_flag:
        expected += workspace_flag + [str(tmp_path)]
    assert argv == expected + ['--model', 'model;$(not-a-shell)'] + effort_flag
    assert not any(name.startswith('DREAM_') for name in env)
    assert 'CLAUDECODE' not in env and 'NODE_OPTIONS' not in env
    assert env['TERM'] == 'xterm-256color'
    assert not any(word in argv for word in ('exec', '--json', '--print', '--headless'))


def test_absent_native_cli_is_actionable_and_local_refuses_native(tmp_path, monkeypatch):
    monkeypatch.setattr(native, 'find_cli', lambda name: None)
    with pytest.raises(ValueError, match='claude is not installed'):
        native.native_launch('anthropic', '', '', tmp_path)
    with pytest.raises(ValueError, match='Dream workflow'):
        native.native_launch('machx', '', '', tmp_path)


def test_managed_launch_effort_must_be_set_by_its_own_command(tmp_path):
    board = DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:1')
    with pytest.raises(ValueError, match='/effort'):
        board._node_config({'provider': 'codex', 'profile': 'dream', 'initial_effort': 'high'})
    assert native.capabilities('codex', 'native')['commands']['effort'] == '/model'
    assert 'remote' not in native.capabilities('codex', 'native')['commands']


def test_persistence_preserves_legacy_links_and_native_profile(tmp_path):
    root = tmp_path / '.dream'
    root.mkdir()
    (root / 'deepsleep.json').write_text(json.dumps({'version': 1, 'nodes': [
        {'id': 'a', 'provider': 'codex'}, {'id': 'b', 'provider': 'anthropic'}],
        'links': [{'id': 'edge', 'source': 'a', 'target': 'b', 'auto_forward': True}]}))
    board = DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:1')
    assert board.nodes['a'].profile == 'dream' and len(board.links) == 1
    board.nodes['n'] = board._node_config({'id': 'n', 'provider': 'codex', 'profile': 'native', 'initial_effort': 'high'})
    board.save()
    restored = DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:1')
    assert restored.nodes['n'].profile == 'native'
    assert restored.nodes['n'].state == 'exited' and restored.nodes['n'].initial_effort == 'high'
    assert restored.snapshot()['workspace'] == str(tmp_path)
    assert restored.snapshot()['nodes'][-1]['capabilities']['linked'] is True
    assert restored.snapshot()['nodes'][-1]['capabilities']['automatic'] is False
    assert len(restored.links) == 1


async def test_native_cannot_forge_managed_delivery_or_bridge(tmp_path):
    board = DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:1')
    board.nodes['n'] = Node('n', 'codex', '', 'Native', layout({}), state='ready', profile='native')
    board.nodes['d'] = Node('d', 'codex', '', 'Dream', layout({}), state='ready')
    with pytest.raises(ValueError, match='Dream workflow'):
        board._link('n', 'd', True)
    with pytest.raises(ValueError, match='Structured task delivery'):
        await board.send('n', 'work')
    # Since 2026-10-10 a native pane's own key reaches the bridge (the board and its own links), as itself only.
    listed = await board.bridge(board.nodes['n'].bridge_token, {'operation': 'list'})
    assert listed['you']['id'] == 'n' and listed['sends_to'] == [] and listed['hears_from'] == []
    with pytest.raises(ValueError, match='Connect a directed link'):
        await board.bridge(board.nodes['n'].bridge_token, {'operation': 'send', 'target': 'd', 'prompt': 'work', 'id': 'forged'})
    with pytest.raises(ValueError, match='Group synthesis'):
        await board.group(['d'], 'n', 'work')
    assert not board.receipts and not board.links and not board.groups


def test_workspace_browser_does_not_follow_symlinks_or_read_files(tmp_path):
    (tmp_path / 'folder').mkdir()
    (tmp_path / 'folder' / 'report.txt').write_text('private contents never listed')
    (tmp_path / 'escape').symlink_to('/etc', target_is_directory=True)
    (tmp_path / '.git').mkdir()
    result = native.workspace_files(tmp_path)
    assert {'name': 'folder', 'path': 'folder', 'directory': True} in result['entries']
    assert not any(row['name'] in ('.git', 'escape') for row in result['entries'])
    result = native.workspace_files(tmp_path, 'folder')
    assert result['entries'][0]['name'] == 'report.txt'
    assert 'private contents' not in json.dumps(result)
    for path in ('../etc', '/etc', 'folder/../', 'folder\\..', '\0'):
        with pytest.raises(ValueError):
            native.workspace_files(tmp_path, path)
    with pytest.raises(OSError):
        native.workspace_files(tmp_path, 'escape')


async def test_node_file_routes_require_owner_and_pinned_workspace(tmp_path):
    (tmp_path / 'report.md').write_text('report')
    current = tmp_path
    server = StudioServer(EventBus(), session=lambda: {'workspace': str(current)})
    board = server._board()
    board.nodes['n'] = Node('n', 'codex', '', 'Native', layout({}), profile='native')
    headers = {'X-Dream-Token': server.token}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://127.0.0.1') as client:
        assert (await client.post('/api/deepsleep/files', json={'node': 'n'})).status_code == 401
        listing = await client.post('/api/deepsleep/files', headers=headers, json={'node': 'n'})
        assert listing.status_code == 200
        assert any(row['name'] == 'report.md' for row in listing.json()['entries'])
        upload = await client.post('/api/deepsleep/upload?node=n&name=photo.png', headers=headers, content=b'fixture')
        assert upload.status_code == 200
        assert (tmp_path / upload.json()['path']).read_bytes() == b'fixture'
        current = tmp_path / 'different'
        current.mkdir()
        switched = await client.post('/api/deepsleep/upload?node=n&name=wrong.txt', headers=headers, content=b'never written')
        assert switched.status_code == 400 and not list(current.iterdir())


async def test_actual_native_pty_owns_input_resize_and_soft_close(tmp_path, monkeypatch):
    # A native-program substitute exercises the actual supervisor and PTY. It
    # reports a menu rather than performing any model request or network call.
    script = tmp_path / 'native.py'
    script.write_text("import os,sys\nprint('NATIVE READY', flush=True)\nfor line in sys.stdin:\n print('MODEL MENU' if line.strip() == '/model' else 'INPUT '+line.strip(), flush=True)\n")
    monkeypatch.setattr('dream.gui.deepsleep.native_launch', lambda *args, **kw: ([sys.executable, '-u', str(script)], dict(os.environ, TERM='xterm-256color')))
    async def substitute_launch(argv, env):
        return argv
    monkeypatch.setattr('dream.gui.deepsleep.compatible_codex_argv', substitute_launch)
    board = DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:1')
    n = await board.create({'provider': 'codex', 'profile': 'native'})
    import asyncio
    try:
        async def wait_for(text):
            async with asyncio.timeout(4):
                while text not in n.output:
                    await asyncio.sleep(.01)
        await wait_for('NATIVE READY')
        # The pane's private folder holds only its board key (2026-10-10); a native pane still has no Dream address.
        assert n.state == 'ready' and n.address is None and [p.name for p in Path(n.private.name).iterdir()] == ['bridge.json']
        board.resize(n, 90, 20)
        board.input(n, '/model\r')
        await wait_for('MODEL MENU')
        assert not board.receipts and len(n.tasks) == 1
    finally:
        await board.stop()
    assert n.process.returncode is not None and n.master is None


async def test_late_owned_spawn_eventually_releases_pty_after_soft_stop_timeout(tmp_path, monkeypatch):
    import asyncio
    import pty
    from unittest.mock import Mock
    exited = asyncio.Event()
    process = SimpleNamespace(returncode=None, send_signal=Mock())
    async def wait():
        await exited.wait()
        process.returncode = 0
        return 0
    process.wait = wait
    original_wait_for = asyncio.wait_for
    async def timeout_stop(awaitable, timeout):
        if timeout == 4:
            raise asyncio.TimeoutError()
        return await original_wait_for(awaitable, timeout)
    monkeypatch.setattr(asyncio, 'wait_for', timeout_stop)
    board = DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:1')
    n = Node('late', 'codex', '', 'Late', layout({}), profile='native')
    board.nodes[n.id] = n
    n.master, slave = pty.openpty()
    os.close(slave)
    n.spawn_task = asyncio.get_running_loop().create_future()
    n.spawn_task.set_result(process)
    try:
        await board.close(n)
        assert n.state == 'stopping' and n.master is not None
        exited.set()
        await original_wait_for(asyncio.gather(*n.tasks), 1)
        assert n.state == 'exited' and n.master is None and n.spawn_task is None
    finally:
        exited.set()
        await board.stop()


@pytest.mark.parametrize('version,has_flag', [('0.114.0', False), ('0.160.1', True)])
async def test_codex_compatibility_probes_same_executable_then_starts_interactive(tmp_path, monkeypatch, version, has_flag):
    import asyncio
    # This executable rejects the flag just as the owner's Snap build does.
    executable = tmp_path / 'codex'
    executable.write_text('#!' + sys.executable + '\n' +
        'import sys\n' +
        f'version={version!r}\nhas_flag={has_flag!r}\n' +
        'if "--help" in sys.argv:\n print("  --no-daemon" if has_flag else "  --no-alt-screen"); sys.exit(0)\n' +
        'if "--version" in sys.argv:\n print("codex-cli " + version); sys.exit(0)\n' +
        'if "--no-daemon" in sys.argv and not has_flag:\n print("unexpected argument --no-daemon",flush=True); sys.exit(2)\n' +
        'print("INTERACTIVE READY",flush=True)\n' +
        'for line in sys.stdin:\n print("MODEL MENU" if line.strip()=="/model" else "INPUT",flush=True)\n')
    executable.chmod(0o700)
    monkeypatch.setattr(native, 'find_cli', lambda _: str(executable))
    board = DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:1')
    try:
        n = await board.create({'provider': 'codex', 'profile': 'native'})
        async with asyncio.timeout(4):
            while 'INTERACTIVE READY' not in n.output:
                await asyncio.sleep(.01)
        assert n.state == 'ready' and 'unexpected argument' not in n.output
        board.input(n, '/model\r')
        async with asyncio.timeout(4):
            while 'MODEL MENU' not in n.output:
                await asyncio.sleep(.01)
        assert not board.receipts
    finally:
        await board.stop()
    assert n.process.returncode is not None and n.master is None


@pytest.mark.parametrize('help_result,version_result', [
    (SimpleNamespace(returncode=1, output=b'unsupported --no-daemon; secret', timed_out=False, truncated=False), None),
    (SimpleNamespace(returncode=0, output=b'  --no-daemon', timed_out=True, truncated=False), None),
    (SimpleNamespace(returncode=0, output=b'  --no-daemon', timed_out=False, truncated=True), None),
    (SimpleNamespace(returncode=0, output=b'  --no-alt-screen', timed_out=False, truncated=False),
     SimpleNamespace(returncode=0, output=b'codex-cli 0.155.0', timed_out=False, truncated=False)),
    (SimpleNamespace(returncode=0, output=b'  --no-alt-screen', timed_out=False, truncated=False),
     SimpleNamespace(returncode=1, output=b'codex-cli 0.114.0', timed_out=False, truncated=False)),
])
async def test_unknown_or_failed_codex_capability_check_never_guesses_or_leaks(help_result, version_result, monkeypatch):
    calls = []
    async def run(argv, **kw):
        calls.append(argv)
        return help_result if argv[-1] == '--help' else version_result
    monkeypatch.setattr(native, 'run_owned', run)
    with pytest.raises(ValueError) as error:
        await native.compatible_codex_argv(['/fixture/codex', '--no-daemon', '-C', '/workspace'], {})
    assert 'secret' not in str(error.value)
    assert calls[0] == ('/fixture/codex', '--help')
    assert all(call[0] == '/fixture/codex' and call[-1] in ('--help', '--version') for call in calls)


async def test_codex_preflight_cancellation_propagates(monkeypatch):
    import asyncio
    async def run(*a, **kw):
        raise asyncio.CancelledError()
    monkeypatch.setattr(native, 'run_owned', run)
    with pytest.raises(asyncio.CancelledError):
        await native.compatible_codex_argv(['/fixture/codex', '--no-daemon'], {})


async def test_terminal_authorization_precedes_replayed_device_query(tmp_path, monkeypatch):
    import asyncio
    from starlette.websockets import WebSocketDisconnect
    from unittest.mock import Mock
    server = StudioServer(EventBus(), session={'workspace': str(tmp_path)})
    board = server._board()
    n = Node('native', 'codex', '', 'Codex', layout({}), profile='native', state='ready', output='\x1b[6n')
    board.nodes[n.id] = n
    replied = asyncio.Event()
    accept_input = Mock(side_effect=lambda *a: replied.set())
    monkeypatch.setattr(board, 'input', accept_input)
    class Socket:
        path_params = {'node': 'native'}
        def __init__(self):
            self.frames, self.incoming = [], asyncio.Queue()
            self.incoming.put_nowait({'token': server.token})
        async def accept(self):
            pass
        async def receive_json(self):
            packet = await self.incoming.get()
            if packet is None:
                raise WebSocketDisconnect()
            return packet
        async def send_json(self, packet):
            self.frames.append(packet)
            if packet.get('data') == '\x1b[6n':
                # A native renderer can answer only after receiving authority.
                if self.frames[0] == {'type': 'state', 'state': 'ready'}:
                    self.incoming.put_nowait({'type': 'input', 'data': '\x1b[1;1R'})
        async def close(self):
            pass
    socket = Socket()
    handling = asyncio.create_task(server._deepsleep_terminal(socket))
    try:
        await asyncio.wait_for(replied.wait(), 1)
        socket.incoming.put_nowait(None)
        await asyncio.wait_for(handling, 1)
        accept_input.assert_called_once_with(n, '\x1b[1;1R')
        assert not n.subscribers
    finally:
        socket.incoming.put_nowait(None)
        await handling


async def test_live_output_during_initial_state_send_is_not_replayed_twice(tmp_path):
    import asyncio
    from starlette.websockets import WebSocketDisconnect
    server = StudioServer(EventBus(), session={'workspace': str(tmp_path)})
    board = server._board()
    n = Node('native', 'codex', '', 'Codex', layout({}), state='ready', profile='native', output='earlier')
    board.nodes[n.id] = n
    state_started, release_state, delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    class Socket:
        path_params = {'node': 'native'}
        def __init__(self):
            self.frames, self.incoming = [], asyncio.Queue()
            self.incoming.put_nowait({'token': server.token})
        async def accept(self):
            pass
        async def receive_json(self):
            packet = await self.incoming.get()
            if packet is None:
                raise WebSocketDisconnect()
            return packet
        async def send_json(self, packet):
            if packet['type'] == 'state':
                state_started.set()
                await release_state.wait()
            self.frames.append(packet)
            if packet.get('data') == 'live':
                delivered.set()
        async def close(self):
            raise WebSocketDisconnect()  # peer already disconnected
    socket = Socket()
    handling = asyncio.create_task(server._deepsleep_terminal(socket))
    try:
        await asyncio.wait_for(state_started.wait(), 1)
        n.output += 'live'
        for subscriber in n.subscribers:
            subscriber.put_nowait({'type': 'output', 'data': 'live'})
        release_state.set()
        await asyncio.wait_for(delivered.wait(), 1)
        socket.incoming.put_nowait(None)
        await asyncio.wait_for(handling, 1)
        assert ''.join(f['data'] for f in socket.frames if f['type'] == 'output') == 'earlierlive'
        assert socket.frames[0]['type'] == 'state' and not n.subscribers
    finally:
        release_state.set()
        socket.incoming.put_nowait(None)
        await handling


@pytest.mark.parametrize('ending', ['close', 'stop'])
async def test_close_during_preflight_prevents_late_interactive_spawn(tmp_path, monkeypatch, ending):
    import asyncio
    from unittest.mock import AsyncMock
    entered, release = asyncio.Event(), asyncio.Event()
    async def preflight(argv, env):
        entered.set()
        await release.wait()
        return argv
    monkeypatch.setattr('dream.gui.deepsleep.native_launch', lambda *a, **k: (['/fixture/codex', '--no-daemon'], {}))
    monkeypatch.setattr('dream.gui.deepsleep.compatible_codex_argv', preflight)
    spawning = AsyncMock()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawning)
    board = DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:1')
    task = asyncio.create_task(board.create({'id': 'race', 'provider': 'codex', 'profile': 'native'}))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        if ending == 'close':
            await board.action({'action': 'close', 'node': 'race'})
            assert 'race' not in board.nodes
        else:
            await board.stop()
        release.set()
        n = await asyncio.wait_for(task, 1)
        assert n.state == 'exited' and n.process is None and n.master is None
        spawning.assert_not_awaited()
    finally:
        release.set()
        await task
        await board.stop()


@pytest.mark.parametrize('stale_error', [False, True])
async def test_stale_preflight_cannot_spawn_or_clean_up_restarted_node(tmp_path, monkeypatch, stale_error):
    import asyncio
    from unittest.mock import AsyncMock
    entered, release = asyncio.Event(), asyncio.Event()
    async def preflight(argv, env):
        entered.set()
        await release.wait()
        if stale_error:
            raise ValueError('Old launch failed')
        return argv
    monkeypatch.setattr('dream.gui.deepsleep.native_launch', lambda *a, **k: (['/fixture/codex', '--no-daemon'], {}))
    monkeypatch.setattr('dream.gui.deepsleep.compatible_codex_argv', preflight)
    spawning = AsyncMock()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawning)
    board = DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:1')
    task = asyncio.create_task(board.create({'id': 'race', 'provider': 'codex', 'profile': 'native'}))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        n = board.nodes['race']
        await board.close(n)
        async def newer(node):
            node.state, node.output = 'ready', 'newer session'
        board._launcher = newer
        await board.start(n)
        release.set()
        assert await asyncio.wait_for(task, 1) is n
        assert n.state == 'ready' and n.output == 'newer session' and not n.error
        spawning.assert_not_awaited()
    finally:
        release.set()
        await task
        await board.stop()


@pytest.mark.parametrize('close_reads_first', [True, False])
async def test_close_retains_a_spawn_that_has_not_returned_its_process(tmp_path, monkeypatch, close_reads_first):
    import asyncio
    import signal
    from unittest.mock import Mock
    entered, release = asyncio.Event(), asyncio.Event()
    process = SimpleNamespace(returncode=None, send_signal=Mock())
    async def wait():
        process.returncode = 0
        return 0
    process.wait = wait
    async def spawn(*argv, **kwargs):
        entered.set()
        await release.wait()
        return process
    monkeypatch.setattr('dream.gui.deepsleep.native_launch', lambda *a, **k: (['/fixture/claude'], {}))
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    board = DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:1')
    allow_close, close_entered = asyncio.Event(), asyncio.Event()
    original_close = board._close
    async def delayed_close(node):
        close_entered.set()
        await allow_close.wait()
        await original_close(node)
    if not close_reads_first:
        monkeypatch.setattr(board, '_close', delayed_close)
    task = asyncio.create_task(board.create({'id': 'race', 'provider': 'anthropic', 'profile': 'native'}))
    closing = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        n = board.nodes['race']
        closing = asyncio.create_task(board.close(n))
        if close_reads_first:
            await asyncio.sleep(0)
        else:
            await asyncio.wait_for(close_entered.wait(), 1)
        release.set()
        if not close_reads_first:
            await asyncio.wait_for(task, 1)
            allow_close.set()
        await asyncio.wait_for(asyncio.gather(task, closing), 2)
        process.send_signal.assert_called_once_with(signal.SIGTERM)
        assert process.returncode == 0 and n.state == 'exited' and n.master is None
    finally:
        release.set()
        allow_close.set()
        await task
        if closing:
            await closing
        await board.stop()
