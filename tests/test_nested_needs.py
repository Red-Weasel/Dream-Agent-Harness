"""Nested Dream P7 (DREAM-194 UI): the Needs-you stack over the permission contract (NESTED_EVENTS.md, section P7). A
`permission` event naming a worker (run_id, agent) makes that worker Needs you -- the orange pip, a waiting clock --
and puts the request in the orchestrator's stack with the tool, the reason and the choices the request itself offers,
numbered; answering POSTs /api/permission {id, choice} with the token; 200, 409 and 400 as the server says them;
permission_done closes it. The lead's own requests (no run_id) are the orchestrator's and paint no pip. Keys 1-9
answer a focused request, `i` cycles the workers that need the owner, a reconnect rebuilds what still waits, and a
failed or stopped worker offers "Ask the orchestrator to retry <agent>" through the composer's own path. Scripted
events, a scripted /api/permission (and the real one for the reconnect); no model, no engine."""
from __future__ import annotations

import asyncio
import re

import pytest
from playwright.async_api import expect

from dream.core.backends.base import Event
from test_nested_drawer import ENDINGS, lanes
from test_nested_view import _activity, open_nested, studio  # noqa: F401

pytestmark = pytest.mark.asyncio
CHOICES = {'y': 'Run in sandbox once', 'n': 'Deny', 'a': 'Always allow in sandbox (session)'}
REASON = 'Run: rm -rf build\nApproval reason: deletes files in the workspace.'
STALE = 'This request is no longer waiting for an answer'
BAD = 'Choose one of the displayed permission options'
LATER = "() => { const real = Date.now.bind(Date); Date.now = () => real() + 3 * 60000 + 5000; }"   # three minutes on


def permission(s, pid, run_id=None, agent='writer', tool='run_bash', command='rm -rf build', choices=CHOICES, reason=REASON):
    """What StudioServer.request_permission publishes: {id, tool, input, reason, choices}, plus run_id and agent when a
    worker asks (the backend's P7, 9390c3b on nested-backend)."""
    payload = {'id': pid, 'tool': tool, 'input': {'command': command}, 'reason': reason, 'choices': choices}
    if run_id:
        payload.update(run_id=run_id, agent=agent)
    s.srv.bus.publish(Event('permission', payload))
    return payload


def done(s, pid):
    s.srv.bus.publish(Event('permission_done', {'id': pid}))


async def fake_permission(s, bodies, respond):
    """/api/permission answered by `respond(body) -> (status, payload)`, or aborted when it returns None; every request
    must carry the session token."""
    async def handle(route, request):
        assert request.headers.get('x-dream-token'), 'an answer must carry the session token'
        body = request.post_data_json
        bodies.append(body)
        answer = await respond(body)
        if answer is None:
            await route.abort()
            return
        await route.fulfill(status=answer[0], json=answer[1])
    await s.page.route('**/api/permission', handle)


async def ok(body):
    return 200, {'ok': True}


