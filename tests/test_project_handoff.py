"""Handoff drafts use saved project evidence without inference or implicit saving."""
import re
import httpx
import pytest
from test_desktop_chat import chat  # noqa: F401
from dream import config
from dream.memory.store import MemoryStore
from dream.projects.library import ProjectLibrary
from dream.projects.documents import ProjectDocuments
from dream.projects.workspace import ProjectError


@pytest.fixture
def archive(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path / 'private')
    monkeypatch.setattr(config, 'DB_PATH', tmp_path / 'archive.sqlite3')
    store = MemoryStore(config.DB_PATH)
    store.start_session('session-a', 'Launch work')
    store.add_turn('session-a', 'user', 'Improve the existing rocket. Do not rebuild it.')
    store.add_turn('session-a', 'assistant', 'I saved launch.blend; rendering remains unverified.')
    library = ProjectLibrary()
    project = library.create('Rocket', str(tmp_path), session_id='session-a')
    yield library, project, store
    store.close()


def test_draft_retains_sources_unknown_checks_and_explicit_save(archive):
    library, project, store = archive
    draft = library.handoff(project['id'], 'session-a')
    document = draft['document']
    assert document['kind'] == 'memory' and document['include'] is False
    assert 'Do not rebuild it.' in document['content']
    assert 'rendering remains unverified' in document['content']
    assert 'UNKNOWN' in document['content']
    assert 'Assistant report' in document['content']
    assert 'session-a' in document['content']
    assert draft['source']['project_id'] == project['id']
    assert ProjectDocuments(project['id']).list() == []
    saved = ProjectDocuments(project['id']).save(document)
    assert ProjectDocuments(project['id']).get(saved['id'])['content'] == document['content']
    assert store.get_session('session-a')['summary'] is None


def test_draft_rejects_cross_project_and_missing_session(archive, tmp_path):
    library, project, store = archive
    other = tmp_path / 'other'; other.mkdir()
    second = library.create('Other', str(other))
    with pytest.raises(ProjectError): library.handoff(second['id'], 'session-a')
    library.associate(project['id'], 'missing')
    with pytest.raises(ProjectError): library.handoff(project['id'], 'missing')


def test_draft_bounds_sources_and_finds_recent_text_after_tool_flood(archive):
    library, project, store = archive
    store.add_turn('session-a', 'user', 'Latest correction: keep the saved asset.' + 'x' * 3000)
    store.add_turn('session-a', 'assistant', 'Latest report: checks have not run.' + 'y' * 6000)
    for _ in range(30): store.add_turn('session-a', 'tool', 'DO NOT COPY TOOL FLOOD ' * 400)
    store.end_session('session-a', 'Old summary ' * 1000)
    draft = library.handoff(project['id'], 'session-a')
    body = draft['document']['content']
    assert 'Latest correction' in body and 'Latest report' in body
    assert 'DO NOT COPY TOOL FLOOD' not in body
    assert 'excerpt' in body.lower() and draft['source']['partial'] is True
    assert len(body) < 5500


async def test_handoff_route_requires_auth_and_does_not_save(archive):
    from dream.gui.bus import EventBus
    from dream.gui.server import StudioServer
    library, project, store = archive
    server = StudioServer(EventBus())
    url = f"/api/projects/{project['id']}/sessions/session-a/handoff"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://test') as client:
        assert (await client.get(url)).status_code == 401
        client.headers['X-Dream-Token'] = server.token
        response = await client.get(url)
        assert response.status_code == 200, response.text
        assert response.headers['cache-control'] == 'no-store'
        assert response.json()['document']['include'] is False
        assert (await client.get(url.replace('session-a','missing'))).status_code == 400
    assert ProjectDocuments(project['id']).list() == []


async def test_real_browser_draft_save_and_reopen(chat, archive, tmp_path):
    from playwright.async_api import expect
    library, project, store = archive
    _, page, prompts, controls = chat
    await page.locator('#dream-nav-projects').click()
    await page.locator('#dream-projects-page .library-list button').first.click()
    await page.get_by_role('button', name='Open conversation', exact=True).click()
    await page.get_by_role('button', name='Draft project handoff', exact=True).click()
    body = page.get_by_label('Document Markdown', exact=True)
    await expect(body).to_have_value(re.compile('.*Do not rebuild it.*', re.S))
    await expect(page.get_by_label('Include in project context', exact=True)).not_to_be_checked()
    await body.fill('## Goal\nPolish existing rocket.\n## Next action\nInspect launch.blend.\n## Source\nsession-a')
    await page.get_by_role('button', name='Save document', exact=True).click()
    await expect(page.locator('[data-document-editor]')).to_contain_text('Document saved')
    docs = ProjectDocuments(project['id']).list()
    assert len(docs) == 1 and docs[0]['include'] is False
    await page.get_by_role('button', name='Close editor', exact=True).click()
    await page.get_by_role('button', name='Edit document', exact=True).click()
    await expect(body).to_have_value(docs[0]['content'])
    assert prompts == []
    assert not any(c.get('action') == 'project_open' for c in controls)
    await page.screenshot(path=str(tmp_path / 'handoff-saved.png'))


def test_multiline_excerpt_overhead_stays_within_context_budget(archive):
    library, project, store = archive
    # Quoting every line expands text; raw source limits alone are insufficient.
    store.add_turn('session-a', 'user', '\n' * 800)
    store.add_turn('session-a', 'assistant', '\n' * 1000)
    store.end_session('session-a', '\n' * 1000)
    draft = library.handoff(project['id'], 'session-a')['document']
    assert len(draft['content']) < 5500
    ProjectDocuments(project['id']).save({**draft, 'include': True})
