"""Nested Dream P5 (DREAM-192 UI): Stop, Pause/Resume and Pause all over a scripted /api/control (the documented
shapes and refusals from NESTED_EVENTS.md, section P5), each shown failing first. A lane shows the server's answer
only: nothing before the response, the response's state, then the status rows; a refusal verbatim on that lane; the
controls off while a request is in flight and on a finished lane."""
from __future__ import annotations

import asyncio

import pytest
from playwright.async_api import expect

from test_nested_drawer import lanes
from test_nested_view import _activity, open_nested, studio  # noqa: F401

pytestmark = pytest.mark.asyncio
UNAVAILABLE = ('Worker controls are unavailable: this session has no backend with workers yet, '
               'or its provider does not report its workers.')
NOT_RUNNING = "No running worker has run id 'r-1' in this session: it has finished, or it never ran here."
NOTHING_TO_PAUSE = 'No worker is running in this session; there is nothing to pause.'


def result(run_id, state):
    return {'run_id': run_id, 'agent': 'writer', 'state': state, 'changed': True}


async def fake_control(s, bodies, respond):
    """/api/control answered by `respond(body) -> (status, payload)`; every request must carry the session token."""
    async def handle(route, request):
        assert request.headers.get('x-dream-token') == s.srv.token, 'the control request must carry the session token'
        body = request.post_data_json
        bodies.append(body)
        status, payload = await respond(body)
        await route.fulfill(status=status, json=payload)
    await s.page.route('**/api/control', handle)


async def test_stop_takes_a_click_shows_the_servers_state_and_then_the_row(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        await expect(page.locator('.nd-win[data-run-id]')).to_have_count(2)
        bodies, gate = [], asyncio.Event()

        async def respond(body):
            await gate.wait()
            return 200, {'ok': True, 'result': result(body['run_id'], 'stopping')}
        await fake_control(s, bodies, respond)
        win = page.locator('.nd-win[data-run-id="r-1"]')
        stop = win.locator('[data-ctl="agent_stop"]')
        await stop.hover()
        await asyncio.sleep(0.2)
        assert bodies == []                                                    # hovering does nothing
        await stop.click()
        await expect(stop).to_be_disabled()                                    # in flight: off, and nothing claimed yet
        await expect(win.locator('.nd-ctl-pause')).to_be_disabled()
        await expect(win.locator('.nd-state')).to_have_text('Working')
        assert bodies == [{'action': 'agent_stop', 'run_id': 'r-1'}]
        gate.set()
        await expect(win.locator('.nd-state')).to_have_text('Stopping')       # the server's word
        await expect(stop).to_be_disabled()
        await expect(page.locator('.nd-pips .nd-pip[data-run-id="r-1"]')).to_have_attribute('title', '1 writer: Stopping')
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='interrupted', text='Stopped by you during request 1.'))
        await expect(win.locator('.nd-state')).to_have_text('Stopped')
        await expect(win.locator('.nd-headline')).to_have_text('Stopped by you during request 1.')
        await expect(win.locator('[data-ctl]')).to_have_count(2)
        for b in await win.locator('[data-ctl]').all():
            await expect(b).to_be_disabled()
        # the keyboard: Enter on a card's Stop posts the same
        s.srv.bus.publish(_activity('r-3', 'writer', 'status', status='running', text='Subagent started.'))
        card = page.locator('.nd-card[data-run-id="r-3"]')
        await card.locator('[data-ctl="agent_stop"]').focus()
        await s.page.keyboard.press('Enter')
        await expect(card.locator('.nd-state')).to_have_text('Stopping')
        assert bodies[-1] == {'action': 'agent_stop', 'run_id': 'r-3'}
        assert s.errors == []


