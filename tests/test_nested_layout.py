"""Nested Dream phase 9 (DREAM-196): resizable and movable panes (pointer and keyboard, ARIA separators, bounded), the
orchestrator's side, pinning by drag-and-drop and by button, the card conveyor (arrows, auto-scroll that pauses on
hover and focus, discrete steps under reduced motion), Reset layout, and a layout persisted under
localStorage['dream.nested.*'] that survives a reload and a storage that throws. No model, no engine, no Dream."""
from __future__ import annotations

import asyncio
import time

import pytest
from playwright.async_api import expect

from test_nested_drawer import lanes  # noqa: F401
from test_nested_view import PAGE, _activity, open_nested, shot, studio  # noqa: F401

pytestmark = pytest.mark.asyncio
KEYS = "Object.keys(localStorage).filter(k => k.startsWith('dream.nested.')).sort()"
# Storage that throws for the view's own keys only (the rest of the page keeps its own preferences).
POISON = """(() => {
  let real; try { real = window.localStorage; } catch (e) { return; }      // a sandboxed frame has none to poison
  const own = k => typeof k === 'string' && k.startsWith('dream.nested.');
  const proxy = new Proxy(real, {get(t, p) {
    if (['getItem', 'setItem', 'removeItem'].includes(p)) return (k, ...a) => { if (own(k)) throw new Error('storage disabled'); return t[p](k, ...a); };
    const v = t[p]; return typeof v === 'function' ? v.bind(t) : v; }});
  Object.defineProperty(window, 'localStorage', {get: () => proxy, configurable: true});
})();"""


async def width(page, selector):
    return await page.locator(selector).evaluate('e => e.getBoundingClientRect().width')


async def until(fn, predicate, timeout=5.0, every=0.05):
    deadline = time.monotonic() + timeout
    value = await fn()
    while not predicate(value):
        assert time.monotonic() < deadline, f'condition not met in {timeout}s; last value {value!r}'
        await asyncio.sleep(every)
        value = await fn()
    return value


async def test_sizes_and_side_persist_across_reload(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 3, {})
        orch, app = PAGE + ' .nd-orch', PAGE + ' .nd-app'
        await expect(page.locator('.nd-win[data-run-id]')).to_have_count(2)
        w0 = await width(s.page, orch)
        h = page.locator('#nd-h-orch')
        await expect(h).to_have_attribute('role', 'separator')
        await expect(h).to_have_attribute('aria-orientation', 'vertical')
        await h.focus()
        for _ in range(3):
            await s.page.keyboard.press('ArrowRight')             # +16 px each
        await s.page.keyboard.press('Shift+ArrowRight')            # +64 px
        assert abs(await width(s.page, orch) - (round(w0) + 112)) <= 2
        now, lo, hi = [int(await h.get_attribute(a)) for a in ('aria-valuenow', 'aria-valuemin', 'aria-valuemax')]
        assert lo <= now <= hi
        hp, ht = page.locator('#nd-h-pin'), page.locator('#nd-h-top')
        await expect(hp).to_have_attribute('aria-valuenow', '50')
        await expect(ht).to_have_attribute('aria-valuenow', '60')
        await hp.focus()
        await s.page.keyboard.press('ArrowRight')
        await s.page.keyboard.press('ArrowRight')
        await expect(hp).to_have_attribute('aria-valuenow', '54')
        await ht.focus()
        await s.page.keyboard.press('ArrowDown')
        await s.page.keyboard.press('ArrowDown')
        await expect(ht).to_have_attribute('aria-valuenow', '64')
        first, pinned = page.locator('.nd-win[data-slot="0"]'), page.locator('.nd-pinned')
        share = await first.evaluate('(e, p) => e.getBoundingClientRect().width / (p.getBoundingClientRect().width - 40)', await pinned.element_handle())
        assert 0.52 <= share <= 0.56, share
        await page.locator('#nd-side').click()                      # the orchestrator moves to the right
        await expect(page.locator('.nd-app')).to_have_attribute('data-side', 'right')
        left = await page.locator('.nd-orch').evaluate('e => e.getBoundingClientRect().left')
        assert left > await page.locator('.nd-main').evaluate('e => e.getBoundingClientRect().left')
        rows = await page.evaluate("""p => { const r = s => p.querySelector(s).getBoundingClientRect();
            const a = r('.nd-app'), o = r('.nd-orch'), m = r('.nd-main'); return {o: [o.top, o.bottom], m: [m.top, m.bottom], a: [a.top, a.bottom]}; }""")
        assert abs(rows['o'][0] - rows['m'][0]) <= 1 and abs(rows['m'][1] - rows['a'][1]) <= 1, rows   # side by side, full height
        assert await s.page.evaluate(KEYS) == ['dream.nested.orch-w', 'dream.nested.pin-split', 'dream.nested.pins.nested-test', 'dream.nested.side', 'dream.nested.top-h']
        await shot(s.page, 'nested-layout-right-1600.png')
        await s.page.reload()
        await expect(s.page.locator('#stat')).to_have_text('Ready')
        page = await open_nested(s)
        await expect(page.locator('.nd-win[data-run-id]')).to_have_count(2)
        assert abs(await width(s.page, orch) - (round(w0) + 112)) <= 2
        await expect(page.locator('#nd-h-pin')).to_have_attribute('aria-valuenow', '54')
        await expect(page.locator('#nd-h-top')).to_have_attribute('aria-valuenow', '64')
        await expect(page.locator('.nd-app')).to_have_attribute('data-side', 'right')
        assert s.errors == []


