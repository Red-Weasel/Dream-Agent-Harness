"""The DREAM-195 gate's known limits on 449936b (gate-reports/p8-r1.md), each shown failing first: Shift guards the
letters only, Escape and `a` cancel a pending digit, a held key does not repeat, Escape works inside the composer, the
drawer's empty states honour the provider and name a next step, the phone drawer sits above Dream's nav bar, the
transcript bounds hold, and a history reset under an open detail keeps the focus in the drawer."""
from __future__ import annotations

import asyncio

import pytest
from playwright.async_api import expect

from test_nested_drawer import ENDINGS, lanes
from test_nested_view import PAGE, _activity, open_nested, studio  # noqa: F401

pytestmark = pytest.mark.asyncio
DRAWER = '#nd-drawer'
REPEAT = "document.body.dispatchEvent(new KeyboardEvent('keydown', {key: '2', repeat: true, bubbles: true, cancelable: true}))"


async def test_shift_guards_the_letters_and_a_held_key_does_not_repeat(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 3, {})
        drawer, detail = page.locator(DRAWER), page.locator('#nd-detail')
        await page.locator('.nd-title').click()
        await s.page.keyboard.press('Shift+a')
        await asyncio.sleep(0.3)
        await expect(drawer).to_be_hidden()
        await s.page.keyboard.press('a')
        await expect(drawer).to_be_visible()
        await s.page.keyboard.press('a')
        await expect(drawer).to_be_hidden()
        await s.page.evaluate(REPEAT)                     # a key held down: its repeats open nothing
        await asyncio.sleep(0.9)
        await expect(drawer).to_be_hidden()
        await s.page.keyboard.press('2')
        await expect(detail).to_have_attribute('data-run-id', 'r-2')
        assert s.errors == []


async def test_escape_and_a_cancel_a_pending_digit_and_escape_works_inside_the_composer(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 12, {})
        drawer, detail = page.locator(DRAWER), page.locator('#nd-detail')
        await page.locator('.nd-title').click()
        await s.page.keyboard.press('1')                  # twelve workers: "1" waits for a second digit
        await s.page.keyboard.press('Escape')             # ...and Escape cancels it
        await asyncio.sleep(0.9)
        await expect(drawer).to_be_hidden()
        await s.page.keyboard.press('1')
        await s.page.keyboard.press('a')                  # `a` opens the list and cancels the digit
        await expect(drawer).to_be_visible()
        await asyncio.sleep(0.9)
        await expect(detail).to_be_hidden()
        await s.page.keyboard.press('a')
        await expect(drawer).to_be_hidden()
        # Escape inside the composer closes an open drawer
        await page.locator('#nd-drawer-btn').click()
        await expect(drawer).to_be_visible()
        await page.locator('#nd-compose').click()
        await s.page.keyboard.type('a1')
        await expect(page.locator('#nd-compose')).to_have_value('a1')
        await expect(drawer).to_be_visible()
        await s.page.keyboard.press('Escape')
        await expect(drawer).to_be_hidden()
        await expect(page.locator('#nd-compose')).to_have_value('a1')
        assert s.errors == []


async def test_the_drawers_empty_states_honour_the_provider_and_name_a_next_step(studio):
    async with studio(provider='ChatGPT · Codex') as s:
        page = await open_nested(s)
        await page.locator('#nd-drawer-btn').click()
        await expect(page.locator('#nd-dlist')).to_contain_text('does not report worker activity')
        assert await page.locator('#nd-dlist').evaluate("e => /workers appear/i.test(e.textContent)") is False
        assert s.errors == []
    async with studio(provider='Claude · Anthropic') as s:             # its sub-agents are read-only cards (DREAM-212)
        page = await open_nested(s)
        await page.locator('#nd-drawer-btn').click()
        await expect(page.locator('#nd-dlist')).to_contain_text('No workers yet')
        await expect(page.locator('#nd-dlist')).to_contain_text('read-only cards once one runs')
        assert await page.locator('#nd-dlist').evaluate("e => /does not report/i.test(e.textContent)") is False
        assert s.errors == []
    async with studio() as s:
        page = await open_nested(s)
        await page.locator('#nd-drawer-btn').click()
        await expect(page.locator('#nd-dlist')).to_contain_text('No workers yet')
        lanes(s, 2, {})
        await page.locator('#nd-filters button').nth(1).click()          # Needs you: nothing asks
        await expect(page.locator('#nd-dlist')).to_contain_text('No workers match this filter')
        await expect(page.locator('#nd-dlist')).to_contain_text('Choose All')
        assert s.errors == []


