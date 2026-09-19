"""Studio tools: the model sees what it made.

Two frames. The hidden frame (``dream.gui.preview``) is a headless Chromium page
the model loads, probes, and screenshots without disturbing the user; it works with no
Studio open at all. The user's frame is the Studio panel; ``show_to_user`` and
``done`` push an artifact there through the session's event funnel.

Names and contracts follow the Design prompt these were specified from, so its
guidance transfers: ``show_html`` then ``get_webview_logs`` to check a page loads
cleanly; ``done`` as the end-of-turn handoff that must come back clean.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from .. import config
from ..core.backends.base import Event
from ..core import script_execution
from ..core.execution import ExecutionRefused, ExecutionUnavailable, current_execution
from ..gui.bundle import prepare_studio_media
from ..gui.preview import MAX_CAPTURE_STEPS, get_preview
from ..workflows.validation import verify_page
from .context import ctx, err, ok, studio

MULTI_MAX_STEPS = 12
_CAPTURE_PATH_PATTERN = r"(^|/)[^/]+\.([pP][nN][gG]|[jJ][pP][eE]?[gG])(?![\s\S])"
_RENDERABLE = {".html", ".htm", ".svg"}


def _resolve(raw: str) -> Path:
    return (ctx().workspace / Path(raw).expanduser()).resolve()


def _page(args: dict[str, Any]) -> tuple[Path | None, dict[str, Any] | None]:
    raw = args.get("path")
    if not raw:
        return None, err("This tool needs a 'path' argument — the HTML (or SVG) file to show.")
    p = _resolve(str(raw))
    if not p.is_file():
        return None, err(f"No file at {p}")
    if p.suffix.lower() not in _RENDERABLE:
        return None, err(f"{p.name} is not an HTML or SVG file; the preview renders those.")
    return p, None


def _summarize(logs: list[str]) -> str:
    errors = [l for l in logs if l.startswith(("error:", "console.error:"))]
    blocked = [l for l in logs if l.startswith("blocked:")]
    if not logs:
        return "clean — no console output, no errors."
    parts = [f"{len(logs)} console line(s), {len(errors)} error(s)"]
    if blocked:
        parts.append(f"{len(blocked)} network request(s) blocked")
    head = "; ".join(parts) + "."
    return head + "\n" + "\n".join(logs[:40]) + ("\n… (get_webview_logs for the rest)" if len(logs) > 40 else "")


async def _load(p: Path) -> list[str]:
    try:
        return await get_preview().load(p)
    except Exception as e:
        raise RuntimeError(f"the hidden frame could not load {p.name}: {type(e).__name__}: {e}")


def _show_in_studio(p: Path, given: str) -> bool:
    """Queue an explicit show, keyed like write_file. True means queued, not visible."""
    emit = ctx().emit
    panel = studio()
    if emit is None or panel is None or getattr(panel, "ready", True) is False:
        return False
    try:
        body = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    body = prepare_studio_media(p, body)
    event = Event("studio", {"op": "show", "path": given, "title": p.name, "content": body})
    retain = getattr(panel, "retain_show", None)
    if callable(retain):
        retain(event)
    emit(event)
    return True


def _delivery_notice(p: Path, queued: bool) -> str:
    if not queued:
        return f"Opened {p.name} in the hidden frame (Studio is not open or delivery is unavailable)."
    clients = getattr(studio(), "client_count", 0)
    prepared = f"Prepared {p.name} for Studio and the hidden frame. "
    if clients > 0:
        return prepared + (f"Preview queued for {clients} connected Studio browser(s); "
                           "rendering is not confirmed.")
    return prepared + "Preview queued; no Studio browser is connected yet."


# --- see the artifact ------------------------------------------------------------------


@tool(
    "show_html",
    "Open an HTML file in YOUR hidden preview frame (not the user's pane). Use this "
    "before get_webview_logs to check the page loads cleanly. The user's Studio panel "
    "is not affected — call show_to_user when you want to surface a file in their view.",
    {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
)
async def show_html(args: dict[str, Any]) -> dict[str, Any]:
    p, bad = _page(args)
    if bad:
        return bad
    t0 = time.monotonic()
    try:
        logs = await _load(p)
    except RuntimeError as e:
        return err(str(e))
    note = get_preview().renderer_note()
    return ok(f"Loaded {p} in the hidden frame ({(time.monotonic() - t0) * 1000:.0f} ms): "
              + _summarize(logs) + (f"\n{note}" if note else "")
              + "\nThis preview is not shown in the user's Studio. When ready to share it, "
                "call show_to_user with this HTML path; use done for final delivery. "
                "If the needed signature is deferred, load it once with tool_schema.")


@tool(
    "get_webview_logs",
    "Get console logs and errors from the hidden preview frame since its last load. "
    "Call after show_html to check the page rendered cleanly.",
    {"type": "object", "properties": {}, "required": []},
)
async def get_webview_logs(args: dict[str, Any]) -> dict[str, Any]:
    pv = get_preview()
    if pv.loaded is None:
        return err("Nothing is loaded in the hidden frame yet — call show_html first.")
    logs = pv.logs
    if not logs:
        return ok(f"{pv.loaded.name}: no console output, no errors.")
    return ok(f"{pv.loaded.name}: {len(logs)} line(s)\n" + "\n".join(logs[:200]))


@tool(
    "show_to_user",
    "Open a file in the USER's Studio panel so they can see and interact with it, and "
    "load it in your hidden frame too. Use this to direct their attention to something "
    "mid-task. For end-of-turn delivery use `done` instead — it does the same AND "
    "returns console errors.",
    {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
)
async def show_to_user(args: dict[str, Any]) -> dict[str, Any]:
    p, bad = _page(args)
    if bad:
        return bad
    try:
        await _load(p)
    except RuntimeError as e:
        return err(str(e))
    try:
        queued = _show_in_studio(p, str(args["path"]))
    except ValueError as e:
        return err(str(e))
    return ok(_delivery_notice(p, queued))


@tool(
    "done",
    "Finish your turn: check `path` in the hidden frame, queue it for the user's Studio "
    "panel, and report browser connection status and hidden-frame console errors. "
    "If errors come back, fix them and call done again. A clean `done` is the "
    "end-of-turn handoff; the verifier runs after it, never before.",
    {"type": "object", "properties": {"path": {"type": "string",
                                               "description": "HTML file to surface to the user."}},
     "required": ["path"]},
)
async def done(args: dict[str, Any]) -> dict[str, Any]:
    raw = args.get("path")
    other = _resolve(str(raw)) if raw else None
    if other is not None and other.is_file() and other.suffix.lower() not in _RENDERABLE:
        # A handoff doc, a script, data: the deliverable is the file on disk. It is
        # not previewed, and a non-HTML deliverable is not a failed turn (live, a
        # HANDOFF.md turned a finished turn into "delivery_failed", Dream fix #20).
        return ok(f"Delivered {other} ({other.stat().st_size:,} bytes). Only HTML/SVG render in "
                  "the Studio panel, so this file is not previewed; it is on disk for the user.")
    p, bad = _page(args)
    if bad:
        return bad
    try:
        checks = await asyncio.to_thread(verify_page, ctx().workspace, str(args['path']))
    except (ValueError, OSError) as e:
        return err(f'Deliverable check failed: {e}. Fix the artifact and call done again.')
    try:
        logs = await _load(p)
    except RuntimeError as e:
        return err(str(e))
    try:
        queued = _show_in_studio(p, str(args["path"]))
    except ValueError as e:
        return err(str(e))
    errors = [l for l in logs if l.startswith(("error:", "console.error:"))]
    delivery = _delivery_notice(p, queued)
    if errors:
        return err(f"{delivery} Hidden frame is NOT clean — fix these and call done "
                   f"again:\n" + "\n".join(errors[:40]) + "\n\nFull console:\n"
                   + "\n".join(logs[:80]))
    return ok(delivery + " Hidden frame: " + _summarize(logs) + "\n" + checks)


# --- screenshots and probes -----------------------------------------------------------


def _steps(raw: Any, *, code_required: bool, limit: int) -> tuple[list[dict[str, Any]] | None, str | None]:
    if not isinstance(raw, list) or not raw:
        return None, "'steps' must be a non-empty list of {code?, delay?}."
    if len(raw) > limit:
        return None, f"at most {limit} steps per call."
    out: list[dict[str, Any]] = []
    for i, s in enumerate(raw):
        if not isinstance(s, dict):
            return None, f"steps[{i}] must be an object."
        if code_required and not s.get("code"):
            return None, f"steps[{i}] needs 'code' (JavaScript to run before capturing)."
        if "code" in s and not isinstance(s["code"], str):
            return None, f"steps[{i}].code must be a string of JavaScript."
        delay = s.get("delay", 200)
        if (isinstance(delay, bool) or not isinstance(delay, (int, float))
                or isinstance(delay, float) and not math.isfinite(delay)):
            return None, f"steps[{i}].delay must be a number of milliseconds (finite)."
        out.append({"code": s.get("code"), "delay": delay})
    return out, None


async def _ensure_loaded(p: Path) -> str | None:
    pv = get_preview()
    if pv.loaded != p.resolve():
        try:
            await _load(p)
        except RuntimeError as e:
            return str(e)
    return None


def _visual_next_step() -> str:
    try:
        multimodal = getattr(ctx(), "multimodal", None)
    except RuntimeError:
        multimodal = None
    prefix = "Saved paths are not visual evidence. "
    if multimodal is True:
        return (prefix + "Call `see` on a saved path to supply image pixels for inspection. "
                "If its signature is deferred, load it once with tool_schema(name=\"see\") "
                "when that lookup is available and lists see. If unavailable, report visual checks as unverified.")
    if multimodal is False:
        return prefix + "This session's image input is disabled; report visual checks as unverified."
    return (prefix + "Use `see` if listed; if tool_schema lists it as deferred, load name=\"see\" once. "
            "Otherwise report visual checks as unverified.")


@tool(
    "save_screenshot",
    "Take one or more screenshots of the hidden preview frame and save them — to disk "
    "(save_path, relative to the workspace) or in memory (in_memory_png_key, for a later "
    "run_script). Each step optionally runs a JS snippet, waits `delay` ms (default 200), "
    "then captures; for a single plain screenshot use one step with no code. Several "
    "captures get numeric prefixes (01-name.png, 02-name.png). hq=true captures PNG "
    "instead of low-quality JPEG — avoid unless you need lossless. Does NOT return the "
    "image. Use `see` on a returned path if listed, or load its deferred schema once "
    "with tool_schema(name=\"see\") when that lookup lists it; "
    "otherwise visual inspection is unavailable. A path does not supply image pixels.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The HTML file to capture (loaded if not already)."},
            "steps": {"type": "array", "minItems": 1, "maxItems": MAX_CAPTURE_STEPS,
                "items": {"type": "object", "properties": {
                "code": {"type": "string"}, "delay": {"type": "number"}}}},
            "save_path": {"type": "string", "minLength": 1,
                "pattern": _CAPTURE_PATH_PATTERN,
                "description": "Destination file (.png, .jpg or .jpeg), relative to the workspace; choose this OR in_memory_png_key."},
            "in_memory_png_key": {"type": "string", "minLength": 1,
                "description": "Memory capture key; choose this OR save_path."},
            "hq": {"type": "boolean"},
        },
        "required": ["path", "steps"],
        "oneOf": [{"required": ["save_path"]}, {"required": ["in_memory_png_key"]}],
    },
)
async def save_screenshot(args: dict[str, Any]) -> dict[str, Any]:
    p, bad = _page(args)
    if bad:
        return bad
    steps, why = _steps(args.get("steps"), code_required=False, limit=MAX_CAPTURE_STEPS)
    if why:
        return err(why)
    save_path, key = args.get("save_path"), args.get("in_memory_png_key")
    for name in ("save_path", "in_memory_png_key"):
        if name in args and (not isinstance(args[name], str) or not args[name]):
            return err(f"{name} must be a non-empty string.")
    if ("save_path" in args) == ("in_memory_png_key" in args):
        return err("Give exactly one of save_path or in_memory_png_key.")
    target = None
    if save_path:
        if re.search(_CAPTURE_PATH_PATTERN, save_path) is None:
            return err("save_path must end in .png or .jpg (.jpeg is also supported).")
        try:
            target = _resolve(save_path)
        except (ValueError, OSError, RuntimeError):
            return err("save_path could not be resolved; use a valid destination path.")
        if target.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            return err("save_path must end in .png or .jpg (.jpeg is also supported).")
    if (fail := await _ensure_loaded(p)):
        return err(fail)
    pv = get_preview()
    try:
        if key:
            await pv.screenshot(steps, hq=True, key=str(key))
            return ok(f"Stashed {len(steps)} PNG capture(s) under key '{key}' for run_script.")
        paths = await pv.screenshot(steps, hq=bool(args.get("hq")), save_to=target)
    except Exception as e:
        return err(f"Screenshot failed: {type(e).__name__}: {e}")
    return ok("Saved:\n" + "\n".join(str(x) for x in paths) + "\n" + _visual_next_step())


@tool(
    "multi_screenshot",
    "Take up to 12 screenshots of the hidden preview frame, running a JS snippet before "
    "each capture — different slides, UI states, scroll positions. Every step needs "
    "`code`. Images land in Dream's screenshot folder. Paths do not supply pixels; "
    "inspect with `see` if listed, or load it once when tool_schema lists it as deferred.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The HTML file currently shown (loaded if not)."},
            "steps": {"type": "array", "minItems": 1, "maxItems": MULTI_MAX_STEPS,
                "items": {"type": "object", "properties": {
                "code": {"type": "string", "minLength": 1}, "delay": {"type": "number"}},
                "required": ["code"]}},
        },
        "required": ["path", "steps"],
    },
)
async def multi_screenshot(args: dict[str, Any]) -> dict[str, Any]:
    p, bad = _page(args)
    if bad:
        return bad
    steps, why = _steps(args.get("steps"), code_required=True, limit=MULTI_MAX_STEPS)
    if why:
        return err(why)
    if (fail := await _ensure_loaded(p)):
        return err(fail)
    config.SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    target = config.SCREENSHOT_DIR / f"preview-{p.stem}-{int(time.time())}.jpg"
    try:
        paths = await get_preview().screenshot(steps, save_to=target)
    except Exception as e:
        return err(f"Screenshot failed: {type(e).__name__}: {e}")
    return ok("Saved:\n" + "\n".join(str(x) for x in paths) + "\n" + _visual_next_step())


@tool(
    "eval_js",
    "Execute JavaScript in the hidden preview frame and return the last expression's "
    "value (JSON). Use it for DOM and style queries, to drive a page into a state, or "
    "to check a computed value. The page has no network access.",
    {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
)
async def eval_js(args: dict[str, Any]) -> dict[str, Any]:
    code = args.get("code")
    if not code:
        return err("eval_js needs a 'code' argument.")
    pv = get_preview()
    if pv.loaded is None:
        return err("Nothing is loaded in the hidden frame yet — call show_html first.")
    try:
        value = await pv.eval(str(code))
    except Exception as e:
        return err(f"{type(e).__name__}: {e}")
    try:
        text = json.dumps(value, ensure_ascii=False, default=repr)
    except Exception:
        text = repr(value)
    if len(text) > 50_000:
        text = text[:50_000] + "\n[...truncated]"
    return ok(text)


# --- the user's view ---------------------------------------------------------------------

_SCRIPT_OPEN = re.compile(r"<script\b", re.IGNORECASE)


def _inert_scripts(html: str) -> str:
    return _SCRIPT_OPEN.sub('<script type="text/plain" data-inert="snapshot"', html)



@tool(
    "eval_js_user_view",
    "Execute JavaScript in the USER's Studio panel — the artifact they have open — not "
    "your hidden frame. Only when you need state the hidden frame cannot reproduce: what "
    "they typed, toggled, or scrolled to, or when the user says \"look at what I'm seeing\". "
    "For all normal DOM and style queries use eval_js. Returns the last expression's "
    "value as JSON. Fails plainly when Studio is not open or nothing is open in it.",
    {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
)
async def eval_js_user_view(args: dict[str, Any]) -> dict[str, Any]:
    code = args.get("code")
    if not code:
        return err("eval_js_user_view needs a 'code' argument.")
    srv = studio()
    if srv is None:
        return err("Studio is not open; use eval_js on the hidden frame instead.")
    try:
        value = await srv.ask_frame("eval", {"code": str(code)})
    except (TimeoutError, RuntimeError) as e:
        return err(str(e))
    except Exception as e:
        return err(f"{type(e).__name__}: {e}")
    try:
        text = json.dumps(value, ensure_ascii=False, default=repr)
    except Exception:
        text = repr(value)
    return ok(text[:50_000] + ("\n[...truncated]" if len(text) > 50_000 else ""))


@tool(
    "screenshot_user_view",
    "Screenshot the USER's Studio panel — the artifact they have open, as they see it "
    "now. Takes a snapshot of its live DOM (typed values and toggled states included; "
    "state held only in JavaScript variables is not), renders it in your hidden frame, "
    "and returns an image path, not pixels. Inspect with `see` if listed, or load it "
    "once when tool_schema lists it as deferred. Only when the hidden frame cannot reproduce "
    "the state; for normal verification use save_screenshot. The hidden frame then "
    "holds the snapshot — show_html to go back to your own page.",
    {"type": "object", "properties": {}, "required": []},
)
async def screenshot_user_view(args: dict[str, Any]) -> dict[str, Any]:
    srv = studio()
    if srv is None:
        return err("Studio is not open; use save_screenshot on the hidden frame instead.")
    try:
        html = await srv.ask_frame("snapshot", {})
    except (TimeoutError, RuntimeError) as e:
        return err(str(e))
    except Exception as e:
        return err(f"{type(e).__name__}: {e}")
    if not isinstance(html, str) or not html.strip():
        return err("The panel returned no document.")
    stamp = int(time.time() * 1000)
    config.SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snap = config.SCREENSHOT_DIR / f"user-view-{stamp}.html"
    # The snapshot IS the state; the page's scripts must not run again on top of
    # it and rebuild their initial state. Inert them; styles stay live.
    snap.write_text(_inert_scripts(html), encoding="utf-8")
    try:
        pv = get_preview()
        await pv.load(snap)
        (out,) = await pv.screenshot([{}], save_to=config.SCREENSHOT_DIR / f"user-view-{stamp}.jpg")
    except Exception as e:
        return err(f"Could not render the snapshot: {type(e).__name__}: {e}")
    return ok(f"Saved {out} — the user's view as of now (live DOM, form values included). "
              f"{_visual_next_step()} The hidden frame now holds this snapshot.")


# --- present_fs_item_for_download ---------------------------------------------------------


@tool(
    "present_fs_item_for_download",
    "Present a file, a folder, or the whole workspace as a download to the user: a "
    "download card appears in Studio (a folder is zipped). Omit path for the entire "
    "workspace. Use it to hand over a finished deliverable — a PDF, a bundle, an "
    "exported deck — not for files the user only needs to see.",
    {"type": "object", "properties": {
        "path": {"type": "string", "description": "File or folder relative to the workspace; omit for all of it."},
        "label": {"type": "string", "description": "Display label for the card (default: the item's name)."}},
     "required": []},
)
async def present_fs_item_for_download(args: dict[str, Any]) -> dict[str, Any]:
    raw = str(args.get("path") or "").strip()
    ws = ctx().workspace.resolve()
    target = ws if not raw else _resolve(raw)
    if not target.is_relative_to(ws):
        return err("path must be inside the workspace.")
    if not target.exists():
        return err(f"No such file or folder: {target}")
    rel = str(target.relative_to(ws)) if target != ws else ""
    label = str(args.get("label") or (target.name if rel else "Project")).strip()
    kind = "folder (zipped)" if target.is_dir() else f"file ({target.stat().st_size:,} bytes)"
    emit = ctx().emit
    if emit is None or studio() is None:
        return ok(f"Studio is not open, so there is no download card; the {kind} is at {target}. "
                  f"Tell the user the path.")
    emit(Event("studio", {"op": "download", "path": rel, "label": label, "kind": kind}))
    return ok(f"Download card '{label}' ({kind}) is showing in Studio.")


# --- run_script -------------------------------------------------------------------------


@tool(
    "run_script",
    "Execute an async JavaScript script to programmatically manipulate project files and "
    "images — batch operations that would be tedious as single tool calls: read several "
    "files and combine or transform them, find-and-replace across files, load an image "
    "and draw on it with Canvas, compose an image from layers, generate a file from data. "
    "Helpers: log(...args), await readFile(path), await readFileBinary(path) → Blob, "
    "await readImage(path) → HTMLImageElement, await saveFile(path, data) with data a "
    "string, a Canvas (saved as PNG), or a Blob; await ls(path?), await getCaptures(key) "
    "→ Blob[] from save_screenshot's in_memory_png_key, createCanvas(w, h). Paths are "
    "relative to the workspace and must stay inside it. Requires verified OS containment "
    "and an installed JavaScript runtime; no network; 30 s timeout. Code errors can be "
    "fixed and retried; unavailable execution prerequisites cannot be fixed by rewriting "
    "the script. Not for bulk-copying binary files — "
    "use copy_files.",
    {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
)
async def run_script(args: dict[str, Any]) -> dict[str, Any]:
    code = args.get("code")
    if not code:
        return err("run_script needs a 'code' argument.")
    try:
        logs = await script_execution.run_script(code, current_execution(ctx().workspace),
                                                  get_preview().captures)
    except asyncio.TimeoutError as e:
        logs = getattr(e, "logs", [])
        return err(f"run_script timed out (limit {script_execution.SCRIPT_TIMEOUT:g} s or scope expiry); "
                   "owned processes terminated and reaped."
                   + ("\nLogged before the timeout:\n" + "\n".join(logs) if logs else ""))
    except ExecutionUnavailable as e:
        return err(f"Execution refused: {type(e).__name__}: {str(e)[:2000]}\n"
                   "Execution prerequisite unavailable. Rewriting the script will not resolve "
                   "this failure; report the prerequisite issue and await an environment change.")
    except ExecutionRefused as e:
        return err(f"Execution refused: {type(e).__name__}: {str(e)[:2000]}")
    except Exception as e:
        return err(f"Script error: {type(e).__name__}: {str(e)[:2000]}")
    return ok("Done." + ("\n" + "\n".join(logs) if logs else " (no log output)"))


STUDIO_TOOLS = [show_html, get_webview_logs, show_to_user, done, save_screenshot,
                multi_screenshot, eval_js, eval_js_user_view, screenshot_user_view, run_script,
                present_fs_item_for_download]
