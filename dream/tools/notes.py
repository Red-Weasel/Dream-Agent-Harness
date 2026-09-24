"""Working-memory tools: quick scratch notes for the current task."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from ..memory import project as projects
from .context import ctx, err, in_thread, ok
from .memory_tools import ALL_PROJECTS_ARG, PROJECT_ARG, read_scope

_NOTE_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string", "description": "A short working note to jot down."}},
    "required": ["text"],
}


@tool(
    "note",
    "Jot a short-term working note for the current task — a running scratchpad. These "
    "are reviewed at session end and durable ones get promoted to long-term memory.",
    _NOTE_SCHEMA,
)
async def note(args: dict[str, Any]) -> dict[str, Any]:
    c = ctx()
    await in_thread(c.working.note, args["text"])
    return ok("noted.")


ELIDED_PREFIX = "[elided "


@tool(
    "read_notes",
    "Read back this session's working notes. Content that compaction removed from your "
    "context was saved as notes too: pass `query` (words, or a note id like '#12') to "
    "search all notes, elided content included.",
    {"type": "object", "properties": {
        "query": {"type": "string", "description": "Words to match, or '#<id>' for one note."},
        "limit": {"type": "integer", "description": "Max matches (default 10)."},
        "project": PROJECT_ARG, "all_projects": ALL_PROJECTS_ARG}, "required": []},
)
async def read_notes(args: dict[str, Any]) -> dict[str, Any]:
    c = ctx()
    query = str(args.get("query") or "").strip()
    if args.get("project") or args.get("all_projects"):
        # Other sessions' notes only on request, each with its session and project (DREAM-108).
        try:
            scope, current, _cross = read_scope(args)
            limit = max(1, min(50, int(args.get("limit") or 10)))
        except (TypeError, ValueError) as e:
            return err(f"read_notes: {e}.")
        hits = await in_thread(c.store.project_notes, query, scope, limit)
        if not hits:
            return ok(f"No note matches {query!r} there." if query else "No notes there.")
        lines = [f"{len(hits)} note(s)" + (f" matching {query!r}" if query else "") + ":"]
        for n in hits:
            owner = projects.tag(n.get("project") or "", current, cross=True)
            lines.append(f"\n#{n['id']} ({n['ts']}, session {n['session_id']}){owner}\n{n['note']}")
        return ok("\n".join(lines))
    if query:
        try:
            limit = max(1, min(50, int(args.get("limit") or 10)))
        except (TypeError, ValueError):
            return err("read_notes: limit must be a number.")
        if query.startswith("#") and query[1:].isdigit():
            hits = [n for n in await in_thread(c.working.notes, False) if n["id"] == int(query[1:])]
        else:
            hits = await in_thread(c.working.search, query, limit)
        if not hits:
            return ok(f"No note matches {query!r}.")
        lines = [f"{len(hits)} note(s) matching {query!r}:"]
        for n in hits:
            lines.append(f"\n#{n['id']} ({n['ts']})\n{n['note']}")
        return ok("\n".join(lines))
    notes = await in_thread(c.working.notes, False)
    if not notes:
        return ok("No working notes this session.")
    plain = [n for n in notes if not n["note"].startswith(ELIDED_PREFIX)]
    elided = len(notes) - len(plain)
    lines = ["Working notes this session:"] if plain else ["No working notes of your own this session."]
    for n in plain:
        lines.append(f"• {n['note']}")
    if elided:
        lines.append(f"\n({elided} elided item(s) from compaction are saved too — context "
                     f"recovery, not facts to promote; read_notes(query=...) searches them.)")
    return ok("\n".join(lines))
