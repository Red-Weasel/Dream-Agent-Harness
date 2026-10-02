"""After the P9 gate's second round (PASS on 51a4c0b): Auto-scroll survives a switch to Chat and back -- hidden, the
track measures 0 x 0, which is no verdict on what can scroll. (The Customize width flake is a retrying assertion in
test_nested_customize.py.)"""
from __future__ import annotations

import asyncio

import pytest
from playwright.async_api import expect

from test_nested_view import _activity, open_nested, studio  # noqa: F401

pytestmark = pytest.mark.asyncio
LEFT = 'document.getElementById("nd-track").scrollLeft'


async def test_auto_scroll_survives_a_view_switch(studio):
    async with studio() as s:
        page = await open_nested(s)
        for i in range(1, 12):
            s.srv.bus.publish(_activity(f'r-{i}', 'writer', 'status', status='running', text='Subagent started.'))
        await expect(page.locator('.nd-card[data-run-id]')).to_have_count(9)
        auto = page.locator('#nd-auto')
        await expect(auto).to_be_enabled()
        await auto.click()
        await expect(auto).to_have_attribute('aria-pressed', 'true')
        await s.page.wait_for_function(f'{LEFT} > 4')
        await s.page.locator('#dream-nav-chat').click()
        await expect(page).to_be_hidden()
        await asyncio.sleep(0.3)                                                   # the hidden track's resize is observed
        await s.page.locator('#dream-nav-nested').click()
        await expect(page).to_be_visible()
        await expect(auto).to_have_attribute('aria-pressed', 'true')             # still on
        before = await s.page.evaluate(LEFT)
        await s.page.wait_for_function(f'{LEFT} > {before} + 4')                   # and still moving
        assert s.errors == []
