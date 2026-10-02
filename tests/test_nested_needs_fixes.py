"""The P7 UI gate's findings on 5028402 (gate-reports/p7ui-r1.md), each shown failing first: a question form open in
Chat is pointed to from the Needs-you stack; at short heights with a phased plan the picture gives way first, the empty
line costs nothing, the first waiting request's choices and the composer's Send and footer stay in view; a digit typed
outside a request answers nothing, and every needing worker shows its clock on its card and its drawer row; a retry
asked says so and turns its button off, and a finished turn's failures leave the stack; a request closes the Context
panel without leaving the Nested view. Scripted events, a scripted /api/permission; no model, no engine."""
from __future__ import annotations

import asyncio

import pytest
from playwright.async_api import expect

from dream.core.backends.base import Event
from test_nested_drawer import lanes
from test_nested_needs import fake_permission, ok, permission
from test_nested_plan import PHASES, plan
from test_nested_view import open_nested, studio  # noqa: F401

pytestmark = pytest.mark.asyncio
FORM = {'title': 'Release notes', 'questions': [
    {'id': 'scope', 'title': 'Which subsystems should the notes cover?\nPick every one that changed.', 'kind': 'text-options',
     'options': ['Engine', 'Desktop']},
    {'id': 'leave', 'title': 'Anything to leave out?', 'kind': 'freeform'}]}
SHELL = {'y': 'Run in sandbox once', 'n': 'Deny', 'a': 'Always allow in sandbox (session)'}
VIEW = 'document.documentElement.dataset.dreamView'
# A control is in view when it lies inside the viewport and is what the pointer would reach at its centre.
SEEN = """e => { const b = e.getBoundingClientRect(); if (b.top < 0 || b.bottom > innerHeight + 0.5 || b.height === 0) return false;
  const t = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2); return t === e || e.contains(t); }"""
# The Nested page settled: nothing in it has changed for 400 ms, so no render is still to come from what went before.
QUIET = """() => new Promise(done => { const page = document.getElementById('dream-nested-page'), t0 = performance.now();
  let last = t0; const mo = new MutationObserver(() => { last = performance.now(); });
  mo.observe(page, {subtree: true, childList: true, characterData: true, attributes: true});
  const tick = () => { const now = performance.now();
    if (now - last >= 400 || now - t0 > 8000) { mo.disconnect(); done(now - last >= 400); } else setTimeout(tick, 50); };
  setTimeout(tick, 50); })"""


# --- 1. a question form open in Chat is pointed to from the stack -------------------------------------------------

