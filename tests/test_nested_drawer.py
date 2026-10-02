"""Nested Dream phase 8 (DREAM-195): the All-agents drawer and the keyboard. The drawer (nested-drawer.js) reads the
state nested.js keeps: filters with counts over fixed-height rows, a detail view with the whole retained transcript of
one run, Pin (the only action with a backend today), Esc with focus return, inert while closed, digits that open
agent N through a two-digit buffer, `a` and `i`, and the top-row counts that cycle. No model, no engine, no Dream."""
from __future__ import annotations

import re

import pytest
from playwright.async_api import expect

from test_nested_view import _activity, open_nested, shot, studio  # noqa: F401

pytestmark = pytest.mark.asyncio
DRAWER = '#nd-drawer'
ENDINGS = {'completed': 'Subagent finished; its result was returned to the lead agent.',
           'failed': 'Subagent timed out; work is incomplete.',
           'interrupted': 'Subagent observation was interrupted. In-flight effects may need checking.'}


def lanes(s, n, endings):
    """n scripted workers r-1..r-n, each with one full round; `endings` maps a run id to its terminal status."""
    for i in range(1, n + 1):
        run = f'r-{i}'
        s.srv.bus.publish(_activity(run, 'writer', 'status', status='running', text='Subagent started.'))
        s.srv.bus.publish(_activity(run, 'writer', 'request', status='awaiting_response', request_index=1,
                                    model='fixture-27b', context={'used': 1024 * i, 'window': 32768}))
        s.srv.bus.publish(_activity(run, 'writer', 'thinking_report', text=f'Reading shelf {i}.'))
        s.srv.bus.publish(_activity(run, 'writer', 'tool_use', status='requested',
                                    data={'id': f'{run}:1:0:t', 'name': 'read_file', 'input': {'path': f'shelf{i}.md'}}))
        s.srv.bus.publish(_activity(run, 'writer', 'tool_result', status='returned',
                                    data={'id': f'{run}:1:0:t', 'name': 'read_file', 'content': f'shelf {i} read', 'is_error': False}))
        s.srv.bus.publish(_activity(run, 'writer', 'response', status='received', request_index=1, text=f'Report on shelf {i}.'))
        if run in endings:
            s.srv.bus.publish(_activity(run, 'writer', 'status', status=endings[run], text=ENDINGS[endings[run]]))


async def test_drawer_lists_every_worker_with_filter_counts_and_fixed_rows(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 5, {'r-3': 'failed', 'r-4': 'interrupted', 'r-5': 'completed'})
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(5)
        drawer = page.locator(DRAWER)
        await expect(drawer).to_be_hidden()
        await page.locator('#nd-drawer-btn').click()
        await expect(drawer).to_be_visible()
        await expect(page.locator('#nd-drawer-btn')).to_have_attribute('aria-expanded', 'true')
        assert await drawer.evaluate('d => d.inert') is False
        await expect(drawer).to_have_attribute('aria-hidden', 'false')
        await expect(page.locator('#nd-dcount')).to_have_text('5 workers · 2 working')
        filters = page.locator('#nd-filters button')
        await expect(filters).to_have_text(['All 5', 'Needs you 0', 'Working 2', 'Failed or stopped 2', 'Idle 1'])
        rows = page.locator('#nd-dlist .nd-item')
        await expect(rows).to_have_count(5)
        heights = await rows.evaluate_all('els => els.map(e => Math.round(e.getBoundingClientRect().height))')
        assert heights == [56] * 5, heights
        await expect(rows.nth(2)).to_have_attribute('aria-label', '3 writer, Failed')
        await expect(rows.nth(2)).to_contain_text('Report on shelf 3.')
        await expect(rows.nth(2)).to_contain_text('fixture-27b')
        await filters.nth(3).click()                      # Failed or stopped
        await expect(filters.nth(3)).to_have_attribute('aria-pressed', 'true')
        await expect(rows).to_have_count(2)
        await expect(rows.nth(0)).to_have_attribute('data-run-id', 'r-3')
        await expect(rows.nth(1)).to_have_attribute('data-run-id', 'r-4')
        await filters.nth(4).click()                      # Idle
        await expect(rows).to_have_count(1)
        await expect(rows.first).to_have_attribute('data-run-id', 'r-5')
        await filters.nth(1).click()                      # Needs you: nothing asks yet
        await expect(rows).to_have_count(0)
        await expect(page.locator('#nd-dlist')).to_contain_text('No workers match')
        await filters.nth(0).click()
        await expect(rows).to_have_count(5)
        # the list follows the state
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='completed', text=ENDINGS['completed']))
        await expect(filters).to_have_text(['All 5', 'Needs you 0', 'Working 1', 'Failed or stopped 2', 'Idle 2'])
        await expect(page.locator('#nd-dcount')).to_have_text('5 workers · 1 working')
        await expect(rows.nth(0)).to_have_attribute('aria-label', '1 writer, Done')
        await shot(s.page, 'nested-drawer-list-1600.png')
        assert s.errors == []


