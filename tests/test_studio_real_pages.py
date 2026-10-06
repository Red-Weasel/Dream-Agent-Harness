"""A0 (build it right there): the owner's kinds of page, through the real `done` tool, the real StudioServer and
the real desktop page (`?companion=1`) in headless Chromium at 1100x800 and 900x700 -- what the owner saw was
"booting..." over a blank frame and "rendering is not confirmed" in the model's result, nine shows out of nine.

(1) a site with style.css, app.js and logo.png renders with the style applied, the script run and the image
decoded, and the result says the owner's view loaded clean; (2) a page with an importmap and local modules
runs (DREAM-238: its module graph is inlined) and a page that fetches a local file shows the owner-visible
notice and the Open in browser link; (3) a page busy for 5 s right after load is not
closed as "navigated away" (the watchdog re-pings), while a page that does navigate away is still closed;
(4) a self-contained page is byte-identical in the event; (5) the pane's report is in the runtime log."""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

import pytest
from playwright.async_api import async_playwright, expect

import dream.config as config
from dream.gui import bundle, preview as preview_mod
from dream.tools import mirror
from dream.tools.native import write_file
from dream.tools.studio import done
from _artifact_play import Play, text

SHOTS = Path(os.environ.get("DREAM_STUDIO_SHOTS") or "/nonexistent")
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAEklEQVR4nGP4z8DwHwyBNAMDAHbjD/0ie3IoAAAAAElFTkSuQmCC")
SITE = {"index.html": ('<!doctype html><html><head><title>Coffee Shop</title><link rel="stylesheet" href="style.css">'
                       '</head><body><h1 id="h">booting...</h1><img id="logo" src="logo.png" alt="logo">'
                       '<script src="app.js"></script></body></html>'),
        "style.css": "body{background:rgb(1, 2, 3);color:#fff;margin:0}#swatch{width:120px;height:120px;background:rgb(200, 30, 40)}",
        "app.js": "document.getElementById('h').textContent = 'running'; document.body.insertAdjacentHTML('beforeend', '<div id=swatch></div>');"}
FALCON = ('<!doctype html><html><head><title>Falcon 9</title><script type="importmap">{"imports":{"three":"./vendor/three.js"}}'
          '</script></head><body><h1 id="h">booting...</h1><script type="module">import("./falcon9/main.js").then(m => m.start());'
          '</script></body></html>')
# Busy from the moment it loaded: a 5 s task queued on the posted-message source at load runs before the
# pane's ping (the same source, posted later), so the ping waits 5 s -- the shape of a scene that compiles
# its shaders on its first frame. Before the A0 watchdog, headless Chromium closed this page as "navigated
# away" in 2 runs of 3 (the race between the parent's load event and the frame's queued task).
BUSY = ('<!doctype html><title>Busy</title><h1 id="h">busy</h1><script>'
        "addEventListener('load', () => { const ch = new MessageChannel(); ch.port1.onmessage = () => { "
        "const t = Date.now(); while (Date.now() - t < 5000) {} document.getElementById('h').textContent = 'done'; }; "
        "ch.port2.postMessage(1); });</script>")
# Reloads itself once (window.name survives the reload): the document leaves and comes back with the bridge.
RELOADS = ('<!doctype html><title>Reloads</title><h1 id="h">first</h1><script>'
           "if(window.name !== 'again'){ window.name = 'again'; setTimeout(() => location.reload(), 300); }"
           " else { document.getElementById('h').textContent = 'reloaded'; }</script>")
PLAIN = "<!doctype html><title>Plain</title><style>body{background:#123}</style><h1>Plain page</h1><script>let a = 1;</script>"


@pytest.fixture(params=[(1100, 800), (900, 700)], ids=["1100x800", "900x700"])
def size(request):
    return request.param


@pytest.fixture
async def pane(tmp_path, monkeypatch, size):
    monkeypatch.delenv("DREAM_DESKTOP_SESSION_FILE", raising=False)
    monkeypatch.setattr(preview_mod, "display_render_node", lambda: None)
    ws = tmp_path / "ws"
    ws.mkdir()
    async with Play(ws, session_id="a0-real") as play:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-gpu"])
            page = await browser.new_page(viewport={"width": size[0], "height": size[1]})
            page.set_default_timeout(8000)
            await page.goto(play.companion_url, wait_until="load")
            await expect(page.locator("#stat")).to_have_text("Ready")
            await play.connected()
            yield play, page, ws, size
            await browser.close()


