"""The shipped desktop companion, exercised in Chromium at a 480px pane width.

Only an isolated StudioServer and synthetic events are used; no engines,
embedding models, or GPU models are instantiated.
"""
from __future__ import annotations

import asyncio
import pytest
from playwright.async_api import async_playwright, expect

from dream.core.backends.base import Event
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer


PAGE = """<!doctype html><html><head><style>
body{margin:0;padding:28px;font:15px system-ui;background:#f4f1ea;color:#23382d}
h1{font-size:32px;font-weight:500;margin:12px 0}p{line-height:1.6}
button{padding:12px 20px;border:0;border-radius:6px;background:#315642;color:white}
input{display:block;padding:10px;margin:18px 0;border:1px solid #9ca99e}
</style></head><body><small>Field notes</small><h1>A little room to grow.</h1>
<p>A page to explore, test, and refine with Dream.</p>
<input id="draft" aria-label="Draft" value="original">
<section data-screen-label="Garden"><button id="cta" onclick="sendPrompt('Use this direction')">Try this direction</button></section>
</body></html>"""


@pytest.fixture
async def studio(tmp_path, monkeypatch):
    monkeypatch.delenv("DREAM_DESKTOP_SESSION_FILE", raising=False)
    prompts = []
    srv = StudioServer(EventBus(), on_prompt=prompts.append,
                       session={"workspace": str(tmp_path), "provider": "test", "model": "fixture"})
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-gpu"])
            page = await browser.new_page(viewport={"width": 480, "height": 800}, reduced_motion="reduce")
            page.set_default_timeout(5000)
            errors = []
            page.on("pageerror", lambda err: errors.append(str(err)))
            yield srv, page, url + "&companion=1", prompts, errors
            await browser.close()
    finally:
        await srv.stop()


async def ready(page, url):
    await page.goto(url)
    await expect(page.locator("#stat")).to_have_text("Ready")


async def show(srv, page, content=PAGE, path="garden.html"):
    srv.bus.publish(Event("studio", {"op": "show", "path": path, "content": content}))
    await expect(page.locator("#artname")).to_have_text(path.rsplit("/", 1)[-1])
    await expect(page.locator("#artbody iframe")).to_be_visible()
    await expect(page.locator("#studio-loading")).to_be_hidden()


async def no_overflow(page):
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    width = page.viewport_size["width"]
    for selector in (".studio-nav", ".artbar", ".studio-foot"):
        box = await page.locator(selector).bounding_box()
        if box:
            assert box["x"] >= 0 and box["x"] + box["width"] <= width, (selector, box)