async def test_keyboard_and_pointer_resize_stay_in_bounds(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 3, {})
        await expect(page.locator('.nd-win[data-run-id]')).to_have_count(2)
        orch = PAGE + ' .nd-orch'
        app_w = await width(s.page, PAGE + ' .nd-app')
        h = page.locator('#nd-h-orch')
        await h.focus()
        for _ in range(40):
            await s.page.keyboard.press('Shift+ArrowLeft')
        assert 359 <= await width(s.page, orch) <= 361
        now, lo, hi = [int(await h.get_attribute(a)) for a in ('aria-valuenow', 'aria-valuemin', 'aria-valuemax')]
        assert lo <= now <= hi
        for _ in range(40):
            await s.page.keyboard.press('Shift+ArrowRight')
        assert await width(s.page, orch) <= app_w - 488 + 1
        now = int(await h.get_attribute('aria-valuenow'))
        assert lo <= now <= hi
        hp = page.locator('#nd-h-pin')
        await hp.focus()
        for _ in range(30):
            await s.page.keyboard.press('Shift+ArrowLeft')
        await expect(hp).to_have_attribute('aria-valuenow', '25')
        for _ in range(30):
            await s.page.keyboard.press('Shift+ArrowRight')
        await expect(hp).to_have_attribute('aria-valuenow', '75')
        ht = page.locator('#nd-h-top')
        await ht.focus()
        for _ in range(30):
            await s.page.keyboard.press('Shift+ArrowUp')
        await expect(ht).to_have_attribute('aria-valuenow', '30')
        for _ in range(30):
            await s.page.keyboard.press('Shift+ArrowDown')
        await expect(ht).to_have_attribute('aria-valuenow', '80')
        assert await page.locator('.nd-belt').evaluate('e => e.getBoundingClientRect().height') > 40   # the card row never vanishes
        # the pointer is bounded the same way: dragging the edge far to the left stops at 360 px
        box = await h.bounding_box()
        await s.page.mouse.move(box['x'] + 4, box['y'] + 200)
        await s.page.mouse.down()
        await s.page.mouse.move(40, box['y'] + 200, steps=6)
        await s.page.mouse.up()
        assert 359 <= await width(s.page, orch) <= 361
        assert await s.page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert s.errors == []


