"""File tools the model can trust: surgical edits, search, copy, delete, image
facts, and a bounded wait — for backends without the SDK's built-ins.

Each is a dedicated tool rather than a bash incantation for two reasons. The
description is instruction: a model that reads "old_string must match exactly
once" edits differently from one told to run sed. And a dedicated tool carries its
own policy class — `grep` is read-only and free in auto mode, `delete_file` asks in
every mode — where a bash call is one class for everything it might do.

Names follow the Design prompt these were specified from, so its guidance
transfers verbatim.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from .context import ctx, err, in_thread, ok

_NEAR_MISS_QUOTE = 600


def _near_miss_hint(text: str, old: str) -> str:
    """Where old_string matches once each line's leading and trailing whitespace is ignored, quote the file's exact
    text there. A local model began three old_strings in a row mid-line WITH indentation and only "read the file
    again" came back (2026-09-23, live session 20260923-094144-f5ed)."""
    if not old.strip():
        return ""
    pattern = r"[ \t]*\r?\n[ \t]*".join(re.escape(line.strip()) for line in old.split("\n"))
    found = [m for _, m in zip(range(3), re.finditer(pattern, text))]
    if len(found) > 1:
        return (f" Ignoring indentation it would match in {len(found)}{'+' if len(found) == 3 else ''} places;"
                " widen it with a neighbouring line.")
    if not found:
        # No whitespace near miss: name the first old_string line the file lacks and the file's closest line. The
        # model's recall of its own earlier write drifted by look-alike characters (`"Fac”` for `"Fac"]`, 2026-09-23).
        import difflib
        file_lines = [l.strip() for l in text.split("\n")]
        for i, line in enumerate(old.split("\n"), 1):
            want = line.strip()
            if want and want not in text:
                close = difflib.get_close_matches(want, [l for l in file_lines if l], n=1, cutoff=0.8)   # 0.6 named unrelated lines
                if close and len(close[0]) <= _NEAR_MISS_QUOTE:
                    at = file_lines.index(close[0]) + 1
                    return (f" Line by line, line {i} of old_string is not in the file; closest is line {at}: "
                            f"{json.dumps(close[0], ensure_ascii=False)} — copy the file's text exactly.")
                return f" Line by line, line {i} of old_string is not in the file."
        return ""
    if len(found[0].group()) > _NEAR_MISS_QUOTE:
        return ""
    line = text.count("\n", 0, found[0].start()) + 1
    return (f" Ignoring indentation it matches once, at line {line}: the file has {json.dumps(found[0].group())}"
            " — copy that exactly as old_string.")


_GREP_MAX_FILES = 3000
_GREP_MAX_HITS = 100
_GREP_CONTEXT = 2
_GREP_LINE_CHARS = 300
_GREP_SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build",
}
_SLEEP_MAX = 60.0


def _resolve(raw: str) -> Path:
    """Relative paths live in the session workspace; absolute paths pass through."""
    return (ctx().workspace / Path(raw).expanduser()).resolve()


def _resolve_nofollow(raw: str) -> Path:
    """Like _resolve, but the LAST component is never followed: the parents are
    resolved, the leaf stays whatever it is. delete_file must act on a link, not
    on what the link points at (Gate 3 finding 1)."""
    p = ctx().workspace / Path(raw).expanduser()
    if p.name in ("", ".", ".."):
        return p.resolve()  # not a leaf that can be a link; ".." must not survive
    return p.parent.resolve() / p.name


def _count_overlapping(text: str, needle: str) -> int:
    """`str.count` is non-overlapping: "aa" in "aaa" counts 1 and the edit would
    still be ambiguous. Count every position instead."""
    n = start = 0
    while (i := text.find(needle, start)) != -1:
        n += 1
        start = i + 1
    return n


# --- str_replace_edit -----------------------------------------------------------------


