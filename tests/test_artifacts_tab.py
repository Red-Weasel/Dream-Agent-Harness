"""The Artifacts tab (DREAM-235): the nav destination the Studio tab was, under its new name. Its home lists what
Dream built in this project -- newest first, under the day or month it was built (DREAM-241; this session's items
carry a badge) -- with each artifact's title, kind, version, age and size, and the ways to open it: in the tab at
full width (the item itself: the same frame and Preview | Code as the panel) and, under its "..." menu, beside the
chat, in the browser or its application, as a download. What the Studio tab did for the
other screens keeps working: a show from another view opens the tab's viewer, the open artifact follows the owner
back to Chat, a question brings the conversation back, Compare and the media history are reached from the tab. The
scripted session of tests/test_chat_artifact_panel.py. Real Chromium; no model, no engine."""
from __future__ import annotations

import re

from playwright.async_api import expect

from dream.core.backends.base import Event
from dream.gui.artifacts import ArtifactRegistry
from test_chat_artifact_kinds import png, shell
from test_chat_artifact_panel import ABOUT, COFFEE, GEOMETRY, build, shot  # noqa: F401  (build is the fixture)

ROWS = "#art-home-list .art-row"


def row(page, title):
    return page.locator(ROWS, has_text=title)


def size(n):
    """index.html's fileSize()."""
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{n / 1024:.1f} KB" if n >= 1024 else f"{n} bytes"


async def menu(item):
    """An item's "..." menu, opened: the other ways to open it (DREAM-241)."""
    await item.hover()
    await item.locator(".art-more > summary").click()
    return item.locator(".art-more .acts")


async def rows(page):
    return [" ".join(t.split()) for t in await page.locator(ROWS + " .t").all_inner_texts()]


async def test_the_nav_entry_reads_artifacts_and_its_home_says_what_will_appear(build):
    _, page, _, _ = build
    entry = page.locator("#dream-nav-studio")
    await expect(entry).to_have_attribute("aria-label", "Go to Artifacts")
    await expect(entry).to_have_attribute("title", "Artifacts")
    assert "Studio" not in await page.locator("#dream-nav").inner_text()
    await entry.click()
    g = await page.evaluate(GEOMETRY)
    assert g["view"] == "studio" and g["nav"] == "dream-nav-studio", g                  # the view id is the Studio tab's
    home = page.locator("#studio-empty")
    await expect(home).to_be_visible()
    await expect(home.get_by_role("heading", level=1)).to_have_text("Artifacts")
    await expect(home).to_contain_text("Nothing built yet")
    await expect(home).to_contain_text("Ask Dream to build a page, write a report, or make an image, a video or a document. "
                                       "Each one opens beside the chat as it is built and is kept here with its versions.")
    await expect(page.locator(ROWS)).to_have_count(0)
    assert "Your Studio" not in await home.inner_text()
    for control in ("#art-filters", "#art-search", "#art-view-grid"):                   # nothing to filter, search or lay out yet
        await expect(page.locator(control)).to_be_hidden()
    await expect(page.locator("#dream-output-tools")).to_be_hidden()                    # the canvas's tools wait for an artifact
    await expect(page.locator("#input")).to_be_visible()                                # asking for one is right there
    assert g["panel"]["w"] >= g["split"]["w"] - 4, g                                    # the whole tab
    await shot(page, "tab-home-empty-1100x800.png")
    # the command palette and the classic bar name the same place
    await page.keyboard.press("Control+k")
    await page.locator("#dream-command-search").fill("art")
    await expect(page.locator("#dream-command-list button")).to_have_text(["Artifacts"])
    await page.locator("#dream-command-list button").click()
    assert (await page.evaluate(GEOMETRY))["view"] == "studio"
    assert await page.locator("#studio-preview").text_content() == "Artifacts"
    # the media history is reached from the home
    await home.get_by_role("button", name="Media history").click()
    await expect(page.locator("#dream-compare h2")).to_have_text("Registered output history")
    await page.locator("#dream-compare").get_by_role("button", name="Close").click()


