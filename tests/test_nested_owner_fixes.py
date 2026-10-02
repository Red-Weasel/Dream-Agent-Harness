"""The owner's screen findings of 2026-09-30 (screenshots 20:58-21:14), item 6 of the fix list.

(a) With the Understand panel open, the Nested composer's Send button was cut off at the panel's edge.
(b) A long prompt filled the orchestrator column as a 20 px bold heading. It reads as normal text now, cut after four
    lines, with a Show all / Show less button that keeps its state while the column re-renders.
"""
from __future__ import annotations

import pytest
from playwright.async_api import expect

from dream.core.backends.base import Event
from test_nested_view import open_nested, studio  # noqa: F401  (the fixture and the page helper)

LONG = " ".join(f"Step {i}: map the module, cite its functions and note what could break." for i in range(1, 40))


async def _send_is_fully_usable(page) -> dict:
    return await page.evaluate("""() => {
        const b = document.getElementById('nd-send'), r = b.getBoundingClientRect();
        const col = b.closest('.nd-orch').getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return {inColumn: r.left >= col.left - 1 && r.right <= col.right + 1, inViewport: r.right <= innerWidth + 1,
                onTop: hit === b || b.contains(hit), width: r.width};
    }""")


@pytest.mark.parametrize("size", [(2000, 1125), (1600, 1000), (1366, 768)])
async def test_send_stays_visible_and_clickable_with_the_understand_panel_open(studio, size):  # noqa: F811
    async with studio(width=size[0], height=size[1]) as s:
        # the owner's order: the Understand switch first (it opens the panel beside the chat), then Nested Dream
        await s.page.locator('#dream-nav-understand').click()
        await expect(s.page.locator('#dream-nav-understand')).to_have_attribute('aria-pressed', 'true')
        view = await open_nested(s)
        await view.locator('#nd-compose').fill(LONG)
        found = await _send_is_fully_usable(s.page)
        panel = await s.page.locator('#dream-understand').bounding_box()
        app = await view.locator('.nd-app').bounding_box()
        assert app['x'] + app['width'] <= panel['x'] + 1, (app, panel)       # the whole view sits beside the panel
        assert found['width'] > 20 and found['inColumn'] and found['inViewport'] and found['onTop'], found
        assert s.errors == []


async def test_a_long_goal_reads_as_normal_text_cut_after_four_lines_with_show_all(studio):  # noqa: F811
    async with studio() as s:
        page = await open_nested(s)
        await s.srv._accept_prompt(LONG)
        goal = page.locator('.nd-goal')
        await expect(goal).to_have_text(LONG)                                 # every word is still there
        look = await goal.evaluate("""e => { const c = getComputedStyle(e);
            return {weight: parseInt(c.fontWeight), size: parseFloat(c.fontSize), line: parseFloat(c.lineHeight),
                    height: e.getBoundingClientRect().height}; }""")
        assert look['weight'] <= 400 and look['size'] <= 14, look
        assert look['height'] <= 4 * look['line'] + 2, look                   # at most four lines on screen
        more = page.locator('.nd-goal-more')
        await expect(more).to_have_text('Show all')
        await expect(more).to_have_attribute('aria-expanded', 'false')
        await more.click()
        await expect(more).to_have_text('Show less')
        await expect(more).to_have_attribute('aria-expanded', 'true')
        assert await goal.evaluate("e => e.getBoundingClientRect().height") > 3 * look['height']
        s.srv.bus.publish(Event('turn_start', {}))                             # the column re-renders while it streams
        s.srv.bus.publish(Event('text_delta', 'Mapping now.'))
        await expect(page.locator('.nd-msg.nd-dream .nd-stream')).to_contain_text('Mapping now.')
        await expect(more).to_have_text('Show less')                          # the reader's choice survives
        await more.click()
        await expect(more).to_have_text('Show all')
        assert s.errors == []


async def test_a_short_goal_shows_whole_with_no_button(studio):  # noqa: F811
    async with studio() as s:
        page = await open_nested(s)
        await s.srv._accept_prompt('Plan the release.')
        await expect(page.locator('.nd-goal')).to_have_text('Plan the release.')
        await expect(page.locator('.nd-goal-more')).to_have_count(0)
        assert s.errors == []
