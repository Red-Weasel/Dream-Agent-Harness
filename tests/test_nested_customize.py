"""Nested Dream phase 13, part 1 (DREAM-200): the Customize panel -- text size, the orchestrator's side, which pips show,
the card width and thinking on cards -- kept under localStorage['dream.nested.*'] with guarded storage, keyboard
operable, an ARIA dialog opened from the top row; Reset layout returns everything to the defaults. No model, no engine."""
from __future__ import annotations

import asyncio

import pytest
from playwright.async_api import expect

from test_nested_drawer import ENDINGS, lanes
from test_nested_layout import POISON
from test_nested_view import PAGE, _activity, open_nested, studio  # noqa: F401

pytestmark = pytest.mark.asyncio
KEYS = "Object.keys(localStorage).filter(k => k.startsWith('dream.nested.')).sort()"
FONT = 'e => parseFloat(getComputedStyle(e).fontSize)'
HIT = ('e => { const b = e.getBoundingClientRect(); const t = document.elementFromPoint(b.left + b.width / 2, '
       'b.top + b.height / 2); return t === e || e.contains(t); }')
OVERLAP = """([a, b]) => { const r = s => document.querySelector(s).getBoundingClientRect(), p = r(a), q = r(b);
  return Math.max(0, Math.min(p.right, q.right) - Math.max(p.left, q.left)) * Math.max(0, Math.min(p.bottom, q.bottom) - Math.max(p.top, q.top)); }"""
SETTLED = "() => getComputedStyle(document.getElementById('nd-drawer')).transform === 'none'"   # slid all the way in


async def open_panel(page):
    await page.locator('#nd-custom-btn').click()
    panel = page.locator('#nd-custom')
    await expect(panel).to_be_visible()
    return panel


async def test_the_panel_is_a_dialog_opened_from_the_top_row_and_closed_by_escape(studio):
    async with studio() as s:
        page = await open_nested(s)
        btn, panel = page.locator('#nd-custom-btn'), page.locator('#nd-custom')
        await expect(panel).to_be_hidden()
        await expect(btn).to_have_attribute('aria-expanded', 'false')
        await expect(btn).to_have_attribute('aria-controls', 'nd-custom')
        await open_panel(page)
        await expect(panel).to_have_attribute('role', 'dialog')
        await expect(panel).to_have_attribute('aria-labelledby', 'nd-custom-h')
        await expect(btn).to_have_attribute('aria-expanded', 'true')
        await expect(page.locator('#nd-custom-close')).to_be_focused()
        await s.page.keyboard.press('Escape')
        await expect(panel).to_be_hidden()
        await expect(btn).to_be_focused()
        await expect(btn).to_have_attribute('aria-expanded', 'false')
        await btn.click()
        await page.locator('#nd-custom-close').click()
        await expect(panel).to_be_hidden()
        await expect(btn).to_be_focused()
        assert s.errors == []


