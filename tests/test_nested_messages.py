"""Nested Dream P6 (DREAM-193 UI): a message to one worker, over the contract in NESTED_EVENTS.md, section P6. A small
composer in each pinned window and in the drawer's detail POSTs /api/control {action: agent_message, run_id, text}
with the token; the answer's `pending` shows as "N waiting" until the worker's message_received / message_undelivered
rows arrive, which the lane's transcript keeps with their preview; a refusal is said verbatim and the draft stays; the
box is off on a finished or stopping worker; the 4,000-character limit shows before sending, counted as the server
counts. The composer is one node per lane and place, so a draft survives the stream of events around it. Scripted
events and a scripted /api/control; no model, no engine."""
from __future__ import annotations

import asyncio

import pytest
from playwright.async_api import expect

from dream.core.backends.base import Event
from test_nested_drawer import ENDINGS, lanes
from test_nested_view import _activity, open_nested, studio  # noqa: F401

pytestmark = pytest.mark.asyncio
STOPPING = "Worker 'writer' is stopping; the message was not delivered."


def accepted(run_id, pending):
    return 200, {'ok': True, 'result': {'run_id': run_id, 'agent': 'writer', 'state': 'running', 'pending': pending}}


async def held_control(s, bodies):
    """/api/control whose answers wait for the test: each request takes the next (status, payload) off the queue.
    Every request carries the session's own token."""
    replies = asyncio.Queue()

    async def handle(route, request):
        assert request.headers.get('x-dream-token') == s.srv.token, 'a message must carry the session token'
        bodies.append(request.post_data_json)
        status, payload = await replies.get()
        await route.fulfill(status=status, json=payload)
    await s.page.route('**/api/control', handle)
    return replies


def row(run_id, status, text):
    return _activity(run_id, 'writer', 'status', status=status, text=text)


