"""One self-contained HTML file: every local script, stylesheet, image, and font
the page references, inlined — so it opens offline, from anywhere, as it was.

The Design prompt's super_inline_html. Same rules: only files inside the page's
folder tree are inlined (a `../` or absolute path is left alone and reported);
remote URLs are left alone (the frames have no network anyway); a required
`<template id="__bundler_thumbnail">` is checked so the bundle has a splash.
"""

from __future__ import annotations

import base64
import mimetypes
import os
import re
import stat
from pathlib import Path
from urllib.parse import unquote, urlsplit

MAX_ASSET_BYTES = 8 * 1024 * 1024
MAX_STUDIO_MEDIA_BYTES = 32 * 1024 * 1024
_URL_RE = re.compile(r"""url\(\s*(['"]?)([^'")]+)\1\s*\)""")
_REMOTE = ("http://", "https://", "//", "data:", "blob:", "about:", "#", "javascript:")


def _read_studio_media(path: Path, limit: int) -> bytes:
    """Pin each resolved path component so replacements cannot redirect reads."""
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("local media must be a regular file")
            if info.st_size > limit:
                raise ValueError("local media exceeds the preview size limit; use a smaller preview or download")
            return stream.read(limit + 1)
    finally:
        os.close(directory)


def prepare_studio_media(html_path: Path, html: str) -> str:
    """Embed local media for the opaque Studio frame, without changing disk files.

    Unlike export bundling, this needs no thumbnail and leaves non-media pages
    byte-for-byte unchanged. Remote references stay subject to the frame CSP.
    """
    from bs4 import BeautifulSoup

    root = html_path.resolve().parent
    soup = BeautifulSoup(html, "html.parser")
    total = 0
    changed = False
    for tag in soup.find_all(["video", "audio", "source", "track"]):
        if tag.name == "source" and (tag.parent is None or tag.parent.name not in {"video", "audio"}):
            continue
        for attr in (["src", "poster"] if tag.name == "video" else ["src"]):
            ref = tag.get(attr)
            if not isinstance(ref, str) or not ref.strip():
                continue
            ref = ref.strip()
            try:
                url = urlsplit(ref)
                if url.scheme.lower() in {"http", "https", "data", "blob"} or url.netloc or ref.startswith("#"):
                    continue
                if url.scheme or ref.startswith(("/", "\\")):
                    raise ValueError("media must use a relative path inside the page folder")
                path = (root / unquote(url.path)).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    raise ValueError("media is missing or outside the page folder")
                mime = mimetypes.guess_type(path.name)[0] or ""
                allowed = (mime.startswith("image/") if attr == "poster" else
                           mime == "text/vtt" if tag.name == "track" else
                           mime.startswith(("video/", "audio/")))
                if not allowed:
                    raise ValueError("unsupported local media type")
                limit = min(MAX_ASSET_BYTES, MAX_STUDIO_MEDIA_BYTES - total)
                payload = _read_studio_media(path, limit)
                if len(payload) > limit:
                    raise ValueError("local media exceeds the preview size limit; use a smaller preview or download")
            except (OSError, RuntimeError, ValueError) as exc:
                raise ValueError(f"Studio media preparation failed for {ref!r}: {exc}") from exc
            total += len(payload)
            tag[attr] = f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"
            changed = True
    return str(soup) if changed else html


def _local(ref: str, base: Path, root: Path) -> Path | None:
    ref = unquote(ref.split("#", 1)[0].split("?", 1)[0])
    if not ref or ref.startswith(_REMOTE) or ref.startswith("/"):
        return None
    p = (base / ref).resolve()
    if not p.is_relative_to(root) or not p.is_file():
        return None
    return p


def _data_uri(p: Path) -> str:
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"


def _inline_css(css: str, base: Path, root: Path, report: list[str]) -> str:
    def repl(m: re.Match) -> str:
        p = _local(m.group(2), base, root)
        if p is None:
            return m.group(0)
        if p.stat().st_size > MAX_ASSET_BYTES:
            report.append(f"left alone (over {MAX_ASSET_BYTES // (1024 * 1024)} MB): {p.name}")
            return m.group(0)
        report.append(f"inlined {p.relative_to(root)}")
        return f"url({_data_uri(p)})"
    return _URL_RE.sub(repl, css)


def bundle(html_path: Path, root: Path | None = None) -> tuple[str, list[str]]:
    """Return (bundled html, report lines). ``root`` bounds what may be inlined;
    default: the page's own folder."""
    from bs4 import BeautifulSoup

    html_path = html_path.resolve()
    base = html_path.parent
    root = (root or base).resolve()
    report: list[str] = []
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8", errors="replace"), "lxml")

    if soup.find("template", id="__bundler_thumbnail") is None:
        raise ValueError('the page needs a <template id="__bundler_thumbnail"> holding a simple '
                         "SVG splash (an icon or 1–2 letters on a colored background); it shows "
                         "while the bundle unpacks and when scripts are off")

    for tag in soup.find_all("script", src=True):
        p = _local(tag["src"], base, root)
        if p is None:
            report.append(f"left alone: script {tag['src']}")
            continue
        code = p.read_text(encoding="utf-8", errors="replace")
        report.append(f"inlined {p.relative_to(root)}")
        del tag["src"]
        tag.string = code.replace("</script", "<\\/script")

    for tag in soup.find_all("link", rel=lambda v: v and "stylesheet" in v, href=True):
        p = _local(tag["href"], base, root)
        if p is None:
            report.append(f"left alone: stylesheet {tag['href']}")
            continue
        css = _inline_css(p.read_text(encoding="utf-8", errors="replace"), p.parent, root, report)
        report.append(f"inlined {p.relative_to(root)}")
        style = soup.new_tag("style")
        style.string = css
        tag.replace_with(style)

    for tag in soup.find_all("style"):
        if tag.string:
            tag.string = _inline_css(tag.string, base, root, report)

    for tag in soup.find_all(["img", "source", "video", "audio", "track"], src=True):
        p = _local(tag["src"], base, root)
        if p is None:
            continue
        if p.stat().st_size > MAX_ASSET_BYTES:
            report.append(f"left alone (over {MAX_ASSET_BYTES // (1024 * 1024)} MB): {p.name}")
            continue
        report.append(f"inlined {p.relative_to(root)}")
        tag["src"] = _data_uri(p)

    for tag in soup.find_all(attrs={"style": True}):
        tag["style"] = _inline_css(tag["style"], base, root, report)

    out = str(soup)
    if not out.lstrip().lower().startswith("<!doctype"):
        out = "<!doctype html>\n" + out
    return out, report
