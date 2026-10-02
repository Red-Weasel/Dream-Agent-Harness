"""Nested Dream phase 11 (DREAM-198): the plan tracker from real `plan` events. The orchestrator's update_plan tool
(tools/project.py) emits Event('plan', {title, phases[{name, status, summary, steps[{name, status}]}], path,
updated_at}); nested-plan.js keeps the latest one and whether "All phases" is open, mounted in the orchestrator column
through the page's own structure, never through nested.js. Real StudioServer and EventBus; no model, no engine, no
Dream process."""
from __future__ import annotations

import re
from pathlib import Path

from playwright.async_api import expect

from dream import diagnostics
from dream.core.backends.base import Event

from test_nested_view import open_nested, shot, studio  # noqa: F401

STATIC = Path(__file__).resolve().parents[1] / 'dream/gui/static'
TITLE = 'Write the release notes for 0.3.0'
# (name, status, summary, steps): the shape update_plan validates (statuses pending, in_progress, done).
PHASES = [('Map the subsystems that changed', 'done', 'Four subsystems changed; one record each.', []),
          ('Draft the notes per subsystem', 'in_progress', '',
           [('Engine notes', 'done'), ('Desktop notes', 'in_progress'), ('Tests', 'pending')]),
          ('Verify the numbers against the records', 'pending', '', [('Desktop check', 'pending')]),
          ('Write the record and bundle', 'pending', '', [])]


def plan(phases, title=TITLE, updated_at=1_759_200_000.0):
    return Event('plan', {'title': title, 'path': '/workspace/PLAN.md', 'updated_at': updated_at, 'phases': [
        {'name': name, 'status': status, 'summary': summary,
         'steps': [{'name': s, 'status': st} for s, st in steps]} for name, status, summary, steps in phases]})


async def decoration(locator):
    return await locator.evaluate('e => getComputedStyle(e).textDecorationLine')


# --- (a) nothing without a plan; then the current phase with its steps, and Next ------------------------------------

async def test_nothing_shows_without_a_plan_and_a_plan_shows_the_current_phase_and_next(studio):
    async with studio() as s:
        page = await open_nested(s)
        tracker = page.locator('#nd-plan')
        await expect(tracker).to_be_hidden()
        await expect(page.locator('.nd-pnow')).to_have_count(0)
        s.srv.bus.publish(plan(PHASES))
        await expect(tracker).to_be_visible()
        assert await tracker.evaluate('e => e.closest(".nd-orch") !== null')      # the orchestrator column
        # the last thing in the column's scroll box, which ends at the composer (the composer is outside it: gate P7 r4)
        assert await tracker.evaluate('e => e.parentElement.lastElementChild === e && e.parentElement.nextElementSibling?.classList.contains("nd-composer")')
        now = page.locator('.nd-pnow')
        await expect(now.locator('.nd-pk')).to_have_text('Phase 2 of 4')
        await expect(now.locator('.nd-pt')).to_have_text('Draft the notes per subsystem')
        steps = now.locator('.nd-pstep')
        await expect(steps).to_have_text(['Engine notes', 'Desktop notes', 'Tests'])
        await expect(steps.nth(0)).to_have_class(re.compile(r'\bnd-done\b'))
        await expect(steps.nth(1)).to_have_class(re.compile(r'\bnd-now\b'))
        await expect(steps.nth(2)).to_have_class(re.compile(r'\bnd-pending\b'))
        assert await decoration(steps.nth(0)) == 'line-through'
        assert await decoration(steps.nth(1)) == 'none'
        nxt = page.locator('.nd-pnext')
        await expect(nxt.locator('.nd-pk')).to_have_text('Next')
        await expect(nxt.locator('.nd-pt')).to_have_text('Verify the numbers against the records')
        toggle = page.locator('#nd-plan-toggle')
        await expect(toggle).to_have_text('All phases')
        await expect(toggle).to_have_attribute('aria-expanded', 'false')
        await expect(toggle).to_have_attribute('aria-controls', 'nd-plan-all')
        await expect(page.locator('#nd-plan-all')).to_be_hidden()
        # no worker sits under a phase: a plan event names none
        await expect(tracker.locator('[data-run-id], .nd-pip')).to_have_count(0)
        await shot(s.page, 'nested-plan-compact-1600.png')
        assert s.errors == []


