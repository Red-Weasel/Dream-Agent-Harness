"""Project tools: the plan, the title, the template, and the design-system assets.

- `update_todos` is the local path's plan tool: a full-replace checklist the plan
  panel renders (the Claude path's TodoWrite feeds the same panel).
- `set_project_title` names the session, for `/export library` and Studio.
- `save_as_template` files the workspace into the Library's templates folder.
- `register_assets` / `unregister_assets` keep the design-system manifest: which
  files are versions of which named asset, with a review status and a group.
  Each registered file is also filed in the Library under /design-system/<Group>/,
  so a version survives the workspace; the manifest itself is a Library file
  (/design-system/manifest.json) that Studio's Design System tab reads.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from .context import ctx, err, in_thread, ok
from .library_tools import library

STATUSES = ("needs-review", "approved", "changes-requested")
GROUPS = ("Type", "Colors", "Spacing", "Components", "Brand")
MANIFEST_FOLDER = "/design-system"
MANIFEST_NAME = "manifest.json"
TEMPLATE_FOLDER = "/templates"
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache"}
_TEMPLATE_MAX_BYTES = 50 * 1024 * 1024

# Session-scoped, set by set_project_title; read by /export library and Studio.
_STATE: dict[str, str] = {"title": ""}


def project_title() -> str:
    return _STATE["title"]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "untitled"


# --- update_todos ---------------------------------------------------------------------


@tool(
    "update_todos",
    "Track your task list. Use this whenever you have more than one discrete task, or a "
    "long-running or multi-step task. Call it early to lay out your plan, then again as "
    "you complete, add, or remove tasks. Each call sends the COMPLETE current list — it "
    "fully replaces the previous state. Shown in the plan panel; call your next tool "
    "right after it, it never blocks.",
    {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string", "description": "Task description"},
                                   "completed": {"type": "boolean"}},
                    "required": ["name", "completed"],
                },
            },
        },
        "required": ["todos"],
    },
)
async def update_todos(args: dict[str, Any]) -> dict[str, Any]:
    todos = args.get("todos")
    if not isinstance(todos, list):
        return err("update_todos needs a 'todos' list of {name, completed}.")
    names: list[tuple[str, bool]] = []
    for i, t in enumerate(todos):
        if not isinstance(t, dict) or not str(t.get("name") or "").strip():
            return err(f"todos[{i}] needs a non-empty 'name'.")
        names.append((str(t["name"]).strip(), bool(t.get("completed"))))
    if not names:
        return ok("Plan cleared.")
    done = sum(1 for _, c in names if c)
    lines = [f"{'✓' if c else '·'} {n}" for n, c in names]
    return ok(f"Plan: {len(names)} task(s), {done} done.\n" + "\n".join(lines))


# --- update_plan -------------------------------------------------------------------------
# The project's phased plan (owner design, DREAM-083): a visible PLAN.md in the workspace
# and a panel beside the chat, with ● done / ◐ in progress / ○ not started. A phase that
# turns done carries a summary, and the backend compacts the conversation at that
# boundary so the next phase starts lean -- PLAN.md is what carries over.

PHASE_DONE = "Phase complete:"
_STATUSES = ("pending", "in_progress", "done")
_MARK = {"done": "●", "in_progress": "◐", "pending": "○"}
_LAST_PLAN: dict[str, dict[str, str]] = {}   # workspace -> phase name -> status last written
# DREAM-123: facts learned the hard way (an environment limit, a tool quirk, a convention) kept in PLAN.md's
# "## Lessons" section, which a compaction, a Fresh start and /resume carry to the model verbatim.
LESSONS_HEADING = "## Lessons"
LESSONS_MAX = 12        # lessons kept; the newest (last) win
LESSON_CHARS = 200      # one lesson, clipped


def plan_lessons(path: Path) -> list[str]:
    """The lessons in PLAN.md's "## Lessons" section (its "- " lines, up to the next heading), clipped and capped;
    [] when the file or the section is missing."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lessons: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("#"):
            inside = line.strip() == LESSONS_HEADING
        elif inside and line.startswith("- ") and line[2:].strip():
            lessons.append(_clip_lesson(line[2:]))
    return lessons[-LESSONS_MAX:]


def _clip_lesson(text: str) -> str:
    one = " ".join(text.split())
    return one if len(one) <= LESSON_CHARS else one[: LESSON_CHARS - 1].rstrip() + "…"


