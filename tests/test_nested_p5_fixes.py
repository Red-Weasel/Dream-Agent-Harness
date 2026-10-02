"""The P5 UI gate's findings on 937a758 (gate-reports/a2-r1.md), each shown failing first: a control response never
overwrites a status row that arrived while it was in flight (held responses, and the real Studio server, whose terminal
row reaches the page before the response); the keyboard's place after an action (restored only when it was lost, after
every view re-rendered, to the lane when the control ends disabled, Pause all's own button, never taken from the
composer); header controls that wrap instead of clipping; one persistent live region for refusals; a queued worker's
pause and resume; a pausing row keeps a requested tool. No model, no engine."""
from __future__ import annotations

import asyncio
import contextlib

import pytest
from playwright.async_api import async_playwright, expect

from dream.core.backends.base import Event
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from test_nested_drawer import ENDINGS, lanes
from test_nested_view import _activity, open_nested, studio  # noqa: F401

pytestmark = pytest.mark.asyncio
NOT_RUNNING = "No running worker has run id 'r-1' in this session: it has finished, or it never ran here."
LONG = 'a-very-long-worker-name-that-keeps-going-and-going'
COLOUR = 'e => getComputedStyle(e).backgroundColor'


def result(run_id, state):
    return {'run_id': run_id, 'agent': 'writer', 'state': state, 'changed': True}


async def held_control(s, bodies):
    """/api/control whose answers wait for the test: each request takes the next (status, payload) put on the queue.
    Every request carries the session's own token."""
    replies = asyncio.Queue()

    async def handle(route, request):
        assert request.headers.get('x-dream-token') == s.srv.token, 'the control request must carry the session token'
        bodies.append(request.post_data_json)
        status, payload = await replies.get()
        await route.fulfill(status=status, json=payload)
    await s.page.route('**/api/control', handle)
    return replies


async def settle(page):
    await page.wait_for_timeout(300)          # a response the page might still apply


# --- 1. the race: a status row that beat the response is newer than the response --------------------------------

async def test_a_late_response_never_overwrites_a_newer_status_row(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 3, {})
        bodies = []
        replies = await held_control(s, bodies)
        w1, w2, card = (page.locator('.nd-win[data-run-id="r-1"]'), page.locator('.nd-win[data-run-id="r-2"]'),
                        page.locator('.nd-card[data-run-id="r-3"]'))
        # Stop: the terminal row lands first, then {state: stopping}
        await w1.locator('[data-ctl="agent_stop"]').click()
        await expect(w1.locator('[data-ctl="agent_stop"]')).to_be_disabled()
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='interrupted', text='Stopped by you during request 1.'))
        await expect(w1.locator('.nd-state')).to_have_text('Stopped')
        await replies.put((200, {'ok': True, 'result': result('r-1', 'stopping')}))
        await settle(s.page)
        await expect(w1.locator('.nd-state')).to_have_text('Stopped')
        await expect(w1.locator('.nd-headline')).to_have_text('Stopped by you during request 1.')
        for b in await w1.locator('[data-ctl]').all():
            await expect(b).to_be_disabled()
        # Pause: the pausing and paused rows land first, then {state: pausing}
        await w2.locator('.nd-ctl-pause').click()
        s.srv.bus.publish(_activity('r-2', 'writer', 'status', status='pausing', text='Pauses after its current request.'))
        s.srv.bus.publish(_activity('r-2', 'writer', 'status', status='paused', text='Paused by you before request 2; it goes on when you resume it.'))
        await expect(w2.locator('.nd-state')).to_have_text('Paused')
        await replies.put((200, {'ok': True, 'result': result('r-2', 'pausing')}))
        await settle(s.page)
        await expect(w2.locator('.nd-state')).to_have_text('Paused')
        # Resume: the worker finished before the answer came
        await expect(w2.locator('.nd-ctl-pause')).to_have_text('Resume')
        await w2.locator('.nd-ctl-pause').click()
        s.srv.bus.publish(_activity('r-2', 'writer', 'status', status='completed', text=ENDINGS['completed']))
        await expect(w2.locator('.nd-state')).to_have_text('Done')
        await replies.put((200, {'ok': True, 'result': result('r-2', 'running')}))
        await settle(s.page)
        await expect(w2.locator('.nd-state')).to_have_text('Done')
        for b in await w2.locator('[data-ctl]').all():
            await expect(b).to_be_disabled()
        # Pause all: a run that ended meanwhile keeps its end
        await page.locator('#nd-pause-all').click()
        s.srv.bus.publish(_activity('r-3', 'writer', 'status', status='completed', text=ENDINGS['completed']))
        await expect(card.locator('.nd-state')).to_have_text('Done')
        await replies.put((200, {'ok': True, 'result': {'runs': [result('r-3', 'pausing')]}}))
        await settle(s.page)
        await expect(card.locator('.nd-state')).to_have_text('Done')
        # the turn's end leaves the stopped worker its own words
        s.srv.bus.publish(Event('turn_end', {}))
        await settle(s.page)
        await expect(w1.locator('.nd-headline')).to_have_text('Stopped by you during request 1.')
        assert [b['action'] for b in bodies] == ['agent_stop', 'agent_pause', 'agent_resume', 'agents_pause_all']
        assert s.errors == []


