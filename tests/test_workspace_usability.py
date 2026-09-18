"""Catalog and presentation behavior with synthetic data and real Chromium."""
import asyncio
import re

from playwright.async_api import expect
from test_desktop_chat import chat
from test_workspace_library_ui import library


async def test_presentation_preferences_survive_reload_and_classic(library):
    _, page, prompts, _, _ = library
    await page.locator('#input').fill('Keep my draft through presentation changes')
    await page.locator('#dream-inspect-toggle').click()
    await page.get_by_label('Workspace spacing').select_option('compact')
    await page.locator('#dream-banners-toggle').click()
    await page.get_by_role('button', name='Quiet / atmospheric', exact=True).click()
    await page.get_by_role('button', name='Close context', exact=True).click()
    await page.locator('#dream-nav-skills').click()
    await expect(page.locator('html')).to_have_attribute('data-banners', 'collapsed')
    assert (await page.locator('.library-heading').first.bounding_box())['height'] < 130
    await page.reload()
    await expect(page.locator('html')).to_have_attribute('data-density', 'compact')
    await expect(page.locator('html')).to_have_attribute('data-banners', 'collapsed')
    await expect(page.locator('html')).to_have_attribute('data-atmosphere', 'quiet')
    await expect(page.locator('#input')).to_have_value('Keep my draft through presentation changes')
    await page.locator('#dream-inspect-toggle').click()
    await page.get_by_role('button', name='Use classic presentation', exact=True).click()
    await expect(page.locator('#studio-skills')).to_be_visible()
    await page.locator('#studio-skills').click()
    await expect(page.locator('#dream-skills-page')).to_be_visible()
    await page.locator('#dream-restore-design').click()
    await expect(page.locator('#studio-skills')).to_be_hidden()
    await page.locator('#dream-inspect-toggle').click()
    await expect(page.get_by_label('Workspace spacing')).to_have_value('compact')
    await expect(page.locator('#dream-banners-toggle')).to_have_attribute('aria-expanded', 'false')
    assert prompts == []


async def test_skills_filters_sort_keyboard_and_draft(library):
    _, page, prompts, _, _ = library
    rows = [
        {'name':'zebra','description':'Animation tools','managed':False,'enabled':False},
        {'name':'writing','description':'Write clearly','managed':False,'enabled':True},
        {'name':'alpha','description':'Animation notes','managed':True,'enabled':True},
    ]
    await page.route('**/api/skills', lambda r:r.fulfill(json={'skills':rows}))
    await page.locator('#dream-nav-skills').click()
    names = page.locator('#dream-skills-page .library-list strong')
    await expect(names).to_have_text(['alpha','writing','zebra'])
    await page.get_by_label('Sort skills').select_option('reverse')
    await expect(names).to_have_text(['zebra','writing','alpha'])
    await page.get_by_label('Filter skills').select_option('managed')
    await expect(names).to_have_text(['alpha'])
    await page.get_by_label('Filter skills').select_option('disabled')
    await expect(names).to_have_text(['zebra'])
    await page.get_by_label('Filter skills').select_option('enabled')
    await expect(names).to_have_text(['writing','alpha'])
    await page.get_by_label('Filter skills').select_option('all')
    await page.get_by_label('Search skills', exact=True).fill('animation')
    await expect(names).to_have_text(['zebra','alpha'])
    await page.get_by_label('Search skills', exact=True).fill('')
    await page.keyboard.press('ArrowDown')
    await expect(page.locator('[data-key=zebra]')).to_be_focused()
    await page.keyboard.press('End')
    await expect(page.locator('[data-key=alpha]')).to_be_focused()
    await page.keyboard.press('Home')
    await page.keyboard.press('ArrowDown')
    await expect(page.locator('[data-key=writing]')).to_be_focused()
    await expect(page.locator('#dream-skills-page .library-detail')).to_contain_text('Instructions you can shape')
    await page.keyboard.press('Enter')
    editor=page.get_by_label('Skill Markdown',exact=True)
    await expect(editor).to_have_value(re.compile('Full instructions'))
    await editor.fill('Unsaved catalog draft')
    await page.get_by_label('Filter skills').select_option('managed')
    await page.get_by_label('Sort skills').select_option('name')
    await expect(editor).to_have_value('Unsaved catalog draft')
    await page.locator('[data-key=alpha]').click()
    await expect(page.locator('#dream-skills-page .library-status')).to_contain_text('Save this draft')
    await expect(editor).to_have_value('Unsaved catalog draft')
    await page.get_by_label('Search skills', exact=True).fill('unmatched')
    await expect(page.locator('#dream-skills-page .library-count')).to_have_text('0 of 3 skills')
    assert prompts == []