async def test_detail_shows_the_whole_transcript_and_pin_works_without_dead_buttons(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 3, {'r-3': 'completed'})
        await expect(page.locator('.nd-card[data-run-id="r-3"]')).to_be_visible()
        await page.locator('#nd-drawer-btn').click()
        await page.locator('#nd-dlist .nd-item[data-run-id="r-3"]').click()
        detail = page.locator('#nd-detail')
        await expect(detail).to_be_visible()
        await expect(page.locator('#nd-dlist')).to_be_hidden()
        await expect(detail).to_have_attribute('data-run-id', 'r-3')
        await expect(detail.locator('.nd-back')).to_be_focused()
        await expect(detail.locator('.nd-dfacts')).to_contain_text('fixture-27b')
        await expect(detail.locator('.nd-dfacts')).to_contain_text('9% · 3,072/32,768')
        entries = detail.locator('.nd-dbody .nd-entry')
        await expect(entries).to_have_count(7)            # status, request, thinking, tool, reply line, text, status
        await expect(entries.nth(0)).to_contain_text('Subagent started.')
        await expect(entries.nth(1)).to_contain_text('Request 1 · waiting for the reply')
        await expect(entries.nth(2)).to_contain_text('Reading shelf 3.')
        await expect(entries.nth(3)).to_contain_text('read_file')
        await expect(entries.nth(3)).to_contain_text('shelf 3 read')
        await expect(entries.nth(4)).to_contain_text('Request 1 · reply received')
        await expect(entries.nth(5)).to_have_text('Report on shelf 3.')
        await expect(entries.nth(6)).to_contain_text('finished')
        # a finished worker: no Retry (it did not fail), and P5's Pause and Stop and P6's reply box are there but off
        assert await detail.get_by_role('button', name=re.compile(r'retry', re.I)).count() == 0
        controls = detail.get_by_role('button', name=re.compile(r'^(pause|stop)$', re.I))
        assert await controls.count() == 2
        for b in await controls.all():
            await expect(b).to_be_disabled()
        assert await detail.locator('textarea, input').count() == 1
        await expect(detail.get_by_label('Message 3 writer', exact=True)).to_have_attribute('aria-disabled', 'true')
        await expect(detail.get_by_role('button', name='Send to 3 writer', exact=True)).to_be_disabled()
        # Pin works: the third worker takes the second window and the second becomes a card
        pin = page.locator('#nd-pin-btn')
        await expect(pin).to_have_text('Pin to a window')
        await pin.click()
        wins = page.locator('.nd-win[data-run-id]')
        await expect(wins.nth(1)).to_have_attribute('data-run-id', 'r-3')
        await expect(page.locator('.nd-card[data-run-id]')).to_have_attribute('data-run-id', 'r-2')
        await expect(pin).to_have_text('Unpin')
        await pin.click()
        await expect(page.locator('.nd-win-empty')).to_have_count(1)
        await expect(page.locator('.nd-card[data-run-id]')).to_have_count(2)
        await expect(pin).to_have_text('Pin to a window')
        # the next worker the orchestrator starts takes the empty window
        s.srv.bus.publish(_activity('r-4', 'writer', 'status', status='running', text='Subagent started.'))
        await expect(wins.nth(1)).to_have_attribute('data-run-id', 'r-4')
        await expect(page.locator('.nd-win-empty')).to_have_count(0)
        # rows keep arriving in the open detail
        s.srv.bus.publish(_activity('r-3', 'writer', 'text_delta', text='Postscript.'))
        await expect(entries).to_have_count(8)
        await expect(entries.nth(7)).to_have_text('Postscript.')
        await shot(s.page, 'nested-drawer-detail-1600.png')
        # Back returns to the list with that worker's row focused
        await detail.locator('.nd-back').click()
        await expect(page.locator('#nd-dlist')).to_be_visible()
        await expect(page.locator('#nd-dlist .nd-item[data-run-id="r-3"]')).to_be_focused()
        assert s.errors == []