def _plan_markdown(title: str, phases: list[dict[str, Any]], lessons: list[str] = ()) -> str:
    from datetime import datetime
    lines = [f"# Plan{': ' + title if title else ''}", "",
             f"Status: ● done · ◐ in progress · ○ not started — updated {datetime.now():%Y-%m-%d %H:%M} by Dream", ""]
    if lessons:
        lines += [LESSONS_HEADING, *(f"- {lesson}" for lesson in lessons), ""]
    for i, phase in enumerate(phases, 1):
        lines.append(f"## {_MARK[phase['status']]} {i}. {phase['name']}")
        lines.extend(f"- {_MARK[s['status']]} {s['name']}" for s in phase["steps"])
        if phase.get("summary"):
            lines.append(f"\n> **Summary:** {phase['summary']}")
        lines.append("")
    return "\n".join(lines)


@tool(
    "update_plan",
    "Keep the project's phased plan in PLAN.md (workspace) and the plan panel. Call it first for any "
    "multi-step build, then whenever a step changes; each call sends the COMPLETE plan. Status: "
    "pending | in_progress | done. A phase marked done needs a `summary` (what exists, what the next "
    "phase needs): Dream compacts the conversation at each phase boundary, so PLAN.md carries over. "
    "`steps` is optional: a phase described by its summary may omit it or send []. `lessons`: facts learned the "
    "hard way that the next step must not re-learn (environment limits, tool quirks, conventions), one line "
    f"each, at most {LESSONS_MAX} (the newest win); PLAN.md keeps them and every compaction hands them back. Omit "
    "`lessons` to keep the current ones; send the complete list to change them, [] to clear them.",
    {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "phases": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "status": {"type": "string", "enum": list(_STATUSES)},
                "summary": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "object", "properties": {
                    "name": {"type": "string"}, "status": {"type": "string", "enum": list(_STATUSES)}},
                    "required": ["name", "status"]}}},
                "required": ["name", "status"]}},
            "lessons": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["phases"],
    },
)
async def update_plan(args: dict[str, Any]) -> dict[str, Any]:
    raw = args.get("phases")
    if not isinstance(raw, list) or not raw:
        return err("update_plan needs a non-empty 'phases' list of {name, status, summary?, steps?} (steps optional).")
    phases: list[dict[str, Any]] = []
    for i, p in enumerate(raw):
        if not isinstance(p, dict) or not str(p.get("name") or "").strip() or p.get("status") not in _STATUSES:
            return err(f"phases[{i}] needs a name and a status ({', '.join(_STATUSES)}).")
        steps = []
        for j, s in enumerate(p.get("steps") or []):
            if not isinstance(s, dict) or not str(s.get("name") or "").strip() or s.get("status") not in _STATUSES:
                return err(f"phases[{i}].steps[{j}] needs a name and a status ({', '.join(_STATUSES)}).")
            steps.append({"name": str(s["name"]).strip(), "status": s["status"]})
        phases.append({"name": str(p["name"]).strip(), "status": p["status"], "steps": steps,
                       "summary": str(p.get("summary") or "").strip()})
    key = str(ctx().workspace)
    prev = _LAST_PLAN.get(key, {})
    newly_done = [p["name"] for p in phases if p["status"] == "done" and prev and prev.get(p["name"]) != "done"]
    missing = [n for n in newly_done if not next(p for p in phases if p["name"] == n)["summary"]]
    if missing:
        return err(f"Give phase {missing[0]!r} a summary (what exists now, what the next phase needs) before "
                   "marking it done: the conversation is compacted at the phase boundary, so the summary "
                   "is what carries over. Call update_plan again with it.")
    title = str(args.get("title") or "").strip()
    path = ctx().workspace / "PLAN.md"
    raw_lessons = args.get("lessons")
    if raw_lessons is None:                  # kept: a plan update never drops them silently
        given, lessons = [], await in_thread(plan_lessons, path)
    elif isinstance(raw_lessons, list):
        given = [str(x) for x in raw_lessons if str(x).strip()]
        lessons = [_clip_lesson(x) for x in given][-LESSONS_MAX:]
    else:
        return err("update_plan's 'lessons' is a list of short strings (omit it to keep the current ones).")
    try:
        await in_thread(path.write_text, _plan_markdown(title, phases, lessons), "utf-8")
    except OSError as e:
        return err(f"Could not write {path}: {type(e).__name__}: {e}")
    _LAST_PLAN[key] = {p["name"]: p["status"] for p in phases}
    emit = ctx().emit
    if emit is not None:
        import time
        from ..core.backends.base import Event
        # updated_at: the strip shows the plan's age and turns stale after 30 minutes (#89); the retained
        # event carries it through a reload.
        emit(Event("plan", {"title": title, "phases": phases, "path": str(path), "updated_at": time.time()}))
    steps = [s for p in phases for s in p["steps"]]
    note = (f"PLAN.md updated: {len(phases)} phase(s), {sum(s['status'] == 'done' for s in steps)}"
            f"/{len(steps)} steps done.")
    if lessons:
        dropped = len(given) - len(lessons)
        clipped = sum(len(" ".join(x.split())) > LESSON_CHARS for x in given[-LESSONS_MAX:])
        note += (f" {len(lessons)} lesson(s) kept"
                 + (f"; {dropped} older dropped (at most {LESSONS_MAX})" if dropped > 0 else "")
                 + (f"; {clipped} clipped to {LESSON_CHARS} characters" if clipped else "") + ".")
    elif raw_lessons is not None:
        note += " Lessons cleared (PLAN.md has none now)."
    if newly_done:
        note = (f"{PHASE_DONE} {', '.join(newly_done)}. Dream compacts the conversation before the next "
                "request so the next phase starts lean; PLAN.md, the phase summary and your notes carry "
                "over.\n" + note)
    return ok(note)


