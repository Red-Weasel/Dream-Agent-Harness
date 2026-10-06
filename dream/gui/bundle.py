"""One self-contained HTML file: every local script, stylesheet, image, and font
the page references, inlined — so it opens offline, from anywhere, as it was.

The Design prompt's super_inline_html. Same rules: only files inside the page's
folder tree are inlined (a `../` or absolute path is left alone and reported);
remote URLs are left alone (the frames have no network anyway); a required
`<template id="__bundler_thumbnail">` is checked so the bundle has a splash.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import stat
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

MAX_ASSET_BYTES = 8 * 1024 * 1024
MAX_STUDIO_MEDIA_BYTES = 32 * 1024 * 1024
_URL_RE = re.compile(r"""url\(\s*(['"]?)([^'")]+)\1\s*\)""")
_REMOTE = ("http://", "https://", "//", "data:", "blob:", "about:", "#", "javascript:")
_SCRIPT_TYPES = {"", "text/javascript", "application/javascript", "text/ecmascript", "application/ecmascript",
                 "text/babel", "text/jsx"}
# A page's local ES modules (DREAM-238). The frame loads nothing by URL, so the graph is inlined: every local
# module the page reaches becomes a data: URL under a bare key in ONE importmap, and each import of it -- relative,
# or bare through the page's own importmap -- is rewritten to that key. The same file is the same key from every
# importer, so a library imported from two places is one instance (three.js relies on that). The specifier of a
# static import / export-from, a side-effect import and a dynamic import of a string literal:
_MODULE_KEY = "@dream-local/"
_MODULE_SPEC_RE = re.compile(r"""(?P<head>\b(?:import|export)\b[^'"`;()]*?\bfrom\s*|\bimport\s*\(\s*|\bimport\s*)"""
                             r"""(?P<q>['"])(?P<spec>[^'"\n]+)(?P=q)""")
# What the frame cannot run even with every local file inlined: a script that fetches a local file by URL.
_LOCAL_FETCH_RE = re.compile(r"""\bfetch\s*\(\s*(['"`])(?!(?:https?:|data:|blob:|about:|//))""")
NOTICE_ID = "__dream_notice"
NOTICE_STYLE = ("position:fixed;top:0;left:0;right:0;z-index:2147483647;box-sizing:border-box;margin:0;"
                "padding:8px 12px;background:#fff3cd;color:#5c4400;font:13px/1.4 system-ui,sans-serif;"
                "border-bottom:1px solid #e0c060")


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
    """The page as the Studio frame can run it, without changing disk files: local media
    (as before), and -- A0 -- the page's own stylesheets, classic scripts, images and the
    fonts and images its CSS names, inlined from its folder tree with the same pinned reads
    and caps -- and, DREAM-238, its local ES-module graph (a vendored three.js and its
    addons, the page's own modules), through one importmap of data: URLs. The frame loads no
    local file by URL (srcdoc), so every such reference was a blank pane before. What it
    still cannot run (a fetch() of a local file, a file that could not be inlined) is named
    in a notice at the top of the page, so the owner sees why and not a blank frame.

    Unlike export bundling, this needs no thumbnail and leaves a page with no local
    references byte-for-byte unchanged (the digest of DREAM-111 holds); an .svg is never
    changed. Remote references stay subject to the frame CSP. The hidden model frame loads
    this same body (dream.gui.preview), so the model's check is the owner's pane.
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
    if html_path.suffix.lower() in {".html", ".htm"}:
        left: list[str] = []      # files the page loads by URL that stay URLs, each with its reason
        assets = _StudioAssets(root, total, left)
        changed = _inline_page_assets(soup, assets) or changed
        changed = _inline_modules(soup, assets) or changed
        needs = _needs_site_frame(soup) + (["fetch() of a local file in a module"] if assets.module_fetch else [])
        if needs or left:
            _add_notice(soup, needs, left)
            changed = True
    return str(soup) if changed else html


class _StudioAssets:
    """The page's local files, read once each under the media rules: pinned path components,
    MAX_ASSET_BYTES per file, MAX_STUDIO_MEDIA_BYTES for the page (media counted). A file
    that cannot be inlined is left as the URL it was and named with its reason."""

    def __init__(self, root: Path, total: int, left: list[str]) -> None:
        self.root, self.total, self.left = root, total, left
        self.module_fetch = False      # an inlined module fetches a local file by URL (the notice says so)

    def read(self, ref: str, base: Path, kinds: tuple[str, ...] | None) -> tuple[Path, bytes, str] | None:
        """(path, bytes, mime) for a local reference whose type starts with one of ``kinds`` (None:
        any, a script), or None (remote, data:, outside the page folder, missing, too big, wrong type)."""
        ref = ref.strip()
        try:
            url = urlsplit(ref)
        except ValueError:
            return None
        if url.scheme or url.netloc or not url.path or ref.startswith(("/", "\\", "#")):
            return None
        shown = unquote(url.path)
        try:
            path = (base / shown).resolve()
        except (OSError, RuntimeError):
            return None
        name = shown.rsplit("/", 1)[-1] or shown
        if not path.is_relative_to(self.root):
            self.left.append(f"{name} (outside the page's folder)")
            return None
        if not path.is_file():
            self.left.append(f"{name} (missing)")
            return None
        mime = mimetypes.guess_type(path.name)[0] or ""
        if kinds is not None and not mime.startswith(kinds):
            self.left.append(f"{name} (not a {'/'.join(k.rstrip('/') for k in kinds)} file)")
            return None
        limit = min(MAX_ASSET_BYTES, MAX_STUDIO_MEDIA_BYTES - self.total)
        try:
            payload = _read_studio_media(path, limit)
        except (OSError, RuntimeError, ValueError):
            payload = None
        if payload is None or len(payload) > limit:
            self.left.append(f"{name} (over the preview's {MAX_ASSET_BYTES >> 20} MB per-file or "
                             f"{MAX_STUDIO_MEDIA_BYTES >> 20} MB per-page limit)" if path.stat().st_size > limit
                             else f"{name} (could not be read)")
            return None
        self.total += len(payload)
        return path, payload, mime

    def css(self, text: str, base: Path) -> str:
        """``url()`` references in CSS (fonts, background images) as data: URIs."""
        def repl(m: re.Match) -> str:
            got = self.read(m.group(2), base, ("image/", "font/", "application/font", "application/x-font",
                                                "application/vnd.ms-fontobject"))
            if got is None:
                return m.group(0)
            _, payload, mime = got
            return f"url(data:{mime};base64,{base64.b64encode(payload).decode('ascii')})"
        return _URL_RE.sub(repl, text)


def _inline_page_assets(soup: Any, assets: _StudioAssets) -> bool:
    changed = False
    base = assets.root
    for tag in soup.find_all("link", href=True):
        rel = tag.get("rel") or []
        if "stylesheet" not in [str(r).lower() for r in (rel if isinstance(rel, list) else [rel])]:
            continue
        got = assets.read(tag["href"], base, ("text/css",))
        if got is None:
            continue
        path, payload, _ = got
        style = soup.new_tag("style")
        if tag.get("media"):
            style["media"] = tag["media"]
        style.string = assets.css(payload.decode("utf-8", "replace"), path.parent)
        tag.replace_with(style)
        changed = True
    deferred = []
    for tag in soup.find_all("script", src=True):
        kind = str(tag.get("type") or "").strip().lower()
        if kind not in _SCRIPT_TYPES:
            continue      # a module (the notice names it), an importmap, a template
        got = assets.read(tag["src"], base, None)
        if got is None:
            continue
        del tag["src"]
        tag.string = got[1].decode("utf-8", "replace").replace("</script", "<\\/script")
        if tag.has_attr("defer") or tag.has_attr("async"):
            # A deferred script ran after the document was parsed; inline, it would run where it
            # stands (before the body it reads). It keeps its place in time: the end of the body.
            for attr in ("defer", "async"):
                if tag.has_attr(attr):
                    del tag[attr]
            deferred.append(tag)
        changed = True
    for tag in deferred:
        if soup.body is not None:
            soup.body.append(tag.extract())
    for tag in soup.find_all("img", src=True):
        got = assets.read(tag["src"], base, ("image/",))
        if got is None:
            continue
        _, payload, mime = got
        tag["src"] = f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"
        changed = True
    for tag in soup.find_all("style"):
        if tag.string and _URL_RE.search(tag.string):
            css = assets.css(tag.string, base)
            if css != tag.string:
                tag.string = css
                changed = True
    for tag in soup.find_all(attrs={"style": True}):
        if _URL_RE.search(tag["style"]):
            css = assets.css(tag["style"], base)
            if css != tag["style"]:
                tag["style"] = css
                changed = True
    return changed


def _inline_modules(soup: Any, assets: _StudioAssets) -> bool:
    """Inline the page's local ES-module graph (see _MODULE_KEY); whether the page changed. A module that
    cannot be inlined keeps its specifier and is named in ``assets.left``; remote imports and the remote
    entries of the page's importmap are left to the frame's network policy."""
    scripts = soup.find_all("script")
    kind_of = lambda tag: str(tag.get("type") or "").strip().lower()   # noqa: E731
    map_tag = next((t for t in scripts if kind_of(t) == "importmap"), None)
    try:
        declared = json.loads(map_tag.string or "{}") if map_tag is not None else {}
    except ValueError:
        return False                   # an importmap the browser refuses too: the page stays as written
    if not isinstance(declared, dict) or not isinstance(declared.get("imports") or {}, dict):
        return False
    imports = dict(declared.get("imports") or {})
    root = assets.root
    modules: dict[Path, str | None] = {}      # file -> its source, local specifiers rewritten (None while loading)

    def mapped(spec: str) -> str | None:
        """A bare specifier through the page's importmap: the exact entry, else the longest "prefix/" one."""
        if isinstance(imports.get(spec), str):
            return imports[spec]
        prefix = max((k for k, v in imports.items() if k.endswith("/") and spec.startswith(k) and isinstance(v, str)),
                     key=len, default=None)
        return imports[prefix] + spec[len(prefix):] if prefix else None

    def key_for(spec: str, base: Path) -> str | None:
        """The key of the local module ``spec`` names from a module in ``base``, inlining it on first sight;
        None when it is remote, unknown to the importmap, or could not be inlined."""
        if spec.startswith(_REMOTE):
            return None
        if not spec.startswith(("./", "../", "/")):
            spec, base = mapped(spec), root      # an importmap address is relative to the page
            if spec is None or spec.startswith(_REMOTE):
                return None
        if spec.startswith("/"):
            spec, base = "./" + spec.lstrip("/"), root
        try:
            path = (base / unquote(urlsplit(spec).path)).resolve()
        except (OSError, RuntimeError, ValueError):
            return None
        if path not in modules:
            got = assets.read(spec, base, None)
            if got is None:
                return None
            modules[path] = None
            text = got[1].decode("utf-8", "replace")
            assets.module_fetch = assets.module_fetch or bool(_LOCAL_FETCH_RE.search(text))
            modules[path] = _MODULE_SPEC_RE.sub(lambda m: rewrite(m, path.parent), text)
        return _MODULE_KEY + path.relative_to(root).as_posix()

    def rewrite(m: re.Match, base: Path) -> str:
        key = key_for(m["spec"], base)
        return m.group(0) if key is None else f"{m['head']}{m['q']}{key}{m['q']}"

    changed = False
    first_module = None
    for tag in scripts:
        if kind_of(tag) != "module":
            continue
        first_module = first_module or tag
        src = str(tag.get("src") or "").strip()
        if src:
            # A src is a URL relative to the page, never a bare specifier.
            key = key_for(src if src.startswith(_REMOTE + ("./", "../", "/")) else "./" + src, root)
            if key is not None:
                del tag["src"]
                tag.string = f'import "{key}";'
                changed = True
        elif tag.string:
            text = _MODULE_SPEC_RE.sub(lambda m: rewrite(m, root), tag.string)
            if text != tag.string:
                tag.string = text
                changed = True
    if not modules:
        return changed
    for path, text in modules.items():
        imports[_MODULE_KEY + path.relative_to(root).as_posix()] = (
            "data:text/javascript;base64," + base64.b64encode((text or "").encode("utf-8")).decode("ascii"))
    declared["imports"] = imports
    if map_tag is None:
        map_tag = soup.new_tag("script", type="importmap")
        first_module.insert_before(map_tag)      # an importmap must come before the first module script
    map_tag.string = json.dumps(declared)
    return True


def _needs_site_frame(soup: Any) -> list[str]:
    """What the frame still cannot run once everything local is inlined: a module file that stayed a URL and
    a script that fetches a local file by URL."""
    needs: list[str] = []
    for tag in soup.find_all("script"):
        kind = str(tag.get("type") or "").strip().lower()
        src = str(tag.get("src") or "").strip()
        if kind == "module" and src and not src.startswith(_REMOTE):
            needs.append(f"ES module {src}")
        if kind != "importmap" and _LOCAL_FETCH_RE.search(tag.string or ""):
            needs.append("fetch() of a local file")
    return needs


def _add_notice(soup: Any, needs: list[str], left: list[str]) -> None:
    """The owner-visible notice at the top of the page (A0): the frame paints it whatever the page
    does, where before the pane was blank and only the model heard why (DREAM-111's report)."""
    if needs:
        text = ("This page needs files the preview cannot load by URL (a file a script fetches, or a module "
                "that could not be inlined). Open in browser runs it. Found: " + "; ".join(needs[:4]))
        if left:
            text += ". Not inlined: " + "; ".join(left[:4])
    else:
        text = ("Part of this page is missing here: the preview loads nothing by URL, and these files could "
                "not be inlined: " + "; ".join(left[:4]))
    if len(needs) > 4 or len(left) > 4:
        text += "; ..."
    notice = soup.new_tag("div", id=NOTICE_ID, role="status", style=NOTICE_STYLE)
    notice.string = text + "."
    if soup.body is not None:
        soup.body.insert(0, notice)
    elif soup.html is not None:
        soup.html.append(notice)
    else:
        soup.append(notice)


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