async def test_a_pinned_window_sends_a_message_and_shows_how_many_wait(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        bodies = []
        replies = await held_control(s, bodies)
        win = page.locator('.nd-win[data-run-id="r-1"]')
        box = win.get_by_label('Message 1 writer', exact=True)
        await expect(box).to_have_attribute('placeholder', 'Message writer')
        await box.click()
        await s.page.keyboard.type('Check the tests first.')
        await expect(win.locator('.nd-say-count')).to_have_text('22 / 4,000')
        await s.page.keyboard.press('Enter')
        send = win.get_by_role('button', name='Send to 1 writer', exact=True)
        await expect(send).to_be_disabled()                                           # in flight: nothing claimed yet
        await expect(win.locator('.nd-say-wait')).to_have_text('')
        await replies.put(accepted('r-1', 1))
        await expect(win.locator('.nd-say-wait')).to_have_text('1 waiting')
        await expect(box).to_have_value('')
        await expect(box).to_be_focused()
        assert bodies == [{'action': 'agent_message', 'run_id': 'r-1', 'text': 'Check the tests first.'}]
        await s.page.keyboard.type('  And the docs.  ')
        await send.click()
        await replies.put(accepted('r-1', 2))
        await expect(win.locator('.nd-say-wait')).to_have_text('2 waiting')
        assert bodies[-1]['text'] == 'And the docs.'                                  # trimmed, as the chat sends
        # the worker takes them at its next request: the rows count down and join its transcript
        s.srv.bus.publish(row('r-1', 'message_received', 'Received your message: "Check the tests first."'))
        await expect(win.locator('.nd-say-wait')).to_have_text('1 waiting')
        await expect(win.locator('.nd-headline')).to_have_text('Received your message: "Check the tests first."')
        s.srv.bus.publish(row('r-1', 'message_received', 'Received your message: "And the docs."'))
        await expect(win.locator('.nd-say-wait')).to_have_text('')
        await expect(win.locator('.nd-state')).to_have_text('Working')                # a receipt is not a state
        await page.locator('.nd-pips .nd-pip[data-run-id="r-1"]').click()
        msgs = page.locator('#nd-detail .nd-dbody .nd-e-msg')
        await expect(msgs).to_have_count(2)
        await expect(msgs.nth(0)).to_contain_text('Received your message: "Check the tests first."')
        await expect(msgs.nth(1)).to_contain_text('Received your message: "And the docs."')
        assert s.errors == []


async def test_a_draft_survives_the_stream_and_a_refusal_keeps_it(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        bodies = []
        replies = await held_control(s, bodies)
        win = page.locator('.nd-win[data-run-id="r-1"]')
        box = win.get_by_label('Message 1 writer', exact=True)
        await box.click()
        await s.page.keyboard.type('Half a ')
        await s.page.evaluate("""() => { window.__detached = 0; const n = document.querySelector('.nd-win[data-run-id="r-1"] .nd-say');
            new MutationObserver(ms => { for (const m of ms) for (const r of m.removedNodes) if (r === n || (r.contains && r.contains(n))) window.__detached++; })
              .observe(document.getElementById('dream-nested-page'), {childList: true, subtree: true}); }""")
        for i in range(6):                                                            # the window re-renders around it
            s.srv.bus.publish(_activity('r-1', 'writer', 'text_delta', request_index=2, text=f'streamed {i} '))
            await s.page.wait_for_timeout(30)
        await expect(win.locator('.nd-stream')).to_contain_text('streamed 5')
        await s.page.keyboard.type('thought.')
        await expect(box).to_have_value('Half a thought.')
        await expect(box).to_be_focused()
        assert await s.page.evaluate('window.__detached') == 0                        # never taken out of the page (an IME keeps its composition)
        await s.page.keyboard.press('Enter')
        await replies.put((400, {'error': STOPPING}))
        await expect(win.locator('.nd-say-note')).to_have_text(STOPPING)          # verbatim, in that composer
        await expect(page.locator('#nd-live')).to_have_text(STOPPING)                 # and said once
        await expect(box).to_have_value('Half a thought.')                            # the draft stays
        await expect(page.locator('.nd-win[data-run-id="r-2"] .nd-say-note')).to_have_text('')
        await replies.put(accepted('r-1', 1))                                         # a second try goes through
        await win.get_by_role('button', name='Send to 1 writer', exact=True).click()
        await expect(win.locator('.nd-say-wait')).to_have_text('1 waiting')
        await expect(win.locator('.nd-say-note')).to_have_text('')
        await expect(box).to_have_value('')
        # a reconnect (a history reset, the rows again) keeps a draft in progress
        await box.click()
        await s.page.keyboard.type('Unsent words')
        await s.page.evaluate("window.dispatchEvent(new CustomEvent('dream:event', {detail: {m: {kind: 'history', data: {events: []}}, replay: false}}))")
        lanes(s, 1, {})
        await expect(win.get_by_label('Message 1 writer', exact=True)).to_have_value('Unsent words')
        assert s.errors == []


async def test_the_composer_is_off_on_finished_and_stopping_workers(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 3, {'r-1': 'completed'})
        bodies = []
        replies = await held_control(s, bodies)
        done = page.locator('.nd-win[data-run-id="r-1"]')
        box = done.get_by_label('Message 1 writer', exact=True)
        await expect(box).to_have_attribute('aria-disabled', 'true')
        await expect(box).to_have_attribute('placeholder', 'This worker has finished')
        await expect(done.get_by_role('button', name='Send to 1 writer', exact=True)).to_be_disabled()
        await box.click(force=True)                                                   # still focusable (read-only, not disabled)
        await expect(box).to_be_focused()
        await s.page.keyboard.type('Too late')
        await s.page.keyboard.press('Enter')
        await expect(box).to_have_value('')                                           # read-only: nothing typed, nothing sent
        await expect(page.locator('#nd-drawer')).to_be_hidden()                       # and no shortcut fired from it
        # a worker the owner stops: off while stopping, and its undelivered message is said before its end
        live = page.locator('.nd-win[data-run-id="r-2"]')
        lbox = live.get_by_label('Message 2 writer', exact=True)
        await lbox.fill('Wait for me')
        await live.get_by_role('button', name='Send to 2 writer', exact=True).click()
        await replies.put(accepted('r-2', 1))
        await expect(live.locator('.nd-say-wait')).to_have_text('1 waiting')
        await live.locator('[data-ctl="agent_stop"]').click()
        await replies.put((200, {'ok': True, 'result': {'run_id': 'r-2', 'agent': 'writer', 'state': 'stopping', 'changed': True}}))
        await expect(live.locator('.nd-state')).to_have_text('Stopping')
        await expect(lbox).to_have_attribute('aria-disabled', 'true')
        await expect(lbox).to_have_attribute('placeholder', 'This worker is stopping')
        s.srv.bus.publish(row('r-2', 'message_undelivered', 'Not delivered: it ended before its next request ("Wait for me").'))
        s.srv.bus.publish(row('r-2', 'interrupted', 'Stopped by you during request 1.'))
        await expect(live.locator('.nd-state')).to_have_text('Stopped')
        await expect(live.locator('.nd-say-wait')).to_have_text('')
        await expect(lbox).to_have_attribute('aria-disabled', 'true')
        await page.locator('.nd-pips .nd-pip[data-run-id="r-2"]').click()
        await expect(page.locator('#nd-detail .nd-dbody .nd-e-msg')).to_contain_text('Not delivered: it ended before its next request ("Wait for me").')
        await s.page.wait_for_timeout(200)
        assert [b['action'] for b in bodies] == ['agent_message', 'agent_stop'] and s.errors == []


async def test_the_limit_shows_before_sending_and_counts_like_the_server(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        bodies = []
        replies = await held_control(s, bodies)
        win = page.locator('.nd-win[data-run-id="r-1"]')
        box, send, count = (win.get_by_label('Message 1 writer', exact=True), win.get_by_role('button', name='Send to 1 writer', exact=True),
                            win.locator('.nd-say-count'))
        await box.fill('a' * 4001)
        await expect(count).to_have_text('4,001 / 4,000 · too long to send')
        await expect(count).to_have_class('nd-say-count nd-num nd-over')
        await expect(send).to_be_disabled()
        await box.press('Enter')
        await s.page.wait_for_timeout(200)
        assert bodies == []
        await box.fill('a' * 3999 + '\U0001F600')                                       # 4,000 code points; 4,001 UTF-16 units
        await expect(count).to_have_text('4,000 / 4,000')
        await expect(send).to_be_enabled()
        await send.click()
        await replies.put(accepted('r-1', 1))
        await expect(win.locator('.nd-say-wait')).to_have_text('1 waiting')
        assert len(bodies[-1]['text']) == 4000                                        # Python's count on the way back
        assert s.errors == []


async def test_the_drawers_detail_has_its_own_composer(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 4, {})
        bodies = []
        replies = await held_control(s, bodies)
        await page.locator('.nd-card[data-run-id="r-3"]').click()                     # a card's detail
        detail = page.locator('#nd-detail')
        await expect(detail).to_have_attribute('data-run-id', 'r-3')
        box = detail.get_by_label('Message 3 writer', exact=True)
        await box.fill('From the drawer.')
        await detail.get_by_role('button', name='Send to 3 writer', exact=True).click()
        await replies.put(accepted('r-3', 1))
        await expect(detail.locator('.nd-say-wait')).to_have_text('1 waiting')
        assert bodies == [{'action': 'agent_message', 'run_id': 'r-3', 'text': 'From the drawer.'}]
        # each worker keeps its own draft in the detail
        await box.fill('A draft for three')
        await page.locator('#nd-back').click()
        await page.locator('#nd-dlist .nd-item[data-run-id="r-4"]').click()
        await expect(detail.get_by_label('Message 4 writer', exact=True)).to_have_value('')
        await page.locator('#nd-back').click()
        await page.locator('#nd-dlist .nd-item[data-run-id="r-3"]').click()
        await expect(detail.get_by_label('Message 3 writer', exact=True)).to_have_value('A draft for three')
        assert s.errors == []


async def test_the_real_route_takes_a_message_to_a_running_worker(monkeypatch):
    """The merged backend (940a482): a real OpenAICompatBackend whose three workers wait on their first reply; the
    Studio route /api/control calls worker_control as the app does (tui/app.py); the page's composer sends to the first
    worker, the answer's pending shows, and when the worker starts its next round its message_received row reaches the
    page and the count clears."""
    import contextlib
    from types import SimpleNamespace
    from playwright.async_api import async_playwright
    from dream.gui.bus import EventBus
    from dream.gui.server import StudioServer
    from test_nested_view import _backend, drive
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    release = asyncio.Event()
    backend = _backend(release)
    srv = StudioServer(EventBus(), on_prompt=lambda *_: None,
                       on_control=lambda p: backend.worker_control(p.get('action'), p.get('run_id'), p.get('text')),
                       session={'provider': 'OpenAI', 'model': 'fixture', 'session_id': 'messages-real', 'lanes': None})
    url = await srv.start()
    turn = None
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
            turn = asyncio.create_task(drive(SimpleNamespace(srv=srv), backend, 'Ship it.'))
            win = page.locator('#dream-nested-page .nd-win[data-run-id]').first
            await expect(win.locator('.nd-state')).to_have_text('Working')
            box = win.get_by_label('Message 1 worker', exact=True)
            await box.fill('Use the smaller fix.')
            await box.press('Enter')
            await expect(win.locator('.nd-say-wait')).to_have_text('1 waiting')          # the real worker_control's pending
            await expect(box).to_have_value('')
            release.set()                                                                # the workers go on to their next round
            await expect(win.locator('.nd-say-wait')).to_have_text('')
            await page.locator('#dream-nested-page .nd-pips .nd-pip').first.click()
            await expect(page.locator('#nd-detail .nd-dbody .nd-e-msg')).to_contain_text('Received your message: "Use the smaller fix."')
            await asyncio.wait_for(turn, 10)
            assert errors == []
            await browser.close()
    finally:
        release.set()
        if turn is not None and not turn.done():
            turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
        with contextlib.suppress(Exception):
            await srv.stop()


# --- gate P6 UI round 1 --------------------------------------------------------------------------------------------

async def test_a_receipt_that_beats_the_answer_is_not_counted_twice(studio):
    """Gate P6 r1 #1: the worker's next round can take the message while the POST's answer is on its way. The answer's
    `pending` counted that message, and its receipt row already reached the page: the count must not stick at 1."""
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        bodies = []
        replies = await held_control(s, bodies)
        win = page.locator('.nd-win[data-run-id="r-1"]')
        box = win.get_by_label('Message 1 writer', exact=True)
        await box.fill('Quick.')
        await box.press('Enter')
        await expect(win.get_by_role('button', name='Send to 1 writer', exact=True)).to_be_disabled()   # on its way
        s.srv.bus.publish(row('r-1', 'message_received', 'Received your message: "Quick."'))
        await expect(win.locator('.nd-headline')).to_have_text('Received your message: "Quick."')      # the receipt is here
        await replies.put(accepted('r-1', 1))                                          # then the answer that counted it
        await expect(box).to_have_value('')
        await expect(win.locator('.nd-say-wait')).to_have_text('')
        # a message the worker has not taken yet still counts
        await box.fill('Slow.')
        await box.press('Enter')
        await replies.put(accepted('r-1', 1))
        await expect(win.locator('.nd-say-wait')).to_have_text('1 waiting')
        s.srv.bus.publish(row('r-1', 'message_received', 'Received your message: "Slow."'))
        await expect(win.locator('.nd-say-wait')).to_have_text('')
        assert [b['text'] for b in bodies] == ['Quick.', 'Slow.'] and s.errors == []


async def test_the_real_route_when_the_worker_takes_the_message_before_the_answer_leaves(monkeypatch):
    """Gate P6 r1 #1 through the real route (/api/control -> App._runtime_control -> OpenAICompatBackend.worker_control):
    the message goes into the worker's inbox, the worker's held first request then answers, and its next round takes
    the message (message_received) and waits in its second request. The POST's answer ({pending: 1}, counted before
    the worker took it) is held until the page shows that receipt: nothing waits, so the box says nothing, while the
    worker is still working and once it is done."""
    import contextlib
    from types import SimpleNamespace
    from playwright.async_api import async_playwright
    from dream.gui.bus import EventBus
    from dream.gui.server import StudioServer
    from dream.tui.app import App
    from test_local_subagents import _sub_final, _sub_toolcall
    from test_nested_worker_control import _Engine, _researcher_backend, _until
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    client = _Engine([], {'inspect': [_sub_toolcall('web_search', {'query': 'q'}), _sub_final('done')]})
    first = client.gates[('inspect', 0)] = asyncio.Event()
    second = client.gates[('inspect', 1)] = asyncio.Event()
    shown = asyncio.Event()
    holder = SimpleNamespace(engine=SimpleNamespace(backend=None))

    async def control(payload):
        result = await App._runtime_control(holder, payload)                          # the app's own route
        if payload.get('action') == 'agent_message':
            first.set()                                                               # its next round takes the message
            await shown.wait()                                                        # the answer leaves after the receipt
        return result
    srv = StudioServer(EventBus(), on_prompt=lambda *_: None, on_control=control,
                       session={'provider': 'MachX', 'model': 'fixture', 'session_id': 'messages-held', 'lanes': None})
    url = await srv.start()
    run = None
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
            events = []
            b = _researcher_backend(events, client)
            b._background_emit = lambda e: (events.append(e), srv.bus.publish(e))
            holder.engine.backend = b
            run = asyncio.ensure_future(b._run_subagent('researcher', 'inspect'))
            rid = await _until(lambda: client.run_ids.get('inspect'))
            win = page.locator(f'#dream-nested-page .nd-win[data-run-id="{rid}"]')
            box = win.get_by_label('Message 1 researcher', exact=True)
            await box.fill('Use the smaller fix.')
            await box.press('Enter')
            await page.wait_for_function('id => (window.DreamNested.lane(id)?.log || []).some(e => e.kind === "message")', arg=rid)
            shown.set()
            await expect(box).to_have_value('')                                       # the answer arrived
            await expect(win.locator('.nd-say-wait')).to_have_text('')
            await expect(win.locator('.nd-state')).to_have_text('Working')            # in the request that carries it
            assert b._workers[rid].inbox == []
            second.set()
            await asyncio.wait_for(run, 5)
            await expect(win.locator('.nd-state')).to_have_text('Done')
            await expect(win.locator('.nd-say-wait')).to_have_text('')
            assert errors == []
            await browser.close()
    finally:
        first.set()
        second.set()
        shown.set()
        if run is not None and not run.done():
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)
        with contextlib.suppress(Exception):
            await srv.stop()


async def test_a_worker_that_ends_has_nothing_waiting(studio):
    """Gate P6 r1 #1: a worker's end row, or its turn's end, leaves nothing waiting for it, whatever rows came before;
    and an answer that arrives after the worker ended counts nothing."""
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 3, {})
        bodies = []
        replies = await held_control(s, bodies)
        win = [page.locator(f'.nd-win[data-run-id="r-{i}"]') for i in (1, 2)]
        box = [w.locator('.nd-say textarea') for w in win]
        for i, text in enumerate(('One', 'Two'), 1):
            await box[0].fill(text)
            await box[0].press('Enter')
            await replies.put(accepted('r-1', i))
            await expect(win[0].locator('.nd-say-wait')).to_have_text(f'{i} waiting')
        s.srv.bus.publish(row('r-1', 'message_undelivered', 'Not delivered: it ended before its next request ("One").'))
        await expect(win[0].locator('.nd-say-wait')).to_have_text('1 waiting')
        s.srv.bus.publish(row('r-1', 'completed', ENDINGS['completed']))                 # ends with one still counted
        await expect(win[0].locator('.nd-state')).to_have_text('Done')
        await expect(win[0].locator('.nd-say-wait')).to_have_text('')
        # the worker ends while the message is on its way; the answer comes after
        await box[1].fill('Late')
        await box[1].press('Enter')
        s.srv.bus.publish(row('r-2', 'message_undelivered', 'Not delivered: it ended before its next request ("Late").'))
        s.srv.bus.publish(row('r-2', 'interrupted', ENDINGS['interrupted']))
        await expect(win[1].locator('.nd-state')).to_have_text('Stopped')
        await replies.put(accepted('r-2', 1))
        await expect(box[1]).to_have_value('')
        await expect(win[1].locator('.nd-say-wait')).to_have_text('')
        # a worker still counted when its turn ends, with a second message on its way: the turn's end settles it and
        # its count, and the answer that comes after counts nothing
        await page.locator('.nd-pips .nd-pip[data-run-id="r-3"]').click()
        detail = page.locator('#nd-detail')
        dbox = detail.locator('.nd-say textarea')
        await dbox.fill('Before the end')
        await dbox.press('Enter')
        await replies.put(accepted('r-3', 1))
        await expect(detail.locator('.nd-say-wait')).to_have_text('1 waiting')
        await dbox.fill('On its way')
        await dbox.press('Enter')
        await expect(detail.get_by_role('button', name='Send to 3 writer', exact=True)).to_be_disabled()
        s.srv.bus.publish(Event('turn_end', {}))
        await expect(detail.locator('.nd-state')).to_have_text('Stopped')
        await expect(detail.locator('.nd-say-wait')).to_have_text('')
        await replies.put(accepted('r-3', 2))
        await expect(dbox).to_have_value('')
        await expect(detail.locator('.nd-say-wait')).to_have_text('')
        assert s.errors == []


async def test_send_by_mouse_or_keyboard_leaves_the_focus_in_the_box(studio):
    """Gate P6 r1 #2: Send is off while its message is on its way, and a focused button that turns off drops the focus
    to the page, where the next keys fire the tab's shortcuts ("a2" opened the drawer, then worker 2). The focus goes
    to the box as the send starts; the box is read-only until the answer, so keys then type nothing and fire nothing."""
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 4, {})
        bodies = []
        replies = await held_control(s, bodies)
        win = page.locator('.nd-win[data-run-id="r-1"]')
        box, send = (win.get_by_label('Message 1 writer', exact=True), win.get_by_role('button', name='Send to 1 writer', exact=True))
        drawer = page.locator('#nd-drawer')
        await box.fill('Mouse send')
        await send.click()
        await expect(send).to_be_disabled()
        await expect(box).to_be_focused()                                             # on its way
        await s.page.keyboard.type('a2')
        await s.page.wait_for_timeout(700)                                            # past the digit buffer
        await expect(drawer).to_have_attribute('aria-hidden', 'true')
        await expect(box).to_have_value('Mouse send')
        await replies.put(accepted('r-1', 1))
        await expect(win.locator('.nd-say-wait')).to_have_text('1 waiting')
        await expect(box).to_be_focused()
        await s.page.keyboard.type('a2')
        await s.page.wait_for_timeout(700)
        await expect(box).to_have_value('a2')                                         # the words reach the box
        await expect(drawer).to_have_attribute('aria-hidden', 'true')
        # the keyboard: Tab from the box to Send, then Enter
        await box.fill('Keyboard send')
        await s.page.keyboard.press('Tab')
        await expect(send).to_be_focused()
        await s.page.keyboard.press('Enter')
        await expect(send).to_be_disabled()
        await expect(box).to_be_focused()
        await replies.put(accepted('r-1', 2))
        await expect(win.locator('.nd-say-wait')).to_have_text('2 waiting')
        await expect(box).to_be_focused()
        assert [b['text'] for b in bodies] == ['Mouse send', 'Keyboard send'] and s.errors == []


