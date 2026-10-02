"""Nested Dream phase 1 (DREAM-188, ADR-068): a view over the session's own events. The orchestrator is the lead, the
workers are its `task` sub-agents on an OpenAI-compatible backend, one lane per agent_activity run_id; the page rebuilds
its state from the chat's `dream:event` hook, live and on history replay. Real StudioServer and EventBus, a real
OpenAICompatBackend with a scripted transport; no model, no engine, no Dream process."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright, expect

from dream.core.backends.base import Event
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.subagents import LocalSubagentSpec
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer

sys.path.insert(0, str(Path(__file__).parent))
from test_local_subagents import _FakeClient, _sse, _tool  # noqa: E402

pytestmark = pytest.mark.asyncio
STATIC = Path(__file__).resolve().parents[1] / 'dream/gui/static'
PAGE = '#dream-nested-page'
PROMPTS = ['alpha', 'beta', 'gamma']
SHOTS = os.environ.get('NESTED_SHOTS')          # optional folder for screenshots of the fixtures


async def shot(page, name):
    if SHOTS:
        await page.screenshot(path=os.path.join(SHOTS, name), full_page=False)


@pytest.fixture
def studio(monkeypatch):
    """A factory: one Studio server and one Chromium page per `async with studio(provider) as s`."""
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)

    @contextlib.asynccontextmanager
    async def open_studio(provider='MachX', width=1600, height=1000, lanes=None):
        prompts, steers = [], []

        async def on_steer(text, identifier, target):
            steers.append((text, identifier, target))
            return {'status': 'pending'}

        srv = StudioServer(EventBus(), on_prompt=prompts.append, on_steer=on_steer,
                           session={'provider': provider, 'model': 'fixture', 'session_id': 'nested-test', 'lanes': lanes})
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
                yield SimpleNamespace(srv=srv, page=page, prompts=prompts, steers=steers, errors=errors)
                await browser.close()
        finally:
            await srv.stop()
    return open_studio


async def open_nested(s):
    await s.page.locator('#dream-nav-nested').click()
    await expect(s.page.locator(PAGE)).to_be_visible()
    return s.page.locator(PAGE)


# --- the lead round: three contiguous `task` calls, then a text reply -------------------------------------------

def _lead_round():
    deltas = [{'index': i, 'id': f'c{i + 1}', 'function': {'name': 'task', 'arguments': json.dumps(
        {'subagent_type': 'worker', 'prompt': prompt})}} for i, prompt in enumerate(PROMPTS)]
    return [_sse({'choices': [{'delta': {'tool_calls': deltas}, 'finish_reason': None}]}),
            _sse({'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}]}), 'data: [DONE]']


def _lead_final(text):
    return [_sse({'choices': [{'delta': {'content': text}, 'finish_reason': 'stop'}]}), 'data: [DONE]']


def _backend(release: asyncio.Event) -> OpenAICompatBackend:
    """A real backend on a provider whose `task` calls run together (openai, non-loopback), with a scripted lead
    stream and a per-worker scripted reply: the first request of every worker waits on `release`, so the lanes are
    seen Working before they are Done; each worker then calls web_search once and reports."""
    provider = SimpleNamespace(key='openai', label='OpenAI', base_url='https://api.example.com/v1',
                               multimodal=False, api_key=lambda: 'n')

    async def web_search(args):
        return {'content': [{'type': 'text', 'text': 'found ' + str(args.get('query'))}]}

    subs = {'worker': LocalSubagentSpec(name='worker', description='does work', prompt='You are a worker.',
                                        tool_names=('web_search',))}
    backend = OpenAICompatBackend(provider=provider, model='fixture-model', system_prompt='S',
                                  tools=[_tool('web_search', web_search)], permission_cb=None, subagents=subs)
    backend.n_ctx = 16384
    backend._client = _FakeClient(stream_scripts=[_lead_round(), _lead_final('All three workers reported.')])
    seen: dict[str, int] = {}

    async def post(payload):
        prompt = payload['messages'][1]['content']
        n = seen.get(prompt, 0)
        seen[prompt] = n + 1
        if n == 0:
            await release.wait()
            return SimpleNamespace(json=lambda: {'choices': [{'message': {
                'content': None, 'reasoning_content': f'Thinking about {prompt}.',
                'tool_calls': [{'id': 'call-' + prompt, 'function': {
                    'name': 'web_search', 'arguments': json.dumps({'query': prompt})}}]}}]})
        return SimpleNamespace(json=lambda: {'choices': [{'message': {'content': f'Report on {prompt}.',
                                                                      'tool_calls': []}}]})
    backend._post_with_retry = post
    return backend


async def drive(s, backend, prompt):
    """What the engine does around a turn: the owner's message, the turn markers, every lead event and every
    sub-agent activity row on the session's one bus."""
    await s.srv._accept_prompt(prompt)
    backend._background_emit = s.srv.bus.publish
    s.srv.bus.publish(Event('turn_start', {}))
    try:
        async for event in backend.ask(prompt):
            s.srv.bus.publish(event)
    finally:
        s.srv.bus.publish(Event('turn_end', {}))


