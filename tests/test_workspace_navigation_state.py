"""Visible navigation and classic handlers share the actual destination."""
import re
from playwright.async_api import expect
from test_desktop_chat import chat
from test_workspace_library_ui import library


async def test_navigation_selection_tracks_full_pages_and_preserves_draft(library):
    _, page, prompts, _, _ = library
    await page.locator('#input').fill('Keep this draft')
    ids = {'chat': 'studio-interactions', 'studio': 'studio-preview',
           'projects': 'studio-project', 'skills': 'studio-skills'}
    for view, identifier in ids.items():
        await page.locator('#dream-nav-' + view).click()
        await expect(page.locator('.studio-nav .selected')).to_have_count(1)
        await expect(page.locator('#' + identifier)).to_have_class(re.compile(r'\bselected\b'))
        await expect(page.locator('#' + identifier)).to_have_attribute('aria-current', 'page')
        await expect(page.locator('#dream-nav [aria-current="page"]')).to_have_count(1)
        await expect(page.locator('#dream-nav-' + view)).to_have_attribute('aria-current', 'page')
    await expect(page.locator('#studio-project')).to_be_hidden()
    await expect(page.locator('#studio-skills')).to_be_hidden()
    await page.locator('#dream-nav-home').click()
    await expect(page.locator('.studio-nav .selected')).to_have_count(0)
    await expect(page.locator('.studio-nav [aria-current]')).to_have_count(0)
    await page.locator('#dream-nav-chat').click()
    await expect(page.locator('#input')).to_have_value('Keep this draft')
    await page.keyboard.press('Escape')
    await expect(page.locator('#studio-interactions')).to_have_class(re.compile(r'\bselected\b'))
    await expect(page.locator('.studio-nav .selected')).to_have_count(1)
    await expect(page.locator('#studio-interactions')).to_have_attribute('aria-current', 'page')
    assert prompts == []


async def test_classic_library_selection_and_drawer_close_agree(library):
    _, page, prompts, _, _ = library
    await page.locator('#dream-inspect-toggle').click()
    await page.get_by_role('button', name='Use classic presentation', exact=True).click()
    for name, identifier in [('skills', 'studio-skills'), ('projects', 'studio-project')]:
        await page.locator('#' + identifier).click()
        await expect(page.locator('#dream-' + name + '-page')).to_be_visible()
        await expect(page.locator('#' + identifier)).to_have_attribute('aria-current', 'page')
        await expect(page.locator('.studio-nav .selected')).to_have_count(1)
    await page.locator('#studio-interactions').click()
    await expect(page.locator('#studio-interactions')).to_have_attribute('aria-current', 'page')
    await page.keyboard.press('Escape')
    await expect(page.locator('#studio-preview')).to_have_attribute('aria-current', 'page')
    assert prompts == []