async def test_the_phone_drawer_sits_above_the_nav_bar(studio):
    async with studio(width=390, height=844) as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        await page.locator('#nd-drawer-btn').click()
        close = page.locator('#nd-drawer-close')
        await expect(close).to_be_visible()
        await s.page.wait_for_function("() => { const r = document.getElementById('nd-drawer').getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth + 1; }")   # slid in
        hit = await close.evaluate("e => { const b = e.getBoundingClientRect(); const t = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2); return t === e || e.contains(t); }")
        assert hit, 'the close button is covered'
        await page.locator('#nd-dlist .nd-item').first.click()
        back = page.locator('#nd-back')
        await expect(back).to_be_visible()
        hit = await back.evaluate("e => { const b = e.getBoundingClientRect(); const t = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2); return t === e || e.contains(t); }")
        assert hit, 'Back is covered'
        await back.click()
        await expect(page.locator('#nd-dlist')).to_be_visible()
        assert s.errors == []


async def test_the_transcript_is_bounded(studio):
    async with studio() as s:
        page = await open_nested(s)
        for i in range(450):
            s.srv.bus.publish(_activity('r-big', 'writer', 'status', status='running', text=f'Step {i + 1}.'))
        s.srv.bus.publish(_activity('r-big', 'writer', 'tool_use', status='requested',
                                    data={'id': 'r-big:1:0:t', 'name': 'read_file', 'input': {'path': 'big.md'}}))
        s.srv.bus.publish(_activity('r-big', 'writer', 'tool_result', status='returned',
                                    data={'id': 'r-big:1:0:t', 'name': 'read_file', 'content': 'x' * 5000, 'is_error': False}))
        await expect(page.locator('.nd-win[data-run-id="r-big"] .nd-headline')).to_have_text('Step 450.')
        await page.locator('.nd-pips .nd-pip').first.click()
        entries = page.locator('#nd-detail .nd-dbody .nd-entry')
        await expect(entries).to_have_count(400)
        await expect(page.locator('#nd-detail .nd-dbody')).to_contain_text('51 earlier entries are no longer retained.')
        await expect(entries.first).to_contain_text('Step 52.')
        assert len(await entries.last.locator('.nd-tool-out').text_content()) == 2000
        assert len(await page.locator('.nd-win[data-run-id="r-big"] .nd-tool-out').text_content()) == 201   # the compact tail
        assert s.errors == []


async def test_a_history_reset_under_an_open_detail_keeps_the_focus_in_the_drawer(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        await page.locator('.nd-pips .nd-pip').first.click()
        detail = page.locator('#nd-detail')
        await expect(detail).to_have_attribute('data-run-id', 'r-1')
        await expect(page.locator('#nd-back')).to_be_focused()
        # a history reset that brings back no worker (a new session): the detail collapses to the list
        await s.page.evaluate("window.dispatchEvent(new CustomEvent('dream:event', {detail: {m: {kind: 'history', data: {events: []}}, replay: false}}))")
        await expect(detail).to_be_hidden()
        await expect(page.locator('#nd-dlist')).to_be_visible()
        await expect(page.locator('#nd-drawer-close')).to_be_focused()
        await expect(page.locator('#nd-dlist')).to_contain_text('No workers yet')
        await s.page.keyboard.press('Escape')
        await expect(page.locator(DRAWER)).to_be_hidden()
        assert await detail.get_attribute('data-run-id') is None      # nothing stale is left behind
        assert s.errors == []
