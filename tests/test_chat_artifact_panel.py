"""The owner's sentence, end to end (DREAM-233, batch A1): [feedback]. A scripted turn -- the real StudioServer, the real
`ArtifactRegistry` on the App's funnel, the real `write_file` / `str_replace_edit` / `done` handlers -- writes a
page, and the Chat view's panel slides in from the right with the page rendered, while the view stays Chat. Then
the rules around it: Preview | Code, close and the card, reload, the never-steal rules, the turn's winner, a
worker's page, the frame's sandbox, and MachX's streamed write. Real Chromium; no model, no engine."""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import pytest
from playwright.async_api import async_playwright, expect

from dream.core.backends.base import Event
from dream.gui import preview as preview_mod
from dream.gui.artifacts import ArtifactRegistry
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.tools import context as tool_context
from dream.tools.context import ToolContext, set_context, set_studio
from dream.tools.files import str_replace_edit
from dream.tools.native import write_file

SHOTS = Path(os.environ.get("DREAM_A1_SHOTS") or "/nonexistent")
TOOLS = {"write_file": write_file, "str_replace_edit": str_replace_edit}

COFFEE = ("<!doctype html><html><head><meta charset='utf-8'><title>Coffee Shop</title>"
          "<style>body{font-family:sans-serif;margin:0}header{background:#6b3e26;color:#fff;padding:32px}</style></head>"
          "<body><header><h1 id='h'>Beans &amp; Steam</h1><p>Small-batch roasts, every morning.</p></header>"
          "<main><h2>Menu</h2><ul><li>Espresso</li><li>Flat white</li></ul></main>"
          "<script>document.getElementById('h').dataset.ran = '1';</script></body></html>")
ABOUT = "<!doctype html><title>About Us</title><h1 id='h'>About</h1>"
HOME = "<!doctype html><title>Home</title><h1 id='h'>Home</h1>"


class Scripted:
    """One session's funnel: what the App's _render_event does for each event, with the registry on it."""

    def __init__(self, srv, registry, ws):
        self.srv, self.registry, self.ws = srv, registry, ws
        self.n = 0

    def funnel(self, ev):
        self.srv.retain_show(ev)
        self.srv.bus.publish(ev)
        self.registry.observe(ev)

    async def turn(self, said, steps, *, end=True, preview=None):
        """user -> turn_start -> [tool_preview deltas] -> for each step: tool_use, the REAL handler, tool_result
        -> result -> turn_end. A step is (tool, args) for the lead or (tool, args, run_id) for a worker."""
        await self.srv._accept_prompt(said)
        self.funnel(Event("turn_start", {}))
        for piece in preview or ():
            self.funnel(Event("tool_preview", piece))
        for step in steps:
            await self.call(*step)
        if end:
            self.end()

    async def call(self, tool, args, run_id=None):
        self.n += 1
        tid = f"{run_id or 'lead'}:{self.n}"
        use = {"id": tid, "name": tool, "input": args}
        if run_id:
            self.funnel(Event("agent_activity", {"kind": "tool_use", "run_id": run_id, "agent": "worker", "data": use}))
        else:
            self.funnel(Event("tool_use", use))
        if tool == "done":
            from dream.tools.studio import done
            res = await done.handler(args)
        else:
            res = await TOOLS[tool].handler(args)
        result = {"id": tid, "name": tool, "content": res["content"][0]["text"], "is_error": bool(res.get("is_error"))}
        if run_id:
            self.funnel(Event("agent_activity", {"kind": "tool_result", "run_id": run_id, "agent": "worker", "data": result}))
        else:
            self.funnel(Event("tool_result", result))
        return res

    def end(self):
        self.funnel(Event("result", {}))
        self.funnel(Event("turn_end", {}))


