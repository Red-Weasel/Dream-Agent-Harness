"""Prompt drafts use the explicit optimizer route, never task execution."""
import asyncio

import httpx
import pytest

from dream.gui.bus import EventBus
from dream.gui.server import StudioServer


def make_server(tmp_path, **kwargs):
    return StudioServer(EventBus(), session={'session_id': 's1', 'workspace': str(tmp_path)}, **kwargs)


async def post(server, tmp_path, **changes):
    body = {'draft': 'Write a short launch announcement.', 'mode': 'model',
            'session_id': 's1', 'workspace': str(tmp_path), **changes}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://test') as client:
        return await client.post('/api/prompt-optimizer', headers={'X-Dream-Token': server.token}, json=body)


async def test_optimizer_calls_only_explicit_callback_and_keeps_selected_uploads(tmp_path):
    calls, task_prompts = [], []
    async def optimize(body, files):
        calls.append((body, files))
        return {'prompt': 'GOAL\nDraft announcement', 'questions': [], 'notes': [], 'evidence': []}
    server = make_server(tmp_path, on_prompt=task_prompts.append, on_optimize_prompt=optimize)
    from dream.gui.uploads import save_file
    file = save_file(tmp_path, 'brief.txt', b'Announcement evidence')
    server._uploads[file['id']] = {**file, 'workspace': str(tmp_path)}
    response = await post(server, tmp_path, attachments=[file['id']])
    assert response.status_code == 200
    assert response.json()['prompt'].startswith('GOAL')
    assert len(calls) == 1 and calls[0][1][0]['id'] == file['id']
    assert not task_prompts and file['id'] in server._uploads


async def test_quick_format_never_invokes_model_or_regular_chat(tmp_path):
    def forbidden(*args):
        raise AssertionError('Quick formatting cannot call a model or send a task')
    server = make_server(tmp_path, on_prompt=forbidden, on_optimize_prompt=forbidden)
    response = await post(server, tmp_path, mode='quick')
    assert response.status_code == 200
    assert 'Write a short launch announcement.' in response.json()['prompt']


@pytest.mark.parametrize('change', [
    {'workspace': '/another-project'}, {'session_id': 'stale'}, {'attachments': ['missing']},
    {'mode': 'surprise'}, {'draft': ['invalid']},
])
async def test_bad_or_stale_drafts_never_invoke_provider(tmp_path, change):
    called = []
    server = make_server(tmp_path, on_optimize_prompt=lambda *args: called.append(args))
    response = await post(server, tmp_path, **change)
    assert response.status_code in (400, 409) and not called


async def test_missing_callback_is_visible_and_has_no_fallback(tmp_path):
    response = await post(make_server(tmp_path), tmp_path)
    assert response.status_code == 503 and response.json()['error']


async def test_auth_origin_size_and_json_gates(tmp_path):
    called = []
    server = make_server(tmp_path, on_optimize_prompt=lambda *args: called.append(args))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://test') as client:
        assert (await client.post('/api/prompt-optimizer', json={})).status_code == 401
        headers = {'X-Dream-Token': server.token, 'Origin': 'https://unrelated.invalid'}
        assert (await client.post('/api/prompt-optimizer', headers=headers, json={})).status_code == 403
        headers.pop('Origin')
        assert (await client.post('/api/prompt-optimizer', headers=headers, json=[])).status_code == 400
        assert (await client.post('/api/prompt-optimizer', headers=headers, content=b'x' * 131073)).status_code == 413
    assert not called


async def test_provider_error_is_visible_without_task_fallback(tmp_path):
    async def fail(*args):
        raise RuntimeError('Provider unavailable; no draft was generated.')
    called = []
    server = make_server(tmp_path, on_prompt=called.append, on_optimize_prompt=fail)
    response = await post(server, tmp_path)
    assert response.status_code == 502 and 'Provider unavailable' in response.json()['error']
    assert not called


async def test_session_changed_during_request_discards_old_result(tmp_path):
    async def switch(*args):
        server._session['session_id'] = 's2'
        return {'prompt': 'Old session draft'}
    server = make_server(tmp_path, on_optimize_prompt=switch)
    response = await post(server, tmp_path)
    assert response.status_code == 409
    assert 'prompt' not in response.json()
