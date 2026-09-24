"""Skill tools: the agent's hands on its own procedural memory.

Four tools over ``memory.skills`` — write a skill, patch one that turned out wrong,
list them cheaply, load one in full when it's actually needed.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from ..memory import longterm, skills
from ..memory.store import ScopeError
from .context import ctx, err, in_thread, ok


def _lines(value: Any) -> list[str]:
    """Steps and pitfalls are declared as arrays, but local models routinely emit a
    newline block instead — accept both rather than lose the skill."""
    if value is None:
        return []
    if isinstance(value, str):
        value = value.splitlines()
    return [str(v).strip() for v in value if str(v).strip()]


_STEPS = {
    "type": "array",
    "items": {"type": "string"},
    "description": "The procedure, one step per item, in order. Concrete: exact "
    "commands, paths, and flags — not a description of the approach.",
}
_PITFALLS = {
    "type": "array",
    "items": {"type": "string"},
    "description": "What bites: the dead ends, the silent failures, the thing that "
    "looked right and wasn't. Optional, but this is usually the most valuable part.",
}
_VERIFICATION = {
    "type": "string",
    "description": "How to check the procedure actually worked, and how to check the "
    "skill itself is still valid — a command to run, an output to look for.",
}

_SAVE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Short name for the procedure."},
        "when_to_use": {
            "type": "string",
            "description": "One line: the situation that should make future-you reach "
            "for this. This is the only part carried in context, so make it recognizable.",
        },
        "steps": _STEPS,
        "pitfalls": _PITFALLS,
        "verification": _VERIFICATION,
        "slug": {
            "type": "string",
            "description": "Optional stable id; pass an existing slug to rewrite that "
            "skill wholesale (prefer skill_patch to amend one).",
        },
    },
    "required": ["title", "when_to_use", "steps", "verification"],
}


@tool(
    "skill_save",
    "Write down how to do something, as a reusable procedure future-you inherits. "
    "Save one after a multi-step task that worked, after recovering from a dead end "
    "(the recovery is the skill), or after the user corrects your approach — that "
    "correction is the most valuable kind. Don't save one-off trivia, a fact (use "
    "remember), or anything obvious from reading the code. A skill with no "
    "verification step is a claim, not a skill.",
    _SAVE_SCHEMA,
)
async def skill_save(args: dict[str, Any]) -> dict[str, Any]:
    c = ctx()
    steps = _lines(args.get("steps"))
    if not steps:
        return err("A skill needs at least one step — what would future-you actually do?")
    try:
        mem = await in_thread(
            skills.save_skill,
            c.store,
            title=args["title"],
            when_to_use=args.get("when_to_use", ""),
            steps=steps,
            pitfalls=_lines(args.get("pitfalls")),
            verification=args.get("verification", ""),
            slug=args.get("slug") or None,
            source_session=c.session_id,
        )
    except ScopeError as e:
        # A slug another project owns is that project's skill (DREAM-108): never overwritten from here.
        where = "is unassigned" if e.owner == "unassigned" else f"belongs to project {e.owner}"
        return err(f"skill_save: skill {args.get('slug')!r} {where}, not this project. "
                   "Leave out slug to save this one here.")
    await in_thread(longterm.write_markdown, mem)
    return ok(f"Saved skill '{mem['title']}' (slug: {mem['slug']}).")


_PATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "slug": {"type": "string", "description": "Slug of the skill to amend."},
        "when_to_use": {"type": "string", "description": "Replacement when-to-use line."},
        "steps": _STEPS,
        "pitfalls": _PITFALLS,
        "verification": _VERIFICATION,
        "title": {"type": "string", "description": "Replacement title."},
    },
    "required": ["slug"],
}


@tool(
    "skill_patch",
    "Fix one section of an existing skill, leaving the OTHER sections intact. Use "
    "this the moment a skill turns out to be wrong or incomplete, rather than saving "
    "a second, competing skill. Each section you pass REPLACES that whole section, so "
    "to add one pitfall send the existing pitfalls plus the new one — skill_load "
    "first if you don't have them. Pass only the sections you're changing; passing "
    "just the slug re-stamps the skill as verified today.",
    _PATCH_SCHEMA,
)
async def skill_patch(args: dict[str, Any]) -> dict[str, Any]:
    c = ctx()
    fields: dict[str, Any] = {}
    for key in ("when_to_use", "verification", "title"):
        if args.get(key) is not None:
            fields[key] = args[key]
    for key in ("steps", "pitfalls"):
        if args.get(key) is None:
            continue
        lines = _lines(args[key])
        if not lines:
            # An empty array reads as "clear this section", but it arrives from the
            # same malformed-array failure mode _lines() exists to absorb — and
            # deleting a procedure's steps is not something to guess at. skill_save
            # already refuses empty steps; refuse here too rather than wipe.
            return err(f"'{key}' came through empty — pass the full replacement list, "
                       f"or omit '{key}' to leave that section alone.")
        fields[key] = lines
    try:
        mem = await in_thread(skills.patch_skill, c.store, args["slug"], **fields)
    except ValueError as e:
        return err(str(e))
    if mem is None:
        return err(f"No skill with slug '{args['slug']}'. Use skill_list to see them.")
    await in_thread(longterm.write_markdown, mem)
    amended = ", ".join(fields) if fields else "nothing — re-verified only"
    return ok(f"Patched skill '{mem['slug']}' ({amended}).")


_LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "limit": {"type": "integer", "description": f"Max skills (default {skills.INDEX_LIMIT})."}
    },
    "required": [],
}


@tool(
    "skill_list",
    "List your skills — one line each, slug and when to use it. Cheap; check it "
    "before working out an approach from scratch, then skill_load the one that fits.",
    _LIST_SCHEMA,
)
async def skill_list(args: dict[str, Any]) -> dict[str, Any]:
    c = ctx()
    lines = await in_thread(
        skills.index_lines, c.store, int(args.get("limit", skills.INDEX_LIMIT))
    )
    if not lines:
        return ok("No skills saved yet.")
    body = "\n".join(f"• {ln}" for ln in lines)
    return ok(f"Your skills ({len(lines)}) — skill_load a slug for the full procedure:\n{body}")


_LOAD_SCHEMA = {
    "type": "object",
    "properties": {"slug": {"type": "string", "description": "Slug of the skill to read."}},
    "required": ["slug"],
}


@tool(
    "skill_load",
    "Read a skill in full: its steps, pitfalls, and how to verify it. Do this before "
    "following one — the index line only tells you when it applies, not how.",
    _LOAD_SCHEMA,
)
async def skill_load(args: dict[str, Any]) -> dict[str, Any]:
    c = ctx()
    body = await in_thread(skills.load_skill, c.store, args["slug"])
    if body is None:
        # This tool reads the owner's LEARNED skills. An installed skill's name sent here got "no skill with slug"
        # and a pointer to skill_list, which lists the learned ones (2026-09-23, live session): name the right door.
        from . import installed_skill_tools
        hit = await in_thread(installed_skill_tools._by_name, args["slug"])
        where = (f" '{hit.name}' is an installed skill: skill_open(name=\"{hit.name}\")." if hit
                 else " Learned skills: skill_list. Installed skills: skill_find, then skill_open.")
        return err(f"No learned skill with slug '{args['slug']}'.{where}")
    return ok(f"Skill '{args['slug']}':\n\n{body}")


SKILL_TOOLS = [skill_save, skill_patch, skill_list, skill_load]