async def test_a_workers_request_paints_it_orange_and_the_stack_offers_its_own_choices(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 3, {})
        stack = page.locator('#nd-stack')
        await expect(stack).to_have_text('Nothing is waiting on you.')                # the empty state
        await expect(page.locator('#nd-needs-c')).to_have_text('')
        permission(s, 'p-1', 'r-2')
        pip, win = page.locator('.nd-pips .nd-pip[data-run-id="r-2"]'), page.locator('.nd-win[data-run-id="r-2"]')
        await expect(pip).to_have_class(re.compile(r'\bnd-needs\b'))
        await expect(pip).to_have_attribute('aria-label', '2 writer: Needs you')
        needs = await page.locator('.nd-attn').evaluate("a => getComputedStyle(a.querySelector('.nd-needs')).color")
        await expect(pip).to_have_css('background-color', needs)                       # the orange of "needs you" (retried:
        # every render replaces the pips, and a one-shot read could land on a detached one under load)
        await expect(win.locator('.nd-state')).to_have_text('Needs you')
        await expect(win.locator('.nd-wait')).to_have_text('waiting <1m')
        await expect(page.locator('.nd-attn .nd-needs')).to_have_text('1 needs you')
        for other in ('r-1', 'r-3'):                                                    # the others are untouched
            await expect(page.locator(f'.nd-pips .nd-pip[data-run-id="{other}"]')).to_have_class(re.compile(r'\bnd-working\b'))
        item = stack.locator('.nd-ask')
        await expect(item).to_have_count(1)
        await expect(item.locator('.nd-who b')).to_have_text('writer')
        await expect(item.locator('.nd-who .nd-pip')).to_have_text('2')
        await expect(item.locator('.nd-ask-tool')).to_contain_text('run_bash')
        await expect(item.locator('.nd-ask-tool')).to_contain_text('rm -rf build')
        await expect(item.locator('.nd-ask-reason')).to_have_text(REASON)
        await expect(item.locator('.nd-opt')).to_have_text(['1Run in sandbox once', '2Deny', '3Always allow in sandbox (session)'])
        await expect(item).to_have_attribute('aria-label', '2 writer asks to use run_bash')
        await expect(page.locator('#nd-needs-c')).to_have_text('1')
        await expect(s.page.locator('.permission-card')).to_have_count(1)             # the chat keeps its own card...
        assert await s.page.evaluate('document.documentElement.dataset.dreamView') == 'nested'   # ...but the owner stays here
        await expect(page).to_be_visible()
        # the clock moves on by itself
        await s.page.evaluate(LATER)
        await expect(win.locator('.nd-wait')).to_have_text('waiting 3m', timeout=8000)
        await expect(item.locator('.nd-wait')).to_have_text('waiting 3m')
        # the drawer's Needs you filter lists it, and only it
        await page.locator('#nd-drawer-btn').click()
        filters = page.locator('#nd-filters button')
        await expect(filters).to_have_text(['All 3', 'Needs you 1', 'Working 2', 'Failed or stopped 0', 'Idle 0'])
        await expect(page.locator('#nd-dcount')).to_have_text('3 workers · 2 working · 1 needs you')
        await filters.nth(1).click()
        await expect(page.locator('#nd-dlist .nd-item')).to_have_count(1)
        await expect(page.locator('#nd-dlist .nd-item')).to_have_attribute('aria-label', '2 writer, Needs you')
        await s.page.keyboard.press('Escape')
        # answering: the server's 200 marks it answered; permission_done closes it and the worker is Working again
        bodies = []
        await fake_permission(s, bodies, ok)
        await item.locator('.nd-opt').nth(1).click()
        await expect(item).to_contain_text('Answered: Deny')
        assert bodies == [{'id': 'p-1', 'choice': 'n'}]
        for b in await item.locator('.nd-opt').all():
            await expect(b).to_be_disabled()
        await expect(pip).to_have_class(re.compile(r'\bnd-needs\b'))                  # nothing claimed before permission_done
        done(s, 'p-1')
        await expect(stack).to_have_text('Nothing is waiting on you.')
        await expect(pip).to_have_attribute('aria-label', '2 writer: Working')
        await expect(pip).to_have_class(re.compile(r'\bnd-working\b'))
        await expect(win.locator('.nd-state')).to_have_text('Working')
        await expect(win.locator('.nd-wait')).to_have_count(0)
        await expect(page.locator('.nd-attn .nd-needs')).to_have_count(0)
        await expect(s.page.locator('.permission-card')).to_have_count(0)
        assert s.errors == []


