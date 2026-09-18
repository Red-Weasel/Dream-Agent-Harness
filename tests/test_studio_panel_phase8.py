"""Phase 8 in the real panel: typed cards, a widget in the artifact frame, the
image row, the research card, and sendPrompt into the composer (never sent)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from dream.core.backends.base import Event
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer

UI = Path(__file__).parent.parent / "dream" / "gui" / "static" / "index.html"


def test_the_panel_has_renderers_and_escapes_everything():
    ui = UI.read_text()
    for n in ("function renderWidget", "function renderCard", "function chartSvg", "function quizHtml",
              "d.op === 'widget'", "__dream_send_prompt", "window.sendPrompt"):
        assert n in ui, n
    rc = ui[ui.index("function renderCard"):ui.index("function chartSvg")]
    assert rc.count("esc(") >= 30, "every model string is escaped"
    assert "innerHTML = String(" not in rc
    sp = ui[ui.index("__dream_send_prompt'){"):ui.index("__dream_mention'){")]
    assert "submit()" not in sp and "input.value" in sp, "sendPrompt fills the composer, never sends"


@pytest.mark.asyncio
async def test_end_to_end_cards_widget_images_research_and_sendprompt(tmp_path):
    from playwright.async_api import async_playwright

    prompts: list[str] = []
    srv = StudioServer(EventBus(), on_prompt=prompts.append, session={"workspace": str(tmp_path)})
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1600, "height": 900})
            await page.goto(url, wait_until="load")
            await page.wait_for_function("document.getElementById('stat').textContent === 'live'", timeout=5000)

            def pub(widget, data, text=""):
                srv.bus.publish(Event("studio", {"op": "widget", "widget": widget, "data": data, "text": text}))

            # a typed card with hostile text renders as text
            pub("step_card_display_v0", {"steps": [{"title": "<img src=x onerror=\"document.title='pwned'\">Open",
                                                     "description": "d"}, {"title": "Close", "description": "d"}],
                                         "summary": "two <b>steps</b>"})
            await page.wait_for_selector(".vcard.step_card", timeout=5000)
            assert await page.title() != "pwned"
            assert "<img" not in await page.inner_html(".vcard.step_card") or "&lt;img" in await page.inner_html(".vcard.step_card")
            assert "two <b>steps</b>" == await page.text_content(".vcard.step_card .vs")
            # a chart draws an svg; a comparison draws a table; a quiz has options
            pub("chart_display_v0", {"style": "bar", "title": "Sales", "series": [{"name": "Q", "values": [1, 3, 2]}],
                                     "x_axis": {"categories": ["a", "b", "c"]}})
            await page.wait_for_selector(".vcard.chart svg rect", timeout=5000)
            pub("comparison_card_display_v0", {"products": [
                {"name": "A", "attributes": [{"label": "X", "value": "1"}, {"label": "Y", "value": "2"}]},
                {"name": "B", "attributes": [{"label": "X", "value": "3"}, {"label": "Y", "value": "4"}]}], "summary": "s"})
            await page.wait_for_selector(".vcard.comparison_card table.cmp", timeout=5000)
            assert await page.locator(".vcard.comparison_card tbody tr").count() == 2
            pub("quiz_display_v0", {"questions": [{"id": "q1", "question": "?", "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}],
                                                   "correct_option_id": "b", "explanation": "because"}]})
            await page.wait_for_selector(".vcard.quiz details.q", timeout=5000)
            # images: thumbnails with dimensions, links out
            pub("image_search", {"query": "eiffel", "images": [
                {"src": "https://img.test/a.jpg", "thumb": "https://img.test/a_s.jpg", "title": "A", "width": 100, "height": 50, "page": "https://p.test/a"}]})
            await page.wait_for_selector(".vcard.images img", timeout=5000)
            assert await page.get_attribute(".vcard.images a", "href") == "https://p.test/a"
            assert "100×50" == await page.text_content(".vcard.images a span")
            # a widget lands in the sandboxed artifact frame and can fill the composer
            pub("visualize_show_widget", {"title": "calc", "kind": "html",
                                          "code": "<button id='b' onclick=\"sendPrompt('use 42')\">go</button>", "loading": []})
            await page.wait_for_selector("#artbody iframe", timeout=5000)
            assert await page.get_attribute("#artbody iframe", "sandbox") == "allow-scripts"
            await page.wait_for_timeout(300)
            await page.frame_locator("#artbody iframe").locator("#b").click()
            await page.wait_for_function("document.getElementById('input').value === 'use 42'", timeout=5000)
            assert prompts == [], "sendPrompt never sends by itself"
            # and never over what the user was typing: it joins on a new line
            await page.fill("#input", "mine so far")
            await page.frame_locator("#artbody iframe").locator("#b").click()
            await page.wait_for_function("document.getElementById('input').value === 'mine so far\\nuse 42'", timeout=5000)
            await page.fill("#input", "")
            # the research card sends a prompt when the user presses the button
            pub("suggest_research", {"rationale": "compare many vendors"})
            await page.wait_for_selector(".vcard.research button", timeout=5000)
            await page.click(".vcard.research button")
            for _ in range(50):
                if prompts:
                    break
                await asyncio.sleep(0.05)
            assert prompts and "compare many vendors" in prompts[-1] and "researcher" in prompts[-1]
            await browser.close()
    finally:
        await srv.stop()


@pytest.mark.asyncio
async def test_the_csp_holds_when_the_artifact_carries_a_stray_html_or_head(tmp_path):
    """Gate 8 blocking finding: the CSP was spliced in at the first "<head"/"<html"
    match, so '<head>' inside a script string or a comment, or an <html> after
    other content, put the meta in the body — where Chromium ignores it — and
    the frame ran with no CSP. A loopback canary records every request the
    frame manages to make; the answer must be none."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from playwright.async_api import async_playwright

    hits: list[str] = []

    class Canary(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    canary = ThreadingHTTPServer(("127.0.0.1", 0), Canary)
    threading.Thread(target=canary.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{canary.server_address[1]}"
    variants = [
        f'<p>x</p><html><img src="{base}/img/1.png">',
        f"<script>var s='<html>'</script><script>fetch('{base}/f2')</script>",
        f'<!-- <head> --><head><meta charset="utf-8"></head><body><img src="{base}/img/3.png">',
        f"<body><script>var s='<head>'</script><img src=\"{base}/img/4.png\">",
        f'<!doctype html><html lang="en"><head><title>t</title></head><body><script>fetch("{base}/f5")</script></body></html>',
    ]
    srv = StudioServer(EventBus(), on_prompt=lambda t: None, session={"workspace": str(tmp_path)})
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1600, "height": 900})
            await page.goto(url, wait_until="load")
            await page.wait_for_function("document.getElementById('stat').textContent === 'live'", timeout=5000)
            for i, code in enumerate(variants):
                for op in ("widget", "show"):
                    if op == "widget":
                        srv.bus.publish(Event("studio", {"op": "widget", "widget": "visualize_show_widget",
                                                         "data": {"title": f"w{i}", "kind": "html", "code": code, "loading": []}}))
                    else:
                        srv.bus.publish(Event("studio", {"op": "show", "path": f"p{i}.html", "content": code}))
                    await page.wait_for_function(
                        f"document.querySelector('#artname') && document.querySelector('#artname').textContent === "
                        f"{'\"w%d\"' % i if op == 'widget' else '\"p%d.html\"' % i}", timeout=5000)
                    await page.wait_for_timeout(500)
                    frame = [f for f in page.frames if f != page.main_frame][-1]
                    where = await frame.evaluate(
                        "() => { const m = document.querySelector('meta[http-equiv=\"Content-Security-Policy\"]');"
                        " return m ? m.parentElement.tagName : 'MISSING'; }")
                    assert where == "HEAD", (i, op, where)
            await page.wait_for_timeout(500)
            await browser.close()
    finally:
        await srv.stop()
        canary.shutdown()
    assert hits == [], hits