async def test_text_size_side_pips_card_width_and_thinking_apply_and_persist(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 4, {'r-3': 'completed'})
        s.srv.bus.publish(_activity('r-4', 'writer', 'status', status='queued', text='Waiting for an engine lane (2 of 2 busy)'))
        s.srv.bus.publish(_activity('r-5', 'thinker', 'status', status='running', text='Subagent started.'))
        s.srv.bus.publish(_activity('r-5', 'thinker', 'thinking_report', text='Only a thought so far.'))
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(5)
        title, row = page.locator('.nd-title'), page.locator('.nd-card[data-run-id="r-5"]')
        assert abs(await title.evaluate(FONT) - 14) < 0.05
        await expect(row.locator('.nd-cbody .nd-th')).to_have_count(1)                     # thinking on cards, by default
        panel = await open_panel(page)
        # text size
        await panel.get_by_role('button', name='Large', exact=True).first.click()
        assert abs(await title.evaluate(FONT) - 16) < 0.05                                 # 14 px x 1.143
        await expect(panel.get_by_role('button', name='Large', exact=True).first).to_have_attribute('aria-pressed', 'true')
        # the orchestrator's side
        await panel.get_by_role('button', name='Right', exact=True).click()
        await expect(page.locator('.nd-app')).to_have_attribute('data-side', 'right')
        # which pips show: active only hides the finished and the queued
        await panel.get_by_role('button', name='Active only', exact=True).click()
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(3)
        await expect(page.locator('.nd-pips .nd-pip').nth(0)).to_have_attribute('aria-label', '1 writer: Working')
        await panel.get_by_role('button', name='Every agent', exact=True).click()
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(5)
        await panel.get_by_role('button', name='Active only', exact=True).click()
        # card width
        card = page.locator('.nd-card[data-run-id="r-4"]')
        await expect(card).to_have_css('width', '320px')
        await panel.locator('[data-opt="cardw"]').get_by_role('button', name='Large', exact=True).click()
        await expect(card).to_have_css('width', '368px')   # retrying: the click queues a render that rebuilds the track
        # thinking on cards
        await panel.get_by_label('Show thinking on cards').uncheck()
        await expect(row.locator('.nd-cbody .nd-th')).to_have_count(0)
        await expect(row.locator('.nd-cbody')).to_have_text('')                         # nothing: the headline already says it
        await panel.get_by_label('Show thinking on cards').check()
        await expect(row.locator('.nd-cbody .nd-th')).to_have_count(1)
        await panel.get_by_label('Show thinking on cards').uncheck()
        keys = await s.page.evaluate(KEYS)
        assert keys == ['dream.nested.card-think', 'dream.nested.cardw', 'dream.nested.fz', 'dream.nested.pins.nested-test', 'dream.nested.pips', 'dream.nested.side'], keys
        # everything survives a reload
        await s.page.reload()
        await expect(s.page.locator('#stat')).to_have_text('Ready')
        page = await open_nested(s)
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(3)
        assert abs(await page.locator('.nd-title').evaluate(FONT) - 16) < 0.05
        await expect(page.locator('.nd-app')).to_have_attribute('data-side', 'right')
        assert abs(await page.locator('.nd-card[data-run-id="r-4"]').evaluate('e => e.getBoundingClientRect().width') - 368) < 1
        await expect(page.locator('.nd-card[data-run-id="r-5"] .nd-cbody .nd-th')).to_have_count(0)
        panel = await open_panel(page)
        await expect(panel.get_by_role('button', name='Large', exact=True).first).to_have_attribute('aria-pressed', 'true')
        await expect(panel.get_by_label('Show thinking on cards')).not_to_be_checked()
        assert s.errors == []


async def test_the_panel_works_from_the_keyboard_and_reset_layout_restores_the_defaults(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 3, {'r-3': 'completed'})
        title = page.locator('.nd-title')
        await page.locator('#nd-custom-btn').focus()
        await s.page.keyboard.press('Enter')                                                # opens
        await expect(page.locator('#nd-custom')).to_be_visible()
        await expect(page.locator('#nd-custom-close')).to_be_focused()
        await s.page.keyboard.press('Tab')                                                  # the first size button
        await expect(page.locator('#nd-custom [data-opt="fz"] button').first).to_be_focused()
        await s.page.keyboard.press('Tab')
        await s.page.keyboard.press('Tab')
        await s.page.keyboard.press('Tab')                                                  # "Larger"
        await s.page.keyboard.press('Space')
        assert abs(await title.evaluate(FONT) - 18) < 0.05                                 # 14 px x 1.286
        await s.page.keyboard.press('Escape')
        await expect(page.locator('#nd-custom')).to_be_hidden()
        await expect(page.locator('#nd-custom-btn')).to_be_focused()
        await page.locator('#nd-custom-btn').click()
        await page.locator('#nd-custom').get_by_role('button', name='Active only', exact=True).click()
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(2)
        await page.locator('#nd-reset').click()                                             # Reset layout: sizes, side, pins and these
        assert abs(await title.evaluate(FONT) - 14) < 0.05
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(3)
        await expect(page.locator('#nd-custom [data-opt="fz"] button[aria-pressed="true"]')).to_have_text('Default')
        await expect(page.locator('#nd-custom [data-opt="pips"] button[aria-pressed="true"]')).to_have_text('Every agent')
        await expect(page.locator('#nd-custom').get_by_label('Show thinking on cards')).to_be_checked()
        assert await s.page.evaluate(KEYS) == []
        assert s.errors == []


