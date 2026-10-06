"""Nested Dream, the right-hand pane (DREAM-243). The owner, 2026-10-04: [feedback], and, of the Understand map forced beside the view, [feedback]. The workers'
bar's title is the first of three tabs; Preview is the page's own preview panel over the workers' area (what Dream
builds opens there, the view stays Nested); Map is the Understand dock over the same area; the orchestrator column
keeps its width through all of it. Real Chromium; no model, no engine."""
from __future__ import annotations

import re

from playwright.async_api import expect

from test_chat_artifact_panel import COFFEE, build  # noqa: F401

BOXES = """ids => Object.fromEntries(ids.map(s => { const e = document.querySelector(s); if(!e) return [s, null];
  const r = e.getBoundingClientRect();
  return [s, {x: r.x, y: r.y, w: r.width, h: r.height, r: r.right, b: r.bottom, shown: getComputedStyle(e).display !== 'none' && r.width > 0}]; }))"""
IDS = [".nd-orch", ".nd-top", ".nd-main", "#artpanel", "#dream-understand", ".split", "#nd-compose"]


async def nested(page, width=1600, height=900):
    await page.set_viewport_size({"width": width, "height": height})
    await page.locator("#dream-nav-nested").click()
    await expect(page.locator("#dream-nested-page")).to_be_visible()
    await expect(page.locator("#nd-pane-preview")).to_be_visible()


async def boxes(page):
    await page.evaluate("new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))")
    return await page.evaluate(BOXES, IDS)


async def test_the_bars_title_is_the_first_of_three_tabs(build):
    scripted, page, _, errors = build
    await nested(page)
    tabs = page.locator(".nd-top [role=tablist] [role=tab]")
    await expect(tabs).to_have_text(["Workers", "Preview", "Map"])
    assert await tabs.evaluate_all("ts => ts.map(t => [t.getAttribute('aria-selected'), t.tabIndex])") == [["true", 0], ["false", -1], ["false", -1]]
    assert await page.locator(".nd-title").get_attribute("id") == "nd-pane-workers"      # the title itself, its type unchanged
    await expect(page.locator("html")).to_have_attribute("data-nd-pane", "workers")
    before = await boxes(page)
    # The keyboard: the arrows walk the tabs and choose (the ARIA tabs pattern); Home returns to Workers.
    await page.locator("#nd-pane-workers").focus()
    await page.keyboard.press("ArrowRight")
    await expect(page.locator("#nd-pane-preview")).to_be_focused()
    await expect(page.locator("html")).to_have_attribute("data-nd-pane", "preview")
    # Preview with nothing built: it says what the pane is for, and the worker controls leave the bar.
    await expect(page.locator("#nd-pane-empty")).to_contain_text("Nothing to preview yet")
    await expect(page.locator("#nd-pane-latest")).to_be_hidden()
    await expect(page.locator(".nd-top .nd-grp")).to_be_hidden()
    await page.keyboard.press("Home")
    await expect(page.locator("#nd-pane-workers")).to_be_focused()
    await expect(page.locator("#nd-pane-empty")).to_be_hidden()
    await expect(page.locator(".nd-top .nd-grp")).to_be_visible()
    after = await boxes(page)
    assert after[".nd-orch"] == before[".nd-orch"] and after[".nd-main"] == before[".nd-main"], (before, after)   # Workers is the view as it was
    # The column's own tabs belong to the Workers pane: a click on one returns to it.
    await page.locator("#nd-pane-preview").click()
    await page.locator("#nd-tab-workflows").click()
    await expect(page.locator("html")).to_have_attribute("data-nd-pane", "workers")
    await page.locator("#nd-tab-agents").click()
    assert errors == []