@pytest.mark.asyncio
async def test_gate8_pass_observations_svg_sizing_sendprompt_gesture_and_self_navigation(tmp_path):
    """Gate 8 pass, observations 1-3, folded in: a viewBox-only SVG is visible;
    sendPrompt needs a user gesture inside the frame and cannot grow the
    composer past a ceiling; a page that navigates itself away is closed."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from playwright.async_api import async_playwright

    hits: list[str] = []

    class Site(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            body = b"<p>elsewhere</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    site = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{site.server_address[1]}"
    prompts: list[str] = []
    srv = StudioServer(EventBus(), on_prompt=prompts.append, session={"workspace": str(tmp_path)})
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1600, "height": 900})
            await page.goto(url, wait_until="load")
            await page.wait_for_function("document.getElementById('stat').textContent === 'live'", timeout=5000)

            def widget(title, kind, code):
                srv.bus.publish(Event("studio", {"op": "widget", "widget": "visualize_show_widget",
                                                 "data": {"title": title, "kind": kind, "code": code, "loading": []}}))

            async def open_frame(title):
                await page.wait_for_function(
                    f"document.querySelector('#artname') && document.querySelector('#artname').textContent === {title!r}"
                    " && document.querySelector('#artbody iframe')", timeout=5000)
                await page.wait_for_timeout(300)
                return [f for f in page.frames if f != page.main_frame][-1]

            # 1. a viewBox-only SVG (the form the rules and the tests use) has a real size
            widget("plain_svg", "svg", "<svg viewBox='0 0 960 540'><rect width='960' height='540' fill='red'/></svg>")
            frame = await open_frame("plain_svg")
            box = await frame.evaluate("() => { const r = document.querySelector('svg').getBoundingClientRect(); return [r.width, r.height]; }")
            assert box[0] > 300 and box[1] > 100, box
            assert abs(box[0] / box[1] - 960 / 540) < 0.05, "the viewBox aspect is kept"

            # 2. sendPrompt on load or on a timer does nothing; a click fills the composer
            widget("asker", "html", "<button id='b' onclick=\"sendPrompt('use 42')\">go</button>"
                   "<script>sendPrompt('on load'); setTimeout(() => sendPrompt('on timer'), 50);</script>")
            frame = await open_frame("asker")
            await page.wait_for_timeout(400)
            assert await page.input_value("#input") == ""
            await page.frame_locator("#artbody iframe").locator("#b").click()
            await page.wait_for_function("document.getElementById('input').value === 'use 42'", timeout=5000)
            assert prompts == []
            # ... and never past the ceiling: what the user has stays exactly as it was
            long = "a" * 7999
            await page.fill("#input", long)
            await page.frame_locator("#artbody iframe").locator("#b").click()
            await page.wait_for_timeout(400)
            assert await page.input_value("#input") == long
            await page.fill("#input", "")

            # 3. a page that navigates itself away is closed, and the user is told — even
            # one that leaves while still parsing, which fires a single load event
            widget("runaway", "html", f"<p>hi</p><script>location.href = '{base}/selfnav';</script>")
            await page.wait_for_function("!document.querySelector('#artbody iframe')", timeout=8000)
            assert "navigated away" in await page.text_content("#artnote")
            assert any("navigated away" in t for t in await page.locator(".sys").all_text_contents())
            assert hits == ["/selfnav"], hits
            # the frame's questions still get an answer, not a hang
            ans = asyncio.ensure_future(srv.ask_frame("eval", {"code": "1+1"}, timeout=5))
            with pytest.raises(Exception) as ei:
                await ans
            assert "no artifact frame" in str(ei.value)
            await browser.close()
    finally:
        await srv.stop()
        site.shutdown()
