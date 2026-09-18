"""Private Markdown documents and explicit recall stay bounded and project-scoped."""
import asyncio
import os
import pytest
import httpx

from dream import config
from dream.projects.documents import ProjectDocuments, build_context
from dream.projects.workspace import ProjectError, StaleRevision


def payload(**updates):
    return {'title':'Decision log','kind':'memory','content':'Use keyboard navigation.','include':True,**updates}


def test_markdown_persistence_context_and_conflict(tmp_path, monkeypatch):
    monkeypatch.setattr(config,'DATA_DIR',tmp_path)
    store=ProjectDocuments('project-a')
    saved=store.save(payload())
    assert (store.root/(saved['id']+'.md')).read_text().endswith('Use keyboard navigation.')
    assert (store.root/(saved['id']+'.md')).stat().st_mode & 0o777 == 0o600
    assert 'Use keyboard navigation.' in build_context('project-a')
    assert build_context('project-b')==''
    changed=store.save(payload(content='Revised decision.',expected_sha256=saved['sha256']),saved['id'])
    assert ProjectDocuments('project-a').get(saved['id'])==changed
    with pytest.raises(StaleRevision):store.save(payload(expected_sha256=saved['sha256']),saved['id'])
    assert store.get(saved['id'])['content']=='Revised decision.'


def test_unselected_references_dont_enter_context_and_no_silent_truncation(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'DATA_DIR',tmp_path)
    store=ProjectDocuments('p')
    store.save(payload(content='Large reference '*300,include=False,kind='reference'))
    assert build_context('p')==''
    store.save(payload(content='a'*5900))
    with pytest.raises(ProjectError,match='6,000'):store.save(payload(content='b'*200))
    assert len(store.list())==2


@pytest.mark.parametrize('identity',['../other','/tmp/outside','x/y','',None])
def test_invalid_identity(identity):
    with pytest.raises(ProjectError):ProjectDocuments(identity)


def test_symlinks_special_files_and_corrupt_metadata_refused(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'DATA_DIR',tmp_path)
    store=ProjectDocuments('p');saved=store.save(payload())
    path=store.root/(saved['id']+'.md');path.unlink()
    outside=tmp_path/'outside';outside.write_text('private')
    path.symlink_to(outside)
    with pytest.raises(OSError):store.get(saved['id'])
    assert outside.read_text()=='private'
    path.unlink();os.mkfifo(path)
    with pytest.raises(ProjectError):store.get(saved['id'])
    path.unlink();path.write_text('malformed')
    with pytest.raises(ProjectError):store.get(saved['id'])


async def test_document_routes_real_catalog_auth_and_restart(tmp_path,monkeypatch):
    from dream.projects.library import ProjectLibrary
    from dream.gui.server import StudioServer
    from dream.gui.bus import EventBus
    monkeypatch.setattr(config,'DATA_DIR',tmp_path/'private')
    workspace=tmp_path/'workspace';workspace.mkdir()
    project=ProjectLibrary().create('Fixture',str(workspace))
    srv=StudioServer(EventBus(),session={'workspace':str(workspace)})
    path='/api/projects/'+project['id']+'/documents'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app),base_url='http://test') as c:
        assert (await c.post(path,json=payload())).status_code==401
        c.headers['X-Dream-Token']=srv.token
        assert (await c.post(path,headers={'origin':'https://other.test'},json=payload())).status_code==403
        created=await c.post(path,json=payload());assert created.status_code==200,created.text
        doc=created.json()
        assert len((await c.get(path)).json()['documents'])==1
        assert (await c.post(path+'/'+doc['id'],json=payload(expected_sha256='wrong'))).status_code==409
        assert (await c.get('/api/projects/unrelated/documents')).status_code in {400,404}
        assert (await c.post('/api/projects/unrelated/documents',json=payload())).status_code in {400,404}
    assert not (config.DATA_DIR/'project-documents'/'unrelated').exists()


async def test_context_usage_and_deep_json_are_truthful(tmp_path,monkeypatch):
    from dream.projects.library import ProjectLibrary
    from dream.gui.server import StudioServer
    from dream.gui.bus import EventBus
    monkeypatch.setattr(config,'DATA_DIR',tmp_path/'private')
    project=ProjectLibrary().create('Fixture',str(tmp_path))
    store=ProjectDocuments(project['id'])
    store.save(payload(title='T'*160,content='x'*5815))
    srv=StudioServer(EventBus())
    path='/api/projects/'+project['id']+'/documents'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app),base_url='http://test',headers={'X-Dream-Token':srv.token}) as c:
        result=await c.get(path)
        assert result.json()['context_chars']==len(build_context(project['id']))
        nested='['*10000+'0'+']'*10000
        response=await c.post(path,content=nested)
        assert response.status_code==400
