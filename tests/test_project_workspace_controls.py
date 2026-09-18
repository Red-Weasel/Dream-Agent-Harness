"""Capability facts and explicit reconciliation against CPU browser fixtures."""
from playwright.async_api import expect
from test_studio_controls import READ_ONLY_STARTUP_CALLS, controls, open_controls  # noqa: F401


async def test_capabilities_unknown_and_explicit_request_reconciliation(controls):
    _, page, api, url, errors = controls
    api.runtime['capabilities'] = {'vision': {'known': True, 'value': False, 'source': 'fixture'},
        'tool_calling': {'known': True, 'value': True, 'source': 'fixture'}, 'configured': {'max_parallel': 4}, 'warnings': []}
    api.runtime['coordination'] = {'enabled': True, 'state': 'uncertain', 'request_id': 'fixture-request', 'waiting': 1, 'can_reconcile': True}
    calls = []
    async def reconcile(route):
        payload = route.request.post_data_json
        if payload["action"] == "permission_mode_status":
            await route.fallback()
            return
        assert payload["action"] == "reconcile_local_request"
        calls.append(payload)
        api.runtime['coordination'] = {'enabled': True, 'state': 'idle', 'can_reconcile': False}
        await route.fulfill(json={'ok': True, 'result': api.runtime['coordination']})
    await page.route('**/api/control', reconcile)
    await open_controls(page, url)
    await expect(page.locator('#dc-capability-facts')).to_contain_text('Unknown')
    await expect(page.locator('#dc-capability-facts')).to_contain_text('Unsupported')
    await expect(page.locator('#dc-coordination-status')).to_contain_text('uncertain')
    await expect(page.locator('#dc-reconcile-submit')).to_be_disabled()
    assert api.calls == READ_ONLY_STARTUP_CALLS
    assert not calls
    await page.locator('#dc-reconcile-confirm').check()
    await page.locator('#dc-reconcile-submit').click()
    await expect(page.locator('#dc-coordination-status')).to_contain_text('idle')
    assert api.calls == READ_ONLY_STARTUP_CALLS
    assert calls == [{'action': 'reconcile_local_request', 'request_id': 'fixture-request', 'confirmed_idle': True}]
    await expect(page.locator('#dc-reconcile-form')).to_be_hidden()
    assert not errors


async def test_reconcile_confirmation_does_not_carry_to_different_request(controls):
    _, page, api, url, errors = controls
    api.runtime['coordination'] = {'enabled': True, 'state': 'uncertain', 'request_id': 'first', 'can_reconcile': True}
    await open_controls(page, url)
    await page.locator('#dc-reconcile-confirm').check()
    api.runtime['coordination']['request_id'] = 'second'
    await page.get_by_role('button', name='Refresh controls').click()
    await expect(page.locator('#dc-coordination-status')).to_contain_text('second')
    await expect(page.locator('#dc-reconcile-confirm')).not_to_be_checked()
    await expect(page.locator('#dc-reconcile-submit')).to_be_disabled()
    assert api.calls == READ_ONLY_STARTUP_CALLS and not errors
