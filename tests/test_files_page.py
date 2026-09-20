"""The Files page (owner request, DREAM-084): browse the project folder and Dream's
own setup folders as a tree, and read a file. Read-only, and never outside a root."""
import re

import httpx
import pytest
from playwright.async_api import async_playwright, expect

from dream.gui.bus import EventBus
from dream.gui.server import StudioServer

pytestmark = pytest.mark.asyncio


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "rocket"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.js").write_text("export const stages = 2;\n")
    (root / "README.md").write_text("# Rocket\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("secret")
    (root / "escape").symlink_to(tmp_path)
    srv = StudioServer(EventBus(), on_prompt=lambda p: None,
                       session={'workspace': str(root), 'model': 'fixture'})
    return srv, root


async def test_the_tree_lists_reads_and_refuses_to_leave_the_folder(ws):
    srv, root = ws
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://test',
                                 headers={'X-Dream-Token': srv.token}) as client:
        top = (await client.get('/api/files?root=workspace')).json()
        names = {e['name']: e['type'] for e in top['entries']}
        assert names['src'] == 'dir' and names['README.md'] == 'file'
        assert '.git' not in names and 'escape' not in names     # hidden, and a symlink leading out
        assert top['base'] == str(root.resolve())
        inner = (await client.get('/api/files?root=workspace&path=src')).json()
        assert [e['name'] for e in inner['entries']] == ['main.js'] and inner['path'] == 'src'
        text = (await client.get('/api/files/read?root=workspace&path=src/main.js')).json()
        assert text['text'] == 'export const stages = 2;\n' and text['binary'] is False

        assert (await client.get('/api/files?root=workspace&path=../..')).status_code == 400
        assert (await client.get('/api/files?root=nowhere')).status_code == 400
        assert (await client.get('/api/files?root=dream&path=var')).status_code == 400
        assert (await client.get('/api/files?root=dream')).status_code == 200
        assert (await client.get('/api/files?root=workspace',
                                 headers={'X-Dream-Token': 'wrong'})).status_code == 401


async def test_the_files_page_opens_a_folder_and_shows_a_file(ws, monkeypatch):
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    srv, _ = ws
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': 1200, 'height': 800})
            page.set_default_timeout(4000)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            await page.goto(url + '&companion=1')
            await page.locator('#dream-nav-files').click()
            await expect(page.locator('#dream-files-page')).to_be_visible()
            await expect(page.locator('#dream-files-page .file-folder summary')).to_have_text('src')
            await page.locator('#dream-files-page .file-folder summary').click()
            await page.locator('#dream-files-page .file-entry', has_text='main.js').click()
            await expect(page.locator('#dream-files-page .file-text')).to_contain_text('export const stages = 2;')
            await page.locator('#dream-files-page .library-list > .file-entry', has_text='README.md').click()
            await expect(page.locator('#dream-files-page .file-text')).to_contain_text('# Rocket')
            await page.locator('#dream-files-page button', has_text='Ask about this file').click()
            await expect(page.locator('#input')).to_have_value(re.compile('README.md'))
            assert errors == []
            await browser.close()
    finally:
        await srv.stop()
