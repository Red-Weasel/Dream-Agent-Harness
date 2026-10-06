"""Nested Dream redone to the owner-approved v2 mockup (2026-10-01; the owner: [feedback]). What the redo added beyond the look, each from real rows only: a message says when this page saw
it (nothing for a replayed one); a finished worker's hand-off is a line in the conversation; back-to-back task calls
share one row with how they stand; a worker's run clock counts from its first request seen live; a pinned worker's
request is answerable in its window too; a short goal is the column's heading; the orchestrator's header row names its
model; a worker's speed shows only while it works; a tool row says what the call touched; with the Understand panel open
every pinned window keeps its Send in view; the top bar is one line at the owner's sizes; the palette is the mockup's.
Scripted events on a real StudioServer; no model, no engine."""
from __future__ import annotations

import re

import pytest
from playwright.async_api import expect

from dream.core.backends.base import Event
from test_nested_drawer import lanes
from test_nested_needs import fake_permission, ok, permission
from test_nested_view import _activity, open_nested, studio  # noqa: F401

pytestmark = pytest.mark.asyncio
HHMM = re.compile(r'^\d\d:\d\d$')
LATER = "() => { const real = Date.now.bind(Date); Date.now = () => real() + 3 * 60000 + 5000; }"   # three minutes on
COMPLETED = 'Subagent finished; its result was returned to the lead agent.'


def task(i, kind='explorer'):
    return Event('tool_use', {'id': f'call-{i}', 'name': 'task', 'input': {'subagent_type': kind, 'prompt': f'Map part {i}.'}})


async def test_a_message_says_when_this_page_saw_it_and_a_replayed_one_says_nothing(studio):  # noqa: F811
    async with studio() as s:
        page = await open_nested(s)
        await s.srv._accept_prompt('Map the harness.')
        you = page.locator('.nd-msg.nd-you .nd-when')
        await expect(you).to_have_text(HHMM)
        now = await s.page.evaluate("() => { const d = new Date(); return [d.getHours(), d.getMinutes()]; }")
        shown = [int(x) for x in (await you.text_content()).split(':')]
        assert abs((shown[0] * 60 + shown[1]) - (now[0] * 60 + now[1])) <= 1, (shown, now)   # this page's clock, now
        s.srv.bus.publish(Event('turn_start', {}))
        s.srv.bus.publish(Event('text_delta', 'Fanning out.'))
        lead = page.locator('.nd-msg.nd-dream .nd-when')
        await expect(lead).to_have_text('now')                                          # still streaming
        s.srv.bus.publish(Event('assistant_done', {}))
        await expect(lead).to_have_text(HHMM)
        s.srv.bus.publish(Event('turn_end', {}))
        await s.page.reload()                                                           # the history carries no time
        await expect(s.page.locator('#stat')).to_have_text('Ready')
        page = await open_nested(s)
        await expect(page.locator('.nd-msg.nd-you')).to_contain_text('Map the harness.')
        await expect(page.locator('.nd-msg.nd-dream .nd-stream')).to_have_text('Fanning out.')
        await expect(page.locator('#nd-convo .nd-when')).to_have_count(0)
        assert s.errors == []