@pytest.mark.parametrize('busy_ms', [0, 30])
async def test_on_the_real_server_every_stopped_worker_ends_stopped(monkeypatch, busy_ms):
    """The real Studio server (Starlette, uvicorn, the websocket), stopping the way the backend does: cancel the run's
    task, answer {state: stopping} at once, and let the run publish its terminal row as it unwinds -- which reaches the
    page before the response. Adapted from the gate's probe (tests/test_gate_a2_race.py in its scratch)."""
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    bus, tasks = EventBus(), {}

    async def run(run_id):
        try:
            await asyncio.sleep(3600)                                  # a request in flight
        except asyncio.CancelledError:
            bus.publish(_activity(run_id, 'writer', 'status', status='interrupted', text='Stopped by you during request 1.'))
            raise

    def on_control(payload):
        if payload.get('action') == 'agent_stop':
            tasks[payload['run_id']].cancel()                          # like the worker's scope: its row comes as it unwinds
            return result(payload['run_id'], 'stopping')
        return {'mode': 'ask', 'modes': ['ask'], 'labels': {'ask': 'Ask'}}

    srv = StudioServer(bus, on_prompt=lambda *_: None, on_control=on_control,
                       session={'provider': 'MachX', 'model': 'fixture', 'session_id': f'race-{busy_ms}', 'lanes': None})
    url = await srv.start()
    stale = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': 1600, 'height': 1000})
            page.set_default_timeout(5000)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            await page.goto(url.replace('/#', '/?companion=1#'))
            await expect(page.locator('#stat')).to_have_text('Ready')
            await page.locator('#dream-nav-nested').click()
            nested = page.locator('#dream-nested-page')
            for i in range(1, 7):
                rid = f'r-{i}'
                tasks[rid] = asyncio.create_task(run(rid))
                bus.publish(_activity(rid, 'writer', 'status', status='running', text='Subagent started.'))
                bus.publish(_activity(rid, 'writer', 'request', status='awaiting_response', request_index=1))
                lane = nested.locator(f'.nd-win[data-run-id="{rid}"], .nd-card[data-run-id="{rid}"]')
                await expect(lane.locator('.nd-state')).to_have_text('Working')
                if busy_ms:   # the page's main thread busy for busy_ms just after the request leaves (a heavy render)
                    await page.evaluate("""ms => { const f = window.fetch; window.fetch = function(u) { const p = f.apply(this, arguments);
                        if (String(u).includes('/api/control')) { window.fetch = f; setTimeout(() => { const t = performance.now(); while (performance.now() - t < ms); }, 0); }
                        return p; }; }""", busy_ms)
                await lane.locator('[data-ctl="agent_stop"]').click()
                await page.wait_for_timeout(250)
                if await lane.locator('.nd-state').text_content() != 'Stopped':
                    stale.append((rid, await lane.locator('.nd-state').text_content(), await lane.locator('.nd-headline').text_content()))
            assert errors == []
            await browser.close()
    finally:
        for t in tasks.values():
            t.cancel()
        with contextlib.suppress(Exception):
            await srv.stop()
    assert not stale, stale


