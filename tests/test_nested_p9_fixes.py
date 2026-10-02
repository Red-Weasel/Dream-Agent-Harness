"""The DREAM-196 gate's findings on a661ddb (gate-reports/p9-r1.md), each shown failing first: the pin-split edge is one
node that survives every render (a pointer drag keeps following while events arrive); pins persist after an unpin and
after a live auto-fill, replayed lanes never undo the owner's choice, and a saved id the replay never claims is
dropped; keyboard pin and unpin keep the focus; the orchestrator edge's ARIA comes from its real bounds; the arrows and
Auto-scroll are disabled while nothing can scroll; a capped response keeps the streamed words."""
from __future__ import annotations

import asyncio

import pytest
from playwright.async_api import expect

from dream.core.backends.base import Event
from test_nested_drawer import lanes
from test_nested_view import PAGE, _activity, open_nested, studio  # noqa: F401

pytestmark = pytest.mark.asyncio
SHARE = 'e => { const p = e.parentElement.closest(".nd-pinned").getBoundingClientRect(); return +(e.getBoundingClientRect().width / (p.width - 40) * 100).toFixed(1); }'
SAVED = "localStorage.getItem('dream.nested.pins.nested-test')"


async def reopen(s):
    await s.page.reload()
    await expect(s.page.locator('#stat')).to_have_text('Ready')
    return await open_nested(s)


async def test_the_pin_split_edge_is_one_node_and_a_drag_survives_a_render(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 5, {})
        await expect(page.locator('.nd-card[data-run-id]')).to_have_count(3)
        hp, win0 = page.locator('#nd-h-pin'), page.locator('.nd-win[data-slot="0"]')
        await hp.evaluate('e => { window.__hp = e; }')
        s0 = await win0.evaluate(SHARE)
        box = await hp.bounding_box()
        y = box['y'] + 100
        await s.page.mouse.move(box['x'] + 4, y)
        await s.page.mouse.down()
        await s.page.mouse.move(box['x'] + 4 - 60, y, steps=4)
        s1 = await win0.evaluate(SHARE)
        assert s1 < s0 - 2, (s0, s1)
        s.srv.bus.publish(_activity('r-2', 'writer', 'text_delta', request_index=1, text=' A re-render arrives mid-drag.'))
        await expect(page.locator('.nd-win[data-run-id="r-2"] .nd-stream')).to_contain_text('mid-drag')
        assert await s.page.evaluate("window.__hp === document.getElementById('nd-h-pin')")   # the same node
        await s.page.mouse.move(box['x'] + 4 - 160, y, steps=4)
        s2 = await win0.evaluate(SHARE)
        await s.page.mouse.up()
        assert s2 < s1 - 2, (s0, s1, s2)                                                    # still following
        assert abs(int(await hp.get_attribute('aria-valuenow')) - s2) <= 1, s2                # whole percents
        assert s.errors == []


async def test_pins_persist_after_an_unpin_a_live_auto_fill_and_a_replay_that_lacks_a_saved_run(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 3, {})
        await expect(page.locator('.nd-win[data-run-id]')).to_have_count(2)
        # an unpin, then a reload: the slot stays empty, the worker stays a card
        await page.locator('.nd-win[data-run-id="r-1"] [data-unpin]').click()
        await expect(page.locator('.nd-win-empty')).to_have_count(1)
        page = await reopen(s)
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(3)
        await expect(page.locator('.nd-win-empty')).to_have_count(1)
        await expect(page.locator('.nd-win[data-slot="1"]')).to_have_attribute('data-run-id', 'r-2')
        await expect(page.locator('.nd-card[data-run-id="r-1"]')).to_be_visible()
        # a new worker takes the empty slot live and that is kept too
        s.srv.bus.publish(_activity('r-9', 'writer', 'status', status='running', text='Subagent started.'))
        await expect(page.locator('.nd-win[data-slot="0"]')).to_have_attribute('data-run-id', 'r-9')
        assert await s.page.evaluate(SAVED) == '["r-9","r-2"]'
        page = await reopen(s)
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(4)
        await expect(page.locator('.nd-win[data-slot="0"]')).to_have_attribute('data-run-id', 'r-9')
        await expect(page.locator('.nd-win[data-slot="1"]')).to_have_attribute('data-run-id', 'r-2')
        # a reconnect (a history replay in the same page) keeps the owner's pins as well
        await page.locator('.nd-win[data-run-id="r-2"] [data-unpin]').click()
        await expect(page.locator('.nd-win-empty')).to_have_count(1)
        await s.page.evaluate("window.__replays = 0; addEventListener('dream:event', e => { if (e.detail.m.kind === 'history') window.__replays++; })")
        await s.page.evaluate('socket.close()')
        await expect(s.page.locator('#stat')).to_have_text('Ready')
        await s.page.wait_for_function('window.__replays >= 1')
        await expect(page.locator('.nd-win[data-slot="0"]')).to_have_attribute('data-run-id', 'r-9')
        await expect(page.locator('.nd-win-empty')).to_have_count(1)
        # a saved id the replay never claims is dropped and the slot is filled from the replayed workers
        await s.page.evaluate("localStorage.setItem('dream.nested.pins.nested-test', JSON.stringify(['ghost-run', null]))")
        page = await reopen(s)
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(4)
        await expect(page.locator('.nd-win[data-slot="0"]')).to_have_attribute('data-run-id', 'r-1')    # the ghost's window
        await expect(page.locator('.nd-win-empty')).to_have_count(1)                                       # the saved empty slot is the owner's
        assert 'ghost' not in str(await s.page.evaluate('window.DreamNested.pins()'))
        assert await s.page.evaluate(SAVED) == '["r-1",null]'
        s.srv.bus.publish(_activity('r-10', 'writer', 'status', status='running', text='Subagent started.'))
        await expect(page.locator('.nd-win[data-slot="1"]')).to_have_attribute('data-run-id', 'r-10')   # a live worker takes it
        await expect(page.locator('.nd-win[data-slot="0"]')).to_have_attribute('data-run-id', 'r-1')
        assert s.errors == []


