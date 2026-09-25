"""Dream's hidden model frame: a headless Chromium page the model loads an artifact
into, reads the console of, evaluates JS in, and screenshots — without disturbing
what the user is looking at in Studio, and without needing Studio to be open at all.

Playwright's bundled Chromium, headless. One page, kept warm and reused, so state
set by one `eval_js` is still there for the next. Network is blocked wholesale —
the rule the Studio panel enforces with its CSP — so a page the model wrote can
render local files but cannot phone home: only file:, data:, blob:, and about:
requests are allowed through. Closes itself after idling, like the Camoufox
browser does.

The page itself parks (DREAM-111) -- it goes to about:blank, so nothing of it runs or
renders -- right after `done` and after PARK_IDLE_S unused; `parked` remembers the
file, and the next eval, screenshot or load reopens it. Live, a heavy three.js scene
kept rendering here after `done`, on the same GPU as the owner's Studio.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

from .. import config

VIEWPORT = {"width": 1280, "height": 800}
# With GPU rendering the BROWSER stays up across a slow local model's steps (minutes
# each) instead of relaunching for every check; its page parks sooner (PARK_IDLE_S).
GPU_IDLE_S = 1800
# A page nobody used for this long parks (about:blank): a render loop left running
# costs the GPU the owner's Studio draws with. Reopening costs one reload.
PARK_IDLE_S = 60
_RENDERER_JS = """() => { const gl = document.createElement('canvas').getContext('webgl');
  if (!gl) return 'none'; const e = gl.getExtension('WEBGL_debug_renderer_info');
  return e ? gl.getParameter(e.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER); }"""


def display_render_node() -> str | None:
    """The render node of the GPU that drives the displays (boot_vga), so WebGL in
    the hidden frame runs on that GPU. Chromium's default is SwiftShader on the CPU:
    a three.js page took ~13 cores there and halved a local model's decode speed
    (Dream fix #5). Never a card a local model sits on, unless it is the display's."""
    if sys.platform != "linux":
        return None
    for node in sorted(Path("/dev/dri").glob("renderD*")):
        try:
            if (Path("/sys/class/drm") / node.name / "device" / "boot_vga").read_text().strip() == "1":
                return str(node)
        except OSError:
            continue
    return None
LOAD_TIMEOUT_MS = 15_000
# Under Playwright's own 30 s default on purpose: when both fire, ours must win,
# so the model reads "the page was reset — reload", not a navigation error.
EVAL_TIMEOUT_MS = 25_000
# Let a page's own post-load work (timers, layout, a thrown error) land before
# the logs are read back. Short on purpose; `sleep` exists for the rest.
SETTLE_MS = 150
MAX_CAPTURE_STEPS = 100
# A page that floods the console must not grow memory without bound; the tools
# show the head anyway. Past this, one marker line stands in for the rest.
MAX_LOG_LINES = 2000
DEFAULT_STEP_DELAY_MS = 200
JPEG_QUALITY = 60
_ALLOWED_SCHEMES = ("file:", "data:", "blob:", "about:")


def _allowed(url: str) -> bool:
    return url.startswith(_ALLOWED_SCHEMES)


def _same_document(url: str, loaded: Path | None) -> bool:
    """A hash change (a deck's #3, a stage's position) is the same document."""
    return loaded is None or url.split("#", 1)[0] == loaded.as_uri()


class Preview:
    def __init__(self) -> None:
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._lock = asyncio.Lock()
        # One page, one operation at a time: two loads interleaving would clear
        # each other's logs; an eval racing a load reads a half-built page.
        self._op = asyncio.Lock()
        self._reaper: asyncio.Task | None = None
        self._dropped = 0
        # True only while WE are opening a page, so the popup-closer can tell
        # our own page (the "page" event fires before new_page() returns) from
        # one the artifact opened.
        self._expecting_page = False
        self.last_used = 0.0
        self.loaded: Path | None = None
        # The file the page held when it parked (DREAM-111); None while loaded or fresh.
        self.parked: Path | None = None
        # How many times a call found its page parked and loaded it again (the tools say so).
        self.reopens = 0
        # What WebGL renders with in this frame, told to the model with every load.
        self.renderer = ""
        self.gpu = False
        self.logs: list[str] = []
        self.blocked: list[str] = []
        # PNG captures stashed by key for a later `run_script` (Phase 7).
        self.captures: dict[str, list[bytes]] = {}

    @property
    def running(self) -> bool:
        return self._page is not None

    # --- lifecycle ------------------------------------------------------------------

    async def _ensure(self) -> None:
        if self._page is not None:
            return
        async with self._lock:
            if self._page is not None:
                return
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            # --allow-file-access-from-files: Babel loads a `<script type="text/babel"
            # src="x.jsx">` by XHR, and Chromium refuses file→file XHR by default,
            # which would make every multi-file JSX page fail in the hidden frame.
            # A page can then read local files into itself — the same reads the
            # model already has for free — but still cannot send them anywhere:
            # the network stays blocked.
            node = display_render_node()
            if node:
                self._browser = await self._pw.chromium.launch(
                    headless=True, ignore_default_args=["--disable-gpu"],
                    args=["--allow-file-access-from-files", "--enable-gpu", "--ignore-gpu-blocklist",
                          "--use-gl=angle", "--use-angle=gl-egl", f"--render-node-override={node}"])
                self.renderer = await self._probe_renderer()
                if self.renderer == "none" or "swiftshader" in self.renderer.lower():
                    await self._browser.close()      # the GPU path did not come up
                    node = None
            if not node:
                self._browser = await self._pw.chromium.launch(
                    headless=True, args=["--allow-file-access-from-files"])
                self.renderer = await self._probe_renderer()
            self.gpu = bool(node)
            self._context = await self._browser.new_context(viewport=VIEWPORT)
            await self._context.route("**/*", self._route)
            # A popup is a second page nothing will ever look at. Close it.
            self._context.on("page", self._on_popup)
            self._page = await self._new_page()
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap_loop())

    async def _probe_renderer(self) -> str:
        try:
            page = await self._browser.new_page()
            try:
                return str(await page.evaluate(_RENDERER_JS))
            finally:
                await page.close()
        except Exception as e:
            return f"unknown ({type(e).__name__})"

    def renderer_note(self) -> str:
        """One line for the model: which GPU the frame's WebGL timings describe."""
        if not self.renderer:
            return ""
        if "swiftshader" in self.renderer.lower():
            return ("WebGL renderer: SwiftShader — software rendering on the CPU. Frame rates and "
                    "timings here are far below a real GPU; do not budget quality against them.")
        return (f"WebGL renderer: {self.renderer} — the preview's GPU, not necessarily the "
                "viewer's; treat frame rates as a rough guide.")

    async def _new_page(self) -> Any:
        self._expecting_page = True
        try:
            page = await self._context.new_page()
        finally:
            self._expecting_page = False
        page.on("console", lambda m: self._log(f"console.{m.type}: {m.text}"))
        page.on("pageerror", lambda e: self._log(f"error: {e}"))
        return page

    def _log(self, line: str) -> None:
        if len(self.logs) < MAX_LOG_LINES:
            self.logs.append(line)
            return
        self._dropped += 1
        marker = f"[… {self._dropped} more console line(s) dropped]"
        if self.logs and self.logs[-1].startswith("[… "):
            self.logs[-1] = marker
        else:
            self.logs.append(marker)

    async def _on_popup(self, page: Any) -> None:
        if self._expecting_page or page is self._page:
            return
        self._log("blocked: a popup window was opened and closed (the preview is one page)")
        try:
            await page.close()
        except Exception:
            pass

    async def _recycle_page(self) -> None:
        """A page whose script never yields (an infinite loop, a load that never
        finishes) would wedge every later call. Replace it; the browser stays."""
        old, self._page = self._page, None
        self.loaded = None
        if old is not None:
            try:
                await asyncio.wait_for(old.close(), 5)
            except Exception:
                pass
        if self._context is not None:
            self._page = await self._new_page()

    async def _route(self, route: Any) -> None:
        url = route.request.url
        try:
            if _allowed(url):
                await route.continue_()
                return
            if len(self.blocked) < MAX_LOG_LINES:
                self.blocked.append(url)
            self._log(f"blocked: {url} (the preview has no network, like the Studio panel)")
            await route.abort()
        except Exception:
            # A request still in flight when the page or browser went away. There
            # is nothing to deliver it to; a traceback here is noise.
            pass

    async def _reset(self) -> None:
        async with self._lock:
            pw, browser, context = self._pw, self._browser, self._context
            self._pw = self._browser = self._context = self._page = None
            self.loaded = None
        # Innermost first, and a yield between each, so handlers still in flight
        # (a routed request, a console line) finish against a live object instead
        # of surfacing as "Future exception was never retrieved" at teardown.
        for closer in (getattr(context, "close", None), getattr(browser, "close", None),
                       getattr(pw, "stop", None)):
            if closer is not None:
                try:
                    await closer()
                except Exception:
                    pass
                await asyncio.sleep(0)

    async def _reap_loop(self) -> None:
        idle = config.BROWSER_IDLE_SHUTDOWN_S
        if idle > 0 and self.gpu:
            idle = max(idle, GPU_IDLE_S)
        tick = min(t for t in (30, PARK_IDLE_S, idle) if t > 0)
        try:
            while self._page is not None:
                await asyncio.sleep(tick)
                quiet = time.monotonic() - self.last_used
                if self._page is None:
                    return
                if idle > 0 and quiet >= idle:
                    await self._reset()
                    return
                if self.loaded is not None and quiet >= PARK_IDLE_S:
                    await self.park(unused_since=self.last_used)
        except asyncio.CancelledError:
            pass

    async def park(self, *, unused_since: float | None = None) -> bool:
        """Stop the loaded page: it goes to about:blank, so no script or render loop of it
        runs; `parked` keeps the file for the next eval, screenshot or load to reopen.
        False when nothing is loaded, or (``unused_since``) the page was used since."""
        if self._page is None:
            return False
        async with self._op:
            if self.loaded is None or (unused_since is not None and self.last_used != unused_since):
                return False
            parked = self.loaded
            try:
                await asyncio.wait_for(self._page.goto("about:blank"), 10)
            except Exception:
                try:
                    await self._recycle_page()   # a page that will not leave is replaced
                except Exception:
                    pass                         # the browser is gone: nothing of the page runs either
            self.loaded, self.parked = None, parked
            return True

    async def aclose(self) -> None:
        reaper, self._reaper = self._reaper, None
        if reaper is not None and not reaper.done():
            reaper.cancel()
            try:
                await reaper
            except Exception:
                pass
        await self._reset()

    # --- the four things the model can do ---------------------------------------------

    async def load(self, path: Path) -> list[str]:
        """Navigate to a local file; return the console since this load."""
        await self._ensure()
        async with self._op:
            return await self._load_locked(path)

    async def _load_locked(self, path: Path) -> list[str]:
        self.last_used = time.monotonic()
        self.logs.clear()
        self.blocked.clear()
        self._dropped = 0
        try:
            await asyncio.wait_for(
                self._page.goto(path.resolve().as_uri(), wait_until="load",
                                timeout=LOAD_TIMEOUT_MS),
                LOAD_TIMEOUT_MS / 1000 + 5,
            )
            await asyncio.wait_for(self._page.wait_for_timeout(SETTLE_MS), 5)
        except Exception:
            await self._recycle_page()
            raise
        self.loaded, self.parked = path.resolve(), None
        self.last_used = time.monotonic()
        await self._check_page()
        return list(self.logs)

    async def _unpark_locked(self) -> None:
        """With the operation lock held: a page parked while this call waited for the lock is
        loaded again before the call runs (DREAM-111, gate 1). Checked before the lock, a park
        that finished in between left eval, screenshot and pdf working on about:blank."""
        if self.loaded is None and self.parked is not None:
            self.reopens += 1
            await self._load_locked(self.parked)

    async def _check_page(self) -> None:
        """Two things Chromium does not report on the console: an XML parse error
        (it renders its own error document and says nothing) and a page that sent
        itself somewhere else (a blocked http navigation lands on chrome-error://).
        Both make the loaded document not the file, so both become error lines."""
        page = self._page
        url = page.url
        if not _same_document(url, self.loaded):
            self._log(f"error: the page navigated away from {self.loaded.name} to {url}; "
                      f"what is loaded now is not your file")
            return
        try:
            bad = await page.evaluate(
                "!!document.querySelector('parsererror') || "
                "/This page contains the following errors/.test(document.documentElement.textContent)")
        except Exception:
            bad = False
        if bad:
            self._log("error: the document did not parse (XML/SVG syntax error) — Chromium "
                      "rendered its error page instead of the file")

    async def eval(self, code: str) -> Any:
        """Evaluate JS on the loaded page. An expression returns its value; a block
        of statements returns what it `return`s (or null). A parked page is reopened
        first (a fresh load: state set by earlier evals is gone)."""
        await self._ensure()
        async with self._op:
            await self._unpark_locked()
            return await self._eval(code)

    async def _eval(self, code: str) -> Any:
        self.last_used = time.monotonic()
        page = self._page
        if not _same_document(page.url, self.loaded):
            raise RuntimeError(f"the page navigated away from {self.loaded.name} to {page.url}; "
                               f"reload it with show_html before evaluating")
        # Expression or statements? Ask the parser, not the runtime — running the
        # code to find out would run it twice when it merely throws a SyntaxError.
        try:
            kind = await page.evaluate(
                "c => { try { new Function('return (' + c + '\\n)'); return 'expr'; } "
                "catch (e) { return 'stmt'; } }", code)
        except Exception:
            kind = "expr"
        body = code if kind == "expr" else "(async () => {\n" + code + "\n})()"
        try:
            return await asyncio.wait_for(page.evaluate(body), EVAL_TIMEOUT_MS / 1000)
        except asyncio.TimeoutError:
            # The script may still be running; a page that never yields would
            # wedge everything after it. Replace it and say so.
            loaded = self.loaded
            await self._recycle_page()
            raise TimeoutError(
                f"eval did not finish within {EVAL_TIMEOUT_MS // 1000}s; the page was "
                f"reset — reload {loaded.name if loaded else 'it'} with show_html before "
                f"the next call")

    async def screenshot(self, steps: list[dict[str, Any]], *, hq: bool = False,
                         save_to: Path | None = None, key: str | None = None) -> list[Path]:
        """Run each step (optional JS, then a delay), capturing after each.

        `save_to` names a file; with several steps the captures get numeric
        prefixes (`01-name.png`). `key` stashes PNG bytes in `captures` instead.
        Exactly one of the two."""
        if (save_to is None) == (key is None):
            raise ValueError("give exactly one of save_to or key")
        if not steps or len(steps) > MAX_CAPTURE_STEPS:
            raise ValueError(f"steps must have 1..{MAX_CAPTURE_STEPS} entries")
        await self._ensure()
        async with self._op:
            await self._unpark_locked()
            self.last_used = time.monotonic()
            page = self._page
            png = hq or key is not None or (save_to is not None and save_to.suffix.lower() == ".png")
            out: list[Path] = []
            blobs: list[bytes] = []
            for i, step in enumerate(steps, 1):
                code = step.get("code")
                if code:
                    await self._eval(str(code))
                delay = step.get("delay", DEFAULT_STEP_DELAY_MS)
                await page.wait_for_timeout(max(0, min(int(delay), 10_000)))
                if png:
                    data = await page.screenshot(type="png")
                else:
                    data = await page.screenshot(type="jpeg", quality=JPEG_QUALITY)
                if key is not None:
                    blobs.append(data)
                    continue
                width = max(2, len(str(len(steps))))
                target = save_to if len(steps) == 1 else save_to.with_name(f"{i:0{width}d}-{save_to.name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                out.append(target)
            if key is not None:
                self.captures[key] = blobs
            self.last_used = time.monotonic()
            return out


    # --- exports and scripts ------------------------------------------------------

    async def pdf(self, out: Path, *, width: int | None = None, height: int | None = None,
                  landscape: bool = False) -> Path:
        """Print the loaded page to PDF. With width/height (CSS px) each page is
        that size — one page per slide for a deck that prints one section per
        page; without, A4/Letter follows the page's own @page rule."""
        await self._ensure()
        async with self._op:
            await self._unpark_locked()
            if self.loaded is None:
                raise RuntimeError("nothing is loaded; show_html first")
            await self._page.emulate_media(media="print")
            try:
                kwargs: dict[str, Any] = {"path": str(out), "print_background": True,
                                          "prefer_css_page_size": True}
                if width and height:
                    kwargs.update({"width": f"{int(width)}px", "height": f"{int(height)}px",
                                   "prefer_css_page_size": False})
                else:
                    kwargs["landscape"] = landscape
                await self._page.pdf(**kwargs)
            finally:
                await self._page.emulate_media(media="screen")
        return out

    async def run_script(self, code: str, workspace: Path, *, timeout_s: float = 30.0) -> list[str]:
        """Run async JS in a scratch page of the same browser, with file helpers
        bound to the workspace: readFile, readFileBinary (bytes as a Blob),
        readImage, saveFile (string, Canvas, or Blob), ls, getCaptures (the PNGs
        save_screenshot stashed by key), createCanvas, log. Paths stay inside the
        workspace; the page has no network. Returns the log lines."""
        await self._ensure()
        ws = workspace.resolve()
        logs: list[str] = []

        def inside(rel: str) -> Path:
            p = (ws / str(rel)).resolve()
            if not p.is_relative_to(ws):
                raise ValueError(f"{rel}: outside the workspace")
            return p

        async def b_read(_src: Any, rel: str) -> str:
            return inside(rel).read_text(encoding="utf-8", errors="replace")

        async def b_read_b64(_src: Any, rel: str) -> str:
            import base64
            return base64.b64encode(inside(rel).read_bytes()).decode("ascii")

        async def b_save(_src: Any, rel: str, text_or_b64: str, binary: bool) -> str:
            import base64
            p = inside(rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            if binary:
                p.write_bytes(base64.b64decode(text_or_b64))
            else:
                p.write_text(text_or_b64, encoding="utf-8")
            return str(p.relative_to(ws))

        async def b_ls(_src: Any, rel: str) -> list[str]:
            p = inside(rel or ".")
            if not p.is_dir():
                raise ValueError(f"{rel}: not a directory")
            return sorted(e.name + ("/" if e.is_dir() else "") for e in p.iterdir())

        async def b_captures(_src: Any, key: str) -> list[str]:
            import base64
            return [base64.b64encode(b).decode("ascii") for b in self.captures.get(key, [])]

        async def b_log(_src: Any, line: str) -> None:
            if len(logs) < MAX_LOG_LINES:
                logs.append(str(line))

        self._expecting_page = True
        try:
            page = await self._context.new_page()
        finally:
            self._expecting_page = False
        try:
            for name, fn in (("__readFile", b_read), ("__readFileB64", b_read_b64), ("__saveFile", b_save),
                             ("__ls", b_ls), ("__captures", b_captures), ("__log", b_log)):
                await page.expose_binding(name, fn)
            await page.set_content("<!doctype html><title>run_script</title>")
            helpers = r"""
            const log = (...a) => window.__log(a.map(x => typeof x === 'string' ? x : JSON.stringify(x)).join(' '));
            const readFile = p => window.__readFile(String(p));
            const b64ToBlob = (b64, type) => { const s = atob(b64); const u = new Uint8Array(s.length);
              for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i); return new Blob([u], {type: type || 'application/octet-stream'}); };
            const readFileBinary = async p => b64ToBlob(await window.__readFileB64(String(p)));
            const readImage = async p => { const blob = await readFileBinary(p); const url = URL.createObjectURL(blob);
              const img = new Image(); await new Promise((ok, no) => { img.onload = ok; img.onerror = () => no(new Error('not an image: ' + p)); img.src = url; }); return img; };
            const blobToB64 = blob => new Promise((ok, no) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(',')[1]); r.onerror = no; r.readAsDataURL(blob); });
            const saveFile = async (p, data) => {
              if (typeof data === 'string') return window.__saveFile(String(p), data, false);
              if (data && data.tagName === 'CANVAS') return window.__saveFile(String(p), data.toDataURL('image/png').split(',')[1], true);
              if (data instanceof Blob) return window.__saveFile(String(p), await blobToB64(data), true);
              throw new Error('saveFile: data must be a string, a Canvas, or a Blob');
            };
            const ls = p => window.__ls(p === undefined ? '' : String(p));
            const getCaptures = async key => (await window.__captures(String(key))).map(b => b64ToBlob(b, 'image/png'));
            const createCanvas = (w, h) => { const c = document.createElement('canvas'); c.width = w; c.height = h; return c; };
            """
            wrapped = "(async () => {" + helpers + "\n" + code + "\n})()"
            try:
                await asyncio.wait_for(page.evaluate(wrapped), timeout_s)
            except asyncio.TimeoutError:
                raise ScriptTimeout(logs) from None
        finally:
            try:
                await page.close()
            except Exception:
                pass
        return logs


class ScriptTimeout(asyncio.TimeoutError):
    """run_script hit its limit; `logs` is what it printed before that."""

    def __init__(self, logs: list[str]) -> None:
        super().__init__()
        self.logs = logs


_PREVIEW: Preview | None = None


def get_preview() -> Preview:
    global _PREVIEW
    if _PREVIEW is None:
        _PREVIEW = Preview()
    return _PREVIEW
