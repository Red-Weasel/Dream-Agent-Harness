"""Task tools: work that outlives a turn and a session (Phase 11)."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from ..memory import project as projects
from ..memory.tasks import STATUSES
from .context import ctx, err, in_thread, ok
from .memory_tools import (
    ALL_PROJECTS_ARG, FROM_PROJECT_ARG, PROJECT_ARG, here, move_refusal, not_here, owned, read_scope,
    resolve_project,
)


def _fmt(t: dict[str, Any], current: str | None = None, cross: bool = False) -> str:
    line = f"#{t['id']} {t['title']} [{t['status']}]"
    if current is not None:
        line += projects.tag(t.get("project") or "", current, cross=cross)
    if t.get("notes"):
        line += "\n    " + t["notes"].strip().replace("\n", "\n    ")
    return line


def _after_write(c) -> str:
    """THREADS.md follows the store: regenerated after every write. A failure is
    said out loud — the row landed, the file did not, and only the tool result
    can tell anyone that."""
    try:
        c.tasks.write_threads()
        return ""
    except Exception as e:
        return f"\n(THREADS.md was not regenerated: {type(e).__name__}: {e})"


@tool(
    "task_add",
    "Add a piece of work that outlives this turn: a title, optional notes, status open "
    "(default), active, or blocked. Use it for anything you leave unfinished or plan to "
    "come back to — it appears in your next wake-up and in THREADS.md.",
    {"type": "object", "properties": {
        "title": {"type": "string"},
        "notes": {"type": "string", "description": "Where things stand, what is next."},
        "status": {"type": "string", "description": "open (default) | active | blocked"}},
     "required": ["title"]},
)
async def task_add(args: dict[str, Any]) -> dict[str, Any]:
    c = ctx()
    if c.tasks is None:
        return err("task_add: no task store in this session.")
    status = str(args.get("status") or "open")
    if status not in ("open", "active", "blocked"):
        return err("task_add: status must be open, active, or blocked "
                   "(task_update marks one done).")
    try:
        t = await in_thread(c.tasks.add, str(args.get("title") or ""), str(args.get("notes") or ""), status,
                            here())
    except ValueError as e:
        return err(f"task_add: {e}.")
    note = await in_thread(_after_write, c)
    return ok("Added " + _fmt(t, here()) + note)


@tool(
    "task_update",
    "Change a task: status (open | active | blocked | done), title, notes (replace), or "
    "append_note (add a line). Mark done when it is done, so it leaves the open list.",
    {"type": "object", "properties": {
        "id": {"type": "integer"}, "status": {"type": "string"}, "title": {"type": "string"},
        "notes": {"type": "string"}, "append_note": {"type": "string"},
        "project": {"type": "string", "description": "Name the task's own project to act on another "
                    "project's task; a different one (or 'user') moves it there."},
        "from_project": FROM_PROJECT_ARG},
     "required": ["id"]},
)
async def task_update(args: dict[str, Any]) -> dict[str, Any]:
    c = ctx()
    if c.tasks is None:
        return err("task_update: no task store in this session.")
    raw = args.get("id")
    if isinstance(raw, bool) or not isinstance(raw, (int, str)) or (
            isinstance(raw, str) and not (raw.strip().lstrip("-").isascii()
                                          and raw.strip().lstrip("-").isdigit())):
        return err("task_update: id must be a whole number.")
    task_id = int(raw)
    fields = {k: args.get(k) for k in ("title", "status", "notes", "append_note") if args.get(k) is not None}
    if not fields and not args.get("project"):
        return err("task_update: nothing to change — pass status, title, notes, append_note or project.")
    current_task = await in_thread(c.tasks.get, task_id)
    if current_task is None:
        return err(f"task_update: no task #{task_id}. task_list shows them.")
    owner, current = current_task.get("project") or projects.UNASSIGNED, here()
    try:
        target = resolve_project(args["project"]) if args.get("project") else None
        source = resolve_project(args["from_project"]) if args.get("from_project") else None
    except ValueError as e:
        return err(f"task_update: {e}.")
    if source is not None and source != owner:
        return err(f"task_update: task #{task_id} is in {owner}, not {source}; nothing changed.")
    moved_from = None
    if target is not None and target != owner:
        if target == projects.UNASSIGNED:
            return err("task_update: unassigned is not a destination; name a project.")
        refused = move_refusal(f"task #{task_id}", owner, args)   # another project's: name the source
        if refused:
            return err(f"task_update: {refused}")
        fields["project"], moved_from = target, owner
    elif not owned(owner, current) and owner not in (target, source):
        return err("task_update: " + not_here(f"task #{task_id}", owner,
                                              f"Pass project={owner!r} to change it there; moving it "
                                              f"elsewhere also needs from_project={owner!r}."))
    try:
        t = await in_thread(lambda: c.tasks.update(task_id, **fields))
    except ValueError as e:
        return err(f"task_update: {e}.")
    if t is None:
        return err(f"task_update: no task #{task_id}. task_list shows them.")
    note = await in_thread(_after_write, c)
    moved = f"Moved task #{task_id} from {moved_from} to {fields['project']}. " if moved_from else ""
    return ok(moved + "Updated " + _fmt(t, current) + note)


@tool(
    "task_list",
    "List this project's open work (active, blocked, open — in that order); include_done "
    "for the whole history; status to filter.",
    {"type": "object", "properties": {
        "status": {"type": "string"}, "include_done": {"type": "boolean"},
        "project": PROJECT_ARG, "all_projects": ALL_PROJECTS_ARG}, "required": []},
)
async def task_list(args: dict[str, Any]) -> dict[str, Any]:
    c = ctx()
    if c.tasks is None:
        return err("task_list: no task store in this session.")
    status = args.get("status")
    if status and status not in STATUSES:
        return err(f"task_list: status must be one of {', '.join(STATUSES)}.")
    try:
        scope, current, cross = read_scope(args)
    except ValueError as e:
        return err(f"task_list: {e}.")
    inc = args.get("include_done")
    inc = inc if isinstance(inc, bool) else str(inc or "").strip().lower() in ("true", "yes", "1")
    rows = await in_thread(lambda: c.tasks.list(status, inc, scope=scope))
    if not rows:
        return ok("No tasks" + (f" with status {status}" if status else " open") + ".")
    return ok(f"{len(rows)} task(s):\n" + "\n".join(_fmt(t, current, cross) for t in rows))


TASK_TOOLS = [task_add, task_update, task_list]
