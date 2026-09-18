"""Offline unit tests for helpers that don't need a browser or network."""

from types import SimpleNamespace

from dream.tools import web
from dream.tools.context import ToolContext, set_context
from dream.web.browser import _looks_dead, _slug, extract_text


def test_extract_text_fallback():
    html = "<html><body><h1>Title</h1><p>Hello world.</p><script>x=1</script></body></html>"
    text = extract_text(html, "https://example.com")
    assert "Hello world" in text
    assert "x=1" not in text


def test_looks_dead():
    assert _looks_dead(Exception("Target page, context or browser has been closed"))
    assert not _looks_dead(Exception("net::ERR_NAME_NOT_RESOLVED"))


def test_slug():
    assert _slug("https://Example.com/Path") == "https-example-com-path"


class _ShotBrowser:
    async def fetch(self, url, screenshot=False, wait_selector=None):
        return {"url": url, "final_url": url, "status": 200, "title": "T",
                "text": "body", "screenshot": "/var/shot.png"}


def _ctx_with(multimodal: bool) -> None:
    set_context(ToolContext(
        store=SimpleNamespace(), working=SimpleNamespace(),
        browser=_ShotBrowser(), session_id="s", multimodal=multimodal,
    ))


async def test_browse_offers_see_when_the_model_can_look():
    _ctx_with(True)
    out = await web.browse.handler({"url": "https://x.test", "screenshot": True})
    assert "call `see`" in out["content"][0]["text"]


async def test_browse_does_not_offer_see_to_a_text_only_model():
    """MachX isn't multimodal, so `see` is stripped from the toolset — but the
    browse result still told DeepSeek to call it, and the call went nowhere."""
    _ctx_with(False)
    text = (await web.browse.handler({"url": "https://x.test", "screenshot": True}))["content"][0]["text"]
    assert "call `see`" not in text
    assert "/var/shot.png" in text  # the path is still worth reporting


def test_searxng_fts_query():
    from dream.memory.store import _fts_query

    assert _fts_query("hello world") == "hello* OR world*"
    assert _fts_query("!!!") is None
