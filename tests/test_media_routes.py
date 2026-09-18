"""Studio media boundary and real project round trips."""
from starlette.testclient import TestClient
from dream.gui.server import StudioServer
from dream.gui.bus import EventBus


def session(tmp_path):
    server = StudioServer(EventBus(), session={'workspace': str(tmp_path)})
    return server, TestClient(server.app)


def test_media_auth_origin_and_request_limits(tmp_path):
    server, client = session(tmp_path)
    assert client.get('/api/media/status').status_code == 401
    headers = {'x-dream-token': server.token}
    assert client.post('/api/media/create', headers={**headers, 'origin': 'https://foreign.test'}, json={}).status_code == 403
    assert client.post('/api/media/create', headers=headers, content='[]').status_code == 400
    assert client.post('/api/media/create', headers=headers, content='x' * 1_048_577).status_code == 413
    assert client.get('/api/media/status', headers=headers).json()['renderer']['support'] == 'native'
    assert client.get('/api/media/create', headers=headers).status_code == 405


def test_create_revision_upload_preview_and_asset_scope(tmp_path):
    server, client = session(tmp_path)
    client.headers['x-dream-token'] = server.token
    project = client.post('/api/media/create', json={'title': '<b>Demo</b>'}).json()
    pid = project['id']
    assert client.get('/api/media/projects').json()['projects'][0]['title'] == '<b>Demo</b>'
    import base64
    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWuoAAAAASUVORK5CYII=')
    result = client.post('/api/media/upload', params={'project_id': pid, 'filename': 'reference.png'}, content=png)
    assert result.status_code == 200, result.text
    asset = result.json()
    c = project['composition']; c['scenes'][0]['asset_id'] = asset['id']
    assert client.post('/api/media/save', json={'project_id': pid, 'composition': c, 'expected_revision': 1}).json()['revision'] == 2
    assert client.post('/api/media/save', json={'project_id': pid, 'composition': c, 'expected_revision': 1}).status_code == 400
    preview = client.post('/api/media/preview', json={'project_id': pid}).json()['html']
    assert 'data:image/png;base64,' in preview
    assert server.token not in preview
    assert client.get('/api/media/assets/' + asset['id']).content == png
    assert client.get('/api/media/assets/missing').status_code == 400
    assert client.post('/api/media/import', json={'project_id': pid, 'path': '/etc/passwd'}).status_code == 400
    assert not list((tmp_path/'.dream/media/tmp').glob('upload-*'))


def test_validate_source_normalizes_without_saving_and_rejects_bad_scenes(tmp_path):
    server, client = session(tmp_path)
    client.headers['x-dream-token'] = server.token
    project = client.post('/api/media/create', json={'title': 'Draft'}).json()
    valid = client.post('/api/media/validate', json={'composition': {'scenes': [{'title': 'Draft text'}]}})
    assert valid.status_code == 200, valid.text
    assert valid.json()['composition']['scenes'][0]['duration'] == 5
    for scenes in [[None], [{'animation': 'spin'}], [{'duration': 0}]]:
        result = client.post('/api/media/validate', json={'composition': {'scenes': scenes}})
        assert result.status_code == 400
    saved = client.get('/api/media/projects/' + project['id']).json()['project']
    assert saved == project
