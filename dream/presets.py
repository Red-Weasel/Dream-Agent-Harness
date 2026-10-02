"""Skill presets (DREAM-170): a named narrowing of the skills, tool groups, MCP servers and plugins a session starts with.

Default is no filter at all: its session takes the code path of a Dream without presets, so the prompt and the tools
are byte-identical. A preset is fixed when a session starts (``start_session``); changing the choice applies to the
next session, so the tools array, and with it a local engine's prompt cache, never changes mid-session. Presets only
narrow what the Extensions toggles already allow; they never enable anything, and never remove the core tools.

The built-ins ship in ``dream/presets.json``; the owner's choice and own presets live in
``data/config/presets.json`` (``{"active": name, "presets": {name: {skills, tools, mcp, plugins}}}``), where an
entry named like a built-in replaces it. A tool group is the module that defines the tool (``web``,
``library_tools``, a custom tool's file stem), the same rule the backend's pins use.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from . import config

DEFAULT = "Default"
BUILTIN_PATH = Path(__file__).with_name("presets.json")
KEYS = ("skills", "tools", "mcp", "plugins")
# No preset removes these: the hands, notes and memory, skill lookup, the plan, tasks and the question tool.
CORE_TOOLS = frozenset({"read_file", "write_file", "list_dir", "run_bash", "str_replace_edit", "grep", "note",
                        "read_notes", "recall", "remember", "memory_read", "skill_find", "skill_open", "skill_file",
                        "update_plan", "task_add", "task_update", "task_list", "ask_user_input"})
_SESSION: dict[str, frozenset[str]] | None = None   # this process's session preset; None = Default (no filter)
_SESSION_NAME = DEFAULT
_CATALOG: list[Any] = []   # the session's tools before its preset: what the Skills workspace's costs count from


class PresetsError(ValueError):
    """The owner's presets file must be repaired by hand; Dream never overwrites it."""


class Conflict(PresetsError):
    pass


def user_path() -> Path:
    return config.DATA_DIR / "config" / "presets.json"


def _read_user() -> tuple[dict[str, Any], str]:
    """The owner's file and the sha256 of its bytes ('' when there is none)."""
    try:
        raw = user_path().read_bytes()
    except FileNotFoundError:
        return {}, ""
    try:
        data = json.loads(raw)
        if (not isinstance(data, dict) or not isinstance(data.get("active", DEFAULT), str)
                or not isinstance(data.get("presets", {}), dict)):
            raise ValueError('expected {"active": name, "presets": {...}}')
    except ValueError as exc:
        raise PresetsError(f"{user_path()} is not a valid presets file ({exc}); repair or remove it") from exc
    return data, hashlib.sha256(raw).hexdigest()


def _builtins() -> dict[str, Any]:
    return json.loads(BUILTIN_PATH.read_text(encoding="utf-8"))["presets"]


def _collect() -> tuple[dict[str, Any], list[str]]:
    """The usable presets (built-ins, then the owner's) and a note for each owner entry skipped as malformed."""
    found, notes = _builtins(), []
    for name, entry in _read_user()[0].get("presets", {}).items():
        try:
            _normal(name, entry)
            found[name] = entry
        except PresetsError as exc:
            notes.append(f"{user_path()}: {exc}; it is skipped until repaired")
    return found, notes


def presets() -> dict[str, Any]:
    """Every usable preset except Default, by name."""
    return _collect()[0]


def _normal(name: str, preset: Any) -> dict[str, frozenset[str]]:
    if name == DEFAULT or not isinstance(preset, dict) or not all(
            isinstance(preset.get(k, []), list) and all(isinstance(v, str) for v in preset.get(k, [])) for k in KEYS):
        raise PresetsError(f"preset '{name}' needs lists of names for {', '.join(KEYS)}"
                           + (" and cannot be called Default" if name == DEFAULT else ""))
    return {k: frozenset(v.casefold() for v in preset.get(k, [])) for k in KEYS}


def active() -> str:
    return _read_user()[0].get("active", DEFAULT)


