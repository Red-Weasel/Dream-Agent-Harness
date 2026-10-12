"""A native pane's tools end to end (the owner, 2026-10-10): an MCP client stands in for the CLI and starts
`python -m dream.mcp.deepsleep` over stdio, exactly as claude, codex, grok or agy would; the server reaches the
real page server's DeepSleep bridge with the pane's key file. No model, no GPU."""
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from dream import config
from dream.gui.bus import EventBus
from dream.gui.deepsleep import Node, layout
from dream.gui.server import StudioServer

ROOT = Path(__file__).resolve().parents[1]


def key_file(folder: Path, url: str, node) -> Path:
    folder.mkdir(mode=0o700)
    path = folder / 'bridge.json'
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        json.dump({'url': url + '/api/deepsleep/bridge', 'token': node.bridge_token, 'node': node.id, 'title': node.title}, stream)
    return path


async def tool(path: Path | None, name: str, arguments: dict | None = None):
    env = {'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(ROOT)}
    if path is not None:
        env['DREAM_DEEPSLEEP_BRIDGE_FILE'] = str(path)
    server = StdioServerParameters(command=sys.executable, args=['-m', 'dream.mcp.deepsleep'], env=env)
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]
            assert names == ['board_read', 'board_post', 'links', 'link_send', 'link_inbox']
            result = await session.call_tool(name, arguments or {})
            return result.content[0].text, result.isError


async def test_a_native_pane_posts_reads_lists_its_links_and_sends_directly(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'MEMORY_DIR', tmp_path / 'memory')
    workspace = tmp_path / 'project'
    workspace.mkdir()
    s = StudioServer(EventBus(), session={'session_id': 'native-mcp-fixture', 'workspace': str(workspace)})
    url = (await s.start()).split('/#')[0].split('?')[0].rstrip('/')
    try:
        ds = s._board()
        delivered = []
        ds._request = lambda address, token, envelope=None: (
            {'receipts': []} if envelope is None else (delivered.append(envelope) or dict(envelope, state='queued', result='', error='')))
        for key, title, profile in (('codex1', 'Codex', 'native'), ('grok1', 'Grok', 'native'), ('claude1', 'Claude', 'dream')):
            node = ds.nodes[key] = Node(key, 'codex', '', title, layout({}), state='ready', profile=profile)
            node.address = ('http://127.0.0.1:1', 'child-token') if profile == 'dream' else None
        await ds.action({'action': 'link', 'source': 'codex1', 'target': 'claude1', 'auto_forward': False})
        codex = key_file(tmp_path / 'codex-pane', url, ds.nodes['codex1'])
        grok = key_file(tmp_path / 'grok-pane', url, ds.nodes['grok1'])

        text, failed = await tool(codex, 'links')
        assert not failed and 'You are Codex (codex1)' in text and 'send directly to: Claude (claude1)' in text

        text, failed = await tool(codex, 'board_post', {'text': 'Codex: migrations are done.'})
        assert not failed and text.startswith('Posted #1')
        text, failed = await tool(grok, 'board_read')
        assert not failed and 'Codex: migrations are done.' in text and 'terminal codex1' in text

        text, failed = await tool(codex, 'link_send', {'target': 'claude1', 'prompt': 'work with me on xyz'})
        assert not failed and 'queued' in text and delivered[-1]['source'] == 'codex1'
        text, failed = await tool(grok, 'link_send', {'target': 'claude1', 'prompt': 'not linked'})
        assert failed and 'link' in text.lower() and len(delivered) == 1    # Grok has no link to Claude

        text, failed = await tool(None, 'board_post', {'text': 'from an unrelated session'})
        assert failed and 'not a DeepSleep terminal' in text
        assert [p['text'] for p in ds.snapshot()['board']['posts']] == ['Codex: migrations are done.']
    finally:
        await s.stop()


async def test_a_key_file_others_can_read_is_refused(tmp_path):
    folder = tmp_path / 'pane'
    folder.mkdir(mode=0o700)
    path = folder / 'bridge.json'
    path.write_text(json.dumps({'url': 'http://127.0.0.1:9/api/deepsleep/bridge', 'token': 'x' * 32}))
    path.chmod(0o644)
    text, failed = await tool(path, 'links')
    assert failed and 'not private' in text