async def test_shift_enter_adds_a_line_and_a_message_is_sent_once(studio):
    """Gate P6 r1 #3 (mutants M03, M04): Shift+Enter is a new line and sends nothing; while a message is on its way,
    another Enter, a held Enter's repeats or a second click send nothing more."""
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        bodies = []
        replies = await held_control(s, bodies)
        win = page.locator('.nd-win[data-run-id="r-1"]')
        box, send = (win.get_by_label('Message 1 writer', exact=True), win.get_by_role('button', name='Send to 1 writer', exact=True))
        await box.click()
        await s.page.keyboard.type('Line one')
        await s.page.keyboard.press('Shift+Enter')
        await s.page.keyboard.type('line two')
        await expect(box).to_have_value('Line one\nline two')
        await s.page.wait_for_timeout(200)
        assert bodies == []
        await s.page.keyboard.press('Enter')
        await expect(send).to_be_disabled()
        await s.page.keyboard.press('Enter')                                          # again, while it is on its way
        await s.page.keyboard.press('Enter')
        await s.page.wait_for_timeout(300)
        assert len(bodies) == 1
        await replies.put(accepted('r-1', 1))
        await expect(win.locator('.nd-say-wait')).to_have_text('1 waiting')
        await box.fill('Held')
        await box.focus()
        for _ in range(8):                                                            # one press, then its repeats
            await s.page.keyboard.down('Enter')
            await s.page.wait_for_timeout(20)
        await s.page.keyboard.up('Enter')
        await s.page.wait_for_timeout(300)
        assert len(bodies) == 2
        await replies.put(accepted('r-1', 2))
        await expect(win.locator('.nd-say-wait')).to_have_text('2 waiting')
        await box.fill('Clicked twice')
        await send.dblclick()
        await s.page.wait_for_timeout(300)
        assert len(bodies) == 3
        await replies.put(accepted('r-1', 3))
        await expect(win.locator('.nd-say-wait')).to_have_text('3 waiting')
        assert [b['text'] for b in bodies] == ['Line one\nline two', 'Held', 'Clicked twice'] and s.errors == []


