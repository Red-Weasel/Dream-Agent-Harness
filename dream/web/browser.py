"""A persistent, in-process Camoufox (stealth Firefox) browser.

Launched lazily on first use and kept warm so there's no cold-start per fetch. Guarded
by a lock against concurrent launches, self-heals once if the browser process dies, and
reclaims itself after a stretch of inactivity to free memory. ``fetch`` never raises —
it returns a dict with an ``error`` key on failure so the tool layer can report cleanly
and the agent can recover in-loop.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from .. import config


def _load_camoufox() -> Any:
    """Imported on first launch, not at module scope: camoufox costs ~160ms to import
    and it sits on every session's boot path, but most sessions never browse."""
    try:
        from camoufox.async_api import AsyncCamoufox
    except Exception as exc:
        raise RuntimeError(f"Camoufox is not importable: {exc}") from exc
    return AsyncCamoufox


def _slug(url: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[:40] or "page"


def _looks_dead(exc: Exception) -> bool:
    msg = f"{type(exc).__name__}: {exc}".lower()
    return any(
        k in msg
        for k in ("closed", "crash", "target", "disconnected", "browser has been")
    )


def extract_text(html: str, url: str) -> str:
    """Best readable-text extraction: trafilatura, then a BeautifulSoup fallback."""
    # Kept out of module scope for the same reason as camoufox — trafilatura is ~200ms
    # of import that only a session which actually reads a page needs to pay.
    import trafilatura

    text = trafilatura.extract(
        html, url=url, include_comments=False, include_tables=True, favor_recall=True
    )
    if text:
        return text.strip()
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        tag.decompose()
    return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n")).strip()


class Browser:
    def __init__(self) -> None:
        self._cm: Any = None
        self._browser: Any = None
        self._lock = asyncio.Lock()
        self.last_used = 0.0
        self._inflight = 0
        self._reaper: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._browser is not None

    async def _ensure(self) -> None:
        if self._browser is not None:
            return
        async_camoufox = _load_camoufox()
        async with self._lock:
            if self._browser is not None:
                return
            self._cm = async_camoufox(headless=config.BROWSER_HEADLESS, humanize=True)
            self._browser = await self._cm.__aenter__()
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap_loop())

    async def _reset(self) -> None:
        async with self._lock:
            cm = self._cm
            self._cm = None
            self._browser = None
        if cm is not None:
            try:
                await cm.__aexit__(None, None, None)
            except Exception:
                pass

    async def _reap_loop(self) -> None:
        """Close the warm browser after it's been idle long enough to be worth the
        ~200MB it holds. Exits when the browser is down; restarted on next launch."""
        idle = config.BROWSER_IDLE_SHUTDOWN_S
        if idle <= 0:
            return
        try:
            while self._browser is not None:
                await asyncio.sleep(min(idle, 30))
                if (
                    self._browser is not None
                    and self._inflight == 0
                    and (time.monotonic() - self.last_used) >= idle
                ):
                    await self._reset()
                    return
        except asyncio.CancelledError:
            pass

    async def fetch(
        self,
        url: str,
        screenshot: bool = False,
        wait_ms: int = 0,
        wait_selector: str | None = None,
    ) -> dict[str, Any]:
        """Load a page and return {url, final_url, status, title, text, screenshot}."""
        if not re.match(r"^https?://", url):
            url = "https://" + url
        try:
            await self._ensure()
            return await self._do_fetch(url, screenshot, wait_ms, wait_selector)
        except Exception as exc:
            if _looks_dead(exc):
                await self._reset()
                try:
                    await self._ensure()
                    return await self._do_fetch(url, screenshot, wait_ms, wait_selector)
                except Exception as exc2:
                    return {"url": url, "error": f"{type(exc2).__name__}: {exc2}"}
            return {"url": url, "error": f"{type(exc).__name__}: {exc}"}

    async def _do_fetch(
        self, url: str, screenshot: bool, wait_ms: int, wait_selector: str | None
    ) -> dict[str, Any]:
        self._inflight += 1
        self.last_used = time.monotonic()
        # A fresh context per fetch keeps pages isolated; no_viewport avoids a
        # Camoufox/Playwright viewport-protocol mismatch. Created before the try so
        # its close is guaranteed even if new_page/goto raises.
        context = await self._browser.new_context(no_viewport=True)
        try:
            page = await context.new_page()
            resp = await page.goto(
                url, wait_until="domcontentloaded", timeout=config.BROWSER_TIMEOUT_MS
            )
            if wait_selector:
                try:
                    await page.wait_for_selector(
                        wait_selector, timeout=config.BROWSER_TIMEOUT_MS
                    )
                except Exception:
                    pass
            if wait_ms:
                await page.wait_for_timeout(min(wait_ms, 15000))
            title = await page.title()
            html = await page.content()
            final_url = page.url
            status = resp.status if resp else None
            shot_path = None
            if screenshot:
                config.SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
                shot_path = str(
                    config.SCREENSHOT_DIR / f"{_slug(url)}-{int(time.time())}.png"
                )
                await page.screenshot(path=shot_path, full_page=True)
        finally:
            try:
                await context.close()
            except Exception:
                pass
            self.last_used = time.monotonic()
            self._inflight -= 1
        return {
            "url": url,
            "final_url": final_url,
            "status": status,
            "title": title,
            "text": extract_text(html, final_url),
            "screenshot": shot_path,
        }

    async def aclose(self) -> None:
        reaper = self._reaper
        self._reaper = None
        if reaper is not None and not reaper.done():
            reaper.cancel()
            try:
                await reaper
            except Exception:
                pass
        await self._reset()


# Process-wide singleton so all tools share one warm browser.
_BROWSER: Browser | None = None


def get_browser() -> Browser:
    global _BROWSER
    if _BROWSER is None:
        _BROWSER = Browser()
    return _BROWSER
