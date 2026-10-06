"""The panel beside the chat shows more than pages (DREAM-236): a report (`.md`, rendered), an image, a video and
an audio file (a player), and a document the frame cannot render (a card with the ways out) -- each with its card
in the conversation, the same automatic opening and never-steal rules as a page, a version on each rewrite, and
the turn's winner rules across kinds. And the panel's header keeps the title readable at the desktop's default
width (DREAM-235). The same scripted session as tests/test_chat_artifact_panel.py: the real StudioServer, the
real registry on the funnel, the real file tools. Real Chromium; no model, no engine."""
from __future__ import annotations

import base64
import io
import re
import wave

from PIL import Image
from playwright.async_api import expect

from dream.core.backends.base import Event
from test_chat_artifact_panel import COFFEE, GEOMETRY, build, card, shot  # noqa: F401  (build is the fixture)
from test_studio_media_playback import MP4

REPORT = ("# Quarterly findings\n\nSales rose **12 %** in the north.\n\n| Region | Sales |\n|---|---|\n| North | 10 |\n"
          "| South | 12 |\n\n```python\nprint('total', 22)\n```\n\n- first point\n- second point\n")


def png(width=40, height=30, colour=(30, 90, 160)):
    out = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(out, "PNG")
    return out.getvalue()


def wav(seconds=1):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(8000)
        w.writeframes(b"\0\0" * 8000 * seconds)
    return out.getvalue()


async def shell(scripted, files):
    """A run_bash call as the funnel sees it: tool_use, the files it left in the workspace, tool_result."""
    scripted.n += 1
    tid = f"shell:{scripted.n}"
    scripted.funnel(Event("tool_use", {"id": tid, "name": "run_bash", "input": {"command": "python make.py"}}))
    for rel, data in files.items():
        target = scripted.ws / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    scripted.funnel(Event("tool_result", {"id": tid, "name": "run_bash", "content": "done", "is_error": False}))


async def card_text(page, title):
    return " ".join((await card(page, title).text_content()).split())


async def test_a_report_opens_rendered_at_a_documents_width_with_its_card_code_view_and_versions(build):
    scripted, page, _, _ = build
    await scripted.turn("write up the quarter", [("write_file", {"path": "reports/q3.md", "content": REPORT})], end=False)
    doc = page.locator("#artbody .artdoc")
    await expect(doc.locator("h1")).to_have_text("Quarterly findings")
    await expect(doc.locator("strong")).to_have_text("12 %")
    await expect(doc.locator("table tbody tr")).to_have_count(2)
    await expect(doc.locator("table tbody tr").nth(1).locator("td").first).to_have_text("South")
    await expect(doc.locator(".codeblock pre code")).to_have_text("print('total', 22)")
    await expect(doc.locator(".codeblock .codecopy")).to_have_text("Copy")
    await expect(doc.locator("li")).to_have_count(2)
    await expect(page.locator("#artbody iframe")).to_have_count(0)                       # nothing runs: no frame
    g = await page.evaluate(GEOMETRY)
    assert g["view"] == "chat" and g["chatArt"] and g["panel"]["x"] >= g["main"]["r"] - 1, g   # beside the chat, like a page
    await expect(page.locator("#artname")).to_have_text("Quarterly findings")            # its first heading
    await expect(page.locator("#artvn")).to_have_text("v1")
    assert await card_text(page, "Quarterly findings") == "MD Quarterly findings · Report · v1 · Open"
    assert await page.evaluate("document.activeElement && document.activeElement.id") in ("input", ""), "no focus() call"
    await shot(page, "kinds-report-1100x800.png")
    # a readable column, centred, however wide the panel is
    await page.set_viewport_size({"width": 2200, "height": 1000})
    box = await page.evaluate("""() => { const e = document.querySelector('#artbody .artdoc'), d = e.getBoundingClientRect(),
        b = document.getElementById('artbody').getBoundingClientRect();
        return {text: parseFloat(getComputedStyle(e).width), body: b.width, left: d.x - b.x, right: b.right - d.right}; }""")
    assert box["body"] > 900 and 600 <= box["text"] <= 720 and abs(box["left"] - box["right"]) <= 16, box
    await page.set_viewport_size({"width": 1100, "height": 800})
    # Code: the source as written
    await page.locator("#artcode").click()
    assert await page.locator("#artbody pre").text_content() == REPORT
    await page.locator("#artprev").click()
    await expect(doc.locator("h1")).to_have_text("Quarterly findings")
    # a rewrite: a version, in place
    await scripted.call("str_replace_edit", {"path": "reports/q3.md", "old_string": "# Quarterly findings",
                                             "new_string": "# Quarterly findings, revised"})
    await expect(doc.locator("h1")).to_have_text("Quarterly findings, revised")
    await expect(page.locator("#artvn")).to_have_text("v2")
    assert await card_text(page, "Quarterly findings") == "MD Quarterly findings, revised · Report · v2 · Open"
    await expect(page.locator("#stream .artcard")).to_have_count(1)
    scripted.end()
    # the code block's Copy works here as in a reply
    await page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    await doc.locator(".codeblock .codecopy").click()
    await expect(doc.locator(".codeblock .codecopy")).to_have_text("Copied")