def start_session() -> tuple[str, str | None]:
    """Fix this session's preset: (its name, a warning for the boot banner or None). Anything unusable is Default,
    said out loud."""
    global _SESSION, _SESSION_NAME
    _SESSION, _SESSION_NAME = None, DEFAULT
    try:
        name = active()
        if name == DEFAULT:
            return DEFAULT, None
        found = presets()
        if name not in found:
            return DEFAULT, f"skill preset '{name}' does not exist or is malformed; this session uses Default"
        _SESSION, _SESSION_NAME = _normal(name, found[name]), name
        return name, None
    except (OSError, PresetsError) as exc:
        return DEFAULT, f"skill preset: {exc}; this session uses Default"


def session() -> dict[str, frozenset[str]] | None:
    return _SESSION


def set_catalog(tools: list[Any]) -> None:
    _CATALOG[:] = tools


def group(tool: Any) -> str:
    """A tool's group: the module that defines it."""
    return getattr(tool.handler, "__module__", "").rsplit(".", 1)[-1]


def keeps_tool(tool: Any, preset: dict[str, frozenset[str]] | None) -> bool:
    if preset is None or tool.name in CORE_TOOLS:
        return True
    if getattr(tool, "_dream_mcp_server", None) or "__" in tool.name:
        return True               # an MCP tool: its server started, so the preset has it (keeps_mcp)
    plugin = getattr(tool, "_dream_plugin_id", None)
    if plugin:
        return plugin.split(":", 1)[1] in preset["plugins"]
    # a group (the defining module) or, for one tool a skill needs, the tool's own name (DREAM-174)
    return group(tool).casefold() in preset["tools"] or tool.name.casefold() in preset["tools"]


def keeps_mcp(cfg: dict[str, Any], preset: dict[str, frozenset[str]] | None) -> bool:
    return (preset is None or cfg["name"].casefold() in preset["mcp"]
            or str(cfg.get("_dream_plugin") or "").casefold() in preset["plugins"])


def keeps_skill(skill: Any, preset: dict[str, frozenset[str]] | None) -> bool:
    return (preset is None or skill.name.casefold() in preset["skills"]
            or (skill.parent_extension or ":").split(":", 1)[1] in preset["plugins"])


def listing() -> dict[str, Any]:
    """The names (Default first), the next session's choice and the file's revision; a broken file is an error."""
    try:
        data, sha = _read_user()
        found, notes = _collect()
        return {"names": [DEFAULT, *found], "active": data.get("active", DEFAULT), "sha256": sha, "error": None,
                "notes": notes}
    except (OSError, PresetsError) as exc:
        return {"names": [DEFAULT], "active": DEFAULT, "sha256": None, "error": str(exc), "notes": []}


def cost(name: str, tools: list[Any], skills: list[Any], found: dict[str, Any] | None = None) -> dict[str, int]:
    """What a preset would give a session, out of this session's full catalog: counts and the tool schemas' tokens."""
    from .core.backends.openai_compat import _tool_schema
    from .core.tool_budget_schemas import measure

    preset = None if name == DEFAULT else _normal(name, (found or presets())[name])
    kept = [t for t in tools if keeps_tool(t, preset)]
    return {"tools": len(kept), "skills": sum(keeps_skill(s, preset) for s in skills),
            "tokens": measure([_tool_schema(t) for t in kept])}


def overview(skills: list[Any]) -> dict[str, Any]:
    """What the Skills workspace shows: every preset with its origin, items and cost, the tool groups, the choices."""
    listed = listing()
    found = {} if listed["error"] else presets()
    builtin, mine = _builtins(), ({} if listed["error"] else _read_user()[0].get("presets", {}))
    rows = [{"name": DEFAULT, "origin": "default", "items": None, "cost": cost(DEFAULT, _CATALOG, skills)}]
    for name, entry in found.items():
        rows.append({"name": name, "items": {k: list(entry.get(k, [])) for k in KEYS},
                     "origin": ("edited" if name in mine else "builtin") if name in builtin else "user",
                     "cost": cost(name, _CATALOG, skills, found)})
    groups: dict[str, list[str]] = {}
    for tool in _CATALOG:
        if not (getattr(tool, "_dream_plugin_id", None) or "__" in tool.name):
            groups.setdefault(group(tool), []).append(tool.name)
    return {**listed, "session": _SESSION_NAME, "presets": rows,
            "groups": [{"name": g, "tools": len(names), "core": len(set(names) & CORE_TOOLS), "names": names}
                       for g, names in sorted(groups.items())]}