@pytest.fixture
async def build(tmp_path, monkeypatch):
    """The companion page (the desktop's Chat view) at 1100x800 on a session whose model is scripted."""
    monkeypatch.delenv("DREAM_DESKTOP_SESSION_FILE", raising=False)
    monkeypatch.setattr(preview_mod, "display_render_node", lambda: None)
    ws = tmp_path / "ws"
    ws.mkdir()
    prompts = []
    bus = EventBus()
    registry = ArtifactRegistry(ws, "s1", bus.publish, store=tmp_path / "store")
    # Following is off: the Studio mirror's own route (an SVG write shows in the Studio tab, DREAM-104) is not
    # what this file tests; the registry's events are.
    srv = StudioServer(bus, on_prompt=prompts.append, session={"workspace": str(ws), "provider": "test",
                       "model": "fixture", "session_id": "s1"}, artifacts=registry, follow_model_view=False)
    scripted = Scripted(srv, registry, ws)
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="s1", workspace=ws, emit=scripted.funnel))
    preview_mod._PREVIEW = None
    url = await srv.start()
    set_studio(srv)
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-gpu"])
            page = await browser.new_page(viewport={"width": 1100, "height": 800})
            page.set_default_timeout(5000)
            errors = []
            page.on("pageerror", lambda err: errors.append(str(err)))
            await page.goto(url.replace("/#", "/?companion=1#"))
            await expect(page.locator("#stat")).to_have_text("Ready")
            yield scripted, page, prompts, errors
            assert errors == [], errors
            await browser.close()
    finally:
        set_studio(None)
        tool_context._CTX = None
        await srv.stop()
        pv = preview_mod._PREVIEW
        if pv is not None:
            await pv.aclose()
        preview_mod._PREVIEW = None


GEOMETRY = """() => {
  const box = el => { const r = el.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height, r: r.right}; };
  const panel = document.getElementById('artpanel');
  return {main: box(document.getElementById('main')), panel: box(panel), split: box(document.querySelector('.split')),
          panelShown: getComputedStyle(panel).display !== 'none' && panel.getBoundingClientRect().width > 0,
          view: document.documentElement.dataset.dreamView, nav: document.querySelector('#dream-nav [aria-current=page]')?.id,
          drawer: document.body.classList.contains('interactions-open'), chatArt: document.body.classList.contains('chat-art'),
          transform: getComputedStyle(panel).transform, transition: getComputedStyle(panel).transitionDuration,
          inner: [innerWidth, innerHeight]};
}"""

WATCH_SLIDE = """() => { window.__slide = []; const p = document.getElementById('artpanel');
  for(const kind of ['transitionstart', 'transitionend']) p.addEventListener(kind, e => {
    if(e.target === p && e.propertyName === 'transform') __slide.push([kind, getComputedStyle(p).transform]); });
  return true; }"""


async def shot(page, name):
    if SHOTS.is_dir():
        await page.screenshot(path=str(SHOTS / name))


def card(page, title="Coffee Shop"):
    return page.locator("#stream .artcard", has_text=title)


async def test_a_scripted_build_slides_the_page_in_beside_the_chat_and_the_view_stays_chat(build):
    scripted, page, prompts, _ = build
    await page.evaluate(WATCH_SLIDE)
    before = await page.evaluate(GEOMETRY)
    assert before["view"] == "chat" and not before["panelShown"], before
    await scripted.turn("build a landing page for a coffee shop",
                        [("write_file", {"path": "coffee-shop/index.html", "content": COFFEE})], end=False)
    t0 = asyncio.get_event_loop().time()
    await expect(page.locator("#artbody iframe")).to_be_visible()
    frame = page.frame_locator("#artbody iframe")
    await expect(frame.locator("#h")).to_have_text("Beans & Steam")
    assert asyncio.get_event_loop().time() - t0 < 1.0, "the panel shows within a second of the write's result"
    assert prompts == ["build a landing page for a coffee shop"]
    g = await page.evaluate(GEOMETRY)
    assert g["view"] == "chat" and g["nav"] == "dream-nav-chat" and g["drawer"] and g["chatArt"], g      # still Chat
    assert g["panelShown"] and g["panel"]["x"] >= g["main"]["r"] - 1, g                                  # to the right of the chat
    assert g["main"]["w"] >= 360 and g["panel"]["w"] >= 300, g
    assert abs(g["main"]["w"] / g["split"]["w"] - 0.44) < 0.03, g                                        # 44 % chat, the rest panel
    await expect(page.locator("#stream .msg.you")).to_be_visible()                                       # the owner's message in view
    await expect(page.locator("#artname")).to_have_text("Coffee Shop")                                   # the page's <title>
    await expect(page.locator("#artvn")).to_have_text("v1")
    assert await frame.locator("#h").get_attribute("data-ran") == "1", "the page's script ran"
    # (2) the entry came from the right: the transition started off-screen and ended in place
    # (the frame's text can resolve before the 200 ms slide has begun: wait for its first event)
    await page.wait_for_function("window.__slide.length > 0")
    slide = await page.evaluate("window.__slide")
    assert slide and slide[0][0] == "transitionstart" and slide[0][1] != "none" and slide[0][1] != "matrix(1, 0, 0, 1, 0, 0)", slide
    await page.wait_for_function("__slide.some(s => s[0] === 'transitionend')")
    assert (await page.evaluate(GEOMETRY))["transform"] in ("none", "matrix(1, 0, 0, 1, 0, 0)")
    assert g["transition"] == "0.2s", g
    # never steals: nothing in the page took the focus (the composer keeps it as before the build)
    assert await page.evaluate("document.activeElement && document.activeElement.id") in ("input", ""), "no focus() call"
    await shot(page, "chat-panel-open-1100x800.png")
    scripted.end()
    await expect(page.locator("#stat")).to_have_text("Ready")
    await expect(page.locator("#artbody iframe")).to_be_visible()


