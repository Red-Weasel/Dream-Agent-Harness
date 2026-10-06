"""The Artifacts tab's home as a library (DREAM-241). The owner, 2026-10-04, with examples (a library grid with a
picture per item; a list with one row per item, grouped by day and month): "Also gave some examples for Artifact tab
polish". Pinned here: the groups by day and month with this session's badge, the kind filters with their counts, the
search over title and path, Grid | List and its memory, the thumbnails (what each kind shows, that a page's runs no
script and obeys the internet switch, that no content is fetched for them), the "..." menu by pointer and keyboard,
a redraw that moves nothing, the Nested palette, and a phone-width window. The scripted session of
tests/test_chat_artifact_panel.py. Real Chromium; no model, no engine."""
from __future__ import annotations

import json
import os
import re
import time

from playwright.async_api import expect

from dream.core.backends.base import Event
from dream.gui import server as server_mod
from dream.gui.artifacts import ArtifactRegistry
from test_artifacts_tab import ROWS, menu, row, rows, size
from test_chat_artifact_kinds import png, shell, wav
from test_chat_artifact_panel import ABOUT, COFFEE, build  # noqa: F401  (build is the fixture)

SVG = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 200'><rect width='320' height='200' fill='#f4efe6'/>"
       "<rect x='30' y='40' width='100' height='50' fill='#1f4e79'/><title>Pipeline</title></svg>")
CARD, SELECT = "rgb(21, 19, 24)", "rgb(42, 31, 51)"            # --nd-card, --nd-select


def earlier(scripted, session, files, days):
    """An earlier session of the same workspace whose artifacts were built `days` ago."""
    ws, store = scripted.ws, scripted.registry._store
    reg = ArtifactRegistry(ws, session, lambda ev: None, store=store)
    for rel, text in files:
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(text)
        reg.observe(Event("tool_use", {"id": rel, "name": "write_file", "input": {"path": rel, "content": text}}))
        reg.observe(Event("tool_result", {"id": rel, "name": "write_file", "content": "ok", "is_error": False}))
    then = time.time() - days * 86400
    index = store / session / "index.json"
    data = json.loads(index.read_text())
    for item in data["artifacts"]:
        for version in item["versions"]:
            version["at"] = then
    index.write_text(json.dumps(data))
    for rel, _ in files:                               # the files are that old too (a later shell command did not write them)
        os.utime(ws / rel, (then, then))


def month(days):
    """The heading of something built `days` ago: its month, with the year when it is not this one."""
    then, now = time.gmtime(time.time() - days * 86400), time.gmtime()
    return time.strftime("%B", then) if then.tm_year == now.tm_year else time.strftime("%B %Y", then)


async def home(page):
    await page.evaluate("document.getElementById('dream-nav-studio').click()")
    await expect(page.locator("#studio-empty")).to_be_visible()


async def mixed(scripted, page, count=5):
    """A page, a report, a diagram, an image and a sound, in that order; the home showing."""
    await scripted.turn("build the shop", [("write_file", {"path": "coffee-shop/index.html", "content": COFFEE})])
    await scripted.turn("write it up", [("write_file", {"path": "notes.md", "content": "# Field notes\n"})])
    await scripted.turn("draw it", [("write_file", {"path": "pipeline.svg", "content": SVG})])
    await scripted.turn("chart it", [], end=False)
    await shell(scripted, {"out/chart.png": png(), "out/voice.wav": wav()})
    scripted.end()
    await expect(page.locator("#stream .artcard")).to_have_count(5)
    await home(page)
    await expect(page.locator(ROWS)).to_have_count(count)


