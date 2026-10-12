"""Direct links between real Dream terminals (the owner, 2026-10-10): the agent in A calls session_send itself.

Three real Dream terminal processes on a controlled HTTP model, no GPU. A is linked to B; C is not linked.
A's model sends to B over the link, then tries C: B receives the framed message, C receives nothing.
"""
import asyncio
from dataclasses import replace
import json
import re
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import threading

from dream import config
from dream.core import providers
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer

from test_deepsleep_live_integration import eventually


def _text(message):
    content = message.get('content')
    if isinstance(content, list):
        return ' '.join(part.get('text', '') for part in content if isinstance(part, dict))
    return content if isinstance(content, str) else ''


class LinkModel(BaseHTTPRequestHandler):
    """'SEND-TO:<id>' in the newest owner prompt -> one session_send call to that id; a tool result -> 'Done.';
    a framed linked-session message -> 'Direct message received.'"""

    def log_message(self, *args):
        pass

    def do_GET(self):
        data = ({'object': 'list', 'data': [{'id': 'controlled-model', 'object': 'model'}]} if self.path.startswith('/v1/models')
                else {'total_slots': 1, 'slot_ctx': 32768, 'prompt_cache_slots': 1, 'lanes': 1, 'lanes_active': 0,
                      'inflight': 0, 'queued': 0, 'vision': {'ready': False, 'reason': 'controlled text fixture'}})
        self._send(json.dumps(data).encode(), 'application/json')

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(payload)
        messages = payload.get('messages', [])
        last = messages[-1] if messages else {}
        users = [_text(m) for m in messages if m.get('role') == 'user']
        tools = [t.get('function', {}).get('name', '') for t in payload.get('tools') or []]
        send = next((name for name in tools if name.endswith('session_send')), 'session_send')  # deferred: named in the catalog
        target = re.search(r'SEND-TO:([A-Za-z0-9_-]+)', users[-1]) if users else None
        if last.get('role') == 'tool':
            delta = {'role': 'assistant', 'content': 'Done.'}
            finish = 'stop'
        elif users and 'External linked-session material' in users[-1]:
            delta = {'role': 'assistant', 'content': 'Direct message received.'}
            finish = 'stop'
        elif target and send:
            arguments = json.dumps({'target': target.group(1), 'prompt': 'hello from A over the link'})
            delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'call_' + target.group(1)[:8], 'type': 'function',
                                                          'function': {'name': send, 'arguments': arguments}}]}
            finish = 'tool_calls'
        else:
            delta = {'role': 'assistant', 'content': 'Nothing to send.'}
            finish = 'stop'
        chunks = [dict(id='fixture', object='chat.completion.chunk', model='controlled-model',
                       choices=[{'index': 0, 'delta': delta, 'finish_reason': None}]),
                  dict(id='fixture', object='chat.completion.chunk', model='controlled-model',
                       choices=[{'index': 0, 'delta': {}, 'finish_reason': finish}],
                       usage={'prompt_tokens': 10, 'completion_tokens': 3, 'total_tokens': 13})]
        self._send(''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks).encode() + b'data: [DONE]\n\n', 'text/event-stream')

    def _send(self, raw, kind):
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


async def test_a_linked_terminal_reaches_its_partner_directly_and_an_unlinked_one_is_refused(tmp_path, monkeypatch):
    http = ThreadingHTTPServer(('127.0.0.1', 0), LinkModel)
    http.requests = []
    threading.Thread(target=http.serve_forever, daemon=True).start()
    isolated, workspace = tmp_path / 'isolated-runtime', tmp_path / 'workspace'
    isolated.mkdir()
    workspace.mkdir()
    monkeypatch.setattr(config, 'ROOT', isolated)
    monkeypatch.setitem(providers.PROVIDERS, 'machx', replace(providers.PROVIDERS['machx'], base_url=f'http://127.0.0.1:{http.server_port}/v1'))
    s = StudioServer(EventBus(), session={'session_id': 'direct-link-fixture', 'workspace': str(workspace), 'provider': 'machx'})
    await s.start()
    try:
        ds = s._board()                                         # the page server's own DeepSleep board
        # One after another: Dream terminals starting together on a fresh memory database can crash on its WAL
        # switch (dream/memory/store.py; found 2026-10-10, not this test's subject).
        made = []
        for title in ('A', 'B', 'C'):
            node = await ds.create({'provider': 'machx', 'title': title})
            await eventually(lambda: node.state == 'ready' and node.address, 40)
            made.append(node)
        a, b, c = made
        await ds.action({'action': 'link', 'source': a.id, 'target': b.id, 'auto_forward': False})

        # A knows it is linked, and to whom.
        listed = await ds.bridge(a.bridge_token, {'operation': 'list'})
        assert [(l['source'], l['target']) for l in listed['links']] == [(a.id, b.id)]
        assert {n['id'] for n in listed['nodes']} == {a.id, b.id}

        await eventually(lambda: 'you' in a.output, 30)
        # A DeepSleep terminal starts in auto, where Dream's own session_send runs without asking (2026-10-11).
        ds.input(a, f'Please SEND-TO:{b.id}\r')
        await eventually(lambda: any(r['source'] == a.id and r['target'] == b.id and r['state'] == 'completed'
                                     for r in ds.receipts.values()), 40)
        got = [m for p in http.requests for m in p.get('messages', []) if 'hello from A over the link' in _text(m)]
        assert got and 'External linked-session material' in _text(got[0])      # framed as another session's words

        ds.input(a, f'Please SEND-TO:{c.id}\r')
        await eventually(lambda: sum(1 for p in http.requests for m in p.get('messages', [])[-1:]
                                     if m.get('role') == 'tool') >= 2, 30)
        assert not any(r['target'] == c.id for r in ds.receipts.values())          # nothing was delivered to C
        refusal = [_text(p['messages'][-1]) for p in http.requests if p['messages'][-1].get('role') == 'tool'][-1]
        assert 'link' in refusal.lower(), refusal
    finally:
        await s.stop()
        http.shutdown()
        http.server_close()
