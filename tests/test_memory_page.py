"""The Memory page and project memory (owner request, DREAM-083)."""
import httpx
import pytest
from playwright.async_api import async_playwright, expect

from dream import config
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.memory import project as project_memory

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mem(tmp_path, monkeypatch):
    root = tmp_path / "memory"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(config, "MEMORY_DIR", root)
    monkeypatch.setattr(config, "MEMORY_INDEX_FILE", root / "MEMORY.md")
    (root / "user-prefers-plain-english.md").write_text(
        "---\nname: user-prefers-plain-english\ntitle: Plain English\nkind: semantic\n---\nThe user wants plain English.\n")
    ws = tmp_path / "rocket"
    ws.mkdir()
    project_memory.add_note(ws, "three.js is vendored under vendor/three")
    srv = StudioServer(EventBus(), on_prompt=lambda p: None, session={'workspace': str(ws), 'model': 'fixture'})
    return srv, root, ws


async def test_list_read_save_and_delete(mem):
    srv, root, ws = mem
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://test',
                                 headers={'X-Dream-Token': srv.token}) as client:
        items = (await client.get('/api/memory')).json()['items']
        scopes = {(i['scope'], i['title']) for i in items}
        assert ('global', 'Plain English') in scopes
        project = next(i for i in items if i['scope'] == 'project')
        assert project['title'] == 'rocket — project notebook' and project['description'] == str(ws.resolve())
        text = (await client.get(f"/api/memory/project/{project['name']}")).json()['text']
        assert 'three.js is vendored' in text
        saved = await client.post('/api/memory/global/user-prefers-plain-english', json={'text': '---\ntitle: Plain English\n---\nShort answers, plain words.\n'})
        assert saved.status_code == 200 and 'Short answers' in (root / 'user-prefers-plain-english.md').read_text()
        assert (await client.post('/api/memory/global/..%2Fsecrets', json={'text': 'x'})).status_code in (400, 404)
        assert (await client.post('/api/memory/global/MEMORY', json={'text': 'x'})).status_code == 400
        gone = await client.post(f"/api/memory/project/{project['name']}", json={'delete': True})
        assert gone.json()['deleted'] and not project_memory.notebook(ws).exists()
        assert (await client.get('/api/memory', headers={'X-Dream-Token': 'wrong'})).status_code == 401


async def test_project_memory_reaches_the_instructions_only_in_its_project(mem, tmp_path):
    _, _, ws = mem
    assert 'three.js is vendored' in project_memory.prompt_section(ws)
    other = tmp_path / "other"
    other.mkdir()
    assert project_memory.prompt_section(other) == ''
    assert project_memory.prompt_section(config.ROOT) == ''


async def test_the_memory_page_opens_edits_and_drafts_a_consolidation(mem, monkeypatch):
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    srv, root, _ = mem
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': 1200, 'height': 800})
            page.set_default_timeout(4000)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            await page.goto(url + '&companion=1')
            await page.locator('#dream-nav-memory').click()
            await expect(page.locator('#dream-memory-page')).to_be_visible()
            await expect(page.locator('#dream-memory-page .memory-row')).to_have_count(2)
            await page.locator('#dream-memory-page .memory-row button', has_text='Plain English').click()
            editor = page.locator('#dream-memory-page textarea')
            await expect(editor).to_have_value(__import__('re').compile('plain English'))
            await editor.fill('---\ntitle: Plain English\n---\nEdited in the Memory page.\n')
            await page.locator('#dream-memory-page button', has_text='Save').click()
            await expect(page.locator('#dream-memory-page .library-status')).to_contain_text('Saved')
            assert 'Edited in the Memory page' in (root / 'user-prefers-plain-english.md').read_text()
            await page.locator('#dream-memory-page .memory-row input').first.check()
            await page.locator('#dream-memory-page button', has_text='Consolidate selected').click()
            await expect(page.locator('#input')).to_have_value(__import__('re').compile('Consolidate these memories'))
            assert errors == []
            await browser.close()
    finally:
        await srv.stop()