def _activity(run_id, agent, kind, **fields):
    return Event('agent_activity', {'run_id': run_id, 'agent': agent, 'phase': 'subagent', 'kind': kind, **fields})


# --- (a) navigation ------------------------------------------------------------------------------------------------

async def test_nav_shows_the_nested_page_and_hides_the_chat_footer(studio):
    async with studio() as s:
        page = await open_nested(s)
        await expect(page).to_have_attribute('aria-label', 'Nested Dream')
        await expect(s.page.locator('footer')).to_be_hidden()
        assert await s.page.evaluate('document.documentElement.dataset.dreamView') == 'nested'
        await expect(s.page.locator('#dream-nav [aria-current="page"]')).to_have_count(1)
        await expect(s.page.locator('#dream-nav-nested')).to_have_attribute('aria-current', 'page')
        assert await s.page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await s.page.locator('#dream-nav-chat').click()
        await expect(page).to_be_hidden()
        await expect(s.page.locator('footer')).to_be_visible()
        assert s.errors == [] and s.prompts == []


# --- (b) three real workers: lanes, pins, cards, Working -> Done, thinking and tool rows ----------------------------

async def test_three_task_calls_become_three_lanes_working_then_done(studio):
    async with studio() as s:
        page = await open_nested(s)
        release = asyncio.Event()
        turn = asyncio.create_task(drive(s, _backend(release), 'Ship the Nested view.'))
        try:
            pips = page.locator('.nd-pips .nd-pip')
            await expect(pips).to_have_count(3)
            for i in range(3):
                await expect(pips.nth(i)).to_have_class(re.compile(r'\bnd-working\b'))
                await expect(pips.nth(i)).to_have_attribute('aria-label', f'{i + 1} worker: Working')
                await expect(pips.nth(i)).to_have_attribute('title', f'{i + 1} worker: Working')
                await expect(pips.nth(i)).to_have_text(str(i + 1))
            wins = page.locator('.nd-win[data-run-id]')
            cards = page.locator('.nd-card[data-run-id]')
            await expect(wins).to_have_count(2)
            await expect(cards).to_have_count(1)
            ids = await page.locator('[data-run-id]').evaluate_all('els => els.map(e => e.dataset.runId)')
            assert len(set(ids)) == 3 and len(ids) == 6, ids          # three runs: a pip and a window or card each
            assert [await wins.nth(i).locator('.nd-state').text_content() for i in range(2)] == ['Working', 'Working']
            await expect(cards.first.locator('.nd-state')).to_have_text('Working')
            await expect(page.locator('.nd-goal')).to_have_text('Ship the Nested view.')
            await expect(page.locator('.nd-empty')).to_have_count(0)
            release.set()
            await asyncio.wait_for(turn, 10)
            for i in range(3):
                await expect(pips.nth(i)).to_have_class(re.compile(r'\bnd-done\b'))
                await expect(pips.nth(i)).to_have_attribute('aria-label', f'{i + 1} worker: Done')
            await expect(wins.first.locator('.nd-state')).to_have_text('Done')
            await expect(cards.first.locator('.nd-state')).to_have_text('Done')
            # the worker's own words and tools, from agent_activity: the thinking tail is visible while collapsed
            first = wins.first
            await expect(first.locator('.nd-think summary')).to_be_visible()
            await expect(first.locator('.nd-think summary')).to_contain_text('Thinking about alpha.')
            await expect(first.locator('.nd-tool')).to_have_count(1)
            await expect(first.locator('.nd-tool')).to_contain_text('web_search')
            await expect(first.locator('.nd-tool')).to_contain_text('Returned')
            await expect(first.locator('.nd-tool')).to_contain_text('found alpha')
            await expect(first.locator('.nd-headline')).to_contain_text('finished')
            await expect(wins.nth(1).locator('.nd-think summary')).to_contain_text('Thinking about beta.')
            await expect(cards.first).to_contain_text('gamma')
            # the lead's own reply streams into the orchestrator column
            await expect(page.locator('.nd-orch')).to_contain_text('All three workers reported.')
            assert s.prompts == ['Ship the Nested view.'] and s.errors == []
        finally:
            release.set()
            if not turn.done():
                turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)


