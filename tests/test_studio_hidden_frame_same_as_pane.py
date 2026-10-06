"""A0: the hidden model frame loads a page the way the owner's pane runs it. The pane is a srcdoc frame under
CSP default-src 'none': it loads nothing by URL. The hidden frame used to load the raw file:// page with every
local file reachable, so a page whose stylesheet, script, image or module the pane could not fetch read "clean"
to the model while the owner saw a blank pane (6 of the owner's 9 shows). Now the frame is served the prepared
body (`prepare_studio_media`, the body the pane gets) at the file's own URL, and every other local load is
refused with a line that says why; the file's own origin (file:) and the network block are as before."""
from __future__ import annotations

import base64

import pytest

from dream.gui import preview as preview_mod
from dream.gui.preview import Preview
from dream.tools import context as tool_context, studio
from dream.tools.context import ToolContext, set_context, set_studio
from dream.tools.studio import done, show_html

pytestmark = pytest.mark.asyncio

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAEklEQVR4nGP4z8DwHwyBNAMDAHbjD/0ie3IoAAAAAElFTkSuQmCC")
SITE = {"index.html": ('<!doctype html><html><head><title>Coffee Shop</title><link rel="stylesheet" href="style.css">'
                       '</head><body><h1 id="h">booting...</h1><img id="logo" src="logo.png" alt="logo">'
                       '<script src="app.js"></script></body></html>'),
        "style.css": "body{background:rgb(1, 2, 3)}",
        "app.js": "document.getElementById('h').textContent = 'running';"}
MODULE = ('<!doctype html><title>m</title><h1 id="h">booting...</h1><script type="module" src="post.js"></script>')
FETCHES = ('<!doctype html><title>f</title><h1 id="h">booting...</h1><script>'
           "fetch('./data.json').then(r => r.json()).then(d => { document.getElementById('h').textContent = d.ok; });"
           '</script>')
STUDIO_ONLY = ("<!doctype html><title>s</title><h1 id='h'>booting...</h1><script>"
               "if (location.protocol === 'about:') throw new Error('fails only in the Studio frame');"
               "document.getElementById('h').textContent = 'running';</script>")


def _errors(logs):
    return [l for l in logs if l.startswith(("error:", "console.error:"))]


@pytest.fixture
async def pv(monkeypatch):
    monkeypatch.setattr(preview_mod, "display_render_node", lambda: None)
    p = Preview()
    yield p
    await p.aclose()