async def test_a_rewrite_of_the_open_report_keeps_the_readers_place(build):
    scripted, page, _, _ = build
    long = "# Long report\n\n" + "".join(f"## Section {n}\n\nParagraph {n} of the report.\n\n" for n in range(60))
    await scripted.turn("write the long one", [("write_file", {"path": "long.md", "content": long})], end=False)
    await expect(page.locator("#artbody .artdoc h2")).to_have_count(60)
    await page.evaluate("() => { document.getElementById('artbody').scrollTop = 700; return true; }")
    await scripted.call("str_replace_edit", {"path": "long.md", "old_string": "Paragraph 59 of", "new_string": "The last paragraph of"})
    await expect(page.locator("#artbody .artdoc")).to_contain_text("The last paragraph of the report.")
    assert await page.evaluate("document.getElementById('artbody').scrollTop") == 700
    scripted.end()


async def test_a_reports_markup_is_text_never_elements(build):
    scripted, page, _, errors = build
    nasty = "# Notes <img src=x onerror=\"window.__pwned = 1\">\n\n<script>window.__pwned = 2</script>\n\n[x](javascript:alert(1))\n"
    await scripted.turn("notes", [("write_file", {"path": "notes.md", "content": nasty})])
    doc = page.locator("#artbody .artdoc")
    await expect(doc.locator("h1")).to_contain_text("<img src=x")
    assert await doc.locator("img, script, a").count() == 0
    assert await page.evaluate("window.__pwned") is None


async def test_an_image_a_command_made_shows_fitted_with_its_card_and_a_version_on_each_rewrite(build):
    scripted, page, _, _ = build
    await scripted.turn("plot the sales", [], end=False)
    await shell(scripted, {"out/chart.png": png(1600, 1200)})
    img = page.locator("#artbody img")
    await expect(img).to_have_js_property("naturalWidth", 1600)
    fit = await page.evaluate("""() => { const i = document.querySelector('#artbody img').getBoundingClientRect(),
        b = document.getElementById('artbody').getBoundingClientRect();
        return {iw: i.width, ih: i.height, inside: i.x >= b.x - 1 && i.right <= b.right + 1 && i.y >= b.y - 1 && i.bottom <= b.bottom + 1}; }""")
    assert fit["inside"] and abs(fit["iw"] / fit["ih"] - 4 / 3) < 0.02, fit                 # the whole picture, its own shape
    await expect(page.locator("#artbody .stillbar")).to_have_count(0)                      # not a still of anything
    await expect(page.locator("#artname")).to_have_text("chart.png")
    assert await card_text(page, "chart.png") == "IMG chart.png · Image · v1 · Open"
    g = await page.evaluate(GEOMETRY)
    assert g["view"] == "chat" and g["chatArt"], g
    await expect(page.locator("#artnote")).to_contain_text("image · out/chart.png")
    await shot(page, "kinds-image-1100x800.png")
    first = await img.get_attribute("src")
    assert "/api/download?path=out%2Fchart.png" in first
    await shell(scripted, {"out/chart.png": png(800, 200, (200, 60, 60))})
    await expect(page.locator("#artbody img")).to_have_js_property("naturalWidth", 800)   # the rewritten file, loaded again
    assert await page.locator("#artbody img").get_attribute("src") != first
    await expect(page.locator("#artvn")).to_have_text("v2")
    assert await card_text(page, "chart.png") == "IMG chart.png · Image · v2 · Open"
    scripted.end()