# --- the Customize gate's known limits on 35aef5b: a storage that throws, Escape from anywhere on the view, never over
#     the drawer, the workers' side with the orchestrator on the right, `a` stays out of the panel and the drawer ---------

async def test_customize_still_applies_live_when_storage_throws(studio):
    async with studio() as s:
        await s.page.add_init_script(POISON)
        await s.page.reload()
        await expect(s.page.locator('#stat')).to_have_text('Ready')
        page = await open_nested(s)
        lanes(s, 3, {'r-3': 'completed'})
        panel = await open_panel(page)
        await panel.locator('[data-opt="fz"]').get_by_role('button', name='Larger', exact=True).click()
        assert abs(await page.locator('.nd-title').evaluate(FONT) - 18) < 0.05
        await panel.get_by_role('button', name='Right', exact=True).click()
        await expect(page.locator('.nd-app')).to_have_attribute('data-side', 'right')
        await panel.get_by_role('button', name='Active only', exact=True).click()
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(2)
        await panel.locator('[data-opt="cardw"]').get_by_role('button', name='Large', exact=True).click()
        await expect(panel.locator('[data-opt="cardw"] button[aria-pressed="true"]')).to_have_text('Large')
        await panel.get_by_label('Show thinking on cards').uncheck()
        await expect(panel.get_by_label('Show thinking on cards')).not_to_be_checked()
        assert await s.page.evaluate(KEYS) == []                                        # applied live, kept nowhere
        await page.locator('#nd-reset').click()
        assert abs(await page.locator('.nd-title').evaluate(FONT) - 14) < 0.05
        assert s.errors == [], s.errors


async def test_escape_closes_the_panel_wherever_the_focus_is_on_the_view(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 3, {})
        btn, panel = page.locator('#nd-custom-btn'), page.locator('#nd-custom')
        await open_panel(page)
        await btn.focus()                                                              # the focus leaves the open panel
        await expect(panel).to_be_visible()
        await s.page.keyboard.press('Escape')
        await expect(panel).to_be_hidden()
        await expect(btn).to_be_focused()
        await expect(btn).to_have_attribute('aria-expanded', 'false')
        # typing to the orchestrator: Escape closes the panel and leaves the words and the focus where they are
        await open_panel(page)
        compose = page.locator('#nd-compose')
        await compose.click()
        await s.page.keyboard.type('draft')
        await s.page.keyboard.press('Escape')
        await expect(panel).to_be_hidden()
        await expect(compose).to_be_focused()
        await expect(compose).to_have_value('draft')
        # a pinned worker's window
        await open_panel(page)
        await page.locator('.nd-win[data-run-id="r-1"]').focus()
        await s.page.keyboard.press('Escape')
        await expect(panel).to_be_hidden()
        await s.page.keyboard.press('Escape')                                          # nothing open: nothing changes
        await expect(page).to_be_visible()
        assert s.errors == []


