"""Nested Dream phase 12 (DREAM-199): the orchestrator's footer, the Agents / Workflows tabs and the Workflows stub.
nested-footer.js mounts under the composer: the permission-mode chip (the chat's own control, tui/app.py
_runtime_control 'permission_mode' through /api/control), Read ×2 (the chat's stored preference, sent as read_twice
on /api/prompt), and prefill, decode and context from real `stats` events, computed as the chat's perf() does and blank
until data arrives. The tabs follow the ARIA tabs pattern; nested-workflows.js is an honest empty state pointing at
Create → Guided tasks. Real StudioServer and EventBus with the app's mode control answered by a fixture; no model, no
engine, no Dream process."""
from __future__ import annotations

import contextlib
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright, expect

from dream import diagnostics
from dream.core import policy
from dream.core.backends.base import Event
from dream.gui.bus import EventBus
from dream.gui.server import READ_AGAIN, StudioServer

from test_nested_view import _activity, open_nested, shot  # noqa: F401
from test_nested_plan import PHASES, plan, reconnect

STATIC = Path(__file__).resolve().parents[1] / 'dream/gui/static'
STATS = {'pp_n': 2000, 'pp_ms': 4000.0, 'gen_n': 100, 'gen_ms': 8000.0, 'exact': True, 'ttft_ms': 4000.0,
         'prompt_tokens': 3000, 'cached': 1000, 'window': 32768}
PERF = 'prefill 500 tok/s · decode 12.5 tok/s · context 3,000 / 32,768 (1K cached)'


@pytest.fixture
def studio(monkeypatch):
    """One Studio server and one Chromium page per `async with studio() as s`, with the app's two permission-mode
    control actions answered the way tui/app.py _runtime_control answers them (mode, modes, labels), or none."""
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)

    @contextlib.asynccontextmanager
    async def open_studio(controls=True, width=1600, height=1000):
        prompts, actions, state = [], [], {'mode': 'ask'}

        async def control(payload):
            actions.append(payload)
            action = payload.get('action')
            if action in {'permission_mode', 'permission_mode_status'}:
                if action == 'permission_mode':
                    state['mode'] = policy.next_mode(state['mode'])
                return {'mode': state['mode'], 'modes': list(policy.MODES), 'labels': policy.MODE_LABEL}
            raise ValueError(f'Unknown control action {action!r}')

        srv = StudioServer(EventBus(), on_prompt=prompts.append, on_control=control if controls else None,
                           session={'provider': 'MachX', 'model': 'fixture', 'session_id': 'nested-footer-test'})
        url = await srv.start()
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(args=['--disable-gpu'])
                page = await browser.new_page(viewport={'width': width, 'height': height})
                page.set_default_timeout(5000)
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                await page.goto(url.replace('/#', '/?companion=1#'))
                await expect(page.locator('#stat')).to_have_text('Ready')
                yield SimpleNamespace(srv=srv, page=page, prompts=prompts, actions=actions, errors=errors, state=state)
                await browser.close()
        finally:
            await srv.stop()
    return open_studio


# --- (a) the permission-mode chip: the confirmed mode, cycled through the app's control ---------------------------

async def test_mode_chip_shows_the_confirmed_mode_and_cycles_through_the_control(studio):
    async with studio() as s:
        page = await open_nested(s)
        chip = page.locator('#nd-mode')
        await expect(chip).to_have_text(policy.MODE_LABEL['ask'])
        await expect(chip).to_be_visible()
        assert await chip.evaluate('e => e.closest(".nd-orch .nd-composer") !== null')
        await expect(chip).to_have_attribute('data-mode', 'ask')
        chip_actions = lambda: [a for a in s.actions if a['action'] != 'settings_get']   # noqa: E731 (the Agents control, DREAM-197, reads its setting too)
        assert chip_actions() == [{'action': 'permission_mode_status'}]
        await chip.click()
        await expect(chip).to_have_text(policy.MODE_LABEL['accept-edits'])
        await expect(chip).to_have_attribute('data-mode', 'accept-edits')
        assert chip_actions() == [{'action': 'permission_mode_status'}, {'action': 'permission_mode'}]
        assert s.state['mode'] == 'accept-edits'
        await expect(s.page.locator('#chat-mode')).to_have_text(policy.MODE_LABEL['accept-edits'])   # one truth
        await chip.click()
        await expect(chip).to_have_text(policy.MODE_LABEL['auto'])
        assert s.prompts == [] and s.errors == []
    async with studio(controls=False) as s:      # no runtime control in this session: no chip, nothing invented
        page = await open_nested(s)
        await expect(page.locator('#nd-read-twice')).to_be_visible()
        await expect(page.locator('#nd-mode')).to_be_hidden()
        assert s.errors == []