async def test_items_sit_under_their_day_or_month_newest_first_and_this_sessions_are_marked(build):
    scripted, page, _, _ = build
    earlier(scripted, "s-yesterday", [("old/pricing.html", COFFEE.replace("Coffee Shop", "Pricing page"))], 1)
    earlier(scripted, "s-month", [("old/landing.html", ABOUT.replace("About Us", "Landing v1"))], 45)
    earlier(scripted, "s-year", [("old/archive.md", "# Archive\n")], 420)
    await scripted.turn("build the shop", [("write_file", {"path": "coffee-shop/index.html", "content": COFFEE})])
    await home(page)
    await expect(page.locator(ROWS)).to_have_count(4)
    await expect(page.locator("#art-home-list h2")).to_have_text(["Today", "Yesterday", month(45), month(420)])
    titles = await page.locator(ROWS + " b").all_text_contents()
    assert titles == ["Coffee Shop", "Pricing page", "Landing v1", "Archive"], titles          # newest first, across the groups
    per_group = await page.evaluate("[...document.querySelectorAll('#art-home-list .art-section')].map(s => s.querySelectorAll('.art-row').length)")
    assert per_group == [1, 1, 1, 1], per_group
    await expect(row(page, "Coffee Shop").locator(".badge")).to_have_text("this session")
    assert await page.locator(ROWS + " .badge").count() == 1                                   # an earlier session's has none
    # an older item says its date where a new one says how long ago
    assert re.fullmatch(r"Pricing page Page · v1 · \w{3} \d{1,2}, \d{4} · .+", (await rows(page))[1]), await rows(page)
    # the old home's sentence is the title's tooltip now
    assert "pages, reports, images" in await page.locator("#studio-empty h1").get_attribute("title")


async def test_filters_show_the_kinds_that_exist_with_counts_and_search_reads_titles_and_paths(build):
    scripted, page, _, _ = build
    await mixed(scripted, page)
    shown = page.locator("#art-filters button:visible")
    assert [" ".join(t.split()) for t in await shown.all_inner_texts()] == ["All 5", "Pages 2", "Reports 1", "Images 1", "Media 1"]
    await expect(page.locator('#art-filters button[data-kind="document"]')).to_be_hidden()    # no document was built
    await expect(page.locator('#art-filters button[data-kind="all"]')).to_have_attribute("aria-pressed", "true")
    pages = page.get_by_role("button", name="Pages 2", exact=True)
    await pages.click()
    await expect(pages).to_have_attribute("aria-pressed", "true")
    await expect(pages).to_be_focused()                                                        # the redraw left the keyboard there
    assert await page.locator(ROWS + " b").all_text_contents() == ["Pipeline", "Coffee Shop"]  # a diagram is a page too
    assert await pages.evaluate("b => getComputedStyle(b).backgroundColor") == SELECT
    await page.get_by_role("button", name="Media 1", exact=True).click()
    assert await page.locator(ROWS + " b").all_text_contents() == ["voice.wav"]
    await page.get_by_role("button", name="All 5", exact=True).click()
    search = page.get_by_label("Search artifacts")
    await search.fill("NOTES")                                                                 # the title, any case
    assert await page.locator(ROWS + " b").all_text_contents() == ["Field notes"]
    await expect(search).to_be_focused()
    await search.fill("out/ch")                                                                # the path
    assert await page.locator(ROWS + " b").all_text_contents() == ["chart.png"]
    # nothing matches: it says what was looked for and where, and Clear gives everything back
    await page.get_by_role("button", name="Reports 1", exact=True).click()
    await search.fill("zzz")
    await expect(page.locator(ROWS)).to_have_count(0)
    none = page.locator("#art-home-list .art-none")
    await expect(none.get_by_role("heading")).to_have_text("Nothing matches")
    await expect(none.locator("p")).to_have_text('No artifact in Reports has "zzz" in its title or path.')
    await none.get_by_role("button", name="Clear").click()
    await expect(page.locator(ROWS)).to_have_count(5)
    await expect(search).to_have_value("")
    await expect(search).to_be_focused()
    await expect(page.locator('#art-filters button[data-kind="all"]')).to_have_attribute("aria-pressed", "true")
    # a hostile title is text, never markup
    await scripted.turn("a page", [("write_file", {"path": "x.html", "content": "<!doctype html><title>&lt;img src=x onerror=window.pwned=1&gt;</title><p>x</p>"})])
    await home(page)
    await expect(page.locator(ROWS)).to_have_count(6)
    assert await page.evaluate("window.pwned") is None and await page.locator("#art-home-list img[src='x']").count() == 0