async def test_the_panel_in_a_1280_window_and_reduced_motion_has_no_transition(build):
    scripted, page, _, _ = build
    await page.set_viewport_size({"width": 1280, "height": 800})
    await page.emulate_media(reduced_motion="reduce")
    await page.evaluate(WATCH_SLIDE)
    await scripted.turn("build it", [("write_file", {"path": "coffee-shop/index.html", "content": COFFEE})])
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("Beans & Steam")
    g = await page.evaluate(GEOMETRY)
    assert g["transition"] == "0s" and g["transform"] in ("none", "matrix(1, 0, 0, 1, 0, 0)"), g
    await page.wait_for_timeout(300)
    assert await page.evaluate("window.__slide") == [], "reduced motion: no transition at all"
    await shot(page, "chat-panel-open-1280x800.png")


async def test_preview_and_code_toggle_and_code_shows_the_file(build):
    scripted, page, _, _ = build
    await scripted.turn("build it", [("write_file", {"path": "coffee-shop/index.html", "content": COFFEE})])
    await expect(page.locator("#artbody iframe")).to_be_visible()
    await page.locator("#artcode").click()
    await expect(page.locator("#artbody pre")).to_contain_text("<title>Coffee Shop</title>")
    assert await page.locator("#artbody pre").text_content() == COFFEE
    await expect(page.locator("#artbody iframe")).to_have_count(0)
    assert (await page.evaluate(GEOMETRY))["view"] == "chat", "toggling never leaves Chat"
    await shot(page, "chat-panel-code-1100x800.png")
    await page.locator("#artprev").click()
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("Beans & Steam")
    assert (await page.evaluate(GEOMETRY))["view"] == "chat"


async def test_close_returns_the_chat_to_full_width_and_the_card_reopens_it(build):
    scripted, page, _, _ = build
    await scripted.turn("build it", [("write_file", {"path": "coffee-shop/index.html", "content": COFFEE})])
    await expect(page.locator("#artbody iframe")).to_be_visible()
    await page.locator("#artclose").click()
    await expect(page.locator("#artbody iframe")).to_have_count(0)
    g = await page.evaluate(GEOMETRY)
    assert not g["panelShown"] and g["view"] == "chat" and abs(g["main"]["w"] - g["split"]["w"]) <= 2, g   # full width (#main's 1 px side borders), still Chat
    c = card(page)
    await expect(c).to_have_count(1)
    text = " ".join((await c.text_content()).split())
    assert text == "</> Coffee Shop · Page · v1 · Open", text          # the kind's glyph, the title, kind, version, Open
    assert await page.evaluate("document.activeElement && document.activeElement.id") != "studio-artifacts", "close moves no focus"
    await shot(page, "chat-card-closed-1100x800.png")
    await c.get_by_role("button", name="Open").click()
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("Beans & Steam")
    g = await page.evaluate(GEOMETRY)
    assert g["panelShown"] and g["view"] == "chat" and g["panel"]["x"] >= g["main"]["r"] - 1, g


async def test_after_a_reload_the_card_is_still_there_and_opens_v1_from_the_route(build):
    scripted, page, _, _ = build
    await scripted.turn("build it", [("write_file", {"path": "coffee-shop/index.html", "content": COFFEE})])
    await expect(page.locator("#artbody iframe")).to_be_visible()
    await page.reload()
    await expect(page.locator("#stat")).to_have_text("Ready")
    c = card(page)
    await expect(c).to_have_count(1)
    await expect(page.locator("#artbody iframe")).to_have_count(0)                 # a reload replays the card, not the open
    requests = []
    page.on("request", lambda r: requests.append(r.url) if "/api/artifact/" in r.url else None)
    await c.get_by_role("button", name="Open").click()
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("Beans & Steam")
    assert any(u.endswith("?v=1") for u in requests), requests
    await expect(page.locator("#artvn")).to_have_text("v1")