# --- (b) Read ×2: the chat's stored preference, mirrored both ways --------------------------------------------------

async def test_read_twice_chip_mirrors_the_chats_toggle_and_the_chat_doubles_its_next_message(studio):
    async with studio() as s:
        page = await open_nested(s)
        chip, chat = page.locator('#nd-read-twice'), s.page.locator('#read-twice')
        await expect(chip).to_have_attribute('aria-pressed', 'false')
        # until nested.js sends read_twice from its own composer, the chip says whose composer it applies to
        assert (await chip.get_attribute('title')).endswith('(applies to the chat composer for now)')
        await chip.click()
        await expect(chip).to_have_attribute('aria-pressed', 'true')
        await expect(chat).to_have_attribute('aria-pressed', 'true')
        assert await s.page.evaluate("localStorage.getItem('dream.readTwice')") == '1'
        # the chat's composer sends read_twice: the server stores the words doubled
        await s.page.locator('#dream-nav-chat').click()
        await s.page.locator('#input').fill('Keep the change small.')
        await s.page.locator('#send').click()
        await expect(s.page.locator('#stream .you')).to_have_count(1)
        assert s.prompts == ['Keep the change small.' + READ_AGAIN + 'Keep the change small.']
        await expect(s.page.locator('#stream .you')).to_contain_text('Keep the change small.')
        await expect(s.page.locator('#stream .you')).not_to_contain_text('Read the request above again')   # shown once
        # the preference survives a reload, and the chat's own button moves the chip
        await s.page.reload()
        await expect(s.page.locator('#stat')).to_have_text('Ready')
        page = await open_nested(s)
        await expect(page.locator('#nd-read-twice')).to_have_attribute('aria-pressed', 'true')
        await s.page.evaluate("document.getElementById('read-twice').click()")
        await expect(page.locator('#nd-read-twice')).to_have_attribute('aria-pressed', 'false')
        assert await s.page.evaluate("localStorage.getItem('dream.readTwice')") == '0'
        assert s.errors == []


# --- (c) the speeds and context from real stats, exactly as the chat shows them; blank before -------------------

async def test_stats_render_prefill_decode_and_context_as_the_chat_does_and_are_blank_before(studio):
    async with studio() as s:
        page = await open_nested(s)
        perf = page.locator('#nd-perf')
        await expect(perf).to_have_text('')
        s.srv.bus.publish(Event('stats', dict(STATS)))
        await expect(perf).to_have_text(PERF)
        await expect(s.page.locator('#perf')).to_have_text(PERF)                   # the chat's own line
        await expect(perf.locator('.nd-pp')).to_contain_text('prefill 500 tok/s')
        await expect(perf.locator('.nd-dd')).to_contain_text('decode 12.5 tok/s')
        await expect(perf.locator('.nd-cx')).to_have_text('context 3,000 / 32,768 (1K cached)')
        # a client-side estimate without a window: only what it carries, no NaN, no trailing separator
        s.srv.bus.publish(Event('stats', {'pp_n': 0, 'pp_ms': 0.0, 'gen_n': 40, 'gen_ms': 2000.0, 'exact': False,
                                          'ttft_ms': None, 'prompt_tokens': 0, 'cached': 0, 'window': None}))
        await expect(perf).to_have_text('decode 20.0 tok/s')
        await expect(perf.locator('.nd-pp, .nd-cx')).to_have_count(0)
        # stats are not retained for a reload: blank again until the next request reports
        await s.page.reload()
        await expect(s.page.locator('#stat')).to_have_text('Ready')
        page = await open_nested(s)
        await expect(page.locator('#nd-perf')).to_have_text('')
        assert s.errors == []


# --- (d) the tabs: the ARIA pattern; Workflows an honest empty state -----------------------------------------------

