"""Boot latency guards: the web stack must not load until something actually browses.

``dream.tools.context`` pulls in ``dream.web.browser``, so anything eagerly imported at
the top of ``browser.py`` lands in every session's startup path — including the
overwhelming majority that never open a page. camoufox (~160ms) and trafilatura
(~200ms) are the expensive ones. The sys.modules assertions below are the sharp guard;
the timing test is a loose backstop for any *new* heavy import.

Nothing here launches a real browser: the fetch tests replace ``_load_camoufox``, so a
regression to an eager import fails on the missing hook rather than opening Firefox.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import types

import pytest

# Cheap to import in-process — that is the whole point of this module.
from dream.web import browser as browser_mod

_PROBE = """
import sys
import dream.tui.app  # noqa: F401
print(",".join(sorted(m for m in sys.modules if m.split(".")[0] in {"camoufox", "trafilatura"})))
"""


def _run(code: str, *py_args: str) -> subprocess.CompletedProcess[str]:
    # A subprocess is the only honest check: this pytest session has already imported
    # much of the tree itself.
    return subprocess.run(
        [sys.executable, *py_args, "-c", code],
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_tui_import_does_not_pull_in_web_stack():
    proc = _run(_PROBE)
    assert proc.returncode == 0, proc.stderr
    leaked = proc.stdout.strip()
    assert leaked == "", f"importing dream.tui.app eagerly imported: {leaked}"


def test_tui_import_stays_under_ceiling():
    # Measured ~290ms here; the ceiling is deliberately loose so it fires only when a
    # genuinely heavy dependency moves back onto the import path, not on a slow box.
    ceiling_us = 500_000
    proc = _run("import dream.tui.app", "-X", "importtime")
    assert proc.returncode == 0, proc.stderr
    cumulative = None
    for line in proc.stderr.splitlines():
        parts = line.split("|")
        if len(parts) == 3 and parts[2].strip() == "dream.tui.app":
            cumulative = int(parts[1])
    assert cumulative is not None, proc.stderr
    assert cumulative < ceiling_us, f"dream.tui.app import cost {cumulative}us"


def test_extract_text_still_works_both_paths():
    # trafilatura path.
    html = (
        "<html><body><article><h1>Headline</h1>"
        + "<p>The quick brown fox jumps over the lazy dog, repeatedly and at length.</p>"
        * 5
        + "</article></body></html>"
    )
    assert "quick brown fox" in browser_mod.extract_text(html, "https://example.com")
    # BeautifulSoup fallback, for markup trafilatura declines to extract.
    bare = "<html><body><p>Hello world.</p><script>x=1</script></body></html>"
    text = browser_mod.extract_text(bare, "https://example.com")
    assert "Hello world" in text
    assert "x=1" not in text


class _FakeCamoufox:
    def __init__(self, **kw):
        self.kwargs = kw

    async def __aenter__(self):
        return _FakeBrowser()

    async def __aexit__(self, *exc):
        return False


class _FakeBrowser:
    async def new_context(self, **kw):
        return _FakeContext()


class _FakeContext:
    async def new_page(self):
        return _FakePage()

    async def close(self):
        pass


class _FakePage:
    url = "https://example.com/final"

    async def goto(self, url, **kw):
        return types.SimpleNamespace(status=200)

    async def title(self):
        return "Fake"

    async def content(self):
        return "<html><body><p>Fake body text.</p></body></html>"


def _fake_camoufox_module() -> types.ModuleType:
    pkg = types.ModuleType("camoufox")
    api = types.ModuleType("camoufox.async_api")
    api.AsyncCamoufox = _FakeCamoufox
    pkg.async_api = api
    return pkg


def test_load_camoufox_resolves_the_async_api_class(monkeypatch):
    pkg = _fake_camoufox_module()
    monkeypatch.setitem(sys.modules, "camoufox", pkg)
    monkeypatch.setitem(sys.modules, "camoufox.async_api", pkg.async_api)
    assert browser_mod._load_camoufox() is _FakeCamoufox


def test_load_camoufox_raises_when_missing(monkeypatch):
    # None in sys.modules makes the import fail without touching the real package.
    monkeypatch.setitem(sys.modules, "camoufox", None)
    monkeypatch.setitem(sys.modules, "camoufox.async_api", None)
    with pytest.raises(RuntimeError, match="Camoufox is not importable"):
        browser_mod._load_camoufox()


async def test_fetch_launches_through_the_lazy_loader(monkeypatch):
    monkeypatch.setattr(browser_mod, "_load_camoufox", lambda: _FakeCamoufox)
    monkeypatch.setattr(browser_mod.config, "BROWSER_IDLE_SHUTDOWN_S", 0)

    b = browser_mod.Browser()
    res = await b.fetch("example.com")
    assert "error" not in res, res
    assert res["status"] == 200
    assert res["title"] == "Fake"
    assert res["final_url"] == "https://example.com/final"
    assert "Fake body text" in res["text"]
    assert b.running
    # The fakes never touch real I/O, so give the reaper task the tick it would
    # normally get before shutdown cancels it.
    await asyncio.sleep(0)
    await b.aclose()
    assert not b.running


async def test_fetch_reports_unavailable_camoufox_as_an_error(monkeypatch):
    """Import failure still surfaces as a fetch error dict, never a raise."""

    def boom():
        raise RuntimeError("Camoufox is not importable: No module named 'camoufox'")

    monkeypatch.setattr(browser_mod, "_load_camoufox", boom)
    res = await browser_mod.Browser().fetch("https://example.com")
    assert "error" in res
    assert "Camoufox is not importable" in res["error"]


def test_get_browser_is_a_singleton():
    assert browser_mod.get_browser() is browser_mod.get_browser()
