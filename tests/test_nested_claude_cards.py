"""Nested Dream on a Claude-led session (the view of DREAM-212 and DREAM-213): the Claude SDK's sub-agents are read-only
cards -- their Stop, Pause and message box off, with the backend's own reason, and no request going out from them --
while the session's local helpers (delegate_local) keep every control; Pause all pauses what the backend pauses, and
says the backend's words when the backend would refuse it with them; `unknown` is its own state, never Failed; the empty
state and the P10 hint say what a Claude session shows; the drawer's Run fact tells two Claude runs apart.

The rows come from the real backend: AnthropicBackend's card mapping on a scripted SDK stream (the stream scripted as in
test_claude_subagent_cards.py) and LocalHelpers on a loopback engine (test_delegate_local.py's). The controls go through
the real /api/control route to AnthropicBackend.worker_control, as App._runtime_control sends them. No model, no GPU,
no Claude.
"""
from __future__ import annotations

import asyncio
import contextlib
import itertools
import re
from types import SimpleNamespace

import pytest
from anyio import CancelScope
from claude_agent_sdk import TextBlock, ToolUseBlock
from playwright.async_api import async_playwright, expect

from dream.core.backends.anthropic import WORKER_REFUSAL, WORKERS_REFUSAL, AnthropicBackend, _SubagentCards
from dream.core.backends.openai_compat import _Worker
from dream.core.local_helpers import LocalHelpers
from dream.core.providers import Provider
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from lease_isolation import isolated_lease_dir  # noqa: F401  (the helpers' leases under the test's tmp_path)
from test_claude_subagent_cards import ScriptedClient, launch, launched_in_background, lead_gets, said, turn_done
from test_delegate_local import WORKER, _LocalEngine, _tasks, _until
from test_nested_contrast import scan
from test_nested_view import STATIC, _activity, open_nested, studio  # noqa: F401

pytestmark = pytest.mark.asyncio
CLAUDE = AnthropicBackend.provider_label             # what hello.provider says on a Claude session
WORKER_ACTIONS = {'agent_stop', 'agent_pause', 'agent_resume', 'agents_pause_all', 'agent_message'}
# Three launches of the SDK's Agent tool; their card run ids are "claude-" + these ids, alike in their first 12 characters.
A, B, C = 'toolu_01AlphaQ7cXb2mNpR4sTvW8', 'toolu_01BravoK3dLf9gHjP2qWeR5', 'toolu_01CharlieM6nBv1cXz8aSdF'
PEWTER = 'rgb(74, 74, 92)'                          # nested.css --nd-pewter: the pip of the states that are neither good nor bad
EMPTY_CLAUDE = ("Claude's sub-agents appear here as read-only cards once one runs, and its local helpers "
                "(delegate_local) as full cards you can pause, stop and message.")
LANES_CLAUDE = "The local engine's lanes: how many of Claude's local helpers can run at once"
PAUSE_ALL = 'Pause every queued or working worker at its next round boundary'
PAUSE_ALL_CLAUDE = ("Pause every queued or working local worker at its next round boundary; Claude's own sub-agents "
                    "keep running.")
NOONE = 'No worker is running in this session; there is nothing to pause.'


@pytest.fixture
def claude_studio(monkeypatch):
    """A factory: a Studio server on a Claude session whose /api/control reaches a real AnthropicBackend's
    worker_control (the lead, its cards' rows on the same bus), and one Chromium page."""
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)

    @contextlib.asynccontextmanager
    async def open_studio(width=1600, height=1000):
        bus, posted = EventBus(), []
        lead = AnthropicBackend(system_prompt='fixture', mcp_server={}, preapproved_tool_ids=[], agents=None,
                                permission_cb=None, model='claude-lead', cwd='/tmp', activity_emit=bus.publish)

        def on_control(payload):                     # App._runtime_control's route for the workers' actions
            posted.append(payload)
            if payload.get('action') in WORKER_ACTIONS:
                return lead.worker_control(payload['action'], payload.get('run_id'), payload.get('text'))
            raise ValueError('Runtime controls are unavailable')

        srv = StudioServer(bus, on_prompt=lambda text: None, on_control=on_control,
                           session={'provider': CLAUDE, 'model': 'claude-lead', 'session_id': 'nested-claude', 'lanes': None})
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
                yield SimpleNamespace(srv=srv, page=page, lead=lead, posted=posted, errors=errors)
                await browser.close()
        finally:
            await srv.stop()
    return open_studio