async def test_tabs_follow_the_aria_pattern_and_workflows_is_an_honest_empty_state(studio):
    async with studio() as s:
        page = await open_nested(s)
        tabs = page.locator('.nd-top [role=tablist]')
        await expect(tabs).to_have_attribute('aria-label', 'Views')
        agents, workflows = page.locator('#nd-tab-agents'), page.locator('#nd-tab-workflows')
        await expect(tabs.locator('[role=tab]')).to_have_text(['Agents', 'Workflows'])
        await expect(agents).to_have_attribute('aria-selected', 'true')
        await expect(agents).to_have_attribute('tabindex', '0')
        await expect(workflows).to_have_attribute('aria-selected', 'false')
        await expect(workflows).to_have_attribute('tabindex', '-1')
        await expect(agents).to_have_attribute('aria-controls', 'nd-agents')
        await expect(workflows).to_have_attribute('aria-controls', 'nd-workflows')
        main, wf = page.locator('#nd-agents'), page.locator('#nd-workflows')
        await expect(main).to_have_class(re.compile(r'\bnd-main\b'))                # the workers area is the panel
        await expect(main).to_have_attribute('role', 'tabpanel')
        await expect(main).to_have_attribute('aria-labelledby', 'nd-tab-agents')
        await expect(wf).to_have_attribute('role', 'tabpanel')
        await expect(wf).to_have_attribute('aria-labelledby', 'nd-tab-workflows')
        await expect(wf).to_be_hidden()
        await expect(main).to_be_visible()
        await page.locator('#nd-drawer-btn').click()
        await expect(page.locator('#nd-drawer')).to_be_visible()
        await workflows.click()
        await expect(workflows).to_have_attribute('aria-selected', 'true')
        await expect(agents).to_have_attribute('tabindex', '-1')
        await expect(wf).to_be_visible()
        await expect(main).to_be_hidden()
        await expect(page.locator('#nd-drawer')).to_be_hidden()                     # a panel switch closes it
        empty = wf.locator('.nd-wf-empty')
        await expect(empty).to_contain_text('No workflows here yet')                # what
        await expect(empty).to_contain_text('no backend')                            # why
        await expect(empty).to_contain_text('Create → Guided tasks')                 # next
        for sample in ['Frontier review loop', 'Run again', 'Save as template', 'Personas', 'judges score']:
            await expect(empty).not_to_contain_text(sample)
        assert await wf.evaluate('e => e.scrollWidth <= e.clientWidth')
        await shot(s.page, 'nested-workflows-1600.png')
        # the pointer is a real door: Create opens on its Guided tasks
        await wf.locator('#nd-wf-create').click()
        await expect(s.page.locator('#dream-create')).to_have_attribute('open', '')
        await expect(s.page.locator('#mc-guided')).to_be_visible()
        await expect(s.page.locator('#mc-guided-open')).to_have_attribute('aria-expanded', 'true')
        await s.page.locator('#mc-close').click()
        await expect(s.page.locator('#dream-create')).not_to_have_attribute('open', '')
        await expect(s.page.locator('#dream-create-open')).to_be_focused()        # Create's close returns focus there
        # the keyboard: arrows move selection and focus, Home and End reach the ends
        await workflows.focus()
        await s.page.keyboard.press('ArrowLeft')
        await expect(agents).to_have_attribute('aria-selected', 'true')
        await expect(agents).to_be_focused()
        await expect(main).to_be_visible()
        await s.page.keyboard.press('ArrowRight')
        await expect(workflows).to_have_attribute('aria-selected', 'true')
        await expect(workflows).to_be_focused()
        await s.page.keyboard.press('ArrowRight')                                   # wraps
        await expect(agents).to_have_attribute('aria-selected', 'true')
        await s.page.keyboard.press('End')
        await expect(workflows).to_have_attribute('aria-selected', 'true')
        await expect(wf).to_be_visible()
        await s.page.keyboard.press('Home')
        await expect(agents).to_have_attribute('aria-selected', 'true')
        await expect(agents).to_be_focused()
        await expect(main).to_be_visible()
        assert s.prompts == [] and s.errors == []


# --- (e) the row gives way in a narrow column without cutting the context, and the tabs tighten ----------------

