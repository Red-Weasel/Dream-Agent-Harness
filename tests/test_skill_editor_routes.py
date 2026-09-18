from starlette.applications import Starlette
from starlette.testclient import TestClient

from dream.gui.skill_routes import routes, MAX_JSON
from test_skill_editor import body


class Server:
    def _authorized(self, request):
        return request.headers.get('x-dream-token') == 'fixture'


def test_authenticated_routes_origin_full_text_and_conflict(tmp_path, monkeypatch):
    monkeypatch.setenv('DREAM_SKILL_DIRS', str(tmp_path / 'empty'))
    client = TestClient(Starlette(routes=routes(Server())))
    assert client.get('/api/skills').status_code == 401
    assert client.post('/api/skills', json={'name': 'example', 'content': body()}).status_code == 401
    client.headers['x-dream-token'] = 'fixture'
    assert client.post('/api/skills', headers={'origin': 'https://other.test'}, json={}).status_code == 403
    assert client.post('/api/skills', content='[]').status_code == 400
    assert client.post('/api/skills', content='x' * (MAX_JSON + 1)).status_code == 413
    created = client.post('/api/skills', json={'name': 'example', 'content': body()})
    assert created.status_code == 200
    assert created.json()['content'] == body()
    full = client.get('/api/skills/example')
    assert full.headers['cache-control'] == 'no-store'
    assert full.json()['content'] == body()
    assert client.get('/api/skills').json()['skills'][0]['name'] == 'example'
    saved = client.post('/api/skills/example', json={'content': body(text='New.'), 'expected_sha256': full.json()['sha256']})
    assert saved.status_code == 200
    assert client.post('/api/skills/example', json={'content': body(), 'expected_sha256': full.json()['sha256']}).status_code == 409
    assert client.get('/api/skills/unknown').status_code == 404
    assert client.post('/api/skills/example', json={'content': body()}).status_code == 400