def _write(change, expected_sha256: str | None) -> Any:
    """Apply ``change(data)`` to the owner's file under a lock; a stale revision is refused and nothing is written."""
    path = user_path().resolve()   # a symlinked file is written through to its target, never replaced
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(".presets.lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data, sha = _read_user()
        if expected_sha256 is not None and expected_sha256 != sha:
            raise Conflict("The presets changed since they were shown. Reload and choose again.")
        result = change(data)
        temporary = path.with_name(f".presets.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    return result


def set_active(name: str, expected_sha256: str | None = None) -> dict[str, Any]:
    """Choose the preset for the next session. A stale revision (the file changed since it was read) is refused.
    Without a revision (``/preset`` in the terminal; ``op: "use"`` without ``sha256``) it still re-reads the file under
    the lock and changes ``active`` only, so an edit made meanwhile elsewhere is kept, never overwritten."""
    def change(data):
        if name != DEFAULT and name not in presets():
            raise ValueError(f"No preset named '{name}'.")
        data["active"] = name
    _write(change, expected_sha256)
    return {"active": name, "applies": "next session"}


def _new_name(value: Any, taken) -> str:
    name = value.strip() if isinstance(value, str) else ""
    if not 0 < len(name) <= 60 or any(ord(c) < 32 for c in name):
        raise ValueError("A preset name is 1 to 60 characters.")
    if name.casefold() in {DEFAULT.casefold(), *(t.casefold() for t in taken)}:
        raise ValueError(f"A preset called '{name}' already exists.")
    return name


def edit(op: str, args: dict[str, Any], expected_sha256: str) -> dict[str, Any]:
    """The Skills workspace's preset edits (create, assign, rename, delete, reset). Assigning adds an item to one
    preset and never removes it from another; a built-in is edited as the owner's copy, and Reset drops that copy."""
    builtin = _builtins()

    def change(data):
        mine = data.setdefault("presets", {})
        known = {**builtin, **mine}
        if op == "create":
            name = _new_name(args.get("name"), known)
            mine[name] = {k: [] for k in KEYS}
            return name
        name = args.get("preset")
        if name == DEFAULT:
            raise ValueError("Default means everything; it has nothing to change.")
        if name not in known:
            raise ValueError(f"No preset named '{name}'.")
        if op == "assign":
            kind, item, on = args.get("kind"), args.get("item"), args.get("on")
            if kind not in KEYS or not isinstance(item, str) or not 0 < len(item) <= 240 or type(on) is not bool:
                raise ValueError("assign needs kind (skills, tools, mcp or plugins), item and on (true or false).")
            _normal(name, known[name])   # a malformed owner entry is a 400 with its reason, never a crash
            entry = {k: list(known[name].get(k, [])) for k in KEYS}
            kept = [v for v in entry[kind] if v.casefold() != item.casefold()]
            entry[kind] = kept + [item] if on else kept
            mine[name] = entry
        elif op == "rename":
            if name in builtin:
                raise ValueError("A built-in preset keeps its name.")
            new = _new_name(args.get("to"), set(known) - {name})
            mine[new] = mine.pop(name)
            if data.get("active") == name:
                data["active"] = new
            return new
        elif op in ("delete", "reset"):
            if op == "delete" and name in builtin:
                raise ValueError("A built-in preset cannot be deleted; Reset returns it to how it shipped.")
            if op == "reset" and not (name in builtin and name in mine):
                raise ValueError("Only an edited built-in preset can be reset.")
            del mine[name]
            if op == "delete" and data.get("active") == name:
                data["active"] = DEFAULT
        else:
            raise ValueError(f"Unknown preset operation '{op}'.")
        return name

    if not isinstance(expected_sha256, str):
        raise ValueError("The presets revision is required. Reload the Skills page.")
    return {"preset": _write(change, expected_sha256)}