async def test_a_question_form_in_chat_is_pointed_to_from_the_stack(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        await expect(page.locator('#nd-stack')).to_have_text('Nothing is waiting on you.')
        # the Agents control's read on opening the view (P10) is answered and rendered: from here only the chat's form
        # can bring its pointer (the gate's N05 went unseen while that read's render came a moment after the form)
        await expect(page.locator('#nd-cap-note')).to_have_text('Runtime controls are unavailable')
        # and nothing is still to render: a late render (the first item's ResizeObserver after the lanes' layout, 10 ms
        # after the form) drew the pointer without the form observer, and N05 lived in one of this round's mutant runs
        assert await s.page.evaluate(QUIET)
        s.srv.bus.publish(Event('studio', {'op': 'ask', 'form': FORM}))                 # questions_v2 and ask_user_input send this
        item = page.locator('#nd-stack .nd-ask-form')
        await expect(item).to_have_count(1)
        await expect(item.locator('.nd-who b')).to_have_text('Orchestrator')
        await expect(item.locator('.nd-who .nd-state')).to_have_text('2 questions in Chat')
        await expect(item.locator('.nd-ask-tool')).to_have_text('Release notes')
        await expect(item.locator('.nd-ask-reason')).to_have_text('Which subsystems should the notes cover?')   # the first line only
        await expect(page.locator('#nd-needs-c')).to_have_text('1')
        assert await s.page.evaluate(VIEW) == 'nested'
        # Open Chat lands on the form, its first answer focused
        await item.get_by_role('button', name='Open Chat', exact=True).click()
        assert await s.page.evaluate(VIEW) == 'chat'
        form = s.page.locator('#stream form.qform')
        await expect(form).to_be_visible()
        await expect(form.locator('input').first).to_be_focused()
        # answered in Chat: the pointer clears
        await form.locator('input[value="Engine"]').check()
        await form.get_by_role('button', name='Send answers').click()
        await expect(form).to_have_count(0)
        assert 'Engine' in s.prompts[-1]
        page = await open_nested(s)
        await expect(page.locator('#nd-stack')).to_have_text('Nothing is waiting on you.')
        await expect(page.locator('#nd-needs-c')).to_have_text('')
        # two forms, two pointers; a reconnect (the history again) keeps them while the chat keeps its forms
        s.srv.bus.publish(Event('studio', {'op': 'ask', 'form': FORM}))
        s.srv.bus.publish(Event('studio', {'op': 'ask', 'form': {'title': 'One more', 'questions': [{'id': 'q', 'title': 'Ship it?', 'kind': 'freeform'}]}}))
        await expect(item).to_have_count(2)
        await expect(item.nth(1).locator('.nd-who .nd-state')).to_have_text('1 question in Chat')
        await s.page.evaluate("window.dispatchEvent(new CustomEvent('dream:event', {detail: {m: {kind: 'history', data: {events: []}}, replay: false}}))")
        await expect(item).to_have_count(2)
        await expect(page.locator('#nd-needs-c')).to_have_text('2')
        # a reload: the chat keeps no form (forms are not replayed), and neither does the stack
        await s.page.reload()
        await expect(s.page.locator('#stat')).to_have_text('Ready')
        page = await open_nested(s)
        assert await s.page.locator('#stream form.qform').count() == 0
        await expect(page.locator('#nd-stack')).to_have_text('Nothing is waiting on you.')
        assert s.errors == []


# --- 2. short heights with a phased plan ------------------------------------------------------------------------------

def busy(s, waiting=True):
    """The gate's S1 scene: eight workers (two failed), the owner's goal, a phased plan, and while `waiting` six worker
    requests and the orchestrator's own."""
    lanes(s, 8, {'r-7': 'failed', 'r-8': 'interrupted'} if waiting else {f'r-{i}': 'completed' for i in range(1, 9)})
    s.srv.bus.publish(Event('user', 'Ship the Needs-you stack with its tests and its docs'))
    s.srv.bus.publish(plan(PHASES, title='Ship the Needs-you stack'))
    if waiting:
        s.srv.bus.publish(Event('turn_start', {}))
        for i in range(1, 7):
            permission(s, f'p-{i}', f'r-{i}', command=f'pytest tests/test_part_{i}.py -q', choices=SHELL,
                       reason=f'Run: pytest tests/test_part_{i}.py -q\nApproval reason: runs the tests of part {i}.')
        permission(s, 'p-lead', tool='mcp__dream__write_file', choices={'y': 'Allow once', 'n': 'Deny', 'a': 'Allow this session'},
                   reason='Write: docs/nested-dream.md')


@pytest.mark.parametrize('w,h', [(1920, 600), (901, 700), (1280, 600), (1366, 768)])
async def test_at_short_heights_the_first_request_send_and_the_footer_stay_in_view(studio, w, h):
    async with studio(width=w, height=h) as s:
        page = await open_nested(s)
        busy(s)
        await expect(page.locator('#nd-stack .nd-ask[data-ask]')).to_have_count(7)
        await expect(page.locator('#nd-plan')).to_be_visible()                          # the phased plan is on screen
        await s.page.wait_for_timeout(300)
        first = page.locator('#nd-stack .nd-ask[data-ask]').first
        await expect(first.locator('.nd-opt')).to_have_count(3)
        for i in range(3):                                                              # every choice of the first request
            assert await first.locator('.nd-opt').nth(i).evaluate(SEEN), (w, h, i)
        assert await page.locator('#nd-send').evaluate(SEEN), (w, h)
        foot = await page.locator('#nd-foot').evaluate('e => e.getBoundingClientRect().bottom')
        assert foot <= await s.page.evaluate('innerHeight') + 0.5, (w, h, foot)
        assert await page.locator('.nd-hero').evaluate('e => e.getBoundingClientRect().height') < 128, (w, h)   # the picture gave way first
        assert s.errors == []


@pytest.mark.parametrize('w,h', [(1920, 600), (901, 700), (1280, 600), (1366, 768)])
async def test_at_short_heights_nothing_waiting_costs_no_line(studio, w, h):
    async with studio(width=w, height=h) as s:
        page = await open_nested(s)
        busy(s, waiting=False)
        await expect(page.locator('#nd-plan')).to_be_visible()
        await s.page.wait_for_timeout(300)
        assert await page.locator('#nd-needs-sec').evaluate('e => e.getBoundingClientRect().height') == 0, (w, h)
        assert await page.locator('#nd-send').evaluate(SEEN), (w, h)
        foot = await page.locator('#nd-foot').evaluate('e => e.getBoundingClientRect().bottom')
        assert foot <= await s.page.evaluate('innerHeight') + 0.5, (w, h, foot)
        assert s.errors == []


# --- 3. a digit outside a request answers nothing; every needing worker shows its clock ------------------------------

async def test_a_digit_outside_a_request_answers_nothing_and_every_needing_worker_shows_its_clock(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 3, {})
        permission(s, 'p-1', 'r-1')
        permission(s, 'p-3', 'r-3')
        permission(s, 'p-lead', tool='write_file', choices={'y': 'Allow once', 'n': 'Deny'}, reason='Write: notes.md')
        await expect(page.locator('#nd-stack .nd-ask[data-ask]')).to_have_count(3)
        bodies = []
        await fake_permission(s, bodies, ok)
        compose = page.locator('#nd-compose')
        await compose.click()
        await s.page.keyboard.type('1 2 3 then 9')
        await expect(compose).to_have_value('1 2 3 then 9')                             # typed as typed
        await page.locator('#nd-needs-h').focus()                                       # the stack's heading
        await s.page.keyboard.press('2')
        await asyncio.sleep(0.8)
        await s.page.keyboard.press('Escape')
        await page.locator('.nd-card[data-run-id="r-3"]').focus()                       # a card
        await s.page.keyboard.press('1')
        await asyncio.sleep(0.8)
        assert bodies == []                                                             # nothing answered anything
        await expect(page.locator('#nd-stack .nd-ask-done')).to_have_count(0)
        await s.page.keyboard.press('Escape')
        # the clock on the needing worker's card, window and drawer row; none on the others
        await expect(page.locator('.nd-card[data-run-id="r-3"] .nd-wait')).to_have_text('waiting <1m')
        await expect(page.locator('.nd-win[data-run-id="r-1"] .nd-wait')).to_have_text('waiting <1m')
        await expect(page.locator('.nd-win[data-run-id="r-2"] .nd-wait')).to_have_count(0)
        await page.locator('#nd-drawer-btn').click()
        rows = page.locator('#nd-dlist .nd-item')
        await expect(rows).to_have_count(3)
        for run, clock in (('r-1', 1), ('r-2', 0), ('r-3', 1)):
            await expect(page.locator(f'#nd-dlist .nd-item[data-run-id="{run}"] .nd-wait')).to_have_count(clock)
        await expect(page.locator('#nd-dlist .nd-item[data-run-id="r-3"] .nd-wait')).to_have_text('waiting <1m')
        assert s.errors == []


# --- 4. a retry asked says so; a finished turn's failures leave the stack -------------------------------------------

async def test_a_retry_asked_is_said_and_a_finished_turns_failures_leave_the_stack(studio):
    async with studio() as s:
        page = await open_nested(s)
        s.srv.bus.publish(Event('turn_start', {}))
        lanes(s, 3, {'r-1': 'completed', 'r-2': 'failed', 'r-3': 'interrupted'})
        items = page.locator('#nd-stack .nd-ask-fail')
        await expect(items).to_have_count(2)
        retry = items.nth(0).get_by_role('button', name='Ask the orchestrator to retry writer', exact=True)
        await retry.click()
        await expect(items.nth(0)).to_contain_text('Retry asked')
        await expect(retry).to_be_disabled()
        await retry.click(force=True)                                                   # a second click sends nothing
        await asyncio.sleep(0.3)
        assert len(s.steers) == 1
        await page.locator('.nd-pips .nd-pip[data-run-id="r-2"]').click()              # the detail says the same
        detail = page.locator('#nd-detail')
        await expect(detail.get_by_role('button', name='Ask the orchestrator to retry writer', exact=True)).to_be_disabled()
        await expect(detail).to_contain_text('Retry asked')
        await s.page.keyboard.press('Escape')
        await s.page.keyboard.press('Escape')
        s.srv.bus.publish(Event('result', {}))
        s.srv.bus.publish(Event('turn_end', {}))
        await expect(items).to_have_count(2)                                            # the turn's own failures stay after it ends
        s.srv.bus.publish(Event('turn_start', {}))                                      # the next turn: they leave the stack
        await expect(page.locator('#nd-stack')).to_have_text('Nothing is waiting on you.')
        await expect(page.locator('#nd-needs-c')).to_have_text('')
        await page.locator('#nd-drawer-btn').click()
        await page.locator('#nd-filters [data-f="failed"]').click()
        await expect(page.locator('#nd-dlist .nd-item')).to_have_count(2)               # still under Failed or stopped
        assert s.errors == []


# --- 5. a request closes the Context panel and keeps the Nested view ------------------------------------------------

async def test_a_request_closes_the_context_panel_and_keeps_the_nested_view(studio):
    async with studio(width=901, height=700) as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        await s.page.locator('#dream-inspect-toggle').evaluate('b => b.click()')
        await expect(s.page.locator('#dream-inspector')).to_be_visible()
        permission(s, 'p-1', 'r-1')
        await expect(page.locator('#nd-stack .nd-ask[data-ask]')).to_have_count(1)
        await expect(s.page.locator('#dream-inspector')).to_be_hidden()
        assert await s.page.evaluate(VIEW) == 'nested'
        assert await page.locator('#nd-stack .nd-opt').first.evaluate(SEEN)
        assert s.errors == []


# --- gate P7 UI round 2 (gate-reports/p7ui-r2.md) -------------------------------------------------------------------

# What tui/app.py asks for a native shell command: the command's summary, the approval reason and the sandbox note; its
# buttons are y and n, then h, a and ha when the host option and the remember flags apply.
SANDBOX_NOTE = ('\nSandbox execution keeps workspace protection. Outside-sandbox execution uses your normal host access. A '
                'once choice does not remember approval. Always choices remember only this exact command in this workspace '
                'for this session, not other commands or future sessions.')
SHELL3 = {'y': 'Run in sandbox once', 'n': 'Deny', 'a': 'Always allow in sandbox (session)'}
SHELL5 = {'y': 'Run in sandbox once', 'n': 'Deny', 'h': 'Run outside sandbox once', 'a': 'Always allow in sandbox (session)',
          'ha': 'Always allow outside sandbox (session)'}
SHORT = [(1920, 600), (901, 700), (1280, 600), (1366, 768)]
# Inside the viewport, and what the pointer reaches at its left, its centre and its right (the gate's measure).
HIT = """e => { const b = e.getBoundingClientRect();
  if (b.height === 0 || b.top < -0.5 || b.bottom > innerHeight + 0.5 || b.left < -0.5 || b.right > innerWidth + 0.5) return false;
  const y = (b.top + b.bottom) / 2;
  return [b.left + 10, (b.left + b.right) / 2, b.right - 10].every(x => { const t = document.elementFromPoint(x, y); return !!t && (t === e || e.contains(t)); }); }"""
# The focused control: on screen, above the composer, and what the pointer reaches at its centre.
FOCUSED = """() => { const a = document.activeElement, b = a.getBoundingClientRect(),
  foot = document.querySelector('#dream-nested-page .nd-composer').getBoundingClientRect();
  const t = document.elementFromPoint((b.left + b.right) / 2, (b.top + b.bottom) / 2);
  return {cls: String(a.className), text: a.textContent.trim().slice(0, 40), in_stack: !!a.closest('#nd-stack'),
          in_composer: !!a.closest('#dream-nested-page .nd-composer'),
          on_screen: b.height > 0 && b.top >= 0 && b.bottom <= Math.min(innerHeight, foot.top) + 0.5 && !!t && (t === a || a.contains(t))}; }"""
# The orchestrator column's scroll box: its body above the composer (gate P7 r4), or the column itself before that.
SCROLLER = "(document.querySelector('#dream-nested-page .nd-orch-body') || document.querySelector('#dream-nested-page .nd-orch'))"
# The conversation's own list on screen: inside its scroll box, the viewport and above the composer.
CONVO_SEEN = """() => { const q = s => document.querySelector(s).getBoundingClientRect();
  const c = q('#nd-convo'), box = q('#nd-orch-scroll'), foot = q('#dream-nested-page .nd-composer');
  return Math.max(0, Math.round(Math.min(c.bottom, box.bottom, foot.top, innerHeight) - Math.max(c.top, box.top, 0))); }"""


def shell_request(s, pid='p-1', run='r-1', choices=SHELL5):
    from dream.tui.render import summarize_tool_input
    command = 'pytest tests/test_part_1.py -q'
    reason = summarize_tool_input('run_bash', {'command': command}) + '\nApproval reason: runs the tests of part 1.' + SANDBOX_NOTE
    permission(s, pid, run, command=command, choices=choices, reason=reason)


def working_turn(s, workers, endings=None, form=False, talk=1):
    """The owner's goal, a phased plan, a turn under way with `talk` exchanges in the conversation, its workers, and
    maybe a question form open in Chat."""
    s.srv.bus.publish(Event('user', 'Ship the Needs-you stack with its tests and its docs'))
    s.srv.bus.publish(plan(PHASES, title='Ship the Needs-you stack'))
    s.srv.bus.publish(Event('turn_start', {}))
    for i in range(talk):
        if i:
            s.srv.bus.publish(Event('user', f'Keep going with part {i + 1}.'))
        s.srv.bus.publish(Event('text_delta', f'Part {i + 1}: I split the work into three pieces and started the workers; '
                                              'the first one runs its tests now.'))
        s.srv.bus.publish(Event('assistant_done', {}))
    lanes(s, workers, endings or {})
    if form:
        s.srv.bus.publish(Event('studio', {'op': 'ask', 'form': FORM}))


@pytest.mark.parametrize('n', [3, 5])
@pytest.mark.parametrize('w,h', SHORT)
async def test_the_real_shell_request_keeps_every_choice_in_view_at_short_heights(studio, w, h, n):
    """Gate P7 r2 #1: with the app's own text (a long sandbox note) the choices sat below the fold (1 of 5 in view at
    1280 x 600, Deny under the composer). At a short height the reason shows its first lines and a toggle, so every
    choice of the first request is in view and reached by the pointer at its left, centre and right."""
    async with studio(width=w, height=h) as s:
        page = await open_nested(s)
        working_turn(s, 4)
        shell_request(s, choices={3: SHELL3, 5: SHELL5}[n])
        first = page.locator('#nd-stack .nd-ask[data-ask]').first
        await expect(first.locator('.nd-opt')).to_have_count(n)
        await s.page.wait_for_timeout(300)                                              # the reveal runs after the render
        seen = [await c.evaluate(HIT) for c in await first.locator('.nd-opt').all()]
        assert all(seen), (w, h, n, seen)
        room = await first.evaluate("""a => document.querySelector('#dream-nested-page .nd-composer').getBoundingClientRect().top
            - Math.max(...[...a.querySelectorAll('.nd-opt')].map(o => o.getBoundingClientRect().bottom))""")
        assert room >= 8, (w, h, n, room)                                               # not flush against the composer
        assert await page.locator('#nd-send').evaluate(HIT), (w, h, n)
        more = first.get_by_role('button', name='Show the whole reason', exact=True)
        await first.locator('.nd-opt').last.focus()
        await s.page.keyboard.press('Tab')                                              # the toggle, after the choices
        await expect(more).to_be_focused()
        await s.page.wait_for_timeout(150)
        assert (await s.page.evaluate(FOCUSED))['on_screen'], (w, h, n)                 # in view, not under the composer
        assert s.errors == []


async def test_a_long_reason_opens_whole_from_its_toggle(studio):
    """Gate P7 r2 #1: the cut reason's toggle is a labelled button that says what it controls and whether it is open;
    Enter and Space work it and the focus stays on it. A tall window shows every reason whole, with no toggle, and so
    does a short window for a reason that fits in two lines."""
    async with studio(width=1280, height=600) as s:
        page = await open_nested(s)
        working_turn(s, 4)
        shell_request(s)
        permission(s, 'p-2', 'r-2')                                                    # a short reason, in the rest box
        first = page.locator('#nd-stack .nd-ask[data-ask="p-1"]')
        reason = first.locator('.nd-ask-reason')
        more = first.get_by_role('button', name='Show the whole reason', exact=True)
        await expect(more).to_have_attribute('aria-expanded', 'false')
        assert await more.get_attribute('aria-controls') == await reason.get_attribute('id')
        await expect(reason).to_be_visible()                                            # laid out before it is measured
        # read in the page, in one call: every render replaces the stack, so a handle taken before one reads a node gone
        live = "document.querySelector('#nd-stack .nd-ask[data-ask=\\'p-1\\'] .nd-ask-reason')"
        cut = await s.page.evaluate(f'() => {{ const r = {live}; return [r.scrollHeight, r.clientHeight, parseFloat(getComputedStyle(r).lineHeight)]; }}')
        assert cut[0] > cut[1] + 1 and cut[1] <= 2 * cut[2] + 1, cut                     # its first two lines
        await expect(page.locator('#nd-stack .nd-ask[data-ask="p-2"] .nd-more')).to_be_hidden()
        await first.locator('.nd-opt').last.focus()
        await s.page.keyboard.press('Tab')
        await expect(more).to_be_focused()
        await s.page.wait_for_timeout(150)
        assert (await s.page.evaluate(FOCUSED))['on_screen']                           # Gate P7 r3 #1b: was under the composer
        await s.page.keyboard.press('Enter')
        less = first.get_by_role('button', name='Show less', exact=True)
        await expect(less).to_have_attribute('aria-expanded', 'true')
        await expect(less).to_be_focused()
        await s.page.wait_for_timeout(200)
        assert (await s.page.evaluate(FOCUSED))['on_screen']                           # the whole reason pushed it down: shown again
        assert await s.page.evaluate(f'() => {{ const r = {live}; return r.scrollHeight - r.clientHeight; }}') <= 1   # whole
        await expect(reason).to_contain_text('future sessions.')
        await s.page.keyboard.press('Space')
        await expect(more).to_have_attribute('aria-expanded', 'false')
        await expect(more).to_be_focused()
        await s.page.wait_for_timeout(200)
        assert (await s.page.evaluate(FOCUSED))['on_screen']
        await s.page.set_viewport_size({'width': 1920, 'height': 953})
        await expect(first.locator('.nd-more')).to_be_hidden()
        await s.page.wait_for_timeout(200)
        assert await s.page.evaluate(f'() => {{ const r = {live}; return r.scrollHeight - r.clientHeight; }}') <= 1
        assert s.errors == []


@pytest.mark.parametrize('w,h,busy_turn,at_rest', [(2554, 1338, True, True), (1920, 953, True, False), (1920, 1000, True, False),
                                                   (1920, 1000, False, True), (1920, 953, False, True)])
async def test_the_conversation_keeps_its_room_while_the_stack_gives_way(studio, w, h, busy_turn, at_rest):
    """Gate P7 r2 #2: the stack squeezed the conversation to 0 px (a real request, a form and two failures at 2554 x
    1338; one real request at 1920 x 1000). The items after the first give way first -- their box shrinks to about one
    item and scrolls (gate P7 r3 #1) -- so the conversation keeps its room: at least 120 px of its list on screen with
    every choice of the first request, or, in the busy stack at 1920 x 953 and 1920 x 1000, once the column is scrolled
    to it; and the plan tracker stays reachable: in view once the column is scrolled to its end."""
    async with studio(width=w, height=h) as s:
        page = await open_nested(s)
        working_turn(s, 6, {'r-5': 'failed', 'r-6': 'interrupted'} if busy_turn else None, form=busy_turn, talk=6)
        shell_request(s)
        await expect(page.locator('#nd-stack .nd-ask')).to_have_count(4 if busy_turn else 1)
        await s.page.wait_for_timeout(300)
        for choice in await page.locator('#nd-stack .nd-ask[data-ask] .nd-opt').all():
            assert await choice.evaluate(HIT), (w, h, busy_turn)
        assert await page.locator('#nd-orch-scroll').evaluate('c => c.getBoundingClientRect().height') >= 191.5   # its room
        if not at_rest:
            await page.locator('#nd-convo').evaluate("c => c.scrollIntoView({block: 'nearest'})")               # the owner scrolls to it
            await s.page.wait_for_timeout(200)
        seen = await s.page.evaluate(CONVO_SEEN)
        assert seen >= 120, (w, h, busy_turn, seen)
        await s.page.evaluate(f'() => {{ const c = {SCROLLER}; c.scrollTop = c.scrollHeight; }}')      # the owner scrolls down
        await s.page.wait_for_timeout(200)
        assert await page.locator('#nd-plan-toggle').evaluate(HIT), (w, h, busy_turn)
        plan_box = await page.locator('#nd-plan').evaluate("""p => { const b = p.getBoundingClientRect(),
            f = document.querySelector('#dream-nested-page .nd-composer').getBoundingClientRect(); return [b.top, b.bottom, f.top]; }""")
        assert plan_box[0] >= 0 and plan_box[1] <= plan_box[2] + 0.5, plan_box                      # the whole tracker
        assert s.errors == []


async def test_a_retry_whose_message_fails_can_be_asked_again(studio):
    """Gate P7 r2 #3 (mutant N11): "Retry asked" only once the orchestrator has the message: a failed POST leaves the
    item as it was, its button on, and the next click sends the same steer again."""
    async with studio() as s:
        page = await open_nested(s)
        s.srv.bus.publish(Event('turn_start', {}))
        lanes(s, 2, {'r-1': 'failed', 'r-2': 'interrupted'})
        posts, fail = [], [True]

        async def handle(route, request):
            posts.append(request.post_data_json)
            if fail[0]:
                fail[0] = False
                await route.fulfill(status=503, json={'error': 'Scripted: the steer could not be saved'})
            else:
                await route.continue_()
        await s.page.route('**/api/prompt', handle)
        item = page.locator('#nd-stack .nd-ask-fail').first
        retry = item.get_by_role('button', name='Ask the orchestrator to retry writer', exact=True)
        await retry.click()
        await expect(page.locator('#nd-status')).to_have_text('Scripted: the steer could not be saved')
        await expect(item).not_to_contain_text('Retry asked')
        await expect(retry).to_be_enabled()
        await retry.click()
        await expect(item).to_contain_text('Retry asked')
        await expect(retry).to_be_disabled()
        assert len(posts) == 2 and posts[0].get('steering_id') and posts[0].get('steering_id') == posts[1].get('steering_id'), posts
        assert len(s.steers) == 1 and s.errors == []


async def test_the_column_stays_where_the_owner_scrolled_it(studio):
    """Gate P7 r2 #3 (mutant N15): at a short height the column shows the first waiting request; once the owner scrolls
    the column, renders leave it where the owner put it for a few seconds (a streaming reply renders twice a second)."""
    async with studio(width=1920, height=600) as s:
        page = await open_nested(s)
        busy(s)
        await expect(page.locator('#nd-stack .nd-ask[data-ask]')).to_have_count(7)
        top = f'() => {SCROLLER}.scrollTop'
        await s.page.wait_for_timeout(400)
        assert await s.page.evaluate(top) > 0                                           # scrolled to the first request
        await page.locator('#nd-goal-wrap').hover()
        await s.page.mouse.wheel(0, -3000)                                               # the owner, back to the goal
        await s.page.wait_for_function(f'() => {SCROLLER}.scrollTop === 0')
        for i in range(6):                                                              # three seconds of streaming
            s.srv.bus.publish(Event('text_delta', f'piece {i} '))
            await s.page.wait_for_timeout(500)
            assert await s.page.evaluate(top) == 0, i
        assert s.errors == []


@pytest.mark.parametrize('w,h', [(2554, 1338), (1920, 953), (1366, 768)])
async def test_the_rest_box_scrolls_its_items_and_never_spills(studio, w, h):
    """Gate P7 r2 #3 (mutant N16) and r3 #1: the items after the first scroll inside their own box, which shows about
    one item or more (6rem, or all of them when they are less): scrolled to its end, the last item ends within the box,
    and the box ends above the conversation."""
    async with studio(width=w, height=h) as s:
        page = await open_nested(s)
        busy(s)
        rest = page.locator('#nd-stack-rest')
        await expect(rest.locator('.nd-ask')).to_have_count(6)                           # the six requests after the first
        b = await rest.evaluate("""r => { r.scrollTop = r.scrollHeight; const box = r.getBoundingClientRect(), last = r.lastElementChild.getBoundingClientRect();
            return {overflow: getComputedStyle(r).overflowY, scrolls: r.scrollHeight > r.clientHeight + 1, top: box.top, bottom: box.bottom,
                    lastTop: last.top, lastBottom: last.bottom, convo: document.getElementById('nd-orch-scroll').getBoundingClientRect().top}; }""")
        assert b['overflow'] == 'auto' and b['scrolls'] and b['bottom'] - b['top'] >= 95.5, b
        assert b['top'] - 0.5 <= b['lastBottom'] <= b['bottom'] + 0.5, b
        assert b['bottom'] <= b['convo'] + 0.5, b
        assert s.errors == []


# --- gate P7 UI round 3 (gate-reports/p7ui-r3.md) -------------------------------------------------------------------

@pytest.mark.parametrize('w,h', [(1920, 953), (1366, 768)])
async def test_every_item_of_the_stack_can_be_seen_and_reached_from_the_keyboard(studio, w, h):
    """Gate P7 r3 #1: with one real request and the turn's two failures, the rest box gave way to 0-1 px: the failures
    and their Retry buttons were invisible yet in the Tab order, and Enter on the next Tab stop sent a retry the owner
    could not see. The rest box keeps about one item (6rem, or all of it when less); every Retry can be scrolled into
    view and hit; every Tab stop from the request's last choice to the composer is on screen, above the composer, as
    it takes the focus; and Enter acts on the one the owner sees."""
    async with studio(width=w, height=h) as s:
        page = await open_nested(s)
        working_turn(s, 6, {'r-5': 'failed', 'r-6': 'interrupted'})
        shell_request(s)
        await expect(page.locator('#nd-stack .nd-ask')).to_have_count(3)
        await expect(page.locator('#nd-needs-c')).to_have_text('3')
        await s.page.wait_for_timeout(300)
        rest = page.locator('#nd-stack-rest')
        room = await rest.evaluate('r => [r.getBoundingClientRect().height, Math.min(r.scrollHeight, 96)]')
        assert room[0] >= room[1] - 0.5, (w, h, room)
        retries = page.locator('#nd-stack .nd-ask-fail .nd-retry')
        await expect(retries).to_have_count(2)
        for retry in await retries.all():
            await retry.evaluate("b => b.scrollIntoView({block: 'nearest'})")
            await s.page.wait_for_timeout(100)
            assert await retry.evaluate(HIT), (w, h)
        await page.locator('#nd-stack .nd-ask[data-ask] .nd-opt').last.focus()
        path = []
        for _ in range(8):
            await s.page.keyboard.press('Tab')
            await s.page.wait_for_timeout(150)
            here = await s.page.evaluate(FOCUSED)
            if here['in_composer']:
                break
            path.append(here)
        assert path and all(p['on_screen'] for p in path), (w, h, path)
        assert sum('nd-retry' in p['cls'] for p in path) == 2, path
        # Enter on the first Retry the keyboard reached: the owner sees what it acts on
        await page.locator('#nd-stack .nd-ask[data-ask] .nd-opt').last.focus()
        for _ in range(4):
            await s.page.keyboard.press('Tab')
            if 'nd-retry' in (await s.page.evaluate(FOCUSED))['cls']:
                break
        await s.page.wait_for_timeout(150)
        assert (await s.page.evaluate(FOCUSED))['on_screen']
        await s.page.keyboard.press('Enter')
        await expect(page.locator('#nd-stack .nd-ask-fail').first).to_contain_text('Retry asked')
        assert len(s.steers) == 1 and s.errors == []


@pytest.mark.parametrize('start,to', [((1920, 953), (1280, 600)), ((1920, 953), (901, 700)), ((1280, 780), (1280, 600))])
async def test_a_resize_in_a_quiet_moment_brings_the_choices_back_into_view(studio, start, to):
    """Gate P7 r3 #2: shrinking the window left choices under the composer until the session's next event (2 of 5
    in view at 1280 x 600 for eight quiet seconds): the view fits itself and reveals the first request on a resize --
    also when only the window's height changed, which moves the composer and not the first item -- and a page just
    opened does not count as the owner having scrolled it."""
    async with studio(width=start[0], height=start[1]) as s:
        page = await open_nested(s)
        working_turn(s, 4)
        shell_request(s)
        first = page.locator('#nd-stack .nd-ask[data-ask]').first
        await expect(first.locator('.nd-opt')).to_have_count(5)
        await s.page.wait_for_timeout(500)
        before = [await o.evaluate(HIT) for o in await first.locator('.nd-opt').all()]
        assert all(before), (start, before)
        await s.page.set_viewport_size({'width': to[0], 'height': to[1]})
        await s.page.wait_for_timeout(800)                                              # nothing happens in the session
        seen = [await o.evaluate(HIT) for o in await first.locator('.nd-opt').all()]
        assert all(seen), (start, to, seen)
        assert s.errors == []


async def test_the_used_toggle_stays_in_view_without_scroll_anchoring(studio):
    """Gate P7 r3 #1b: opening the whole reason moves its toggle down -- under the composer when the owner had it right
    above it and clicked it (a click scrolls nothing); the view scrolls it back above the composer itself rather than
    counting on the browser's scroll anchoring (switched off here), which not every engine has."""
    async with studio(width=1280, height=600) as s:
        page = await open_nested(s)
        await s.page.add_style_tag(content='#dream-nested-page .nd-orch{overflow-anchor:none}')
        working_turn(s, 4)
        shell_request(s)
        first = page.locator('#nd-stack .nd-ask[data-ask="p-1"]')
        await expect(first.locator('.nd-opt')).to_have_count(5)
        await s.page.wait_for_timeout(300)
        more = first.get_by_role('button', name='Show the whole reason', exact=True)
        await more.evaluate("m => m.scrollIntoView({block: 'end'})")                     # right above the composer
        await s.page.wait_for_timeout(150)
        await more.click()
        await expect(first.get_by_role('button', name='Show less', exact=True)).to_be_focused()
        await s.page.wait_for_timeout(250)
        assert (await s.page.evaluate(FOCUSED))['on_screen']
        await s.page.keyboard.press('Space')
        await expect(first.get_by_role('button', name='Show the whole reason', exact=True)).to_be_focused()
        await s.page.wait_for_timeout(250)
        assert (await s.page.evaluate(FOCUSED))['on_screen']
        assert s.errors == []


async def test_the_toggle_follows_the_columns_width(studio):
    """Gate P7 r3 mutant R09: the column's width moves with its edge (dragged, or the arrow keys on it) without any
    window resize; the first request's size follows, and its toggle shows exactly while its reason is cut."""
    async with studio(width=1280, height=600) as s:
        page = await open_nested(s)
        working_turn(s, 4)
        permission(s, 'p-1', 'r-1', command='pytest -q -k resize', choices=SHELL3,
                   reason='Run: pytest tests/test_nested_needs_fixes.py -q -k resize\nApproval reason: runs one test.')
        first = page.locator('#nd-stack .nd-ask[data-ask="p-1"]')
        await expect(first.locator('.nd-opt')).to_have_count(3)
        edge = page.locator('#nd-h-orch')
        state = """() => { const r = document.querySelector('#nd-stack .nd-ask[data-ask="p-1"] .nd-ask-reason'),
            m = document.querySelector('#nd-stack .nd-ask[data-ask="p-1"] .nd-more');
            return {cut: r.scrollHeight > r.clientHeight + 1, shown: !!m && !m.hidden}; }"""
        ends = []
        for key in ('Shift+ArrowLeft', 'Shift+ArrowRight'):                            # the narrowest, then the widest
            await edge.focus()
            for _ in range(5):
                await s.page.keyboard.press(key)
            await s.page.wait_for_timeout(300)
            ends.append(await s.page.evaluate(state))
        assert ends[0]['cut'] != ends[1]['cut'], ends                                   # the width decides the cut
        assert all(e['cut'] == e['shown'] for e in ends), ends
        assert s.errors == []


async def test_a_run_of_resizes_keeps_the_choices_in_view(studio):
    """Gate P7 r3 probe F: in a run of resizes, one where the column's content fits moves its scroll position to 0 by
    layout alone; that is not the owner scrolling, so the next resize still reveals the first request's choices."""
    async with studio(width=1920, height=953) as s:
        page = await open_nested(s)
        working_turn(s, 6, {'r-5': 'failed', 'r-6': 'interrupted'})
        shell_request(s)
        first = page.locator('#nd-stack .nd-ask[data-ask]').first
        await expect(first.locator('.nd-opt')).to_have_count(5)
        await s.page.wait_for_timeout(600)
        for w, h in ((1920, 953), (1280, 600), (2554, 1338), (901, 700)):
            await s.page.set_viewport_size({'width': w, 'height': h})
            await s.page.wait_for_timeout(800)                                          # nothing happens in the session
            seen = [await o.evaluate(HIT) for o in await first.locator('.nd-opt').all()]
            assert all(seen), (w, h, seen)
        assert s.errors == []


# --- gate P7 UI round 4 (gate-reports/p7ui-r4.md) -------------------------------------------------------------------

@pytest.mark.parametrize('w,h', SHORT)
async def test_typing_in_the_composer_leaves_the_choices_in_view(studio, w, h):
    """Gate P7 r4 #1: the composer sat inside the column's scroll box, so typing in it scrolled the column to bring the
    caret into view and the waiting request's choices left the view (5 of 5 to 0 after one word at 1280 x 600). The
    composer is outside the scroll box: a mouse click in it and word after word of typing, a new line included, leave
    every choice of the first request in view and reached by the pointer."""
    async with studio(width=w, height=h) as s:
        page = await open_nested(s)
        working_turn(s, 4)
        shell_request(s)
        first = page.locator('#nd-stack .nd-ask[data-ask]').first
        await expect(first.locator('.nd-opt')).to_have_count(5)
        await s.page.wait_for_timeout(500)
        choices = """() => [...document.querySelectorAll('#nd-stack-first .nd-ask[data-ask] .nd-opt')].map(e => { const b = e.getBoundingClientRect();
            if (b.height === 0 || b.top < -0.5 || b.bottom > innerHeight + 0.5) return false; const y = (b.top + b.bottom) / 2;
            return [b.left + 10, (b.left + b.right) / 2, b.right - 10].every(x => { const t = document.elementFromPoint(x, y); return !!t && (t === e || e.contains(t)); }); })"""
        assert all(await s.page.evaluate(choices)), (w, h, 'at rest')
        box = await page.locator('#nd-compose').bounding_box()
        await s.page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)   # as a mouse does it
        await expect(page.locator('#nd-compose')).to_be_focused()
        for word in ('Please ', 'also ', 'check ', 'the ', 'second ', 'part ', 'of ', 'the ', 'tests.'):
            await s.page.keyboard.type(word)
            await s.page.wait_for_timeout(120)
            seen = await s.page.evaluate(choices)
            assert all(seen), (w, h, word, seen)
        await s.page.keyboard.press('Shift+Enter')
        await s.page.keyboard.type('And the docs.')
        await s.page.wait_for_timeout(150)
        seen = await s.page.evaluate(choices)
        assert all(seen), (w, h, 'second line', seen)
        await expect(page.locator('#nd-compose')).to_have_value('Please also check the second part of the tests.\nAnd the docs.')
        assert s.errors == []