# --- project_note --------------------------------------------------------------------------


@tool(
    "project_note",
    "Add to this project's memory notebook (kept in Dream's memory folder, loaded at every session "
    "in this project): what exists, conventions, decisions, what is left. `replace: true` rewrites "
    "the whole notebook (to consolidate it); otherwise the text is appended as a dated line.",
    {"type": "object", "properties": {"text": {"type": "string"}, "replace": {"type": "boolean"}},
     "required": ["text"]},
)
async def project_note(args: dict[str, Any]) -> dict[str, Any]:
    from ..memory import project as project_memory
    text = str(args.get("text") or "").strip()
    if not text:
        return err("project_note needs 'text'.")
    workspace = ctx().workspace
    if not project_memory.is_project(workspace):
        return err("This session has no project folder (it runs in your home or Dream's own folder); "
                   "use remember() for global memory.")
    try:
        path = await in_thread(project_memory.add_note, workspace, text, replace=args.get("replace") is True)
    except OSError as e:
        return err(f"Could not write project memory: {type(e).__name__}: {e}")
    return ok(f"Project memory updated: {path}")


# --- set_project_title -------------------------------------------------------------------


@tool(
    "set_project_title",
    "Name the current session/project. Use it once you have identified a brand or "
    "product name, so the session is findable in the Library instead of sitting under "
    "a generic placeholder. Used as the title of `/export library` and shown in Studio.",
    {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
)
async def set_project_title(args: dict[str, Any]) -> dict[str, Any]:
    title = str(args.get("title") or "").strip()
    if not title:
        return err("set_project_title needs a non-empty 'title'.")
    if len(title) > 120:
        return err("Keep the title under 120 characters.")
    _STATE["title"] = title
    return ok(f"Project title: {title}")


# --- save_as_template ----------------------------------------------------------------------


def _zip_workspace(root: Path, meta: dict[str, Any]) -> bytes:
    buf = io.BytesIO()
    total = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("template.json", json.dumps(meta, indent=2))
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if any(part in _SKIP_DIRS for part in rel.parts) or p.is_symlink() or not p.is_file():
                continue
            total += p.stat().st_size
            if total > _TEMPLATE_MAX_BYTES:
                raise ValueError(f"the workspace exceeds {_TEMPLATE_MAX_BYTES // (1024 * 1024)} MB; "
                                 f"move large assets out before saving a template")
            z.write(p, str(rel))
    return buf.getvalue()


@tool(
    "save_as_template",
    "Save the current workspace as a reusable template in the Library's /templates "
    "folder: a zip of the project (without .git, node_modules, .venv) plus a "
    "template.json with the title, description, and the composer intro shown to whoever "
    "starts from it. Returns the Library id.",
    {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Display name for the template."},
            "description": {"type": "string", "description": "Short description shown in the template picker."},
            "intro_text": {"type": "string",
                           "description": "What to ask someone starting from this template to provide."},
        },
        "required": ["title"],
    },
)
async def save_as_template(args: dict[str, Any]) -> dict[str, Any]:
    title = str(args.get("title") or "").strip()
    if not title:
        return err("save_as_template needs a non-empty 'title'.")
    meta = {"title": title, "description": str(args.get("description") or ""),
            "intro_text": str(args.get("intro_text") or ""), "source": str(ctx().workspace)}
    name = f"{_slug(title)}.zip"
    try:
        data = await in_thread(_zip_workspace, ctx().workspace, meta)
        lib = library()
        prior = await in_thread(lib.by_name, name, folder=TEMPLATE_FOLDER)
        if prior:
            f = await in_thread(lib.replace, prior[0].id, data, note=f"template updated: {meta['description']}")
            verb = f"updated to v{f.version}"
        else:
            f = await in_thread(lib.create, data, name=name, folder=TEMPLATE_FOLDER,
                                mime="application/zip", note=f"template: {meta['description']}")
            verb = "saved"
    except Exception as e:
        return err(f"Could not save the template: {type(e).__name__}: {e}")
    return ok(f"Template {verb}: {f.path}  (id {f.id}, {f.size_bytes:,} bytes). "
              f"Tell the user the id; a new project can start from it with library_materialize.")


