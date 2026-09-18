"""Provider completion evidence must precede importing media into a durable job."""
import asyncio
import io

import httpx
import pytest
from PIL import Image

from dream.media.service import MediaService
from dream.media.store import MediaError


def service_with_history(tmp_path, status):
    buffer = io.BytesIO()
    Image.new('RGB', (2, 2)).save(buffer, format='PNG')
    entry = {
        'outputs': {'1': {'images': [
            {'filename': 'preview.png', 'subfolder': '', 'type': 'temp'},
        ]}},
    }
    entry.update(status)
    requests = []

    def respond(request):
        requests.append(request.url.path)
        if request.url.path == '/history/abc':
            return httpx.Response(200, json={'abc': entry})
        assert request.url.path == '/view'
        return httpx.Response(200, content=buffer.getvalue())

    service = MediaService(tmp_path, provider_transport=httpx.MockTransport(respond))
    project = service.store.create_project('Completion fixture')
    job = service.store.create_job(
        project['id'], 'generate', {'base_url': 'http://127.0.0.1:8188'},
    )
    service.store.update_job(job['id'], 'running', backend_id='abc')
    return service, project['id'], job['id'], entry, requests


@pytest.mark.parametrize('state', ['running', 'unknown', 'cancel_requested'])
@pytest.mark.parametrize('status', [
    {'completed': False, 'status_str': 'running'},
    {'completed': False, 'status_str': 'success'},
    {'completed': False},
    {'status_str': 'running'},
    {'completed': True, 'status_str': 'running'},
    {'status_str': 'queued'},
])
def test_incomplete_history_defers_import_and_can_later_complete(tmp_path, state, status):
    service, project_id, job_id, entry, requests = service_with_history(
        tmp_path, {'status': status},
    )
    if state != 'running':
        service.store.update_job(job_id, state)
    result = asyncio.run(service.execute('reconcile', {'job_id': job_id}))
    assert result['status'] == state
    assert service.store.get_job(job_id)['status'] == state
    assert service.store.list_assets(project_id) == []
    assert requests == ['/history/abc']
    entry['status'] = {'completed': True, 'status_str': 'success'}
    result = asyncio.run(service.execute('reconcile', {'job_id': job_id}))
    assert result['status'] == 'succeeded'
    assert len(result['result']['asset_ids']) == 1
    assert len(service.store.list_assets(project_id)) == 1
    assert requests == ['/history/abc', '/history/abc', '/view']


@pytest.mark.parametrize('status', [
    None, [], 'success', False,
    {'completed': 'false'}, {'completed': 0}, {'completed': 1},
    {'completed': None}, {'status_str': []}, {'status_str': None},
])
def test_malformed_status_cannot_publish_success(tmp_path, status):
    service, project_id, job_id, _, requests = service_with_history(tmp_path, {'status': status})
    with pytest.raises(MediaError, match='status'):
        asyncio.run(service.execute('reconcile', {'job_id': job_id}))
    assert service.store.get_job(job_id)['status'] == 'running'
    assert service.store.list_assets(project_id) == []
    assert requests == ['/history/abc']


@pytest.mark.parametrize('completed', [False, True, 'false'])
@pytest.mark.parametrize('failure', ['error', 'failed'])
def test_explicit_failure_precedes_completion_and_outputs(tmp_path, completed, failure):
    service, project_id, job_id, _, requests = service_with_history(
        tmp_path, {'status': {'completed': completed, 'status_str': failure}},
    )
    with pytest.raises(MediaError, match='execution failed'):
        asyncio.run(service.execute('reconcile', {'job_id': job_id}))
    assert service.store.get_job(job_id)['status'] == 'running'
    assert service.store.list_assets(project_id) == []
    assert requests == ['/history/abc']


@pytest.mark.parametrize('status', [
    {}, {'status': {}}, {'status': {'completed': True}},
    {'status': {'status_str': 'success'}},
    {'status': {'completed': True, 'status_str': 'success'}},
])
def test_complete_and_legacy_history_import_valid_outputs(tmp_path, status):
    service, project_id, job_id, _, requests = service_with_history(tmp_path, status)
    result = asyncio.run(service.execute('reconcile', {'job_id': job_id}))
    assert result['status'] == 'succeeded'
    assert len(result['result']['asset_ids']) == 1
    assert service.store.get_asset(result['result']['asset_ids'][0])['mime'] == 'image/png'
    assert len(service.store.list_assets(project_id)) == 1
    assert requests == ['/history/abc', '/view']
