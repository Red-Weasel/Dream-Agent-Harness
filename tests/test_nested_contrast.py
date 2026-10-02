"""Nested Dream phase 13 (DREAM-200), the contrast check: every text the Nested view paints, in every module of this
tree (nested.js, nested-drawer.js, nested-layout.js, nested-plan.js, nested-footer.js, nested-workflows.js), against
the colour actually behind it (its own paint composited over every ancestor's), from computed styles, at WCAG AA:
4.5:1 for text, 3:1 for large text (24 px, or 18.66 px bold) and for the icon buttons' glyphs. The states are rendered
with scripted events on a real StudioServer (a full session with working, done, failed, stopped, queued and waiting
workers, tool rows, thinking, streamed text, a plan, stats, a confirmed mode, the drawer's list and detail, the
Workflows panel, the composer's receipts), then hovered and focused. The two states no event sets yet (Verifying,
Needs you) are checked from the stylesheet's tokens. One theme exists in this tree (theme.css's dark palette); a light
theme is not checked because there is none. No model, no engine, no Dream process."""
from __future__ import annotations

import contextlib
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright, expect

from dream.core import policy
from dream.core.backends.base import Event
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer

from test_nested_footer import PERF, STATS
from test_nested_plan import PHASES, plan
from test_nested_view import _activity, open_nested

STATIC = Path(__file__).resolve().parents[1] / 'dream/gui/static'
QUEUED = 'Waiting for an engine lane (2 of 2 busy)'
WAITING = 'Waiting for another Dream request on this local engine to finish…'

# Every element that paints text (or an icon button's glyph) under `root`, with the colour behind it: the element's
# own background composited over its ancestors' until one is opaque (over white if none is: nothing here relies on
# that). A text colour with alpha is composited the same way. Elements behind an <img> that covers them are marked
# `image` (the hero's move button): a computed style cannot say what the picture shows there.
SCAN = r'''(root) => {
  const parse = s => { const m = /rgba?\(([^)]+)\)/.exec(s || ''); if(!m) return null; const p = m[1].split(',').map(Number); return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1}; };
  const over = (top, bot) => { const a = top.a + bot.a * (1 - top.a); const f = k => a ? (top[k] * top.a + bot[k] * bot.a * (1 - top.a)) / a : 0; return {r: f('r'), g: f('g'), b: f('b'), a}; };
  const lum = c => { const v = [c.r, c.g, c.b].map(x => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); }); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
  const ratio = (a, b) => { const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x); return (hi + 0.05) / (lo + 0.05); };
  const covers = (img, el) => { const a = img.getBoundingClientRect(), b = el.getBoundingClientRect(); return a.left <= b.left && a.top <= b.top && a.right >= b.right && a.bottom >= b.bottom; };
  const background = el => {
    let acc = {r: 0, g: 0, b: 0, a: 0}, image = false;
    for(let n = el; n; n = n.parentElement){
      const cs = getComputedStyle(n);
      if(cs.backgroundImage && cs.backgroundImage !== 'none') image = true;
      for(const img of n.querySelectorAll(':scope > img')) if(covers(img, el)) image = true;
      const c = parse(cs.backgroundColor);
      if(c && c.a > 0){ acc = over(acc, c); if(acc.a >= 0.999) break; }
    }
    if(acc.a < 0.999) acc = over(acc, {r: 255, g: 255, b: 255, a: 1});
    return {colour: acc, image};
  };
  const visible = el => {
    if(!el.getClientRects().length) return false;
    for(let n = el; n; n = n.parentElement){ const cs = getComputedStyle(n); if(cs.visibility === 'hidden' || cs.display === 'none' || parseFloat(cs.opacity) === 0) return false; }
    return true;
  };
  const name = el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.classList.length ? '.' + [...el.classList].join('.') : '');
  const out = [];
  for(const el of [root, ...root.querySelectorAll('*')]){
    if(el.matches('script, style, img, textarea, svg, svg *, .nd-sr, .nd-sr *')) continue;
    if(el.matches('button:disabled, button:disabled *')) continue;
    const glyph = el.matches('.nd-iconbtn');
    const text = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim();
    if(!text && !glyph) continue;
    if(!visible(el)) continue;
    const cs = getComputedStyle(el), fg0 = parse(cs.color);
    if(!fg0) continue;
    const {colour: bg, image} = background(el);
    const fg = fg0.a < 1 ? over(fg0, bg) : fg0;
    const size = parseFloat(cs.fontSize), weight = parseInt(cs.fontWeight) || 400;
    const large = size >= 24 || (size >= 18.66 && weight >= 700);
    out.push({el: name(el), text: (text || '<glyph>').slice(0, 40), fg: [fg.r, fg.g, fg.b].map(Math.round), bg: [bg.r, bg.g, bg.b].map(Math.round),
              ratio: Math.round(ratio(fg, bg) * 100) / 100, need: glyph || large ? 3 : 4.5, size, weight, image,
              hover: el.matches(':hover'), focus: el.matches(':focus-visible')});
  }
  return out;
}'''
# The typed text and the placeholder of a text box are not text nodes: read them apart.
BOX = r'''(t) => {
  const parse = s => { const m = /rgba?\(([^)]+)\)/.exec(s || ''); const p = m[1].split(',').map(Number); return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1}; };
  const lum = c => { const v = [c.r, c.g, c.b].map(x => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); }); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
  const ratio = (a, b) => { const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x); return (hi + 0.05) / (lo + 0.05); };
  let n = t, bg = null; while(n && !bg){ const c = parse(getComputedStyle(n).backgroundColor); if(c.a > 0.999) bg = c; n = n.parentElement; }
  return {text: ratio(parse(getComputedStyle(t).color), bg), placeholder: ratio(parse(getComputedStyle(t, '::placeholder').color), bg)};
}'''