async def test_pin_by_button_and_by_drag_and_the_pins_persist(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 4, {})
        wins = page.locator('.nd-win[data-run-id]')
        await expect(wins).to_have_count(2)
        await expect(wins.nth(0)).to_have_attribute('data-run-id', 'r-1')
        await expect(wins.nth(1)).to_have_attribute('data-run-id', 'r-2')
        # by button: no empty slot, so the third worker replaces the second window
        await page.locator('.nd-card[data-run-id="r-3"] [data-pin]').click()
        await expect(page.locator('.nd-win[data-slot="1"]')).to_have_attribute('data-run-id', 'r-3')
        await expect(page.locator('.nd-card[data-run-id="r-2"]')).to_be_visible()
        # unpin by the window's button empties the slot
        await page.locator('.nd-win[data-run-id="r-3"] [data-unpin]').click()
        await expect(page.locator('.nd-win-empty')).to_have_count(1)
        await expect(page.locator('.nd-card[data-run-id]')).to_have_count(3)
        # by drag: a card dropped on the first window replaces it
        await s.page.drag_and_drop('.nd-card[data-run-id="r-4"]', '.nd-win[data-run-id="r-1"]')
        await expect(page.locator('.nd-win[data-slot="0"]')).to_have_attribute('data-run-id', 'r-4')
        await expect(page.locator('.nd-card[data-run-id="r-1"]')).to_be_visible()
        # a card dropped on the empty window fills it
        await s.page.drag_and_drop('.nd-card[data-run-id="r-2"]', '.nd-win-empty')
        await expect(page.locator('.nd-win[data-slot="1"]')).to_have_attribute('data-run-id', 'r-2')
        await expect(page.locator('.nd-win-empty')).to_have_count(0)
        assert [k for k in await s.page.evaluate(KEYS) if k.startswith('dream.nested.pins.')] == ['dream.nested.pins.nested-test']
        await shot(s.page, 'nested-layout-pins-1600.png')
        await s.page.reload()
        await expect(s.page.locator('#stat')).to_have_text('Ready')
        page = await open_nested(s)
        await expect(page.locator('.nd-win[data-slot="0"]')).to_have_attribute('data-run-id', 'r-4')
        await expect(page.locator('.nd-win[data-slot="1"]')).to_have_attribute('data-run-id', 'r-2')
        await expect(page.locator('.nd-card[data-run-id]')).to_have_count(2)
        assert s.errors == []


async def test_conveyor_arrows_and_auto_scroll_that_pauses_on_hover_and_focus(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 9, {})                                            # seven cards: wider than the row at 1600 px
        await expect(page.locator('.nd-card[data-run-id]')).to_have_count(7)
        track = page.locator('.nd-track')
        left = lambda: track.evaluate('t => t.scrollLeft')  # noqa: E731
        assert await track.evaluate('t => t.scrollWidth > t.clientWidth')
        base = await left()                                        # the row's snap may rest a few pixels in
        assert base < 20, base
        await page.locator('#nd-next').click()
        await until(left, lambda v: v >= base + 300)
        await page.locator('#nd-prev').click()
        await until(left, lambda v: v <= base + 1)
        auto = page.locator('#nd-auto')
        await expect(auto).to_have_attribute('aria-pressed', 'false')
        await auto.click()
        await expect(auto).to_have_attribute('aria-pressed', 'true')
        await until(left, lambda v: v > 0)
        await s.page.hover(PAGE + ' .nd-belt')                   # the pointer over the cards: it holds still
        a = await left()
        await asyncio.sleep(0.4)
        assert await left() == a
        await s.page.mouse.move(5, 5)
        await until(left, lambda v: v > a)
        await page.locator('.nd-card').nth(3).focus()              # focus inside the row: it holds still
        b = await left()
        await asyncio.sleep(0.4)
        assert await left() == b
        await s.page.evaluate('document.activeElement.blur()')
        await until(left, lambda v: v != b)
        # reduced motion: no continuous movement, one card-sized step every 4 s
        await s.page.emulate_media(reduced_motion='reduce')
        await asyncio.sleep(0.2)
        c = await left()
        await asyncio.sleep(1.0)
        assert await left() == c
        d = await until(left, lambda v: v != c, timeout=6.0)
        step = await page.evaluate("p => (parseInt(getComputedStyle(p).getPropertyValue('--nd-card-w')) || 320) + 16")
        assert d == 0 or abs(d - c - step) <= 1, (c, d, step)
        await auto.click()
        await expect(auto).to_have_attribute('aria-pressed', 'false')
        e = await left()
        await asyncio.sleep(0.4)
        assert await left() == e
        assert s.errors == []


