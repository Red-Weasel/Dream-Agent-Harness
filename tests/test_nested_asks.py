"""The P11/P12 builder's asks and that gate's short-height finding, each shown failing first: a queued send carries
`read_twice` while the chat's Read x2 toggle is pressed (never a steer); the workers area is the agents tabpanel the
tab strip labels; the orchestrator column keeps its composer, and a footer under it, inside short viewports above
the phone breakpoint. (The file guard's glob is in test_nested_view.py.)"""
from __future__ import annotations

import re

import pytest
from playwright.async_api import expect

from dream.core.backends.base import Event
from test_nested_view import open_nested, studio  # noqa: F401

pytestmark = pytest.mark.asyncio
PRESSED = "v => document.getElementById('read-twice').setAttribute('aria-pressed', v)"


async def test_a_queued_send_carries_read_twice_only_while_the_toggle_is_pressed(studio):
    async with studio() as s:
        page = await open_nested(s)
        bodies = []

        async def handle(route, request):
            bodies.append(request.post_data_json)
            await route.continue_()
        await s.page.route('**/api/prompt', handle)
        await expect(s.page.locator('#read-twice')).to_have_attribute('aria-pressed', 'false')   # the chat's own toggle, in its footer
        await s.page.evaluate(PRESSED, 'true')
        await page.locator('#nd-compose').fill('Build the orbit shot.')
        await page.locator('#nd-send').click()
        await expect(page.locator('#nd-compose')).to_have_value('')
        assert bodies[-1].get('read_twice') is True and bodies[-1].get('delivery', 'queue') == 'queue', bodies
        assert s.prompts[0].startswith('Build the orbit shot.') and 'Build the orbit shot.' in s.prompts[0][21:]   # the server read it twice
        await s.page.evaluate(PRESSED, 'false')
        await page.locator('#nd-compose').fill('And the docs.')
        await page.locator('#nd-send').click()
        await expect(page.locator('#nd-compose')).to_have_value('')
        assert 'read_twice' not in bodies[-1] and s.prompts[1] == 'And the docs.', (bodies, s.prompts)
        await s.page.evaluate(PRESSED, 'true')             # a steer never carries it
        s.srv.bus.publish(Event('turn_start', {}))
        await expect(page.locator('#nd-send')).to_have_text('Steer')
        await page.locator('#nd-compose').fill('Prefer the smaller change.')
        await page.locator('#nd-send').click()
        await expect(page.locator('#nd-compose')).to_have_value('')
        assert bodies[-1]['delivery'] == 'steer' and 'read_twice' not in bodies[-1], bodies
        assert [t for t, _, _ in s.steers] == ['Prefer the smaller change.'] and len(s.prompts) == 2 and s.errors == []


async def test_the_workers_area_is_the_agents_tabpanel(studio):
    async with studio() as s:
        page = await open_nested(s)
        main = page.locator('#nd-agents')
        await expect(main).to_have_class(re.compile(r'\bnd-main\b'))
        await expect(main).to_have_attribute('role', 'tabpanel')
        await expect(main).to_have_attribute('aria-labelledby', 'nd-tab-agents')
        assert s.errors == []


@pytest.mark.parametrize('w,h', [(901, 760), (1280, 720), (1920, 600)])
async def test_the_orchestrator_column_keeps_the_composer_and_a_footer_in_view_at_short_heights(studio, w, h):
    async with studio(width=w, height=h) as s:
        page = await open_nested(s)
        s.srv.bus.publish(Event('plan', {'title': 'Ship the Nested view with its tests and its docs'}))
        for i in range(6):
            s.srv.bus.publish(Event('text_delta', f"Paragraph {i} of the orchestrator's reply, long enough to need the scroll. " * 6))
        s.srv.bus.publish(Event('assistant_done', {}))
        await expect(page.locator('#nd-convo')).to_contain_text('Paragraph 5')
        await expect(page.locator('#nd-goal-wrap')).to_contain_text('Ship the Nested view')
        orch = page.locator('.nd-orch')
        await expect(page.locator('#nd-foot')).to_be_attached()                      # P12's footer (frontend B), under the composer
        b = await orch.evaluate("""e => { const b = s => e.querySelector(s).getBoundingClientRect().bottom;
            return {compose: b('#nd-compose'), send: b('#nd-send'), footer: b('#nd-foot'), inner: innerHeight}; }""")
        assert b['compose'] <= b['inner'] and b['send'] <= b['inner'] and b['footer'] <= b['inner'], b
        assert await page.locator('#nd-orch-scroll').evaluate('e => e.scrollHeight > e.clientHeight')   # the conversation is what scrolls
        assert s.errors == []