async def test_a_video_and_an_audio_file_get_a_player(build):
    scripted, page, _, _ = build
    await scripted.turn("render the clip", [], end=False)
    await shell(scripted, {"media/clip.mp4": base64.b64decode(MP4)})
    video = page.locator("#artbody video")
    await expect(video).to_be_visible()
    assert await video.get_attribute("controls") is not None
    await expect(video).to_have_js_property("videoWidth", 32)
    await video.evaluate("v => { v.muted = true; return v.play(); }")
    await video.evaluate("""v => new Promise((resolve, reject) => { if(v.currentTime > 0) return resolve();
        const t = setTimeout(() => reject(new Error('the clock did not advance')), 3000);
        v.addEventListener('timeupdate', () => { if(v.currentTime > 0){ clearTimeout(t); resolve(); } }); })""")
    await expect(page.locator("#artbody iframe")).to_have_count(0)
    await expect(page.locator("#artpanel .seg")).to_be_hidden()                            # nothing to show as code
    assert await card_text(page, "clip.mp4") == "VID clip.mp4 · Video · v1 · Open"
    await shot(page, "kinds-video-1100x800.png")
    scripted.end()
    await scripted.turn("and the voice-over", [], end=False)
    await shell(scripted, {"media/voice.wav": wav()})
    audio = page.locator("#artbody audio")
    await expect(audio).to_be_visible()
    await expect(audio).to_have_js_property("duration", 1)
    assert await card_text(page, "voice.wav") == "AUD voice.wav · Audio · v1 · Open"
    await expect(page.locator("#artname")).to_have_text("voice.wav")
    scripted.end()


async def test_a_media_file_over_the_cap_or_one_the_browser_cannot_play_says_so_on_a_card(build):
    scripted, page, _, _ = build
    await scripted.turn("render the long one", [], end=False)
    await shell(scripted, {"media/long.mp4": b"\0" * (9 * 1024 * 1024)})
    file = page.locator("#artbody .artfile")
    await expect(file).to_contain_text("long.mp4")
    await expect(file.locator(".why")).to_have_text("This video is 9.0 MB: the preview plays files up to 8 MB.")
    await expect(file.get_by_role("button", name="Open in app")).to_be_visible()
    await expect(file.get_by_role("link", name="Download")).to_have_attribute("href", re.compile(r"/api/download\?path=media%2Flong\.mp4&token="))
    await expect(page.locator("#artbody video")).to_have_count(0)
    scripted.end()
    await scripted.turn("and a broken one", [], end=False)
    await shell(scripted, {"media/broken.webm": b"this is not a video"})
    await expect(page.locator("#artbody .artfile .why")).to_have_text(
        "This video could not be played here (the browser reported a media error).")
    await expect(page.locator("#artbody video")).to_have_count(0)
    scripted.end()


async def test_a_document_gets_a_card_with_open_in_app_and_download_and_never_a_blank_frame(build, monkeypatch):
    from dream.gui import server as server_mod

    opened = []
    monkeypatch.setattr(server_mod, "open_in_app", opened.append)
    scripted, page, _, _ = build
    deck = b"PK" + bytes(range(256)) * 8
    await scripted.turn("make the deck", [], end=False)
    await shell(scripted, {"decks/launch.pptx": deck})
    file = page.locator("#artbody .artfile")
    await expect(file.locator("b")).to_have_text("launch.pptx")
    await expect(file).to_contain_text("Document · 2.0 KB · decks/launch.pptx")
    await expect(file.locator(".why")).to_have_text("The preview cannot render this kind of file here.")
    await expect(page.locator("#artbody iframe")).to_have_count(0)
    await expect(page.locator("#artpanel .seg")).to_be_hidden()
    assert await card_text(page, "launch.pptx") == "DOC launch.pptx · Document · v1 · Open"
    g = await page.evaluate(GEOMETRY)
    assert g["view"] == "chat" and g["chatArt"] and g["panelShown"], g
    await shot(page, "kinds-document-1100x800.png")
    await file.get_by_role("button", name="Open in app").click()
    await expect(file.locator(".said")).to_have_text("Opened launch.pptx in its application.")
    assert opened == [scripted.ws.resolve() / "decks" / "launch.pptx"]
    async with page.expect_download() as waiting:
        await file.get_by_role("link", name="Download").click()
    download = await waiting.value
    assert download.suggested_filename == "launch.pptx"
    assert open(await download.path(), "rb").read() == deck

    def refuse(path):
        raise OSError("this machine has no xdg-open")

    monkeypatch.setattr(server_mod, "open_in_app", refuse)
    await file.get_by_role("button", name="Open in app").click()
    await expect(file.locator(".said")).to_have_text(
        "launch.pptx could not be opened in an application: this machine has no xdg-open. Download it instead.")
    await expect(file.locator(".said")).to_have_class(re.compile(r"\bbad\b"))
    scripted.end()
    for name in ("paper.pdf", "letter.docx", "sheet.xlsx"):                                # every document kind: the same card
        await scripted.turn("another", [], end=False)
        await shell(scripted, {f"decks/{name}": b"%PDF-1.7 " + name.encode()})
        await expect(page.locator("#artbody .artfile b")).to_have_text(name)
        await expect(page.locator("#artbody iframe")).to_have_count(0)
        scripted.end()


