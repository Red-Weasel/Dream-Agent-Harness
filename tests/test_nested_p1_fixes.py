"""The DREAM-188 gate's findings on fd02696, each shown failing first: the pips and Send keep their colours under the
page's button reset and the companion's hover paint; a reconnect replays the history without doubling anything; live
rows mirror the history whitelist (no agent-less row, run ids capped like agent_activity.py); one steering receipt id
per draft, kept across a failed POST; the steer target dies with the turn."""
from __future__ import annotations

import asyncio
import re

import pytest
from playwright.async_api import expect

from dream.core.backends.base import Event
from test_nested_drawer import ENDINGS, lanes
from test_nested_view import _activity, open_nested, studio  # noqa: F401

pytestmark = pytest.mark.asyncio
BG, FG = 'e => getComputedStyle(e).backgroundColor', 'e => getComputedStyle(e).color'
PEWTER, GREEN, ACCENT = (74, 74, 92), (127, 214, 174), (246, 160, 106)    # --nd-pewter, --nd-ok, --nd-accent (theme --ember)


def rgb(css):
    return tuple(int(n) for n in re.findall(r'\d+', css)[:3])


def contrast(a, b):
    def lum(c):
        v = [x / 255 for x in c]
        v = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in v]
        return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]
    hi, lo = sorted([lum(a), lum(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


async def colours(el, want):
    """The computed colours once the element is settled: a re-render can replace it between the locator resolving
    and the read (a detached element computes to ''), so the background is awaited with a retry first, and both
    colours are read in one evaluation."""
    await expect(el).to_have_css('background-color', 'rgb(%d, %d, %d)' % want)
    for _ in range(20):
        bg, fg = await el.evaluate('e => [getComputedStyle(e).backgroundColor, getComputedStyle(e).color]')
        if bg and fg:
            return rgb(bg), rgb(fg)
        await asyncio.sleep(0.1)
    raise AssertionError('the element never settled')


async def test_pips_and_send_keep_their_colours_under_the_resets_and_on_hover(studio):
    async with studio() as s:
        page = await open_nested(s)
        lanes(s, 2, {'r-1': 'completed'})
        done, working, send = page.locator('.nd-pips .nd-pip').nth(0), page.locator('.nd-pips .nd-pip').nth(1), page.locator('#nd-send')
        await expect(done).to_have_attribute('aria-label', '1 writer: Done')
        for el, want in ((done, PEWTER), (working, GREEN), (send, ACCENT)):
            bg, fg = await colours(el, want)
            assert bg == want and contrast(fg, bg) >= 4.5, (bg, fg)
            await el.hover()
            bg, fg = await colours(el, want)
            assert bg == want and contrast(fg, bg) >= 4.5, ('hover', bg, fg)
            await el.focus()
            bg, fg = await colours(el, want)
            assert bg == want and contrast(fg, bg) >= 4.5, ('focus', bg, fg)
        assert s.errors == []


async def test_a_reconnect_replays_the_history_without_doubling_anything(studio):
    async with studio() as s:
        page = await open_nested(s)
        await s.srv._accept_prompt('Index the archive.')
        lanes(s, 3, {'r-1': 'completed'})
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(3)
        await expect(page.locator('.nd-msg.nd-you')).to_have_count(1)
        win = page.locator('.nd-win[data-run-id="r-1"]')
        await expect(win.locator('.nd-stream')).to_have_text('Report on shelf 1.')
        await s.page.evaluate("window.__replays = 0; addEventListener('dream:event', e => { if (e.detail.m.kind === 'history') window.__replays++; })")
        await s.page.evaluate('socket.close()')                 # the socket drops; the page rejoins the session and replays its history
        await expect(s.page.locator('#stat')).to_have_text('Ready')
        await s.page.wait_for_function('window.__replays >= 1')
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(3)
        await expect(page.locator('.nd-msg.nd-you')).to_have_count(1)
        await expect(win.locator('.nd-stream')).to_have_text('Report on shelf 1.')
        await expect(win.locator('.nd-tool')).to_have_count(1)
        await expect(win.locator('.nd-think summary')).to_have_count(1)
        await expect(page.locator('.nd-pips .nd-pip').first).to_have_attribute('aria-label', '1 writer: Done')
        await page.locator('.nd-pips .nd-pip').first.click()
        await expect(page.locator('#nd-detail .nd-dbody .nd-entry')).to_have_count(7)
        assert s.errors == []


async def test_live_rows_mirror_the_history_whitelist(studio):
    async with studio() as s:
        page = await open_nested(s)
        s.srv.bus.publish(Event('agent_activity', {'run_id': 'r-nameless', 'kind': 'status', 'status': 'running', 'text': 'Subagent started.'}))
        long_id = 'r-' + 'x' * 150
        s.srv.bus.publish(_activity(long_id, 'writer', 'status', status='running', text='Subagent started.'))
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(1)          # the agent-less row makes no lane
        shown = await page.locator('.nd-win[data-run-id]').first.get_attribute('data-run-id')
        assert shown == long_id[:100] + '\n[Display truncated]'                 # capped as agent_activity.py caps it
        s.srv.bus.publish(_activity(long_id, 'writer', 'status', status='completed', text=ENDINGS['completed']))
        await expect(page.locator('.nd-pips .nd-pip').first).to_have_attribute('aria-label', '1 writer: Done')
        await s.page.reload()
        await expect(s.page.locator('#stat')).to_have_text('Ready')
        page = await open_nested(s)
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(1)
        assert await page.locator('.nd-win[data-run-id]').first.get_attribute('data-run-id') == shown
        await expect(page.locator('.nd-pips .nd-pip').first).to_have_attribute('aria-label', '1 writer: Done')
        assert s.errors == []


async def test_one_steering_id_per_draft_across_a_failed_post_and_the_target_dies_with_the_turn(studio):
    async with studio() as s:
        page = await open_nested(s)
        bodies, failures = [], [1]

        async def handle(route, request):
            bodies.append(request.post_data_json)
            if failures[0]:
                failures[0] -= 1
                await route.fulfill(status=503, json={'error': 'Engine busy'})
                return
            await route.continue_()
        await s.page.route('**/api/prompt', handle)
        s.srv.bus.publish(Event('steering_ready', {'session_id': 'nested-test', 'turn': 3}))
        s.srv.bus.publish(Event('turn_start', {}))
        await expect(page.locator('#nd-send')).to_have_text('Steer')
        await page.locator('#nd-compose').fill('Prefer the smaller change.')
        await page.locator('#nd-send').click()
        await expect(page.locator('#nd-status')).to_contain_text('Engine busy')
        await expect(page.locator('#nd-compose')).to_have_value('Prefer the smaller change.')   # the draft stays
        await page.locator('#nd-send').click()                                                    # the retry carries the same receipt id
        await expect(page.locator('#nd-compose')).to_have_value('')
        assert len(bodies) == 2 and bodies[0]['steering_id'] == bodies[1]['steering_id'], bodies
        assert re.fullmatch(r'[a-f0-9]{32}', bodies[0]['steering_id'])
        assert bodies[1]['steering_target'] == {'session_id': 'nested-test', 'turn': 3}
        assert [t for t, _, _ in s.steers] == ['Prefer the smaller change.'] and s.steers[0][2] == {'session_id': 'nested-test', 'turn': 3}
        await page.locator('#nd-compose').fill('And test it.')                                    # a new draft: a new id
        await page.locator('#nd-send').click()
        await expect(page.locator('#nd-compose')).to_have_value('')
        assert bodies[2]['steering_id'] != bodies[0]['steering_id']
        s.srv.bus.publish(Event('turn_end', {}))                                                  # the target dies with the turn
        s.srv.bus.publish(Event('turn_start', {}))
        await page.locator('#nd-compose').fill('Third.')
        await page.locator('#nd-send').click()
        await expect(page.locator('#nd-compose')).to_have_value('')
        assert bodies[3]['delivery'] == 'steer' and bodies[3]['steering_target'] is None and s.steers[-1][2] is None
        assert s.errors == []