@tool(
    "str_replace_edit",
    "Edit a file by replacing strings. Each old_string must appear EXACTLY once — zero "
    "or several matches are refused with the count. For zero matches, reread the file "
    "and copy its current text exactly; for several, include surrounding lines until "
    "unique. Pass `edits` for several replacements in one atomic call "
    "(all or none); use path+old_string+new_string OR path+edits, not both. Prefer this "
    "over rewriting a whole file, and read the file first so old_string is exact.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "edits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"old_string": {"type": "string"},
                                   "new_string": {"type": "string"}},
                    "required": ["old_string", "new_string"],
                },
            },
        },
        "required": ["path"],
    },
)
async def str_replace_edit(args: dict[str, Any]) -> dict[str, Any]:
    raw = args.get("path")
    if not raw:
        return err("str_replace_edit needs a 'path' argument.")
    single = "old_string" in args or "new_string" in args
    batch = args.get("edits")
    if single and batch is not None:
        return err("Use either old_string/new_string or edits, not both.")
    if batch is not None:
        if not isinstance(batch, list) or not batch:
            return err("'edits' must be a non-empty list of {old_string, new_string}.")
        edits: list[tuple[str, str]] = []
        for i, e in enumerate(batch):
            if not isinstance(e, dict) or "old_string" not in e or "new_string" not in e:
                return err(f"edits[{i}] needs both old_string and new_string.")
            edits.append((str(e["old_string"]), str(e["new_string"])))
    elif single:
        if args.get("old_string") is None or args.get("new_string") is None:
            return err("str_replace_edit needs both old_string and new_string.")
        edits = [(str(args["old_string"]), str(args["new_string"]))]
    else:
        return err("str_replace_edit needs old_string/new_string, or edits.")

    p = _resolve(raw)
    if not p.is_file():
        return err(f"No file at {p}")
    try:
        # newline="" keeps the file's own line endings: a CRLF file edited on one
        # line must not come back LF on every line.
        with p.open("r", encoding="utf-8", newline="") as fh:
            text = fh.read()
    except UnicodeDecodeError:
        return err(f"{p} is not UTF-8 text; str_replace_edit only edits text files.")
    except Exception as e:
        return err(f"{type(e).__name__}: {e}")

    # Verify every edit against the progressively edited text BEFORE writing, so a
    # batch is all-or-nothing and a later edit may build on an earlier one.
    new = text
    for i, (old, rep) in enumerate(edits):
        label = f"edits[{i}]: " if batch is not None else ""
        if not old:
            return err(f"{label}old_string is empty. Nothing was changed.")
        if old == rep:
            return err(f"{label}old_string and new_string are identical. Nothing was changed.")
        n = _count_overlapping(new, old)
        if n != 1:
            hint = ""
            if n == 0 and "\r\n" in new and "\n" in old and "\r\n" not in old:
                hint = (" This file uses CRLF line endings and your old_string uses bare "
                        "\\n — spell its line breaks as \\r\\n.")
            elif n == 0:
                hint = _near_miss_hint(new, old)
            recovery = ("Read the current file and copy the exact text to replace."
                        if n == 0 else "Widen it with surrounding text until it is unique.")
            return err(
                f"{label}old_string matches {n} time(s) in {p}; it must match exactly "
                f"once. {recovery}{hint} Nothing "
                f"was changed."
            )
        new = new.replace(old, rep, 1)
    try:
        with p.open("w", encoding="utf-8", newline="") as fh:
            fh.write(new)
    except Exception as e:
        return err(f"{type(e).__name__}: {e}")
    return ok(f"Edited {p}: {len(edits)} replacement(s).")


# --- grep -----------------------------------------------------------------------------


@tool(
    "grep",
    "Search file contents for a regex (Python re, case-insensitive). Each match returns "
    "file path, line number, and 2 lines of context either side. Searches up to 3000 "
    "files under the workspace, skipping .git, .venv, node_modules, other dot-folders, "
    "and binaries; no approval needed; "
    "returns up to 100 matches — past the cap, narrow the pattern or scope with `path` "
    "(a directory searches under it, a file searches just it).",
    {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "Directory or file to limit the "
                                                       "search to; default: whole workspace."},
        },
        "required": ["pattern"],
    },
)
async def grep(args: dict[str, Any]) -> dict[str, Any]:
    pattern = args.get("pattern")
    if not pattern:
        return err("grep needs a 'pattern' argument.")
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return err(f"Bad regex /{pattern}/: {e}")
    root = _resolve(args.get("path") or ".")
    if not root.exists():
        return err(f"No such path: {root}")
    return ok(await in_thread(_grep, rx, root, ctx().workspace))