async def test_a_held_enter_against_a_refusal_sends_once(studio):
    """Gate P6 r1 #4: a refusal keeps the draft, so a held Enter's key repeats sent it again and again (about 30 POSTs
    a second, each refusal said again). Only a new press sends."""
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        bodies = []

        async def handle(route, request):
            bodies.append(request.post_data_json)
            await route.fulfill(status=400, json={'error': STOPPING})
        await s.page.route('**/api/control', handle)
        win = page.locator('.nd-win[data-run-id="r-1"]')
        box = win.get_by_label('Message 1 writer', exact=True)
        await box.fill('Held against a refusal')
        await box.focus()
        for _ in range(20):
            await s.page.keyboard.down('Enter')
            await s.page.wait_for_timeout(30)
        await s.page.keyboard.up('Enter')
        await expect(win.locator('.nd-say-note')).to_have_text(STOPPING)
        await expect(page.locator('#nd-live')).to_have_text(STOPPING)
        await s.page.wait_for_timeout(300)
        assert len(bodies) == 1
        await expect(box).to_have_value('Held against a refusal')
        await s.page.keyboard.press('Enter')                                          # a new press tries again
        for _ in range(100):
            if len(bodies) == 2:
                break
            await s.page.wait_for_timeout(20)
        assert len(bodies) == 2 and s.errors == []


