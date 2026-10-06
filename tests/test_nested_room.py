"""The orchestrator's conversation keeps its room (the owner's screenshot of 2026-10-02 00:55, a 15-worker swarm with
three failed workers): the three Failed cards under "Needs you" took the column and left the orchestrator's final answer
a strip of about six lines. The owner: [feedback].

- A failed or stopped worker is one row in the stack: its number, its name, its state, its reason on one line and its
  retry. A toggle shows the whole reason; the choice survives a re-render.
- The failed and stopped workers of "Needs you" fold (Hide failed or stopped / Show failed or stopped (N)); the count
  stays in sight and the choice survives a re-render. Only they fold: a request that waits for an answer always shows,
  folded or not, and answering it does not undo the owner's Hide.
- The conversation keeps at least two fifths of the column's body whatever sits above it.

Scripted events, the real page; no model, no engine."""
from __future__ import annotations

import pytest
from playwright.async_api import expect

from dream.core.backends.base import Event
from test_nested_needs import done, permission
from test_nested_view import _activity, open_nested, studio  # noqa: F401

pytestmark = pytest.mark.asyncio
CUT = ("Subagent returned a failed or incomplete result: (subagent 'explorer' output truncated at the token limit "
       "— treat as incomplete; no tools from this response ran)")
GOAL = ("You are the orchestrator. Review this project with sub-agents. Do not review the files yourself.\n\n"
        + "\n".join(f"{i}. build_rocket.py: question number {i} about the rocket, with file and line references."
                    for i in range(1, 16))
        + "\n\nStep 4. Tell me: how many workers finished, how many failed, and where the report is.")
ANSWER = "\n".join(f"| {i} | Major | Finding number {i}: the particle list grows without a bound on a 4K screen. |"
                   for i in range(1, 41))


def swarm(s, n=15, failed=(2, 8, 13)):
    """n explorer workers in this turn; the ones in `failed` end as the owner's did, the others finish."""
    for i in range(1, n + 1):
        run = f'r-{i}'
        s.srv.bus.publish(_activity(run, 'explorer', 'status', status='running', text='Subagent started.'))
        s.srv.bus.publish(_activity(run, 'explorer', 'request', status='awaiting_response', request_index=1,
                                    model='fixture-35b', context={'used': 1024 * i, 'window': 32768}))
        s.srv.bus.publish(_activity(run, 'explorer', 'response', status='received', request_index=1, text=f'Report {i}.'))
        if i in failed:
            s.srv.bus.publish(_activity(run, 'explorer', 'status', status='failed', text=CUT))
        else:
            s.srv.bus.publish(_activity(run, 'explorer', 'status', status='completed',
                                        text='Subagent finished; its result was returned to the lead agent.'))


async def owners_run(s, failed=(2, 8, 13)):
    page = await open_nested(s)
    await s.srv._accept_prompt(GOAL)
    s.srv.bus.publish(Event('turn_start', {}))
    swarm(s, failed=failed)
    s.srv.bus.publish(Event('text_delta', ANSWER))
    s.srv.bus.publish(Event('result', {}))
    s.srv.bus.publish(Event('turn_end', {}))
    await expect(page.locator('#nd-needs-c')).to_have_text(str(len(failed)))   # listed (a short window starts them folded)
    return page


async def _room(page) -> dict:
    return await page.evaluate("""() => { const h = s => document.querySelector(s).getBoundingClientRect();
        const body = h('#nd-orch-body'), convo = h('#nd-orch-scroll'), send = h('#nd-send');
        return {body: body.height, convo: convo.height, convoBottom: convo.bottom, bodyBottom: body.bottom,
                send: send.bottom, inner: innerHeight, bodyScrolls: document.querySelector('#nd-orch-body').scrollHeight
                    > document.querySelector('#nd-orch-body').clientHeight + 1}; }""")


@pytest.mark.parametrize("size", [(2000, 1125), (1920, 953), (1600, 1000), (1440, 900), (1280, 1024), (1000, 1000)])
async def test_three_failed_workers_leave_the_final_answer_its_room(studio, size):  # noqa: F811
    async with studio(width=size[0], height=size[1]) as s:
        page = await owners_run(s)
        found = await _room(page)
        assert found['convo'] >= 0.4 * found['body'] - 1, found          # the answer keeps two fifths of the body
        assert not found['bodyScrolls'], found                           # and nothing is pushed out of the column
        assert found['send'] <= found['inner'], found
        await expect(page.locator('#nd-needs-c')).to_have_text('3')     # all three are still listed
        assert s.errors == []