# --- (c) the lead's stream and the composer ------------------------------------------------------------------------

async def test_lead_text_and_thinking_stream_to_the_orchestrator_and_the_composer_steers_during_a_turn(studio):
    async with studio() as s:
        page = await open_nested(s)
        await expect(page.locator('.nd-goal')).to_contain_text('No goal yet')
        await s.srv._accept_prompt('Plan the release.')
        await expect(page.locator('.nd-goal')).to_have_text('Plan the release.')
        await expect(page.locator('.nd-msg.nd-you')).to_contain_text('Plan the release.')
        s.srv.bus.publish(Event('turn_start', {}))
        s.srv.bus.publish(Event('thinking_delta', 'Weighing the options.'))
        s.srv.bus.publish(Event('text_delta', 'Planning '))
        s.srv.bus.publish(Event('text_delta', 'the work.'))
        stream = page.locator('.nd-msg.nd-dream .nd-stream')
        await expect(stream).to_have_text('Planning the work.')
        await expect(page.locator('.nd-msg.nd-dream .nd-think summary')).to_contain_text('Weighing the options.')
        # while the turn runs the composer steers: delivery steer, a receipt id, never the after-turn queue
        await expect(page.locator('#nd-send')).to_have_text('Steer')
        await page.locator('#nd-compose').fill('Prefer the smaller change.')
        await page.locator('#nd-send').click()
        await expect(page.locator('.nd-msg.nd-you').last).to_contain_text('Prefer the smaller change.')
        assert [t for t, _, _ in s.steers] == ['Prefer the smaller change.']
        assert re.fullmatch(r'[a-f0-9]{32}', s.steers[0][1])
        assert s.prompts == ['Plan the release.']
        await expect(page.locator('#nd-compose')).to_have_value('')
        await expect(page.locator('#nd-status')).to_contain_text('next model request')
        # between turns it sends: the ordinary queue
        s.srv.bus.publish(Event('result', {}))
        s.srv.bus.publish(Event('turn_end', {}))
        await expect(page.locator('#nd-send')).to_have_text('Send')
        await page.locator('#nd-compose').fill('Now the docs.')
        await page.locator('#nd-compose').press('Enter')
        await expect(page.locator('.nd-goal')).to_have_text('Now the docs.')
        assert s.prompts == ['Plan the release.', 'Now the docs.'] and len(s.steers) == 1
        assert s.errors == []


# --- (d) a reload rebuilds the lanes from the retained history -----------------------------------------------------