async def test_project_catalog_and_responsive_compact_screens(library):
    _, page, prompts, controls, _ = library
    rows=[{'id':'zebra','name':'Zebra','workspace':'/projects/z','session_count':4},
          {'id':'alpha','name':'Alpha','workspace':'/projects/alpha','session_count':1},
          {'id':'empty','name':'Empty','workspace':'/projects/empty','session_count':0}]
    await page.route('**/api/projects',lambda r:r.fulfill(json={'projects':rows}))
    await page.evaluate("window.DREAM_SESSION.workspace='/projects/alpha'")
    await page.locator('#dream-nav-projects').click()
    names=page.locator('#dream-projects-page .library-list strong')
    await expect(names).to_have_text(['Alpha','Empty','Zebra'])
    await page.get_by_label('Sort projects').select_option('conversations')
    await expect(names).to_have_text(['Zebra','Alpha','Empty'])
    await page.get_by_label('Filter projects').select_option('conversations')
    await expect(names).to_have_text(['Zebra','Alpha'])
    await page.get_by_label('Filter projects').select_option('current')
    await expect(names).to_have_text(['Alpha'])
    await page.get_by_label('Search projects',exact=True).focus()
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('Enter')
    await expect(page.locator('#dream-projects-page .library-detail > .library-editor > h2')).to_have_text('Alpha')
    await page.locator('#dream-inspect-toggle').click()
    await page.get_by_label('Workspace spacing').select_option('compact')
    await page.locator('#dream-banners-toggle').click()
    await page.get_by_role('button',name='Close context',exact=True).click()
    for width,height in [(390,844),(720,520),(1280,800)]:
        await page.set_viewport_size({'width':width,'height':height})
        await page.locator('#dream-projects-page').evaluate('(e)=>e.scrollTop=0')
        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        await expect(page.locator('#studio-project')).to_be_hidden()
        await expect(page.locator('#dream-nav-projects')).to_be_visible()
        if width <= 900:
            await page.locator('#dream-workspace-tools > summary').click()
            await expect(page.locator('#studio-new')).to_be_visible()
            await page.locator('#studio-new').focus()
            await page.keyboard.press('Escape')
            await expect(page.locator('#dream-workspace-tools > summary')).to_be_focused()
            await expect(page.locator('#studio-new')).to_be_hidden()
        await expect(page.get_by_label('Filter projects')).to_be_visible()
        await page.screenshot(path=f'/tmp/dream-usability-projects-{width}.png')
    assert prompts == []
    assert not any(c.get('action')=='project_open' for c in controls)


async def test_compact_mobile_approval_is_visible(library):
    srv,page,_,_,_=library
    await page.set_viewport_size({'width':390,'height':844})
    await page.locator('#dream-inspect-toggle').click()
    await page.get_by_label('Workspace spacing').select_option('compact')
    await page.get_by_role('button',name='Close context',exact=True).click()
    await page.locator('#dream-nav-projects').click()
    pending=asyncio.create_task(srv.request_permission('run_bash', {'command':'pwd'}, 'Fixture approval', {'y':'Allow once','n':'Deny'}))
    await expect(page.locator('.permission-card')).to_be_visible()
    await expect(page.locator('#dream-approval-return')).to_be_visible()
    await expect(page.locator('#dream-projects-page')).to_be_hidden()
    await page.get_by_role('button',name='Deny',exact=True).click()
    assert await pending == 'n'
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