async def test_a_finished_worker_hands_its_result_to_the_orchestrator_in_the_conversation(studio):  # noqa: F811
    async with studio() as s:
        page = await open_nested(s)
        s.srv.bus.publish(Event('turn_start', {}))
        lanes(s, 3, {'r-1': 'completed', 'r-2': 'completed'})                         # back to back: one line
        lines = page.locator('#nd-convo .nd-handoff')
        await expect(lines).to_have_count(1)
        await expect(lines.first).to_have_text(re.compile(r'^writer 1 and 2 handed their results to Orchestrator\d\d:\d\d$'))
        s.srv.bus.publish(Event('text_delta', 'Two of three are back.'))
        s.srv.bus.publish(_activity('r-3', 'writer', 'status', status='completed', text=COMPLETED))
        await expect(lines).to_have_count(2)
        await expect(lines.nth(1)).to_contain_text('writer 3 handed its result to Orchestrator')
        order = await page.locator('#nd-convo > *').evaluate_all("els => els.map(e => e.className.replace('nd-msg', '').trim())")
        assert order == ['nd-handoff', 'nd-dream', 'nd-handoff'], order                # where each happened
        s.srv.bus.publish(Event('user', 'Retry the rest.'))
        lanes_failed = 'r-4'
        s.srv.bus.publish(_activity(lanes_failed, 'writer', 'status', status='running', text='Subagent started.'))
        s.srv.bus.publish(_activity(lanes_failed, 'writer', 'status', status='failed', text='Subagent timed out; work is incomplete.'))
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(4)
        await expect(lines).to_have_count(2)                                            # a failure hands nothing over
        s.srv.bus.publish(Event('turn_end', {}))
        await s.page.reload()                                                           # the replay puts them back in place
        await expect(s.page.locator('#stat')).to_have_text('Ready')
        page = await open_nested(s)
        lines = page.locator('#nd-convo .nd-handoff')
        await expect(lines).to_have_count(2)
        await expect(lines.first).to_have_text('writer 1 and 2 handed their results to Orchestrator')   # no time after a replay
        assert s.errors == []


async def test_back_to_back_task_calls_share_one_row_with_how_they_stand(studio):  # noqa: F811
    async with studio() as s:
        page = await open_nested(s)
        s.srv.bus.publish(Event('turn_start', {}))
        for i in (1, 2, 3):
            s.srv.bus.publish(task(i))
        row = page.locator('#nd-convo .nd-tool-group')
        await expect(row).to_have_count(1)
        await expect(row.locator('.nd-who-to')).to_have_text('Orchestrator handed 3 tasks to explorer')
        await expect(row.locator('.nd-badge')).to_have_text('3 running')
        s.srv.bus.publish(Event('tool_result', {'id': 'call-1', 'content': 'done', 'is_error': False}))
        s.srv.bus.publish(Event('tool_result', {'id': 'call-2', 'content': 'boom', 'is_error': True}))
        await expect(row.locator('.nd-badge')).to_have_text('1 completed · 1 failed · 1 running')
        s.srv.bus.publish(task(4, kind='writer'))                                       # still back to back: the row grows
        await expect(row.locator('.nd-who-to')).to_have_text('Orchestrator handed 4 tasks to workers')
        s.srv.bus.publish(Event('tool_use', {'id': 'o-1', 'name': 'read_file', 'input': {'path': 'docs/PLAN.md'}}))
        s.srv.bus.publish(Event('tool_use', {'id': 'o-2', 'name': 'read_file', 'input': {'path': 'docs/NOTES.md'}}))
        single = page.locator('#nd-convo .nd-tool-msg:not(.nd-tool-group)')
        await expect(single).to_have_count(2)                                           # any other call: a row each
        await expect(single.nth(0).locator('.nd-tool-args')).to_have_text('docs/PLAN.md')
        await expect(single.nth(1).locator('.nd-badge')).to_have_text('Running')
        assert s.errors == []


async def test_a_workers_run_clock_counts_from_its_first_request_seen_live_and_stops_at_its_end(studio):  # noqa: F811
    async with studio() as s:
        page = await open_nested(s)
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='queued', text='Waiting for an engine lane (1 of 1 busy)'))
        win = page.locator('.nd-win[data-run-id="r-1"]')
        await expect(win).to_be_visible()
        await expect(win.locator('.nd-clock')).to_have_count(0)                         # waiting for a lane: not running yet
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='running', text='Subagent started.'))
        await expect(win.locator('.nd-clock')).to_have_text(re.compile(r'^0:0\d$'))
        await s.page.evaluate(LATER)
        await expect(win.locator('.nd-clock')).to_have_text(re.compile(r'^3:0\d$'))     # it moves on its own, each second
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='completed', text=COMPLETED))
        await expect(win.locator('.nd-state')).to_have_text('Done')
        frozen = await win.locator('.nd-clock').text_content()
        await s.page.wait_for_timeout(1500)
        await expect(win.locator('.nd-clock')).to_have_text(frozen)                    # stopped at its end
        await page.locator('#nd-drawer-btn').click()
        await expect(page.locator('#nd-dlist .nd-item[data-run-id="r-1"] .nd-clock')).to_have_text(frozen)
        await s.page.keyboard.press('Escape')
        await s.page.reload()                                                           # rebuilt from the history: no time to count from
        await expect(s.page.locator('#stat')).to_have_text('Ready')
        page = await open_nested(s)
        await expect(page.locator('.nd-win[data-run-id="r-1"] .nd-state')).to_have_text('Done')
        await expect(page.locator('.nd-win[data-run-id="r-1"] .nd-clock')).to_have_count(0)
        assert s.errors == []