# --- (b) the disclosure: keyboard operable, done phases struck through, at most five rows visible --------------------

async def test_all_phases_disclosure_is_keyboard_operable_and_strikes_done_phases(studio):
    async with studio() as s:
        page = await open_nested(s)
        s.srv.bus.publish(plan(PHASES))
        toggle, listing = page.locator('#nd-plan-toggle'), page.locator('#nd-plan-all')
        await toggle.focus()
        await s.page.keyboard.press('Enter')
        await expect(toggle).to_have_attribute('aria-expanded', 'true')
        await expect(toggle).to_have_text('Current only')
        await expect(toggle).to_be_focused()
        await expect(listing).to_be_visible()
        rows = listing.locator('.nd-ph')
        await expect(rows).to_have_count(4)
        await expect(rows.locator('.nd-pt')).to_have_text([n for n, *_ in PHASES])
        await expect(rows.nth(0)).to_have_class(re.compile(r'\bnd-done\b'))
        assert await decoration(rows.nth(0).locator('.nd-pt')) == 'line-through'
        await expect(rows.nth(0)).to_have_attribute('title', PHASES[0][2])          # the done phase's summary
        await expect(rows.nth(1)).to_have_class(re.compile(r'\bnd-now\b'))
        await expect(rows.nth(1)).to_have_attribute('aria-current', 'step')
        assert await decoration(rows.nth(1).locator('.nd-pt')) == 'none'
        await expect(rows.nth(1).locator('.nd-pstep')).to_have_count(3)             # the current phase's steps
        await expect(rows.nth(2).locator('.nd-pstep')).to_have_count(0)
        assert await decoration(rows.nth(3).locator('.nd-pt')) == 'none'
        await expect(page.locator('.nd-pnext')).to_have_count(0)                    # the list says what is next
        await shot(s.page, 'nested-plan-all-1600.png')
        await s.page.keyboard.press('Space')
        await expect(toggle).to_have_attribute('aria-expanded', 'false')
        await expect(toggle).to_have_text('All phases')
        await expect(listing).to_be_hidden()
        await expect(toggle).to_be_focused()
        await toggle.click()
        await expect(listing).to_be_visible()
        # eight phases, the sixth current: at most five rows fit, the list scrolls, the current one is in view
        many = [(f'Phase {i} of the long plan', 'done' if i < 6 else 'in_progress' if i == 6 else 'pending', 'Done.' if i < 6 else '', [])
                for i in range(1, 9)]
        s.srv.bus.publish(plan(many))
        await expect(rows).to_have_count(8)
        assert await listing.evaluate('e => e.scrollHeight > e.clientHeight')
        visible = await rows.evaluate_all('''els => { const box = els[0].parentElement.getBoundingClientRect();
            return els.filter(e => { const r = e.getBoundingClientRect(); return r.top >= box.top - 1 && r.bottom <= box.bottom + 1; }).length; }''')
        assert 1 <= visible <= 5, visible
        assert await rows.nth(5).evaluate('''e => { const box = e.parentElement.getBoundingClientRect(), r = e.getBoundingClientRect();
            return r.top >= box.top - 1 && r.bottom <= box.bottom + 1; }''')
        assert s.errors == []


# --- (c) updates re-render, a reload rebuilds from the retained plan, an empty plan hides the tracker --------------

