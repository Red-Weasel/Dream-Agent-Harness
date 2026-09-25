"""DREAM-111 (#55, #67): the Studio shows the model's real work, and a still says it is one.

Live, the owner thought the animation "froze": the Studio was showing a saved screenshot with
nothing saying so. Every still now carries "Still image: <workspace path>", and when a live page
had been showing, "Back to live page" restores it in one click. The last test drives the real
pane (the desktop's ?companion=1 page) and the real hidden frame in headless Chromium at the
owner's 2554x1338 and at a narrow 1000x760: a live page, a saved screenshot, a `see`, a run_bash
render, back to live, and "Open in browser" opening the page on its own origin.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from dream.core import execution
from dream.gui import preview as preview_mod
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.tools import context as tool_context, mirror, studio
from dream.tools.context import ToolContext, set_context, set_studio
from dream.tools.native import run_bash
from dream.tools.studio import save_screenshot, show_html
from dream.tools.vision import see

UI = Path(__file__).parent.parent / "dream" / "gui" / "static"
SHOTS = Path(os.environ.get("DREAM_STUDIO_SHOTS") or "/nonexistent")
LIVE = ("<!doctype html><title>live</title><h1 id='h'>Hello, live page</h1><canvas id='c' width='300' height='120'>"
        "</canvas><script type='module'>const g = document.getElementById('c').getContext('2d'); let t = 0;"
        "(function f(){ t++; g.fillStyle = `hsl(${t % 360} 70% 50%)`; g.fillRect(0, 0, 300, 120);"
        " requestAnimationFrame(f); })();</script>")


def test_the_pane_labels_every_still_and_offers_the_way_back():
    ui = (UI / "index.html").read_text()
    paint = ui[ui.index("function paintArtifact"):]
    image = paint[paint.index("if(a.kind === 'image'){"):paint.index("lastLive = artOpen;")]
    assert "'Still image: ' + a.path" in image and "'Back to live page · ' + live.title" in image
    assert "openArtifact(lastLive)" in image and "not the running page" in image
    assert ".stillbar{" in ui and ".stilltag{" in ui
    assert "Still image · ${a.path}" in (UI / "companion.js").read_text()


@pytest.mark.asyncio
async def test_the_real_pane_follows_renders_labels_stills_and_opens_the_page_in_a_browser(tmp_path, monkeypatch):
    from playwright.async_api import async_playwright, expect

    monkeypatch.delenv("DREAM_DESKTOP_SESSION_FILE", raising=False)
    monkeypatch.setattr(preview_mod, "display_render_node", lambda: None)
    monkeypatch.setattr(mirror, "SHELL_SHOW_MIN_S", 0.0)
    mirror._reset_shell_throttle()
    ws = tmp_path / "falcon9"
    ws.mkdir()
    (ws / "page.html").write_text(LIVE)
    srv = StudioServer(EventBus(), on_prompt=lambda p: None, session={"workspace": str(ws), "model": "fixture"})

    def funnel(ev):
        srv.retain_show(ev)
        srv.bus.publish(ev)

    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=ws, emit=funnel))
    preview_mod._PREVIEW = None
    studio._forget_reports()
    url = await srv.start()
    set_studio(srv)
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-gpu"])
            context = await browser.new_context(viewport={"width": 2554, "height": 1338})
            page = await context.new_page()
            page.set_default_timeout(8000)
            await page.goto(url + "&companion=1", wait_until="load")
            await page.wait_for_function("document.getElementById('stat').textContent !== 'connecting'")
            for _ in range(50):
                if srv.client_count:
                    break
                await asyncio.sleep(0.05)
            frame = page.frame_locator("#artbody iframe")

            # the live page
            res = await show_html.handler({"path": "page.html"})
            assert "loaded clean" in res["content"][0]["text"], res["content"][0]["text"]
            await expect(frame.locator("#h")).to_have_text("Hello, live page")
            link = page.locator("#artext")
            await expect(link).to_be_visible()
            href = await link.get_attribute("href")
            assert href == srv.pages.base_url + "page.html" and srv.token not in href
            assert await link.get_attribute("target") == "_blank"
            assert await link.get_attribute("rel") == "noopener noreferrer"
            if SHOTS.is_dir():
                await page.screenshot(path=str(SHOTS / "studio-live-page-2554x1338.png"))

            # a saved screenshot: a still, labelled, with the way back
            res = await save_screenshot.handler({"path": "page.html", "steps": [{}], "save_path": "shots/cap.png"})
            assert not res.get("is_error"), res
            tag = page.locator("#artbody .stilltag")
            await expect(tag).to_have_text("Still image: shots/cap.png")
            back = page.locator("#artlive")
            await expect(back).to_have_text("Back to live page · page.html")
            await page.wait_for_function("(i => i && i.complete && i.naturalWidth > 0)(document.querySelector('#artbody img'))")
            assert "not the running page" in await page.locator("#artnote").text_content()
            if SHOTS.is_dir():
                await page.screenshot(path=str(SHOTS / "studio-still-labelled-2554x1338.png"))

            # one click: the live page again
            await back.click()
            await expect(frame.locator("#h")).to_have_text("Hello, live page")
            await expect(page.locator("#artbody .stilltag")).to_have_count(0)

            # the model looks at its capture with `see`: the owner sees the same still
            res = await see.handler({"path": "shots/cap.png"})
            assert "the user's Studio pane shows cap.png too" in res["content"][-1]["text"]
            await expect(tag).to_have_text("Still image: shots/cap.png")

            # the model renders with its own script (the live session's way): the newest capture shows
            async def render(command, context_, *, timeout, max_output):
                for i in (1, 2, 3):
                    target = ws / "review" / "terrain-sea" / f"{i:02d}.png"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    Image.new("RGB", (640, 360), (30 * i, 90, 160)).save(target)
                    os.utime(target, ns=(time.time_ns(), time.time_ns() + i))   # written in this order
                return SimpleNamespace(output=b"captured 3", truncated=False, timed_out=False, returncode=0), True
            monkeypatch.setattr(execution, "execute_bash", render)
            monkeypatch.setattr(execution, "current_execution", lambda ws_: object())
            res = await run_bash.handler({"command": "python3 review/capture.py"})
            assert not res.get("is_error"), res
            await expect(tag).to_have_text("Still image: review/terrain-sea/03.png")
            await expect(back).to_have_text("Back to live page · page.html")
            await page.wait_for_function("(i => i && i.complete && i.naturalWidth === 640)(document.querySelector('#artbody img'))")
            if SHOTS.is_dir():
                await page.screenshot(path=str(SHOTS / "studio-shell-render-2554x1338.png"))

            # Open in browser: the live page on its own origin, no referrer, no opener
            await back.click()
            await expect(link).to_be_visible()
            async with context.expect_page() as opened:
                await link.click()
            tab = await opened.value
            await tab.wait_for_load_state("load")
            assert tab.url == srv.pages.base_url + "page.html"
            await expect(tab.locator("#h")).to_have_text("Hello, live page")
            assert await tab.evaluate("document.referrer") == ""
            assert await tab.evaluate("window.opener") is None
            if SHOTS.is_dir():
                await tab.screenshot(path=str(SHOTS / "studio-open-in-browser-tab.png"))
            await tab.close()

            # a narrow window: the label, the way back and the link stay on screen
            await page.set_viewport_size({"width": 1000, "height": 760})
            await save_screenshot.handler({"path": "page.html", "steps": [{}], "save_path": "shots/narrow.png"})
            await expect(tag).to_have_text("Still image: shots/narrow.png")
            for loc in (tag, back, link):
                box = await loc.bounding_box()
                assert box and box["x"] >= 0 and box["x"] + box["width"] <= 1000 + 1, (await loc.text_content(), box)
            assert await page.evaluate("document.documentElement.scrollWidth <= 1000")
            if SHOTS.is_dir():
                await page.screenshot(path=str(SHOTS / "studio-still-narrow-1000x760.png"))
            await browser.close()
    finally:
        mirror._reset_shell_throttle()
        set_studio(None)
        tool_context._CTX = None
        studio._forget_reports()
        await srv.stop()
        pv = preview_mod._PREVIEW
        if pv is not None:
            await pv.aclose()
        preview_mod._PREVIEW = None