async def test_a_failed_worker_is_one_row_and_a_toggle_shows_its_whole_reason(studio):  # noqa: F811
    async with studio(width=1600, height=1000) as s:
        page = await owners_run(s)
        row = page.locator('#nd-stack .nd-ask-fail').first
        await expect(row.locator('.nd-state')).to_have_text('Failed')
        await expect(row.locator('.nd-ask-reason')).to_have_text(CUT)    # every word is still in the page
        look = await row.evaluate("""e => { const r = e.querySelector('.nd-ask-reason'), c = getComputedStyle(r);
            return {height: e.getBoundingClientRect().height, reason: r.getBoundingClientRect().height,
                    line: parseFloat(c.lineHeight), cut: r.scrollWidth > r.clientWidth + 1 || r.scrollHeight > r.clientHeight + 1}; }""")
        assert look['reason'] <= look['line'] + 2 and look['cut'], look   # one line, and it does cut this reason
        assert look['height'] <= 56, look                                 # the whole row is one line of controls
        await expect(row.get_by_role('button', name='Ask the orchestrator to retry explorer', exact=True)).to_have_count(1)
        more = row.locator('.nd-more')
        await expect(more).to_be_visible()
        await expect(more).to_have_attribute('aria-expanded', 'false')
        await more.click()
        row = page.locator('#nd-stack .nd-ask-fail').first
        await expect(row.locator('.nd-more')).to_have_attribute('aria-expanded', 'true')
        whole = await row.locator('.nd-ask-reason').evaluate(
            "r => r.scrollWidth <= r.clientWidth + 1 && r.scrollHeight <= r.clientHeight + 1")
        assert whole                                                       # nothing is cut now
        s.srv.bus.publish(Event('agent_activity', {'run_id': 'r-1', 'agent': 'explorer', 'phase': 'subagent',
                                                    'kind': 'thinking_report', 'text': 'A late line.'}))
        await s.page.wait_for_timeout(100)                                  # a re-render
        await expect(page.locator('#nd-stack .nd-ask-fail').first.locator('.nd-more')).to_have_attribute('aria-expanded', 'true')
        await page.locator('#nd-stack .nd-ask-fail').first.locator('.nd-more').click()
        await expect(page.locator('#nd-stack .nd-ask-fail').first.locator('.nd-more')).to_have_attribute('aria-expanded', 'false')
        assert s.errors == []


async def test_a_short_reason_shows_whole_with_no_toggle(studio):  # noqa: F811
    async with studio(width=1600, height=1000) as s:
        page = await open_nested(s)
        s.srv.bus.publish(Event('turn_start', {}))
        s.srv.bus.publish(_activity('r-1', 'explorer', 'status', status='running', text='Subagent started.'))
        s.srv.bus.publish(_activity('r-1', 'explorer', 'status', status='failed', text='Timed out.'))
        row = page.locator('#nd-stack .nd-ask-fail')
        await expect(row).to_have_count(1)
        await expect(row.locator('.nd-ask-reason')).to_have_text('Timed out.')
        await expect(row.locator('.nd-more')).to_be_hidden()
        assert s.errors == []


FOLD_OPEN, FOLD_SHUT = 'Hide failed or stopped', 'Show failed or stopped ({})'


async def _clickable(s, selector) -> bool:
    """The element is on screen and on top at its centre (a real click would reach it), once the layout has settled."""
    await s.page.wait_for_function("""q => { const e = document.querySelector(q); if (!e) return false;
        const r = e.getBoundingClientRect(), hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return r.width > 0 && r.bottom <= innerHeight && (hit === e || e.contains(hit)); }""", arg=selector, timeout=3000)
    return True


async def test_the_fold_hides_only_the_failed_workers_and_never_a_waiting_request(studio):  # noqa: F811
    async with studio(width=1600, height=1000) as s:
        page = await owners_run(s, failed=(2, 8))
        fold, rows = page.locator('#nd-needs-fold'), page.locator('#nd-stack .nd-ask-fail')
        await expect(fold).to_be_visible()
        await expect(fold).to_have_text(FOLD_OPEN)
        await expect(fold).to_have_attribute('aria-expanded', 'true')
        open_height = (await page.locator('#nd-needs-sec').bounding_box())['height']
        convo_open = (await page.locator('#nd-orch-scroll').bounding_box())['height']
        await fold.click()
        await expect(fold).to_have_attribute('aria-expanded', 'false')
        await expect(fold).to_have_text(FOLD_SHUT.format(2))             # how many are folded away
        await expect(page.locator('#nd-stack')).to_be_hidden()
        await expect(page.locator('#nd-needs-c')).to_have_text('2')      # the count stays in sight
        shut_height = (await page.locator('#nd-needs-sec').bounding_box())['height']
        assert shut_height <= 48 and shut_height < open_height, (shut_height, open_height)
        assert (await page.locator('#nd-orch-scroll').bounding_box())['height'] >= convo_open   # the room goes to the answer
        # another failed worker in the same turn stays folded with the others
        s.srv.bus.publish(_activity('r-13', 'explorer', 'status', status='failed', text=CUT))
        await expect(fold).to_have_text(FOLD_SHUT.format(3))
        await expect(rows).to_have_count(0)
        # a request that waits for an answer shows at once, the failed workers still folded
        permission(s, 'p-1', 'r-1', agent='explorer')
        ask = page.locator('#nd-stack .nd-ask[data-ask="p-1"]')
        await expect(ask).to_be_visible()
        await expect(page.locator('#nd-needs-c')).to_have_text('4')
        await expect(fold).to_have_attribute('aria-expanded', 'false')
        await expect(rows).to_have_count(0)
        assert await _clickable(s, '#nd-stack .nd-ask[data-ask="p-1"] .nd-opt')
        assert not (await _room(page))['bodyScrolls']                    # a waiting request has the column's room first
        # Show, then Hide again: the request is there through both (the gate's finding: Hide folded it away)
        await fold.click()
        await expect(rows).to_have_count(3)
        await expect(ask).to_be_visible()
        await fold.click()
        await expect(fold).to_have_attribute('aria-expanded', 'false')
        await expect(rows).to_have_count(0)
        await expect(ask).to_be_visible()
        assert await _clickable(s, '#nd-stack .nd-ask[data-ask="p-1"] .nd-opt')
        # answered: the owner's Hide still holds (a request never undoes it)
        done(s, 'p-1')
        await expect(ask).to_have_count(0)
        await expect(fold).to_have_attribute('aria-expanded', 'false')
        await expect(page.locator('#nd-stack')).to_be_hidden()
        await expect(page.locator('#nd-needs-c')).to_have_text('3')
        assert s.errors == []