async def test_pause_and_resume_follow_the_servers_state_and_the_rows(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {'r-2': 'completed'})
        win = page.locator('.nd-win[data-run-id="r-1"]')
        await expect(page.locator('.nd-win[data-run-id="r-2"] .nd-state')).to_have_text('Done')
        bodies, gate = [], asyncio.Event()

        async def respond(body):
            await gate.wait()
            state = {'agent_pause': 'pausing', 'agent_resume': 'running'}[body['action']]
            return 200, {'ok': True, 'result': result(body['run_id'], state)}
        await fake_control(s, bodies, respond)
        pause = win.locator('.nd-ctl-pause')
        await expect(pause).to_have_text('Pause')
        await pause.focus()
        await s.page.keyboard.press('Enter')
        await expect(pause).to_be_disabled()                                   # in flight: nothing claimed yet
        await asyncio.sleep(0.2)
        await expect(win.locator('.nd-state')).to_have_text('Working')
        await expect(pause).to_have_text('Pause')
        gate.set()
        await expect(win.locator('.nd-state')).to_have_text('Pausing')
        await expect(pause).to_have_text('Resume')
        await expect(pause).to_be_focused()                                    # the keyboard keeps its place
        assert bodies == [{'action': 'agent_pause', 'run_id': 'r-1'}]
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='pausing', text='Pauses after its current request.'))
        await expect(win.locator('.nd-headline')).to_have_text('Pauses after its current request.')
        await expect(win.locator('.nd-state')).to_have_text('Pausing')
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='paused',
                                    text='Paused by you before request 2; it goes on when you resume it.'))
        await expect(win.locator('.nd-state')).to_have_text('Paused')
        await expect(win.locator('.nd-headline')).to_have_text('Paused by you before request 2; it goes on when you resume it.')
        pip, done = page.locator('.nd-pips .nd-pip[data-run-id="r-1"]'), page.locator('.nd-pips .nd-pip[data-run-id="r-2"]')
        await expect(pip).to_have_attribute('title', '1 writer: Paused')
        colour = 'e => getComputedStyle(e).backgroundColor'
        assert await pip.evaluate(colour) == await done.evaluate(colour)      # pewter, like Done; never the Verifying yellow
        await expect(pause).to_have_text('Resume')
        await pause.click()
        await expect(win.locator('.nd-state')).to_have_text('Working')
        await expect(pause).to_have_text('Pause')
        assert bodies[-1] == {'action': 'agent_resume', 'run_id': 'r-1'}
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='running', text='Resumed by you.'))
        await expect(win.locator('.nd-headline')).to_have_text('Resumed by you.')
        # the drawer's detail offers the same controls
        await pip.click()
        detail = page.locator('#nd-detail')
        await expect(detail).to_be_visible()
        await detail.locator('.nd-ctl-pause').click()
        await expect(detail.locator('.nd-dtop .nd-state')).to_have_text('Pausing')
        await expect(win.locator('.nd-state')).to_have_text('Pausing')
        assert bodies[-1] == {'action': 'agent_pause', 'run_id': 'r-1'}
        assert s.errors == []


async def test_pause_all_is_offered_while_a_worker_can_pause_and_applies_every_result(studio):
    async with studio() as s:
        page = await open_nested(s)
        btn = page.locator('#nd-pause-all')
        await expect(btn).to_be_disabled()                                     # nothing to pause yet
        lanes(s, 3, {'r-3': 'completed'})
        await expect(page.locator('.nd-card[data-run-id="r-3"] .nd-state')).to_have_text('Done')
        await expect(btn).to_be_enabled()
        bodies, gate = [], asyncio.Event()

        async def respond(body):
            await gate.wait()
            return 200, {'ok': True, 'result': {'runs': [result('r-1', 'pausing'), result('r-2', 'pausing')]}}
        await fake_control(s, bodies, respond)
        await btn.click()
        await expect(btn).to_be_disabled()                                     # in flight: nothing claimed yet
        await asyncio.sleep(0.2)
        for run in ('r-1', 'r-2'):
            await expect(page.locator(f'.nd-win[data-run-id="{run}"] .nd-state')).to_have_text('Working')
        gate.set()
        for run in ('r-1', 'r-2'):
            await expect(page.locator(f'.nd-win[data-run-id="{run}"] .nd-state')).to_have_text('Pausing')
        assert bodies == [{'action': 'agents_pause_all'}]
        await expect(page.locator('.nd-card[data-run-id="r-3"] .nd-state')).to_have_text('Done')   # untouched
        await expect(btn).to_be_disabled()                                     # every live worker is pausing already
        for run in ('r-1', 'r-2'):
            s.srv.bus.publish(_activity(run, 'writer', 'status', status='paused',
                                        text='Paused by you before request 2; it goes on when you resume it.'))
        await expect(page.locator('.nd-win[data-run-id="r-2"] .nd-state')).to_have_text('Paused')
        s.srv.bus.publish(_activity('r-4', 'writer', 'status', status='queued', text='Waiting for a free lane.'))
        await expect(btn).to_be_enabled()                                      # a queued worker can pause
        assert s.errors == []