# --- register_assets / unregister_assets --------------------------------------------------


def _load_manifest(lib: Any) -> tuple[list[dict[str, Any]], str | None]:
    hits = lib.by_name(MANIFEST_NAME, folder=MANIFEST_FOLDER)
    if not hits:
        return [], None
    text, _ = lib.read(hits[0].id)
    try:
        items = json.loads(text)
    except ValueError:
        items = []
    return (items if isinstance(items, list) else []), hits[0].id


def _save_manifest(lib: Any, items: list[dict[str, Any]], file_id: str | None) -> str:
    data = json.dumps(items, indent=2).encode("utf-8")
    if file_id:
        return lib.replace(file_id, data, note=f"{len(items)} asset version(s)").id
    return lib.create(data, name=MANIFEST_NAME, folder=MANIFEST_FOLDER, mime="application/json",
                      note="design-system manifest").id


def _register(items: list[dict[str, Any]], workspace: Path) -> str:
    lib = library()
    manifest, mid = _load_manifest(lib)
    lines = []
    for it in items:
        rel, asset = it["path"], it["asset"]
        src = (workspace / rel).resolve()
        group = it.get("group") or "Brand"
        status = it.get("status") or "needs-review"
        folder = f"{MANIFEST_FOLDER}/{group}"
        prior = lib.by_name(asset, folder=folder)
        if prior:
            f = lib.replace(prior[0].id, src, note=f"{status} · {rel} · {it.get('subtitle', '')}")
        else:
            f = lib.create(src, name=asset, folder=folder, note=f"{status} · {rel} · {it.get('subtitle', '')}")
        entry = {"asset": asset, "path": rel, "group": group, "status": status,
                 "subtitle": it.get("subtitle", ""), "viewport": it.get("viewport"),
                 "library_file_id": f.id, "version": f.version, "updated": f.updated}
        manifest = [m for m in manifest if not (m.get("asset") == asset and m.get("path") == rel)]
        manifest.append(entry)
        lines.append(f"{group}/{asset} ← {rel}  [{status}]  (Library {f.id} v{f.version})")
    _save_manifest(lib, manifest, mid)
    return "Registered:\n" + "\n".join(lines) + f"\nManifest: {len(manifest)} version(s) across " \
           f"{len({m['asset'] for m in manifest})} asset(s)."