async def shot(page, name):
    if SHOTS.is_dir():
        await page.screenshot(path=str(SHOTS / name))


async def test_a_site_with_css_js_and_an_image_renders_and_the_result_says_loaded_clean(pane):
    play, page, ws, size = pane
    steps = [(write_file, {"path": f"site/{name}", "content": body}) for name, body in SITE.items()]
    (ws / "site").mkdir()
    (ws / "site" / "logo.png").write_bytes(PNG)
    results = await play.turn("build a landing page for a coffee shop", steps + [(done, {"path": "site/index.html"})])
    result = results[-1]
    assert not result.get("is_error"), text(result)
    assert "Studio (owner's view): loaded clean (no errors)." in text(result), text(result)
    assert "rendering is not confirmed" not in text(result)
    frame = page.frame_locator("#artbody iframe")
    await expect(frame.locator("#h")).to_have_text("running")                                  # app.js ran
    assert await frame.locator("body").evaluate("b => getComputedStyle(b).backgroundColor") == "rgb(1, 2, 3)"   # style.css applied
    assert await frame.locator("#logo").evaluate("i => i.naturalWidth") == 2                   # logo.png decoded
    assert await frame.locator("#swatch").evaluate("e => getComputedStyle(e).backgroundColor") == "rgb(200, 30, 40)"
    box = await page.locator("#artbody iframe").bounding_box()
    assert box["width"] >= 300 and box["height"] >= 300, box
    assert await frame.locator(f"#{bundle.NOTICE_ID}").count() == 0, "nothing to warn about"
    await shot(page, f"a0-site-{size[0]}x{size[1]}.png")
    # the pane's report is in the runtime log (A0 acceptance 6)
    log = config.LOG_DIR / "runtime" / "a0-real.jsonl"
    events = [json.loads(l) for l in log.read_text().splitlines()]
    reports = [e for e in events if e["event"] == "studio_report" and e["path"] == "site/index.html"]
    assert reports and reports[0]["status"] == "loaded" and reports[0]["errors"] == [], events


async def test_an_importmap_page_with_local_modules_runs_in_the_owners_pane(pane):
    """DREAM-238: the page of the owner's rocket viewer -- an importmap to a vendored library and a module that
    imports the page's own module -- ran nowhere in Dream before (a blank pane, then A0's notice). Its local module
    graph is inlined now, so it runs in the pane's sandboxed frame and in the model's hidden frame alike."""
    play, page, ws, size = pane
    (ws / "falcon9").mkdir()
    (ws / "falcon9" / "main.js").write_text("import { THREE } from 'three';\nexport function start(){ "
                                            "document.getElementById('h').textContent = 'running ' + THREE; }")
    (ws / "vendor").mkdir()
    (ws / "vendor" / "three.js").write_text("export const THREE = 1;")
    (ws / "falcon9-launch.html").write_text(FALCON)
    (result,) = await play.turn("show the launch viewer", [(done, {"path": "falcon9-launch.html"})])
    assert not result.get("is_error"), text(result)
    assert "Studio (owner's view): loaded clean (no errors)." in text(result), text(result)
    frame = page.frame_locator("#artbody iframe")
    await expect(frame.locator("#h")).to_have_text("running 1")            # the page's module ran, with the library
    assert await frame.locator(f"#{bundle.NOTICE_ID}").count() == 0, "nothing to warn about"
    assert await page.locator("#artbody iframe").get_attribute("sandbox") == "allow-scripts"   # the same sandbox as before
    await shot(page, f"a4-local-modules-{size[0]}x{size[1]}.png")


async def test_dreams_own_three_starter_loads_in_the_owners_pane(pane):
    """The real vendored three.js (two files of half a megabyte and its addons) through the pane's importmap, under
    the pane's policy. No WebGL here (the test browser has no GPU): the library, a scene graph and an addon class."""
    from dream.tools.starters import copy_starter_component
    play, page, ws, size = pane
    out = await copy_starter_component.handler({"kind": "three"})
    tags = text(out)[text(out).index('<script type="importmap">'):]
    scene = tags.replace("// your scene", """
const s = new THREE.Scene(); s.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1)));
const c = new OrbitControls(new THREE.PerspectiveCamera(50, 1.6, 0.1, 100), document.body);
document.getElementById('h').textContent = 'three r' + THREE.REVISION + ' ' + s.children.length + ' ' + (c instanceof OrbitControls) + ' ' + (typeof Sky);""")
    (ws / "scene.html").write_text('<!doctype html><title>Scene</title><body><h1 id="h">booting...</h1>' + scene + "</body>")
    (result,) = await play.turn("show the scene", [(done, {"path": "scene.html"})])
    frame = page.frame_locator("#artbody iframe")
    await expect(frame.locator("#h")).to_have_text(re.compile(r"^three r\d+ 1 true function$"))
    assert await frame.locator(f"#{bundle.NOTICE_ID}").count() == 0
    assert "Studio (owner's view): loaded clean (no errors)." in text(result), text(result)


