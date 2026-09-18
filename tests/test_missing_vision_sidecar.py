"""A confirmed image rejection must not poison every later text request."""
import base64
import json
from dataclasses import replace

import httpx
import pytest

from dream.core.backends import openai_compat
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.profiles import PROFILES
from dream.core.providers import get_provider
from dream.tools.context import ToolContext, bind_context
from dream.tools.vision import see
from test_compaction import _text_round, _tool_round


MISSING = ('error: this deepseek4 load has no vision sidecar '
           '(*-Native.safetensors beside the GGUF, or $IE_DS4_VISION)')


@pytest.fixture
def session(monkeypatch, tmp_path):
    """Only HTTP is fake; tool execution and request construction stay real."""
    pixels = b'synthetic-image-pixels'
    (tmp_path / 'fixture.png').write_bytes(pixels)
    payloads = []
    error = {'message': MISSING}

    def transport(request):
        if request.method == 'GET':
            assert request.url.path == '/props'
            return httpx.Response(200, json={'default_generation_settings': {'n_ctx': 300000}})
        assert request.url.path == '/v1/chat/completions'
        payload = json.loads(request.content)
        payloads.append(payload)
        if len(payloads) == 1:
            lines = _tool_round('see', '{"path":"fixture.png"}')
        elif any(part.get('type') == 'image_url' for message in payload['messages']
                 if isinstance(message.get('content'), list) for part in message['content']):
            lines = ['data: ' + json.dumps({'error': error}), 'data: [DONE]']
        else:
            lines = _text_round('Text continuation succeeded.')
        return httpx.Response(200, text='\n\n'.join(lines) + '\n\n',
                              headers={'content-type': 'text/event-stream'})

    client = httpx.AsyncClient
    class FixtureClient(client):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx.MockTransport(transport))
    monkeypatch.setattr(openai_compat.httpx, 'AsyncClient', FixtureClient)
    backend = OpenAICompatBackend(
        provider=replace(get_provider('machx'), base_url='http://fixture.invalid/v1'),
        model='fixture-model', system_prompt='Synthetic fixture', tools=[see], permission_cb=None,
        profile=replace(PROFILES['balanced'], vision=True, auto_filer=False))
    backend._local_capabilities = {'features': {'vision': True}}
    return backend, payloads, error, tmp_path, base64.b64encode(pixels).decode()


async def rejected_turn(session):
    backend, payloads, _, tmp_path, _ = session
    await backend.connect()
    with bind_context(ToolContext(None, None, None, 'fixture', workspace=tmp_path, multimodal=True)):
        events = [event async for event in backend.ask('Inspect fixture.png and preserve this task text.')]
    assert len(payloads) == 2  # The rejected request is never retried.
    result = next(event.data for event in reversed(events) if event.kind == 'result')
    assert result['is_error'] and result['subtype'] == 'server_error'
    return events


async def test_rejected_pixels_are_omitted_and_next_explicit_text_turn_succeeds(session):
    backend, payloads, _, _, encoded = session
    try:
        events = await rejected_turn(session)
        assert any('not inspected' in str(event.data).lower() for event in events if event.kind == 'error')
        assert backend.capability_status()['vision']['value'] is True  # Architecture remains distinct.
        readiness = backend.capability_status()['image_readiness']
        assert readiness['known'] and readiness['value'] is False
        assert 'see' not in backend.tools_by_name
        assert backend.provider.multimodal is False  # Even an explicit positive profile cannot override rejection.
        assert backend.profile.vision is True
        events = [event async for event in backend.ask('Continue using text only.')]
        assert len(payloads) == 3
        sent = json.dumps(payloads[-1])
        assert 'image_url' not in sent and encoded not in sent
        assert 'image omitted' in sent.lower() and 'not inspected' in sent.lower()
        assert 'preserve this task text' in sent
        assert 'see' not in {tool['function']['name'] for tool in payloads[-1]['tools']}
        assert not next(event.data for event in reversed(events) if event.kind == 'result')['is_error']
        rows = {row['name']: row for row in backend.generation_settings_status()['summary']}
        assert rows['image_readiness']['value'] is False
    finally:
        await backend.disconnect()


@pytest.mark.parametrize('message,provider', [
    ('error: temporary generation failure', 'machx'),
    (MISSING + ' unrelated suffix', 'machx'),
    (MISSING, 'openai'),
])
async def test_unrelated_errors_do_not_disable_images(session, message, provider):
    backend, _, error, _, _ = session
    error['message'] = message
    backend.provider = replace(backend.provider, key=provider)
    try:
        await rejected_turn(session)
        assert backend.provider.multimodal
        assert 'see' in backend.tools_by_name
        assert not backend.capability_status()['image_readiness']['known']
    finally:
        await backend.disconnect()


@pytest.mark.parametrize('reset', ['connect', 'model'])
async def test_only_new_connection_or_model_clears_observed_rejection(session, reset):
    backend, _, _, _, _ = session
    try:
        await rejected_turn(session)
        await backend.set_model(backend.model)
        backend.set_performance_mode('quick')
        backend.set_effort('high')
        backend._configure_tool_images()
        assert 'see' not in backend.tools_by_name
        assert backend.capability_status()['image_readiness']['value'] is False
        if reset == 'connect':
            await backend.disconnect()
            await backend.connect()
        else:
            await backend.set_model('new-fixture-model')
        assert 'see' in backend.tools_by_name
        assert not backend.capability_status()['image_readiness']['known']
    finally:
        await backend.disconnect()


@pytest.mark.parametrize('message,rejected', [(MISSING, True), ('temporary failure', False)])
async def test_delegated_image_error_updates_readiness_without_replaying(session, message, rejected):
    from dream.core.subagents import LocalSubagentSpec
    from test_local_subagents import _FakeClient, _sub_toolcall
    backend, _, _, tmp_path, _ = session
    backend._subagents = {'reader': LocalSubagentSpec('reader', 'Read image', 'Read image', ('see',))}
    try:
        await backend.connect()
        await backend._client.aclose()
        backend._client = _FakeClient(post_scripts=[
            _sub_toolcall('see', {'path': 'fixture.png'}), {'error': {'message': message}}])
        with bind_context(ToolContext(None, None, None, 'fixture', workspace=tmp_path, multimodal=True)):
            text, failed = await backend._run_subagent('reader', 'Inspect fixture.png')
        assert failed and message in text
        assert len(backend._client.posted) == 2
        assert 'image_url' in json.dumps(backend._client.posted[1])
        assert backend.capability_status()['image_readiness']['known'] is rejected
        assert backend.provider.multimodal is (not rejected)
        assert ('see' in backend.tools_by_name) is (not rejected)
        if rejected:
            assert 'not inspected' in text and 'not retried' in text
    finally:
        await backend.disconnect()
