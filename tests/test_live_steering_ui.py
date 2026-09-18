"""Shipped composer steering control, with an isolated model-free Studio."""
import pytest
from playwright.async_api import expect
from dream.core.backends.base import Event
from test_desktop_companion import studio  # noqa: F401


@pytest.mark.asyncio
async def test_explicit_steer_queue_and_failure_draft_retry(studio, tmp_path):
    server, page, url, prompts, errors = studio
    corrections = []
    fail = True
    async def steer(text, identifier, target):
        corrections.append((text, identifier, target))
        if fail:
            raise ValueError('Turn is preparing; keep the correction.')
        return {'id': identifier, 'status': 'queued_after_turn'}
    server._on_steer = steer
    await page.goto(url)
    await expect(page.locator('#stat')).to_have_text('Ready')
    await expect(page.locator('#steer')).to_be_hidden()
    server.bus.publish(Event('turn_start', {}))
    server.bus.publish(Event('steering_ready', {'session_id': 'original', 'turn': 1}))
    await expect(page.locator('#steer')).to_be_visible()
    await expect(page.locator('#send')).to_have_text('Queue')
    await page.locator('#input').fill('Use a blue sky.')
    await page.locator('#steer').click()
    await expect(page.locator('#stream')).to_contain_text('Turn is preparing; keep the correction.')
    await expect(page.locator('#input')).to_have_value('Use a blue sky.')
    fail = False
    server.bus.publish(Event('steering_ready', {'session_id': 'replacement', 'turn': 2}))
    await page.wait_for_function("steeringTarget?.session_id === 'replacement'")
    await page.locator('#steer').click()
    await expect(page.locator('#input')).to_have_value('')
    await expect(page.locator('#stream')).to_contain_text('does not support live steering')
    assert corrections[0] == corrections[1]
    assert corrections[1][2] == {'session_id': 'original', 'turn': 1}
    assert not prompts
    await page.locator('#input').fill('Next task.')
    await page.locator('#send').click()
    await expect(page.locator('#input')).to_have_value('')
    assert prompts == ['Next task.']
    for width in (390, 720, 1280):
        await page.set_viewport_size({'width': width, 'height': 800})
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await expect(page.locator('#steer')).to_be_visible()
    await page.screenshot(path=str(tmp_path / 'steering-control.png'))
    server.bus.publish(Event('turn_end', {'interrupted': True}))
    await expect(page.locator('#steer')).to_be_hidden()
    assert not errors