async def test_a_refusal_shows_verbatim_on_that_lane_and_changes_nothing(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 3, {})
        await expect(page.locator('.nd-card[data-run-id="r-3"]')).to_be_visible()
        bodies = []

        async def respond(body):
            return 400, {'error': {'agent_stop': NOT_RUNNING, 'agent_pause': UNAVAILABLE,
                                   'agents_pause_all': NOTHING_TO_PAUSE}[body['action']]}
        await fake_control(s, bodies, respond)
        win = page.locator('.nd-win[data-run-id="r-1"]')
        await win.locator('[data-ctl="agent_stop"]').click()
        await expect(win.locator('.nd-note')).to_have_text(NOT_RUNNING)
        await expect(win.locator('.nd-state')).to_have_text('Working')
        await expect(win.locator('[data-ctl="agent_stop"]')).to_be_enabled()
        await expect(page.locator('.nd-win[data-run-id="r-2"] .nd-note')).to_have_count(0)   # that lane only
        card = page.locator('.nd-card[data-run-id="r-3"]')
        await card.locator('.nd-ctl-pause').click()
        await expect(card.locator('.nd-note')).to_have_text(UNAVAILABLE)
        await page.locator('.nd-pips .nd-pip[data-run-id="r-3"]').click()
        await expect(page.locator('#nd-detail .nd-note')).to_have_text(UNAVAILABLE)          # the drawer shows it too
        await page.locator('#nd-detail-close').click()
        await page.locator('#nd-pause-all').click()
        await expect(page.locator('#nd-ctl-note')).to_have_text(NOTHING_TO_PAUSE)             # Pause all: in the top row
        # a status row from the server outdates the note; a network failure has its own line
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='running', text='Still going.'))
        await expect(win.locator('.nd-note')).to_have_count(0)
        await s.page.unroute('**/api/control')

        async def abort(route, request):
            await route.abort()
        await s.page.route('**/api/control', abort)
        await win.locator('[data-ctl="agent_stop"]').click()
        await expect(win.locator('.nd-note')).to_have_text('Could not reach Dream. Reconnect and try again.')
        await expect(win.locator('.nd-state')).to_have_text('Working')
        assert len(bodies) == 3 and s.errors == []                         # the aborted request never reached the fake


async def test_finished_lanes_keep_no_live_controls(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 3, {'r-1': 'completed', 'r-2': 'failed', 'r-3': 'interrupted'})
        await expect(page.locator('.nd-card[data-run-id="r-3"] .nd-state')).to_have_text('Stopped')
        bodies = []

        async def respond(body):
            return 200, {'ok': True, 'result': result(body.get('run_id'), 'stopping')}
        await fake_control(s, bodies, respond)
        ctls = page.locator('.nd-win [data-ctl], .nd-card [data-ctl]')
        await expect(ctls).to_have_count(6)
        for b in await ctls.all():
            await expect(b).to_be_disabled()
            await b.click(force=True)                                           # a disabled control posts nothing
        await expect(page.locator('#nd-pause-all')).to_be_disabled()
        await page.locator('.nd-pips .nd-pip[data-run-id="r-1"]').click()
        for b in await page.locator('#nd-detail [data-ctl]').all():
            await expect(b).to_be_disabled()
        await asyncio.sleep(0.2)
        assert bodies == [] and s.errors == []