async def test_the_list_shows_what_was_built_newest_first_with_kind_versions_age_and_its_actions(build):
    scripted, page, _, _ = build
    await scripted.turn("build the shop", [("write_file", {"path": "coffee-shop/index.html", "content": COFFEE})])
    await scripted.turn("write it up", [("write_file", {"path": "notes.md", "content": "# Field notes\n"})])
    await scripted.turn("chart it", [], end=False)
    await shell(scripted, {"out/chart.png": png()})
    scripted.end()
    await scripted.turn("darker header", [("str_replace_edit", {"path": "coffee-shop/index.html", "old_string": "#6b3e26", "new_string": "#2b1a10"})])
    await expect(page.locator("#stream .artcard")).to_have_count(3)
    await page.click("#dream-nav-studio")
    await expect(page.locator(ROWS)).to_have_count(3)
    shop_size, chart_size = size(len(COFFEE.encode())), size(len(png()))
    assert await rows(page) == [f"Coffee Shop Page · v2 · just now · {shop_size}", f"chart.png Image · v1 · just now · {chart_size}",
                                "Field notes Report · v1 · just now · 14 bytes"]        # the newest change first
    await expect(page.locator("#art-home-list h2")).to_have_text(["Today"])
    shop, notes = row(page, "Coffee Shop"), row(page, "Field notes")
    for item in (shop, notes):                                                          # the item itself opens it in the tab
        await expect(item.get_by_role("button", name="Open", exact=True)).to_have_count(1)
    assert [" ".join(t.split()) for t in await shop.locator(".acts > *").all_text_contents()] == [
        "Open beside chat", "Open in browser ↗", "Download"]
    assert [" ".join(t.split()) for t in await notes.locator(".acts > *").all_text_contents()] == [
        "Open beside chat", "Open in app", "Download"]
    await menu(shop)
    link = shop.get_by_role("link", name="Open in browser ↗")
    href = await link.get_attribute("href")
    assert href == scripted.srv.pages.base_url + "coffee-shop/index.html" and scripted.srv.token not in href
    assert await link.get_attribute("target") == "_blank" and await link.get_attribute("rel") == "noopener noreferrer"
    await expect(shop.get_by_role("link", name="Download")).to_have_attribute(
        "href", re.compile(r"^/api/download\?path=coffee-shop%2Findex\.html&token="))
    await menu(notes)
    await expect(shop.locator(".art-more")).not_to_have_attribute("open", "")           # one menu at a time
    async with page.expect_download() as waiting:
        await notes.get_by_role("link", name="Download").click()
    assert (await waiting.value).suggested_filename == "notes.md"
    assert (await page.evaluate(GEOMETRY))["view"] == "studio"
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    await shot(page, "tab-home-list-1100x800.png")
    # the list follows the session while it is showing
    await scripted.turn("and the about page", [("write_file", {"path": "coffee-shop/about.html", "content": ABOUT})])
    await expect(page.locator(ROWS)).to_have_count(4)
    assert (await rows(page))[0] == f"About Us Page · v1 · just now · {size(len(ABOUT.encode()))}"
    await page.wait_for_timeout(300)
    assert (await page.evaluate(GEOMETRY))["view"] == "studio"
    assert await page.evaluate("artOpen") == "file:coffee-shop/index.html", "a build while the owner reads the list opens nothing"
    await expect(page.locator("#studio-empty")).to_be_visible()