async def test_grid_and_list_and_the_choice_is_remembered_in_this_browser(build):
    scripted, page, _, _ = build
    await mixed(scripted, page)
    await expect(page.locator("#art-view-grid")).to_have_attribute("aria-pressed", "true")     # the grid is the default
    look = await page.evaluate("""() => { const r = e => e.getBoundingClientRect(), cards = [...document.querySelectorAll('#art-home-list .art-grid .art-row')],
        t = cards[0].querySelector('.thumb');
        return {cards: cards.length, w: r(cards[0]).width, ratio: r(t).width / r(t).height, bg: getComputedStyle(cards[0]).backgroundColor,
                radius: getComputedStyle(cards[0]).borderTopLeftRadius, sameRow: r(cards[0]).y === r(cards[1]).y,
                open: [r(cards[0].querySelector('.primary')).width, r(cards[0]).width], sw: document.documentElement.scrollWidth, iw: innerWidth}; }""")
    assert look["cards"] == 5 and 240 <= look["w"] <= 340 and look["sameRow"], look            # cards side by side
    assert abs(look["ratio"] - 1.6) < 0.02 and look["bg"] == CARD and look["radius"] == "10px", look   # a 16:10 picture; Nested's card
    assert abs(look["open"][0] - look["open"][1]) <= 2 and look["sw"] <= look["iw"], look      # the whole card is its Open button
    shop_size = size(len(COFFEE.encode()))
    assert (await rows(page))[-1] == f"Coffee Shop Page · v1 · just now · {shop_size}"
    await page.get_by_role("button", name="List", exact=True).click()
    await expect(page.locator("#art-home-list .art-rows .art-row")).to_have_count(5)
    assert await page.locator("#art-home-list .thumb").count() == 0                            # a row has no picture: the kind in a square
    shop = row(page, "Coffee Shop")
    await expect(shop.locator(".ic")).to_have_text("</>")
    await expect(shop.locator(".meta")).to_have_text(f"Page · coffee-shop/index.html · {shop_size}")
    await expect(shop.locator(".side")).to_have_text("v1 · just now")
    await expect(shop.locator(".badge")).to_have_text("this session")
    line = await shop.evaluate("r => { const b = r.getBoundingClientRect(), s = r.querySelector('.side').getBoundingClientRect(); return [b.height, s.right > b.x + b.width * .7]; }")
    assert line[0] <= 64 and line[1], line                                                     # one line; the version and age at its right
    assert await page.evaluate("localStorage.getItem('dream.artifacts.view')") == "list"
    await shop.get_by_role("button", name="Open", exact=True).click()                          # the row opens it in the tab
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("Beans & Steam")
    await page.reload()
    await expect(page.locator("#stat")).to_have_text("Ready")
    await home(page)
    await expect(page.locator("#art-home-list .art-rows .art-row")).to_have_count(5)           # remembered
    await expect(page.locator("#art-view-list")).to_have_attribute("aria-pressed", "true")
    await page.get_by_role("button", name="Grid", exact=True).click()
    await expect(page.locator("#art-home-list .art-grid .art-row")).to_have_count(5)
    assert await page.evaluate("localStorage.getItem('dream.artifacts.view')") == "grid"