async def test_one_turn_a_page_over_a_report_over_an_image_and_every_one_has_its_card(build):
    scripted, page, _, _ = build
    await scripted.turn("chart it, write it up, and build the viewer", [], end=False)
    await shell(scripted, {"chart.png": png()})
    await expect(page.locator("#artbody img")).to_have_js_property("naturalWidth", 40)      # the first thing built opens
    await scripted.call("write_file", {"path": "notes.md", "content": "# Notes\n\nSee the chart.\n"})
    await expect(page.locator("#artbody .artdoc h1")).to_have_text("Notes")                # a report takes the image's place
    await scripted.call("write_file", {"path": "viewer.html", "content": COFFEE})
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("Beans & Steam")   # and a page the report's
    await shell(scripted, {"second.png": png()})
    await scripted.call("write_file", {"path": "more.md", "content": "# More\n"})
    await page.wait_for_timeout(300)
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("Beans & Steam")   # nothing lesser replaces it
    await expect(page.locator("#stream .artcard")).to_have_count(5)
    labels = [" ".join(t.split()) for t in await page.locator("#stream .artcard").all_text_contents()]
    assert labels == ["IMG chart.png · Image · v1 · Open", "MD Notes · Report · v1 · Open", "</> Coffee Shop · Page · v1 · Open",
                      "IMG second.png · Image · v1 · Open", "MD More · Report · v1 · Open"], labels
    scripted.end()
    await card(page, "More").get_by_role("button", name="Open").click()                    # a card re-opens its own kind
    await expect(page.locator("#artbody .artdoc h1")).to_have_text("More")
    assert (await page.evaluate(GEOMETRY))["view"] == "chat"


async def test_an_explicit_done_on_a_report_wins_over_the_page_and_says_what_the_panel_shows(build):
    scripted, page, _, _ = build
    await scripted.turn("build the site and summarise", [
        ("write_file", {"path": "site/index.html", "content": COFFEE}),
        ("write_file", {"path": "summary.md", "content": "# Summary\n\nDone.\n"}),
    ], end=False)
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("Beans & Steam")   # the page holds the turn
    await expect(card(page, "Summary")).to_have_count(1)
    res = await scripted.call("done", {"path": "summary.md"})
    assert not res.get("is_error") and "preview panel shows it as a report" in res["content"][0]["text"], res
    await expect(page.locator("#artbody .artdoc h1")).to_have_text("Summary")              # the model's choice wins
    assert (await page.evaluate(GEOMETRY))["view"] == "chat"
    await scripted.call("str_replace_edit", {"path": "site/index.html", "old_string": "Flat white", "new_string": "Cortado"})
    await page.wait_for_timeout(300)
    await expect(page.locator("#artbody .artdoc h1")).to_have_text("Summary")              # and a later write does not take it back
    scripted.end()