async def test_open_shows_it_in_the_tab_at_full_width_and_the_nav_entry_and_close_return_to_the_list(build):
    scripted, page, _, _ = build
    await scripted.turn("build the shop", [("write_file", {"path": "coffee-shop/index.html", "content": COFFEE})])
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("Beans & Steam")   # beside the chat
    await page.click("#dream-nav-studio")
    await expect(page.locator(ROWS)).to_have_count(1)
    await expect(page.locator("#artbody")).to_be_hidden()                               # the list, not the canvas
    await expect(row(page, "Coffee Shop")).to_have_class(re.compile(r"\bnow\b"))        # the one that is open
    await row(page, "Coffee Shop").get_by_role("button", name="Open", exact=True).click()
    frame = page.frame_locator("#artbody iframe")
    await expect(frame.locator("#h")).to_have_text("Beans & Steam")
    g = await page.evaluate(GEOMETRY)
    assert g["view"] == "studio" and g["nav"] == "dream-nav-studio" and not g["chatArt"], g
    assert g["panel"]["w"] >= g["split"]["w"] - 4 and g["main"]["w"] == 0, g            # full width: no conversation column
    await expect(page.locator("#studio-empty")).to_be_hidden()
    assert await page.locator("#artbody iframe").get_attribute("sandbox") == "allow-scripts"   # the same frame as the panel
    await page.locator("#artcode").click()
    assert await page.locator("#artbody pre").text_content() == COFFEE
    await page.locator("#artprev").click()
    await expect(frame.locator("#h")).to_have_text("Beans & Steam")
    await expect(page.locator("#dream-output-expand")).to_be_visible()                  # the tab's tools are here
    await expect(page.locator("#input")).to_be_visible()
    await shot(page, "tab-viewer-1100x800.png")
    # the nav entry is the way back to the list; the page stays loaded under it
    await page.click("#dream-nav-studio")
    await expect(page.locator(ROWS)).to_have_count(1)
    await expect(page.locator("#artbody")).to_be_hidden()
    await expect(page.locator("#artbody iframe")).to_have_count(1)
    await row(page, "Coffee Shop").get_by_role("button", name="Open", exact=True).click()
    await expect(frame.locator("#h")).to_have_text("Beans & Steam")
    # closing in the tab is the list again
    await page.locator("#artclose").click()
    await expect(page.locator("#artbody iframe")).to_have_count(0)
    await expect(page.locator(ROWS)).to_have_count(1)
    await expect(row(page, "Coffee Shop")).not_to_have_class(re.compile(r"\bnow\b"))
    assert (await page.evaluate(GEOMETRY))["view"] == "studio"


async def test_open_beside_chat_and_coming_back_to_chat_put_the_artifact_in_the_panel_there(build):
    scripted, page, _, _ = build
    await scripted.turn("build the shop", [("write_file", {"path": "coffee-shop/index.html", "content": COFFEE})])
    await scripted.turn("and the about page", [("write_file", {"path": "about/index.html", "content": ABOUT})])
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("About")
    await page.locator("#artclose").click()
    await page.click("#dream-nav-studio")
    await (await menu(row(page, "Coffee Shop"))).get_by_role("button", name="Open beside chat").click()
    frame = page.frame_locator("#artbody iframe")
    await expect(frame.locator("#h")).to_have_text("Beans & Steam")
    g = await page.evaluate(GEOMETRY)
    assert g["view"] == "chat" and g["nav"] == "dream-nav-chat" and g["chatArt"] and g["drawer"], g
    assert g["panel"]["x"] >= g["main"]["r"] - 1 and g["main"]["w"] >= 360, g
    await expect(page.locator("#artname")).to_have_text("Coffee Shop")
    # from the tab's viewer back to Chat: the same frame comes along, not rebuilt
    await page.click("#dream-nav-studio")
    await row(page, "About Us").get_by_role("button", name="Open", exact=True).click()
    await expect(frame.locator("#h")).to_have_text("About")
    await page.evaluate("() => { document.querySelector('#artbody iframe').__mark = 'same'; return true; }")
    await page.click("#dream-nav-chat")
    g = await page.evaluate(GEOMETRY)
    assert g["view"] == "chat" and g["chatArt"] and g["panelShown"] and g["panel"]["x"] >= g["main"]["r"] - 1, g
    assert await page.evaluate("document.querySelector('#artbody iframe').__mark") == "same"
    await expect(page.locator("#artname")).to_have_text("About Us")
    await expect(page.locator("#artmore")).to_be_visible()                              # the panel's own header
    await expect(page.locator("#dream-output-tools")).to_be_hidden()
    # and the panel's menu hands it to the tab at full width
    await page.locator("#artmore").click()
    await page.locator("#artstudio").click()
    await expect(frame.locator("#h")).to_have_text("About")
    g = await page.evaluate(GEOMETRY)
    assert g["view"] == "studio" and not g["chatArt"] and g["main"]["w"] == 0, g