async def test_a_pinned_workers_request_is_answerable_in_its_window_too(studio):  # noqa: F811
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {})
        bodies = []
        await fake_permission(s, bodies, ok)
        permission(s, 'p-1', 'r-2')
        band = page.locator('.nd-win[data-run-id="r-2"] .nd-wq')
        await expect(band.locator('.nd-ask[data-ask="p-1"] .nd-opt')).to_have_count(3)
        await expect(band.locator('.nd-ask-reason')).to_contain_text('rm -rf build')
        await expect(band.locator('.nd-who')).to_have_count(0)                         # the window's header says who and how long
        await expect(page.locator('#nd-stack .nd-ask[data-ask="p-1"] .nd-where')).to_have_text('in pinned window 2')
        await expect(page.locator('.nd-win[data-run-id="r-1"] .nd-wq .nd-ask')).to_have_count(0)
        await band.locator('.nd-opt').nth(1).focus()
        await s.page.keyboard.press('1')                                               # the keys answer it here as in the stack
        await expect(page.locator('#nd-stack .nd-ask[data-ask="p-1"]')).to_contain_text('Answered: Run in sandbox once')
        assert bodies == [{'id': 'p-1', 'choice': 'y'}], bodies
        await expect(page.locator('.nd-win[data-run-id="r-2"] .nd-say textarea')).to_be_focused()   # nothing else waits: its message box
        s.srv.bus.publish(Event('permission_done', {'id': 'p-1'}))
        await expect(band.locator('.nd-ask')).to_have_count(0)
        await expect(page.locator('.nd-win[data-run-id="r-2"] .nd-state')).to_have_text('Working')
        assert s.errors == []


async def test_a_short_goal_is_the_columns_heading_and_a_longer_one_reads_as_text(studio):  # noqa: F811
    async with studio() as s:
        page = await open_nested(s)
        await s.srv._accept_prompt('Map the harness and write HARNESS_MAP.md.')
        goal = page.locator('.nd-goal')
        await expect(goal).to_have_text('Map the harness and write HARNESS_MAP.md.')
        await expect(goal).to_have_css('font-weight', '600')                            # the mockup's bold heading
        await expect(goal).to_have_css('font-size', '20px')
        middle = 'Map the harness: ' + 'read every module, cite its functions and say what could break; ' * 2
        assert 120 < len(middle) <= 280
        await s.srv._accept_prompt(middle)
        await expect(goal).to_have_text(middle.strip())
        await expect(goal).to_have_css('font-weight', '400')                            # normal text, whole, no button
        await expect(goal).to_have_css('font-size', '14px')
        await expect(page.locator('.nd-goal-more')).to_have_count(0)
        await s.srv._accept_prompt('Two lines:\nmap, then write.')
        await expect(goal).to_have_text(re.compile('^Two lines:'))
        await expect(goal).to_have_css('font-weight', '400')                            # a goal of several lines is text too
        await expect(goal).to_have_css('font-size', '14px')
        assert s.errors == []


