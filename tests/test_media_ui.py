"""Real Create UI and opaque preview, at desktop and narrow widths."""
import asyncio
import pytest
from playwright.async_api import async_playwright
from dream.gui.server import StudioServer
from dream.gui.bus import EventBus


@pytest.mark.asyncio
async def test_create_scene_revision_export_and_narrow_layout(tmp_path):
    server = StudioServer(EventBus(), session={'workspace': str(tmp_path)})
    await server.start()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.goto(server.url)
            await page.locator('#dream-create-open').click()
            await page.locator('#mc-name').fill('Software story')
            await page.locator('#mc-new button').click()
            await page.locator('#mc-title').wait_for(state='visible')
            await page.locator('#mc-scene-title').fill('Bring your work together')
            await page.locator('#mc-save').click()
            await page.wait_for_function("document.querySelector('#mc-revision').textContent==='Revision 2'")
            frame = page.frame_locator('#mc-frame')
            await frame.locator('h1').wait_for()
            assert await frame.locator('h1').text_content() == 'Bring your work together'
            assert await page.locator('#mc-frame').get_attribute('sandbox') == 'allow-scripts'
            assert server.token not in (await page.locator('#mc-frame').get_attribute('srcdoc'))
            await page.locator('#mc-format').select_option('html')
            await page.locator('#mc-render').click()
            await page.wait_for_function("document.querySelector('#mc-jobs [data-status=succeeded]')", timeout=15000)
            response = await page.request.get(server.url.split('/?')[0] + '/api/media/projects', headers={'x-dream-token': server.token})
            projects = (await response.json())['projects']
            assert projects[0]['revision'] == 2
            assert projects[0]['composition']['scenes'][0]['title'] == 'Bring your work together'
            await page.screenshot(path=str(tmp_path/'create-desktop.png'))
            await page.set_viewport_size({'width':390,'height':844})
            await page.screenshot(path=str(tmp_path/'create-narrow.png'))
            assert await page.evaluate("document.querySelector('#dream-create').scrollWidth <= document.querySelector('#dream-create').clientWidth + 1")
            assert not errors
        finally:
            await browser.close()
            await server.stop()


@pytest.mark.asyncio
async def test_source_validation_canvas_and_revision(tmp_path):
    import json
    server = StudioServer(EventBus(), session={'workspace': str(tmp_path)})
    await server.start()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.goto(server.url)
            await page.locator('#dream-create-open').click()
            await page.locator('#mc-name').fill('Draft protection')
            await page.locator('#mc-new button').click()
            await page.locator('#mc-title').wait_for(state='visible')
            await page.locator('.mc-advanced summary').click()
            original = await page.locator('#mc-scene-title').input_value()
            await page.locator('#mc-source').fill(json.dumps({'scenes': [None]}))
            await page.locator('#mc-source-apply').click()
            await page.wait_for_function("document.querySelector('#mc-feedback').textContent.includes('Each scene must be an object')")
            assert await page.locator('#mc-scene-title').input_value() == original
            composition = {'width': 1080, 'height': 1920, 'scenes': [{'title': 'Portrait draft'}]}
            await page.locator('#mc-source').fill(json.dumps(composition))
            await page.locator('#mc-source-apply').click()
            await page.wait_for_function("document.querySelector('#mc-canvas').value==='1080,1920'")
            assert await page.locator('#mc-duration').input_value() == '5'
            await page.locator('#mc-save').click()
            await page.wait_for_function("document.querySelector('#mc-revision').textContent==='Revision 2'")
            box = await page.locator('.mc-preview').bounding_box()
            assert abs(box['width'] / box['height'] - 1080 / 1920) < .02
            await page.locator('#mc-history-load').click()
            await page.wait_for_function("document.querySelector('#mc-canvas').value==='1280,720'")
            assert await page.locator('#mc-scene-title').input_value() == original
            assert not errors
        finally:
            await browser.close()
            await server.stop()


@pytest.mark.asyncio
async def test_missing_video_tools_and_cancelled_job_update(tmp_path, monkeypatch):
    from dream.media.service import MediaService
    service = MediaService(tmp_path)
    project = await service.execute('create', {'title': 'Missing tools'})
    job = service.store.create_job(project['id'], 'render', {'format': 'mp4', 'revision': 1})
    original_status = MediaService.status
    def missing_tools(self):
        result = original_status(self)
        result['renderer'].update(ffmpeg=False, ffprobe=False)
        return result
    monkeypatch.setattr(MediaService, 'status', missing_tools)
    server = StudioServer(EventBus(), session={'workspace': str(tmp_path)})
    await server.start()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
            await page.goto(server.url)
            await page.locator('#dream-create-open').click()
            await page.locator('#mc-projects button').click()
            await page.locator('#mc-title').wait_for(state='visible')
            await page.wait_for_function("document.querySelector('#mc-render').disabled")
            assert 'ffmpeg and ffprobe' in await page.locator('#mc-renderer-status').text_content()
            assert not await page.locator('#mc-subscriptions').evaluate('(el) => el.open')
            assert not await page.locator('#mc-style').evaluate('(el) => el.open')
            assert '1280 × 720 · 24 fps' in await page.locator('#mc-summary').text_content()
            card = page.locator(f'[data-job-id="{job["id"]}"]')
            assert 'Queued' in await card.text_content()
            assert await card.locator('a').count() == 0
            await card.get_by_role('button', name='Cancel', exact=True).click()
            await page.wait_for_function("document.querySelector('#mc-jobs [data-status=cancelled]')")
            assert service.store.get_job(job['id'])['status'] == 'cancelled'
            await page.locator('#mc-format').select_option('html')
            assert await page.locator('#mc-render').is_enabled()
            assert 'No video encoding is needed' in await page.locator('#mc-format-help').text_content()
        finally:
            await browser.close()
            await server.stop()