async def test_pause_all_keeps_a_row_that_arrived_while_it_was_in_flight(studio):
    """The gate's round 2, finding 3 (R4): Pause all notes each lane's row count when its request leaves, so a worker
    whose pausing and paused rows land before the answer stays Paused; the answer's `pausing` is older."""
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        bodies = []
        replies = await held_control(s, bodies)
        w1, w2 = page.locator('.nd-win[data-run-id="r-1"]'), page.locator('.nd-win[data-run-id="r-2"]')
        await page.locator('#nd-pause-all').click()
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='pausing', text='Pauses after its current request.'))
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='paused', text='Paused by you before request 2; it goes on when you resume it.'))
        await expect(w1.locator('.nd-state')).to_have_text('Paused')
        await replies.put((200, {'ok': True, 'result': {'runs': [result('r-1', 'pausing'), result('r-2', 'pausing')]}}))
        await expect(w2.locator('.nd-state')).to_have_text('Pausing')                 # the lane with no newer row takes it
        await settle(s.page)
        await expect(w1.locator('.nd-state')).to_have_text('Paused')
        assert bodies == [{'action': 'agents_pause_all'}]
        assert s.errors == []


# --- 2. the keyboard's place ---------------------------------------------------------------------------------------

async def test_the_keyboard_keeps_its_place_after_an_action(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 5, {})
        bodies = []
        replies = await held_control(s, bodies)
        win, card = page.locator('.nd-win[data-run-id="r-1"]'), page.locator('.nd-card[data-run-id="r-3"]')
        # Enter on a window's Stop: the Stop ends disabled (Stopping), so its window holds the focus
        await win.locator('[data-ctl="agent_stop"]').focus()
        await s.page.keyboard.press('Enter')
        await replies.put((200, {'ok': True, 'result': result('r-1', 'stopping')}))
        await expect(win.locator('.nd-state')).to_have_text('Stopping')
        await expect(win).to_be_focused()
        # a card's Stop: the card
        await card.locator('[data-ctl="agent_stop"]').focus()
        await s.page.keyboard.press('Enter')
        await replies.put((200, {'ok': True, 'result': result('r-3', 'stopping')}))
        await expect(card.locator('.nd-state')).to_have_text('Stopping')
        await expect(card).to_be_focused()
        # the drawer's detail: Pause keeps the focus on its button (Resume now), after the drawer re-rendered
        await page.locator('.nd-pips .nd-pip[data-run-id="r-2"]').click()
        detail = page.locator('#nd-detail')
        await expect(detail).to_have_attribute('data-run-id', 'r-2')
        await detail.locator('.nd-ctl-pause').focus()
        await s.page.keyboard.press('Enter')
        await replies.put((200, {'ok': True, 'result': result('r-2', 'pausing')}))
        await expect(detail.locator('.nd-ctl-pause')).to_have_text('Resume')
        await expect(detail.locator('.nd-ctl-pause')).to_be_focused()
        # ...and its Stop, ending disabled, gives the focus to Pin beside it
        await detail.locator('[data-ctl="agent_stop"]').focus()
        await s.page.keyboard.press('Enter')
        await replies.put((200, {'ok': True, 'result': result('r-2', 'stopping')}))
        await expect(detail.locator('.nd-dtop .nd-state')).to_have_text('Stopping')
        await expect(page.locator('#nd-pin-btn')).to_be_focused()
        await s.page.keyboard.press('Escape')
        await s.page.keyboard.press('Escape')
        # Pause all: its own button while something can still pause, All agents once nothing can
        pause_all = page.locator('#nd-pause-all')
        await pause_all.focus()
        await s.page.keyboard.press('Enter')
        await replies.put((200, {'ok': True, 'result': {'runs': [result('r-4', 'pausing')]}}))
        await expect(page.locator('.nd-card[data-run-id="r-4"] .nd-state')).to_have_text('Pausing')
        await expect(pause_all).to_be_enabled()                                       # r-5 can still pause
        await expect(pause_all).to_be_focused()
        await s.page.keyboard.press('Enter')
        await replies.put((200, {'ok': True, 'result': {'runs': [result('r-5', 'pausing')]}}))
        await expect(pause_all).to_be_disabled()
        await expect(page.locator('#nd-drawer-btn')).to_be_focused()
        # a held answer never takes the focus from the composer: the next typed space stays a space
        s.srv.bus.publish(_activity('r-6', 'writer', 'status', status='running', text='Subagent started.'))
        six = page.locator('.nd-card[data-run-id="r-6"]')
        await six.locator('.nd-ctl-pause').click()
        compose = page.locator('#nd-compose')
        await compose.click()
        await s.page.keyboard.type('note')
        await replies.put((200, {'ok': True, 'result': result('r-6', 'pausing')}))
        await expect(six.locator('.nd-state')).to_have_text('Pausing')
        await expect(compose).to_be_focused()
        await s.page.keyboard.type(' more')
        await expect(compose).to_have_value('note more')
        await settle(s.page)
        assert [b['action'] for b in bodies].count('agent_resume') == 0, bodies
        assert s.errors == []


