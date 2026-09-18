"""Theme layout checks using real Chromium and the existing model-free fixtures."""
from playwright.async_api import expect
from test_desktop_chat import chat
from test_workspace_library_ui import library
from test_studio_controls import controls, open_controls, no_overflow


async def test_skill_actions_do_not_cover_the_editor(library):
    _, page, prompts, _, writes = library
    await page.locator('#dream-nav-skills').click()
    await page.locator('#dream-skills-page .library-list button').first.click()
    editor = page.get_by_label('Skill Markdown', exact=True)
    await editor.fill('A draft that stays available at every window size.')
    for width, height in [(390, 844), (720, 520), (1280, 800)]:
        await page.set_viewport_size({'width': width, 'height': height})
        await editor.scroll_into_view_if_needed()
        field = await editor.bounding_box()
        actions = await page.locator('#dream-skills-page .library-savebar').bounding_box()
        assert actions['y'] >= field['y'] + field['height']
        await page.get_by_role('button', name='Save skill', exact=True).scroll_into_view_if_needed()
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.screenshot(path=f'/tmp/dream-polish-editor-{width}.png')
        await page.locator('#dream-skills-page').evaluate('(e)=>e.scrollTop=0')
        await page.screenshot(path=f'/tmp/dream-polish-skills-{width}.png')
    await page.get_by_role('button', name='Save skill', exact=True).click()
    await expect(page.locator('#dream-skills-page .library-status')).to_contain_text('Skill saved')
    assert writes[-1]['content'] == 'A draft that stays available at every window size.'
    assert prompts == []


async def test_controls_fit_narrow_windows_with_readable_tabs(controls):
    _, page, api, url, errors = controls
    await open_controls(page, url)
    for width, height in [(390, 844), (720, 520), (1280, 800)]:
        await page.set_viewport_size({'width': width, 'height': height})
        for tab in ['Runtime', 'Extensions', 'Learn']:
            await page.get_by_role('tab', name=tab, exact=True).click()
            await no_overflow(page)
            await page.locator('.dc-scroll').evaluate('(e)=>e.scrollTop=0')
            await page.screenshot(path=f'/tmp/dream-polish-controls-{tab.lower()}-{width}.png')
    assert errors == []
    assert not any(call.get('action') not in ['permission_mode_status'] for call in api.calls)