async def test_earlier_sessions_of_this_project_are_listed_below_and_open_their_stored_version(build):
    scripted, page, prompts, _ = build
    ws, store = scripted.ws, scripted.registry._store
    before = ArtifactRegistry(ws, "s0", lambda ev: None, store=store)                   # an earlier session, same workspace
    for rel, text in (("old-site/index.html", COFFEE.replace("Coffee Shop", "Old Shop").replace("Beans &amp; Steam", "The old shop")),
                      ("kept.md", "# Kept notes\n")):
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(text)
        before.observe(Event("tool_use", {"id": rel, "name": "write_file", "input": {"path": rel, "content": text}}))
        before.observe(Event("tool_result", {"id": rel, "name": "write_file", "content": "ok", "is_error": False}))
    (ws / "old-site" / "index.html").unlink()                                          # since deleted from the workspace
    await scripted.turn("build the shop", [("write_file", {"path": "coffee-shop/index.html", "content": COFFEE})])
    await page.locator("#artclose").click()
    await page.click("#dream-nav-studio")
    await expect(page.locator("#art-home-list h2")).to_have_text(["Today"])            # by day, not by session (DREAM-241)
    old_size = size(len(COFFEE.replace("Coffee Shop", "Old Shop").replace("Beans &amp; Steam", "The old shop").encode()))
    assert await rows(page) == [f"Coffee Shop Page · v1 · just now · {size(len(COFFEE.encode()))}", "Kept notes Report · v1 · just now · 13 bytes",
                                f"Old Shop Page · v1 · just now · {old_size} The file is no longer in the workspace."]
    await expect(row(page, "Coffee Shop").locator(".badge")).to_have_text("this session")   # the running session's are marked
    assert await page.locator(ROWS + " .badge").count() == 1
    old = row(page, "Old Shop")
    await expect(old.locator(".gone")).to_have_text("The file is no longer in the workspace.")
    assert await old.locator("a").count() == 0, "no link to a file that is gone"
    assert await old.locator(".acts > *").all_text_contents() == ["Open beside chat"]   # its stored version still opens
    await expect((await menu(row(page, "Kept notes"))).get_by_role("link", name="Download")).to_be_visible()
    await page.keyboard.press("Escape")
    await shot(page, "tab-home-earlier-1100x800.png")
    requests = []
    page.on("request", lambda r: requests.append(r.url) if "/api/artifact/" in r.url else None)
    await old.get_by_role("button", name="Open", exact=True).click()
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("The old shop")   # that session's bytes
    assert any(u.endswith("?v=1&session=s0") for u in requests), requests
    g = await page.evaluate(GEOMETRY)
    assert g["view"] == "studio" and g["main"]["w"] == 0, g
    await expect(page.locator("#artext")).to_be_hidden()                                # nothing to open in a browser
    # a message sent with it open names no artifact to the model: the id is another session's
    await page.locator("#input").fill("what is this page?")
    await page.locator("#send").click()
    await expect(page.locator("#stream .msg.you .body").last).to_have_text("what is this page?")
    assert prompts[-1] == "what is this page?", prompts[-1]