async def test_digits_open_agent_n_through_a_two_digit_buffer(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 12, {})
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(12)
        await page.locator('.nd-title').click()           # the page, not a text box
        detail = page.locator('#nd-detail')
        await s.page.keyboard.press('3')                  # no worker 30..39 exists: opens at once
        await expect(detail).to_have_attribute('data-run-id', 'r-3')
        await s.page.keyboard.press('Escape')             # back to the list
        await expect(detail).to_be_hidden()
        await expect(page.locator(DRAWER)).to_be_visible()
        await s.page.keyboard.press('1')
        await s.page.keyboard.press('2')                  # inside the buffer window: twelve, not one
        await expect(detail).to_have_attribute('data-run-id', 'r-12')
        await s.page.keyboard.press('Escape')
        await s.page.keyboard.press('Escape')             # closes the drawer
        await expect(page.locator(DRAWER)).to_be_hidden()
        await s.page.keyboard.press('1')                  # alone: worker 1 once the buffer settles
        await expect(detail).to_have_attribute('data-run-id', 'r-1')
        await s.page.keyboard.press('Escape')
        await s.page.keyboard.press('Escape')
        await expect(page.locator(DRAWER)).to_be_hidden()
        await s.page.keyboard.press('0')                  # no worker 0: nothing opens
        await expect(page.locator(DRAWER)).to_be_hidden()
        assert s.errors == []


async def test_escape_closes_the_drawer_focus_returns_and_the_closed_drawer_is_inert(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        drawer, btn = page.locator(DRAWER), page.locator('#nd-drawer-btn')
        assert await drawer.evaluate('d => d.inert') is True
        await expect(drawer).to_have_attribute('aria-hidden', 'true')
        await btn.focus()
        await s.page.keyboard.press('Tab')
        assert await s.page.evaluate("!document.getElementById('nd-drawer').contains(document.activeElement)")
        await btn.click()
        await expect(drawer).to_be_visible()
        await expect(page.locator('#nd-drawer-close')).to_be_focused()
        await s.page.keyboard.press('Tab')
        assert await s.page.evaluate("document.getElementById('nd-drawer').contains(document.activeElement)")
        await s.page.keyboard.press('Escape')
        await expect(drawer).to_be_hidden()
        await expect(btn).to_be_focused()
        await expect(btn).to_have_attribute('aria-expanded', 'false')
        assert await drawer.evaluate('d => d.inert') is True
        # a pip opens its worker; Escape twice returns focus to that pip, re-rendered or not
        pip = page.locator('.nd-pips .nd-pip').nth(1)
        await pip.click()
        await expect(page.locator('#nd-detail')).to_have_attribute('data-run-id', 'r-2')
        s.srv.bus.publish(_activity('r-2', 'writer', 'status', status='completed', text=ENDINGS['completed']))
        await expect(pip).to_have_attribute('aria-label', '2 writer: Done')
        await s.page.keyboard.press('Escape')
        await s.page.keyboard.press('Escape')
        await expect(drawer).to_be_hidden()
        await expect(pip).to_be_focused()
        # the close button closes too, focus back on the opener
        await btn.click()
        await page.locator('#nd-drawer-close').click()
        await expect(drawer).to_be_hidden()
        await expect(btn).to_be_focused()
        assert s.errors == []


async def test_keys_stay_out_of_text_boxes_and_other_views(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 3, {})
        drawer, compose = page.locator(DRAWER), page.locator('#nd-compose')
        await compose.click()
        await s.page.keyboard.type('a1i')
        await expect(compose).to_have_value('a1i')
        await expect(drawer).to_be_hidden()
        await page.locator('.nd-title').click()
        await s.page.keyboard.press('a')
        await expect(drawer).to_be_visible()
        await s.page.keyboard.press('a')
        await expect(drawer).to_be_hidden()
        await s.page.keyboard.press('i')                  # nothing needs the owner: nothing opens
        await expect(drawer).to_be_hidden()
        await s.page.keyboard.press('Escape')             # nothing open: harmless
        await expect(page).to_be_visible()
        # on the chat view the keys are the chat's
        await s.page.locator('#dream-nav-chat').click()
        await expect(page).to_be_hidden()
        await s.page.locator('#input').click()
        await s.page.keyboard.type('a2')
        await expect(s.page.locator('#input')).to_have_value('a2')
        await s.page.evaluate('document.activeElement.blur()')
        await s.page.keyboard.press('2')
        await s.page.keyboard.press('a')
        await s.page.locator('#dream-nav-nested').click()
        await expect(page).to_be_visible()
        await expect(drawer).to_be_hidden()
        assert s.prompts == [] and s.errors == []


async def test_top_row_counts_cycle_through_the_failed_workers(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 4, {'r-2': 'failed', 'r-4': 'interrupted'})
        attn, detail = page.locator('.nd-attn button.nd-fail'), page.locator('#nd-detail')
        await expect(attn).to_have_text('2 failed')
        await attn.click()
        await expect(detail).to_have_attribute('data-run-id', 'r-2')
        await attn.click()
        await expect(detail).to_have_attribute('data-run-id', 'r-4')
        await attn.click()
        await expect(detail).to_have_attribute('data-run-id', 'r-2')
        await s.page.keyboard.press('Escape')
        await expect(page.locator('#nd-dlist .nd-item')).to_have_count(4)
        await expect(page.locator('#nd-filters button').nth(0)).to_have_attribute('aria-pressed', 'true')
        assert s.errors == []
