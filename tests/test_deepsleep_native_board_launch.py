"""How a native pane gets its DeepSleep tools (the owner, 2026-10-10): Dream's MCP server, attached the way each
CLI takes one; the pane's key stays in a private file and never reaches a command line or a saved config."""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from dream.gui import deepsleep as deepsleep_module
from dream.gui import native_terminals
from dream.gui.deepsleep import DeepSleep, Node, layout

SECRET = 'secret-token-that-must-never-show-0123456789'


@pytest.fixture
def clis(monkeypatch):
    monkeypatch.setattr(native_terminals, 'find_cli', lambda name: f'/opt/cli/{name}')
    monkeypatch.setattr(native_terminals, 'node_child_env', lambda *a, **k: None)


def launch(provider, tmp_path):
    key = tmp_path / 'pane' / 'bridge.json'
    return native_terminals.native_launch(provider, '', '', tmp_path, board=key, title='Grok 2'), key


@pytest.mark.parametrize('provider', ['anthropic', 'codex', 'grok', 'antigravity'])
def test_every_native_pane_knows_where_its_key_is_and_never_holds_the_key_itself(clis, tmp_path, provider):
    (argv, env), key = launch(provider, tmp_path)
    assert env['DREAM_DEEPSLEEP_BRIDGE_FILE'] == str(key)
    assert SECRET not in json.dumps([argv, env])


def test_claude_takes_the_tools_and_a_note_for_this_launch_only(clis, tmp_path):
    (argv, _), key = launch('anthropic', tmp_path)
    config = json.loads(argv[argv.index('--mcp-config') + 1])
    server = config['mcpServers']['dream_deepsleep']
    assert server['command'] == sys.executable and server['args'] == ['-m', 'dream.mcp.deepsleep']
    assert server['env']['DREAM_DEEPSLEEP_BRIDGE_FILE'] == str(key)
    note = argv[argv.index('--append-system-prompt') + 1]
    assert 'Grok 2' in note and 'board_post' in note and 'link_send' in note and 'not from your owner' in note
    allowed = argv[argv.index('--allowedTools') + 1]
    assert 'mcp__dream_deepsleep__board_read' in allowed and 'link_send' not in allowed   # a direct send still asks


def test_codex_takes_the_tools_for_this_launch_only(clis, tmp_path):
    (argv, _), key = launch('codex', tmp_path)
    flags = [argv[i + 1] for i, a in enumerate(argv) if a == '-c']
    assert any(f.startswith('mcp_servers.dream_deepsleep.command=') for f in flags)
    assert any(f.startswith('mcp_servers.dream_deepsleep.env=') and str(key) in f for f in flags)


def test_grok_gets_the_note_and_both_saved_entries_hold_no_key(clis, tmp_path):
    (argv, _), _ = launch('grok', tmp_path)
    assert 'board_read' in argv[argv.index('--rules') + 1]
    for provider in ('grok', 'antigravity'):
        add = native_terminals.board_register_argv(provider, f'/opt/cli/{provider}')
        assert add[1:3] == ['mcp', 'add'] and 'dream_deepsleep' in add and add[-2:] == ['-m', 'dream.mcp.deepsleep']
        assert not any('BRIDGE_FILE' in a or SECRET in a for a in add)
        assert native_terminals.board_deregister_argv(provider, f'/opt/cli/{provider}')[1:] == ['mcp', 'remove', 'dream_deepsleep']
    assert native_terminals.board_register_argv('anthropic', '/opt/cli/claude') is None


async def test_a_pane_writes_its_key_privately_and_the_key_is_gone_when_it_exits(tmp_path, monkeypatch):
    seen, commands = {}, []

    def fake_launch(provider, model, effort, workspace, *, board=None, title=''):
        seen['board'] = board
        return [sys.executable, '-c', 'import time; time.sleep(30)'], dict(os.environ)

    async def fake_run(argv, **kwargs):
        commands.append(list(argv))
        class Done:
            returncode, timed_out, output = 0, False, b''
        return Done()
    monkeypatch.setattr(deepsleep_module, 'native_launch', fake_launch)
    monkeypatch.setattr(deepsleep_module, 'run_owned', fake_run)
    monkeypatch.setattr(deepsleep_module, 'find_cli', lambda name: f'/opt/cli/{name}')
    board = DeepSleep(lambda: tmp_path, lambda: 'http://127.0.0.1:4321')
    pane = Node('pane1', 'grok', '', 'Grok', layout({}), profile='native')
    other = Node('pane2', 'grok', '', 'Grok 2', layout({}), profile='native')
    board.nodes.update(pane1=pane, pane2=other)
    try:
        await board.start(pane)
        await board.start(other)
        key = Path(seen['board'])
        assert stat.S_IMODE(key.stat().st_mode) == 0o600 and stat.S_IMODE(key.parent.stat().st_mode) == 0o700
        data = json.loads(key.read_text())
        assert data == {'url': 'http://127.0.0.1:4321/api/deepsleep/bridge', 'token': other.bridge_token,
                        'node': 'pane2', 'title': 'Grok 2'}
        assert commands[0][1:3] == ['mcp', 'add']                           # the saved entry, at launch
        await board.close(other)
        assert not key.exists()                                             # the key leaves with the pane
        assert not any(c[1:3] == ['mcp', 'remove'] for c in commands)       # another Grok pane still runs
        await board.close(pane)
        assert commands[-1][1:] == ['mcp', 'remove', 'dream_deepsleep']     # the last one takes the entry away
    finally:
        await board.stop()