def claude_turn(s, script):
    """The lead's turn on a scripted SDK stream, as the Engine runs it: its cards' rows reach the bus through the
    backend's emitter, and its own events are published after them."""
    s.lead.client = ScriptedClient(script)

    async def run():
        async with contextlib.aclosing(s.lead.ask('go')) as events:
            async for event in events:
                s.srv.bus.publish(event)
    return asyncio.create_task(run())


def asked(s):
    """The workers' actions that reached the server."""
    return [p for p in s.posted if p.get('action') in WORKER_ACTIONS]


def helpers_for(s, engine):
    helpers = LocalHelpers(tools=[], permission_cb=None, emit=s.srv.bus.publish, subagents=WORKER, provider=Provider(
        key='machx', label='MachX', kind='openai', base_url=engine.url, default_api_key='not-needed'))
    s.lead.local_helpers = helpers
    return helpers


async def test_the_claude_sdks_workers_are_read_only_and_say_why(claude_studio):
    """Three sub-agents of the SDK, live: every Stop and Pause is off and described by the backend's reason, the message
    boxes are read-only and say it, Pause all says the SDK's words for them all; nothing a pointer, a key, the drawer or
    the view's own entry points do sends a request; the disabled controls take no focus; the Run fact tells the runs
    apart. The backend refuses the same actions with the same words."""
    async with claude_studio() as s:
        page = await open_nested(s)
        release = asyncio.Event()
        turn = claude_turn(s, [launch((A, 'researcher', 'Agent'), (B, 'writer', 'Agent'), (C, 'editor', 'Agent')),
                               said(A, 'm1', TextBlock('Reading the sources.'), ToolUseBlock('t1', 'Read', {'file_path': 'notes.md'})),
                               release.wait,
                               lead_gets(A, 'Found three sources.'), lead_gets(B, 'Drafted.'), lead_gets(C, 'Edited.'), turn_done()])
        wins = [page.locator(f'.nd-win[data-run-id="claude-{run}"]') for run in (A, B)]
        card = page.locator(f'.nd-card[data-run-id="claude-{C}"]')
        await expect(wins[0].locator('.nd-state')).to_have_text('Working')
        await expect(wins[1]).to_be_visible()
        await expect(card).to_be_visible()
        for scope in (*wins, card):
            await expect(scope.locator('.nd-ctl')).to_have_count(2)
            for b in await scope.locator('.nd-ctl').all():
                await expect(b).to_be_disabled()
                await expect(b).to_have_accessible_description(WORKER_REFUSAL)
        for win in wins:
            box = win.locator('.nd-say')
            text = box.locator('textarea')
            await expect(text).to_have_attribute('aria-disabled', 'true')
            assert await text.evaluate('t => t.readOnly')
            await expect(text).to_have_accessible_description(WORKER_REFUSAL)
            await expect(box.locator('.nd-say-note')).to_have_text(WORKER_REFUSAL)
            await expect(box.locator('.nd-say-send')).to_be_disabled()
        pause_all = page.locator('#nd-pause-all')
        await expect(pause_all).to_be_disabled()
        await expect(pause_all).to_have_accessible_description(WORKERS_REFUSAL)
        # the backend answers these actions with the same words
        with pytest.raises(ValueError, match=re.escape(WORKER_REFUSAL)):
            s.lead.worker_control('agent_stop', f'claude-{C}')
        with pytest.raises(ValueError, match=re.escape(WORKERS_REFUSAL)):
            s.lead.worker_control('agents_pause_all')
        # no focus on them: from the card, Tab goes to its pin, past the two controls
        await card.focus()
        await s.page.keyboard.press('Tab')
        assert await s.page.evaluate('document.activeElement.dataset.fk') == 'pin-3'
        # nothing goes out: a pointer on every control, the keyboard, the drawer's detail, the view's own entry points
        for b in await page.locator('.nd-ctl').all():
            await b.click(force=True)
        await pause_all.click(force=True)
        await card.focus()
        await s.page.keyboard.press('Enter')                                        # opens C in the drawer
        detail = page.locator('#nd-detail')
        await expect(detail.locator('.nd-nm')).to_have_text('editor')
        for b in await detail.locator('.nd-ctl').all():
            await expect(b).to_be_disabled()
            await expect(b).to_have_accessible_description(WORKER_REFUSAL)
            await b.click(force=True)
        box = detail.locator('.nd-say textarea')
        await expect(box).to_have_accessible_description(WORKER_REFUSAL)
        await box.evaluate("t => { t.value = 'Stop, please.'; t.dispatchEvent(new Event('input', {bubbles: true})); }")
        await box.focus()
        await s.page.keyboard.press('Enter')
        await detail.locator('.nd-say-send').click(force=True)
        await s.page.evaluate(f"""() => {{ for (const a of ['agent_stop', 'agent_pause', 'agent_resume'])
            window.DreamNested.control('claude-{C}', a); }}""")
        await s.page.wait_for_timeout(400)
        assert asked(s) == []
        # the Run fact: each run's own tail, the whole id on hover
        facts = []
        for run in (A, B, C):
            await s.page.evaluate(f"() => window.DreamNestedDrawer.open('claude-{run}')")
            fact = detail.locator('.nd-dfacts span', has_text='Run')
            await expect(fact).to_have_attribute('title', f'claude-{run}')
            facts.append(await fact.inner_text())
        assert len(set(facts)) == 3 and all(run[-8:] in fact for run, fact in zip((A, B, C), facts)), facts
        release.set()
        await turn
        await expect(wins[0].locator('.nd-state')).to_have_text('Done')
        await expect(pause_all).to_be_disabled()
        await expect(pause_all).not_to_have_accessible_description(WORKERS_REFUSAL)   # nothing of the SDK's is live now
        await expect(pause_all).to_have_attribute('title', PAUSE_ALL_CLAUDE)
        assert s.errors == []