async def test_reload_rebuilds_the_lanes_from_history(studio):
    async with studio() as s:
        page = await open_nested(s)
        await s.srv._accept_prompt('Index the archive.')
        s.srv.bus.publish(Event('turn_start', {}))
        for n, run in enumerate(('r-one', 'r-two', 'r-three'), 1):
            s.srv.bus.publish(_activity(run, 'researcher', 'status', status='running', text='Subagent started.'))
            s.srv.bus.publish(_activity(run, 'researcher', 'request', status='awaiting_response', request_index=1))
            s.srv.bus.publish(_activity(run, 'researcher', 'response', status='received', request_index=1))
            s.srv.bus.publish(_activity(run, 'researcher', 'thinking_report', text=f'Reading shelf {n}.'))
            s.srv.bus.publish(_activity(run, 'researcher', 'tool_use', status='requested',
                                        data={'id': f'{run}:1:0:t', 'name': 'read_file', 'input': {'path': f'shelf{n}.md'}}))
            s.srv.bus.publish(_activity(run, 'researcher', 'tool_result', status='returned',
                                        data={'id': f'{run}:1:0:t', 'name': 'read_file', 'content': f'shelf {n} read', 'is_error': False}))
        s.srv.bus.publish(_activity('r-one', 'researcher', 'status', status='completed', text='Subagent finished; its result was returned to the lead agent.'))
        s.srv.bus.publish(_activity('r-two', 'researcher', 'status', status='failed', text='Subagent timed out; work is incomplete.'))
        s.srv.bus.publish(_activity('r-three', 'researcher', 'status', status='interrupted', text='Subagent observation was interrupted. In-flight effects may need checking.'))
        s.srv.bus.publish(Event('text_delta', 'Two of three shelves are indexed.'))
        s.srv.bus.publish(Event('result', {}))
        s.srv.bus.publish(Event('turn_end', {}))
        pips = page.locator('.nd-pips .nd-pip')
        await expect(pips).to_have_count(3)
        await expect(page.locator('.nd-attn')).to_contain_text('2 failed')
        await s.page.reload()
        await expect(s.page.locator('#stat')).to_have_text('Ready')
        page = await open_nested(s)
        pips = page.locator('.nd-pips .nd-pip')
        await expect(pips).to_have_count(3)
        await expect(pips.nth(0)).to_have_attribute('aria-label', '1 researcher: Done')
        await expect(pips.nth(1)).to_have_attribute('aria-label', '2 researcher: Failed')
        await expect(pips.nth(2)).to_have_attribute('aria-label', '3 researcher: Stopped')
        await expect(pips.nth(0)).to_have_class(re.compile(r'\bnd-done\b'))
        await expect(pips.nth(1)).to_have_class(re.compile(r'\bnd-failed\b'))
        await expect(pips.nth(2)).to_have_class(re.compile(r'\bnd-stopped\b'))
        await expect(page.locator('.nd-attn')).to_contain_text('2 failed')
        wins = page.locator('.nd-win[data-run-id]')
        await expect(wins).to_have_count(2)
        await expect(wins.nth(0)).to_have_attribute('data-run-id', 'r-one')
        await expect(wins.nth(1)).to_have_attribute('data-run-id', 'r-two')
        await expect(page.locator('.nd-card[data-run-id]')).to_have_count(1)
        await expect(page.locator('.nd-card[data-run-id]')).to_have_attribute('data-run-id', 'r-three')
        await expect(wins.nth(0).locator('.nd-think summary')).to_contain_text('Reading shelf 1.')
        await expect(wins.nth(0).locator('.nd-tool')).to_contain_text('read_file')
        await expect(wins.nth(1).locator('.nd-headline')).to_contain_text('timed out')
        await expect(page.locator('.nd-goal')).to_have_text('Index the archive.')
        await expect(page.locator('.nd-msg.nd-dream .nd-stream')).to_have_text('Two of three shelves are indexed.')
        assert s.prompts == ['Index the archive.'] and s.errors == []


# --- (e) empty states: no workers yet, and a provider that reports none -------------------------------------------

async def test_empty_state_says_what_why_and_next(studio):
    async with studio() as s:
        page = await open_nested(s)
        empty = page.locator('.nd-empty')
        await expect(empty).to_be_visible()
        await expect(empty).to_contain_text('No workers yet')
        await expect(empty).to_contain_text('task')            # why: workers come from the orchestrator's task tool
        await expect(empty).to_contain_text('goal')            # next: give it a goal
        await expect(page.locator('.nd-provider-note')).to_have_count(0)
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(0)
        assert s.errors == []


def _silent_labels():
    """What hello.provider says on each provider that reports no worker activity: the backends' own labels, never a
    copy that can drift (the gate found Grok's and Gemini's copied from the providers table). Claude's sub-agents show
    as read-only cards since DREAM-212 (test_nested_claude_cards.py)."""
    from dream.core.backends.cli_agent import CodexAdapter
    from dream.core.backends.gemini_adapter import GeminiAdapter
    from dream.core.backends.grok_adapter import GrokAdapter
    return [CodexAdapter.label, GrokAdapter.label, GeminiAdapter.label]


