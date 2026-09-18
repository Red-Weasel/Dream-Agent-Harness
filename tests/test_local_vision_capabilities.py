"""Local image transport uses explicit configuration, never a model-name guess."""
import base64
import json
from dataclasses import replace

import pytest

from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.providers import get_provider
from dream.tools.context import ToolContext, bind_context
from dream.tools.vision import see
from test_compaction import _FakeClient, _tool_round, _text_round


def backend(enabled):
    return OpenAICompatBackend(
        provider=replace(get_provider('machx'), multimodal=enabled, base_url='http://fixture.invalid/v1'),
        model='fixture-vision-model', system_prompt='Fixture', tools=[see],
        permission_cb=None)


@pytest.mark.parametrize('enabled', [False, True])
async def test_actual_see_handler_reaches_http_only_with_explicit_image_configuration(tmp_path, enabled):
    # A tiny fixture is sufficient: this tests exact transport, not image inference.
    pixels = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aF5kAAAAASUVORK5CYII=')
    (tmp_path / 'fixture.png').write_bytes(pixels)
    b = backend(enabled)
    b._client = _FakeClient([_tool_round('see', json.dumps({'path': 'fixture.png'})), _text_round()])
    with bind_context(ToolContext(None, None, None, 'fixture', workspace=tmp_path, multimodal=enabled)):
        events = [event async for event in b.ask('Inspect fixture.png')]
    assert events
    names = {t['function']['name'] for t in b._client.payloads[0].get('tools', [])}
    assert ('see' in names) is enabled
    assert ('see' in b.tools_by_name) is enabled
    parts = [part for message in b._client.payloads[1]['messages']
             if isinstance(message.get('content'), list) for part in message['content']]
    images = [part for part in parts if part.get('type') == 'image_url']
    if enabled:
        assert len(images) == 1
        assert images[0]['image_url']['url'] == 'data:image/png;base64,' + base64.b64encode(pixels).decode()
    else:
        assert images == []
        result = next(message['content'] for message in b._client.payloads[1]['messages']
                      if message.get('role') == 'tool')
        assert 'image input' in result.lower()
        assert 'not inspected' in result.lower()
    assert not b.capability_status()['vision']['known']


async def test_disabled_see_refuses_before_resolving_private_path(monkeypatch):
    from dream.tools import vision
    def forbidden(*args):
        pytest.fail('A disabled image tool must not resolve or read the requested path')
    monkeypatch.setattr(vision, '_resolve', forbidden)
    b = backend(False)
    result, failed = await b._exec_tool('See', {'path': '/private/canary.png'})
    assert failed
    assert 'image input' in result.lower()
    assert 'not inspected' in result.lower()
    assert '/private/canary' not in result


@pytest.mark.parametrize('declared,override,expected', [
    (True, None, True), (False, None, False), (None, None, False),
    ('true', None, False), (True, False, False), (False, True, True),
])
async def test_connect_applies_declared_architecture_support_without_claiming_readiness(
        monkeypatch, declared, override, expected):
    from types import SimpleNamespace
    from dream.core.profiles import PROFILES
    from dream.core.backends import openai_compat
    b = backend(False)
    b.profile = replace(PROFILES['balanced'], vision=override)
    b._local_capabilities = {'features': {'vision': declared}}
    before = dict(b.tools_by_name)
    # Inspection never mutates configuration, even with a positive declaration.
    b.capability_status()
    assert b.tools_by_name == before
    class Client:
        async def get(self, *args, **kwargs):
            return SimpleNamespace(status_code=200, json=lambda: {
                'default_generation_settings': {'n_ctx': 8192}, 'total_slots': 1})
        async def aclose(self):
            pass
    monkeypatch.setattr(openai_compat.httpx, 'AsyncClient', lambda **kwargs: Client())
    await b.connect()
    assert ('see' in b.tools_by_name) is expected
    assert b.provider.multimodal is expected
    assert b.capability_status()['vision']['known'] is (type(declared) is bool)
    if type(declared) is bool:
        assert b.capability_status()['vision']['source'] == 'machx.capabilities.features.vision'
    await b.disconnect()


