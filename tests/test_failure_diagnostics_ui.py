import json

from playwright.async_api import expect
from dream.core.backends.base import Event
from dream.telemetry.turn import TurnTiming
from test_studio_controls import controls  # noqa: F401


async def test_failure_guidance_and_download_only_export_fixed_counts(controls):
    server, page, api, url, errors = controls
    timing = TurnTiming()
    timing.observe_tool('tool_result', {'is_error': True,
        'content': 'error while loading shared libraries: PRIVATE_PATH'})
    data = timing.finish('error')
    data['failure_diagnostics']['counts']['PRIVATE_TOOL'] = 4
    data['failure_diagnostics']['raw_error'] = 'PRIVATE_TOKEN'
    await page.goto(url)
    await expect(page.locator('#stat')).to_have_text('Ready')
    server.bus.publish(Event('turn_timing', data))
    card = page.locator('.turn-timing')
    await card.locator(':scope > summary').click()
    await expect(card).to_contain_text('Tool or library dependency')
    await expect(card).to_contain_text('same execution environment')
    await expect(card).to_contain_text('not a confirmed root cause')
    async with page.expect_download() as download:
        await card.get_by_role('button', name='Download failure diagnostics').click()
    result = await download.value
    assert result.suggested_filename == 'dream-failure-diagnostics.json'
    from pathlib import Path
    exported = json.loads(Path(await result.path()).read_text())
    assert exported['counts'] == {'dependency': 1}
    assert exported['root_cause_verified'] is False
    assert 'PRIVATE' not in json.dumps(exported)
    assert 'PRIVATE' not in await card.inner_text()
    assert api.calls == [{'action': 'permission_mode_status'}] and not errors