async def test_a_claude_session_with_local_helpers_pauses_only_the_helpers(claude_studio):
    """A Claude sub-agent and three local helpers on an engine with two lanes, live together: the helpers' cards keep
    every control and the SDK's is off; the lanes come from the `lanes` events (hello.lanes is null on Claude); Pause
    all is on and pauses the helpers alone, as the backend does; Resume and Stop reach a helper through the real route;
    once only the SDK's worker is live, Pause all is off with the backend's words for it."""
    engine = _LocalEngine(slots=2, gated=True)
    try:
        async with claude_studio() as s:
            page = await open_nested(s)
            release = asyncio.Event()
            turn = claude_turn(s, [launch((A, 'researcher', 'Agent')), release.wait, lead_gets(A, 'Found it.'), turn_done()])
            sdk = page.locator(f'.nd-win[data-run-id="claude-{A}"]')
            await expect(sdk.locator('.nd-state')).to_have_text('Working')
            helpers = helpers_for(s, engine)
            call = asyncio.create_task(helpers.delegate(_tasks(3)))
            await _until(lambda: engine.inflight == 2 and helpers.running and len(helpers.running[0]._workers) == 3)
            runs = list(helpers.running[0]._workers)
            lane = {run: page.locator(f'[data-run-id="{run}"]:is(.nd-win, .nd-card)') for run in runs}
            for run in runs:
                await expect(lane[run]).to_be_visible()
            await expect(page.locator('#nd-lanes')).to_have_text('lanes: 2 of 2 busy · 1 queued')
            await expect(page.locator('#nd-lanes')).to_have_attribute('title', LANES_CLAUDE)
            for run in runs:
                await expect(lane[run].locator('[data-ctl="agent_stop"]')).to_be_enabled()
                await expect(lane[run].locator('[data-ctl="agent_pause"]')).to_be_enabled()
                await expect(lane[run].locator('.nd-ctl').first).not_to_have_accessible_description(WORKER_REFUSAL)
            for b in await sdk.locator('.nd-ctl').all():
                await expect(b).to_be_disabled()
            # the Run fact of Dream's own worker keeps its id's head, as before (the gate's G8)
            await s.page.evaluate(f"() => window.DreamNestedDrawer.open('{runs[0]}')")
            fact = page.locator('#nd-detail .nd-dfacts span', has_text='Run')
            await expect(fact).to_have_text(f'Run {runs[0][:12]}')
            await expect(fact).to_have_attribute('title', runs[0])
            await s.page.evaluate('() => window.DreamNestedDrawer.close()')
            pause_all = page.locator('#nd-pause-all')
            await expect(pause_all).to_be_enabled()
            await expect(pause_all).to_have_attribute('title', PAUSE_ALL_CLAUDE)      # it does not reach the SDK's worker
            await expect(pause_all).not_to_have_accessible_description(WORKERS_REFUSAL)
            await pause_all.click()
            for run in runs:
                await expect(lane[run].locator('.nd-state')).to_have_text(re.compile('Pausing|Paused'))
            await expect(sdk.locator('.nd-state')).to_have_text('Working')
            # every helper pausing or paused and the SDK's worker live: nothing left to pause, and nothing refused
            # either -- the backend answers a no-op here -- so off, without the SDK's words (the gate's G2)
            await expect(pause_all).to_be_disabled()
            await expect(pause_all).not_to_have_accessible_description(WORKERS_REFUSAL)
            await expect(pause_all).to_have_attribute('title', PAUSE_ALL_CLAUDE)
            assert asked(s) == [{'action': 'agents_pause_all'}]
            assert {w.state for w in helpers.running[0]._workers.values()} <= {'pausing', 'paused'}
            # a paused helper goes on from its own Resume; another stops from its Stop
            for run in runs:
                await lane[run].locator('[data-ctl="agent_resume"]').click()
                await expect(lane[run].locator('.nd-state')).to_have_text(re.compile('Working|Queued'))
            await lane[runs[1]].locator('[data-ctl="agent_stop"]').click()
            await expect(lane[runs[1]].locator('.nd-state')).to_have_text(re.compile('Stopping|Stopped'))
            assert [p['action'] for p in asked(s)] == ['agents_pause_all', *['agent_resume'] * 3, 'agent_stop']
            assert [p.get('run_id') for p in asked(s)[1:]] == [*runs, runs[1]]
            engine.gate.set()
            text, failed = await call
            assert not failed
            await expect(lane[runs[0]].locator('.nd-state')).to_have_text('Done')
            await expect(lane[runs[1]].locator('.nd-state')).to_have_text('Stopped')
            # only the SDK's worker is live: the backend refuses Pause all with these words, and the view says them
            with pytest.raises(ValueError, match=re.escape(WORKERS_REFUSAL)):
                s.lead.worker_control('agents_pause_all')
            await expect(pause_all).to_be_disabled()
            await expect(pause_all).to_have_accessible_description(WORKERS_REFUSAL)
            release.set()
            await turn
            assert s.errors == []
    finally:
        engine.close()