async def test_the_panel_and_the_drawer_are_never_open_together(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 3, {})
        panel, drawer = page.locator('#nd-custom'), page.locator('#nd-drawer')
        await open_panel(page)
        await page.locator('#nd-drawer-btn').click()                                   # All agents: the panel closes
        await expect(drawer).to_be_visible()
        await expect(panel).to_be_hidden()
        await expect(page.locator('#nd-custom-btn')).to_have_attribute('aria-expanded', 'false')
        await s.page.wait_for_function(SETTLED)
        assert await page.locator('#nd-drawer-close').evaluate(HIT), 'the drawer\'s Close is covered'
        assert await page.locator('#nd-drawer-title').evaluate(HIT), 'the drawer\'s header is covered'
        await expect(page.locator('#nd-drawer-close')).to_be_focused()
        await page.locator('#nd-custom-btn').click()                                   # Customize closes the drawer
        await expect(panel).to_be_visible()
        await expect(drawer).to_be_hidden()
        await expect(page.locator('#nd-custom-close')).to_be_focused()
        await page.locator('.nd-pips .nd-pip').nth(1).click()                          # a pip opens its worker
        await expect(page.locator('#nd-detail')).to_have_attribute('data-run-id', 'r-2')
        await expect(panel).to_be_hidden()
        await s.page.wait_for_function(SETTLED)
        assert await page.locator('#nd-back').evaluate(HIT), 'Back is covered'
        await s.page.keyboard.press('Escape')
        await s.page.keyboard.press('Escape')
        await expect(drawer).to_be_hidden()
        await open_panel(page)                                                         # `a` from the page
        await page.locator('.nd-title').click()
        await s.page.keyboard.press('a')
        await expect(drawer).to_be_visible()
        await expect(panel).to_be_hidden()
        assert s.errors == []


async def test_with_the_orchestrator_on_the_right_the_panel_sits_over_the_workers(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 3, {})
        panel = await open_panel(page)
        assert await s.page.evaluate(OVERLAP, ['#nd-custom', '.nd-orch']) == 0
        await panel.get_by_role('button', name='Right', exact=True).click()
        await expect(page.locator('.nd-app')).to_have_attribute('data-side', 'right')
        assert await s.page.evaluate(OVERLAP, ['#nd-custom', '.nd-orch']) == 0
        box, main = await panel.bounding_box(), await page.locator('.nd-main').bounding_box()
        assert main['x'] <= box['x'] and box['x'] + box['width'] <= main['x'] + main['width'] + 1, (box, main)
        # a phone keeps the sheet across the bottom whichever side is chosen
        await s.page.set_viewport_size({'width': 390, 'height': 844})
        box = await panel.bounding_box()
        assert abs(box['x'] - 16) <= 1 and abs(box['x'] + box['width'] - (390 - 16)) <= 1, box
        assert s.errors == []


async def test_a_stays_out_of_the_panel_and_toggles_the_drawer_from_inside_it(studio):
    """(e), as the lead ruled on the gate's round 2 (finding 4): `a` never fires from inside the Customize panel; the
    All-agents drawer keeps the mockup's toggle -- a second `a` closes it even from inside, except in a text field."""
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 3, {})
        panel, drawer = page.locator('#nd-custom'), page.locator('#nd-drawer')
        await open_panel(page)
        await panel.locator('[data-opt="fz"] button').first.focus()
        await s.page.keyboard.press('a')
        await asyncio.sleep(0.3)
        await expect(drawer).to_be_hidden()
        await expect(panel).to_be_visible()
        await s.page.keyboard.press('Escape')
        await expect(panel).to_be_hidden()
        await page.locator('#nd-drawer-btn').click()
        await expect(drawer).to_be_visible()
        await page.locator('#nd-filters button').nth(2).focus()                        # a filter inside the drawer
        await s.page.keyboard.press('a')
        await expect(drawer).to_be_hidden()
        await page.locator('.nd-title').click()
        await s.page.keyboard.press('a')
        await expect(drawer).to_be_visible()
        await expect(page.locator('#nd-drawer-close')).to_be_focused()                 # its Close holds the focus...
        await s.page.keyboard.press('a')                                              # ...and a second `a` closes it
        await expect(drawer).to_be_hidden()
        await page.locator('.nd-pips .nd-pip').first.click()                          # a worker's detail, its message box
        box = page.locator('#nd-detail').get_by_label('Message 1 writer', exact=True)
        await box.click()
        await s.page.keyboard.type('a')
        await expect(box).to_have_value('a')                                          # typed, not a shortcut
        await expect(drawer).to_be_visible()
        assert s.errors == []