async def test_chat_is_primary_and_preview_and_external_mode_are_preserved(studio, tmp_path):
    srv, page, url, _, errors = studio
    await ready(page, url)
    await expect(page.get_by_role("heading", name="What would you like to work on?")).to_be_visible()
    await expect(page.locator("#input")).to_be_visible()
    await expect(page.locator("#rail")).to_be_hidden()
    await page.screenshot(path=str(tmp_path / "companion-empty.png"))
    for kind, data in [("text_delta", "Terminal conversation"), ("thinking_delta", "Internal reasoning"),
                       ("assistant_done", None), ("system", "Terminal status"),
                       ("tool_use", {"id": "1", "name": "read_file", "input": {"path": "a"}}),
                       ("tool_result", {"id": "1", "content": "file content"})]:
        srv.bus.publish(Event(kind, data))
    await show(srv, page)
    assert await page.locator("#stream .msg, #stream .tool, #stream .think, #stream .sys").count() == 4
    box = await page.locator("#artbody iframe").bounding_box()
    assert box["width"] >= 450 and box["height"] >= 380, box
    await no_overflow(page)
    await page.screenshot(path=str(tmp_path / "companion-preview.png"))
    await page.get_by_role("button", name="Code", exact=True).click()
    await expect(page.locator("#artbody pre")).to_contain_text("Field notes")
    await expect(page.locator("#artpoint")).to_be_disabled()
    await page.locator("#artprev").click()
    await expect(page.frame_locator("#artbody iframe").locator("#cta")).to_be_visible()
    await show(srv, page, PAGE.replace("grow.", "bloom."))
    await expect(page.locator("#artver option")).to_have_count(2)
    await page.locator("#artver").select_option("0")
    await expect(page.frame_locator("#artbody iframe").locator("h1")).to_have_text("A little room to grow.")
    await page.get_by_role("button", name="Close preview", exact=True).click()
    await expect(page.locator("#studio-empty")).to_be_visible()
    await expect(page.locator("#artbody iframe")).to_have_count(0)
    await page.get_by_label("Choose an artifact").select_option("file:garden.html")
    await expect(page.frame_locator("#artbody iframe").locator("h1")).to_have_text("A little room to bloom.")
    await page.set_viewport_size({"width": 800, "height": 800})
    # Expanded Studio dedicates the workspace to the existing frame; no reload.
    await page.locator("#dream-output-expand").click()
    await no_overflow(page)
    box = await page.locator("#artbody iframe").bounding_box()
    assert box["width"] >= 770 and box["height"] >= 430, box
    await page.screenshot(path=str(tmp_path / "companion-preview-800.png"))
    await page.locator("#artcode").click()
    await show(srv, page, PAGE, "second.html")
    await expect(page.frame_locator("#artbody iframe").locator("#cta")).to_be_visible()
    await page.set_viewport_size({"width": 1600, "height": 900})
    await page.goto(url.replace("&companion=1", ""))
    await expect(page.locator("#stat")).to_have_text("live")
    await expect(page.locator("#input")).to_be_visible()
    srv.bus.publish(Event("text_delta", "External conversation stays visible"))
    await expect(page.locator("#stream .msg").last).to_contain_text("External conversation stays visible")
    assert errors == []


async def test_retained_first_show_waits_for_companion_initialization(studio):
    srv, page, url, _, errors = studio
    # The backend's connect contract: hello, then the retained explicit show.
    srv.retain_show(Event("studio", {"op": "show", "path": "garden.html", "content": PAGE}))

    async def delayed_script(route):
        await asyncio.sleep(0.15)
        await route.continue_()

    await page.route("**/assets/companion.js", delayed_script)
    await ready(page, url)
    await expect(page.frame_locator("#artbody iframe").locator("h1")).to_have_text("A little room to grow.")
    assert await srv.ask_frame("eval", {"code": "document.querySelector('h1').textContent"}) == "A little room to grow."
    await page.frame_locator("#artbody iframe").locator("#draft").fill("my live edit")
    snapshot = await srv.ask_frame("snapshot")
    assert 'value="my live edit"' in snapshot
    assert errors == []