async def test_typing_or_another_view_gives_a_report_a_card_only_and_an_explicit_show_there_opens_the_tab(build):
    scripted, page, _, _ = build
    await page.locator("#input").click()
    await page.keyboard.type("one more thing")
    await scripted.turn("write it up", [("write_file", {"path": "notes.md", "content": "# Notes\n"})])
    c = card(page, "Notes")
    await expect(c).to_have_class(re.compile(r"\bpulse\b"))
    await page.wait_for_timeout(300)
    assert not (await page.evaluate(GEOMETRY))["panelShown"]
    assert await page.input_value("#input") == "one more thing"
    await page.locator("#input").fill("")
    await page.click("#dream-nav-files")
    await expect(page.locator("#dream-files-page")).to_be_visible()
    await scripted.turn("and the figures", [], end=False)
    await shell(scripted, {"figures/a.png": png()})
    await page.wait_for_timeout(300)
    assert (await page.evaluate(GEOMETRY))["view"] == "files", "an automatic open never leaves the view the owner chose"
    res = await scripted.call("done", {"path": "notes.md"})
    assert not res.get("is_error"), res
    await expect(page.locator("#artbody .artdoc h1")).to_have_text("Notes")                # the handoff: the Artifacts tab's viewer
    g = await page.evaluate(GEOMETRY)
    assert g["view"] == "studio" and g["nav"] == "dream-nav-studio" and not g["chatArt"], g
    scripted.end()


async def test_a_streamed_report_opens_as_its_source_and_renders_when_it_lands_and_dreams_plan_file_does_not(build):
    scripted, page, _, _ = build
    head = '<tool_call>{"name": "write_file", "arguments": {"path": "notes/plan-b.md", "content": "# Plan B'
    await scripted.turn("write plan b", [], end=False, preview=[head, "\\n\\nFirst we", " measure."])
    await expect(page.locator("#artbody pre")).to_contain_text("First we measure.")           # the source, streaming
    await expect(page.locator("#stream .artcard")).to_have_count(0)
    await scripted.call("write_file", {"path": "notes/plan-b.md", "content": "# Plan B\n\nFirst we measure.\n"})
    await expect(page.locator("#artbody .artdoc h1")).to_have_text("Plan B")                 # rendered once the file is there
    assert await card_text(page, "Plan B") == "MD Plan B · Report · v1 · Open"
    scripted.end()
    await page.locator("#artclose").click()
    head = '<tool_call>{"name": "write_file", "arguments": {"path": "PLAN.md", "content": "# Plan'
    await scripted.turn("update the plan", [], end=False, preview=[head, "\\n\\n- [ ] step"])
    await scripted.call("write_file", {"path": "PLAN.md", "content": "# Plan\n\n- [ ] step\n"})
    await page.wait_for_timeout(300)
    assert not (await page.evaluate(GEOMETRY))["panelShown"], "Dream's own plan file is not an artifact"
    await expect(page.locator("#stream .artcard")).to_have_count(1)
    scripted.end()


# --- the header beside the chat (DREAM-235): the title keeps its room -------------------------------------------

HEADER = """() => {
  const bar = document.querySelector('#artpanel .artbar'), b = bar.getBoundingClientRect();
  const box = e => { const r = e.getBoundingClientRect(); return {id: e.id || e.className, x: r.x, r: r.right, top: r.top, w: r.width, h: r.height,
      clipped: e.scrollWidth > e.clientWidth + 1, shown: r.width > 0 && r.height > 0}; };
  const all = [...bar.querySelectorAll(':scope > *, #artmenu > *')].map(box);
  return {bar: {x: b.x, r: b.right, h: b.height}, by: Object.fromEntries(all.map(i => [i.id, i])),
          seg: [...bar.querySelectorAll('.seg button')].map(box), menu: box(document.getElementById('artmenu'))};
}"""