async def test_unknown_is_its_own_state_never_failed(claude_studio):
    """A background sub-agent still running when the lead's turn ends (the backend's `unknown`), and a local worker
    whose completion could not be confirmed: a neutral pip, "Outcome unknown", the row's text as it came; never in the
    failed count or the Needs-you stack, but under "Failed or stopped" in the drawer. A tool call whose result never
    came reads the same in the window of a sub-agent that failed."""
    async with claude_studio() as s:
        page = await open_nested(s)
        await claude_turn(s, [launch((A, 'researcher', 'Agent'), (B, 'writer', 'Agent')), launched_in_background(A, 'a1'),
                              said(B, 'm1', TextBlock('Checking the tree.'), ToolUseBlock('t9', 'Glob', {'pattern': '*'})),
                              turn_done()])
        s.srv.bus.publish(_activity('r-9', 'writer', 'status', status='running', text='Subagent started.'))   # a card
        s.srv.bus.publish(_activity('r-9', 'writer', 'status', status='unknown',
                                    text='Subagent returned no output; completion could not be confirmed.'))
        texts = {'r-9': 'Subagent returned no output; completion could not be confirmed.',
                 f'claude-{A}': "Still running in the background when the lead's turn ended; Dream shows no more of it."}
        for run, text in texts.items():
            lane = page.locator(f'[data-run-id="{run}"]:is(.nd-win, .nd-card)')
            await expect(lane.locator('.nd-state')).to_have_text('Outcome unknown')
            await expect(lane.locator('.nd-headline')).to_have_text(text)
            pip = page.locator(f'.nd-pips .nd-pip[data-run-id="{run}"]')
            await expect(pip).to_have_class(re.compile(r'\bnd-unknown\b'))
            await expect(pip).to_have_attribute('aria-label', re.compile('Outcome unknown$'))
            assert await pip.evaluate('p => getComputedStyle(p).backgroundColor') == PEWTER
        failed = page.locator(f'.nd-win[data-run-id="claude-{B}"]')                  # its window lists its tool calls
        await expect(failed.locator('.nd-state')).to_have_text('Failed')
        tool = failed.locator('.nd-tool')
        await expect(tool.locator('.nd-badge')).to_have_text('Outcome unknown')
        await expect(tool.locator('.nd-tool-out')).to_have_text('Tool observation interrupted; outcome is unknown.')
        await expect(page.locator('#nd-attn .nd-fail b')).to_have_text('1')             # the failed one alone
        await expect(page.locator('#nd-stack .nd-ask-fail')).to_have_count(1)
        await expect(page.locator('#nd-stack .nd-ask-fail .nd-state')).to_have_text('Failed')
        await page.locator('#nd-drawer-btn').click()
        await page.locator('#nd-filters [data-f="failed"]').click()
        rows = page.locator('#nd-dlist .nd-item')
        await expect(rows).to_have_count(3)
        for run in texts:
            row = page.locator(f'#nd-dlist .nd-item[data-run-id="{run}"]')
            await expect(row.locator('.nd-state')).to_have_text('Outcome unknown')
            await expect(row.locator('.nd-pip')).to_have_class(re.compile(r'\bnd-unknown\b'))
        await s.page.evaluate(f"() => window.DreamNestedDrawer.open('claude-{A}')")
        await expect(page.locator('#nd-detail .nd-e-status .nd-badge').last).to_have_text('Outcome unknown')
        assert s.errors == []


