"""DeepSleep's Preview (the owner, 2026-10-10): the Chat view's preview panel, slid in beside the terminal board.

The scripted session of tests/test_chat_artifact_panel.py on the real page server; real Chromium; no model, no engine.
"""
from __future__ import annotations

import re
from pathlib import Path

from playwright.async_api import expect
from starlette.testclient import TestClient

from test_chat_artifact_kinds import png
from test_chat_artifact_panel import COFFEE, build, shot  # noqa: F401  (build is the fixture)

PANEL, PICK, ROWS = "#artpanel", "#ds-preview-pick", "#ds-preview-pick .ds-pv-row"
GEOMETRY = """() => {
  const box = s => { const r = document.querySelector(s)?.getBoundingClientRect(); return r && {x: r.x, w: r.width, r: r.right, h: r.height}; };
  return {view: document.documentElement.dataset.dreamView, board: box('#dream-deepsleep'), panel: box('#artpanel'),
          width: innerWidth, open: document.documentElement.classList.contains('ds-preview')};
}"""
# Points across the panel, its bottom rows included, that something outside the panel draws over.
COVERED = """() => {
  const p = document.querySelector('#artpanel').getBoundingClientRect(), out = [];
  for (const fy of [.1, .5, .9, .98]) for (const fx of [.1, .5, .9]) {
    const x = p.x + p.width * fx, y = p.y + p.height * fy, hit = document.elementFromPoint(x, y);
    if (!hit || !hit.closest('#artpanel')) out.push({x: Math.round(x), y: Math.round(y), hit: hit && (hit.id || hit.className || hit.tagName)});
  }
  return out;
}"""


async def settled(page, opened=True):
    """After the slide: the panel and the board's edge stop moving (a 0.2 s transition)."""
    await page.wait_for_function("""(opened) => { const p = document.querySelector('#artpanel').getBoundingClientRect(),
      b = document.querySelector('#dream-deepsleep').getBoundingClientRect();
      return opened ? Math.abs(p.right - innerWidth) <= 1 && Math.abs(b.right - p.left) <= 2 : Math.abs(b.right - innerWidth) <= 1; }""", arg=opened)


async def to_deepsleep(page):
    await page.get_by_role("button", name="Go to DeepSleep", exact=True).click()
    await expect(page.locator("#dream-deepsleep")).to_be_visible()


def files(ws: Path):
    (ws / "notes").mkdir()
    (ws / "notes" / "summary.md").write_text("# Findings\n\nThe cache key ignores the prompt head.\n")
    (ws / "shots").mkdir()
    (ws / "shots" / "board.png").write_bytes(png(40, 30))
    (ws / "site").mkdir()
    (ws / "site" / "index.html").write_text("<title>Landing</title><h1 id='h'>Hello from the site</h1>")
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "skip.html").write_text("<h1>never listed</h1>")
    (ws / ".hidden").mkdir()
    (ws / ".hidden" / "secret.md").write_text("never listed")
    (ws / "code.py").write_text("print('not previewable')")


async def test_preview_slides_in_beside_the_terminals_and_lists_what_can_be_shown(build):
    scripted, page, _, _ = build
    files(scripted.ws)
    await page.set_viewport_size({"width": 1500, "height": 900})
    await to_deepsleep(page)
    toggle = page.locator(".ds-controls button[aria-label='Preview artifacts']")
    await expect(toggle).to_have_text("Preview")
    await expect(toggle).to_have_attribute("aria-pressed", "false")
    await expect(page.locator(PANEL)).to_be_hidden()

    await toggle.click()
    await expect(toggle).to_have_attribute("aria-pressed", "true")
    await expect(page.locator(PICK)).to_be_visible()
    await settled(page)
    g = await page.evaluate(GEOMETRY)
    assert g["open"] and abs(g["panel"]["r"] - g["width"]) <= 1, g                   # fixed on the right
    assert abs(g["board"]["r"] - g["panel"]["x"]) <= 2, g                             # the terminals give up its width
    assert 320 <= g["panel"]["w"] <= 761, g                                           # the Chat view's width by default
    await expect(page.locator("#ds-preview-search")).to_be_focused()
    await expect(page.locator(ROWS)).to_have_count(3)                                 # three previewable files
    listed = " ".join(await page.locator(ROWS).all_inner_texts())
    for path in ("notes/summary.md", "shots/board.png", "site/index.html"):
        assert path in listed, listed
    assert "skip.html" not in listed and "secret.md" not in listed and "code.py" not in listed
    await shot(page, "deepsleep-preview-list-1500x900.png")


async def test_a_row_opens_in_the_panel_and_its_close_returns_to_the_list(build):
    scripted, page, _, _ = build
    files(scripted.ws)
    await page.set_viewport_size({"width": 1500, "height": 900})
    await to_deepsleep(page)
    await page.locator("button[aria-label='Preview artifacts']").click()
    await page.locator(ROWS, has_text="index.html").click()
    await expect(page.locator(PICK)).to_be_hidden()
    frame = page.frame_locator("#artbody iframe")
    await expect(frame.locator("#h")).to_have_text("Hello from the site")
    await settled(page)
    covered = await page.evaluate(COVERED)
    assert not covered, covered                                                       # nothing draws over the panel
    await shot(page, "deepsleep-preview-page-1500x900.png")
    await page.locator("#artclose").click()                                           # the panel's own close
    await expect(page.locator(PICK)).to_be_visible()                                   # ... is the list again
    await page.locator(ROWS, has_text="summary.md").click()
    await expect(page.locator("#artbody")).to_contain_text("The cache key ignores the prompt head.")
    await page.locator("#artclose").click()
    await page.locator(ROWS, has_text="board.png").click()
    await expect(page.locator("#artbody img")).to_be_visible()


