"""Phone navigation remains reachable without widening the document."""
from playwright.async_api import expect

from test_session_navigation_ui import navigation


async def test_phone_can_reach_settings_and_council_without_page_overflow(navigation):
    page, api, reads, errors = navigation
    await page.set_viewport_size({'width': 390, 'height': 844})
    await page.locator('#dream-nav-settings').click()
    await expect(page.get_by_role('tab', name='Settings', exact=True)).to_have_attribute('aria-selected', 'true')
    await expect(page.locator('#dc-settings')).to_be_visible()
    await page.keyboard.press('Escape')
    await expect(page.locator('#dream-nav-settings')).to_be_focused()
    await page.locator('#dream-nav-council').click()
    await expect(page.locator('#dream-council')).to_be_visible()
    await page.screenshot(path='/tmp/dream-antigravity-navigation-mobile-20261006.png')
    await page.keyboard.press('Escape')
    await expect(page.locator('#dream-nav-council')).to_be_focused()
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert reads
    assert not any(call['action'] in ('council_configure', 'council_ask', 'council_work') for call in api.calls)
    assert not errors