async def test_launch_declaration_connects_see_and_forwards_pixels_in_actual_http_request(monkeypatch, tmp_path):
    import httpx
    from dream.local.settings import session_options
    from dream.core.backends import openai_compat
    payloads = []
    pixels = b'fixture-image-bytes'
    (tmp_path / 'fixture.png').write_bytes(pixels)
    def transport(request):
        if request.method == 'GET':
            assert request.url.path == '/props'
            return httpx.Response(200, json={'default_generation_settings': {'n_ctx': 32768}})
        assert request.url.path == '/v1/chat/completions'
        payloads.append(json.loads(request.content))
        lines = _tool_round('see', '{"path":"fixture.png"}') if len(payloads) == 1 else _text_round()
        return httpx.Response(200, text='\n\n'.join(lines) + '\n\n',
                              headers={'content-type': 'text/event-stream'})
    client = httpx.AsyncClient
    class FixtureClient(client):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx.MockTransport(transport))
    monkeypatch.setattr(openai_compat.httpx, 'AsyncClient', FixtureClient)
    with session_options({}, 'fixture-vision-model', capabilities={'features': {'vision': True}}):
        b = backend(False)
    assert 'see' not in b.tools_by_name
    await b.connect()
    with bind_context(ToolContext(None, None, None, 'fixture', workspace=tmp_path, multimodal=True)):
        [event async for event in b.ask('Inspect fixture.png')]
    assert 'see' in {t['function']['name'] for t in payloads[0]['tools']}
    images = [part for message in payloads[1]['messages'] if isinstance(message.get('content'), list)
              for part in message['content'] if part.get('type') == 'image_url']
    assert images == [{'type': 'image_url', 'image_url': {
        'url': 'data:image/png;base64,' + base64.b64encode(pixels).decode(), 'detail': 'auto'}}]
    # The image message can also contain task text that must survive the switch.
    visual = next(message for message in b.messages if isinstance(message.get('content'), list))
    visual['content'].append({'type': 'text', 'text': 'Preserve neighboring task text.'})
    await b.set_model('different-model')
    assert not b.provider.multimodal and 'see' not in b.tools_by_name
    assert not b.capability_status()['vision']['known']
    assert get_provider('machx').multimodal is False
    with bind_context(ToolContext(None, None, None, 'fixture', workspace=tmp_path, multimodal=False)):
        [event async for event in b.ask('Continue without images')]
    assert len(payloads) == 3
    sent = json.dumps(payloads[-1])
    assert 'image_url' not in sent
    assert base64.b64encode(pixels).decode() not in sent
    assert 'Preserve neighboring task text.' in sent
    assert 'image omitted' in sent.lower()
    await b.disconnect()


async def test_conflicting_declarations_do_not_enable_images():
    b = backend(False)
    b._provider_metadata = {'vision': False}
    b._local_capabilities = {'features': {'vision': True}}
    b._configure_tool_images()
    assert not b.provider.multimodal
    assert b.capability_status()['vision']['source'] == 'conflicting reports'
    assert 'see' not in b.tools_by_name


async def test_image_only_history_becomes_explicit_omission_on_disabled_switch():
    b = backend(False)
    b._local_capabilities = {'features': {'vision': True}}
    b._configure_tool_images()
    b.messages.append({'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,cHJpdmF0ZQ=='}}]})
    await b.set_model('text-only-model')
    b._client = _FakeClient([_text_round()])
    [event async for event in b.ask('Continue without images')]
    payload = b._client.payloads[0]
    assert 'image_url' not in json.dumps(payload)
    assert 'cHJpdmF0ZQ==' not in json.dumps(payload)
    assert b.messages[1]['content']
    assert 'image omitted' in json.dumps(b.messages[1]).lower()
