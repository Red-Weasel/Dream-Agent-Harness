"""Real Chromium checks for populated workspaces, with fixture-only model output."""
import re
from test_workspace_library_ui import library
from test_desktop_chat import chat
from dream.core.backends.base import Event
from playwright.async_api import expect

async def test_atmosphere(library):
    srv,page,prompts,_,_=library
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    await srv._accept_prompt('Build a quiet place for ideas to become real.')
    srv.bus.publish(Event('text_delta','## Your workspace is ready\n\nThe project keeps your notes, conversations, and working files together. We can shape the next version here.\n\n- Review the project instructions\n- Sketch the first screen\n- Keep the draft available as we work'))
    srv.bus.publish(Event('result',{}))
    await expect(page.locator('#stream')).to_contain_text('Your workspace is ready')
    await page.locator('#input').fill('Keep the eclipse atmosphere in the workspace')
    await page.evaluate("async()=>{const i=new Image();i.src='/assets/dream-eclipse.png';await i.decode()}")
    for width,height in [(1280,800),(720,520),(390,844)]:
        await page.set_viewport_size({'width':width,'height':height})
        await page.locator('#dream-nav-chat').click()
        await page.screenshot(path=f'/tmp/dream-atmosphere-chat-{width}.png')
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await expect(page.locator('#input')).to_be_visible()
        for view in ['projects','skills']:
            await page.locator('#dream-nav-'+view).click()
            await page.locator('#dream-'+view+'-page .library-list button').first.click()
            if view == 'projects':
                await expect(page.locator('#dream-projects-page .library-detail h2')).to_have_text('Alpha')
            else:
                await expect(page.get_by_label('Skill Markdown',exact=True)).to_have_value(re.compile('Full instructions beyond the catalog.'))
            await expect(page.locator('#dream-'+view+'-page .library-status')).not_to_contain_text('Reading')
            await page.screenshot(path=f'/tmp/dream-atmosphere-{view}-{width}.png')
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    await page.set_viewport_size({'width':1280,'height':800})
    await page.locator('#dream-nav-chat').click()
    srv.bus.publish(Event('studio',{'op':'show','path':'fixture.html','content':'<!doctype html><html><body style="background:#fff;color:#142138;padding:32px;font-family:serif"><h1>Working artifact</h1><p>This preview keeps its own colors and typography.</p></body></html>'}))
    await expect(page.frame_locator('#artbody iframe').locator('h1')).to_have_text('Working artifact')
    await page.screenshot(path='/tmp/dream-atmosphere-studio-1280.png')
    for width,height in [(720,520),(390,844)]:
        await page.set_viewport_size({'width':width,'height':height})
        await page.locator('#dream-nav-studio').click()
        await expect(page.locator('#artbody iframe')).to_be_visible()
        await page.screenshot(path=f'/tmp/dream-atmosphere-studio-{width}.png')
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    await page.set_viewport_size({'width':1280,'height':800})
    await page.emulate_media(reduced_motion='reduce')
    assert await page.locator('.dream').first.evaluate('(e)=>getComputedStyle(e).animationName')=='none'
    await page.locator('#input').focus()
    assert await page.locator('.composer').evaluate('(e)=>getComputedStyle(e).borderTopColor')=='rgb(255, 195, 142)'
    await page.keyboard.press('Tab')
    assert await page.evaluate('getComputedStyle(document.activeElement).outlineStyle')=='solid'
    await page.evaluate("document.documentElement.dataset.atmosphere='quiet'")
    assert 'dream-eclipse' not in await page.locator('.split').evaluate('(e)=>getComputedStyle(e).backgroundImage')
    await page.evaluate("delete document.documentElement.dataset.atmosphere")
    await page.locator('#dream-inspect-toggle').click()
    await page.get_by_role('button',name='Use classic presentation',exact=True).click()
    assert await page.locator('.split').evaluate('(e)=>getComputedStyle(e).backgroundImage')=='none'
    await expect(page.locator('#input')).to_have_value('Keep the eclipse atmosphere in the workspace')
    assert prompts==['Build a quiet place for ideas to become real.']
    assert errors==[]