@pytest.mark.parametrize('provider', _silent_labels())
async def test_a_silent_provider_shows_the_note_and_never_a_lane(studio, provider):
    assert provider in (STATIC / 'nested.js').read_text(), provider          # the label the view matches, verbatim
    async with studio(provider=provider) as s:
        page = await open_nested(s)
        await expect(page.locator('.nd-provider-note')).to_have_text('This provider does not report worker activity.')
        await expect(page.locator('.nd-empty')).to_contain_text('OpenAI-compatible')
        await expect(page.locator('.nd-empty')).not_to_contain_text('the Claude SDK')   # it shows Claude's since DREAM-212
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(0)
        await expect(page.locator('.nd-win[data-run-id]')).to_have_count(0)
        assert s.errors == []


# --- (g) the facts DREAM-189 rows may carry: shown when present, never guessed; deltas append (DREAM-190) ----------

async def test_worker_facts_and_output_render_only_when_the_rows_carry_them(studio):
    async with studio() as s:
        page = await open_nested(s)
        s.srv.bus.publish(_activity('r-a', 'coder', 'status', status='running', text='Subagent started.'))
        win = page.locator('.nd-win[data-run-id="r-a"]')
        await expect(win).to_be_visible()
        await expect(win.locator('.nd-md')).to_have_count(0)          # the opening row names no model: nothing shown
        await expect(win.locator('.nd-ctx')).to_have_count(0)
        await expect(win.locator('.nd-rate')).to_have_count(0)
        await expect(win.locator('.nd-stream')).to_have_count(0)
        s.srv.bus.publish(_activity('r-a', 'coder', 'request', status='awaiting_response', request_index=1,
                                    model='fixture-27b', context={'used': 4096, 'window': 32768}))
        await expect(win.locator('.nd-md')).to_have_text('fixture-27b')
        await expect(win.locator('.nd-ctx')).to_have_text('13% · 4,096/32,768')
        await expect(win.locator('.nd-request')).to_have_text('Request 1 · waiting for the reply')
        s.srv.bus.publish(_activity('r-a', 'coder', 'response', status='received', request_index=1, model='fixture-27b',
                                    context={'used': 5120, 'window': 32768}, duration_ms=2000,
                                    text='First I read the file.', usage={'prompt_tokens': 4096, 'completion_tokens': 50}))
        await expect(win.locator('.nd-ctx')).to_have_text('16% · 5,120/32,768')
        await expect(win.locator('.nd-rate')).to_have_text('25.0 tok/s')
        await expect(win.locator('.nd-stream')).to_have_text('First I read the file.')
        await expect(win.locator('.nd-request')).to_have_text('Request 1 · reply received')
        # a reply without usage or duration keeps its text and shows no speed
        s.srv.bus.publish(_activity('r-a', 'coder', 'response', status='received', request_index=2, model='fixture-27b',
                                    text='Then I changed it.'))
        await expect(win.locator('.nd-stream')).to_have_text('First I read the file.\n\nThen I changed it.')
        await expect(win.locator('.nd-rate')).to_have_count(0)
        # a collapsed card shows the tail of what its worker wrote, and live deltas append to it; the response row
        # that closes a streamed round does not repeat the deltas
        for run in ('r-b', 'r-c'):
            s.srv.bus.publish(_activity(run, 'coder', 'status', status='running', text='Subagent started.'))
        s.srv.bus.publish(_activity('r-c', 'coder', 'response', status='received', request_index=1, text='Card text from the worker.'))
        card = page.locator('.nd-card[data-run-id="r-c"]')
        await expect(card.locator('.nd-cbody')).to_have_text('Card text from the worker.')
        s.srv.bus.publish(_activity('r-c', 'coder', 'text_delta', text='Stream'))
        s.srv.bus.publish(_activity('r-c', 'coder', 'text_delta', text='ing.'))
        await expect(card.locator('.nd-cbody')).to_have_text('Card text from the worker.\n\nStreaming.')
        s.srv.bus.publish(_activity('r-c', 'coder', 'response', status='received', request_index=2, text='Streaming.'))
        await expect(card.locator('.nd-cbody')).to_have_text('Card text from the worker.\n\nStreaming.')
        s.srv.bus.publish(_activity('r-c', 'coder', 'thinking_delta', text='Half a '))
        s.srv.bus.publish(_activity('r-c', 'coder', 'thinking_delta', text='thought.'))
        s.srv.bus.publish(_activity('r-c', 'coder', 'status', status='completed', text='Subagent finished; its result was returned to the lead agent.'))
        await expect(card.locator('.nd-state')).to_have_text('Done')
        await expect(page.locator('.nd-pips .nd-pip').nth(2)).to_have_attribute('aria-label', '3 coder: Done')
        assert s.errors == []