# --- 3. header controls wrap instead of clipping --------------------------------------------------------------------

FIT = """p => [...p.querySelectorAll('.nd-win[data-run-id]')].map(w => { const r = w.getBoundingClientRect(), h = w.querySelector('.nd-whead');
  return {run: w.dataset.runId, head: [h.scrollWidth, h.clientWidth],
          out: [...h.querySelectorAll('button')].map(b => { const q = b.getBoundingClientRect(); return {label: b.getAttribute('aria-label') || b.textContent, left: q.left - r.left, right: r.right - q.right}; })
                 .filter(b => b.left < -0.5 || b.right < -0.5)}; })"""


@pytest.mark.parametrize('width,larger', [(901, False), (960, False), (960, True), (1100, True)])
async def test_the_window_header_controls_wrap_instead_of_clipping(studio, width, larger):
    async with studio(width=width, height=900) as s:
        page = await open_nested(s)
        for i in (1, 2):
            s.srv.bus.publish(_activity(f'r-{i}', LONG, 'status', status='running', text='Subagent started.'))
            s.srv.bus.publish(_activity(f'r-{i}', LONG, 'request', status='awaiting_response', request_index=1,
                                        model='fixture-model-with-a-long-name', context={'used': 4096, 'window': 32768}))
        await expect(page.locator('.nd-win[data-run-id]')).to_have_count(2)
        if larger:
            await page.locator('#nd-custom-btn').click()
            await page.locator('#nd-custom [data-opt="fz"]').get_by_role('button', name='Larger', exact=True).click()
            await page.locator('#nd-custom-close').click()
        fits = await page.evaluate(FIT)
        for f in fits:
            assert f['out'] == [] and f['head'][0] <= f['head'][1] + 1, (width, larger, f)
        assert s.errors == []


# --- 5. one persistent live region announces a refusal; renders stay quiet -----------------------------------------