async def test_a_claude_session_says_its_workers_appear_here(claude_studio):
    """No worker yet on Claude: the view and the drawer say its sub-agents come as read-only cards and its local
    helpers as full cards -- not that the SDK runs them out of sight, not that the provider reports nothing."""
    assert CLAUDE in (STATIC / 'nested.js').read_text()                               # the label the view matches, verbatim
    async with claude_studio() as s:
        page = await open_nested(s)
        empty = page.locator('.nd-empty')
        await expect(empty).to_contain_text('No workers yet')
        await expect(empty).to_contain_text(EMPTY_CLAUDE)
        assert await empty.evaluate("e => /out of sight|does not report/i.test(e.textContent)") is False
        await expect(page.locator('.nd-provider-note')).to_have_count(0)
        await page.locator('#nd-drawer-btn').click()
        await expect(page.locator('#nd-dlist .nd-dempty')).to_contain_text("read-only cards once one runs")
        assert await page.locator('#nd-dlist').evaluate("e => /out of sight|does not report/i.test(e.textContent)") is False
        assert s.errors == []


async def test_the_claude_cards_and_their_reasons_meet_aa(claude_studio):
    """The read-only cards, their windows with the reason in the message box, the drawer's detail and an unknown
    outcome, scanned in the page: every painted text meets its contrast (the disabled controls themselves are exempt)."""
    async with claude_studio() as s:
        page = await open_nested(s)
        release = asyncio.Event()
        turn = claude_turn(s, [launch((A, 'researcher', 'Agent'), (B, 'writer', 'Agent'), (C, 'editor', 'Agent')),
                               release.wait, lead_gets(A, 'Done.'), turn_done()])
        await expect(page.locator(f'.nd-win[data-run-id="claude-{A}"] .nd-say-note')).to_have_text(WORKER_REFUSAL)
        s.srv.bus.publish(_activity('r-9', 'writer', 'status', status='running', text='Subagent started.'))   # a card
        s.srv.bus.publish(_activity('r-9', 'writer', 'status', status='unknown',
                                    text='Subagent returned no output; completion could not be confirmed.'))
        await expect(page.locator('.nd-card[data-run-id="r-9"] .nd-state')).to_have_text('Outcome unknown')
        rows, bad = await scan(page)
        assert not bad, bad
        assert any(r['text'].startswith('This worker runs inside the Claude SDK') for r in rows), rows
        await s.page.evaluate(f"() => window.DreamNestedDrawer.open('claude-{C}')")
        await expect(page.locator('#nd-detail .nd-say-note')).to_have_text(WORKER_REFUSAL)
        rows, bad = await scan(page.locator('#nd-drawer'))
        assert not bad, bad
        release.set()
        await turn
        assert s.errors == []