async def test_esc_the_toggle_and_the_list_close_all_close_it(build):
    scripted, page, _, _ = build
    files(scripted.ws)
    await page.set_viewport_size({"width": 1500, "height": 900})
    await to_deepsleep(page)
    toggle = page.locator("button[aria-label='Preview artifacts']")
    await toggle.click()
    await page.keyboard.press("Escape")
    await expect(page.locator(PANEL)).to_be_hidden()
    await expect(toggle).to_have_attribute("aria-pressed", "false")
    await settled(page, opened=False)                                                  # the terminals have the room back
    await toggle.click()
    await page.locator(ROWS, has_text="index.html").click()
    await toggle.click()                                                               # closing with a page open
    await expect(page.locator(PANEL)).to_be_hidden()
    assert await page.locator("#artbody iframe").count() == 0                          # its frame is torn down
    await toggle.click()
    await page.locator("#ds-preview-pick .ds-pv-close").click()
    await expect(page.locator(PANEL)).to_be_hidden()


async def test_the_desktop_header_preview_button_reaches_the_page(build):
    """The desktop app's own header (dream/desktop/window.py) sends 'preview' through DreamDeepSleepToolbar.dispatch."""
    _, page, _, _ = build
    await page.set_viewport_size({"width": 1500, "height": 900})
    await to_deepsleep(page)
    state = await page.evaluate("DreamDeepSleepToolbar.state()")
    assert state["controls"]["preview"] == {"label": "Preview artifacts", "disabled": False, "pressed": False}, state
    send = "id => DreamDeepSleepToolbar.dispatch({...id, control: 'preview'})"
    ident = {"session_id": state["session_id"], "workspace": state["workspace"]}
    assert await page.evaluate(send, ident)
    await expect(page.locator(PICK)).to_be_visible()
    assert (await page.evaluate("DreamDeepSleepToolbar.state()"))["controls"]["preview"]["pressed"] is True
    assert await page.evaluate(send, ident)
    await expect(page.locator(PANEL)).to_be_hidden()


async def test_search_narrows_the_list_and_what_dream_built_is_listed_first(build):
    scripted, page, _, _ = build
    files(scripted.ws)
    await scripted.turn("build it", [("write_file", {"path": "coffee-shop/index.html", "content": COFFEE})])
    await page.set_viewport_size({"width": 1500, "height": 900})
    await to_deepsleep(page)
    await page.locator("button[aria-label='Preview artifacts']").click()
    if await page.locator("#artpanel.open").count():                                  # what Dream built last may be open
        await page.locator("#artclose").click()                                        # ... and its close is the list
    await expect(page.locator("#ds-preview-pick h3").first).to_have_text("Built by Dream")
    await expect(page.locator(ROWS).first).to_contain_text("coffee-shop/index.html")
    await expect(page.locator(ROWS)).to_have_count(4)                                  # listed once, not twice
    await page.locator("#ds-preview-search").fill("summary")
    await expect(page.locator(ROWS)).to_have_count(1)
    await page.locator("#ds-preview-search").fill("nothing-like-this")
    await expect(page.locator("#ds-preview-pick")).to_contain_text("Nothing matches.")


async def test_the_width_follows_the_drag_edge_and_is_kept(build):
    scripted, page, _, _ = build
    files(scripted.ws)
    await page.set_viewport_size({"width": 1500, "height": 900})
    await to_deepsleep(page)
    await page.locator("button[aria-label='Preview artifacts']").click()
    await settled(page)
    edge = await page.locator("#ds-preview-edge").bounding_box()
    await page.mouse.move(edge["x"] + 4, 400)
    await page.mouse.down()
    await page.mouse.move(900, 400, steps=5)
    await page.mouse.up()
    await settled(page)
    g = await page.evaluate(GEOMETRY)
    assert abs(g["panel"]["x"] - 900) <= 3 and abs(g["board"]["r"] - g["panel"]["x"]) <= 2, g
    assert await page.evaluate("localStorage.getItem('dream.dsPreviewWidth')") == str(1500 - 900)


async def test_what_dream_shows_while_preview_is_open_lands_beside_the_terminals(build):
    scripted, page, _, _ = build
    await page.set_viewport_size({"width": 1500, "height": 900})
    await to_deepsleep(page)
    await page.locator("button[aria-label='Preview artifacts']").click()
    await scripted.turn("build it", [("write_file", {"path": "coffee-shop/index.html", "content": COFFEE})])
    await expect(page.locator("#artpanel")).to_have_class(re.compile(r"\bopen\b"))
    await expect(page.locator(PICK)).to_be_hidden()
    await settled(page)
    assert (await page.evaluate(GEOMETRY))["view"] == "deepsleep"


def test_the_file_list_route_needs_the_token_and_skips_hidden_and_dependency_folders(tmp_path):
    from dream.gui.bus import EventBus
    from dream.gui.server import StudioServer
    ws = tmp_path / "ws"
    ws.mkdir()
    files(ws)
    (ws / "link.md").symlink_to(ws / "notes" / "summary.md")
    srv = StudioServer(EventBus(), session={"workspace": str(ws), "session_id": "s1"})
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/preview-files").status_code == 401
        got = client.get("/api/preview-files", headers={"X-Dream-Token": srv.token}).json()
    assert sorted(f["path"] for f in got["files"]) == ["notes/summary.md", "shots/board.png", "site/index.html"]
    assert {f["path"]: f["type"] for f in got["files"]}["shots/board.png"] == "image" and got["truncated"] is False
    assert all(f["bytes"] > 0 and f["at"] > 0 for f in got["files"])