def _iter_files(root: Path):
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in _GREP_SKIP_DIRS and not d.startswith(".")
        )
        for fn in sorted(filenames):
            yield Path(dirpath) / fn


def _grep(rx: re.Pattern[str], root: Path, workspace: Path) -> str:
    hits: list[str] = []
    seen_files = 0
    hit_capped = file_capped = False
    for f in _iter_files(root):
        seen_files += 1
        if seen_files > _GREP_MAX_FILES:
            file_capped = True
            break
        try:
            data = f.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8000]:
            continue  # binary
        lines = data.decode("utf-8", "replace").splitlines()
        try:
            shown = f.relative_to(workspace)
        except ValueError:
            shown = f
        for i, line in enumerate(lines):
            if not rx.search(line):
                continue
            lo, hi = max(0, i - _GREP_CONTEXT), min(len(lines), i + _GREP_CONTEXT + 1)
            block = "\n".join(
                f"{'>' if j == i else ' '} {j + 1}: {lines[j][:_GREP_LINE_CHARS]}"
                for j in range(lo, hi)
            )
            hits.append(f"{shown}:{i + 1}\n{block}")
            if len(hits) >= _GREP_MAX_HITS:
                hit_capped = True
                break
        if hit_capped:
            break
    notes: list[str] = []
    if hit_capped:
        notes.append(f"[capped at {_GREP_MAX_HITS} matches — narrow the pattern or "
                     f"scope it with `path`]")
    if file_capped:
        notes.append(f"[stopped after {_GREP_MAX_FILES} files — scope the search with "
                     f"`path`]")
    if not hits:
        return "\n".join([f"No matches for /{rx.pattern}/ under {root}", *notes])
    return "\n\n".join([*hits, *notes])


# --- copy_files -------------------------------------------------------------------------


@tool(
    "copy_files",
    "Copy or move files and folders. Folders copy recursively (an existing destination "
    "folder is merged into); move=true deletes the source after copying. Every source "
    "is checked before anything is copied, so a bad entry means nothing happens. "
    "Destinations inside the workspace are allowed in accept-edits and auto modes; a "
    "path outside the workspace asks.",
    {
        "type": "object",
        "properties": {
            "files": {
                "type": "array",
                "description": "List of copy operations.",
                "items": {
                    "type": "object",
                    "properties": {
                        "src": {"type": "string", "description": "Source file or folder."},
                        "dest": {"type": "string", "description": "Destination path."},
                        "move": {"type": "boolean",
                                 "description": "Delete the source after copying. "
                                                "Default: false."},
                    },
                    "required": ["src", "dest"],
                },
            },
        },
        "required": ["files"],
    },
)
async def copy_files(args: dict[str, Any]) -> dict[str, Any]:
    files = args.get("files")
    if not isinstance(files, list) or not files:
        return err("copy_files needs a non-empty 'files' list of {src, dest, move?}.")
    plan: list[tuple[Path, Path, bool]] = []
    for i, f in enumerate(files):
        if not isinstance(f, dict) or not f.get("src") or not f.get("dest"):
            return err(f"files[{i}] needs src and dest. Nothing was copied.")
        src, dest = _resolve(str(f["src"])), _resolve(str(f["dest"]))
        if not src.exists():
            return err(f"files[{i}]: no such source: {src}. Nothing was copied.")
        if src.is_dir() and (dest == src or dest.is_relative_to(src)):
            return err(f"files[{i}]: cannot copy a folder into itself ({src}). "
                       f"Nothing was copied.")
        plan.append((src, dest, bool(f.get("move"))))
    return await in_thread(_copy, plan)