# --- the gate's cards-ui-r1 findings 1, 2 and 3 -------------------------------------------------------------------

HELPER_STATES = ['queued', 'working', 'pausing', 'paused', 'stopping']
BACKEND_STATE = {'queued': 'running', 'working': 'running', 'pausing': 'pausing', 'paused': 'paused', 'stopping': 'stopping'}


def backend_pause_all(helper_states, sdk):
    """What the real AnthropicBackend.worker_control answers to agents_pause_all, its local helpers' workers in
    `helper_states` and the SDK's worker none, live or ended (the gate's cards-ui-r1 probe M, adopted): {'answer':
    'ok', 'runs', 'changed'} or {'answer': the refusal}."""
    lead = AnthropicBackend(system_prompt='x', mcp_server={}, preapproved_tool_ids=[], agents=None, permission_cb=None,
                            model='m')
    cards = lead._cards = _SubagentCards(None)
    if sdk in ('live', 'ended'):
        run = cards._run('toolu_01SdkRunX', 'researcher')
        if sdk == 'ended':
            cards._end(run, 'completed', 'done')
    helpers = LocalHelpers(tools=[], permission_cb=None, emit=None, subagents=WORKER, provider=Provider(
        key='machx', label='MachX', kind='openai', base_url='http://127.0.0.1:1/v1', default_api_key='not-needed'))
    if helper_states:
        helper = helpers._helper('m')
        for i, state in enumerate(helper_states):
            run_id = f'{i + 1:032x}'
            worker = helper._workers[run_id] = _Worker({'run_id': run_id, 'agent': 'worker', 'phase': 'subagent',
                                                        'round': 0, 'lane': True}, CancelScope())
            worker.state = BACKEND_STATE[state]
        helpers.running.append(helper)
    lead.local_helpers = helpers
    try:
        answer = lead.worker_control('agents_pause_all')
    except ValueError as exc:
        return {'answer': str(exc)}
    return {'answer': 'ok', 'runs': [r['run_id'] for r in answer['runs']], 'changed': [r['changed'] for r in answer['runs']]}


# The same state in the view: a history reset, then each helper's rows (their run ids as the backend's above) and the
# SDK's worker's.
VIEW_SETUP = """(combo) => {
  const fire = (kind, data) => window.dispatchEvent(new CustomEvent('dream:event', {detail: {m: {kind, data}, replay: false}}));
  const row = (run_id, agent, status, text) => fire('agent_activity', {run_id, agent, phase: 'subagent', kind: 'status', round: 0, status, text, data: {}});
  fire('history', null);
  combo.helpers.forEach((st, i) => { const id = String(i + 1).padStart(32, '0');
    if (st === 'queued') row(id, 'helper', 'queued', 'Waiting for an engine lane (2 of 2 busy)');
    else { row(id, 'helper', 'running', 'Subagent started.'); if (st !== 'working') row(id, 'helper', st, 'state ' + st); } });
  if (combo.sdk !== 'none') { row('claude-toolu_01SdkRunX', 'researcher', 'running', 'Subagent started.');
    if (combo.sdk === 'ended') row('claude-toolu_01SdkRunX', 'researcher', 'completed', 'done'); }
}"""
VIEW_READ = """async () => { await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  const b = document.getElementById('nd-pause-all'), d = b.getAttribute('aria-describedby');
  return {disabled: b.disabled, title: b.title, desc: d ? document.getElementById(d).textContent : ''}; }"""