async def test_reset_layout_restores_the_defaults(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 4, {})
        orch = PAGE + ' .nd-orch'
        await expect(page.locator('.nd-win[data-run-id]')).to_have_count(2)
        w0 = await width(s.page, orch)
        h = page.locator('#nd-h-orch')
        await h.focus()
        for _ in range(3):
            await s.page.keyboard.press('Shift+ArrowRight')
        await page.locator('#nd-h-pin').focus()
        await s.page.keyboard.press('ArrowLeft')
        await page.locator('#nd-side').click()
        await page.locator('.nd-card[data-run-id="r-3"] [data-pin]').click()
        await page.locator('#nd-auto').click()
        await expect(page.locator('.nd-app')).to_have_attribute('data-side', 'right')
        await expect(page.locator('.nd-win[data-slot="1"]')).to_have_attribute('data-run-id', 'r-3')
        assert len(await s.page.evaluate(KEYS)) == 4
        await page.locator('#nd-reset').click()
        assert abs(await width(s.page, orch) - w0) <= 1
        await expect(page.locator('.nd-app')).to_have_attribute('data-side', 'left')
        await expect(page.locator('#nd-h-pin')).to_have_attribute('aria-valuenow', '50')
        await expect(page.locator('#nd-h-top')).to_have_attribute('aria-valuenow', '60')
        await expect(page.locator('.nd-win[data-slot="0"]')).to_have_attribute('data-run-id', 'r-1')
        await expect(page.locator('.nd-win[data-slot="1"]')).to_have_attribute('data-run-id', 'r-2')
        await expect(page.locator('#nd-auto')).to_have_attribute('aria-pressed', 'false')
        assert await s.page.evaluate(KEYS) == []
        await s.page.reload()
        await expect(s.page.locator('#stat')).to_have_text('Ready')
        page = await open_nested(s)
        await expect(page.locator('.nd-win[data-run-id]')).to_have_count(2)
        assert abs(await width(s.page, orch) - w0) <= 1
        await expect(page.locator('.nd-app')).to_have_attribute('data-side', 'left')
        assert s.errors == []


async def test_the_page_works_when_storage_throws(studio):
    async with studio() as s:
        await s.page.add_init_script(POISON)
        await s.page.reload()
        await expect(s.page.locator('#stat')).to_have_text('Ready')
        page = await open_nested(s)
        lanes(s, 3, {})
        await expect(page.locator('.nd-win[data-run-id]')).to_have_count(2)
        orch = PAGE + ' .nd-orch'
        w0 = await width(s.page, orch)
        await page.locator('#nd-h-orch').focus()
        await s.page.keyboard.press('Shift+ArrowRight')
        assert abs(await width(s.page, orch) - (round(w0) + 64)) <= 2      # resizing works, just not remembered
        await page.locator('#nd-side').click()
        await expect(page.locator('.nd-app')).to_have_attribute('data-side', 'right')
        await page.locator('.nd-card[data-run-id="r-3"] [data-pin]').click()
        await expect(page.locator('.nd-win[data-slot="1"]')).to_have_attribute('data-run-id', 'r-3')
        await page.locator('#nd-reset').click()
        await expect(page.locator('.nd-app')).to_have_attribute('data-side', 'left')
        assert await s.page.evaluate("Object.keys(localStorage).filter(k => k.startsWith('dream.nested.'))") == []
        assert s.errors == [], s.errors