async def test_the_plan_stays_in_the_columns_scroll_box(studio):
    """Gate P7 r4, the structure: nested-plan.js mounts the tracker next to the composer, a child of the column, and
    never again while it is connected; the column moves it into its scroll box, where it stays -- the scroll box's
    last child, ending at the composer -- across renders, a plan update, a switch of view, a real reconnect (the
    history replayed) and a reload (the tracker then mounted before the view's first render)."""
    async with studio() as s:
        page = await open_nested(s)
        inside = """() => { const p = document.getElementById('nd-plan'), b = document.getElementById('nd-orch-body');
            return !!p && p.parentElement === b && b.lastElementChild === p && b.nextElementSibling.classList.contains('nd-composer'); }"""
        s.srv.bus.publish(plan(PHASES, title='Ship the Needs-you stack'))
        await expect(page.locator('#nd-plan')).to_be_visible()
        assert await s.page.evaluate(inside)
        lanes(s, 3, {})
        for i in range(5):
            s.srv.bus.publish(Event('text_delta', f'piece {i} '))
        await expect(page.locator('#nd-convo')).to_contain_text('piece 4')
        assert await s.page.evaluate(inside)
        s.srv.bus.publish(plan(PHASES[:2], title='Ship the Needs-you stack'))                # a plan update
        await s.page.wait_for_timeout(200)
        assert await s.page.evaluate(inside)
        await s.page.locator('#dream-nav-chat').click()
        page = await open_nested(s)
        assert await s.page.evaluate(inside)
        await s.page.evaluate("() => { window.__hist = 0; window.addEventListener('dream:event', e => { if (e.detail.m.kind === 'history') window.__hist++; }); socket.close(); }")
        await s.page.wait_for_function('window.__hist >= 1', timeout=8000)
        await expect(page.locator('#nd-plan')).to_be_visible()
        assert await s.page.evaluate(inside)
        await s.page.reload()
        await s.page.wait_for_function("['Ready', 'Working'].includes(document.getElementById('stat').textContent)")   # the replayed turn has not ended
        page = await open_nested(s)
        await expect(page.locator('#nd-plan')).to_be_visible()
        assert await s.page.evaluate(inside)
        assert s.errors == []
