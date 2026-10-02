"""The P4 UI gate's known limits (PASS on 8087233), each shown failing first: a worker still live when its turn ends
is settled as Stopped with a plain headline and its pending tools marked, and a later terminal row still overrides
it; a queued card says its reason once; a malformed `lanes` event is ignored rather than read as zero."""
from __future__ import annotations

import pytest
from playwright.async_api import expect

from dream.core.backends.base import Event
from test_nested_drawer import lanes
from test_nested_view import _activity, open_nested, studio  # noqa: F401

pytestmark = pytest.mark.asyncio
ENDED = 'The turn ended without a final report from this worker'


async def test_the_turns_end_settles_every_live_worker_and_a_later_row_still_overrides(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {'r-2': 'completed'})
        s.srv.bus.publish(_activity('r-3', 'writer', 'status', status='queued', text='Waiting for a free lane.'))
        s.srv.bus.publish(_activity('r-4', 'writer', 'status', status='running', text='Subagent started.'))
        s.srv.bus.publish(_activity('r-4', 'writer', 'request', status='awaiting_response', request_index=1))
        s.srv.bus.publish(_activity('r-4', 'writer', 'tool_use', status='requested',
                                    data={'id': 'r-4:1:0:t', 'name': 'run_bash', 'input': {'cmd': 'pytest'}}))
        card = page.locator('.nd-card[data-run-id="r-4"]')
        await expect(card.locator('.nd-cbody')).to_contain_text('run_bash · Requested')
        s.srv.bus.publish(Event('turn_end', {}))
        for run in ('r-1', 'r-3', 'r-4'):
            lane = page.locator(f'.nd-win[data-run-id="{run}"], .nd-card[data-run-id="{run}"]')
            await expect(lane.locator('.nd-state')).to_have_text('Stopped')
            await expect(lane.locator('.nd-headline')).to_have_text(ENDED)
        await expect(page.locator('.nd-win[data-run-id="r-2"] .nd-state')).to_have_text('Done')      # finished stays as it was
        await expect(card.locator('.nd-cbody')).to_contain_text('run_bash · No result reported')
        await expect(page.locator('.nd-pips .nd-pip[data-run-id="r-3"]')).to_have_attribute('title', '3 writer: Stopped')
        late = 'Subagent finished late; its result was returned to the lead agent.'
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='completed', text=late))
        await expect(page.locator('.nd-win[data-run-id="r-1"] .nd-state')).to_have_text('Done')
        await expect(page.locator('.nd-win[data-run-id="r-1"] .nd-headline')).to_have_text(late)
        # the drawer's transcript records the settling
        await page.locator('.nd-pips .nd-pip[data-run-id="r-3"]').click()
        await expect(page.locator('#nd-detail .nd-dbody')).to_contain_text(ENDED)
        assert s.errors == []


async def test_a_queued_card_says_its_reason_once(studio):
    async with studio() as s:
        page = await open_nested(s)
        for i in (1, 2, 3):
            s.srv.bus.publish(_activity(f'r-{i}', 'writer', 'status', status='queued', text='Waiting for a free lane.'))
        card = page.locator('.nd-card[data-run-id="r-3"]')
        await expect(card.locator('.nd-headline')).to_have_text('Waiting for a free lane.')
        await expect(card.locator('.nd-cbody')).to_have_text('')
        s.srv.bus.publish(_activity('r-3', 'writer', 'status', status='running', text='Subagent started.'))
        s.srv.bus.publish(_activity('r-3', 'writer', 'request', status='awaiting_response', request_index=1))
        await expect(card.locator('.nd-cbody')).to_have_text('Request 1 · waiting for the reply')
        await expect(card.locator('.nd-headline')).to_have_text('Subagent started.')
        assert s.errors == []


async def test_a_malformed_lanes_event_is_ignored(studio):
    async with studio(lanes={'served': 0, 'busy': 0}) as s:
        page = await open_nested(s)
        line = page.locator('#nd-lanes')
        await expect(line).to_have_text('')                                                       # no lane served: nothing to say
        s.srv.bus.publish(Event('lanes', {'served': 4, 'busy': 2, 'queued': 1}))
        await expect(line).to_have_text('lanes: 2 of 4 busy · 1 queued')
        for bad in ({'served': 4, 'busy': 5, 'queued': 0}, {'served': 4, 'busy': 2, 'queued': 'many'},
                    {'served': 4, 'busy': 2, 'queued': -1}, {'served': 0, 'busy': 0, 'queued': 0}, {'busy': 1}, 'lanes'):
            s.srv.bus.publish(Event('lanes', bad))
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='running', text='Subagent started.'))   # a render after them
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(1)
        await expect(line).to_have_text('lanes: 2 of 4 busy · 1 queued')
        s.srv.bus.publish(Event('lanes', {'served': 4, 'busy': 3}))                              # queued absent: none
        await expect(line).to_have_text('lanes: 3 of 4 busy')
        assert s.errors == []