def _copy(plan: list[tuple[Path, Path, bool]]) -> dict[str, Any]:
    done: list[str] = []
    for src, dest, move in plan:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if move:
                shutil.move(str(src), str(dest))
            elif src.is_dir():
                shutil.copytree(src, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dest)
        except Exception as e:
            before = ("\nCompleted before the failure:\n" + "\n".join(done)) if done else ""
            return err(f"Failed at {src} → {dest}: {type(e).__name__}: {e}{before}")
        done.append(f"{'moved' if move else 'copied'} {src} → {dest}")
    return ok("\n".join(done))


# --- delete_file ------------------------------------------------------------------------


@tool(
    "delete_file",
    "Delete one or more files or folders inside the workspace. Folders are deleted "
    "recursively; a symlink is unlinked, never followed. This asks the user first in "
    "every mode, and the result lists exactly what was removed. Every path is checked "
    "before anything is deleted, so a missing or outside path means nothing happens.",
    {
        "type": "object",
        "properties": {
            "paths": {"type": "array", "description": "Paths to delete.",
                      "items": {"type": "string"}},
        },
        "required": ["paths"],
    },
)
async def delete_file(args: dict[str, Any]) -> dict[str, Any]:
    paths = args.get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
        return err("delete_file needs a non-empty 'paths' list of strings.")
    targets = [_resolve_nofollow(p) for p in paths]
    missing = [str(t) for t in targets if not t.exists() and not t.is_symlink()]
    if missing:
        return err("No such path: " + ", ".join(missing) + ". Nothing was deleted.")
    root = ctx().workspace.resolve()
    for t in targets:
        # A link is judged by where the LINK sits; anything else by what it is.
        where = t if t.is_symlink() else t.resolve()
        if where == root:
            return err(f"Refusing to delete the workspace itself ({root}). Nothing was deleted.")
        if not where.is_relative_to(root):
            # Defense in depth. The policy asks for outside paths, but a recursive
            # delete is the one tool where "the tool acts, the policy asks" is not
            # enough — this tool never deletes outside the workspace at all.
            return err(f"Refusing to delete outside the workspace: {where}. Nothing was "
                       f"deleted. If you really mean it, use run_bash, which will ask.")
    return await in_thread(_delete, targets)


def _delete(targets: list[Path]) -> dict[str, Any]:
    removed: list[str] = []
    for t in targets:
        try:
            if t.is_symlink():
                # The link goes; whatever it points at is untouched.
                t.unlink()
                removed.append(f"{t} (symlink; target left in place)")
            elif t.is_dir():
                shutil.rmtree(t)
                removed.append(f"{t}/ (folder, recursive)")
            else:
                t.unlink()
                removed.append(str(t))
        except Exception as e:
            before = ("\nRemoved before the failure:\n" + "\n".join(removed)) if removed else ""
            return err(f"Failed to delete {t}: {type(e).__name__}: {e}{before}")
    return ok("Deleted:\n" + "\n".join(removed))


# --- image_metadata ---------------------------------------------------------------------


_SVG_ROOT = re.compile(r"<svg\b[^>]*>", re.IGNORECASE | re.DOTALL)
_SVG_ATTR = re.compile(r"""\b(width|height|viewBox)\s*=\s*["']([^"']*)["']""", re.IGNORECASE)


