"""The active server's identity is visible without fabricating older-server data."""
from playwright.async_api import expect
from test_studio_controls import controls, open_controls, no_overflow


async def test_runtime_identity_exposes_restart_reason_and_escapes_text(controls):
    _, page, api, url, errors = controls
    api.runtime['runtime_identity'] = {
        'version': '0.1.0', 'pid': 123, 'imported_at': '2026-09-13T14:00:00Z',
        'startup_source_id': 'abc123', 'source_changed': True,
        'restart_guidance': 'Restart Dream when convenient to load changed Python source.',
        'scope': '<img src=x onerror=alert(1)> Selected core files only.'}
    await open_controls(page, url)
    panel = page.locator('#dc-runtime-build')
    await panel.locator('summary').click()
    await expect(panel).to_contain_text('0.1.0')
    await expect(panel).to_contain_text('abc123')
    await expect(panel).to_contain_text('Restart Dream')
    await expect(panel.locator('img')).to_have_count(0)
    await page.set_viewport_size({'width': 390, 'height': 844})
    await no_overflow(page)
    assert not errors


async def test_old_server_identity_stays_unavailable(controls):
    _, page, api, url, errors = controls
    api.runtime.pop('runtime_identity', None)
    await open_controls(page, url)
    panel = page.locator('#dc-runtime-build')
    await panel.locator('summary').click()
    await expect(panel).to_contain_text('Not reported')
    await expect(panel).to_contain_text('does not report')
    await expect(panel).not_to_contain_text('0.1.0')
    assert not errors
