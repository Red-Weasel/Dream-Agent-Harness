"""Budget displays use server snapshots and distinguish approval waiting."""
from playwright.async_api import expect
from test_studio_controls import controls, open_controls, no_overflow


async def test_budget_panel_reports_active_approval_and_wall_time(controls):
    _, page, api, url, errors = controls
    api.runtime['run'].update(active_elapsed_s=300, remaining_active_s=900,
        max_active_s=1200, elapsed_s=3900, approval_wait_s=3600,
        approval_pending=True, finished=False, max_wall_s=None, remaining_wall_s=None)
    await open_controls(page, url)
    panel = page.locator('#dc-run-budget')
    await expect(panel).to_contain_text('15m 0s')
    await expect(panel).to_contain_text('1h 0m 0s')
    await expect(panel).to_contain_text('Waiting for approval')
    await expect(panel).to_contain_text('not configured')
    api.runtime['run'].update(remaining_active_s=0, active_elapsed_s=1200,
        approval_pending=False, finished=True, max_wall_s=7200, remaining_wall_s=2400)
    await page.locator('#dc-refresh').click()
    await expect(panel).to_contain_text('Last turn')
    await expect(panel).to_contain_text('40m 0s')
    await expect(panel).not_to_contain_text('Waiting for approval')
    await page.set_viewport_size({'width':480,'height':800})
    await no_overflow(page)
    assert all(call == {'action':'permission_mode_status'} for call in api.calls)
    # The companion reads its mode on startup; budget inspection never mutates it.
    assert not errors


async def test_old_backend_does_not_invent_remaining_budget(controls):
    _, page, api, url, errors = controls
    await open_controls(page, url)
    await expect(page.locator('#dc-run-budget')).to_contain_text('not reported')
    await expect(page.locator('#dc-run-budget')).not_to_contain_text('0s remaining')
    assert not errors


async def test_unlimited_runtime_shows_overnight_usage_and_optional_wall_limit(controls):
    _, page, api, url, errors = controls
    api.runtime['run'].update(active_elapsed_s=43200, remaining_active_s=None,
        max_active_s=None, elapsed_s=43260, approval_wait_s=60,
        approval_pending=False, finished=False, max_wall_s=None, remaining_wall_s=None)
    await open_controls(page, url)
    panel = page.locator('#dc-run-budget')
    await expect(panel).to_contain_text('Unlimited')
    await expect(panel).to_contain_text('12h 0m 0s')
    await expect(panel).not_to_contain_text('not reported')
    api.runtime['run'].update(max_wall_s=86400, remaining_wall_s=43140)
    await page.locator('#dc-refresh').click()
    await expect(panel).to_contain_text('Unlimited')
    await expect(panel).to_contain_text('11h 59m 0s')
    await page.set_viewport_size({'width':480,'height':800})
    await no_overflow(page)
    assert not errors


async def test_missing_runtime_fields_are_not_unlimited(controls):
    _, page, api, url, errors = controls
    api.runtime['run'].update(remaining_active_s=None)
    api.runtime['run'].pop('max_active_s', None)
    await open_controls(page, url)
    panel = page.locator('#dc-run-budget')
    await expect(panel).to_contain_text('not reported')
    await expect(panel).not_to_contain_text('Unlimited')
    assert not errors