def _hex(h):
    return {'r': int(h[1:3], 16), 'g': int(h[3:5], 16), 'b': int(h[5:7], 16)}


def _ratio(a, b):
    def lum(c):
        v = [c[k] / 255 for k in ('r', 'g', 'b')]
        v = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in v]
        return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]
    hi, lo = sorted([lum(a), lum(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def failures(rows):
    return [r for r in rows if not r['image'] and r['ratio'] < r['need']]


@pytest.fixture
def studio(monkeypatch):
    """One Studio server and one Chromium page, with the app's two mode actions answered as tui/app.py answers them,
    the engine's lanes in hello, and the provider label of the session."""
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)

    @contextlib.asynccontextmanager
    async def open_studio(provider='MachX', lanes=None, width=1600, height=1000):
        prompts, state = [], {'mode': 'ask'}

        async def control(payload):
            if payload.get('action') == 'permission_mode':
                state['mode'] = policy.next_mode(state['mode'])
            return {'mode': state['mode'], 'modes': list(policy.MODES), 'labels': policy.MODE_LABEL}

        async def on_steer(text, identifier, target):
            return {'status': 'pending'}

        srv = StudioServer(EventBus(), on_prompt=prompts.append, on_steer=on_steer, on_control=control,
                           session={'provider': provider, 'model': 'fixture', 'session_id': 'nested-contrast', 'lanes': lanes})
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
                yield SimpleNamespace(srv=srv, page=page, prompts=prompts, errors=errors)
                await browser.close()
        finally:
            await srv.stop()
    return open_studio


async def scan(locator):
    rows = await locator.evaluate(SCAN)
    return rows, failures(rows)


async def check_states(page, locator, *, hover=True, focus=True):
    """The element hovered, then focused from the keyboard's modality: no pair inside it may drop below its need,
    and the state must really be on (otherwise the check would only repeat the idle one)."""
    bad = []
    if hover:
        await locator.hover()
        assert await locator.evaluate('e => e.matches(":hover")')
        rows = await locator.evaluate(SCAN)
        assert rows, 'nothing painted inside the hovered control'
        bad += [dict(r, state='hover') for r in failures(rows)]
    if focus:
        await locator.focus()
        assert await locator.evaluate('e => e.matches(":focus-visible")')
        rows = await locator.evaluate(SCAN)
        assert rows, 'nothing painted inside the focused control'
        bad += [dict(r, state='focus') for r in failures(rows)]
    await page.mouse.move(0, 0)
    return bad


def full_session(s):
    """Every state the view renders from events today, in one session."""
    s.srv.bus.publish(plan(PHASES))
    s.srv.bus.publish(Event('turn_start', {}))
    s.srv.bus.publish(Event('thinking_delta', 'Four subsystems changed; one worker each.'))
    s.srv.bus.publish(Event('tool_use', {'id': 'o1', 'name': 'read_file', 'input': {'path': 'docs/project/CURRENT.md'}}))
    s.srv.bus.publish(Event('tool_result', {'id': 'o1', 'content': 'the current state', 'is_error': False}))
    s.srv.bus.publish(Event('tool_use', {'id': 'o2', 'name': 'run_bash', 'input': {'command': 'false'}}))
    s.srv.bus.publish(Event('tool_result', {'id': 'o2', 'content': 'exit 1', 'is_error': True}))
    s.srv.bus.publish(Event('text_delta', 'Splitting the notes by subsystem; one worker each.'))
    for n in range(1, 8):
        s.srv.bus.publish(_activity(f'r-{n}', 'writer', 'status', status='queued' if n in (4, 5) else 'running',
                                    text=QUEUED if n in (4, 5) else 'Subagent started.'))
    for n in (1, 2, 3):
        run = f'r-{n}'
        s.srv.bus.publish(_activity(run, 'writer', 'request', status='awaiting_response', request_index=1, model='fixture-27b', context={'used': 2048 * n, 'window': 32768}))
        s.srv.bus.publish(_activity(run, 'writer', 'thinking_report', text='The record for this subsystem lists three changes and one open item.'))
        s.srv.bus.publish(_activity(run, 'writer', 'tool_use', status='requested', data={'id': f'{run}:1:0:t', 'name': 'read_file', 'input': {'path': f'docs/record-{n}.md'}}))
        s.srv.bus.publish(_activity(run, 'writer', 'tool_result', status='returned', data={'id': f'{run}:1:0:t', 'name': 'read_file', 'content': '# Record\n\nThree changes, one open item.', 'is_error': n == 2}))
        s.srv.bus.publish(_activity(run, 'writer', 'tool_use', status='requested', data={'id': f'{run}:1:1:t', 'name': 'run_bash', 'input': {'command': 'pytest -q'}}))
        s.srv.bus.publish(_activity(run, 'writer', 'response', status='received', request_index=1, duration_ms=1500, usage={'prompt_tokens': 2048, 'completion_tokens': 45},
                                    text=f'Subsystem {n}: three changes shipped; one item stays open until the gate passes.'))
    s.srv.bus.publish(_activity('r-1', 'writer', 'request', status='awaiting_response', request_index=2))
    s.srv.bus.publish(_activity('r-1', 'writer', 'status', status='waiting', text=WAITING))
    s.srv.bus.publish(_activity('r-2', 'writer', 'status', status='completed', text='Subagent finished; its result was returned to the lead agent.'))
    s.srv.bus.publish(_activity('r-3', 'writer', 'status', status='failed', text='Subagent timed out; work is incomplete.'))
    s.srv.bus.publish(_activity('r-4', 'writer', 'status', status='interrupted', text='Stopped while waiting for an engine lane; nothing was sent.'))
    s.srv.bus.publish(_activity('r-6', 'writer', 'text_delta', text='Streaming the sixth subsystem, word by word.'))
    s.srv.bus.publish(_activity('r-7', 'writer', 'thinking_delta', text='Reading the seventh record before writing.'))
    s.srv.bus.publish(Event('stats', dict(STATS)))


# --- (a) the whole view in a full session, idle: every pair -----------------------------------------------------------

async def test_every_text_pair_of_a_full_session_meets_aa(studio):
    async with studio(lanes={'served': 2, 'busy': 2, 'queued': 1}) as s:
        page = await open_nested(s)
        await s.srv._accept_prompt('Write the release notes for 0.3.0, one section per subsystem.')
        full_session(s)
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(7)
        await expect(page.locator('#nd-lanes')).to_have_text('lanes: 2 of 2 busy · 1 queued')
        await expect(page.locator('#nd-perf')).to_have_text(PERF)
        await expect(page.locator('#nd-mode')).to_have_text(policy.MODE_LABEL['ask'])
        await expect(page.locator('#nd-send')).to_have_text('Steer')
        await expect(page.locator('.nd-card[data-run-id="r-7"] .nd-th')).to_be_visible()
        # the composer's receipt line, in its error colour and in its plain one
        await s.page.route('**/api/prompt', lambda r: r.fulfill(status=503, json={'error': 'Model is unavailable'}))
        await page.locator('#nd-compose').fill('Prefer the smaller change.')
        await page.locator('#nd-send').click()
        await expect(page.locator('#nd-status')).to_have_attribute('data-error', 'true')
        rows, bad = await scan(page)
        assert not bad, bad
        assert len(rows) >= 80, len(rows)                       # the scan really saw the page
        await s.page.unroute('**/api/prompt')
        await page.locator('#nd-send').click()
        await expect(page.locator('#nd-status')).to_contain_text('next model request')
        # the open Thinking blocks, the plan's whole list, the workers' text boxes
        for d in await page.locator('details.nd-think').all():
            await d.locator('summary').click()
        await page.locator('#nd-plan-toggle').click()
        await expect(page.locator('#nd-plan-all')).to_be_visible()
        rows, bad = await scan(page)
        assert not bad, bad
        box = await page.locator('#nd-compose').evaluate(BOX)
        assert box['text'] >= 4.5 and box['placeholder'] >= 4.5, box
        # an empty window and the belt's note; a turn that ended (Send)
        await page.locator('.nd-win[data-run-id="r-2"] [data-unpin]').click()
        await expect(page.locator('.nd-win-empty')).to_be_visible()
        for run in ('r-1', 'r-5', 'r-6', 'r-7'):                # they finish first: the turn's end settles only what is live (0428a6a)
            s.srv.bus.publish(_activity(run, 'writer', 'status', status='completed', text='Subagent finished; its result was returned to the lead agent.'))
        s.srv.bus.publish(Event('result', {}))
        s.srv.bus.publish(Event('turn_end', {}))
        await expect(page.locator('#nd-send')).to_have_text('Send')
        rows, bad = await scan(page)
        assert not bad, bad
        # the drawer: the list with its filters, then a worker's detail with its transcript
        await page.locator('#nd-drawer-btn').click()
        drawer = page.locator('#nd-drawer')
        await expect(drawer).to_be_visible()
        await page.locator('#nd-filters [data-f="failed"]').click()
        await expect(page.locator('#nd-dlist .nd-item')).to_have_count(2)
        rows, bad = await scan(drawer)
        assert not bad, bad
        assert any('nd-l2' in r['el'] for r in rows) and any('nd-filters' in r['el'] or r['text'].startswith('Failed or stopped') for r in rows), rows
        await page.locator('#nd-filters [data-f="all"]').click()
        await page.locator('#nd-dlist .nd-item[data-run-id="r-2"]').click()
        await expect(page.locator('#nd-detail')).to_be_visible()
        rows, bad = await scan(drawer)
        assert not bad, bad
        assert any('nd-badge' in r['el'] for r in rows) and any(r['text'].startswith('Model') or r['el'] == 'b' for r in rows), rows
        await s.page.keyboard.press('Escape')
        await s.page.keyboard.press('Escape')
        await expect(drawer).to_be_hidden()
        # the Workflows panel
        await page.locator('#nd-tab-workflows').click()
        await expect(page.locator('#nd-workflows')).to_be_visible()
        rows, bad = await scan(page.locator('#nd-workflows'))
        assert not bad and len(rows) >= 4, (bad, rows)
        rows, bad = await scan(page)
        assert not bad, bad
        assert s.errors == []


# --- (b) hover and focus: every control, in every state it can be in today ------------------------------------------

async def test_hover_and_focus_keep_the_contrast_of_every_control(studio):
    async with studio(lanes={'served': 2, 'busy': 2, 'queued': 1}) as s:
        page = await open_nested(s)
        await s.srv._accept_prompt('Write the release notes for 0.3.0.')
        full_session(s)                                          # the turn runs on: its end would settle the Working and Queued pips (0428a6a)
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(7)
        await expect(page.locator('#nd-mode')).to_have_text(policy.MODE_LABEL['ask'])
        await s.page.keyboard.press('Tab')                      # the keyboard's modality: focus shows from here on
        bad = []
        pips = page.locator('.nd-pips .nd-pip')
        states = set()
        for i in range(7):
            states.add(await pips.nth(i).get_attribute('aria-label'))
            bad += await check_states(s.page, pips.nth(i))
        assert {a.split(': ')[1] for a in states} == {'Working', 'Done', 'Failed', 'Stopped', 'Queued'}, states
        for sel in ['#nd-send', '#nd-drawer-btn', '#nd-auto', '#nd-reset', '#nd-prev', '#nd-next', '#nd-side',
                    '.nd-attn button.nd-fail', '.nd-win[data-run-id] [data-unpin]', '.nd-card[data-run-id] [data-pin]',
                    '.nd-card[data-run-id="r-3"]', '#nd-tab-agents', '#nd-tab-workflows', '#nd-mode', '#nd-read-twice',
                    '#nd-plan-toggle', 'details.nd-think summary']:
            bad += [dict(r, sel=sel) for r in await check_states(s.page, page.locator(sel).first)]
        await page.locator('#nd-plan-toggle').focus()            # the pressed toggle, opened from the keyboard, hovered and focused
        await s.page.keyboard.press('Enter')
        bad += [dict(r, sel='#nd-plan-toggle[aria-expanded=true]') for r in await check_states(s.page, page.locator('#nd-plan-toggle'))]
        await page.locator('#nd-tab-agents').focus()
        await s.page.keyboard.press('End')
        bad += [dict(r, sel='tab selected') for r in await check_states(s.page, page.locator('#nd-tab-workflows'))]
        bad += [dict(r, sel='#nd-wf-create') for r in await check_states(s.page, page.locator('#nd-wf-create'))]
        await s.page.keyboard.press('Home')
        # the drawer: the pressed All agents button, filters pressed and not, a row, Back, the Pin pill
        await page.locator('#nd-drawer-btn').focus()
        await s.page.keyboard.press('Enter')
        await expect(page.locator('#nd-drawer')).to_be_visible()
        bad += [dict(r, sel='#nd-drawer-btn[aria-expanded=true]') for r in await check_states(s.page, page.locator('#nd-drawer-btn'))]
        bad += [dict(r, sel='filter all (pressed)') for r in await check_states(s.page, page.locator('#nd-filters [data-f="all"]'))]
        bad += [dict(r, sel='filter failed') for r in await check_states(s.page, page.locator('#nd-filters [data-f="failed"]'))]
        bad += [dict(r, sel='drawer row') for r in await check_states(s.page, page.locator('#nd-dlist .nd-item[data-run-id="r-3"]'))]
        bad += [dict(r, sel='drawer close') for r in await check_states(s.page, page.locator('#nd-drawer-close'))]
        await page.locator('#nd-dlist .nd-item[data-run-id="r-2"]').focus()
        await s.page.keyboard.press('Enter')
        await expect(page.locator('#nd-detail')).to_be_visible()
        bad += [dict(r, sel='back') for r in await check_states(s.page, page.locator('#nd-back'))]
        bad += [dict(r, sel='pin pill') for r in await check_states(s.page, page.locator('#nd-pin-btn'))]
        bad += [dict(r, sel='detail close') for r in await check_states(s.page, page.locator('#nd-detail-close'))]
        assert not bad, bad
        assert s.errors == []


# --- (c) the empty states, on an engine and on a provider that reports no workers -----------------------------------

@pytest.mark.parametrize('provider,want', [('MachX', 'No workers yet'), ('Claude · Anthropic', "Claude's sub-agents appear here"),
                                           ('ChatGPT · Codex', 'This provider does not report worker')])
async def test_empty_states_meet_aa(studio, provider, want):
    async with studio(provider=provider) as s:
        page = await open_nested(s)
        await expect(page.locator('.nd-empty')).to_be_visible()
        await expect(page.locator('.nd-goal-empty')).to_be_visible()
        rows, bad = await scan(page)
        assert not bad, bad
        texts = {r['text'] for r in rows}
        assert any(x.startswith(want) for x in texts), texts
        await page.locator('#nd-drawer-btn').click()
        await expect(page.locator('#nd-dlist .nd-dempty')).to_be_visible()
        rows, bad = await scan(page.locator('#nd-drawer'))
        assert not bad, bad
        assert any('nd-dempty' in r['el'] for r in rows), rows
        assert s.errors == []


# --- (d) the states no event renders yet, from the stylesheet's tokens --------------------------------------------

def test_pip_tokens_for_every_state_meet_aa_including_the_ones_no_event_sets_yet():
    css = (STATIC / 'nested.css').read_text()
    block = re.search(r'#dream-nested-page\{(.*?)\n\}', css, re.S).group(1)
    nd = dict(re.findall(r'--(nd-[\w-]+):(#[0-9a-f]{6})', block))
    theme = re.search(r'html\.dream-design\{(--bg0:[^}]*)\}', (STATIC / 'theme.css').read_text()).group(1)
    t = dict(re.findall(r'--([\w-]+):(#[0-9a-f]{6})', theme))
    pairs = {'working': (nd['nd-on-status'], nd['nd-ok']), 'verifying': (nd['nd-on-status'], nd['nd-verify']),
             'needs': (t['ember-ink'], t['ember']), 'failed and stopped': (nd['nd-on-status'], nd['nd-fail']),
             'queued and done': (nd['nd-on-pewter'], nd['nd-pewter'])}
    for state, (fg, bg) in pairs.items():
        assert _ratio(_hex(fg), _hex(bg)) >= 4.5, (state, fg, bg, round(_ratio(_hex(fg), _hex(bg)), 2))
    # the accent words (needs you, failed, thinking) on every surface they can sit on
    for text in (t['ember'], nd['nd-fail'], nd['nd-rose']):
        for surface in (t['bg0'], t['bg2'], t['panel'], t['raised'], t['plum']):
            assert _ratio(_hex(text), _hex(surface)) >= 4.5, (text, surface, round(_ratio(_hex(text), _hex(surface)), 2))