@tool(
    "image_metadata",
    "Read an image's size, format, transparency, and frame count without seeing it. "
    "Reports dimensions (width×height), format, whether the format supports "
    "transparency, whether any pixel is actually transparent (decodes and scans the "
    "alpha channel), and whether it is animated (with frame count for GIF/APNG/WebP). "
    "Supports PNG, GIF, JPEG, WebP, BMP, SVG. This is not visual evidence. Use `see` "
    "to inspect pixels if listed, or load it once when tool_schema lists it as deferred. "
    "Otherwise visual inspection is unavailable.",
    {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
)
async def image_metadata(args: dict[str, Any]) -> dict[str, Any]:
    raw = args.get("path")
    if not raw:
        return err("image_metadata needs a 'path' argument.")
    p = _resolve(raw)
    if not p.is_file():
        return err(f"No file at {p}")
    try:
        return ok(await in_thread(_image_metadata, p))
    except Exception as e:
        return err(f"Could not read {p} as an image: {type(e).__name__}: {e}")


def _svg_metadata(text: str) -> str:
    m = _SVG_ROOT.search(text)
    attrs = {k.lower(): v.strip() for k, v in _SVG_ATTR.findall(m.group(0))} if m else {}
    w, h, source = attrs.get("width"), attrs.get("height"), "width/height attributes"
    if (not w or not h) and attrs.get("viewbox"):
        parts = attrs["viewbox"].replace(",", " ").split()
        if len(parts) == 4:
            w, h, source = parts[2], parts[3], "viewBox"
    size = f"{w}×{h} (from {source})" if w and h else "unspecified (no width/height or viewBox)"
    return "\n".join([
        "format: SVG",
        f"size: {size}",
        "transparency supported: yes (vector; the canvas is transparent by default)",
        "has transparent pixels: n/a (vector)",
        "animated: unknown (SVG animation is not detected)",
    ])


def _image_metadata(p: Path) -> str:
    head = p.read_bytes()[:512]
    if p.suffix.lower() == ".svg" or b"<svg" in head.lower():
        text = p.read_text(encoding="utf-8", errors="replace")
        if not _SVG_ROOT.search(text):
            raise ValueError("no <svg> root element")
        return _svg_metadata(text)
    from PIL import Image

    with Image.open(p) as im:
        fmt, (w, h), mode = im.format or "unknown", im.size, im.mode
        n_frames = int(getattr(im, "n_frames", 1) or 1)
        animated = bool(getattr(im, "is_animated", False)) or n_frames > 1
        has_alpha_channel = mode in ("RGBA", "LA", "PA") or "transparency" in im.info
        supports = has_alpha_channel or fmt in ("PNG", "GIF", "WEBP")
        transparent = False
        if has_alpha_channel:
            lo, _hi = im.convert("RGBA").getchannel("A").getextrema()
            transparent = lo < 255
    yn = lambda b: "yes" if b else "no"  # noqa: E731
    return "\n".join([
        f"format: {fmt}",
        f"size: {w}×{h}",
        f"mode: {mode}",
        f"transparency supported: {yn(supports)}",
        f"has transparent pixels: {yn(transparent)}"
        + (" (first frame scanned)" if animated else ""),
        f"animated: {'yes (' + str(n_frames) + ' frames)' if animated else 'no'}",
    ])


# --- sleep ------------------------------------------------------------------------------


@tool(
    "sleep",
    "Wait for a specified duration in seconds (max 60). Useful for letting animations, "
    "transitions, or async rendering settle before a screenshot or a DOM read. DO NOT "
    "sleep proactively or defensively — most tools already wait as needed; sleep only "
    "when something will not work without it. 1–5 seconds is enough for most cases.",
    {
        "type": "object",
        "properties": {"seconds": {"type": "number", "description": "How long to wait (max 60)."}},
        "required": ["seconds"],
    },
)
async def sleep(args: dict[str, Any]) -> dict[str, Any]:
    raw = args.get("seconds")
    if isinstance(raw, bool):
        return err("sleep needs a numeric 'seconds' argument.")
    try:
        s = float(raw)
    except (TypeError, ValueError):
        return err("sleep needs a numeric 'seconds' argument.")
    if not s > 0:
        return err("seconds must be greater than 0.")
    if s > _SLEEP_MAX:
        return err(f"seconds must be at most {_SLEEP_MAX:g}.")
    await asyncio.sleep(s)
    return ok(f"Slept {s:g}s.")


FILE_TOOLS = [str_replace_edit, grep, copy_files, delete_file, image_metadata, sleep]
# The one that is the loop's hand: its schema is never deferred, because a deferred
# tool's description is invisible until looked up, and "old_string must match
# exactly once" is the instruction that stops a small model rewriting whole files.
# The other five may defer — the lookup catalogue shows each one's first sentence,
# which is why every first sentence above is the one-line summary. Pinning grep too
# put the pinned set plus the catalogue over the 10% budget at a 16K window.
HANDS = [str_replace_edit]
