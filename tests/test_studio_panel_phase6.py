"""Phase 6 in the real panel: a question form, Tweaks persisted to disk, and point
mode's <mentioned-element> on the next prompt. Headless Chromium against a live
StudioServer."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from dream.core.backends.base import Event
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.gui.tweaks import read_block

UI = Path(__file__).parent.parent / "dream" / "gui" / "static" / "index.html"

TWEAKABLE = """<!doctype html><html><head><script>
const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{"fontSize": 16}/*EDITMODE-END*/;
window.addEventListener('message', e => {
  const d = e.data || {};
  if(d.type === '__activate_edit_mode') document.body.dataset.tweaks = 'on';
  if(d.type === '__deactivate_edit_mode') document.body.dataset.tweaks = 'off';
});
window.parent.postMessage({type: '__edit_mode_available'}, '*');
</script></head><body><h1 id="h">Title</h1>
<section data-screen-label="02 Agenda"><button id="cta" class="btn primary">Go</button></section>
</body></html>"""


def test_the_panel_has_the_pieces_and_keeps_its_invariants():
    ui = UI.read_text()
    for needle in ('id="arttweaks"', 'id="artpoint"', 'id="mention"', "function renderForm",
                   "function submitForm", "'/api/answer'", "'/api/tweak'", "'/api/upload'",
                   "__dream_point_on", "__dream_mention", "mentionBlock(mention)"):
        assert needle in ui, needle
    # swatches: model SVG never inline — a fully sandboxed frame, and no scripts
    sw = ui[ui.index("function swatchFrame"):ui.index("function renderForm")]
    assert "setAttribute('sandbox', '')" in sw and "srcdoc" in sw
    # form text is escaped
    rf = ui[ui.index("function renderForm"):ui.index("async function submitForm")]
    for m in ("esc(form.title", "esc(o)", "esc(q.title", "esc(q.subtitle)"):
        assert m in rf, m
    # a mention listener only trusts the open frame
    pm = ui[ui.index("/* ---------- point mode"):ui.index("/* ---------- the user's view")]
    assert "e.source !== f.contentWindow" in pm


@pytest.mark.asyncio
async def test_end_to_end_form_tweaks_and_point_mode(tmp_path):
    from playwright.async_api import async_playwright

    (tmp_path / "page.html").write_text(TWEAKABLE)
    prompts: list[str] = []
    srv = StudioServer(EventBus(), on_prompt=prompts.append, session={"workspace": str(tmp_path)})
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1600, "height": 900})
            await page.goto(url, wait_until="load")
            await page.wait_for_function("document.getElementById('stat').textContent === 'live'", timeout=5000)

            # 1. a question form: fill, submit, and the answers become a prompt
            srv.bus.publish(Event("studio", {"op": "ask", "form": {"title": "Deck", "questions": [
                {"id": "audience", "kind": "text-options", "title": "Who?", "options": ["Execs", "Engineers", "Other"]},
                {"id": "length", "kind": "slider", "title": "Slides", "min": 5, "max": 40, "step": 1, "default": 12},
                {"id": "vibe", "kind": "svg-options", "title": "Tone", "options": ["<svg viewBox='0 0 80 56'><rect width='80' height='56' fill='#0af'/></svg>", "<svg viewBox='0 0 80 56'><script>parent.document.title='pwned'</script></svg>"]},
                {"id": "notes", "kind": "freeform", "title": "Notes"},
            ]}}))
            await page.wait_for_selector("form.qform", timeout=5000)
            await page.check("form.qform input[name='audience'][value='Execs']")
            await page.fill("form.qform textarea[name='notes']", "keep it short")
            await page.click("form.qform label.qsvg:nth-of-type(2) .qsvgbox")
            await page.click("form.qform button[type=submit]")
            await page.wait_for_function("document.querySelector('form.qform') === null", timeout=5000)
            assert prompts and prompts[-1].startswith("Answers to 'Deck':")
            assert "- audience: Execs" in prompts[-1] and "- length: 12" in prompts[-1]
            assert "- vibe: option 2" in prompts[-1] and "- notes: keep it short" in prompts[-1]
            assert await page.title() != "pwned", "a swatch's script must not run in the panel"

            # 2. open a tweakable page: the Tweaks button appears; toggling reaches the page;
            #    the page's edits land in the file on disk
            srv.bus.publish(Event("studio", {"op": "show", "path": "page.html", "title": "page.html", "content": TWEAKABLE}))
            await page.wait_for_selector("#artbody iframe", timeout=5000)
            await page.wait_for_function("document.getElementById('arttweaks').style.display !== 'none'", timeout=5000)
            await page.click("#arttweaks")
            frame = page.frame_locator("#artbody iframe")
            await page.wait_for_timeout(200)
            assert await frame.locator("body").get_attribute("data-tweaks") == "on"
            # the PAGE posts its edit (from inside the frame, as a real page would)
            await frame.locator("body").evaluate("() => parent.postMessage({type:'__edit_mode_set_keys', edits:{fontSize: 22}}, '*')")
            for _ in range(50):
                try:  # the write is atomic now; a poll that lands between two states just retries
                    if read_block((tmp_path / "page.html").read_text()).get("fontSize") == 22:
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.05)
            assert read_block((tmp_path / "page.html").read_text()) == {"fontSize": 22}
            # a message from THIS page (not the frame) must not be honoured
            await page.evaluate("window.postMessage({type:'__edit_mode_set_keys', edits:{fontSize: 99}}, '*')")
            await page.wait_for_timeout(200)
            assert read_block((tmp_path / "page.html").read_text()) == {"fontSize": 22}

            # 3. point mode: click the button inside the frame; the next prompt names it
            await page.click("#artpoint")
            await page.wait_for_timeout(100)
            await frame.locator("#cta").click()
            await page.wait_for_function("document.getElementById('mention').style.display !== 'none'", timeout=5000)
            assert "02 Agenda" in await page.text_content("#mentiontext")
            await page.fill("#input", "make this bigger")
            await page.click("#send")
            for _ in range(50):
                if len(prompts) >= 2:
                    break
                await asyncio.sleep(0.05)
            sent = prompts[-1]
            assert sent.startswith("<mentioned-element>") and sent.endswith("make this bigger")
            assert "screen: 02 Agenda" in sent and "button#cta" in sent and 'id: data-cc-id="cc-1"' in sent
            assert await frame.locator("#cta").get_attribute("data-cc-id") == "cc-1"
            await browser.close()
    finally:
        await srv.stop()