async def test_typing_during_the_write_keeps_the_panel_closed_and_the_draft_intact(build):
    scripted, page, _, _ = build
    await page.locator("#input").click()
    await page.keyboard.type("make the header dar")
    await page.evaluate("() => { const i = document.getElementById('input'); i.setSelectionRange(9, 15); return true; }")
    await scripted.turn("build it", [("write_file", {"path": "coffee-shop/index.html", "content": COFFEE})])
    c = card(page)
    await expect(c).to_have_count(1)
    await expect(c).to_have_class(re.compile(r"\bpulse\b"))
    await page.wait_for_timeout(300)
    await expect(page.locator("#artbody iframe")).to_have_count(0)
    assert not (await page.evaluate(GEOMETRY))["panelShown"]
    assert await page.input_value("#input") == "make the header dar"
    assert await page.evaluate("[document.activeElement.id, input.selectionStart, input.selectionEnd]") == ["input", 9, 15]
    await shot(page, "chat-card-pulse-typing-1100x800.png")


async def test_the_turns_winner_index_beats_about_an_explicit_done_beats_both_and_a_dependency_reloads(build):
    scripted, page, _, _ = build
    ws = scripted.ws
    await scripted.turn("build a two-page site", [
        ("write_file", {"path": "site/about.html", "content": ABOUT}),
        ("write_file", {"path": "site/index.html", "content": HOME}),
        ("write_file", {"path": "site/contact.html", "content": "<title>Contact</title><h1 id='h'>Contact</h1>"}),
    ], end=False)
    frame = page.frame_locator("#artbody iframe")
    await expect(frame.locator("#h")).to_have_text("Home")                        # index.html took about.html's place
    await expect(page.locator("#stream .artcard")).to_have_count(3)             # every page has its card
    await expect(page.locator("#artname")).to_have_text("Home")
    res = await scripted.call("done", {"path": "site/about.html"})
    assert not res.get("is_error"), res
    await expect(frame.locator("#h")).to_have_text("About")                       # the explicit choice wins
    assert (await page.evaluate(GEOMETRY))["view"] == "chat", "an explicit show in Chat stays in Chat"
    await expect(page.locator("#stream .artcard")).to_have_count(3)
    # a dependency write: the open page reloads (with its media embedded) and no card is added
    (ws / "site" / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    await scripted.call("str_replace_edit", {"path": "site/about.html", "old_string": "<h1 id='h'>About</h1>",
                                             "new_string": "<h1 id='h'>About</h1><video id='v' src='clip.mp4'></video>"})
    await expect(frame.locator("#v")).to_have_attribute("src", re.compile(r"^data:video/mp4"))
    await expect(page.locator("#artvn")).to_have_text("v2")
    await expect(page.locator("#stream .artcard")).to_have_count(3)
    await scripted.call("write_file", {"path": "site/style.css", "content": "body{background:teal}"})
    await page.wait_for_timeout(200)
    await expect(page.locator("#stream .artcard")).to_have_count(3)             # style.css: no card
    await expect(frame.locator("#h")).to_have_text("About")
    scripted.end()
    await shot(page, "chat-panel-site-1100x800.png")


async def test_a_workers_page_opens_at_turn_end(build):
    scripted, page, _, _ = build
    await scripted.turn("have a worker build the docs", [
        ("write_file", {"path": "docs/index.html", "content": "<title>Docs</title><h1 id='h'>Docs</h1>"}, "run-1"),
    ], end=False)
    await expect(card(page, "Docs")).to_have_count(1)
    await page.wait_for_timeout(300)
    await expect(page.locator("#artbody iframe")).to_have_count(0)                 # a worker's page waits for the turn's end
    scripted.end()
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("Docs")


async def test_a_second_turn_opens_its_own_page_and_a_closed_panel_stays_closed_for_the_turn(build):
    scripted, page, _, _ = build
    await scripted.turn("build it", [("write_file", {"path": "a/index.html", "content": HOME})])
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("Home")
    await scripted.turn("and a second one", [("write_file", {"path": "b/index.html", "content": ABOUT})], end=False)
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("About")
    await page.locator("#artclose").click()                                     # the owner closes it mid-turn
    await scripted.call("write_file", {"path": "c/index.html", "content": "<title>C</title><h1 id='h'>C</h1>"})
    await expect(card(page, "C")).to_have_count(1)
    await page.wait_for_timeout(300)
    await expect(page.locator("#artbody iframe")).to_have_count(0)
    scripted.end()
    await scripted.turn("third", [("write_file", {"path": "d/index.html", "content": "<title>D</title><h1 id='h'>D</h1>"})])
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("D")   # a new turn may open again


async def test_the_frame_is_sandboxed_with_the_csp_first_and_no_token(build):
    scripted, page, _, _ = build
    await scripted.turn("build it", [("write_file", {"path": "coffee-shop/index.html", "content": COFFEE})])
    f = page.locator("#artbody iframe")
    await expect(f).to_be_visible()
    assert await f.get_attribute("sandbox") == "allow-scripts"
    srcdoc = await f.get_attribute("srcdoc")
    assert srcdoc.startswith('<!doctype html><meta http-equiv="Content-Security-Policy"'), srcdoc[:120]
    token = await page.evaluate("dreamToken()")
    assert token and token not in srcdoc
    frame = page.frame_locator("#artbody iframe")
    thrown = await frame.locator("body").evaluate("() => { try { parent.document; return 'readable'; } catch (e) { return e.name; } }")
    assert thrown == "SecurityError", thrown


async def test_a_streamed_write_opens_the_panel_in_code_view_before_the_call_lands(build):
    scripted, page, _, _ = build
    head = '<tool_call>{"name": "write_file", "arguments": {"path": "game/index.html", "content": "<!doctype html>'
    await scripted.turn("make a snake game", [], end=False,
                        preview=[head, "<title>Snake</title><canvas id=c>", "</canvas><script>// moves"])
    await expect(page.locator("#artbody pre")).to_contain_text("// moves")                 # Code view, streaming
    await expect(page.locator("#artbody pre")).to_contain_text("game/index.html")
    g = await page.evaluate(GEOMETRY)
    assert g["panelShown"] and g["view"] == "chat" and g["chatArt"], g
    await expect(page.locator("#artname")).to_have_text("index.html")
    await expect(page.locator("#stream .artcard")).to_have_count(0), "no card until the file lands"
    body = "<!doctype html><title>Snake</title><canvas id=c></canvas><script>// moves</script>"
    await scripted.call("write_file", {"path": "game/index.html", "content": body})
    await expect(page.frame_locator("#artbody iframe").locator("#c")).to_be_attached()    # it flipped to Preview
    await expect(page.locator("#artname")).to_have_text("Snake")
    await expect(card(page, "Snake")).to_have_count(1)
    scripted.end()


async def test_a_follow_up_names_the_open_page_to_the_model_only(build):
    scripted, page, prompts, _ = build
    await scripted.turn("build it", [("write_file", {"path": "coffee-shop/index.html", "content": COFFEE})])
    await expect(page.locator("#artbody iframe")).to_be_visible()
    await page.locator("#input").fill("make the header dark")
    await page.locator("#send").click()
    await expect(page.locator("#stream .msg.you .body").last).to_have_text("make the header dark")   # beside its "You" label (B1)
    assert prompts[-1].startswith('make the header dark\n\n[Dream, not from the user: the user has "Coffee Shop" open in the '
                                  'preview panel: coffee-shop/index.html (v1, 1 lines).'), prompts[-1]
    await page.locator("#artclose").click()
    await page.locator("#input").fill("thanks")
    await page.locator("#send").click()
    await expect(page.locator("#stream .msg.you .body").last).to_have_text("thanks")
    assert prompts[-1] == "thanks", "nothing open: no note"


async def test_the_switch_off_and_another_view_give_a_card_only(build):
    scripted, page, _, _ = build
    await page.evaluate("() => { localStorage.setItem('dream.artifactAutoOpen', '0'); return true; }")
    await page.reload()
    await expect(page.locator("#stat")).to_have_text("Ready")
    await scripted.turn("build it", [("write_file", {"path": "a/index.html", "content": HOME})])
    await expect(card(page, "Home")).to_have_count(1)
    await page.wait_for_timeout(300)
    assert not (await page.evaluate(GEOMETRY))["panelShown"]
    await page.evaluate("() => { localStorage.removeItem('dream.artifactAutoOpen'); return true; }")
    await page.reload()
    await expect(page.locator("#stat")).to_have_text("Ready")
    await page.click("#dream-nav-files")
    await expect(page.locator("#dream-files-page")).to_be_visible()
    await scripted.turn("build another", [("write_file", {"path": "b/index.html", "content": ABOUT})])
    await page.wait_for_timeout(300)
    g = await page.evaluate(GEOMETRY)
    assert g["view"] == "files" and not g["chatArt"], g
    await page.click("#dream-nav-chat")
    await expect(card(page, "About")).to_have_count(1)
    await expect(page.locator("#artbody iframe")).to_have_count(0)


async def test_at_a_phone_width_the_panel_is_a_sheet_over_the_chat_only_with_an_idle_composer(build):
    scripted, page, _, _ = build
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.locator("#input").fill("a draft I have not sent")
    await scripted.turn("build it", [("write_file", {"path": "a/index.html", "content": HOME})])
    await expect(card(page, "Home")).to_have_count(1)
    await page.wait_for_timeout(300)
    assert not (await page.evaluate(GEOMETRY))["panelShown"], "a sheet would cover the draft"
    await page.locator("#input").fill("")
    await page.evaluate("() => { document.activeElement.blur(); return true; }")
    await page.wait_for_timeout(3100)                                            # past the typing window
    await scripted.turn("build the about page", [("write_file", {"path": "b/about.html", "content": ABOUT})])
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("About")
    g = await page.evaluate(GEOMETRY)
    assert g["panelShown"] and g["view"] == "chat", g
    assert g["panel"]["r"] >= g["split"]["r"] - 1 and g["panel"]["w"] <= g["inner"][0], g    # anchored right, within the window
    assert await page.evaluate("getComputedStyle(document.getElementById('artpanel')).position") == "absolute"
    await shot(page, "chat-sheet-390x844.png")
    await page.locator("#artclose").click()
    await expect(page.locator("#artbody iframe")).to_have_count(0)
    await expect(page.locator("#input")).to_be_visible()


def frame_csp(srcdoc):
    return re.match(r'<!doctype html><meta http-equiv="Content-Security-Policy" content="([^"]*)"', srcdoc).group(1)


async def test_the_header_pill_decides_whether_the_frame_may_reach_the_internet(build):
    """DREAM-234: on by default -- the frame's CSP lets a page load scripts, styles, fonts, images and fetch over
    https (never http, never a file); the pill turns it off (red, remembered) and rebuilds the open page under the
    no-network policy; on again restores it."""
    scripted, page, _, _ = build
    # Chat shows no bars (DREAM-240): the header's pill is clicked through its chip under the composer.
    pill, chip = page.locator("#netpill"), page.locator("#cn-net")
    await expect(chip).to_be_visible()
    assert not await pill.evaluate("b => b.classList.contains('off')")
    await scripted.turn("build a landing page for a coffee shop",
                        [("write_file", {"path": "coffee-shop/index.html", "content": COFFEE})])
    f = page.locator("#artbody iframe")
    await expect(f).to_be_visible()
    csp = frame_csp(await f.get_attribute("srcdoc"))
    assert "connect-src https:" in csp and "script-src 'unsafe-inline' 'unsafe-eval' data: blob: https:" in csp, csp
    assert "font-src data: https:" in csp and "img-src data: blob: https:" in csp, csp
    assert " http:" not in csp and "file:" not in csp and "'self'" not in csp, csp
    assert await f.get_attribute("sandbox") == "allow-scripts"                  # the sandbox itself is unchanged
    await expect(page.locator("#artnote")).to_contain_text("internet on (https only)")
    await chip.click()
    assert await pill.evaluate("b => b.classList.contains('off')")
    assert await page.evaluate("localStorage.getItem('dream.previewNet')") == "0"
    await expect(page.locator("#artnote")).to_contain_text("internet off")
    csp = frame_csp(await page.locator("#artbody iframe").get_attribute("srcdoc"))
    assert "https:" not in csp and "connect-src" not in csp, csp               # the pre-DREAM-234 policy
    await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("Beans & Steam")   # rebuilt, same page
    await chip.click()
    assert not await pill.evaluate("b => b.classList.contains('off')")
    assert await page.evaluate("localStorage.getItem('dream.previewNet')") == "1"
    assert "connect-src https:" in frame_csp(await page.locator("#artbody iframe").get_attribute("srcdoc"))