async def test_what_dream_builds_opens_in_the_preview_pane_and_the_view_stays_nested(build):
    scripted, page, prompts, errors = build
    await nested(page)
    before = await boxes(page)
    await page.locator("#nd-compose").fill("a draft the owner is typing")
    await page.locator("#nd-compose").focus()
    await scripted.turn("build a landing page for a coffee shop", [("write_file", {"path": "coffee-shop/index.html", "content": COFFEE})])
    await expect(page.locator("html")).to_have_attribute("data-nd-pane", "preview")       # it opened here, by itself
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("Beans & Steam")
    await expect(page.locator("html")).to_have_attribute("data-dream-view", "nested")     # the view stays Nested
    await expect(page.locator("#nd-compose")).to_be_focused()                             # nothing took the focus
    await expect(page.locator("#nd-compose")).to_have_value("a draft the owner is typing")
    await page.wait_for_timeout(400)                                                      # the panel's slide has ended
    box = await boxes(page)
    orch, top, panel = box[".nd-orch"], box[".nd-top"], box["#artpanel"]
    assert orch == before[".nd-orch"], (before[".nd-orch"], orch)                         # the column keeps its place and width
    assert panel["shown"] and abs(panel["x"] - top["x"]) <= 1 and abs(panel["w"] - top["w"]) <= 1, (panel, top)   # over the workers' area
    assert abs(panel["y"] - top["b"]) <= 1 and abs(panel["b"] - box[".split"]["b"]) <= 2, (panel, top, box[".split"])   # under their bar, to the bottom
    await expect(page.locator("#artname")).to_have_text("Coffee Shop")
    # Workers and back: the same frame, not rebuilt.
    await page.evaluate("document.querySelector('#artbody iframe').dataset.mark = 'kept'")
    await page.locator("#nd-pane-workers").click()
    await expect(page.locator("#artpanel")).to_be_hidden()
    await expect(page.locator(".nd-main")).to_be_visible()
    await page.locator("#nd-pane-preview").click()
    await expect(page.locator("#artpanel")).to_be_visible()
    assert await page.evaluate("document.querySelector('#artbody iframe').dataset.mark") == "kept"
    # The column's resize edge moves the pane with it.
    await page.locator("#nd-h-orch").focus()
    await page.keyboard.press("Shift+ArrowLeft")
    moved = await boxes(page)
    assert moved[".nd-orch"]["w"] < orch["w"] and abs(moved["#artpanel"]["x"] - moved[".nd-top"]["x"]) <= 1, moved
    # Closing the preview gives the workers back.
    await page.locator("#artclose").click()
    await expect(page.locator("html")).to_have_attribute("data-nd-pane", "workers")
    await expect(page.locator(".nd-main")).to_be_visible()
    # The Preview pane then offers what was built.
    await page.locator("#nd-pane-preview").click()
    await expect(page.locator("#nd-pane-latest")).to_have_text("Open Coffee Shop")
    await page.locator("#nd-pane-latest").click()
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("Beans & Steam")
    assert prompts == ["build a landing page for a coffee shop"] and errors == []


async def test_the_map_is_a_pane_and_never_squeezes_the_view(build):
    scripted, page, _, errors = build
    await nested(page, 2000, 1125)                                                        # the owner's screenshot's size
    before = await boxes(page)
    await page.locator("#nd-pane-map").click()
    await expect(page.locator("html")).to_have_class(re.compile(r"\bunderstand-open\b"))
    await expect(page.locator("#dream-understand")).to_be_visible()
    box = await boxes(page)
    dock, top = box["#dream-understand"], box[".nd-top"]
    assert box[".nd-orch"] == before[".nd-orch"], (before[".nd-orch"], box[".nd-orch"])   # the column is not squeezed
    assert abs(dock["x"] - top["x"]) <= 1 and abs(dock["w"] - top["w"]) <= 1 and abs(dock["y"] - top["b"]) <= 1, (dock, top)
    assert await page.locator("#nd-compose").evaluate("e => { const r = e.getBoundingClientRect(), t = document.elementFromPoint(r.x + 20, r.y + 10); return t === e || e.contains(t); }")
    await page.locator("#nd-pane-workers").click()                                        # leaving the Map closes the dock
    await expect(page.locator("#dream-understand")).to_be_hidden()
    assert (await boxes(page))[".nd-main"] == before[".nd-main"]
    # The Understand switch of the sidebar, in this view: the same pane, and the view stays Nested.
    await page.locator("#dream-nav-understand").click()
    await expect(page.locator("html")).to_have_attribute("data-dream-view", "nested")
    await expect(page.locator("html")).to_have_attribute("data-nd-pane", "map")
    await expect(page.locator("#nd-pane-map")).to_have_attribute("aria-selected", "true")
    await page.locator("#ua-close").click()                                               # the dock's own close: back to Workers
    await expect(page.locator("html")).to_have_attribute("data-nd-pane", "workers")
    # A narrow window is one column: the pane is a sheet over it, and nothing is pushed sideways.
    await page.set_viewport_size({"width": 700, "height": 900})
    await page.locator("#nd-pane-map").click()
    await expect(page.locator("#dream-understand")).to_be_visible()
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    await page.locator("#ua-close").click()
    assert errors == []