async def test_plan_updates_re_render_a_reload_rebuilds_and_an_empty_plan_hides_it(studio):
    async with studio() as s:
        page = await open_nested(s)
        s.srv.bus.publish(plan(PHASES))
        await expect(page.locator('.nd-pnow .nd-pk')).to_have_text('Phase 2 of 4')
        later = [PHASES[0], (PHASES[1][0], 'done', 'Four drafts are in.', PHASES[1][3]),
                 (PHASES[2][0], 'in_progress', '', PHASES[2][3]), PHASES[3]]
        s.srv.bus.publish(plan(later))
        await expect(page.locator('.nd-pnow .nd-pk')).to_have_text('Phase 3 of 4')
        await expect(page.locator('.nd-pnow .nd-pt')).to_have_text('Verify the numbers against the records')
        await expect(page.locator('.nd-pnow .nd-pstep')).to_have_text(['Desktop check'])
        await expect(page.locator('.nd-pnext .nd-pt')).to_have_text('Write the record and bundle')
        await page.locator('#nd-plan-toggle').click()
        rows = page.locator('#nd-plan-all .nd-ph')
        assert await decoration(rows.nth(1).locator('.nd-pt')) == 'line-through'
        await expect(rows.nth(2)).to_have_attribute('aria-current', 'step')
        # the retained plan event rebuilds the tracker after a reload
        await s.page.reload()
        await expect(s.page.locator('#stat')).to_have_text('Ready')
        page = await open_nested(s)
        await expect(page.locator('#nd-plan')).to_be_visible()
        await expect(page.locator('.nd-pnow .nd-pk')).to_have_text('Phase 3 of 4')
        await expect(page.locator('#nd-plan-toggle')).to_have_attribute('aria-expanded', 'false')
        # the last phase: no Next; every phase done: said so
        s.srv.bus.publish(plan([(n, 'done', 'Done.', st) for n, _, _, st in PHASES[:3]] + [(PHASES[3][0], 'in_progress', '', [])]))
        await expect(page.locator('.nd-pnow .nd-pk')).to_have_text('Phase 4 of 4')
        await expect(page.locator('.nd-pnext')).to_have_count(0)
        s.srv.bus.publish(plan([(n, 'done', 'Done.', st) for n, _, _, st in PHASES]))
        await expect(page.locator('.nd-pnow .nd-pk')).to_have_text('All 4 phases done')
        await expect(page.locator('.nd-pnow .nd-pt')).to_have_count(0)
        # a plan without phases is no plan
        s.srv.bus.publish(Event('plan', {'title': TITLE, 'path': '/workspace/PLAN.md', 'updated_at': 1.0, 'phases': []}))
        await expect(page.locator('#nd-plan')).to_be_hidden()
        assert s.prompts == [] and s.errors == []


async def reconnect(s):
    """Close the chat's socket: the page rejoins the session and gets hello, then the retained history, as a real
    reconnect does (not a reload). A counter on the dream:event hook says when the replay has arrived."""
    await s.page.evaluate('''() => { if(!window.__histories){ window.__histories = 0;
        window.addEventListener('dream:event', e => { if(e.detail.m.kind === 'history') window.__histories++; }); }
        window.__histories = 0; socket.close(); }''')
    await s.page.wait_for_function('window.__histories > 0', timeout=15000)
    await expect(s.page.locator('#stat')).to_have_text('Ready', timeout=15000)


# --- (d) a reconnect: `history` clears the tracker; the retained plan rebuilds it (the DREAM-198 gate's probes) --------

async def test_history_without_a_plan_clears_the_tracker_and_a_retained_plan_rebuilds_it_on_a_reconnect(studio):
    async with studio() as s:
        page = await open_nested(s)
        s.srv.bus.publish(plan(PHASES))
        await expect(page.locator('#nd-plan')).to_be_visible()
        # the session's record no longer holds a plan (a trimmed record): rejoining must not keep showing the old one
        s.srv.bus.conversation.events.clear()
        s.srv.bus.conversation.chars = 0
        await reconnect(s)
        await expect(page.locator('#nd-plan')).to_be_hidden(timeout=10000)
        await expect(page.locator('.nd-pnow')).to_have_count(0)
        # with the plan retained again, rejoining rebuilds it
        s.srv.bus.publish(plan(PHASES))
        await expect(page.locator('#nd-plan')).to_be_visible()
        await reconnect(s)
        await expect(page.locator('#nd-plan')).to_be_visible(timeout=10000)
        await expect(page.locator('.nd-pnow .nd-pk')).to_have_text('Phase 2 of 4')
        assert s.errors == []


