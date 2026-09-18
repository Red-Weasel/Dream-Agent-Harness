"""History reads immutable registered outputs without generating new work."""
import pytest
from dream.media.store import MediaStore, MediaError


def test_history_keeps_identity_provenance_and_unknown_evidence(tmp_path):
    store=MediaStore(tmp_path)
    project=store.create_project('Rocket')
    source=tmp_path/'first.html';source.write_text('<h1>first</h1>')
    asset=store.import_asset(project['id'],source,provenance={'job_id':'render-one','revision':1})
    row=store.output_history()[0]
    assert row['id']==asset['id'] and row['sha256']==asset['sha256']
    assert row['provenance']['job_id']=='render-one'
    assert row['verification']=='unreported'
    assert source.read_text()=='<h1>first</h1>'
    assert len(store.list_assets(project['id']))==1
    with pytest.raises(MediaError): store.output_history(101)


def test_history_is_bounded_and_workspace_scoped(tmp_path):
    store=MediaStore(tmp_path)
    assert store.output_history()==[]
    with pytest.raises(MediaError): store.output_history(0)
    with pytest.raises(MediaError): store.output_history(True)


async def test_history_route_requires_auth_and_rejects_unbounded_limit(tmp_path):
    import httpx
    from dream.gui.server import StudioServer
    from dream.gui.bus import EventBus
    server=StudioServer(EventBus(),session={'workspace':str(tmp_path)})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test') as client:
        assert (await client.get('/api/media/history')).status_code==401
        headers={'x-dream-token':server.token}
        response=await client.get('/api/media/history',headers=headers)
        assert response.status_code==200 and response.json()['outputs']==[]
        assert (await client.get('/api/media/history?limit=101',headers=headers)).status_code==400
