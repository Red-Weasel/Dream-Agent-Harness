"""Shipped Studio navigation over controlled HTTP fixtures, no provider calls.

Root runs these browser tests; authoring this file does not execute Chromium.
"""
import asyncio

import pytest
from playwright.async_api import async_playwright, expect

from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from test_council_ui import CouncilAPI


@pytest.fixture
async def navigation(tmp_path, monkeypatch):
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    server = StudioServer(EventBus(), session={'session_id': 'navigation-fixture',
        'workspace': str(tmp_path), 'provider': 'fixture', 'model': 'main-model'})
    url = await server.start()
    api = CouncilAPI()
    settings_reads = []
    async def control(route):
        payload = route.request.post_data_json
        if payload['action'] == 'settings_get':
            settings_reads.append(payload)
            await route.fulfill(json={'ok': True, 'result': {
                'ok': True, 'provider': 'fixture', 'model': 'main-model', 'file': '/fixture/settings.json',
                'sections': [{'id': 'fixture', 'title': 'Fixture settings', 'intro': 'Controlled settings view', 'rows': []}]}})
        else:
            await api.route(route)
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': 1280, 'height': 940}, reduced_motion='reduce')
            page.set_default_timeout(5000)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.route('**/api/control', control)
            await page.route('**/api/runtime', lambda route: route.fulfill(json={
                'provider': 'fixture', 'model': 'main-model', 'profile': {'name': 'fixture'},
                'workspace': str(tmp_path), 'performance': {'supported': False}}))
            await page.route('**/api/extensions', lambda route: route.fulfill(json={
                'extensions': [], 'counts': {}, 'warnings': []}))
            await page.goto(url.replace('/#', '/?companion=1#'))
            try:
                yield page, api, settings_reads, errors
            finally:
                for gate in api.pause.values():
                    gate.set()
                await browser.close()
    finally:
        await server.stop()


@pytest.mark.parametrize('previous', ['Runtime', 'Extensions'])
async def test_sidebar_settings_selects_settings_after_other_controls_tab(navigation, previous):
    page, api, reads, errors = navigation
    sidebar = page.locator('#dream-nav-settings')
    await sidebar.click()
    await page.get_by_role('tab', name=previous, exact=True).click()
    await expect(page.get_by_role('tab', name=previous, exact=True)).to_have_attribute('aria-selected', 'true')
    await page.get_by_role('button', name='Close controls', exact=True).click()
    await sidebar.focus()
    await page.keyboard.press('Enter')
    await expect(page.get_by_role('tab', name='Settings', exact=True)).to_have_attribute('aria-selected', 'true')
    await expect(page.locator('#dc-settings').get_by_role('heading', name='Fixture settings', exact=True)).to_be_visible()
    assert reads and not any(call['action'] == 'council_configure' for call in api.calls)
    await page.keyboard.press('Escape')
    await expect(page.locator('#dream-controls')).not_to_be_visible()
    await expect(sidebar).to_be_focused()
    assert not errors


@pytest.mark.parametrize('view', ['chat', 'nested'])
@pytest.mark.parametrize('state', ['busy', 'unavailable'])
async def test_sidebar_council_remains_accessible_in_active_or_disconnected_session(navigation, view, state):
    page, api, _, errors = navigation
    api.status.update(busy=state == 'busy', main_available=state != 'unavailable')
    await page.locator('#dream-nav-' + view).click()
    sidebar = page.locator('#dream-nav-council')
    await expect(sidebar).to_be_visible()
    await expect(sidebar).to_be_enabled()
    await sidebar.focus()
    await page.keyboard.press('Enter')
    await expect(page.locator('#dream-council')).to_be_visible()
    await expect(page.locator('html')).to_have_attribute('data-dream-view', view)
    await expect(page.get_by_label('Main provider')).to_have_value('openai')
    await expect(page.locator('#council-session-state')).to_contain_text('active' if state == 'busy' else 'disconnected')
    await page.keyboard.press('Escape')
    await expect(page.locator('#dream-council')).not_to_be_visible()
    await expect(sidebar).to_be_focused()
    await expect(page.locator('html')).to_have_attribute('data-dream-view', view)
    assert not any(call['action'] in ('council_ask', 'council_work', 'council_configure', 'interrupt') for call in api.calls)
    assert not errors


async def test_paused_configuration_can_close_reopen_and_retains_draft(navigation):
    page, api, _, errors = navigation
    sidebar = page.locator('#dream-nav-council')
    await sidebar.click()
    await expect(page.get_by_label('Main provider')).to_have_value('openai')
    await page.get_by_label('Main model', exact=True).select_option('retained-draft')
    await page.get_by_label('Question for advisors').fill('Keep this useful task draft')
    gate = asyncio.Event()
    api.pause['council_configure'] = gate
    await page.get_by_role('button', name='Apply Council', exact=True).click()
    try:
        await expect(page.locator('#council-session-state')).to_contain_text('Applying')
        await page.get_by_role('button', name='Close Council', exact=True).click()
        await expect(sidebar).to_be_focused()
        await sidebar.click()
        await expect(page.locator('#dream-council')).to_be_visible()
        await expect(page.locator('html')).to_have_attribute('data-dream-view', 'chat')
        await expect(page.get_by_label('Main model', exact=True)).to_have_value('retained-draft')
        await expect(page.get_by_label('Question for advisors')).to_have_value('Keep this useful task draft')
        assert len([call for call in api.calls if call['action'] == 'council_configure']) == 1
        assert not any(call['action'] in ('council_ask', 'council_work') for call in api.calls)
    finally:
        gate.set()
    await expect(page.locator('#council-feedback')).to_contain_text('applied')
    await expect(page.get_by_label('Main model', exact=True)).to_have_value('retained-draft')
    assert not errors