# --- (i) a streamed worker (DREAM-190's shape): deltas per request and kind, reconciled with the response row -------

async def test_streamed_worker_rows_render_in_order_and_never_twice(studio, monkeypatch):
    from dream import agent_activity
    # The backend's P3 whitelist (fa8d432 on nested-backend) lets the two delta kinds into the reconnect history; this
    # tree lacks it, so the test stands it in to exercise the replay path.
    monkeypatch.setattr(agent_activity, '_KINDS', agent_activity._KINDS | {'text_delta', 'thinking_delta'})
    async with studio() as s:
        page = await open_nested(s)

        def row(kind, index, **fields):
            return _activity('r-s', 'researcher', kind, request_index=index, model='fixture-27b',
                             context={'used': 900, 'window': 32768}, **fields)
        s.srv.bus.publish(_activity('r-s', 'researcher', 'status', status='running', text='Subagent started.'))
        s.srv.bus.publish(row('request', 1, status='awaiting_response'))
        s.srv.bus.publish(row('thinking_delta', 1, text='I will check '))
        s.srv.bus.publish(row('thinking_delta', 1, text='the source first.'))
        s.srv.bus.publish(row('text_delta', 1, text='The source con'))
        win = page.locator('.nd-win[data-run-id="r-s"]')
        await expect(win.locator('.nd-think summary')).to_contain_text('I will check the source first.')
        await expect(win.locator('.nd-stream')).to_have_text('The source con')
        s.srv.bus.publish(row('text_delta', 1, text='firms the observation.'))
        await expect(win.locator('.nd-stream')).to_have_text('The source confirms the observation.')
        # the response row carries the whole text: identical, so it is kept once
        s.srv.bus.publish(row('response', 1, status='received', text='The source confirms the observation.', duration_ms=2000,
                              usage={'prompt_tokens': 30, 'completion_tokens': 10}))
        await expect(win.locator('.nd-stream')).to_have_text('The source confirms the observation.')
        await expect(win.locator('.nd-rate')).to_have_text('5.0 tok/s')
        s.srv.bus.publish(row('tool_use', 1, status='requested', data={'id': 'r-s:1:0:c1', 'name': 'web_search', 'input': {'query': 'observation'}}))
        s.srv.bus.publish(row('tool_result', 1, status='returned', data={'id': 'r-s:1:0:c1', 'name': 'web_search', 'content': 'source observed', 'is_error': False}))
        # round two: the response's text differs from what streamed (a redaction): the response replaces it, once
        s.srv.bus.publish(row('request', 2, status='awaiting_response'))
        s.srv.bus.publish(row('thinking_delta', 2, text='Second thought.'))
        s.srv.bus.publish(row('text_delta', 2, text='Round two, secret token abc'))
        await expect(win.locator('.nd-stream')).to_have_text('The source confirms the observation.\n\nRound two, secret token abc')
        s.srv.bus.publish(row('response', 2, status='received', text='Round two, [Credential omitted]'))
        await expect(win.locator('.nd-stream')).to_have_text('The source confirms the observation.\n\nRound two, [Credential omitted]')
        await expect(win.locator('.nd-think summary')).to_contain_text('Second thought.')
        # the note a refused streamed request leaves in the lane
        s.srv.bus.publish(_activity('r-s', 'researcher', 'status', status='running',
                                    text='The server refused the streamed request (streaming is not supported here); workers run without streaming from here.'))
        await expect(win.locator('.nd-headline')).to_contain_text('refused the streamed request')
        s.srv.bus.publish(_activity('r-s', 'researcher', 'status', status='completed', text='Subagent finished; its result was returned to the lead agent.'))
        await expect(win.locator('.nd-state')).to_have_text('Done')
        # the transcript keeps the arrival order and shows each round's text once
        await page.locator('.nd-pips .nd-pip').first.click()
        entries = page.locator('#nd-detail .nd-dbody .nd-entry')
        kinds = await entries.evaluate_all("els => els.map(e => e.className.replace('nd-entry', '').trim())")
        assert kinds == ['nd-e-status', 'nd-e-req', 'nd-e-think', 'nd-stream', 'nd-e-req', 'nd-tool',
                         'nd-e-req', 'nd-e-think', 'nd-stream', 'nd-e-req', 'nd-e-status', 'nd-e-status'], kinds
        await expect(entries.nth(2)).to_contain_text('I will check the source first.')
        await expect(entries.nth(3)).to_have_text('The source confirms the observation.')
        await expect(entries.nth(8)).to_have_text('Round two, [Credential omitted]')
        await expect(entries.nth(10)).to_contain_text('refused the streamed request')
        await s.page.keyboard.press('Escape')
        await s.page.keyboard.press('Escape')
        # a reload rebuilds the same words from the retained (merged) rows
        await s.page.reload()
        await expect(s.page.locator('#stat')).to_have_text('Ready')
        page = await open_nested(s)
        win = page.locator('.nd-win[data-run-id="r-s"]')
        await expect(win.locator('.nd-stream')).to_have_text('The source confirms the observation.\n\nRound two, [Credential omitted]')
        await expect(win.locator('.nd-think summary')).to_contain_text('Second thought.')
        await expect(win.locator('.nd-state')).to_have_text('Done')
        assert s.errors == []


