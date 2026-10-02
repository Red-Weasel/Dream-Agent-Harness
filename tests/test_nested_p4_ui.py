"""Nested Dream and the backend's P4 rows (DREAM-191, NESTED_EVENTS.md "P4"): a worker that waits for an engine lane
is a pewter Queued pip with the reason as its headline, a lane waiting for another request shows the note, a worker
stopped while queued is Stopped with its reason, a failure carries its reason, and the top row says "lanes: k of N
busy" from hello.lanes and then from every `lanes` event. Scripted rows in the contract's shape; no model, no engine."""
from __future__ import annotations

import pytest
from playwright.async_api import expect

from dream.core.backends.base import Event
from test_nested_view import _activity, open_nested, studio  # noqa: F401

pytestmark = pytest.mark.asyncio
QUEUED = 'Waiting for an engine lane (2 of 2 busy)'
WAITING = 'Waiting for another Dream request on this local engine to finish…'
FREED = 'The local engine is free after 12 s; sending this request.'
STOPPED = 'Stopped while waiting for an engine lane; nothing was sent.'
FAILED = 'Subagent returned a failed or incomplete result: (subagent \'writer\' timed out; incomplete)'


async def test_queued_waiting_stopped_and_failed_rows_render_with_their_reasons(studio):
    async with studio() as s:
        page = await open_nested(s)
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='running', text='Subagent started.'))
        s.srv.bus.publish(_activity('r-2', 'writer', 'status', status='running', text='Subagent started.'))
        s.srv.bus.publish(_activity('r-3', 'writer', 'status', status='queued', text=QUEUED))
        s.srv.bus.publish(_activity('r-4', 'writer', 'status', status='queued', text=QUEUED))
        pips = page.locator('.nd-pips .nd-pip')
        await expect(pips).to_have_count(4)
        await expect(pips.nth(2)).to_have_attribute('aria-label', '3 writer: Queued')
        await expect(pips.nth(2)).to_have_class(r'nd-pip nd-queued')
        await expect(pips.nth(2)).to_have_css('background-color', 'rgb(74, 74, 92)')          # pewter
        card3 = page.locator('.nd-card[data-run-id="r-3"]')
        await expect(card3.locator('.nd-state')).to_have_text('Queued')
        await expect(card3.locator('.nd-headline')).to_have_text(QUEUED)
        # the third worker gets its lane and runs; the fourth is stopped while it still waits
        s.srv.bus.publish(_activity('r-3', 'writer', 'status', status='running', text='Subagent started.'))
        await expect(pips.nth(2)).to_have_attribute('aria-label', '3 writer: Working')
        await expect(card3.locator('.nd-headline')).to_have_text('Subagent started.')
        s.srv.bus.publish(_activity('r-4', 'writer', 'status', status='interrupted', text=STOPPED))
        await expect(pips.nth(3)).to_have_attribute('aria-label', '4 writer: Stopped')
        await expect(pips.nth(3)).to_have_css('background-color', 'rgb(255, 115, 133)')       # the failed/stopped red
        await expect(page.locator('.nd-card[data-run-id="r-4"] .nd-headline')).to_have_text(STOPPED)
        await expect(page.locator('.nd-attn')).to_contain_text('1 failed')
        # a lane waiting for another request on the local engine says so, and says when it is free
        win = page.locator('.nd-win[data-run-id="r-1"]')
        s.srv.bus.publish(_activity('r-1', 'writer', 'request', status='awaiting_response', request_index=1))
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='waiting', text=WAITING))
        await expect(win.locator('.nd-headline')).to_have_text(WAITING)
        await expect(win.locator('.nd-state')).to_have_text('Working')
        await expect(pips.nth(0)).to_have_attribute('aria-label', '1 writer: Working')
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='running', text=FREED))
        await expect(win.locator('.nd-headline')).to_have_text(FREED)
        # a failure carries its reason
        s.srv.bus.publish(_activity('r-2', 'writer', 'status', status='failed', text=FAILED))
        win2 = page.locator('.nd-win[data-run-id="r-2"]')
        await expect(win2.locator('.nd-state')).to_have_text('Failed')
        await expect(win2.locator('.nd-headline')).to_have_text(FAILED)
        await expect(page.locator('.nd-attn')).to_contain_text('2 failed')
        # the drawer counts the queued worker as idle and keeps every reason in the transcript
        s.srv.bus.publish(_activity('r-5', 'writer', 'status', status='queued', text=QUEUED))
        await page.locator('#nd-drawer-btn').click()
        await expect(page.locator('#nd-filters button')).to_have_text(['All 5', 'Needs you 0', 'Working 2', 'Failed or stopped 2', 'Idle 1'])
        await page.locator('#nd-dlist .nd-item[data-run-id="r-1"]').click()
        entries = page.locator('#nd-detail .nd-dbody .nd-entry')
        await expect(entries).to_have_count(4)
        await expect(entries.nth(2)).to_contain_text(WAITING)
        await expect(entries.nth(3)).to_contain_text(FREED)
        assert s.errors == []


async def test_the_top_row_says_how_many_lanes_are_busy(studio):
    async with studio(lanes={'served': 2, 'busy': 1, 'queued': 0}) as s:
        page = await open_nested(s)
        lanes_line = page.locator('#nd-lanes')
        await expect(lanes_line).to_have_text('lanes: 1 of 2 busy')                             # from hello.lanes
        s.srv.bus.publish(Event('lanes', {'served': 2, 'busy': 2, 'queued': 3}))                  # then from each lanes event
        await expect(lanes_line).to_have_text('lanes: 2 of 2 busy · 3 queued')
        s.srv.bus.publish(Event('lanes', {'served': 2, 'busy': 0, 'queued': 0}))
        await expect(lanes_line).to_have_text('lanes: 0 of 2 busy')
        s.srv.bus.publish(Event('lanes', {'served': 'two', 'busy': 1}))                           # malformed: ignored, the last good one stays
        await expect(lanes_line).to_have_text('lanes: 0 of 2 busy')
        await s.page.reload()
        await expect(s.page.locator('#stat')).to_have_text('Ready')
        page = await open_nested(s)
        await expect(page.locator('#nd-lanes')).to_have_text('lanes: 1 of 2 busy')                # hello again, the events are not history
        assert s.errors == []
    async with studio() as s:                                                                       # no lanes in hello: nothing said
        page = await open_nested(s)
        await expect(page.locator('#nd-lanes')).to_have_text('')
        assert s.errors == []
