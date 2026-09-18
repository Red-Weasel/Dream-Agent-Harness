"""Auth and source integrity checks without Engine or inference endpoints."""
from pathlib import Path
from starlette.applications import Starlette
from starlette.testclient import TestClient
from dream.gui.project_routes import routes


class Server:
    def __init__(self, workspace): self.workspace = workspace
    def _workspace(self): return self.workspace
    def _authorized(self, request): return request.headers.get('x-dream-token') == 'fixture'


def test_project_routes_auth_origin_stale_source_and_text_only(tmp_path):
    (tmp_path / 'demo.html').write_text('<script>alert(1)</script>needle')
    client = TestClient(Starlette(routes=routes(Server(tmp_path), lambda workspace: [])))
    assert client.get('/api/project/manifest').status_code == 401
    client.headers['x-dream-token'] = 'fixture'
    assert client.post('/api/project/pin', headers={'origin': 'https://other.test'}, json={}).status_code == 403
    assert client.post('/api/project/pin', content='[]').status_code == 400
    assert client.post('/api/project/pin', content='x' * 16385).status_code == 413
    assert client.get('/api/project/pin').status_code == 405
    result = client.post('/api/project/search', json={'query': 'needle'}).json()
    hit = result['hits'][0]
    source = client.post('/api/project/source', json=hit)
    assert source.status_code == 200 and source.headers['content-type'] == 'application/json'
    download = client.post('/api/project/download', json=hit)
    assert download.headers['content-disposition'].startswith('attachment;')
    assert download.headers['x-content-type-options'] == 'nosniff'
    (tmp_path / 'demo.html').write_text('changed')
    assert client.post('/api/project/source', json=hit).status_code == 409
    assert client.post('/api/project/source', json={'path': '../outside'}).status_code == 400
    assert client.get('/api/project/recovery').json()['available']
    data = {'kind': 'constraint', 'text': 'No model loads', 'expected_revision': 0}
    assert client.post('/api/project/pin', json=data).status_code == 200
    assert client.post('/api/project/pin', json=data).status_code == 409
    assert 'No model loads' in client.get('/api/project/context').json()['text']