async def test_questions_downloads_cards_and_widget_composer_remain_usable(studio, tmp_path):
    srv, page, url, prompts, errors = studio
    await ready(page, url)
    await show(srv, page)
    srv.bus.publish(Event("studio", {"op": "ask", "form": {"title": "Choose a direction", "questions": [
        {"id": "tone", "kind": "text-options", "title": "Tone", "options": ["Quiet", "Vivid"]},
        {"id": "notes", "kind": "freeform", "title": "Anything to add?"},
        {"id": "swatch", "kind": "svg-options", "title": "Palette", "options": ["<svg/>", "<svg/>"]},
    ]}}))
    await expect(page.locator(".qform")).to_be_visible()
    await expect(page.locator("#artbody iframe")).to_be_visible()
    canvas = await page.locator("#artpanel").bounding_box()
    drawer = await page.locator("#main").bounding_box()
    assert canvas["y"] + canvas["height"] <= drawer["y"] + 1, "interactions sit below the preview"
    await page.get_by_label("Quiet", exact=True).check()
    await page.get_by_label("Anything to add?").fill("Keep it calm")
    # A swatch is reachable by keyboard as well as by its visual label.
    await page.get_by_role("radio", name="Option 2").focus()
    await page.keyboard.press("Space")
    await page.screenshot(path=str(tmp_path / "companion-interactions.png"))
    await page.get_by_role("button", name="Send answers").click()
    await expect(page.locator(".qform")).to_have_count(0)
    assert "Quiet" in prompts[-1] and "Keep it calm" in prompts[-1] and "option 2" in prompts[-1]
    (tmp_path / "notes.txt").write_text("field notes")
    srv.bus.publish(Event("studio", {"op": "download", "path": "notes.txt", "label": "Field notes", "kind": "file"}))
    srv.bus.publish(Event("studio", {"op": "widget", "widget": "suggest_plugins", "data": {"items": [{"id": "sample", "what": "Preview a plugin"}]}}))
    await expect(page.locator("#studio-count")).to_have_text("2")
    await page.get_by_role("button", name="Chat", exact=False).first.click()
    await expect(page.locator(".dlcard")).to_be_visible()
    async with page.expect_download() as info:
        await page.locator(".dlcard a").click()
    download = await info.value
    assert download.suggested_filename == "notes.txt"
    await page.locator(".offer").click()
    await expect(page.locator("#input")).to_be_visible()
    await expect(page.locator("#input")).to_have_value("Enable plugin sample")
    assert len(prompts) == 1
    await page.locator("#input").fill("")
    await expect(page.locator("#input")).to_be_visible()
    srv.bus.publish(Event("studio", {"op": "widget", "widget": "visualize_show_widget", "data": {
        "title": "Direction", "kind": "html", "code": "<button id='b' onclick=\"sendPrompt('Use 42')\">Use this</button>", "loading": [],
    }}))
    await expect(page.frame_locator("#artbody iframe").locator("#b")).to_be_visible()
    await page.frame_locator("#artbody iframe").locator("#b").click()
    await expect(page.locator("#input")).to_be_visible()
    await expect(page.locator("#input")).to_have_value("Use 42")
    assert len(prompts) == 1, "widget text is never sent without the user's action"
    await page.locator("#send").click()
    await expect(page.locator("#input")).to_be_visible()
    assert prompts[-1] == "Use 42"
    await no_overflow(page)
    assert errors == []


async def test_point_mode_and_tweaks_keep_the_frame_bridge(studio, tmp_path):
    srv, page, url, prompts, errors = studio
    source = PAGE.replace("</head>", """<script>
const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{"size":16}/*EDITMODE-END*/;
addEventListener('message', e => { if(e.data.type === '__activate_edit_mode') document.body.dataset.tweaks='on'; });
parent.postMessage({type:'__edit_mode_available'}, '*');</script></head>""")
    (tmp_path / "garden.html").write_text(source)
    await ready(page, url)
    await show(srv, page, source)
    await expect(page.locator("#arttweaks")).to_be_visible()
    await page.locator("#arttweaks").click()
    frame = page.frame_locator("#artbody iframe")
    await expect(frame.locator("body")).to_have_attribute("data-tweaks", "on")
    await frame.locator("body").evaluate("() => parent.postMessage({type:'__edit_mode_set_keys', edits:{size:22}}, '*')")
    for _ in range(50):
        if '"size":22' in (tmp_path / "garden.html").read_text().replace(" ", ""):
            break
        await asyncio.sleep(.02)
    assert '"size":22' in (tmp_path / "garden.html").read_text().replace(" ", "")
    await page.locator("#artpoint").click()
    await frame.locator("#cta").click()
    await expect(page.locator("#mention")).to_be_visible()
    await expect(page.locator("#input")).to_be_focused()
    await page.locator("#input").fill("Make this wider")
    await page.locator("#send").click()
    await expect(page.locator("#mention")).to_be_hidden()
    assert "<mentioned-element>" in prompts[-1] and "button#cta" in prompts[-1]
    assert prompts[-1].endswith("Make this wider")
    assert errors == []