async def test_the_orchestrators_own_request_shows_with_the_failed_workers_folded(studio):  # noqa: F811
    async with studio(width=1600, height=1000) as s:
        page = await owners_run(s)
        await page.locator('#nd-needs-fold').click()
        permission(s, 'p-lead')                                         # no run_id: the orchestrator asks; no pip shows it
        ask = page.locator('#nd-stack .nd-ask[data-ask="p-lead"]')
        await expect(ask).to_be_visible()
        await expect(page.locator('#nd-stack .nd-ask-fail')).to_have_count(0)
        assert await _clickable(s, '#nd-stack .nd-ask[data-ask="p-lead"] .nd-opt')
        assert s.errors == []


async def test_a_short_window_starts_with_the_failed_workers_folded_and_show_opens_them(studio):  # noqa: F811
    async with studio(width=1366, height=768) as s:
        page = await owners_run(s)
        fold = page.locator('#nd-needs-fold')
        await expect(fold).to_have_attribute('aria-expanded', 'false')
        await expect(fold).to_have_text(FOLD_SHUT.format(3))
        await expect(page.locator('#nd-needs-c')).to_have_text('3')      # the count is in the heading
        await expect(page.locator('.nd-attn')).to_contain_text('3 failed')   # and in the top bar
        found = await _room(page)
        assert found['convo'] >= 192 and not found['bodyScrolls'], found
        await fold.click()
        await expect(fold).to_have_attribute('aria-expanded', 'true')
        await expect(page.locator('#nd-stack .nd-ask-fail').first).to_be_visible()
        s.srv.bus.publish(Event('agent_activity', {'run_id': 'r-1', 'agent': 'explorer', 'phase': 'subagent',
                                                    'kind': 'thinking_report', 'text': 'A late line.'}))
        await s.page.wait_for_timeout(100)
        await expect(fold).to_have_attribute('aria-expanded', 'true')    # the owner's choice holds
        assert s.errors == []


async def test_a_short_window_shows_a_waiting_request_with_the_failed_workers_folded(studio):  # noqa: F811
    async with studio(width=1366, height=768) as s:
        page = await owners_run(s)
        permission(s, 'p-1', 'r-1', agent='explorer')
        await expect(page.locator('#nd-stack .nd-ask[data-ask="p-1"]')).to_be_visible()
        await expect(page.locator('#nd-stack .nd-ask-fail')).to_have_count(0)
        await expect(page.locator('#nd-needs-fold')).to_have_text(FOLD_SHUT.format(3))
        assert await _clickable(s, '#nd-stack .nd-ask[data-ask="p-1"] .nd-opt')
        done(s, 'p-1')                                                   # answered: still folded, as the window says
        await expect(page.locator('#nd-needs-fold')).to_have_attribute('aria-expanded', 'false')
        assert s.errors == []


async def test_a_focused_row_that_folds_away_hands_the_focus_to_the_fold(studio):  # noqa: F811
    async with studio(width=1600, height=1000) as s:
        page = await owners_run(s)
        await page.locator('#nd-stack .nd-ask-fail .nd-retry').first.focus()
        await s.page.set_viewport_size({'width': 1600, 'height': 760})  # short now: the failed workers fold
        await expect(page.locator('#nd-needs-fold')).to_have_attribute('aria-expanded', 'false')
        await expect(page.locator('#nd-needs-fold')).to_be_focused()
        assert s.errors == []


async def test_with_nothing_waiting_there_is_no_fold(studio):  # noqa: F811
    async with studio(width=1600, height=1000) as s:
        page = await open_nested(s)
        await expect(page.locator('#nd-needs-sec')).to_contain_text('Nothing is waiting on you.')
        await expect(page.locator('#nd-needs-fold')).to_be_hidden()
        permission(s, 'p-1')                                             # a request alone: nothing to fold
        await expect(page.locator('#nd-stack .nd-ask[data-ask="p-1"]')).to_be_visible()
        await expect(page.locator('#nd-needs-fold')).to_be_hidden()
        assert s.errors == []