async def test_footer_and_top_row_tighten_in_a_narrow_column_and_recover(studio):
    async with studio(width=901, height=760) as s:
        page = await open_nested(s)
        s.srv.bus.publish(Event('stats', dict(STATS)))
        foot = page.locator('#nd-foot')
        await expect(foot.locator('.nd-cx')).to_be_visible()
        await expect(foot.locator('.nd-pp')).to_be_hidden()                         # prefill gives way first
        await expect(foot).to_have_class(re.compile(r'\bnd-tight1\b'))
        assert await foot.evaluate('e => e.scrollWidth <= e.clientWidth + 1')
        assert await page.evaluate('e => e.scrollWidth <= e.clientWidth')
        assert await page.locator('#nd-tab-agents').evaluate('e => parseFloat(getComputedStyle(e).paddingLeft)') == 8
        # the owner's desktop: the column is wide enough for the whole row again
        await s.page.set_viewport_size({'width': 1920, 'height': 953})
        await expect(foot.locator('.nd-pp')).to_be_visible()
        await expect(foot.locator('.nd-dd')).to_be_visible()
        await expect(foot).not_to_have_class(re.compile(r'\bnd-tight'))
        await expect(page.locator('#nd-perf')).to_have_text(PERF)
        assert await page.locator('#nd-tab-agents').evaluate('e => parseFloat(getComputedStyle(e).paddingLeft)') == 12
        assert s.errors == []


# --- (f) the owner's two desktop sizes, with a plan, stats, a mode and five workers ---------------------------------

async def test_the_full_column_fits_at_the_owner_desktop_sizes(studio):
    async with studio() as s:
        page = await open_nested(s)
        await s.srv._accept_prompt('Write the release notes for 0.3.0, one section per subsystem.')
        s.srv.bus.publish(Event('turn_start', {}))
        s.srv.bus.publish(plan(PHASES))
        s.srv.bus.publish(Event('text_delta', 'Splitting the notes by subsystem; one worker each.'))
        for n, run in enumerate(('r-1', 'r-2', 'r-3', 'r-4', 'r-5'), 1):
            s.srv.bus.publish(_activity(run, 'writer', 'status', status='running', text='Subagent started.'))
            s.srv.bus.publish(_activity(run, 'writer', 'request', status='awaiting_response', request_index=1, model='fixture-27b', context={'used': 2048 * n, 'window': 32768}))
            s.srv.bus.publish(_activity(run, 'writer', 'thinking_report', text='The record for this subsystem lists three changes and one open item. ' * 2))
            s.srv.bus.publish(_activity(run, 'writer', 'response', status='received', request_index=1, duration_ms=1500, usage={'prompt_tokens': 2048, 'completion_tokens': 45},
                                        text=f'Subsystem {n}: three changes shipped; one item stays open until the gate passes.'))
        s.srv.bus.publish(_activity('r-4', 'writer', 'status', status='failed', text='Subagent timed out; work is incomplete.'))
        s.srv.bus.publish(Event('stats', dict(STATS)))
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(5)
        await expect(page.locator('#nd-mode')).to_have_text(policy.MODE_LABEL['ask'])
        await expect(page.locator('#nd-perf')).to_have_text(PERF)
        for width, height in [(1920, 953), (2554, 1338)]:
            await s.page.set_viewport_size({'width': width, 'height': height})
            await expect(page.locator('.nd-pnow .nd-pk')).to_have_text('Phase 2 of 4')
            assert await s.page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            assert await page.evaluate('e => e.scrollWidth <= e.clientWidth'), width
            foot = page.locator('#nd-foot')
            await expect(foot.locator('.nd-pp')).to_be_visible()
            assert await foot.evaluate('e => e.scrollWidth <= e.clientWidth + 1'), width
            await shot(s.page, f'nested-footer-{width}x{height}.png')
            await page.locator('#nd-plan-toggle').click()
            await shot(s.page, f'nested-footer-allphases-{width}x{height}.png')
            await page.locator('#nd-plan-toggle').click()
        assert s.errors == []


# --- (g) the mounts survive the layout module's side swap and Reset layout (DREAM-196) ------------------------------