# --- (h) the layout holds at desktop, laptop and phone widths -------------------------------------------------------

async def test_layout_fits_every_width_without_horizontal_overflow(studio):
    async with studio() as s:
        page = await open_nested(s)
        await shot(s.page, 'nested-empty-1600.png')
        await s.srv._accept_prompt('Write the release notes for 0.3.0, one section per subsystem, with the numbers from the records.')
        s.srv.bus.publish(Event('turn_start', {}))
        s.srv.bus.publish(Event('thinking_delta', 'Four subsystems changed; one worker each, and one to check the numbers.'))
        s.srv.bus.publish(Event('text_delta', 'Splitting the notes by subsystem.'))
        for n, run in enumerate(('r-1', 'r-2', 'r-3', 'r-4', 'r-5'), 1):
            s.srv.bus.publish(_activity(run, 'a-worker-with-a-rather-long-name' if n == 2 else 'writer', 'status', status='running', text='Subagent started.'))
            s.srv.bus.publish(_activity(run, 'writer', 'request', status='awaiting_response', request_index=1, model='fixture-27b', context={'used': 2048 * n, 'window': 32768}))
            s.srv.bus.publish(_activity(run, 'writer', 'thinking_report', text='The record for this subsystem lists three changes and one open item. ' * 3))
            s.srv.bus.publish(_activity(run, 'writer', 'tool_use', status='requested', data={'id': f'{run}:1:0:t', 'name': 'read_file', 'input': {'path': f'docs/project/updates/record-{n}.md'}}))
            s.srv.bus.publish(_activity(run, 'writer', 'tool_result', status='returned', data={'id': f'{run}:1:0:t', 'name': 'read_file', 'content': '# Record\n\nThree changes, one open item.', 'is_error': False}))
            s.srv.bus.publish(_activity(run, 'writer', 'response', status='received', request_index=1, duration_ms=1500, usage={'prompt_tokens': 2048, 'completion_tokens': 45},
                                        text=f'Subsystem {n}: three changes shipped; one item stays open until the gate passes.'))
        s.srv.bus.publish(_activity('r-4', 'writer', 'status', status='failed', text='Subagent timed out; work is incomplete.'))
        s.srv.bus.publish(_activity('r-5', 'writer', 'status', status='completed', text='Subagent finished; its result was returned to the lead agent.'))
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(5)
        for width, height in [(1600, 1000), (1366, 768), (1100, 800), (900, 700), (390, 844)]:
            await s.page.set_viewport_size({'width': width, 'height': height})
            await expect(page.locator('.nd-win[data-run-id]').first).to_be_visible()
            assert await s.page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            assert await page.evaluate('e => e.scrollWidth <= e.clientWidth'), width
            # every lane box is reachable (a window inside the page's scroll box, a card inside the row's) and no
            # two lane boxes overlap, at every width (the gate found the phone layout overlapping and unscrollable)
            geometry = await page.evaluate("""p => {
              const box = e => { const b = e.getBoundingClientRect(); return {id: e.dataset.runId || 'empty', top: b.top, bottom: b.bottom, left: b.left, right: b.right}; };
              const r = p.getBoundingClientRect(), t = p.querySelector('.nd-track').getBoundingClientRect(), track = p.querySelector('.nd-track');
              return {page: {top: r.top, scrollTop: p.scrollTop, scrollHeight: p.scrollHeight, clientHeight: p.clientHeight},
                      track: {top: t.top, bottom: t.bottom, left: t.left, scrollLeft: track.scrollLeft, scrollWidth: track.scrollWidth},
                      wins: [...p.querySelectorAll('.nd-win')].map(box), cards: [...p.querySelectorAll('.nd-card')].map(box)}; }""")
            wins, cards, pg, tr = geometry['wins'], geometry['cards'], geometry['page'], geometry['track']
            assert len(wins) == 2 and len(cards) == 3, (width, geometry)
            for w in wins:
                top, bottom = w['top'] - pg['top'] + pg['scrollTop'], w['bottom'] - pg['top'] + pg['scrollTop']
                assert -1 <= top and bottom <= pg['scrollHeight'] + 1 and bottom - top > 40, (width, w, pg)
            for c in cards:
                assert tr['top'] - 1 <= c['top'] and c['bottom'] <= tr['bottom'] + 1, (width, c, tr)
                assert c['right'] - tr['left'] + tr['scrollLeft'] <= tr['scrollWidth'] + 1 and c['right'] - c['left'] > 40, (width, c, tr)
            boxes = wins + cards
            for i, a in enumerate(boxes):
                for b in boxes[i + 1:]:
                    meets = min(a['right'], b['right']) - max(a['left'], b['left']) > 1 and min(a['bottom'], b['bottom']) - max(a['top'], b['top']) > 1
                    assert not meets, (width, a, b)
            if width <= 900:
                assert pg['scrollHeight'] > pg['clientHeight'], (width, pg)     # the page scrolls to reach the lanes below
                await page.evaluate('p => p.scrollTo(0, p.scrollHeight)')
                assert await page.locator('.nd-track').evaluate('t => { const b = t.getBoundingClientRect(); return b.bottom <= innerHeight + 1; }'), width
                await page.evaluate('p => p.scrollTo(0, 0)')
            await shot(s.page, f'nested-five-{width}.png')
        assert s.errors == []


# --- (f) nothing from the mockup ships ----------------------------------------------------------------------------

def test_no_mockup_sample_content_or_foreign_fonts_ship():
    banned = ['Backend', 'Studio UI', 'Live memory switch', 'MiMo-V2.6 Flash', 'Qwen3.8', 'Frontier review loop',
              '.dream/wt/', '372k of 600k', 'fonts.googleapis', 'fonts.gstatic', 'cdn.', '-webkit-font-smoothing']
    files = sorted(p for p in STATIC.glob('nested*') if p.suffix in ('.js', '.css'))   # every Nested module, whoever adds one
    assert {'nested-customize.js', 'nested-drawer.js', 'nested-footer.css', 'nested-footer.js', 'nested-layout.js',
            'nested-plan.css', 'nested-plan.js', 'nested-workflows.js', 'nested.css', 'nested.js'} <= {f.name for f in files}, files
    for path in files:
        text = path.read_text()
        for needle in banned:
            assert needle not in text, (path.name, needle)
    css = (STATIC / 'nested.css').read_text()
    assert '#dream-nested-page{' in css.replace(' ', '') and '--nd-' in css      # tokens scoped under the page
    assert re.search(r'^\s*:root\s*\{', css, re.M) is None                        # never Dream's :root