async def test_a_pinned_workers_detail_has_a_box_of_its_own(studio):
    """Gate P6 r1 #3 (mutant M08): the drawer's detail on a pinned worker has its own box and draft; its window keeps
    its box and its draft."""
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        win = page.locator('.nd-win[data-run-id="r-1"]')
        wbox = win.get_by_label('Message 1 writer', exact=True)
        await wbox.fill('Window draft')
        await page.locator('.nd-pips .nd-pip[data-run-id="r-1"]').click()
        detail = page.locator('#nd-detail')
        await expect(detail).to_have_attribute('data-run-id', 'r-1')
        dbox = detail.get_by_label('Message 1 writer', exact=True)
        await expect(dbox).to_have_value('')
        await dbox.fill('Detail draft')
        await expect(win.locator('.nd-say textarea')).to_have_value('Window draft')
        await page.locator('#nd-detail-close').click()
        await expect(wbox).to_have_value('Window draft')
        await page.locator('.nd-pips .nd-pip[data-run-id="r-1"]').click()
        await expect(dbox).to_have_value('Detail draft')
        ids = await s.page.evaluate("[...document.querySelectorAll('.nd-say textarea')].map(t => t.id)")
        assert len(ids) == len(set(ids)) == 3, ids                                     # two windows and the detail
        assert s.errors == []


async def test_the_detail_box_is_never_taken_out_by_the_stream(studio):
    """Gate P6 r1 #3 (mutant M09): the drawer's detail keeps its frame and its box while it shows the same worker, so
    the focus, the caret and an input method's composition survive a worker that streams."""
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 4, {})
        await page.locator('.nd-card[data-run-id="r-3"]').click()
        dbox = page.locator('#nd-detail').get_by_label('Message 3 writer', exact=True)
        await dbox.click()
        await s.page.keyboard.type('Half ')
        await s.page.evaluate("""() => { window.__detached = 0; const n = document.querySelector('#nd-detail .nd-say');
            new MutationObserver(ms => { for (const m of ms) for (const r of m.removedNodes) if (r === n || (r.contains && r.contains(n))) window.__detached++; })
              .observe(document.getElementById('dream-nested-page'), {childList: true, subtree: true}); }""")
        for i in range(5):
            s.srv.bus.publish(_activity('r-3', 'writer', 'text_delta', request_index=2, text=f'streamed {i} '))
            s.srv.bus.publish(row('r-3', 'running', f'Working {i}'))
            await s.page.wait_for_timeout(40)
        await expect(page.locator('#nd-detail .nd-dbody')).to_contain_text('streamed 4')
        await s.page.keyboard.type('done.')
        await expect(dbox).to_have_value('Half done.')
        await expect(dbox).to_be_focused()
        assert await s.page.evaluate('window.__detached') == 0
        assert s.errors == []


async def test_words_typed_while_a_message_is_on_its_way_are_never_lost(studio):
    """Gate P6 r1 #3 (mutant M12): the box is read-only while its message is on its way, so words typed then are never
    taken into the box only for the answer to wipe them; after the answer it takes words again."""
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        bodies = []
        replies = await held_control(s, bodies)
        win = page.locator('.nd-win[data-run-id="r-1"]')
        box = win.get_by_label('Message 1 writer', exact=True)
        await box.fill('First thought')
        await box.press('Enter')
        await expect(win.get_by_role('button', name='Send to 1 writer', exact=True)).to_be_disabled()
        await s.page.keyboard.type(' and more')
        await expect(box).to_have_value('First thought')                              # not taken, so nothing to lose
        await replies.put(accepted('r-1', 1))
        await expect(win.locator('.nd-say-wait')).to_have_text('1 waiting')
        await expect(box).to_have_value('')
        await s.page.keyboard.type('Next thought')
        await expect(box).to_have_value('Next thought')
        assert [b['text'] for b in bodies] == ['First thought'] and s.errors == []


async def test_a_long_unbroken_receipt_wraps_in_the_drawer(studio):
    """Gate P6 r1 #5: a receipt's preview of one unbroken word (a URL, a hash) wraps in the drawer's transcript rather
    than widening it past the drawer."""
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        s.srv.bus.publish(row('r-1', 'message_received', 'Received your message: "https://example.com/' + 'M' * 60 + '…"'))
        await page.locator('.nd-pips .nd-pip[data-run-id="r-1"]').click()
        body = page.locator('#nd-detail .nd-dbody')
        await expect(body.locator('.nd-e-msg')).to_have_count(1)
        wide, room = await body.evaluate('b => [b.scrollWidth, b.clientWidth]')
        assert wide <= room, (wide, room)
        wide, room = await body.locator('.nd-e-msg').evaluate('e => [e.scrollWidth, e.clientWidth]')
        assert wide <= room, (wide, room)
        assert s.errors == []


async def test_a_worker_that_ends_while_its_box_holds_a_draft_says_why(studio):
    """Gate P6 r1 #6: the placeholder that says why a box is off hides behind a draft, so the box's note says it then;
    the draft stays for the owner to copy. An empty box says it once, in its placeholder."""
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 3, {'r-3': 'completed'})
        bodies = []
        replies = await held_control(s, bodies)
        w1, w2 = page.locator('.nd-win[data-run-id="r-1"]'), page.locator('.nd-win[data-run-id="r-2"]')
        await w1.locator('.nd-say textarea').fill('Half-written note')
        s.srv.bus.publish(row('r-1', 'completed', ENDINGS['completed']))
        await expect(w1.locator('.nd-state')).to_have_text('Done')
        await expect(w1.locator('.nd-say-note')).to_have_text(
            'This worker has finished; your draft stays here. Message the orchestrator instead.')
        await expect(w1.locator('.nd-say textarea')).to_have_value('Half-written note')
        await w2.locator('.nd-say textarea').fill('Another')
        await w2.locator('[data-ctl="agent_stop"]').click()
        await replies.put((200, {'ok': True, 'result': {'run_id': 'r-2', 'agent': 'writer', 'state': 'stopping', 'changed': True}}))
        await expect(w2.locator('.nd-state')).to_have_text('Stopping')
        await expect(w2.locator('.nd-say-note')).to_have_text(
            'This worker is stopping; your draft stays here. Message the orchestrator instead.')
        await page.locator('.nd-card[data-run-id="r-3"]').click()                     # finished with an empty box
        detail = page.locator('#nd-detail')
        await expect(detail.locator('.nd-say textarea')).to_have_attribute('placeholder', 'This worker has finished')
        await expect(detail.locator('.nd-say-note')).to_have_text('')
        assert s.errors == []


async def test_a_reconnect_while_a_message_is_on_its_way_never_counts_above_the_answer(studio):
    """The count takes off the worker's message rows that arrived while the message was on its way: the rows the lane
    holds when the answer comes, less those it held when the message left. A reconnect in between rebuilds the lanes
    from the server's retained history, which may no longer hold that worker's older rows; fewer rows is never a
    reason to count more than the answer says."""
    async with studio() as s:
        page = await open_nested(s)
        s.srv.bus.conversation.max_events = 12
        lanes(s, 1, {})
        for word in ('one', 'two', 'three'):
            s.srv.bus.publish(row('r-1', 'message_received', f'Received your message: "{word}"'))
        win = page.locator('.nd-win[data-run-id="r-1"]')
        await expect(win.locator('.nd-headline')).to_have_text('Received your message: "three"')
        bodies = []
        replies = await held_control(s, bodies)
        await win.locator('.nd-say textarea').fill('Four')
        await win.locator('.nd-say textarea').press('Enter')
        for i in range(12):                                                           # the history keeps only these
            s.srv.bus.publish(row('r-1', 'running', f'Working {i}'))
        await expect(win.locator('.nd-headline')).to_have_text('Working 11')
        await s.page.evaluate("() => { window.__hist = 0; window.addEventListener('dream:event', e => { if (e.detail.m.kind === 'history') window.__hist++; }); socket.close(); }")
        await s.page.wait_for_function('window.__hist >= 1', timeout=8000)
        await replies.put(accepted('r-1', 1))
        await expect(win.locator('.nd-say textarea')).to_have_value('')
        await expect(win.locator('.nd-say-wait')).to_have_text('1 waiting')
        assert s.errors == []