async def test_a_show_from_another_view_opens_the_tabs_viewer_and_a_question_brings_the_conversation_back(build):
    scripted, page, _, _ = build
    await page.click("#dream-nav-files")
    await expect(page.locator("#dream-files-page")).to_be_visible()
    (scripted.ws / "report.html").write_text(ABOUT)
    await scripted.turn("show the report", [("done", {"path": "report.html"})], end=False)
    frame = page.frame_locator("#artbody iframe")
    await expect(frame.locator("#h")).to_have_text("About")
    g = await page.evaluate(GEOMETRY)
    assert g["view"] == "studio" and g["nav"] == "dream-nav-studio" and not g["chatArt"], g      # the handoff, as the Studio tab took it
    await expect(page.locator("#studio-empty")).to_be_hidden()                          # its viewer, not its list
    scripted.end()
    # the owner looks at the list; a question from Dream must not wait unseen behind it
    await page.click("#dream-nav-studio")
    await expect(page.locator(ROWS)).to_have_count(1)
    scripted.funnel(Event("studio", {"op": "ask", "form": {"title": "Deck", "questions": [
        {"id": "tone", "title": "Which tone?", "kind": "text-options", "options": ["Plain", "Bold"]}]}}))
    await expect(page.locator("#stream .qform")).to_be_visible()
    await expect(page.locator("#stream .qform")).to_contain_text("Which tone?")
    g = await page.evaluate(GEOMETRY)
    assert g["view"] == "chat" and g["nav"] == "dream-nav-chat" and g["drawer"], g      # from the list: the Chat view


async def test_a_message_sent_from_the_list_goes_to_chat_where_the_build_opens_beside_it(build):
    scripted, page, prompts, _ = build
    await page.click("#dream-nav-studio")
    await expect(page.locator("#studio-empty")).to_contain_text("Nothing built yet")
    await page.locator("#input").fill("build a landing page for a coffee shop")
    await page.locator("#send").click()
    await expect(page.locator("#stream .msg.you .body").last).to_have_text("build a landing page for a coffee shop")
    g = await page.evaluate(GEOMETRY)
    assert g["view"] == "chat" and g["nav"] == "dream-nav-chat" and g["drawer"], g
    assert prompts == ["build a landing page for a coffee shop"]
    await page.wait_for_timeout(3100)                                                   # the model takes longer than the typing window
    scripted.funnel(Event("turn_start", {}))
    await scripted.call("write_file", {"path": "coffee-shop/index.html", "content": COFFEE})
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("Beans & Steam")
    g = await page.evaluate(GEOMETRY)
    assert g["view"] == "chat" and g["chatArt"] and g["panel"]["x"] >= g["main"]["r"] - 1, g
    scripted.end()


async def test_compare_in_the_tab_reads_a_registry_artifacts_two_latest_versions(build):
    scripted, page, _, _ = build
    await scripted.turn("build the shop", [("write_file", {"path": "coffee-shop/index.html", "content": COFFEE})])
    await scripted.turn("rename the roast", [("str_replace_edit", {"path": "coffee-shop/index.html", "old_string": "Flat white", "new_string": "Cortado"})])
    await expect(page.locator("#artvn")).to_have_text("v2")
    await page.reload()                                                                 # this page now holds no version
    await expect(page.locator("#stat")).to_have_text("Ready")
    await page.click("#dream-nav-studio")
    await row(page, "Coffee Shop").get_by_role("button", name="Open", exact=True).click()
    await expect(page.frame_locator("#artbody iframe").locator("li").nth(1)).to_have_text("Cortado")
    compare = page.locator("#dream-output-compare")
    await expect(compare).to_be_enabled()
    await compare.click()
    columns = page.locator("#dream-compare .dream-compare-grid section")
    await expect(columns).to_have_count(2)
    await expect(columns.nth(0).locator("h3")).to_have_text("Coffee Shop · v1")
    await expect(columns.nth(0).locator("pre")).to_contain_text("Flat white")
    await expect(columns.nth(1).locator("pre")).to_contain_text("Cortado")


