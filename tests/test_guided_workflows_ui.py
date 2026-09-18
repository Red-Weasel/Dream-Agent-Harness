"""Real browser, local fixture dispatch, GPU explicitly disabled."""
import pytest
from playwright.async_api import async_playwright, expect
from dream.gui.server import StudioServer
from dream.gui.bus import EventBus


@pytest.mark.asyncio
async def test_guided_review_queue_missing_output_and_animation_handoff(tmp_path):
    server = StudioServer(EventBus(), session={'workspace':str(tmp_path)})
    server._workflow_dispatch = lambda *_: None
    await server.start()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=['--disable-gpu'])
        try:
            page = await browser.new_page(viewport={'width':1100,'height':900})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            await page.goto(server.url)
            await page.locator('#dream-create-open').click()
            await page.locator('#mc-guided-open').click(timeout=1500)
            await page.get_by_role('button',name='Document or report',exact=True).click()
            await page.locator('#gw-goal').fill('Write a sample report')
            await page.get_by_role('button',name='Review task',exact=True).click()
            await page.get_by_text('Task instructions and save location',exact=True).click()
            await page.locator('#gw-review textarea').wait_for()
            assert 'artifacts/guided/' in await page.locator('#gw-review textarea').input_value()
            assert await page.get_by_role('button',name='Start task',exact=True).is_visible()
            await page.get_by_role('button',name='Start task',exact=True).click()
            await page.wait_for_function("document.querySelector('#gw-review').textContent.includes('Queued for the connected agent')")
            from dream.workflows import WorkflowService
            svc=WorkflowService(tmp_path); task=svc.list()[0]
            svc.event(task['id'],'final',attempt=1)
            await page.get_by_role('button',name='Refresh tasks',exact=True).click()
            await page.wait_for_function("document.querySelector('#gw-review').textContent.includes('missing')")
            assert 'missing' in await page.locator('#gw-review').text_content()
            assert await page.locator('#gw-review a').count()==0
            await page.get_by_role('button',name='Animation',exact=True).click()
            await expect(page.locator('#mc-name')).to_be_focused()
            await page.locator('#mc-guided-open').click()
            await page.set_viewport_size({'width':390,'height':844})
            assert await page.evaluate("document.querySelector('#dream-create').scrollWidth <= document.querySelector('#dream-create').clientWidth+1")
            assert not errors
        finally:
            await browser.close();await server.stop()