async def test_the_orchestrators_header_row_has_the_tabs_the_two_buttons_and_its_model(studio):  # noqa: F811
    async with studio() as s:
        page = await open_nested(s)
        head = page.locator('.nd-orch .nd-head')
        await expect(head.locator('[role=tab]')).to_have_text(['Agents', 'Workflows'])
        await expect(head.locator('#nd-drawer-btn')).to_have_accessible_name('All agents')
        await expect(head.locator('#nd-pause-all')).to_have_accessible_name('Pause all')
        await expect(head.locator('#nd-model')).to_have_text('fixture')                  # hello.model, the session's own
        await expect(head.locator('#nd-model')).to_have_attribute('title', "The orchestrator's model")
        geo = await page.evaluate("""p => { const r = s => p.querySelector(s).getBoundingClientRect();
            return {head: r('.nd-head').top, headBottom: r('.nd-head').bottom, orch: r('.nd-orch').top, bar: r('.nd-top').top,
                    barBottom: r('.nd-top').bottom, notes: r('.nd-notes').height, main: r('.nd-main').top, win: r('.nd-pinned').top, page: p.querySelector('.nd-app').getBoundingClientRect().top,
                    header: getComputedStyle(document.querySelector('body>header')).display, workbar: getComputedStyle(document.getElementById('dream-workbar')).display}; }""")
        # DREAM-239: nothing above the work area -- the page's own bars are not shown here, the column's header row is the
        # view's first row, and the workers' bar is one line with it; the workers start under their bar.
        assert geo['header'] == 'none' and geo['workbar'] == 'none', geo
        assert geo['head'] == geo['orch'] and abs(geo['orch'] - geo['page']) <= 1, geo
        assert abs(geo['bar'] - geo['head']) <= 1 and abs(geo['barBottom'] - geo['headBottom']) <= 1 and abs(geo['main'] - geo['barBottom'] - geo['notes']) <= 1, geo   # (a note, when one shows, sits between the bar and the workers)
        # what the bars held is in the column's More menu, each row a click on the page's own control
        await head.locator('#nd-menu summary').click()
        await expect(head.locator('#nd-menu [data-proxy]:not([hidden])')).to_have_text(['New chat', 'Controls', 'Council', 'Create', 'Context', 'Design', 'Checkpoints', 'Metrics'])
        await head.locator('#nd-menu [data-proxy="dream-controls-open"]').click()
        await expect(s.page.locator('#dream-controls')).to_have_attribute('open', '')
        await s.page.keyboard.press('Escape')
        await expect(head.locator('#nd-menu summary')).to_be_focused()                  # the dialog gives the focus back to the menu
        assert s.errors == []


async def test_a_workers_speed_shows_only_while_it_works(studio):  # noqa: F811
    async with studio() as s:
        page = await open_nested(s)
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='running', text='Subagent started.'))
        s.srv.bus.publish(_activity('r-1', 'writer', 'response', status='received', request_index=1, duration_ms=2000,
                                    text='First pass.', usage={'prompt_tokens': 100, 'completion_tokens': 50}))
        win = page.locator('.nd-win[data-run-id="r-1"]')
        await expect(win.locator('.nd-rate')).to_have_text('25.0 tok/s')
        permission(s, 'p-1', 'r-1')
        await expect(win.locator('.nd-state')).to_have_text('Needs you')
        await expect(win.locator('.nd-rate')).to_have_count(0)                          # waiting on you: no speed
        s.srv.bus.publish(Event('permission_done', {'id': 'p-1'}))
        await expect(win.locator('.nd-rate')).to_have_text('25.0 tok/s')
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='completed', text=COMPLETED))
        await expect(win.locator('.nd-state')).to_have_text('Done')
        await expect(win.locator('.nd-rate')).to_have_count(0)                          # ended: no speed
        assert s.errors == []


async def test_a_tool_row_says_what_the_call_touched(studio):  # noqa: F811
    async with studio() as s:
        page = await open_nested(s)
        s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='running', text='Subagent started.'))
        calls = [('read_file', {'path': 'dream/core/engine.py'}), ('run_bash', {'command': 'pytest -q'}),
                 ('web_search', {'query': 'sycl reduce'}), ('mcp__dream__notes', {'topic': 'x', 'depth': 2})]
        for i, (name, args) in enumerate(calls):
            s.srv.bus.publish(_activity('r-1', 'writer', 'tool_use', status='requested', data={'id': f't{i}', 'name': name, 'input': args}))
        rows = page.locator('.nd-win[data-run-id="r-1"] .nd-tool')
        await expect(rows).to_have_count(4)
        await expect(rows.locator('.nd-tool-args')).to_have_text(['dream/core/engine.py', 'pytest -q', 'sycl reduce', '{"topic":"x","depth":2}'])
        await expect(rows.locator('.nd-tool-name')).to_have_text(['read_file', 'run_bash', 'web_search', 'mcp__dream__notes'])
        assert await rows.locator('.nd-ic svg').count() == 4                           # an icon each, by kind
        await page.locator('.nd-pips .nd-pip').first.click()                           # the drawer keeps the whole input
        await expect(page.locator('#nd-detail .nd-entry.nd-tool .nd-tool-args').first).to_have_text('{"path":"dream/core/engine.py"}')
        assert s.errors == []


