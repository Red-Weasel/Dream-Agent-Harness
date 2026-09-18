import importlib.util
from pathlib import Path
from starlette.applications import Starlette
from starlette.testclient import TestClient
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer


def client_for(tmp_path, dispatch=None):
    assert importlib.util.find_spec('dream.gui.workflow_routes') is not None, 'Workflow routes missing'
    from dream.gui.workflow_routes import routes
    server = StudioServer(EventBus(), session={'workspace': str(tmp_path)})
    server._workflow_dispatch = dispatch
    client = TestClient(Starlette(routes=routes(server)))
    client.headers['x-dream-token'] = server.token
    return server, client


def test_boundary_and_no_agent_start(tmp_path):
    server, client = client_for(tmp_path)
    assert client.get('/api/workflows/list', headers={'x-dream-token': 'bad'}).status_code == 401
    assert client.post('/api/workflows/create', headers={'origin':'https://foreign.test'}, json={}).status_code == 403
    assert client.post('/api/workflows/create', content='x' * 32769).status_code == 413
    assert client.get('/api/workflows/start').status_code == 405
    task = client.post('/api/workflows/create', json={'recipe':'report','inputs':{'goal':'Report sample'}}).json()['task']
    response = client.post('/api/workflows/start', json={'task_id':task['id'], 'expected_version':1, 'request_id':'one'})
    assert response.status_code == 503
    assert client.get('/api/workflows/list').json()['tasks'][0]['status'] == 'draft'


def test_dispatch_then_download_immutable_checked_artifact(tmp_path):
    from dream.workflows import WorkflowService
    server, client = client_for(tmp_path, lambda *_: None)
    task = client.post('/api/workflows/create', json={'recipe':'report','inputs':{'goal':'Report sample'}}).json()['task']
    assert client.post('/api/workflows/start', json={'task_id':task['id'],'expected_version':1,'request_id':'one'}).json()['task']['status'] == 'queued'
    output=tmp_path/task['output_path'];output.parent.mkdir(parents=True);output.write_text('# Sample report\n\nThis sample has enough readable body text.\n')
    task=WorkflowService(tmp_path).event(task['id'],'final',attempt=1)
    response=client.get(f"/api/workflows/artifacts/{task['id']}/{task['artifacts'][0]['id']}")
    assert response.status_code==200 and response.content.startswith(b'# Sample report')
    assert response.headers['content-disposition'].startswith('attachment')
    assert response.headers['x-content-type-options']=='nosniff'
    assert 'sandbox' in response.headers['content-security-policy']


import asyncio
import threading
import httpx
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('action',['create','check'])
async def test_workspace_operations_do_not_block_studio_loop(tmp_path,monkeypatch,action):
    from dream.gui.workflow_routes import routes
    from dream.workflows import WorkflowService
    server=StudioServer(EventBus(),session={'workspace':str(tmp_path)})
    entered=threading.Event();release=threading.Event()
    original=getattr(WorkflowService,action)
    def blocked(self,*args,**kwargs):
        entered.set();release.wait(2);return original(self,*args,**kwargs)
    svc=WorkflowService(tmp_path);task=svc.create('report',{'goal':'Fixture report'})
    monkeypatch.setattr(WorkflowService,action,blocked)
    app=Starlette(routes=routes(server))
    payload={'recipe':'report','inputs':{'goal':'Another report'}} if action=='create' else {'task_id':task['id'],'expected_version':1}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver',headers={'x-dream-token':server.token}) as client:
        pending=asyncio.create_task(client.post('/api/workflows/'+action,json=payload))
        await asyncio.sleep(.05)
        responsive=entered.is_set() and not pending.done()
        response=await asyncio.wait_for(client.get('/api/workflows/recipes'),.5)
        release.set();await pending
        assert responsive and response.status_code==200
