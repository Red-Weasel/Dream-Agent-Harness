"""@everyone, post to the message board (the owner, 2026-10-11), on real Dream terminals.

Three real Dream terminal processes, the "Software Development" skill preset active (as on the owner's machine) and
a controlled model server, no GPU. The owner sends one @everyone message from the Message Board asking each terminal
to post its name on the board. Expected: each terminal receives it once, posts once with ram_post, finishes without
an error, and nothing else is sent anywhere."""
import json
import threading
from dataclasses import replace
from http.server import ThreadingHTTPServer

from dream import config
from dream.core import providers
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer

from test_deepsleep_direct_links_live import LinkModel, _text
from test_deepsleep_live_integration import eventually


class BoardModel(LinkModel):
    """A request to post on the board -> one ram_post call; its tool result -> 'Posted.'; anything else -> text."""

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(payload)
        messages = payload.get('messages', [])
        last = messages[-1] if messages else {}
        users = [_text(m) for m in messages if m.get('role') == 'user']
        tools = [t.get('function', {}).get('name', '') for t in payload.get('tools') or []]
        self.server.tool_lists.append(tools)
        if last.get('role') == 'tool':
            self.server.tool_results.append(_text(last))
            delta, finish = {'role': 'assistant', 'content': 'Posted.'}, 'stop'
        elif users and 'message board' in users[-1]:
            name = next((n for n in tools if n.endswith('ram_post')), 'ram_post')
            call = {'index': 0, 'id': 'call_post', 'type': 'function',
                    'function': {'name': name, 'arguments': json.dumps({'text': 'checking in'})}}
            delta, finish = {'role': 'assistant', 'tool_calls': [call]}, 'tool_calls'
        else:
            delta, finish = {'role': 'assistant', 'content': 'Ready.'}, 'stop'
        chunks = [dict(id='fixture', object='chat.completion.chunk', model='controlled-model',
                       choices=[{'index': 0, 'delta': delta, 'finish_reason': None}]),
                  dict(id='fixture', object='chat.completion.chunk', model='controlled-model',
                       choices=[{'index': 0, 'delta': {}, 'finish_reason': finish}],
                       usage={'prompt_tokens': 10, 'completion_tokens': 3, 'total_tokens': 13})]
        self._send(''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks).encode() + b'data: [DONE]\n\n', 'text/event-stream')


async def test_everyone_posts_once_to_the_board_under_a_narrow_preset(tmp_path, monkeypatch):
    http = ThreadingHTTPServer(('127.0.0.1', 0), BoardModel)
    http.requests, http.tool_lists, http.tool_results = [], [], []
    threading.Thread(target=http.serve_forever, daemon=True).start()
    root, workspace = tmp_path / 'runtime', tmp_path / 'workspace'
    (root / 'data' / 'config').mkdir(parents=True)
    workspace.mkdir()
    (root / 'data' / 'config' / 'presets.json').write_text(json.dumps({'active': 'Software Development'}))
    monkeypatch.setattr(config, 'ROOT', root)
    monkeypatch.setattr(config, 'DATA_DIR', root / 'data')
    monkeypatch.setattr(config, 'MEMORY_DIR', root / 'memory')
    monkeypatch.setitem(providers.PROVIDERS, 'machx', replace(providers.PROVIDERS['machx'], base_url=f'http://127.0.0.1:{http.server_port}/v1'))
    s = StudioServer(EventBus(), session={'session_id': 'board-broadcast', 'workspace': str(workspace), 'provider': 'machx'})
    await s.start()
    try:
        ds = s._board()
        made = []
        for title in ('Claude', 'Codex', 'Grok'):
            node = await ds.create({'provider': 'machx', 'title': title})
            await eventually(lambda: node.state == 'ready' and node.address, 40)
            made.append(node)
        ids = [n.id for n in made]
        await ds.action({'action': 'channel_send', 'id': 'channel-everyone', 'targets': ids,
                         'prompt': '@everyone put a message on the message board to confirm everything is working, just write your name'})
        await eventually(lambda: sum(r['state'] == 'completed' for r in ds.receipts.values()) == 3, 60)
        posts = ds.snapshot()['board']['posts']
        assert len(posts) == 3 and len({p['author'] for p in posts}) == 3, posts     # one post from each terminal
        assert all('Posted #' in result for result in http.tool_results), http.tool_results
        assert not any('No such tool' in result or 'is_error' in result for result in http.tool_results)
        assert len(ds.receipts) == 3                                                    # nothing else was sent anywhere
        assert all(r['state'] == 'completed' and not r.get('error') for r in ds.receipts.values())
    finally:
        await s.stop()
        http.shutdown()
        http.server_close()
