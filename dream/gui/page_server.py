"""Open in browser (DREAM-111): the Studio's page, served for the owner's own browser.

The owner runs the model's pages -- a three.js scene built from ES modules -- fine in a normal
browser, and wanted one click to open one there. A file:// URL cannot do it: Chrome refuses
module scripts from file://. So workspace files are served over http, but from a SEPARATE
origin, never Dream's own: a page from the workspace must never be able to call Dream's API
with the owner's credentials. Dream's API authenticates every call with its per-session token
(the X-Dream-Token header or a ?token= query; ?token= on the websocket; no cookie); this origin
never holds it, and being another origin it cannot read Dream's responses either.

The rules, each one a line below:
- its own port on 127.0.0.1 (not configurable), and a request under any other host name is
  refused (a DNS-rebinding page cannot borrow it);
- an unguessable per-session token as the first path segment, different from Dream's;
- read-only (GET), files only: no directory listing, nothing hidden (a dot-segment), 404 for
  anything outside the workspace -- `..`, encoded or not, an absolute path, a symlink whose
  target is outside (realpath check), and a file swapped for a symlink after that check (the
  path is opened one component at a time, never following a link);
- every response says `X-Content-Type-Options: nosniff`, `Cache-Control: no-store` and
  `Referrer-Policy: no-referrer`.
"""

from __future__ import annotations

import asyncio
import mimetypes
import os
import secrets
import stat
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from starlette.background import BackgroundTask
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.routing import Route, Router

HEADERS = [(b"x-content-type-options", b"nosniff"), (b"cache-control", b"no-store"),
           (b"referrer-policy", b"no-referrer")]
# A module script needs a JavaScript type (nosniff makes the browser hold us to it).
TYPES = {".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json", ".map": "application/json",
         ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".webp": "image/webp", ".gif": "image/gif", ".avif": "image/avif", ".ico": "image/x-icon",
         ".wasm": "application/wasm", ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
         ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf",
         ".mp4": "video/mp4", ".webm": "video/webm", ".ogg": "audio/ogg", ".mp3": "audio/mpeg",
         ".wav": "audio/wav", ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8",
         ".glsl": "text/plain; charset=utf-8", ".vert": "text/plain; charset=utf-8",
         ".frag": "text/plain; charset=utf-8"}
_CHUNK = 65_536


def confined(workspace: Path, raw: str) -> Path | None:
    """The workspace file ``raw`` (a URL path, already percent-decoded) names, resolved, or
    None: not relative, a dot-segment or hidden name, outside the workspace after every
    symlink is resolved, or not a regular file."""
    if not raw or "\0" in raw or "\\" in raw or raw.startswith("/"):
        return None
    parts = raw.split("/")
    if any(part in ("", ".", "..") or part.startswith(".") for part in parts):
        return None
    try:
        root = Path(os.path.realpath(workspace))
        real = Path(os.path.realpath(root.joinpath(*parts)))
        if real == root or not real.is_relative_to(root):
            return None
        if any(part.startswith(".") for part in real.relative_to(root).parts):
            return None
        return real if real.is_file() else None
    except OSError:
        # A name the filesystem cannot take (a component over 255 bytes, a path over PATH_MAX:
        # ENAMETOOLONG) is not a file here; before gate 1 it escaped as a bare 500.
        return None


def _open_pinned(root: Path, rel: Path) -> tuple[int, os.stat_result]:
    """Open ``root/rel`` one component at a time without following a symlink, so a path
    swapped for a link after the realpath check is refused, not read."""
    folder = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in rel.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=folder)
            os.close(folder)
            folder = child
        fd = os.open(rel.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
    finally:
        os.close(folder)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise OSError("not a regular file")
    return fd, info


def _stream(source, size: int):
    """At most the size the headers promised; the file is closed after the response."""
    left = size
    while left > 0 and (chunk := source.read(min(_CHUNK, left))):
        left -= len(chunk)
        yield chunk


class PageServer:
    """Serves the Studio session's workspace, read-only, on a separate loopback origin."""

    host = "127.0.0.1"   # not a parameter: nothing beyond this machine may reach it

    def __init__(self, workspace: Callable[[], Path | None]) -> None:
        self.token = secrets.token_urlsafe(32)
        self.port = 0
        self._workspace = workspace
        self._server: Any = None
        self._task: Any = None
        router = Router(routes=[Route("/{token}/{path:path}", self._serve, methods=["GET"])],
                        redirect_slashes=False)

        async def app(scope, receive, send):   # the three headers on every response, 404 and 405 too
            async def with_headers(message):
                if message["type"] == "http.response.start":
                    message = {**message, "headers": [*message.get("headers", []), *HEADERS]}
                await send(message)
            await router(scope, receive, with_headers)
        self.app = app

    @property
    def ready(self) -> bool:
        return self._task is not None and not self._task.done() and bool(self.port)

    @property
    def base_url(self) -> str | None:
        return f"http://{self.host}:{self.port}/{self.token}/" if self.ready else None

    def url_for(self, target: Path) -> str | None:
        """The page-origin URL of a workspace file, or None when it is not served."""
        ws, base = self._workspace(), self.base_url
        if ws is None or base is None:
            return None
        root = Path(os.path.realpath(ws))
        real = Path(os.path.realpath(target))
        if not real.is_relative_to(root) or real == root:
            return None
        rel = real.relative_to(root).as_posix()
        return base + quote(rel, safe="/") if confined(root, rel) == real else None

    async def _serve(self, request):
        missing = PlainTextResponse("Not found", status_code=404)
        host = request.headers.get("host", "")
        if host not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            return missing
        if not secrets.compare_digest(request.path_params["token"].encode(), self.token.encode()):
            return missing
        ws = self._workspace()
        target = confined(ws, request.path_params["path"]) if ws is not None else None
        if target is None:
            return missing
        root = Path(os.path.realpath(ws))
        try:
            fd, info = _open_pinned(root, target.relative_to(root))
        except OSError:
            return missing
        kind = TYPES.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        source = os.fdopen(fd, "rb")
        return StreamingResponse(_stream(source, info.st_size), media_type=kind,
                                 headers={"content-length": str(info.st_size)}, background=BackgroundTask(source.close))

    async def start(self, *, timeout: float = 3.0) -> str:
        import uvicorn

        config = uvicorn.Config(self.app, host=self.host, port=0, log_level="warning", access_log=False)
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None   # a guest on the session's loop
        self._server.capture_signals = nullcontext
        self._task = asyncio.create_task(self._server.serve())
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            if self._task.done():
                self._task.result()
                raise RuntimeError("the page server exited before becoming ready")
            servers = getattr(self._server, "servers", None)
            if getattr(self._server, "started", False) and servers and servers[0].sockets:
                self.port = servers[0].sockets[0].getsockname()[1]
                return self.base_url
            if asyncio.get_running_loop().time() > deadline:
                await self.stop()
                raise TimeoutError(f"the page server did not start within {timeout:g}s")
            await asyncio.sleep(0.01)

    async def stop(self) -> None:
        task, self._task = self._task, None
        if self._server is not None:
            self._server.should_exit = True
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
        except BaseException:
            pass
        await asyncio.gather(task, return_exceptions=True)
        self.port = 0
