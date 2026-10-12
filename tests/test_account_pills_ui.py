"""Account pills use verified account states, never Council membership or launch receipts."""
import copy

import pytest
from playwright.async_api import async_playwright, expect

from dream.core.provider_login import LoginRequests
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from test_council_ui import CouncilAPI

FRONTIERS = [('anthropic', 'Claude'), ('grok', 'Grok'), ('antigravity', 'Gemini'), ('codex', 'Codex')]


@pytest.fixture
async def accounts_page(tmp_path, monkeypatch):
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    server = StudioServer(EventBus(), session={'session_id': 'accounts-fixture', 'workspace': str(tmp_path), 'provider': 'fixture', 'model': 'fixture'})
    url = await server.start()
    council = CouncilAPI()
    requests = LoginRequests(True)
    account = {'fail': False, 'reads': [], 'accounts': [
        {'provider': key, 'status': 'signed_out', 'detail': 'Sign in is required.'} for key, _ in FRONTIERS
    ]}
    async def control(route):
        payload = route.request.post_data_json
        if payload['action'] in ('provider_login', 'provider_login_status'):
            council.login_calls.append(payload)
            result = requests.control(payload)
            if payload['action'] == 'provider_login':
                requests.ack(result['id'], result['provider'], 'launched')
                result = requests.status(result['id'])['request']
            await route.fulfill(json={'ok': True, 'result': result})
        else:
            await council.route(route)
    async def account_route(route):
        account['reads'].append(route.request.headers)
        await route.fulfill(status=503 if account['fail'] else 200, json={'accounts': copy.deepcopy(account['accounts'])})
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': 1440, 'height': 1100}, reduced_motion='reduce')
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.set_default_timeout(4000)
            await page.route('**/api/control', control)
            await page.route('**/api/accounts', account_route)
            await page.route('**/api/deepsleep', lambda route: route.fulfill(json={'nodes': [], 'links': [], 'receipts': [], 'providers': []}))
            await page.goto(url.replace('/#', '/?companion=1#'))
            await expect(page.locator('#dream-frontier-codex')).to_have_attribute('data-account-status', 'signed_out')
            yield page, council, requests, account, errors
            await browser.close()
    finally:
        await server.stop()


async def test_pills_are_spaced_login_actions_not_roster_switches(accounts_page):
    page, council, requests, account, errors = accounts_page
    await expect(page.locator('#dream-frontiers .dream-frontier-pill')).to_have_count(4)
    positions = []
    for key, label in FRONTIERS:
        pill = page.locator('#dream-frontier-' + key)
        await expect(pill).to_have_attribute('aria-label', 'Sign in to ' + label + ' · signed out')
        assert await pill.get_attribute('aria-pressed') is None
        positions.append(await pill.bounding_box())
    assert all(b['y'] >= a['y'] + a['height'] + 8 for a, b in zip(positions, positions[1:]))
    await page.locator('#dream-frontier-antigravity').click()
    await expect(page.locator('#dream-frontier-feedback')).not_to_be_empty()
    assert next(c for c in council.login_calls if c['action'] == 'provider_login')['provider'] == 'antigravity'
    await expect(page.locator('#dream-frontier-antigravity')).to_have_attribute('data-account-status', 'signed_out')
    await expect(page.locator('#dream-council')).not_to_be_visible()
    assert not any(c['action'] == 'council_configure' for c in council.calls)
    assert account['reads'][0]['x-dream-token']
    assert not errors


async def test_only_verified_accounts_turn_green_and_identity_stays_in_tooltip(accounts_page):
    page, council, _, account, errors = accounts_page
    account['accounts'][0].update(status='signed_in', username='Research <user>', detail='Account status verified.')
    account['accounts'][1].update(status='unknown', username='Do not show', detail='No account checker available.')
    await page.evaluate('DreamFrontiers.refresh()')
    await expect(page.locator('#dream-frontier-anthropic')).to_have_attribute('data-account-status', 'signed_in')
    await expect(page.locator('#dream-account-anthropic')).to_be_hidden()
    assert 'Research <user>' in await page.locator('#dream-frontier-anthropic').get_attribute('title')
    assert await page.locator('#dream-account-anthropic user').count() == 0
    await expect(page.locator('#dream-account-grok')).to_be_hidden()
    assert await page.evaluate("DreamFrontiers.state().controls.anthropic.signed_in") is True
    account['fail'] = True
    await page.evaluate('DreamFrontiers.refresh()')
    await expect(page.locator('#dream-frontier-anthropic')).to_have_attribute('data-account-status', 'unknown')
    await expect(page.locator('#dream-account-anthropic')).to_be_hidden()
    await expect(page.locator('#dream-frontier-anthropic')).to_be_enabled()
    assert not errors


async def test_keyboard_login_and_deepsleep_navigation(accounts_page):
    page, council, _, account, errors = accounts_page
    await page.locator('#dream-frontier-codex').focus()
    await page.keyboard.press('Enter')
    await expect(page.locator('#dream-frontier-feedback')).not_to_be_empty()
    assert next(c for c in council.login_calls if c['action'] == 'provider_login')['provider'] == 'codex'
    await page.locator('#dream-nav-deepsleep').click()
    await expect(page.locator('html')).to_have_attribute('data-dream-view', 'deepsleep')
    await page.locator('#dream-nav-chat').click()
    await expect(page.locator('#input')).to_have_attribute('placeholder', 'Type / for skills and commands — or ask Dream…')
    assert not errors


@pytest.mark.parametrize('width,height', [(1440, 1100), (390, 844)])
async def test_account_pill_rendering_and_viewport(accounts_page, width, height):
    page, _, _, account, errors = accounts_page
    account['accounts'][0].update(status='signed_in', username='Researcher', detail='Verified account.')
    await page.evaluate('DreamFrontiers.refresh()')
    await page.set_viewport_size({'width': width, 'height': height})
    await page.locator('#dream-frontier-anthropic').scroll_into_view_if_needed()
    await expect(page.locator('#dream-frontier-anthropic')).to_be_visible()
    await page.screenshot(path=f'/tmp/dream-account-pills-{width}.png')
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors
