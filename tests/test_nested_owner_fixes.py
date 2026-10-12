"""The owner's screen findings of 2026-09-30 (screenshots 20:58-21:14), item 6 of the fix list.

(a) With the Understand panel open, the Nested composer's Send button was cut off at the panel's edge.
(b) A long prompt filled the orchestrator column as a 20 px bold heading. It reads as normal text now, cut after four
    lines, with a Show all / Show less button that keeps its state while the column re-renders.
"""
from __future__ import annotations

import asyncio

import pytest
from playwright.async_api import expect

from dream.core.backends.base import Event
from test_nested_view import open_nested, shot, studio  # noqa: F401  (the fixture and the page helpers)

LONG = " ".join(f"Step {i}: map the module, cite its functions and note what could break." for i in range(1, 40))


async def _send_is_fully_usable(page) -> dict:
    return await page.evaluate("""() => {
        const b = document.getElementById('nd-send'), r = b.getBoundingClientRect();
        const col = b.closest('.nd-orch').getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return {inColumn: r.left >= col.left - 1 && r.right <= col.right + 1, inViewport: r.right <= innerWidth + 1,
                onTop: hit === b || b.contains(hit), width: r.width};
    }""")


@pytest.mark.parametrize("size", [(2000, 1125), (1600, 1000), (1366, 768)])
async def test_send_stays_visible_and_clickable_with_the_understand_panel_open(studio, size):  # noqa: F811
    async with studio(width=size[0], height=size[1]) as s:
        # the owner's order: the Understand switch first (it opens the panel beside the chat), then Nested Dream
        await s.page.locator('#dream-nav-understand').click()
        await expect(s.page.locator('#dream-nav-understand')).to_have_attribute('aria-pressed', 'true')
        view = await open_nested(s)
        await view.locator('#nd-compose').fill(LONG)
        found = await _send_is_fully_usable(s.page)
        panel = await s.page.locator('#dream-understand').bounding_box()
        orch = await view.locator('.nd-orch').bounding_box()
        # DREAM-243: in this view the panel is the right-hand pane's Map, over the workers' area; the orchestrator
        # column keeps its width beside it (it flowed beside the whole view before, squeezing it into three columns)
        assert orch['x'] + orch['width'] <= panel['x'] + 1 and orch['width'] >= 360, (orch, panel)
        await expect(s.page.locator('html')).to_have_attribute('data-nd-pane', 'map')
        assert found['width'] > 20 and found['inColumn'] and found['inViewport'] and found['onTop'], found
        assert s.errors == []


async def test_a_long_goal_reads_as_normal_text_cut_after_four_lines_with_show_all(studio):  # noqa: F811
    async with studio() as s:
        page = await open_nested(s)
        await s.srv._accept_prompt(LONG)
        goal = page.locator('.nd-goal')
        await expect(goal).to_have_text(LONG)                                 # every word is still there
        look = await goal.evaluate("""e => { const c = getComputedStyle(e);
            return {weight: parseInt(c.fontWeight), size: parseFloat(c.fontSize), line: parseFloat(c.lineHeight),
                    height: e.getBoundingClientRect().height}; }""")
        assert look['weight'] <= 400 and look['size'] <= 14, look
        assert look['height'] <= 4 * look['line'] + 2, look                   # at most four lines on screen
        more = page.locator('.nd-goal-more')
        await expect(more).to_have_text('Show all')
        await expect(more).to_have_attribute('aria-expanded', 'false')
        await more.click()
        await expect(more).to_have_text('Show less')
        await expect(more).to_have_attribute('aria-expanded', 'true')
        assert await goal.evaluate("e => e.getBoundingClientRect().height") > 3 * look['height']
        s.srv.bus.publish(Event('turn_start', {}))                             # the column re-renders while it streams
        s.srv.bus.publish(Event('text_delta', 'Mapping now.'))
        await expect(page.locator('.nd-msg.nd-dream .nd-stream')).to_contain_text('Mapping now.')
        await expect(more).to_have_text('Show less')                          # the reader's choice survives
        await more.click()
        await expect(more).to_have_text('Show all')
        assert s.errors == []


async def test_a_short_goal_shows_whole_with_no_button(studio):  # noqa: F811
    async with studio() as s:
        page = await open_nested(s)
        await s.srv._accept_prompt('Plan the release.')
        await expect(page.locator('.nd-goal')).to_have_text('Plan the release.')
        await expect(page.locator('.nd-goal-more')).to_have_count(0)
        assert s.errors == []


async def test_a_running_turn_has_a_stop_button_in_the_nested_composer(studio):  # noqa: F811
    """The owner, 2026-10-04: [feedback]. The Nested composer had Send (Steer while a
    turn runs) and nothing that stops the orchestrator's turn. Stop appears beside Steer while a turn runs, posts the
    Chat view's interrupt, and leaves when the turn ends."""
    async with studio() as s:
        page = await open_nested(s)
        stop = page.locator('#nd-stop')
        await expect(stop).to_be_hidden()                                     # idle: only Send
        s.srv.bus.publish(Event('turn_start', {}))
        s.srv.bus.publish(Event('text_delta', 'Planning the work.'))
        await expect(stop).to_be_visible()
        await expect(stop).to_have_text('Stop')
        await expect(page.locator('#nd-send')).to_have_text('Steer')
        box = await page.evaluate("""() => { const b = document.getElementById('nd-stop'), r = b.getBoundingClientRect();
            const col = b.closest('.nd-orch').getBoundingClientRect(), send = document.getElementById('nd-send').getBoundingClientRect();
            const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            return {inColumn: r.left >= col.left - 1 && r.right <= col.right + 1, onTop: hit === b || b.contains(hit),
                    clear: r.right <= send.left + 1 || r.left >= send.right - 1}; }""")
        assert box == {'inColumn': True, 'onTop': True, 'clear': True}, box
        async with page.page.expect_request(lambda r: r.url.endswith('/api/control') and r.method == 'POST') as got:
            await stop.click()
        assert (await got.value).post_data_json == {'action': 'interrupt'}
        s.srv.bus.publish(Event('turn_end', {}))
        await expect(stop).to_be_hidden()
        await expect(page.locator('#nd-send')).to_have_text('Send')


# --- DREAM-255: what Stop says back -------------------------------------------------------------------------------
# The studio fixture builds its StudioServer without on_control, so /api/control answers 503 there. These tests set
# the server's own callback (StudioServer._on_control, read by the route on every request) to a scripted one. That is
# a control fixture only: nothing here shows that an engine or a socket was cancelled.

STOP_ASKED = 'Stop requested. The turn is still running until Dream reports that it ended.'
STOP_NONE = ('Nothing was stopped: Dream reported nothing it could interrupt. If this turn is still running, it was '
             'not asked to stop.')
IN_VIEW = """e => { const r = e.getBoundingClientRect();
    return r.height > 0 && r.top >= 0 && r.bottom <= innerHeight + 0.5 && r.left >= 0 && r.right <= innerWidth + 0.5; }"""
HELD = 10   # seconds a scripted control answer may be held: a test that fails first cannot leave the server waiting


async def _held(release):
    """Wait for the test to let the answer go, never longer than HELD (the route then answers 400, not a hang)."""
    try:
        await asyncio.wait_for(release.wait(), HELD)
    except asyncio.TimeoutError:
        raise ValueError('Scripted: the test never released this answer') from None


async def _keep_writing(s, page, tag):
    """The orchestrator writes on: every piece is a render of the column (the one that used to rewrite the hint)."""
    for i in range(8):
        s.srv.bus.publish(Event('text_delta', f'{tag} {i} '))
    await expect(page.locator('#nd-convo')).to_contain_text(f'{tag} 7')
    await s.page.wait_for_timeout(200)


async def test_stop_is_pending_while_asked_and_its_receipt_does_not_say_the_turn_ended(studio):  # noqa: F811
    async with studio() as s:
        page = await open_nested(s)
        calls, release = [], asyncio.Event()

        async def on_control(payload):
            calls.append(payload)
            await _held(release)                                              # the answer waits: the pending state
            return {'interrupted': True}
        s.srv._on_control = on_control
        stop, status = page.locator('#nd-stop'), page.locator('#nd-status')
        s.srv.bus.publish(Event('turn_start', {}))
        s.srv.bus.publish(Event('text_delta', 'Planning the work.'))
        await expect(stop).to_be_visible()
        try:
            await stop.click()
            await expect(stop).to_be_disabled()                               # in flight: off, and it says so
            await expect(stop).to_have_text('Stopping…')
            await expect(status).to_have_text('')                             # nothing claimed before the answer
            await stop.click(force=True, timeout=2000)                        # a second click asks nothing: the button is off
            await stop.evaluate('b => b.click()')                             # nor does a click made by script
            await stop.dispatch_event('click')                                # nor one that reaches the handler itself
            await _keep_writing(s, page, 'pending')
            await expect(stop).to_be_disabled()                               # a render does not turn it back on
            assert calls == [{'action': 'interrupt'}]
        finally:
            release.set()                                                     # a failure above never leaves the request open
        await expect(stop).to_be_enabled()
        await expect(stop).to_have_text('Stop')
        await expect(status).to_have_text(STOP_ASKED)
        assert await status.get_attribute('data-error') is None
        assert await status.evaluate(IN_VIEW)
        # the server has sent no turn_end: the view still shows a running turn, and the receipt outlives its renders
        await _keep_writing(s, page, 'after')
        await expect(stop).to_be_visible()
        await expect(page.locator('#nd-send')).to_have_text('Steer')
        await expect(status).to_have_text(STOP_ASKED)
        s.srv.bus.publish(Event('turn_end', {'interrupted': True}))            # only the event ends the turn here
        await expect(stop).to_be_hidden()
        await expect(page.locator('#nd-send')).to_have_text('Send')
        await expect(status).to_have_text('')                                 # "still running" left with the turn
        assert calls == [{'action': 'interrupt'}] and s.errors == []


@pytest.mark.parametrize('size', [(1280, 700), (1600, 1200)])                 # short (the hint is hidden) and tall
@pytest.mark.parametrize('how', ['refusal', 'network'])
async def test_a_stop_that_fails_says_so_in_view_and_keeps_saying_it_while_the_turn_streams(studio, size, how):  # noqa: F811
    async with studio(width=size[0], height=size[1]) as s:
        page = await open_nested(s)
        if how == 'refusal':
            def on_control(payload):
                raise ValueError('Scripted: no turn can be interrupted')
            s.srv._on_control = on_control
            said = 'Scripted: no turn can be interrupted'
        else:
            await s.page.route('**/api/control', lambda route: route.abort())
            said = 'Could not reach Dream. Reconnect and try again.'
        stop, status = page.locator('#nd-stop'), page.locator('#nd-status')
        s.srv.bus.publish(Event('turn_start', {}))
        s.srv.bus.publish(Event('text_delta', 'Planning the work.'))
        await expect(stop).to_be_visible()
        await stop.click()
        await expect(status).to_have_text(said)
        await expect(status).to_have_attribute('data-error', 'true')
        await expect(status).to_be_visible()
        assert await status.evaluate(IN_VIEW), size
        await expect(stop).to_be_enabled()                                    # it can be asked again
        await expect(stop).to_have_text('Stop')
        await _keep_writing(s, page, 'more')                                  # the turn goes on writing
        await expect(status).to_have_text(said)
        await expect(status).to_have_attribute('data-error', 'true')
        assert await status.evaluate(IN_VIEW), size
        await shot(s.page, f'stop-{how}-{size[0]}x{size[1]}.png')             # only with NESTED_SHOTS set
        await expect(stop).to_be_visible()                                    # nothing stopped, and nothing says it did
        await expect(page.locator('#nd-send')).to_have_text('Steer')
        assert s.errors == []


@pytest.mark.parametrize('size', [(1280, 700), (1600, 1200)])                 # short (the hint is hidden) and tall
async def test_a_stop_that_found_nothing_to_interrupt_says_so_and_is_not_a_receipt(studio, size):  # noqa: F811
    """The route's own answer when it has no cancellable target: HTTP 200, {"ok": true, "result": {"interrupted":
    false}} (CONTROL_REFERENCE.txt). That is an outcome, not an acceptance: no "Stop requested" for it."""
    async with studio(width=size[0], height=size[1]) as s:
        page = await open_nested(s)
        calls = []

        def on_control(payload):
            calls.append(payload)
            return {'interrupted': False}
        s.srv._on_control = on_control
        stop, status = page.locator('#nd-stop'), page.locator('#nd-status')
        s.srv.bus.publish(Event('turn_start', {}))
        s.srv.bus.publish(Event('text_delta', 'Planning the work.'))
        await expect(stop).to_be_visible()
        async with s.page.expect_response(lambda r: r.url.endswith('/api/control'), timeout=5000) as got:
            await stop.click()
        answer = await got.value
        assert answer.status == 200 and await answer.json() == {'ok': True, 'result': {'interrupted': False}}
        await expect(status).to_have_text(STOP_NONE)
        await expect(status).to_have_attribute('data-error', 'true')          # the stop did not happen: said as one
        await expect(status).not_to_contain_text('Stop requested')
        await expect(status).to_be_visible()
        assert await status.evaluate(IN_VIEW), size
        await expect(stop).to_be_enabled()                                    # it can be asked again
        await expect(stop).to_have_text('Stop')
        await _keep_writing(s, page, 'more')                                  # the turn goes on writing
        await expect(status).to_have_text(STOP_NONE)
        assert await status.evaluate(IN_VIEW), size
        await shot(s.page, f'stop-nothing-{size[0]}x{size[1]}.png')           # only with NESTED_SHOTS set
        await expect(stop).to_be_visible()                                    # the view still shows a running turn
        await expect(page.locator('#nd-send')).to_have_text('Steer')
        s.srv.bus.publish(Event('turn_end', {}))
        await expect(stop).to_be_hidden()
        await expect(status).to_have_text('')                                 # it was about that turn
        assert await status.get_attribute('data-error') is None
        assert calls == [{'action': 'interrupt'}] and s.errors == []


@pytest.mark.parametrize('size', [(1280, 700), (1600, 1200)])
async def test_the_stop_receipt_is_in_view_at_short_and_tall_heights(studio, size):  # noqa: F811
    async with studio(width=size[0], height=size[1]) as s:
        page = await open_nested(s)
        s.srv._on_control = lambda payload: {'interrupted': True}
        stop, status = page.locator('#nd-stop'), page.locator('#nd-status')
        s.srv.bus.publish(Event('turn_start', {}))
        s.srv.bus.publish(Event('text_delta', 'Planning the work.'))
        await expect(stop).to_be_visible()
        await stop.click()
        await expect(status).to_have_text(STOP_ASKED)
        assert await status.get_attribute('data-error') is None
        assert await status.evaluate(IN_VIEW), size
        await _keep_writing(s, page, 'more')
        await expect(status).to_have_text(STOP_ASKED)
        assert await status.evaluate(IN_VIEW), size
        await shot(s.page, f'stop-requested-{size[0]}x{size[1]}.png')         # only with NESTED_SHOTS set
        assert s.errors == []


# What a late answer may be: each is the answer to a Stop asked of turn A, arriving once turn B runs.
LATE = {'accepted': {'interrupted': True}, 'nothing': {'interrupted': False}, 'refusal': None}


@pytest.mark.parametrize('late', list(LATE))
async def test_a_stop_answered_after_its_turn_ended_says_nothing_about_the_next_turn(studio, late):  # noqa: F811
    async with studio() as s:
        page = await open_nested(s)
        calls, release = [], asyncio.Event()

        async def on_control(payload):
            calls.append(payload)
            if len(calls) > 1:
                return {'interrupted': True}                                   # turn B's own Stop: answered at once
            await _held(release)
            if LATE[late] is None:
                raise ValueError('Scripted: refused late')
            return LATE[late]
        s.srv._on_control = on_control
        stop, status, convo = page.locator('#nd-stop'), page.locator('#nd-status'), page.locator('#nd-convo')
        s.srv.bus.publish(Event('turn_start', {}))
        s.srv.bus.publish(Event('text_delta', 'Turn A writes.'))
        await expect(stop).to_be_visible()
        try:
            await stop.click()
            await expect(stop).to_have_text('Stopping…')
            assert calls == [{'action': 'interrupt'}]
            s.srv.bus.publish(Event('turn_end', {}))                          # A ends while its Stop is unanswered
            s.srv.bus.publish(Event('turn_start', {}))
            s.srv.bus.publish(Event('text_delta', 'Turn B writes.'))
            await expect(convo).to_contain_text('Turn B writes.')
            await expect(stop).to_be_visible()
            await expect(stop).to_be_enabled()                                # B's Stop is B's: not held by A's request
            await expect(stop).to_have_text('Stop')
            await expect(status).to_have_text('')
            async with s.page.expect_response(lambda r: r.url.endswith('/api/control'), timeout=5000) as got:
                release.set()                                                 # A's answer arrives during B
            assert (await got.value).status == (400 if late == 'refusal' else 200)
        finally:
            release.set()
        await s.page.wait_for_timeout(300)                                    # time for the page to have used the answer
        await expect(status).to_have_text('')                                 # nothing of A's on B: no receipt, no error
        assert await status.get_attribute('data-error') is None
        await expect(stop).to_be_enabled()
        await expect(stop).to_have_text('Stop')
        await _keep_writing(s, page, 'B')
        await expect(status).to_have_text('')
        await stop.click()                                                    # B can still be asked, and gets its own receipt
        await expect(status).to_have_text(STOP_ASKED)
        assert await status.get_attribute('data-error') is None
        assert calls == [{'action': 'interrupt'}] * 2 and s.errors == []


# One task of the page: the end of a turn and the start of the next, with no animation frame (so no render) between.
END_THEN_START = """() => {
    const say = kind => window.dispatchEvent(new CustomEvent('dream:event', {detail: {m: {kind, data: {}}, replay: false}}));
    const st = document.getElementById('nd-status'), seen = {};
    say('turn_end');
    seen.afterEnd = {text: st.textContent, error: st.dataset.error ?? null};
    say('turn_start');
    seen.afterStart = {text: st.textContent, error: st.dataset.error ?? null};
    return seen;
}"""
SAID = {'accepted': ({'interrupted': True}, STOP_ASKED, None), 'nothing': ({'interrupted': False}, STOP_NONE, 'true'),
        'refusal': (None, 'Scripted: refused', 'true')}


@pytest.mark.parametrize('answer', list(SAID))
async def test_what_stop_said_leaves_with_its_turn_even_when_the_next_starts_in_the_same_frame(studio, answer):  # noqa: F811
    """The view's own handling of the two events (dispatched as the chat's hook dispatches them): the server is not
    part of this one. Before DREAM-255's fix the line was cleared at render time only, and a render after both events
    saw a running turn and kept turn A's words on turn B."""
    result, said, error = SAID[answer]
    async with studio() as s:
        page = await open_nested(s)

        def on_control(payload):
            if result is None:
                raise ValueError('Scripted: refused')
            return result
        s.srv._on_control = on_control
        stop, status = page.locator('#nd-stop'), page.locator('#nd-status')
        s.srv.bus.publish(Event('turn_start', {}))
        s.srv.bus.publish(Event('text_delta', 'Turn A writes.'))
        await expect(stop).to_be_visible()
        await stop.click()
        await expect(status).to_have_text(said)
        assert await status.get_attribute('data-error') == error
        seen = await s.page.evaluate(END_THEN_START)
        gone = {'text': '', 'error': None}
        assert seen == {'afterEnd': gone, 'afterStart': gone}, seen           # gone with the event, before any render
        await s.page.wait_for_timeout(200)                                    # the render of both
        await expect(stop).to_be_visible()                                    # the view shows the next turn running
        await expect(page.locator('#nd-send')).to_have_text('Steer')
        await expect(status).to_have_text('')
        assert await status.get_attribute('data-error') is None
        assert s.errors == []