async def test_design_checkpoints_keyboard_and_retry_states(studio, tmp_path):
    srv, page, url, _, errors = studio
    await ready(page, url)
    await show(srv, page)
    await page.route("**/api/assets", lambda route: route.fulfill(json={"assets": []}))
    await page.locator("#dream-workspace-tools > summary").click()
    await page.locator("#studio-design").click()
    await expect(page.get_by_role("heading", name="Build a shared design language")).to_be_visible()
    await expect(page.get_by_role("tab", name="Design system")).to_be_focused()
    await page.keyboard.press("Home")
    await expect(page.get_by_role("tab", name="Metrics")).to_be_focused()
    await page.keyboard.press("ArrowRight")
    await expect(page.get_by_role("tab", name="Checkpoints")).to_be_focused()
    await page.keyboard.press("Escape")
    await expect(page.locator("#rail")).to_be_hidden()
    await expect(page.locator("#studio-design")).to_be_focused()
    await page.unroute("**/api/assets")
    await page.route("**/api/assets", lambda route: route.fulfill(status=503, json={"error": "unavailable"}))
    await page.locator("#studio-design").click()
    await expect(page.get_by_role("heading", name="Could not load design system")).to_be_visible()
    await page.unroute("**/api/assets")
    await page.route("**/api/assets", lambda route: route.fulfill(json={"assets": [{
        "asset": "Type specimen", "group": "Type", "path": "type.html", "version": 1, "status": "draft", "content": PAGE,
    }]}))
    await page.get_by_role("button", name="Try again").click()
    await page.locator("#designbody button[data-act=open]").click()
    await expect(page.locator("#rail")).to_be_hidden()
    await expect(page.locator("#artname")).to_have_text("Type specimen")
    await page.route("**/api/checkpoints", lambda route: route.fulfill(json={"checkpoints": []}))
    await page.locator("#studio-checkpoints").click()
    await expect(page.get_by_role("heading", name="A place to return to")).to_be_visible()
    await page.screenshot(path=str(tmp_path / "companion-checkpoints.png"))
    await page.unroute("**/api/checkpoints")
    await page.route("**/api/checkpoints", lambda route: route.fulfill(json={"checkpoints": [{
        "id": "cp1", "created_at": "2026-09-04T12:00:00Z", "label": "First draft", "files": 1, "sealed": True,
    }]}))
    await page.route("**/api/checkpoints/cp1/diff", lambda route: route.fulfill(json={"diff": "-before\n+after"}))
    await page.get_by_role("tab", name="Checkpoints").click()
    await page.locator("button[data-act=diff]").click()
    await expect(page.locator(".cpdiff")).to_contain_text("+after")
    await no_overflow(page)
    assert errors == []


async def test_reconnect_and_send_failure_preserve_preview_and_draft(studio, tmp_path):
    srv, page, url, _, errors = studio
    await ready(page, url)
    await show(srv, page)
    await page.frame_locator("#artbody iframe").locator("#draft").fill("Keep this live edit")
    await page.route("**/api/prompt", lambda route: route.fulfill(status=503, json={"error": "offline"}))
    await expect(page.locator("#input")).to_be_visible()
    await page.locator("#input").fill("Keep my feedback")
    await page.locator("#send").click()
    await expect(page.locator("#studio-error")).to_be_visible()
    await expect(page.locator("#input")).to_have_value("Keep my feedback")
    await expect(page.locator("#input")).to_be_visible()
    # Back off a disconnected socket deterministically, then use the real Retry.
    await page.evaluate("retry = 5; socket.close()")
    await expect(page.locator("#studio-connection")).to_be_visible()
    await expect(page.locator("#artbody iframe")).to_be_visible()
    await expect(page.locator("#studio-connection")).to_contain_text("Your preview is kept here")
    await page.screenshot(path=str(tmp_path / "companion-reconnect.png"))
    await page.locator("#studio-retry").click()
    await expect(page.locator("#stat")).to_have_text("Ready")
    await expect(page.locator("#studio-connection")).to_be_hidden()
    await expect(page.locator("#artver option")).to_have_count(1)
    await expect(page.frame_locator("#artbody iframe").locator("#draft")).to_have_value("Keep this live edit")
    await expect(page.locator("#input")).to_be_visible()
    await expect(page.locator("#input")).to_have_value("Keep my feedback")
    srv.bus.publish(Event("error", "Preview could not be generated"))
    await expect(page.locator("#studio-error-text")).to_have_text("Preview could not be generated")
    assert errors == []