async def test_mounts_survive_a_side_swap_and_a_reset_layout(studio):
    async with studio(width=1920, height=953) as s:
        page = await open_nested(s)
        s.srv.bus.publish(plan(PHASES))
        s.srv.bus.publish(Event('stats', dict(STATS)))
        await expect(page.locator('#nd-perf')).to_have_text(PERF)
        await expect(page.locator('#nd-mode')).to_have_text(policy.MODE_LABEL['ask'])
        left = 'e => e.getBoundingClientRect().left'
        orch, main, wf = page.locator('.nd-orch'), page.locator('#nd-agents'), page.locator('#nd-workflows')

        async def mounted():
            await expect(page.locator('.nd-orch #nd-plan')).to_be_visible()
            await expect(page.locator('.nd-pnow .nd-pk')).to_have_text('Phase 2 of 4')
            await expect(page.locator('.nd-orch .nd-composer #nd-foot')).to_be_visible()
            await expect(page.locator('#nd-perf')).to_have_text(PERF)
            await expect(page.locator('.nd-top [role=tablist] [role=tab]')).to_have_count(2)
            assert await page.locator('#nd-tab-agents').evaluate('e => e.previousElementSibling === null && e.parentElement.previousElementSibling.classList.contains("nd-title")')

        await mounted()
        assert await orch.evaluate(left) < await main.evaluate(left)
        await page.locator('#nd-side').click()                            # the orchestrator moves to the right
        await expect(page.locator('.nd-app')).to_have_attribute('data-side', 'right')
        await mounted()
        assert await orch.evaluate(left) > await main.evaluate(left)
        await page.locator('#nd-tab-workflows').click()                   # Workflows takes the workers' cell, on the left
        await expect(wf).to_be_visible()
        await expect(main).to_be_hidden()
        assert await orch.evaluate(left) > await wf.evaluate(left)
        assert await wf.evaluate('e => Math.abs(e.getBoundingClientRect().left - e.parentElement.getBoundingClientRect().left) < 1')
        await mounted()
        await page.locator('#nd-reset').click()                           # Reset layout: back to the left, nothing lost
        await expect(page.locator('.nd-app')).to_have_attribute('data-side', 'left')
        await expect(wf).to_be_visible()
        assert await orch.evaluate(left) < await wf.evaluate(left)
        await mounted()
        await page.locator('#nd-tab-agents').click()
        await expect(main).to_be_visible()
        await expect(wf).to_be_hidden()
        assert await orch.evaluate(left) < await main.evaluate(left)
        await mounted()
        assert s.prompts == [] and s.errors == []


# --- (h) a reconnect keeps the same session's numbers; another session's hello clears them (the gate's probe) -------

async def test_stats_survive_a_same_session_rejoin_and_clear_on_another_sessions_hello(studio):
    async with studio() as s:
        page = await open_nested(s)
        s.srv.bus.publish(Event('stats', dict(STATS)))
        await expect(page.locator('#nd-perf')).to_have_text(PERF)
        await reconnect(s)                                        # the same session: the last request's numbers stay
        await expect(page.locator('#nd-perf')).to_have_text(PERF)
        await expect(s.page.locator('#perf')).to_have_text(PERF)
        s.srv._session['session_id'] = 'another-session'
        await reconnect(s)                                        # another session: nothing of the old one
        await expect(page.locator('#nd-perf')).to_have_text('', timeout=10000)
        assert s.errors == []


# --- (i) nothing from the mockup ships; the modules are wired like the others --------------------------------------

def test_no_mockup_sample_content_ships_in_the_footer_or_workflows_modules():
    banned = ['Backend', 'Studio UI', 'Live memory switch', 'MiMo-V2.6 Flash', 'Qwen3.8', 'Frontier review loop',
              '.dream/wt/', '372k of 600k', 'fonts.googleapis', 'fonts.gstatic', 'cdn.', '-webkit-font-smoothing',
              'Run again', 'Save as template', 'Brand storyteller', 'Creative director', 'Blender, local GPU',
              'auto mode on', 'asking each change', '53,210', '11.3']
    files = [STATIC / 'nested-footer.js', STATIC / 'nested-footer.css', STATIC / 'nested-workflows.js']
    for path in files:
        assert path.is_file(), path
        text = path.read_text()
        for needle in banned:
            assert needle not in text, (path.name, needle)
    css = re.sub(r'/\*.*?\*/', '', (STATIC / 'nested-footer.css').read_text(), flags=re.S)
    assert re.search(r'^\s*:root\s*\{', css, re.M) is None
    for rule in re.findall(r'(?m)^\s*([^@{}][^{}]*)\{', css):
        for selector in re.split(r',(?![^(]*\))', rule):
            assert selector.strip().startswith('#dream-nested-page'), selector
    html = (STATIC / 'index.html').read_bytes().decode('utf-8', 'replace')
    drawer = html.index('<script src="/assets/nested-drawer.js"></script>')
    for tag in ['<link rel="stylesheet" href="/assets/nested-footer.css">', '<script src="/assets/nested-footer.js"></script>',
                '<script src="/assets/nested-workflows.js"></script>']:
        assert html.index(tag) > drawer, tag
    assert html.index('nested-footer.js') < html.index('nested-workflows.js')      # the tabs before their panel
    assert {'nested-footer.js', 'nested-footer.css', 'nested-workflows.js'} <= set(diagnostics._STATIC)