async def test_a_page_that_fetches_a_local_file_shows_the_owner_the_notice_and_the_open_in_browser_link(pane):
    play, page, ws, size = pane
    (ws / "rows.json").write_text('{"ok": "running"}')
    (ws / "table.html").write_text('<!doctype html><title>Table</title><h1 id="h">booting...</h1><script>'
                                   "fetch('./rows.json').then(r => r.json()).then(d => { document.getElementById('h').textContent = d.ok; });"
                                   "</script>")
    (result,) = await play.turn("show the table", [(done, {"path": "table.html"})])
    frame = page.frame_locator("#artbody iframe")
    notice = frame.locator(f"#{bundle.NOTICE_ID}")
    await expect(notice).to_be_visible()
    await expect(notice).to_contain_text("This page needs files the preview cannot load by URL")
    await expect(notice).to_contain_text("Open in browser runs it. Found: fetch() of a local file")
    top = await notice.evaluate("e => [e.getBoundingClientRect().top, e.getBoundingClientRect().height, getComputedStyle(e).position]")
    assert top[0] == 0 and top[1] >= 20 and top[2] == "fixed", top     # pinned to the frame's top, over the page
    await expect(frame.locator("#h")).to_have_text("booting...")            # the fetch did not run
    link = page.locator("#artext")
    await expect(link).to_be_visible()
    await expect(link).to_have_text("Open in browser ↗")
    href = await link.get_attribute("href")
    assert href and "/table.html" in href, href
    await shot(page, f"a0-fetch-notice-{size[0]}x{size[1]}.png")
    # what the model was told names the failure
    assert "error" in text(result).lower() or "not clean" in text(result).lower(), text(result)


async def test_a_page_busy_for_five_seconds_after_load_is_not_closed(pane):
    play, page, ws, size = pane
    (ws / "busy.html").write_text(BUSY)
    (result,) = await play.turn("show it", [(done, {"path": "busy.html"})])
    assert not result.get("is_error"), text(result)
    await page.wait_for_timeout(9000)                                       # the 5 s task, then past the 3 s watchdog
    note = await page.locator("#artnote").text_content()
    closed = [t for t in await page.locator(".sys").all_text_contents() if "navigated away" in t]
    assert await page.locator("#artbody iframe").count() == 1 and "navigated away" not in note and not closed, (note, closed)
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("done")   # the task finished inside


async def test_a_page_that_reloads_itself_stays_open(pane):
    play, page, ws, size = pane
    (ws / "reloads.html").write_text(RELOADS)
    (result,) = await play.turn("show it", [(done, {"path": "reloads.html"})])
    assert not result.get("is_error"), text(result)
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("reloaded")
    await page.wait_for_timeout(4000)                                       # past the 3 s a leaving document gets
    assert await page.locator("#artbody iframe").count() == 1
    assert "navigated away" not in (await page.locator("#artnote").text_content())


async def test_a_page_that_navigates_away_is_still_closed_promptly(pane):
    play, page, ws, size = pane
    (ws / "leave.html").write_text("<!doctype html><title>Leave</title><p>hi</p><script>location.href = 'about:blank';</script>")
    await play.turn("show it", [(done, {"path": "leave.html"})])
    await page.wait_for_function("!document.querySelector('#artbody iframe')", timeout=8000)
    assert "navigated away" in await page.locator("#artnote").text_content()


async def test_a_self_contained_page_is_byte_identical_in_the_event(pane):
    play, page, ws, size = pane
    (ws / "plain.html").write_text(PLAIN)
    (result,) = await play.turn("show it", [(done, {"path": "plain.html"})])
    assert not result.get("is_error"), text(result)
    shows = [e for e in play.events if e.kind == "studio" and e.data.get("op") == "show"]
    assert shows[-1].data["content"] == PLAIN
    assert mirror.shown()["digest"] == mirror.digest(PLAIN)
    await expect(page.frame_locator("#artbody iframe").locator("h1")).to_have_text("Plain page")
    assert "Studio (owner's view): loaded clean (no errors)." in text(result)
