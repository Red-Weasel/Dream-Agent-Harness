"""A verifier's PASS requires successful inspection and submitted image evidence."""
import base64
from copy import deepcopy
from io import BytesIO
import json

import httpx
from PIL import Image
import pytest

from test_verifier_prerequisites import REQUIRED, viewer_backend


async def review(sequence, *, fail=(), no_pixels=False, agent='verifier'):
    b = viewer_backend()
    attempts, payloads = [], []
    buffer = BytesIO()
    Image.new('RGB', (2, 2), 'blue').save(buffer, format='PNG')
    pixels = base64.b64encode(buffer.getvalue()).decode()
    for name, tool in b.tools_by_name.items():
        async def handler(args, name=name):
            attempts.append(name)
            failed = (name, attempts.count(name)) in fail
            content = [{'type': 'text', 'text': 'Fixture inspection failed' if failed else 'Fixture inspected'}]
            if name == 'see' and not failed and not no_pixels:
                content.append({'type':'image', 'mimeType':'image/png', 'data':pixels})
            return {'is_error':failed, 'content':content}
        tool.handler = handler
    def response(request):
        payloads.append(deepcopy(json.loads(request.content)))
        index = len(payloads) - 1
        assert index <= len(sequence), 'unexpected repeated verifier request'
        if index < len(sequence):
            calls = [{'id':f'v{index}-{n}', 'type':'function', 'function':{'name': name, 'arguments':'{}'}}
                     for n, name in enumerate(sequence[index])]
            message = {'role':'assistant', 'content':None, 'tool_calls':calls}
        else:
            message = {'role':'assistant', 'content':'PASS'}
        return httpx.Response(200, json={'choices':[{'message':message, 'finish_reason':'tool_calls' if index < len(sequence) else 'stop'}]})
    async with httpx.AsyncClient(base_url='http://fixture.invalid', transport=httpx.MockTransport(response)) as client:
        b._client = client
        text, failed = await b._run_subagent(agent, 'Check the rendered page')
    return text, failed, payloads, attempts


@pytest.mark.parametrize('sequence', [[], [('see',)], [REQUIRED[:-1]]])
async def test_pass_without_required_inspections_is_unverified(sequence):
    text, failed, _, _ = await review(sequence)
    assert failed and 'unverified' in text.lower() and text.strip() != 'PASS'


@pytest.mark.parametrize('name', REQUIRED)
async def test_failed_inspection_cannot_be_erased_by_other_successes(name):
    text, failed, _, _ = await review([REQUIRED], fail={(name, 1)})
    assert failed and 'unverified' in text.lower() and name in text


async def test_successful_see_without_pixels_does_not_pass():
    text, failed, _, _ = await review([REQUIRED], no_pixels=True)
    assert failed and 'image' in text.lower()


async def test_successful_inspection_and_actual_image_request_can_pass():
    text, failed, payloads, calls = await review([REQUIRED])
    assert text == 'PASS' and not failed and calls == list(REQUIRED)
    assert any(block.get('type') == 'image_url' for message in payloads[-1]['messages']
               if isinstance(message.get('content'), list) for block in message['content'])


async def test_successful_retry_resolves_only_its_own_failed_check():
    text, failed, payloads, calls = await review([REQUIRED, ('see',)], fail={('see', 1)})
    assert text == 'PASS' and not failed and calls.count('see') == 2
    assert len(payloads) == 3


async def test_non_verifier_summary_keeps_existing_semantics():
    text, failed, payloads, _ = await review([], agent='filer')
    assert text == 'PASS' and not failed and len(payloads) == 1


@pytest.mark.parametrize('second', [('save_screenshot', 'see'), REQUIRED])
async def test_repeated_successful_inspection_keeps_visual_payload(second):
    text, failed, payloads, calls = await review([REQUIRED, second])
    assert text == 'PASS' and not failed and calls.count('see') == 2
    assert any(block.get('type') == 'image_url' for message in payloads[-1]['messages']
               if isinstance(message.get('content'), list) for block in message['content'])


async def test_disabled_inspection_fails_before_inference(monkeypatch):
    from dream import extensions
    monkeypatch.setattr(extensions, 'tool_enabled', lambda tool: tool.name != 'see')
    b = viewer_backend()
    text, failed = await b._run_subagent('verifier', 'Check the page')
    assert failed and 'unavailable' in text and 'see' in text
