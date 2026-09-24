"""Reading the skill packages installed on disk.

The counterpart to ``skill_tools``: those four tools are Dream's hands on the skills
it wrote itself, these three are its hands on the ones somebody else authored and
installed. Kept as separate tools rather than overloaded onto ``skill_list`` /
``skill_load`` because the two stores answer different questions — "what have I
learned?" versus "what has been installed for me?" — and a model that conflates them
will patch a skill it does not own.

Discovery happens once per process. The set only changes when files change, and
re-walking a few thousand directory entries on every call would cost more than it
could ever save; ``skill_refresh`` exists for the session where a skill is edited
mid-flight.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from .. import config
from ..skills import loader
from .context import ctx, err, in_thread, ok

# Discovered once and reused. None = not yet loaded this process.
_CACHE: list[loader.FileSkill] | None = None
_WARNINGS: list[str] = []

# A search that matches nearly everything is not a search; cap what one call returns
# so a vague query cannot dump the whole index back into context.
_FIND_LIMIT = 25


def installed(refresh: bool = False) -> list[loader.FileSkill]:
    """The installed skills, discovering them on first use."""
    return [s for s in inventory(refresh) if loader.enabled(s)]


def inventory(refresh: bool = False) -> list[loader.FileSkill]:
    """All packages for settings, including disabled ones; metadata only."""
    global _CACHE, _WARNINGS
    if _CACHE is None or refresh:
        from .. import plugins
        roots = config.skill_dirs()
        # Settings must retain disabled plugin children so they can explain the
        # parent switch. installed() applies the plugin/skill enable settings.
        roots.extend(p.skill_dir for p in plugins.loaded()
                     if p.skill_dir and p.skill_dir not in roots)
        _CACHE, _WARNINGS = loader.discover(roots)
    return _CACHE


def warnings() -> list[str]:
    """Discovery warnings from the last walk (empty until ``installed()`` runs)."""
    installed()
    return list(_WARNINGS)


def index_lines() -> list[str]:
    """Only the small curated index is carried at wake-up; search sees all enabled roots."""
    try:
        skills = [skill for skill in installed() if skill.curated]
        lines, used = [], 0
        for line in loader.index_lines(skills):
            if used + len(line) + 1 > 1600:
                break
            lines.append(line)
            used += len(line) + 1
        return lines
    except (OSError, ValueError):
        return []


def _by_name(name: str) -> loader.FileSkill | None:
    want = (name or "").strip().lower()
    for s in installed():
        if s.name.lower() == want:
            return s
    return None


_FIND_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Words to match against skill names and descriptions. All "
            "terms must appear. Blank lists everything.",
        }
    },
    "required": [],
}


@tool(
    "skill_find",
    "Search enabled Dream skills by keyword, including explicitly opted-in external "
    "catalogs. Only curated names are in the wake context. Use skill_open for full "
    "instructions; skill_file reads supporting references. Learned memory skills use skill_list.",
    _FIND_SCHEMA,
)
async def skill_find(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "")
    hits = await in_thread(loader.find, installed(), query)
    if not hits:
        return ok(f"No installed skill matches '{query}'.")
    shown = hits[:_FIND_LIMIT]
    body = "\n".join(f"• {s.index_line}  [{s.source}]" for s in shown)
    more = "" if len(hits) <= _FIND_LIMIT else f"\n…and {len(hits) - _FIND_LIMIT} more — narrow the query."
    return ok(f"{len(hits)} installed skill(s) — skill_open a name for the full text:\n{body}{more}")


_OPEN_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Exact skill name, as shown in the index."}
    },
    "required": ["name"],
}


@tool(
    "skill_open",
    "Read an installed skill in full — its whole SKILL.md, plus a list of the "
    "reference and script files bundled with it. Do this before following one: the "
    "index line only says WHEN a skill applies, never how. Files it lists are "
    "fetched separately with skill_file, so nothing is spent on detail you don't need.",
    _OPEN_SCHEMA,
)
async def skill_open(args: dict[str, Any]) -> dict[str, Any]:
    name = str(args.get("name") or "")
    skill = _by_name(name)
    if skill is None:
        near = await in_thread(loader.find, installed(), name)
        hint = ("\nDid you mean: " + ", ".join(s.name for s in near[:5])) if near else ""
        return err(f"No installed skill named '{name}'. Use skill_find to search.{hint}")
    body = await in_thread(loader.load_body, skill)
    if isinstance(body, loader.SkillReadFailure):
        return err(body)
    return ok(f"Skill '{skill.name}' (installed, from {skill.source}):\n{directory_line(skill, _workspace())}\n\n{body}")


def _workspace():
    try:
        return ctx().workspace
    except RuntimeError:          # no live session (standalone caller)
        return None


def directory_line(skill: loader.FileSkill, workspace) -> str:
    """DREAM-101/105: say where the skill lives AND what run_bash can do there. The sandbox holds the workspace plus the
    installed skills' script folders READ-ONLY (dream.core.skill_runtime), so a plugin skill's `<SKILL_DIR>/script`
    runs with run_bash as written; any other folder outside the workspace is invisible to the shell."""
    from ..core import skill_runtime

    root = skill.root.resolve()
    inside = False
    if workspace is not None:
        try:
            root.relative_to(Path(workspace).resolve())
            inside = True
        except ValueError:
            inside = False
    if workspace is None or inside:
        return f"Directory: {root}"
    if any(root.is_relative_to(r) for r in skill_runtime.script_roots()):
        return (f"Directory: {root} -- readable in run_bash's sandbox: run its scripts with run_bash as written, using "
                f"this path for <SKILL_DIR> (read-only; writes stay in the workspace).")
    return (f"Directory: {root} -- outside the workspace, so run_bash cannot see it (its sandbox holds the workspace and the installed skills' script folders, not this one). "
            f"Read bundled files with skill_file(name=\"{skill.name}\", path=...). Do not try to cd or ls there.")


_FILE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "The skill the file belongs to."},
        "path": {
            "type": "string",
            "description": "Path relative to the skill's own directory, exactly as "
            "listed by skill_open (e.g. 'references/materialization.md').",
        },
    },
    "required": ["name", "path"],
}


@tool(
    "skill_file",
    "Read one file bundled with an installed skill — a reference doc it links to, or "
    "a script it ships. Paths are relative to the skill's own directory and cannot "
    "reach outside it. Use this when a skill you opened points at supporting material "
    "you actually need, rather than reading the whole bundle up front.",
    _FILE_SCHEMA,
)
async def skill_file(args: dict[str, Any]) -> dict[str, Any]:
    name, path = str(args.get("name") or ""), str(args.get("path") or "")
    skill = _by_name(name)
    if skill is None:
        return err(f"No installed skill named '{name}'. Use skill_find to search.")
    text = await in_thread(loader.bundled_file, skill, path)
    # Distinguish read failures from valid bracketed content such as JSON arrays.
    if isinstance(text, loader.SkillReadFailure):
        return err(text)
    return ok(f"{skill.name}/{path}:\n\n{text}")


_REFRESH_SCHEMA = {"type": "object", "properties": {}, "required": []}


@tool(
    "skill_refresh",
    "Re-scan the skill directories. Only needed when a skill was added or edited "
    "during this session — the set is otherwise read once and cached.",
    _REFRESH_SCHEMA,
)
async def skill_refresh(args: dict[str, Any]) -> dict[str, Any]:
    found = await in_thread(installed, True)
    note = f"\n{len(_WARNINGS)} warning(s): " + "; ".join(_WARNINGS[:5]) if _WARNINGS else ""
    return ok(f"Re-scanned: {len(found)} installed skills.{note}")


INSTALLED_SKILL_TOOLS = [skill_find, skill_open, skill_file, skill_refresh]