async def test_keyboard_pin_and_unpin_keep_the_focus(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 4, {})
        await expect(page.locator('.nd-card[data-run-id]')).to_have_count(2)
        await page.locator('.nd-card[data-run-id="r-3"] [data-pin]').focus()
        await s.page.keyboard.press('Enter')
        await expect(page.locator('.nd-win[data-slot="1"]')).to_have_attribute('data-run-id', 'r-3')
        await expect(page.locator('.nd-win[data-run-id="r-3"] [data-unpin]')).to_be_focused()
        await s.page.keyboard.press('Enter')
        await expect(page.locator('.nd-win-empty')).to_have_count(1)
        await expect(page.locator('.nd-card[data-run-id="r-3"] [data-pin]')).to_be_focused()
        assert s.errors == []


@pytest.mark.parametrize('w,h', [(1600, 1000), (2554, 1338)])
async def test_the_orchestrator_edges_aria_follows_its_real_bounds(studio, w, h):
    async with studio(width=w, height=h) as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        await expect(page.locator('.nd-win[data-run-id]')).to_have_count(2)
        app_w = await page.locator('.nd-app').evaluate('e => e.getBoundingClientRect().width')
        h_orch = page.locator('#nd-h-orch')
        lo, hi = round(360 / app_w * 100), round((app_w - 488) / app_w * 100)
        await expect(h_orch).to_have_attribute('aria-valuemin', str(lo))
        await expect(h_orch).to_have_attribute('aria-valuemax', str(hi))
        await h_orch.focus()
        for _ in range(40):
            await s.page.keyboard.press('Shift+ArrowLeft')
        await expect(h_orch).to_have_attribute('aria-valuenow', str(lo))
        for _ in range(60):
            await s.page.keyboard.press('Shift+ArrowRight')
        await expect(h_orch).to_have_attribute('aria-valuenow', str(hi))
        assert s.errors == []


async def test_the_arrows_and_auto_scroll_are_disabled_while_nothing_can_scroll(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        await expect(page.locator('.nd-win[data-run-id]')).to_have_count(2)
        for sel in ('#nd-prev', '#nd-next', '#nd-auto'):
            await expect(page.locator(sel)).to_be_disabled()
        for i in range(3, 12):
            s.srv.bus.publish(_activity(f'r-{i}', 'writer', 'status', status='running', text='Subagent started.'))
        await expect(page.locator('.nd-card[data-run-id]')).to_have_count(9)
        for sel in ('#nd-prev', '#nd-next', '#nd-auto'):
            await expect(page.locator(sel)).to_be_enabled()
        await page.locator('#nd-auto').click()
        await expect(page.locator('#nd-auto')).to_have_attribute('aria-pressed', 'true')
        await page.locator('#nd-auto').click()
        await expect(page.locator('#nd-auto')).to_have_attribute('aria-pressed', 'false')
        assert s.errors == []


async def test_a_capped_response_keeps_the_streamed_words(studio):
    async with studio() as s:
        page = await open_nested(s)

        def row(kind, idx, **f):
            return _activity('r-s', 'researcher', kind, request_index=idx, **f)
        s.srv.bus.publish(row('status', None, status='running', text='Subagent started.'))
        s.srv.bus.publish(row('request', 1, status='awaiting_response'))
        s.srv.bus.publish(row('text_delta', 1, text='X' * 30))
        win = page.locator('.nd-win[data-run-id="r-s"]')
        await expect(win.locator('.nd-stream')).to_have_text('X' * 30)
        s.srv.bus.publish(row('response', 1, status='received', text='X' * 10 + '\n[Display truncated]'))   # the server's cap
        await asyncio.sleep(0.3)
        await expect(win.locator('.nd-stream')).to_have_text('X' * 30)
        s.srv.bus.publish(row('request', 2, status='awaiting_response'))
        s.srv.bus.publish(row('text_delta', 2, text='second round streamed'))
        s.srv.bus.publish(row('response', 2, status='received', text='second round, redacted'))                 # a real difference replaces
        await expect(win.locator('.nd-stream')).to_have_text('X' * 30 + '\n\nsecond round, redacted')
        assert s.errors == []