@tool(
    "register_assets",
    "Register one or more files in the design-system review manifest. Each file becomes "
    "a version of the named asset, with a review status (needs-review, approved, "
    "changes-requested; default needs-review) and a group for the Design System tab "
    "(Type, Colors, Spacing, Components, Brand — Title-cased). Re-registering an "
    "existing (asset, path) pair resets its review status. Each file is also filed in "
    "the Library under /design-system/<Group>/<asset>, so the version outlives the "
    "workspace. Omit for support files like CSS or research notes.",
    {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path relative to the workspace."},
                        "asset": {"type": "string", "description": "Asset name to register this file under."},
                        "group": {"type": "string", "enum": list(GROUPS)},
                        "status": {"type": "string", "enum": list(STATUSES)},
                        "subtitle": {"type": "string", "description": "Short description of this version."},
                        "viewport": {"type": "object", "properties": {"width": {"type": "number"},
                                                                       "height": {"type": "number"}}},
                    },
                    "required": ["path", "asset"],
                },
            },
        },
        "required": ["items"],
    },
)
async def register_assets(args: dict[str, Any]) -> dict[str, Any]:
    items = args.get("items")
    if not isinstance(items, list) or not items:
        return err("register_assets needs a non-empty 'items' list of {path, asset, group?, status?}.")
    clean: list[dict[str, Any]] = []
    for i, it in enumerate(items):
        if not isinstance(it, dict) or not it.get("path") or not it.get("asset"):
            return err(f"items[{i}] needs path and asset. Nothing was registered.")
        rel = str(it["path"]).strip()
        src = (ctx().workspace / rel).resolve()
        if not src.is_relative_to(ctx().workspace.resolve()):
            return err(f"items[{i}]: {rel} is outside the workspace. Nothing was registered.")
        if not src.is_file():
            return err(f"items[{i}]: no file at {rel}. Nothing was registered.")
        group = str(it.get("group") or "Brand").strip().title()
        if group not in GROUPS:
            return err(f"items[{i}]: group must be one of {', '.join(GROUPS)}. Nothing was registered.")
        status = str(it.get("status") or "needs-review").strip().lower()
        if status not in STATUSES:
            return err(f"items[{i}]: status must be one of {', '.join(STATUSES)}. Nothing was registered.")
        clean.append({"path": rel, "asset": str(it["asset"]).strip(), "group": group, "status": status,
                      "subtitle": str(it.get("subtitle") or ""), "viewport": it.get("viewport")})
    try:
        return ok(await in_thread(_register, clean, ctx().workspace))
    except Exception as e:
        return err(f"Could not register: {type(e).__name__}: {e}")


def _unregister(items: list[dict[str, Any]]) -> str:
    lib = library()
    manifest, mid = _load_manifest(lib)
    before = len(manifest)
    for it in items:
        a, p = it.get("asset"), it.get("path")
        manifest = [m for m in manifest
                    if not ((a is None or m.get("asset") == a) and (p is None or m.get("path") == p))]
    if len(manifest) != before:
        _save_manifest(lib, manifest, mid)
    return f"Removed {before - len(manifest)} manifest entr{'y' if before - len(manifest) == 1 else 'ies'}; " \
           f"{len(manifest)} remain. Library files are kept (versioned); nothing on disk changed."


@tool(
    "unregister_assets",
    "Remove entries from the design-system review manifest. asset-only removes all "
    "versions of that asset; path-only removes the version wherever it is registered; "
    "asset+path removes one specific version. Files in the Library and on disk are kept.",
    {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "asset": {"type": "string"}, "path": {"type": "string"}}}},
        },
        "required": ["items"],
    },
)
async def unregister_assets(args: dict[str, Any]) -> dict[str, Any]:
    items = args.get("items")
    if not isinstance(items, list) or not items:
        return err("unregister_assets needs a non-empty 'items' list; each needs asset and/or path.")
    for i, it in enumerate(items):
        if not isinstance(it, dict) or not (it.get("asset") or it.get("path")):
            return err(f"items[{i}] needs at least one of asset or path.")
    try:
        return ok(await in_thread(_unregister, items))
    except Exception as e:
        return err(f"Could not unregister: {type(e).__name__}: {e}")


def manifest() -> list[dict[str, Any]]:
    """The design-system manifest, for Studio. Empty when none has been filed."""
    try:
        items, _ = _load_manifest(library())
        return items
    except Exception:
        return []


def manifest_with_content() -> list[dict[str, Any]]:
    """The manifest plus each renderable version's current text, so the Design
    System tab can open an asset in the artifact panel without another round trip.
    Binary or unreadable versions carry no content and get no open button."""
    lib = library()
    out = []
    for item in manifest():
        row = dict(item)
        if str(row.get("path", "")).lower().endswith((".html", ".htm", ".svg")):
            try:
                row["content"], _ = lib.read(row["library_file_id"])
            except Exception:
                row["content"] = None
        else:
            row["content"] = None
        out.append(row)
    return out


PROJECT_TOOLS = [update_todos, update_plan, project_note, set_project_title, save_as_template, register_assets, unregister_assets]