async def test_a_refusal_is_announced_once_and_renders_insert_no_alerts(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        bodies = []
        replies = await held_control(s, bodies)
        await s.page.evaluate("""() => { window.__alerts = 0; new MutationObserver(ms => { for (const m of ms) for (const n of m.addedNodes)
            if (n.nodeType === 1 && (n.matches('[role=alert]') || n.querySelector('[role=alert]'))) window.__alerts++; })
            .observe(document.getElementById('dream-nested-page'), {childList: true, subtree: true}); }""")
        win = page.locator('.nd-win[data-run-id="r-1"]')
        await win.locator('[data-ctl="agent_stop"]').click()
        await replies.put((400, {'error': NOT_RUNNING}))
        await expect(win.locator('.nd-note')).to_have_text(NOT_RUNNING)              # shown on its lane, verbatim
        await expect(page.locator('#nd-live')).to_have_text(NOT_RUNNING)             # announced by the one live region
        await expect(page.locator('#nd-live')).to_have_attribute('role', 'alert')
        await expect(page.locator('.nd-note[role=alert]')).to_have_count(0)
        await s.page.evaluate('window.__alerts = 0')
        for i in range(10):                                                           # ten renders of another lane
            s.srv.bus.publish(_activity('r-2', 'writer', 'text_delta', request_index=2, text=f'word {i} '))
            await s.page.wait_for_timeout(40)
        await expect(page.locator('.nd-win[data-run-id="r-2"] .nd-stream')).to_contain_text('word 9')
        assert await s.page.evaluate('window.__alerts') == 0
        await expect(win.locator('.nd-note')).to_have_text(NOT_RUNNING)
        assert s.errors == []


# --- 6. a queued worker's pause and resume; a pausing row keeps a requested tool ----------------------------------

async def test_a_queued_worker_paused_stays_pewter_and_resumed_stays_queued(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        for rid in ('r-q', 'r-p'):
            s.srv.bus.publish(_activity(rid, 'writer', 'status', status='queued', text='Waiting for an engine lane (2 of 2 busy)'))
        bodies = []
        replies = await held_control(s, bodies)
        card, pip, other = (page.locator('.nd-card[data-run-id="r-q"]'), page.locator('.nd-pips .nd-pip[data-run-id="r-q"]'),
                            page.locator('.nd-pips .nd-pip[data-run-id="r-p"]'))
        await expect(card.locator('.nd-state')).to_have_text('Queued')
        await card.locator('.nd-ctl-pause').click()
        await replies.put((200, {'ok': True, 'result': result('r-q', 'pausing')}))
        await expect(card.locator('.nd-state')).to_have_text('Pausing')
        await expect(pip).to_have_attribute('aria-label', '3 writer: Pausing')
        assert await pip.evaluate(COLOUR) == await other.evaluate(COLOUR)           # pewter like a queued pip, not working green
        s.srv.bus.publish(_activity('r-q', 'writer', 'status', status='pausing', text='Pauses before its first request.'))
        await expect(card.locator('.nd-headline')).to_have_text('Pauses before its first request.')
        assert await pip.evaluate(COLOUR) == await other.evaluate(COLOUR)
        await card.locator('.nd-ctl-pause').click()                                  # Resume, still waiting for a lane
        await replies.put((200, {'ok': True, 'result': result('r-q', 'running')}))
        await expect(card.locator('.nd-state')).to_have_text('Queued')
        s.srv.bus.publish(_activity('r-q', 'writer', 'status', status='running', text='Resumed by you.'))
        await settle(s.page)
        await expect(card.locator('.nd-state')).to_have_text('Queued')
        await expect(pip).to_have_attribute('aria-label', '3 writer: Queued')
        s.srv.bus.publish(_activity('r-q', 'writer', 'status', status='running', text='Subagent started.'))   # it takes a lane
        await expect(card.locator('.nd-state')).to_have_text('Working')
        assert await pip.evaluate(COLOUR) != await other.evaluate(COLOUR)
        assert s.errors == []


async def test_a_pausing_row_keeps_a_requested_tool(studio):
    async with studio() as s:
        page = await open_nested(s)
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='running', text='Subagent started.'))
        s.srv.bus.publish(_activity('r-1', 'writer', 'tool_use', status='requested',
                                    data={'id': 'r-1:1:0:t', 'name': 'read_file', 'input': {'path': 'a.md'}}))
        win = page.locator('.nd-win[data-run-id="r-1"]')
        await expect(win.locator('.nd-tool .nd-badge')).to_have_text('Requested')
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='pausing', text='Pauses after its current request.'))
        await expect(win.locator('.nd-state')).to_have_text('Pausing')
        await settle(s.page)
        await expect(win.locator('.nd-tool .nd-badge')).to_have_text('Requested')    # its call is still running
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='interrupted', text='Stopped by you.'))
        await expect(win.locator('.nd-tool .nd-badge')).to_have_text('Interrupted')
        assert s.errors == []