async def test_pause_all_follows_the_backend_in_every_state(claude_studio):
    """(The gate's cards-ui-r1 matrix, adopted and made exact.) Every subset of the helpers' states times the SDK's
    worker none, live or ended, the view's Pause all beside the real worker_control's answer: on exactly where the
    backend would change a worker (the P5 rule: off where every live helper is already pausing, paused or stopping --
    the backend's no-op); the SDK's words exactly where the backend refuses with them -- every helper stopping, say --
    and never where it answers, not while the helpers are paused (the gate's G1 and G2)."""
    async with claude_studio() as s:
        await open_nested(s)
        combos = 0
        for n in range(len(HELPER_STATES) + 1):
            for helpers in itertools.combinations(HELPER_STATES, n):
                for sdk in ('none', 'live', 'ended'):
                    backend = backend_pause_all(list(helpers), sdk)
                    await s.page.evaluate(VIEW_SETUP, {'helpers': list(helpers), 'sdk': sdk})
                    view = await s.page.evaluate(VIEW_READ)
                    if backend['answer'] == WORKERS_REFUSAL:
                        want = {'disabled': True, 'title': WORKERS_REFUSAL, 'desc': WORKERS_REFUSAL}
                    elif backend['answer'] == NOONE:
                        want = {'disabled': True, 'title': PAUSE_ALL_CLAUDE, 'desc': ''}
                    else:
                        assert backend['answer'] == 'ok' and not any(r.startswith('claude-') for r in backend['runs']), backend
                        want = {'disabled': not any(backend['changed']), 'title': PAUSE_ALL_CLAUDE, 'desc': ''}
                    assert view == want, (helpers, sdk, backend)
                    combos += 1
        assert combos == 96 and s.errors == []


async def test_pause_all_says_what_it_pauses_on_each_provider(studio, claude_studio):
    """The gate's cards-ui-r1 finding 2: on Claude, Pause all pauses the local workers only, and its title says so; on
    the local engine its title is as it was."""
    async with studio() as s:
        page = await open_nested(s)
        await expect(page.locator('#nd-pause-all')).to_have_attribute('title', PAUSE_ALL)
    async with claude_studio() as s:
        page = await open_nested(s)
        await expect(page.locator('#nd-pause-all')).to_have_attribute('title', PAUSE_ALL_CLAUDE)
        await expect(page.locator('#nd-pause-all')).to_have_accessible_description(PAUSE_ALL_CLAUDE)
        assert s.errors == []


# The placeholder's width in the box's font against the room on its one line.
MEASURE = """t => { const cs = getComputedStyle(t), c = document.createElement('canvas').getContext('2d');
  c.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  return [c.measureText(t.placeholder).width, t.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)]; }"""


@pytest.mark.parametrize('w,h', [(1280, 800), (901, 700), (1600, 1000)])
async def test_the_read_only_boxs_placeholder_fits_its_line(claude_studio, w, h):
    """The gate's cards-ui-r1 finding 3: at 1280 x 800 the read-only box's placeholder wrapped and its second line was
    cut. It fits the box's one line in both pinned windows and in the drawer's detail; the whole reason stays below."""
    async with claude_studio(width=w, height=h) as s:
        page = await open_nested(s)
        release = asyncio.Event()
        turn = claude_turn(s, [launch((A, 'researcher', 'Agent'), (B, 'writer', 'Agent')), release.wait,
                               lead_gets(A, 'a'), lead_gets(B, 'b'), turn_done()])
        boxes = [page.locator(f'.nd-win[data-run-id="claude-{run}"] .nd-say') for run in (A, B)]
        for box in boxes:
            await expect(box.locator('.nd-say-note')).to_have_text(WORKER_REFUSAL)
        await s.page.evaluate(f"() => window.DreamNestedDrawer.open('claude-{A}')")
        boxes.append(page.locator('#nd-detail .nd-say'))
        await expect(boxes[-1].locator('.nd-say-note')).to_have_text(WORKER_REFUSAL)
        for box in boxes:
            text = box.locator('textarea')
            assert await text.get_attribute('placeholder')
            width, room = await text.evaluate(MEASURE)
            assert 0 < width <= room, (w, h, width, room)
        release.set()
        await turn
        assert s.errors == []
