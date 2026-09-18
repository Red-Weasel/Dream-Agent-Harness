"""Real upload-to-prompt behavior and workspace boundaries, without inference."""
import asyncio
from pathlib import Path

import httpx
import pytest
from playwright.async_api import async_playwright, expect

from dream.gui.bus import EventBus
from dream.gui.server import StudioServer


@pytest.fixture
def server(tmp_path):
    prompts = []
    srv = StudioServer(EventBus(), on_prompt=prompts.append,
                       session={'workspace': str(tmp_path), 'model': 'fixture'})
    return srv, prompts, tmp_path


async def test_uploaded_file_reaches_agent_and_retained_chat(server):
    srv, prompts, ws = server
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://test',
                                 headers={'X-Dream-Token': srv.token}) as client:
        result = await client.post('/api/upload?name=notes.txt', content=b'Codename: Foxglove',
                                   headers={'Content-Type': 'application/octet-stream'})
        assert result.status_code == 200
        file = result.json()
        assert (ws / file['path']).read_text() == 'Codename: Foxglove'
        response = await client.post('/api/prompt', json={'prompt': 'What is the codename?', 'attachments': [file['id']]})
        assert response.status_code == 200
        assert file['path'] in prompts[0] and 'read_file' in prompts[0]
        assert 'Use see for visual content' in prompts[0]
        assert 'view_image' not in prompts[0]
        events = srv.bus.conversation.snapshot()['events']
        assert 'notes.txt' in events[-1]['data']
        assert 'read_file' not in events[-1]['data']


async def test_attachment_cannot_reference_arbitrary_or_missing_file(server):
    srv, prompts, _ = server
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://test',
                                 headers={'X-Dream-Token': srv.token}) as client:
        for ids in [['/etc/passwd'], ['unknown'], 'not-an-array', [123]]:
            result = await client.post('/api/prompt', json={'prompt': 'Read', 'attachments': ids})
            assert result.status_code == 400
        assert prompts == []


async def test_upload_rejects_symlink_directory_and_does_not_write_outside(server, tmp_path_factory):
    srv, _, ws = server
    outside = tmp_path_factory.mktemp('outside')
    (ws / 'uploads').symlink_to(outside, target_is_directory=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://test',
                                 headers={'X-Dream-Token': srv.token}) as client:
        result = await client.post('/api/upload?name=escape.txt', content=b'no')
        assert result.status_code == 400
        assert list(outside.iterdir()) == []


async def test_upload_is_bounded_and_duplicate_names_do_not_overwrite(server, monkeypatch):
    from dream.gui import uploads
    monkeypatch.setattr(uploads, 'MAX_FILE_BYTES', 100)
    srv, _, ws = server
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://test',
                                 headers={'X-Dream-Token': srv.token}) as client:
        async def chunks():
            yield b'x' * 80
            yield b'x' * 80
        response = await client.post('/api/upload?name=large.txt', content=chunks())
        assert response.status_code == 413
        results = await asyncio.gather(*(client.post('/api/upload?name=same.txt', content=value)
                                         for value in (b'first', b'second')))
        paths = [r.json()['path'] for r in results]
        assert len(set(paths)) == 2
        assert {(ws / p).read_bytes() for p in paths} == {b'first', b'second'}


async def test_multipart_upload_remains_compatible(server):
    srv, _, ws = server
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://test',
                                 headers={'X-Dream-Token': srv.token}) as client:
        result = await client.post('/api/upload', files={'file': ('table.csv', b'a,b\n1,2\n')})
        assert result.status_code == 200
        assert (ws / result.json()['path']).read_text() == 'a,b\n1,2\n'


async def test_uploaded_image_is_visible_from_selected_workspace(server):
    import base64
    import io
    from PIL import Image
    from dream.tools.context import ToolContext, bind_context
    from dream.tools.vision import see
    srv, _, ws = server
    buffer = io.BytesIO()
    Image.new('RGB', (2, 2), 'red').save(buffer, format='PNG')
    data = buffer.getvalue()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://test',
                                 headers={'X-Dream-Token': srv.token}) as client:
        result = await client.post('/api/upload?name=sample.png', content=data)
        assert result.status_code == 200
    with bind_context(ToolContext(None, None, None, 'image-test', workspace=ws)):
        output = await see.handler({'path': result.json()['path']})
    assert not output.get('is_error'), output
    assert base64.b64decode(output['content'][0]['data']) == data


async def test_missing_workspace_image_cannot_read_another_workspace(server, monkeypatch, tmp_path_factory):
    from dream import config
    from dream.tools.context import ToolContext, bind_context
    from dream.tools.vision import see
    _, _, ws = server
    other = tmp_path_factory.mktemp('other-images')
    (other / 'uploads').mkdir()
    (other / 'uploads' / 'sample.png').write_bytes(b'other workspace image')
    monkeypatch.setattr(config, 'ROOT', other)
    with bind_context(ToolContext(None, None, None, 'image-test', workspace=ws)):
        output = await see.handler({'path': 'uploads/sample.png'})
    assert output.get('is_error'), output


async def test_browser_attachment_send_retry_refresh_and_studio(server, monkeypatch):
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    srv, prompts, _ = server
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': 1100, 'height': 800})
            page.set_default_timeout(4000)
            await page.goto(url + '&companion=1')
            await expect(page.locator('#dream-nav-studio')).to_be_visible()
            await page.locator('#file-input').set_input_files({'name': 'notes.txt', 'mimeType': 'text/plain', 'buffer': b'Foxglove'})
            await expect(page.locator('#attachments')).to_contain_text('notes.txt')
            await expect(page.locator('#attachments')).to_contain_text('Ready')
            await page.locator('#input').fill('Read my notes')
            await page.reload()
            await expect(page.locator('#input')).to_have_value('Read my notes')
            await expect(page.locator('#attachments')).to_contain_text('notes.txt')
            await page.route('**/api/prompt', lambda r: r.fulfill(status=503, json={'error': 'Try again'}))
            await page.locator('#send').click()
            await expect(page.locator('#input')).to_have_value('Read my notes')
            await expect(page.locator('#attachments')).to_contain_text('notes.txt')
            assert prompts == []
            await page.unroute('**/api/prompt')
            await page.locator('#send').click()
            await expect(page.locator('#attachments .attachment')).to_have_count(0)
            assert len(prompts) == 1 and 'uploads/' in prompts[0]
            await expect(page.locator('#stream .you')).to_have_count(1)
            from dream.core.backends.base import Event
            srv.bus.publish(Event('turn_end', {}))
            await expect(page.locator('#stat')).to_have_text('Ready')
            await page.locator('#dream-nav-studio').click()
            await expect(page.locator('#studio-empty')).to_be_visible()
            await page.screenshot(path='/tmp/dream-harness-20260909-evidence/studio-attachments.png')
            await page.set_viewport_size({'width': 420, 'height': 800})
            await page.locator('#dream-nav-chat').click()
            await expect(page.get_by_role('button', name='Attach files', exact=True)).to_be_visible()
            await expect(page.locator('#stream .you')).to_be_visible()
            await expect(page.locator('#stream .you')).to_contain_text('Read my notes')
            await expect(page.locator('#stream .you')).to_have_css('opacity', '1')
            await expect(page.locator('#studio-error')).to_be_hidden()
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            await page.screenshot(path='/tmp/dream-harness-20260909-evidence/chat-narrow.png')
            await browser.close()
    finally:
        await srv.stop()