async def test_with_the_understand_panel_open_every_pinned_window_keeps_its_send_in_view(studio):  # noqa: F811
    hit = """e => { const b = e.getBoundingClientRect(); if (!b.width || b.bottom > innerHeight + 0.5 || b.right > innerWidth + 0.5) return false;
      const t = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2); return !!t && (t === e || e.contains(t)); }"""
    async with studio(width=1920, height=953) as s:
        await s.page.locator('#dream-nav-understand').click()
        page = await open_nested(s)
        lanes(s, 3, {})
        await expect(page.locator('.nd-win[data-run-id]')).to_have_count(2)
        # DREAM-243: the panel is the right-hand pane's Map here, over the workers; the Workers tab gives them back
        # at their full width (the panel no longer squeezes the view), and every pinned window has its Send in view
        await expect(s.page.locator('html')).to_have_attribute('data-nd-pane', 'map')
        await page.locator('#nd-pane-workers').click()
        await expect(s.page.locator('#dream-understand')).to_be_hidden()
        for run in ('r-1', 'r-2'):
            await expect(page.locator(f'.nd-win[data-run-id="{run}"] .nd-say-send')).to_be_attached()
            assert await page.locator(f'.nd-win[data-run-id="{run}"] .nd-say-send').evaluate(hit), run
        assert await page.evaluate('p => p.scrollWidth <= p.clientWidth')
        assert s.errors == []


@pytest.mark.parametrize('w,h', [(1920, 953), (2554, 1338)])
async def test_the_top_bar_is_one_line_at_the_owners_sizes(studio, w, h):  # noqa: F811
    async with studio(width=w, height=h, lanes={'served': 4, 'busy': 4, 'queued': 12}) as s:
        page = await open_nested(s)
        lanes(s, 16, {'r-5': 'completed', 'r-12': 'failed'})
        permission(s, 'p-1', 'r-3')
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(16)
        tops = await page.locator('.nd-top > :not(.nd-sr):not([hidden])').evaluate_all(
            "els => els.filter(e => e.getBoundingClientRect().width).map(e => Math.round(e.getBoundingClientRect().top + e.getBoundingClientRect().height / 2))")
        assert max(tops) - min(tops) <= 2, (w, tops)                                   # one row
        assert await page.locator('.nd-top').evaluate('e => e.getBoundingClientRect().height') <= 57   # the header row's height (DREAM-239)
        assert await page.locator('.nd-top').evaluate('e => e.scrollWidth <= e.clientWidth + 1'), 'the bar fits its column: the pips scroll, nothing spills'
        await expect(page.locator('.nd-attn')).to_have_text('1 needs you1 failed')
        assert s.errors == []


async def test_the_palette_is_the_mockups(studio):  # noqa: F811
    css = "(e, p) => getComputedStyle(e)[p]"
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 3, {})
        want = {'#dream-nested-page': ('backgroundColor', 'rgb(11, 10, 14)'), '.nd-orch': ('backgroundColor', 'rgb(21, 19, 24)'),
                '.nd-win[data-run-id="r-1"]': ('backgroundColor', 'rgb(21, 19, 24)'), '#nd-send': ('backgroundColor', 'rgb(255, 138, 76)'),
                '.nd-title': ('color', 'rgb(250, 250, 250)'), '.nd-card[data-run-id="r-3"]': ('borderTopColor', 'rgb(38, 35, 44)')}
        await expect(page.locator('.nd-card[data-run-id="r-3"]')).to_be_visible()
        await s.page.mouse.move(0, 0)
        for sel, (prop, value) in want.items():
            node = s.page.locator(sel).first if sel == '#dream-nested-page' else page.locator(sel).first
            assert await node.evaluate(css, prop) == value, (sel, prop)
        assert s.errors == []
