"""Real browser permission shortcuts through the authenticated runtime API."""
import asyncio

import pytest
from playwright.async_api import expect

from dream.tui.app import App
from test_desktop_chat import chat


@pytest.fixture
async def modes(chat):
    srv, page, _, _ = chat
    app = object.__new__(App)
    app.mode = 'ask'
    srv._on_control = app._runtime_control
    await page.reload()
    await expect(page.locator('#stat')).to_have_text('Ready')
    return app, srv, page


async def test_shift_tab_cycles_real_permission_mode(modes):
    app, _, page = modes
    for mode in ('accept-edits', 'auto', 'plan', 'ask'):
        await page.locator('#input').press('Shift+Tab')
        await expect(page.locator('#chat-mode')).to_have_attribute('data-mode', mode)
        assert app.mode == mode
    await page.locator('#input').press('Shift')
    await page.locator('#input').press('Tab')
    assert app.mode == 'ask'
    await page.locator('#input').fill('Preserved draft')
    await page.locator('#chat-mode').click()
    await expect(page.locator('#chat-mode')).to_have_attribute('data-mode', 'accept-edits')
    await expect(page.locator('#input')).to_have_value('Preserved draft')


async def test_shortcut_preserves_dialog_navigation_and_reports_rejection(modes):
    app, srv, page = modes
    await page.locator('#dream-command-open').click()
    await page.locator('#dream-command-search').press('Shift+Tab')
    assert app.mode == 'ask'
    await page.locator('#dream-palette').get_by_role('button',name='Close',exact=True).click()
    async def rejected(payload):
        if payload['action'] == 'permission_mode':
            raise ValueError('Wait until the current turn finishes')
        return await app._runtime_control(payload)
    srv._on_control = rejected
    await page.locator('#input').press('Shift+Tab')
    await expect(page.locator('#studio-error')).to_contain_text('Wait until the current turn finishes')
    await expect(page.locator('#chat-mode')).to_have_text('Mode unconfirmed')
    assert app.mode == 'ask'


async def test_runtime_cycles_during_work_like_terminal():
    app = object.__new__(App)
    app.mode = 'ask'
    app._interrupt_target = asyncio.create_task(asyncio.sleep(60))
    try:
        result = await app._runtime_control({'action': 'permission_mode'})
        assert result['mode'] == app.mode == 'accept-edits'
        status = await app._runtime_control({'action': 'permission_mode_status'})
        assert status == result
    finally:
        app._interrupt_target.cancel()
        await asyncio.gather(app._interrupt_target, return_exceptions=True)


async def test_pending_shortcut_does_not_queue_extra_mode_changes(modes):
    app, srv, page = modes
    await expect(page.locator('#chat-mode')).to_have_attribute('data-mode', 'ask')
    entered, release = asyncio.Event(), asyncio.Event()
    cycles = []
    async def delayed(payload):
        if payload['action'] == 'permission_mode':
            cycles.append(payload)
            entered.set()
            await release.wait()
        return await app._runtime_control(payload)
    srv._on_control = delayed
    try:
        await page.locator('#input').press('Shift+Tab')
        await asyncio.wait_for(entered.wait(), timeout=3)
        await page.locator('#input').press('Shift+Tab')
        await expect(page.locator('#chat-mode')).to_be_disabled()
        assert app.mode == 'ask'
    finally:
        release.set()
    await expect(page.locator('#chat-mode')).to_have_attribute('data-mode', 'accept-edits')
    assert len(cycles) == 1


async def test_lost_acknowledgment_does_not_claim_old_mode_or_retry(modes):
    app, srv, page = modes
    await expect(page.locator('#chat-mode')).to_have_attribute('data-mode', 'ask')
    calls = []
    async def lost_ack(payload):
        calls.append(payload)
        result = await app._runtime_control(payload)
        if payload['action'] == 'permission_mode':
            raise OSError('Connection lost after changing mode')
        return result
    srv._on_control = lost_ack
    await page.locator('#input').press('Shift+Tab')
    await expect(page.locator('#chat-mode')).to_have_text('Mode unconfirmed')
    assert await page.locator('#chat-mode').get_attribute('data-mode') is None
    assert app.mode == 'accept-edits'
    assert calls == [{'action': 'permission_mode'}]
