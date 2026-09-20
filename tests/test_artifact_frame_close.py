"""Closing the artifact panel must stop the preview rendering, not just hide it.
A three.js artifact left loaded held the display GPU at 60-90 % for hours with
nothing on screen (owner-reported 2026-09-20)."""
import pytest
from playwright.async_api import expect

from dream.core.backends.base import Event
from test_desktop_chat import chat  # noqa: F401

pytestmark = pytest.mark.asyncio

PAGE = """<!doctype html><html><body><canvas id=c></canvas><script>
let n = 0; function loop(){ n++; window.__frames = n; requestAnimationFrame(loop); }
loop();
</script></body></html>"""


async def test_closing_the_panel_tears_the_frame_down(chat):
    server, page, prompts, _ = chat
    server.bus.publish(Event("studio", {"op": "show", "path": "anim.html",
                                        "title": "anim.html", "content": PAGE}))
    await expect(page.locator("#artbody iframe")).to_have_count(1)
    await page.locator("#artclose").click()
    # The iframe is gone, so its render loop cannot still be running.
    await expect(page.locator("#artbody iframe")).to_have_count(0)
    assert await page.evaluate("() => document.getElementById('artbody').children.length") == 0
    assert prompts == []