@pytest.fixture
def ws(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    for name, body in SITE.items():
        (site / name).write_text(body)
    (site / "logo.png").write_bytes(PNG)
    (tmp_path / "post.js").write_text("document.getElementById('h').textContent = 'running';")
    (tmp_path / "data.json").write_text('{"ok": "running"}')
    (tmp_path / "module.html").write_text(MODULE)
    (tmp_path / "fetches.html").write_text(FETCHES)
    (tmp_path / "studio-only.html").write_text(STUDIO_ONLY)
    return tmp_path


async def test_a_page_with_local_css_js_and_an_image_runs_as_the_pane_runs_it(pv, ws):
    page = ws / "site" / "index.html"
    logs = await pv.load(page)
    assert _errors(logs) == [] and pv.blocked == [], logs
    assert pv.loaded == page.resolve()
    assert await pv.eval("location.protocol") == "file:", "the file's own origin, as before"
    assert await pv.eval("document.getElementById('h').textContent") == "running", "the script ran"
    assert await pv.eval("getComputedStyle(document.body).backgroundColor") == "rgb(1, 2, 3)", "the stylesheet applied"
    assert await pv.eval("document.getElementById('logo').naturalWidth") == 2, "the image decoded"
    assert await pv.eval("document.querySelector('link[rel=stylesheet]')") is None, "the prepared body, not the file"
    assert (await pv.eval("document.getElementById('logo').src")).startswith("data:image/png;base64,")


async def test_a_local_module_script_is_inlined_and_runs_here_as_in_the_pane(pv, ws):
    # DREAM-238: the page's local ES modules travel inside the page (one importmap of data: URLs).
    logs = await pv.load(ws / "module.html")
    assert _errors(logs) == [] and pv.blocked == [], logs
    assert await pv.eval("document.getElementById('h').textContent") == "running", "post.js ran as a module"


async def test_a_module_that_cannot_be_inlined_fails_here_too_and_says_why(pv, ws):
    (ws / "gone.html").write_text(MODULE.replace("post.js", "gone.js"))
    logs = await pv.load(ws / "gone.html")
    gone = ws.resolve() / "gone.js"
    assert pv.blocked == [gone.as_uri()]
    assert any(l == f"blocked: {gone.as_uri()} ({preview_mod._PANE_RULE})" for l in logs), logs
    assert _errors(logs), "a refused load is a console error, so done refuses the page"
    assert await pv.eval("document.getElementById('h').textContent") == "booting...", "as the owner would see it"


async def test_a_fetch_of_a_local_file_fails_as_it_fails_in_the_pane(pv, ws):
    """Chromium's Fetch API refuses a file: URL itself (whatever the file-access flag says), so the
    model reads an error line naming the file and the rejection, as the pane's CSP makes it fail."""
    logs = await pv.load(ws / "fetches.html")
    await pv._page.wait_for_timeout(200)
    data = (ws / "data.json").resolve()
    errors = _errors(pv.logs)
    assert any(data.as_uri() in l for l in errors) and any("Failed to fetch" in l for l in errors), pv.logs
    assert await pv.eval("document.getElementById('h').textContent") == "booting..."


async def test_a_file_over_the_cap_is_named_with_the_cap(pv, ws, monkeypatch):
    from dream.gui import bundle
    monkeypatch.setattr(bundle, "MAX_ASSET_BYTES", len(PNG) - 1)
    page = ws / "site" / "index.html"
    await pv.load(page)
    logo = page.resolve().parent / "logo.png"
    line = [l for l in pv.logs if l.startswith(f"blocked: {logo.as_uri()} (")]
    assert line and "over the preview's 0 MB per-file limit" in line[0], pv.logs
    assert await pv.eval("document.getElementById('logo').naturalWidth") == 0


async def test_a_page_that_fails_only_in_the_studio_frame_is_still_clean_here(pv, ws):
    """Changed on purpose: nothing. The pane's opaque origin (about:) is the pane's; the model's frame keeps the
    file's, and the DREAM-111 report tells the model what the owner's frame did with it."""
    logs = await pv.load(ws / "studio-only.html")
    assert logs == [] and await pv.eval("document.getElementById('h').textContent") == "running"


async def test_a_navigation_to_another_local_page_still_reads_as_navigated_away(pv, ws):
    (ws / "other.html").write_text("<!doctype html><title>o</title><p>other</p>")
    (ws / "leave.html").write_text("<!doctype html><script>location.href = 'other.html'</script>")
    await pv.load(ws / "leave.html")
    await pv._page.wait_for_timeout(300)
    with pytest.raises(RuntimeError, match="navigated away"):
        await pv.eval("1")


async def test_a_pane_only_refusal_for_media_still_loads_the_page_as_written(pv, ws):
    """`prepare_studio_media` refuses a page whose video is missing (the show tools report that); the
    hidden frame loads the page as written and refuses the video's URL like any other local load."""
    page = ws / "clip.html"
    page.write_text('<!doctype html><title>v</title><video id="v" src="missing.mp4"></video><p id="p">page</p>')
    logs = await pv.load(page)
    assert await pv.eval("document.getElementById('p').textContent") == "page"
    missing = page.resolve().parent / "missing.mp4"
    assert any(l.startswith(f"blocked: {missing.as_uri()} (") for l in logs), logs


async def test_done_and_show_html_no_longer_claim_clean_for_a_page_the_pane_cannot_run(ws, monkeypatch):
    """Through the real tools, no Studio open (the hidden frame is the only check there is): `done` on a page
    whose module file is missing is refused with the blocked file named; the site page and the page whose local
    module is there (inlined, DREAM-238) are clean and delivered."""
    monkeypatch.setattr(preview_mod, "display_render_node", lambda: None)
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=ws, emit=None))
    set_studio(None)
    preview_mod._PREVIEW = None
    (ws / "gone.html").write_text(MODULE.replace("post.js", "gone.js"))
    try:
        res = await done.handler({"path": "gone.html"})
        text = res["content"][0]["text"]
        assert res.get("is_error") and "gone.js" in text, text     # refused, the missing module named
        assert "clean — no console output" not in text
        res = await show_html.handler({"path": "gone.html"})
        text = res["content"][0]["text"]
        assert "clean — no console output" not in text and "1 network request(s) blocked" in text, text
        for page in ("site/index.html", "module.html"):
            res = await done.handler({"path": page})
            text = res["content"][0]["text"]
            assert not res.get("is_error") and "clean — no console output" in text, (page, text)
    finally:
        tool_context._CTX = None
        pv = preview_mod._PREVIEW
        if pv is not None:
            await pv.aclose()
        preview_mod._PREVIEW = None