async def test_focus_on_the_toggle_survives_a_plan_update_from_the_model(studio):
    async with studio() as s:
        page = await open_nested(s)
        s.srv.bus.publish(plan(PHASES))
        toggle = page.locator('#nd-plan-toggle')
        await toggle.focus()
        await expect(toggle).to_be_focused()
        later = [PHASES[0], (PHASES[1][0], 'done', 'Four drafts are in.', PHASES[1][3]),
                 (PHASES[2][0], 'in_progress', '', PHASES[2][3]), PHASES[3]]
        s.srv.bus.publish(plan(later))
        await expect(page.locator('.nd-pnow .nd-pk')).to_have_text('Phase 3 of 4')
        assert await s.page.evaluate('document.activeElement && document.activeElement.id') == 'nd-plan-toggle'
        # the same with the list open: it stays open, the current phase moves, the focus stays on the button
        await s.page.keyboard.press('Enter')
        await expect(page.locator('#nd-plan-all')).to_be_visible()
        s.srv.bus.publish(plan([PHASES[0], later[1], (PHASES[2][0], 'done', 'Checked.', PHASES[2][3]), (PHASES[3][0], 'in_progress', '', [])]))
        await expect(page.locator('#nd-plan-all .nd-ph').nth(3)).to_have_attribute('aria-current', 'step')
        await expect(page.locator('#nd-plan-all')).to_be_visible()
        assert await s.page.evaluate('document.activeElement && document.activeElement.id') == 'nd-plan-toggle'
        # focus elsewhere is left alone
        await page.locator('#nd-compose').focus()
        s.srv.bus.publish(plan(PHASES))
        await expect(page.locator('.nd-pnow .nd-pk')).to_have_text('Phase 2 of 4')
        assert await s.page.evaluate('document.activeElement && document.activeElement.id') == 'nd-compose'
        assert s.errors == []


async def test_toggle_is_reachable_by_tab_from_the_conversation(studio):
    async with studio() as s:
        page = await open_nested(s)
        s.srv.bus.publish(plan(PHASES))
        toggle = page.locator('#nd-plan-toggle')
        assert await toggle.evaluate('e => e.tabIndex') == 0
        await page.locator('#nd-side').focus()
        seen = []
        for _ in range(12):
            await s.page.keyboard.press('Tab')
            seen.append(await s.page.evaluate('document.activeElement.id || document.activeElement.className'))
            if seen[-1] == 'nd-plan-toggle':
                break
        assert 'nd-plan-toggle' in seen, seen
        await s.page.keyboard.press('Tab')                        # and on to the composer, never trapped
        assert await s.page.evaluate('document.activeElement.id') in ('nd-compose', 'nd-plan-all')
        assert s.errors == []


# --- (e) nothing from the mockup ships; the module is wired like the others ---------------------------------------

def test_no_mockup_sample_content_ships_in_the_plan_module():
    banned = ['Backend', 'Studio UI', 'Live memory switch', 'MiMo-V2.6 Flash', 'Qwen3.8', 'Frontier review loop',
              '.dream/wt/', '372k of 600k', 'fonts.googleapis', 'fonts.gstatic', 'cdn.', '-webkit-font-smoothing',
              'Map how memory gets saved', 'Decide what']
    files = [STATIC / 'nested-plan.js', STATIC / 'nested-plan.css']
    for path in files:
        assert path.is_file(), path
        text = path.read_text()
        for needle in banned:
            assert needle not in text, (path.name, needle)
    css = re.sub(r'/\*.*?\*/', '', (STATIC / 'nested-plan.css').read_text(), flags=re.S)
    assert re.search(r'^\s*:root\s*\{', css, re.M) is None                        # never Dream's :root
    for rule in re.findall(r'(?m)^\s*([^@{}][^{}]*)\{', css):                       # every selector under the page
        for selector in re.split(r',(?![^(]*\))', rule):
            assert selector.strip().startswith('#dream-nested-page'), selector
    html = (STATIC / 'index.html').read_bytes().decode('utf-8', 'replace')
    drawer = html.index('<script src="/assets/nested-drawer.js"></script>')
    assert html.index('<link rel="stylesheet" href="/assets/nested-plan.css">') > drawer
    assert html.index('<script src="/assets/nested-plan.js"></script>') > drawer
    assert {'nested-plan.js', 'nested-plan.css'} <= set(diagnostics._STATIC)
