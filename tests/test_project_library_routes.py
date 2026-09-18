"""Saved project endpoints enforce authentication, scope and revision checks."""
import httpx
import pytest
from starlette.applications import Starlette
from types import SimpleNamespace

from dream import config
from dream.gui.project_library_routes import routes
from dream.memory.store import MemoryStore
from dream.projects.library import ProjectLibrary


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DB_PATH', tmp_path / 'sessions.db')
    store = MemoryStore(config.DB_PATH)
    store.start_session('current', 'Current chat')
    store.add_turn('current', 'user', 'Existing unsaved conversation')
    server = SimpleNamespace(_authorized=lambda request: request.headers.get('x-dream-token') == 'test',
        _session={'workspace':str(tmp_path), 'session_id':'current'})
    app = Starlette(routes=routes(server))
    yield app, store
    store.close()


async def test_authenticated_current_project_save_and_stale_edit(api, tmp_path):
    app, store = api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/api/projects')).status_code == 401
        headers = {'x-dream-token':'test'}
        response = await client.post('/api/projects', headers={**headers,'origin':'http://evil'},
                                     json={'name':'Project','workspace':str(tmp_path)})
        assert response.status_code == 403
        response = await client.post('/api/projects', headers=headers, json={'name':'Project','workspace':str(tmp_path)})
        project = response.json()['project']
        assert project['session_count'] == 1
        path = '/api/projects/' + project['id']
        detail = (await client.get(path, headers=headers)).json()
        assert detail['sessions'][0]['id'] == 'current'
        history = (await client.get(path + '/sessions/current', headers=headers)).json()
        assert history['turns'][0]['content'] == 'Existing unsaved conversation'
        assert (await client.post(path, headers=headers, json={'expected_revision':1,'instructions':'saved'})).status_code == 200
        assert (await client.post(path, headers=headers, json={'expected_revision':1,'instructions':'stale'})).status_code == 409
        assert (await client.get(path, headers=headers)).json()['project']['instructions'] == 'saved'


async def test_other_workspace_never_inherits_current_history(api, tmp_path):
    app, store = api
    other = tmp_path / 'other'
    other.mkdir()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                 headers={'x-dream-token':'test'}) as client:
        project = (await client.post('/api/projects', json={'name':'Other','workspace':str(other)})).json()['project']
        path = '/api/projects/' + project['id']
        assert (await client.get(path)).json()['sessions'] == []
        assert (await client.get(path + '/sessions/current')).status_code == 400
        assert (await client.post('/api/projects', content='x' * 96001)).status_code == 413


async def test_project_memories_and_end_session_summaries_are_read_only_and_scoped(api, tmp_path):
    app, store = api
    store.end_session('current', 'Consolidated verified outcome')
    store.upsert_memory('semantic','Known fact','Scoped body', source_session='current')
    store.upsert_memory('semantic','Unrelated fact','Other body', source_session='other-session')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                 headers={'x-dream-token':'test'}) as client:
        project = (await client.post('/api/projects', json={'name':'Here','workspace':str(tmp_path)})).json()['project']
        response = await client.get('/api/projects/' + project['id'] + '/memory')
        body = response.json()
        assert [row['title'] for row in body['memories']] == ['Known fact']
        assert body['summaries'][0]['summary'] == 'Consolidated verified outcome'
        assert 'latest source session' in body['provenance']
        assert (await client.post('/api/projects/' + project['id'] + '/memory', json={})).status_code == 405


async def test_old_conversation_link_requires_confirmation_and_cannot_steal_live_session(api, tmp_path):
    app, store = api
    store.start_session('old', 'Earlier conversation with unknown workspace')
    store.end_session('old', 'Saved older summary')
    other = tmp_path / 'other'
    other.mkdir()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                 headers={'x-dream-token':'test'}) as client:
        project = (await client.post('/api/projects', json={'name':'Other','workspace':str(other)})).json()['project']
        path = '/api/projects/' + project['id'] + '/sessions'
        candidates = (await client.get('/api/projects/unassigned-sessions')).json()
        assert 'old' in [s['id'] for s in candidates['sessions']]
        assert 'unknown' in candidates['scope']
        assert (await client.post(path, json={'session_id':'old','confirmed_project':False})).status_code == 400
        assert (await client.post(path, json={'session_id':'missing','confirmed_project':True})).status_code == 400
        assert (await client.post(path, json={'session_id':'current','confirmed_project':True})).status_code == 400
        linked = await client.post(path, json={'session_id':'old','confirmed_project':True})
        assert linked.status_code == 200 and linked.json()['sessions'][0]['summary'] == 'Saved older summary'
        assert 'old' not in [s['id'] for s in (await client.get('/api/projects/unassigned-sessions')).json()['sessions']]
        here = (await client.post('/api/projects', json={'name':'Here','workspace':str(tmp_path)})).json()['project']
        assert (await client.post('/api/projects/' + here['id'] + '/sessions',
                                  json={'session_id':'old','confirmed_project':True})).status_code == 400


async def test_deep_json_request_is_rejected_without_server_error(api):
    app, _ = api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                 headers={'x-dream-token':'test'}) as client:
        response = await client.post('/api/projects', content='['*10000+'0'+']'*10000)
        assert response.status_code == 400