async def test_refusals_show_verbatim_on_the_request(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        permission(s, 'p-1', 'r-1')
        replies = [(400, {'error': BAD}), None, (409, {'error': STALE})]
        bodies = []

        async def respond(body):
            return replies.pop(0)
        await fake_permission(s, bodies, respond)
        item, opts = page.locator('#nd-stack .nd-ask'), page.locator('#nd-stack .nd-ask .nd-opt')
        await opts.nth(0).focus()
        await s.page.keyboard.press('Enter')                                          # 400: said as given, answerable again
        await expect(item.locator('.nd-note')).to_have_text(BAD)
        await expect(opts.nth(0)).to_be_enabled()
        await expect(opts.nth(0)).to_be_focused()                                     # the keyboard keeps its place
        await opts.nth(1).click()                                                     # the network fails
        await expect(item.locator('.nd-note')).to_have_text('Could not reach Dream. Reconnect and try again.')
        await expect(opts.nth(1)).to_be_enabled()
        await opts.nth(2).click()                                                     # 409: no longer waiting
        await expect(item.locator('.nd-note')).to_have_text(STALE)
        for b in await opts.all():
            await expect(b).to_be_disabled()
        assert [b['choice'] for b in bodies] == ['y', 'n', 'a'] and {b['id'] for b in bodies} == {'p-1'}
        await expect(page.locator('.nd-pips .nd-pip[data-run-id="r-1"]')).to_have_class(re.compile(r'\bnd-needs\b'))
        done(s, 'p-1')                                                                # closed: the refusal stays readable
        await expect(page.locator('#nd-stack .nd-ask')).to_have_count(0)
        await expect(page.locator('#nd-stack-note')).to_have_text(STALE)
        await expect(page.locator('.nd-pips .nd-pip[data-run-id="r-1"]')).to_have_class(re.compile(r'\bnd-working\b'))
        assert s.errors == []


async def test_the_leads_own_request_is_the_orchestrators_and_paints_no_pip(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        permission(s, 'p-lead', tool='mcp__dream__write_file', choices={'y': 'Allow once', 'n': 'Deny', 'a': 'Allow this session'},
                   reason='Write: notes.md')
        item = page.locator('#nd-stack .nd-ask')
        await expect(item).to_have_count(1)
        await expect(item.locator('.nd-who b')).to_have_text('Orchestrator')
        await expect(item.locator('.nd-who .nd-pip')).to_have_count(0)
        await expect(item.locator('.nd-ask-tool .nd-tool-name')).to_have_text('write_file')
        await expect(item).to_have_attribute('aria-label', 'Orchestrator asks to use write_file')
        await expect(page.locator('.nd-pips .nd-pip.nd-needs')).to_have_count(0)    # no worker is painted
        await expect(page.locator('.nd-win.nd-needs')).to_have_count(0)
        await expect(page.locator('.nd-attn .nd-needs')).to_have_count(0)
        await page.locator('#nd-drawer-btn').click()
        await expect(page.locator('#nd-filters button').nth(1)).to_have_text('Needs you 0')
        await s.page.keyboard.press('Escape')
        bodies = []
        await fake_permission(s, bodies, ok)
        await item.locator('.nd-opt').first.focus()
        await s.page.keyboard.press('3')                                              # its third choice
        await expect(item).to_contain_text('Answered: Allow this session')
        assert bodies == [{'id': 'p-lead', 'choice': 'a'}]
        await expect(page.locator('#nd-needs-h')).to_be_focused()                     # nothing else waits: the heading
        done(s, 'p-lead')
        await expect(page.locator('#nd-stack')).to_have_text('Nothing is waiting on you.')
        assert s.errors == []


async def test_keys_1_to_9_answer_the_focused_request_and_i_cycles_the_workers_that_need_you(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 3, {})
        permission(s, 'p-a', 'r-1')
        permission(s, 'p-b', 'r-3', choices={'y': 'Allow once', 'n': 'Deny'})
        bodies = []
        await fake_permission(s, bodies, ok)
        drawer, detail = page.locator('#nd-drawer'), page.locator('#nd-detail')
        items = page.locator('#nd-stack .nd-ask')
        await expect(items).to_have_count(2)
        await items.nth(1).locator('.nd-opt').first.focus()
        await s.page.keyboard.press('9')                                              # no ninth choice: nothing
        await s.page.keyboard.press('3')                                              # no third one either
        await asyncio.sleep(0.8)                                                      # past the drawer's digit buffer
        assert bodies == []
        await expect(drawer).to_be_hidden()                                           # digits here never open a worker
        await s.page.keyboard.press('2')
        await expect(items.nth(1)).to_contain_text('Answered: Deny')
        assert bodies == [{'id': 'p-b', 'choice': 'n'}]
        await expect(items.nth(0).locator('.nd-opt').first).to_be_focused()          # on to the next open request
        await expect(drawer).to_be_hidden()
        done(s, 'p-b')
        # `i` opens the workers that need the owner in turn; the detail offers the request's choices, and 1-9 work there
        permission(s, 'p-c', 'r-2')
        await page.locator('.nd-title').click()
        await s.page.keyboard.press('i')
        await expect(detail).to_have_attribute('data-run-id', 'r-1')
        await s.page.keyboard.press('i')
        await expect(detail).to_have_attribute('data-run-id', 'r-2')
        await s.page.keyboard.press('i')
        await expect(detail).to_have_attribute('data-run-id', 'r-1')
        ask = detail.locator('.nd-dask .nd-ask')
        await expect(ask).to_have_count(1)
        await expect(detail.locator('.nd-state').first).to_have_text('Needs you')
        await expect(detail.locator('.nd-wait').first).to_have_text('waiting <1m')
        await ask.locator('.nd-opt').first.focus()
        await s.page.keyboard.press('1')
        await expect(ask).to_contain_text('Answered: Run in sandbox once')
        assert bodies[-1] == {'id': 'p-a', 'choice': 'y'}
        await expect(page.locator('#nd-back')).to_be_focused()
        await expect(detail).to_have_attribute('data-run-id', 'r-1')                 # a digit answered, never opened worker 1
        assert s.errors == []


async def test_a_reconnect_rebuilds_the_requests_that_still_wait(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {})

        def waiting(payload):
            """What request_permission holds while it waits: the real /api/permission and the reconnect replay read it."""
            future = asyncio.get_running_loop().create_future()
            s.srv._permissions[payload['id']] = (payload, future)
            return future
        worker = waiting(permission(s, 'p-w', 'r-2'))
        lead = waiting(permission(s, 'p-l', choices={'y': 'Yes', 'n': 'No'}, tool='Confirm action', reason='Continue?'))
        await expect(page.locator('#nd-stack .nd-ask')).to_have_count(2)
        await s.page.evaluate(LATER)
        await expect(page.locator('#nd-stack .nd-wait').first).to_have_text('waiting 3m', timeout=8000)
        # a reconnect (the socket's history, then every waiting request again) keeps the items and their clocks
        await s.page.evaluate("""() => { const send = m => window.dispatchEvent(new CustomEvent('dream:event', {detail: {m, replay: false}}));
            send({kind: 'history', data: {events: []}}); }""")
        for pid in ('p-w', 'p-l'):
            payload = s.srv._permissions[pid][0]
            await s.page.evaluate("p => window.dispatchEvent(new CustomEvent('dream:event', {detail: {m: {kind: 'permission', data: p}, replay: false}}))", payload)
        await expect(page.locator('#nd-stack .nd-ask')).to_have_count(2)
        await expect(page.locator('#nd-stack .nd-wait').first).to_have_text('waiting 3m')
        # a reload: the server replays the lanes and then both waiting requests
        await s.page.reload()
        await expect(s.page.locator('#stat')).to_have_text('Waiting for you')        # the chat rebuilt its cards too
        page = await open_nested(s)
        items = page.locator('#nd-stack .nd-ask')
        await expect(items).to_have_count(2)
        await expect(items.nth(0).locator('.nd-who b')).to_have_text('writer')
        await expect(items.nth(1).locator('.nd-who b')).to_have_text('Orchestrator')
        await expect(page.locator('.nd-pips .nd-pip[data-run-id="r-2"]')).to_have_class(re.compile(r'\bnd-needs\b'))
        await expect(page.locator('.nd-pips .nd-pip[data-run-id="r-1"]')).to_have_class(re.compile(r'\bnd-working\b'))
        # answered through the real route, which resolves the waiting request
        await items.nth(0).locator('.nd-opt').nth(1).click()
        assert await asyncio.wait_for(worker, 5) == 'n'
        await expect(items.nth(0)).to_contain_text('Answered: Deny')
        s.srv._permissions.pop('p-w')
        done(s, 'p-w')
        await expect(items).to_have_count(1)
        await expect(page.locator('.nd-pips .nd-pip[data-run-id="r-2"]')).to_have_class(re.compile(r'\bnd-working\b'))
        # answered elsewhere while this page was away: a reload no longer shows it
        lead.set_result('y')
        s.srv._permissions.pop('p-l')
        await s.page.reload()
        await expect(s.page.locator('#stat')).to_have_text('Ready')
        page = await open_nested(s)
        await expect(page.locator('#nd-stack')).to_have_text('Nothing is waiting on you.')
        assert s.errors == []


async def test_a_failed_or_stopped_worker_offers_a_retry_through_the_composer(studio):
    async with studio() as s:
        page = await open_nested(s)
        s.srv.bus.publish(Event('turn_start', {}))                                      # the workers run in this turn
        lanes(s, 4, {'r-1': 'completed', 'r-2': 'failed', 'r-3': 'interrupted', 'r-4': 'completed'})   # none left for the turn's end to settle
        items = page.locator('#nd-stack .nd-ask-fail')
        await expect(items).to_have_count(2)                                          # the failed and the stopped, never the done
        await expect(page.locator('#nd-needs-c')).to_have_text('2')
        first, second = items.nth(0), items.nth(1)
        await expect(first.locator('.nd-state')).to_have_text('Failed')
        await expect(first.locator('.nd-ask-reason')).to_have_text(ENDINGS['failed'])
        await expect(second.locator('.nd-state')).to_have_text('Stopped')
        retry = first.get_by_role('button', name='Ask the orchestrator to retry writer', exact=True)
        await expect(retry).to_have_count(1)
        # during the turn it steers the orchestrator, like the composer
        await expect(page.locator('#nd-send')).to_have_text('Steer')
        await retry.click()
        await expect(page.locator('#nd-status')).to_contain_text('next model request')
        assert [t for t, _, _ in s.steers] == [f"Please retry the writer worker; it failed: {ENDINGS['failed']}"]
        await expect(page.locator('.nd-msg.nd-you').last).to_contain_text('Please retry the writer worker; it failed')
        assert s.prompts == []
        # between turns it is a message to the orchestrator
        s.srv.bus.publish(Event('result', {}))
        s.srv.bus.publish(Event('turn_end', {}))
        await expect(page.locator('#nd-send')).to_have_text('Send')
        await second.get_by_role('button', name='Ask the orchestrator to retry writer', exact=True).click()
        await expect(page.locator('#nd-status')).to_have_text('Sent to the orchestrator.')
        assert s.prompts == [f"Please retry the writer worker; it was stopped: {ENDINGS['interrupted']}"]
        await expect(page.locator('#nd-compose')).to_have_value('')                  # the composer's own words untouched
        # the drawer's detail offers it for the failed worker, not for the finished one
        await page.locator('.nd-pips .nd-pip[data-run-id="r-2"]').click()
        detail = page.locator('#nd-detail')
        await expect(detail.get_by_role('button', name='Ask the orchestrator to retry writer', exact=True)).to_have_count(1)
        await expect(detail.get_by_role('button', name='Ask the orchestrator to retry writer', exact=True)).to_be_disabled()   # asked already
        await page.locator('#nd-back').click()
        await page.locator('#nd-dlist .nd-item[data-run-id="r-4"]').click()
        await expect(detail).to_have_attribute('data-run-id', 'r-4')
        await expect(detail.get_by_role('button', name=re.compile('retry', re.I))).to_have_count(0)
        assert s.errors == []


async def test_at_a_short_height_the_stack_gives_way_before_the_composer(studio):
    async with studio(width=1920, height=600) as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        s.srv.bus.publish(Event('plan', {'title': 'Ship the Nested view with its tests and its docs'}))
        for i in range(3):
            permission(s, f'p-{i}', 'r-1' if i else None)
        await expect(page.locator('#nd-stack .nd-ask')).to_have_count(3)
        orch = page.locator('.nd-orch')
        await expect(page.locator('#nd-foot')).to_be_attached()                      # P12's footer (frontend B), under the composer
        b = await orch.evaluate("""e => { const r = s => e.querySelector(s).getBoundingClientRect();
            return {compose: r('#nd-compose').bottom, send: r('#nd-send').bottom, footer: r('#nd-foot').bottom, inner: innerHeight,
                    head: r('#nd-needs-h').bottom, sec: r('#nd-needs-sec').bottom}; }""")
        assert b['compose'] <= b['inner'] and b['send'] <= b['inner'] and b['footer'] <= b['inner'], b
        assert b['head'] <= b['sec'] + 0.5, b                                          # its heading stays in sight
        assert await page.locator('#nd-stack-rest').evaluate('e => e.scrollHeight > e.clientHeight')  # the other requests scroll in their box
        assert s.errors == []