def mirrored(scripted, text):
    """The model's own view (DREAM-104): what `show_html` sends the pane while the owner follows it."""
    scripted.funnel(Event("studio", {"op": "show", "path": "page.html", "title": "page.html", "source": "mirror",
                                     "content": f"<!doctype html><title>page.html</title><h1 id='h'>{text}</h1>"}))


async def test_the_models_own_view_opens_beside_the_chat_and_never_while_the_owner_types(build):
    scripted, page, _, _ = build
    await page.locator("#input").click()
    await page.keyboard.type("a correction I am typing")
    mirrored(scripted, "one")
    await page.wait_for_timeout(300)
    g = await page.evaluate(GEOMETRY)
    assert not g["panelShown"] and g["view"] == "chat", g                              # it waits: nothing moves under the owner
    assert await page.input_value("#input") == "a correction I am typing"
    await page.locator("#input").fill("")
    await page.evaluate("() => { document.activeElement.blur(); return true; }")
    await page.wait_for_timeout(3100)                                                   # past the typing window
    mirrored(scripted, "two")
    frame = page.frame_locator("#artbody iframe")
    await expect(frame.locator("#h")).to_have_text("two")
    g = await page.evaluate(GEOMETRY)
    assert g["view"] == "chat" and g["nav"] == "dream-nav-chat" and g["chatArt"] and g["drawer"], g   # no tab is brought up
    assert g["panel"]["x"] >= g["main"]["r"] - 1 and g["main"]["w"] >= 360, g
    # while it is showing, the model's next load replaces it, typing or not, and takes nothing
    await page.locator("#input").click()
    await page.keyboard.type("make it teal")
    mirrored(scripted, "three")
    await expect(frame.locator("#h")).to_have_text("three")
    assert await page.evaluate("[document.activeElement.id, input.value]") == ["input", "make it teal"]
    assert (await page.evaluate(GEOMETRY))["view"] == "chat"
    # what the model showed is in the Artifacts tab's list
    await page.click("#dream-nav-studio")
    await expect(row(page, "page.html")).to_have_count(1)


async def test_in_a_narrow_window_the_models_first_view_brings_the_artifacts_tab_up_once(build):
    scripted, page, _, _ = build
    await page.set_viewport_size({"width": 900, "height": 700})
    mirrored(scripted, "one")
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("one")
    g = await page.evaluate(GEOMETRY)
    assert g["view"] == "studio" and g["nav"] == "dream-nav-studio" and not g["chatArt"], g   # no room beside the chat
    await expect(page.locator("#studio-empty")).to_be_hidden()                          # the viewer, not the list
    await page.click("#dream-nav-chat")
    mirrored(scripted, "two")
    await page.wait_for_timeout(300)
    assert (await page.evaluate(GEOMETRY))["view"] == "chat", "once a session: the owner chose Chat"


async def test_a_sheet_that_is_showing_keeps_what_the_model_shows_next(build):
    scripted, page, _, _ = build
    await page.set_viewport_size({"width": 900, "height": 700})
    await page.evaluate("() => { document.activeElement.blur(); return true; }")      # a sheet needs an idle composer (A1)
    await scripted.turn("build it", [("write_file", {"path": "page.html", "content": ABOUT})])
    frame = page.frame_locator("#artbody iframe")
    await expect(frame.locator("#h")).to_have_text("About")                           # the sheet over the chat
    assert (await page.evaluate(GEOMETRY))["chatArt"]
    mirrored(scripted, "the model looks at it")
    await expect(frame.locator("#h")).to_have_text("the model looks at it")
    g = await page.evaluate(GEOMETRY)
    assert g["view"] == "chat" and g["chatArt"] and g["panelShown"], g                # it stays where it was showing
    assert await page.evaluate("getComputedStyle(document.getElementById('artpanel')).position") == "absolute"