async def test_at_the_default_width_the_title_has_room_and_the_secondary_actions_are_one_menu(build):
    scripted, page, _, _ = build
    await scripted.turn("build it", [("write_file", {"path": "coffee-shop/index.html", "content": COFFEE})])
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("Beans & Steam")
    await expect(page.locator("#artext")).to_be_visible()
    h = await page.evaluate(HEADER)
    by = h["by"]
    assert by["artname"]["w"] >= 200 and not by["artname"]["clipped"], by["artname"]        # "Coffee Shop" in full (it read "C...")
    assert [b["shown"] and not b["clipped"] for b in h["seg"]] == [True, True], h["seg"]    # Preview | Code whole (it read "Pr")
    assert by["artext"]["shown"] and by["artext"]["h"] <= 34, by["artext"]                  # one line (it wrapped onto three)
    assert h["bar"]["h"] <= 52, h["bar"]                                                    # one row
    shown = [i for i in by.values() if i["shown"]]
    assert all(i["x"] >= h["bar"]["x"] - 1 and i["r"] <= h["bar"]["r"] + 1 for i in shown), shown
    assert by["artmore"]["shown"] and not any(by[i]["shown"] for i in ("artrefresh", "artcopy", "artdl", "artstudio", "artauto")), by
    await expect(page.locator("#artext")).to_have_text("Open in browser ↗")                # its words stay its name
    await shot(page, "header-1100x800.png")
    # the menu
    more = page.locator("#artmore")
    await more.click()
    await expect(more).to_have_attribute("aria-expanded", "true")
    for name in ("Refresh", "Copy", "Download", "Artifacts"):
        await expect(page.locator("#artmenu").get_by_text(name, exact=True)).to_be_visible()
    await expect(page.locator("#artauto")).to_be_visible()
    h = await page.evaluate(HEADER)
    inner = await page.evaluate("[innerWidth, innerHeight]")
    assert h["menu"]["x"] >= 0 and h["menu"]["r"] <= inner[0] and h["menu"]["top"] + h["menu"]["h"] <= inner[1], h["menu"]
    assert all(h["by"][i]["h"] <= 36 for i in ("artrefresh", "artcopy", "artdl", "artstudio")), h["by"]
    await shot(page, "header-menu-1100x800.png")
    await page.keyboard.press("Escape")
    await expect(more).to_have_attribute("aria-expanded", "false")
    await expect(page.locator("#artrefresh")).to_be_hidden()
    assert await page.evaluate("document.activeElement.id") == "artmore"
    assert (await page.evaluate(GEOMETRY))["drawer"], "Escape closed the menu, not the conversation"
    await more.click()
    await page.locator("#stream").click(position={"x": 5, "y": 5})                         # a click elsewhere closes it
    await expect(page.locator("#artrefresh")).to_be_hidden()
    # a long title is cut with an ellipsis; the controls keep their size
    long = COFFEE.replace("<title>Coffee Shop</title>", "<title>The Beans and Steam Coffee Company of the Northern Quarter, Since 1987</title>")
    await scripted.turn("rename it", [("write_file", {"path": "coffee-shop/index.html", "content": long})])
    await expect(page.locator("#artname")).to_contain_text("Northern Quarter")
    h = await page.evaluate(HEADER)
    assert h["by"]["artname"]["clipped"] and h["by"]["artname"]["w"] >= 160 and h["bar"]["h"] <= 52, h["by"]["artname"]   # beside the version select
    assert [b["shown"] and not b["clipped"] for b in h["seg"]] == [True, True], h["seg"]


async def test_on_a_wide_window_the_actions_sit_in_the_bar_in_one_row(build):
    scripted, page, _, _ = build
    await page.set_viewport_size({"width": 2200, "height": 1000})
    await scripted.turn("build it", [("write_file", {"path": "coffee-shop/index.html", "content": COFFEE})])
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("Beans & Steam")
    await expect(page.locator("#artext")).to_be_visible()
    h = await page.evaluate(HEADER)
    by = h["by"]
    assert not by["artmore"]["shown"], by["artmore"]
    inline = [by[i] for i in ("artname", "artext", "artrefresh", "artcopy", "artdl", "artstudio", "artauto", "artclose")]
    assert all(i["shown"] for i in inline), inline
    assert by["artext"]["w"] >= 100, by["artext"]                                           # its words, not the arrow alone
    assert h["bar"]["h"] <= 52 and all(i["r"] <= h["bar"]["r"] + 1 for i in inline), h
    await shot(page, "header-2200x1000.png")


async def test_builds_in_quick_succession_end_on_the_last_one_asked_for(build):
    """An open whose content is still being fetched does not replace a later one that arrived first (a report's
    source comes from the route; an image needs no fetch): three turns back to back end on the third's page."""
    scripted, page, _, _ = build
    for n in range(3):
        await scripted.turn("the report", [("write_file", {"path": "notes.md", "content": f"# Notes {n}\n"})])
        await scripted.turn("the chart", [], end=False)
        await shell(scripted, {"chart.png": png(40 + n, 30)})
        scripted.end()
        await scripted.turn("the page", [("write_file", {"path": "viewer.html", "content": COFFEE.replace("Steam", f"Steam {n}")})])
        await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text(f"Beans & Steam {n}")
        await page.wait_for_timeout(250)
        assert await page.evaluate("artOpen") == "file:viewer.html"
        await page.locator("#artclose").click()
