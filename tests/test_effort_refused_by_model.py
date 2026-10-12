"""A model with no reasoning-effort levels: the effort is dropped, never left to fail every request (the owner,
2026-10-11, of a session on 2026-10-10: "i changed the effort ... in the middle of a session and it broke it").

That session's log (var/logs/runtime/20261010-014348-4cd3.jsonl) shows /effort max, then xhigh, high and medium, each
answered by the engine with HTTP 400 "reasoning_effort is not supported by this model's prompt backend", and every
turn after it failing the same way. Here a controlled engine refuses any request that names an effort, as Mach X does
for such a model: the request goes again without it, the owner is told once, and later requests name none."""
import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from dream.core import providers
from dream.core.backends.openai_compat import OpenAICompatBackend

REFUSAL = "reasoning_effort is not supported by this model's prompt backend"


class NoEffortModel(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        data = ({'object': 'list', 'data': [{'id': 'm', 'object': 'model'}]} if self.path.startswith('/v1/models')
                else {'total_slots': 1, 'default_generation_settings': {'n_ctx': 32768}})
        self._send(200, json.dumps(data).encode(), 'application/json')

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(payload)
        if 'reasoning_effort' in payload:
            self._send(400, json.dumps({'error': {'message': REFUSAL, 'type': 'invalid_request_error'}}).encode(),
                       'application/json')
            return
        chunks = [dict(id='x', object='chat.completion.chunk', model='m',
                       choices=[{'index': 0, 'delta': {'role': 'assistant', 'content': 'Answered.'}, 'finish_reason': None}]),
                  dict(id='x', object='chat.completion.chunk', model='m',
                       choices=[{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
                       usage={'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12})]
        self._send(200, ''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks).encode() + b'data: [DONE]\n\n',
                   'text/event-stream')

    def _send(self, status, raw, kind):
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


async def test_an_effort_the_model_refuses_is_dropped_and_the_request_answered():
    http = ThreadingHTTPServer(('127.0.0.1', 0), NoEffortModel)
    http.requests = []
    threading.Thread(target=http.serve_forever, daemon=True).start()
    provider = replace(providers.PROVIDERS['machx'], base_url=f'http://127.0.0.1:{http.server_port}/v1')
    backend = OpenAICompatBackend(provider=provider, model='m', system_prompt='SYSTEM', tools=[], permission_cb=None)
    try:
        await backend.connect()
        backend.set_effort('xhigh')                                     # /effort xhigh, as on 2026-10-10
        first = [ev async for ev in backend.ask('first question')]
        second = [ev async for ev in backend.ask('second question')]
    finally:
        await backend.disconnect()
        http.shutdown()
        http.server_close()
    chats = [r for r in http.requests]
    assert [('reasoning_effort' in r) for r in chats] == [True, False, False]   # refused once, then never named
    notes = [ev.data for ev in first + second if ev.kind == 'system' and 'reasoning effort' in str(ev.data)]
    assert notes == ["This model takes no reasoning effort, so Dream sent the request without one and will not "
                     "send one for it again."]
    for events in (first, second):
        result = next(ev.data for ev in events if ev.kind == 'result')
        assert result.get('is_error') is False and result.get('subtype') == 'success', result
    assert backend._effort is None                                     # /effort shows what is really sent


async def test_the_prompt_optimizers_low_effort_reads_still_answer_on_such_a_model(tmp_path):
    """The optimizer's two reads ask for low effort (2026-10-11); on a model with no levels they go again without."""
    from dream.core.evaluator import ReviewSettings
    from dream import prompt_optimizer
    http = ThreadingHTTPServer(('127.0.0.1', 0), NoEffortModel)
    http.requests = []
    threading.Thread(target=http.serve_forever, daemon=True).start()
    provider = replace(providers.PROVIDERS['machx'], base_url=f'http://127.0.0.1:{http.server_port}/v1')
    try:
        answer = await prompt_optimizer._complete(prompt_optimizer.REVIEW, 'tidy the notes', tmp_path,
                                                  ReviewSettings(provider=provider, model='m', timeout=30), effort='low')
    finally:
        http.shutdown()
        http.server_close()
    assert answer == 'Answered.'
    assert [r.get('reasoning_effort') for r in http.requests] == ['low', None]


def test_a_session_with_no_model_name_still_sends_its_effort_until_refused():
    backend = OpenAICompatBackend(provider=providers.PROVIDERS['machx'], model=None, system_prompt='S', tools=[],
                                  permission_cb=None)
    backend.set_effort('xhigh')
    assert backend._effort_params() == {'reasoning_effort': 'xhigh'}


@pytest.mark.parametrize('reuse_stream', [True, False])
async def test_routed_worker_refusals_preserve_lead_effort_and_are_remembered(monkeypatch, reuse_stream):
    """A worker's unsupported option must not change the lead's next request."""
    from dream.core.backends import openai_compat

    monkeypatch.setattr(openai_compat, '_retry_delay', lambda *_: 0)
    backend = OpenAICompatBackend(provider=providers.PROVIDERS['machx'], model='lead',
                                  system_prompt='S', tools=[], permission_cb=None)
    backend.set_effort('high')
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if payload['model'].startswith('worker') and 'reasoning_effort' in payload:
            return httpx.Response(400, json={'error': {'message': REFUSAL}})
        if not payload['stream']:
            return httpx.Response(200, json={'choices': [{'message': {'content': 'Answered.'}}]})
        return httpx.Response(200, content=b'data: [DONE]\n\n')

    async with httpx.AsyncClient(base_url='http://mock.invalid/v1',
                                transport=httpx.MockTransport(respond)) as client:
        backend._client = client
        for index, model in enumerate(('worker-a', 'worker-b', 'worker-a', 'worker-b', 'lead')):
            stream = index < 2 or reuse_stream
            payload = {'model': model, 'messages': [], 'stream': stream, **backend._effort_params()}
            if stream:
                async with backend._stream_uncoordinated(payload) as response:
                    assert await response.aread() == b'data: [DONE]\n\n'
            else:
                response = await backend._post_retry(payload)
                assert response.json()['choices'][0]['message']['content'] == 'Answered.'

    assert [(request['model'], request.get('reasoning_effort')) for request in requests] == [
        ('worker-a', 'high'), ('worker-a', None),
        ('worker-b', 'high'), ('worker-b', None),
        ('worker-a', None), ('worker-b', None), ('lead', 'high'),
    ]
    assert backend._effort == 'high'