async def test_a_thumbnail_runs_nothing_fetches_no_content_and_obeys_the_internet_switch(build):
    scripted, page, _, _ = build
    earlier(scripted, "s-old", [("old/pricing.html", COFFEE.replace("Coffee Shop", "Pricing page"))], 2)
    fetched = []
    page.on("request", lambda r: fetched.append(r.url) if "/api/artifact/" in r.url else None)
    await mixed(scripted, page, 6)
    fetched.clear()
    await page.wait_for_function("(i => i && i.complete && i.naturalWidth > 0)(document.querySelector('#art-home-list .art-row[data-k=\"file:out/chart.png\"] img'))")
    thumbs = await page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#art-home-list .art-row')].map(r => {
        const t = r.querySelector('.thumb'), f = t.querySelector('iframe'), i = t.querySelector('img'), g = t.querySelector('.glyph');
        return [r.querySelector('b').textContent, f ? {frame: f.getAttribute('src'), sandbox: f.getAttribute('sandbox'), tab: f.tabIndex, hidden: f.getAttribute('aria-hidden'),
                  pointer: getComputedStyle(f).pointerEvents, lazy: f.loading, fills: Math.abs(f.getBoundingClientRect().width - t.clientWidth) <= 2,
                  layout: [f.offsetWidth, f.offsetHeight]}
                : i ? {img: i.getAttribute('src'), lazy: i.loading, alt: i.alt, fit: getComputedStyle(i).objectFit} : {glyph: g.textContent}]; }))""")
    shop = thumbs["Coffee Shop"]
    assert shop["frame"] == scripted.srv.pages.base_url + "coffee-shop/index.html", shop       # the page's own address, not Dream's
    assert shop["sandbox"] == "" and shop["tab"] == -1 and shop["hidden"] == "true" and shop["pointer"] == "none", shop   # no script, no focus, no pointer
    assert shop["lazy"] == "lazy" and shop["fills"] and shop["layout"] == [1280, 800], shop    # laid out at 1280, scaled to the card
    assert scripted.srv.token not in shop["frame"]
    chart, diagram = thumbs["chart.png"], thumbs["Pipeline"]
    assert chart["img"].startswith("/api/download?path=out%2Fchart.png&token=") and chart["lazy"] == "lazy" and chart["alt"] == "" and chart["fit"] == "cover", chart
    assert diagram["img"].startswith("/api/download?path=pipeline.svg&token=") and diagram["fit"] == "contain", diagram   # a diagram is a picture: nothing in it runs
    assert thumbs["Field notes"] == {"glyph": "MD"} and thumbs["voice.wav"] == {"glyph": "AUD"}, thumbs
    assert thumbs["Pricing page"] == {"glyph": "</>"}, thumbs                                   # an earlier session's: its kind only
    assert fetched == [], fetched                                                              # no artifact content was fetched for a picture
    # the internet switch off: a page's picture could load from the network, so it is its kind's tile
    await page.evaluate("document.getElementById('netpill').click()")
    await expect(row(page, "Coffee Shop").locator(".thumb .glyph")).to_have_text("</>")
    assert await page.locator("#art-home-list iframe").count() == 0
    await expect(row(page, "chart.png").locator(".thumb img")).to_have_count(1)                # a picture file loads nothing else
    await page.evaluate("document.getElementById('netpill').click()")
    await expect(row(page, "Coffee Shop").locator(".thumb iframe")).to_have_count(1)
    # the desktop window never loads a page-server page in its own view (DREAM-111): there a page is its kind's tile
    await page.evaluate("document.documentElement.classList.add('dream-native')")
    await expect(row(page, "Coffee Shop").locator(".thumb .glyph")).to_have_text("</>")
    assert await page.locator("#art-home-list iframe").count() == 0
    await expect(row(page, "Pipeline").locator(".thumb img")).to_have_count(1)                 # pictures are Dream's own files: they stay
    await page.evaluate("document.documentElement.classList.remove('dream-native')")
    await expect(row(page, "Coffee Shop").locator(".thumb iframe")).to_have_count(1)
    # a file that is gone: its tile, a line that says so, and only what still works in its menu
    (scripted.ws / "out" / "chart.png").unlink()
    await page.evaluate("window.dispatchEvent(new Event('dream:artifacts'))")
    gone = row(page, "chart.png")
    await expect(gone.locator(".gone")).to_have_text("The file is no longer in the workspace.")
    await expect(gone.locator(".thumb .glyph")).to_have_text("IMG")
    assert await gone.locator(".acts > *").all_text_contents() == ["Open beside chat"]


async def test_the_menu_by_pointer_and_keyboard_and_a_redraw_moves_nothing(build, monkeypatch):
    scripted, page, _, _ = build
    opened = []
    monkeypatch.setattr(server_mod, "open_in_app", opened.append)
    await mixed(scripted, page)
    shop, notes = row(page, "Coffee Shop"), row(page, "Field notes")
    more = shop.locator(".art-more > summary")                                                 # the "..." of that item
    await expect(more).to_have_attribute("aria-label", "More ways to open Coffee Shop")
    # by keyboard: the item, then its "...", Enter opens the menu, Escape shuts it and stays there
    await shop.get_by_role("button", name="Open", exact=True).focus()
    await page.keyboard.press("Tab")
    await expect(more).to_be_focused()
    assert await more.evaluate("s => getComputedStyle(s).opacity") == "1"                      # shown for the keyboard too
    await page.keyboard.press("Enter")
    acts = shop.locator(".art-more .acts")
    await expect(acts.get_by_role("button", name="Open beside chat")).to_be_visible()
    await expect(acts.get_by_role("link", name="Open in browser ↗")).to_be_visible()
    await expect(acts.get_by_role("link", name="Download")).to_be_visible()
    inside = await acts.evaluate("a => { const r = a.getBoundingClientRect(); return r.x >= 0 && r.right <= innerWidth && r.width >= 180; }")
    assert inside
    await page.keyboard.press("Escape")
    await expect(acts).to_be_hidden()
    await expect(more).to_be_focused()
    # by pointer: one menu at a time, and a click elsewhere shuts it
    await menu(shop)
    await menu(notes)
    await expect(shop.locator(".art-more")).not_to_have_attribute("open", "")
    await expect(notes.locator(".art-more")).to_have_attribute("open", "")
    await page.locator("#studio-empty h1").click()
    await expect(notes.locator(".art-more")).not_to_have_attribute("open", "")
    # Open in app says what happened on the item itself
    await (await menu(notes)).get_by_role("button", name="Open in app").click()
    await expect(notes.locator(".said")).to_have_text("Opened notes.md in its application.")
    assert opened == [scripted.ws.resolve() / "notes.md"]
    # a redraw with nothing new moves nothing: the same list, the same frame (a moved frame would load its page again)
    await page.evaluate("""() => { const ul = document.querySelector('#art-home-list ul'); ul.__same = true;
        document.querySelector('#art-home-list iframe').__same = true; window.dispatchEvent(new Event('dream:artifacts')); return true; }""")
    await page.wait_for_timeout(500)
    assert await page.evaluate("[document.querySelector('#art-home-list ul').__same === true, document.querySelector('#art-home-list iframe').__same === true]") == [True, True]
    # something new while a menu is open and the keyboard is on it: the list is redrawn, the menu and the focus stay
    await shop.get_by_role("button", name="Open", exact=True).focus()
    await page.keyboard.press("Tab")
    await page.keyboard.press("Enter")
    await expect(shop.locator(".art-more")).to_have_attribute("open", "")
    await scripted.turn("and the about page", [("write_file", {"path": "coffee-shop/about.html", "content": ABOUT})])
    await expect(page.locator(ROWS)).to_have_count(6)
    await expect(shop.locator(".art-more")).to_have_attribute("open", "")
    await expect(more).to_be_focused()
    assert await page.evaluate("document.documentElement.dataset.dreamView") == "studio"       # and nothing opened by itself


async def test_a_phone_width_window_has_two_columns_and_nothing_pushes_the_page_sideways(build):
    scripted, page, _, _ = build
    await mixed(scripted, page)
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.wait_for_timeout(200)
    grid = await page.evaluate("""() => { const xs = new Set([...document.querySelectorAll('#art-home-list .art-row')].map(r => Math.round(r.getBoundingClientRect().x)));
        const f = document.querySelector('#art-home-list iframe'), t = f.parentElement;
        return {columns: xs.size, sw: document.documentElement.scrollWidth, iw: innerWidth, fills: Math.abs(f.getBoundingClientRect().width - t.clientWidth) <= 2,
                head: document.querySelector('#studio-empty .art-head').scrollWidth <= document.querySelector('#studio-empty .art-head').clientWidth + 1}; }""")
    assert grid["columns"] == 2 and grid["sw"] <= grid["iw"] and grid["fills"] and grid["head"], grid
    await page.get_by_role("button", name="List", exact=True).click()
    row_fits = await page.evaluate("""() => [...document.querySelectorAll('#art-home-list .art-row')].every(r => r.getBoundingClientRect().right <= innerWidth)
        && document.documentElement.scrollWidth <= innerWidth""")
    assert row_fits
    await expect(row(page, "Coffee Shop").locator(".side")).to_be_hidden()                     # the row keeps its title's room
