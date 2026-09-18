"""Task tools: work that outlives a turn and a session (Phase 11)."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from ..memory.tasks import STATUSES
from .context import ctx, err, in_thread, ok


def _fmt(t: dict[str, Any]) -> str:
    line = f"#{t['id']} {t['title']} [{t['status']}]"
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
        t = await in_thread(c.tasks.add, str(args.get("title") or ""), str(args.get("notes") or ""), status)
    except ValueError as e:
        return err(f"task_add: {e}.")
    note = await in_thread(_after_write, c)
    return ok("Added " + _fmt(t) + note)


@tool(
    "task_update",
    "Change a task: status (open | active | blocked | done), title, notes (replace), or "
    "append_note (add a line). Mark done when it is done, so it leaves the open list.",
    {"type": "object", "properties": {
        "id": {"type": "integer"}, "status": {"type": "string"}, "title": {"type": "string"},
        "notes": {"type": "string"}, "append_note": {"type": "string"}},
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
    if not fields:
        return err("task_update: nothing to change — pass status, title, notes, or append_note.")
    try:
        t = await in_thread(lambda: c.tasks.update(task_id, **fields))
    except ValueError as e:
        return err(f"task_update: {e}.")
    if t is None:
        return err(f"task_update: no task #{task_id}. task_list shows them.")
    note = await in_thread(_after_write, c)
    return ok("Updated " + _fmt(t) + note)


@tool(
    "task_list",
    "List open work (active, blocked, open — in that order); include_done for the whole "
    "history; status to filter.",
    {"type": "object", "properties": {
        "status": {"type": "string"}, "include_done": {"type": "boolean"}}, "required": []},
)
async def task_list(args: dict[str, Any]) -> dict[str, Any]:
    c = ctx()
    if c.tasks is None:
        return err("task_list: no task store in this session.")
    status = args.get("status")
    if status and status not in STATUSES:
        return err(f"task_list: status must be one of {', '.join(STATUSES)}.")
    inc = args.get("include_done")
    inc = inc if isinstance(inc, bool) else str(inc or "").strip().lower() in ("true", "yes", "1")
    rows = await in_thread(c.tasks.list, status, inc)
    if not rows:
        return ok("No tasks" + (f" with status {status}" if status else " open") + ".")
    return ok(f"{len(rows)} task(s):\n" + "\n".join(_fmt(t) for t in rows))


TASK_TOOLS = [task_add, task_update, task_list]
