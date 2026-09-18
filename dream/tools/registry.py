"""Assemble every tool into Dream's in-process MCP server, including any the agent has
written for itself under ``custom/``."""

from __future__ import annotations

import importlib.util
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server

from .. import config, extensions
from ..core import policy
from . import (
    checkpoint_tools,
    computer_tools,
    installed_skill_tools,
    library_tools,
    memory_file_tools,
    memory_tools,
    media_tools,
    plugin_tools,
    notes,
    export_tools,
    github_tools,
    project,
    questions,
    skill_tools,
    starters,
    studio,
    task_tools,
    vision,
    web,
    widgets,
)

# The tools that ship with Dream.
_BASE_TOOLS: list[SdkMcpTool] = [
    web.web_search,
    web.browse,
    *computer_tools.COMPUTER_TOOLS,
    vision.see,
    memory_tools.remember,
    memory_tools.recall,
    memory_tools.forget,
    memory_tools.recall_sessions,
    memory_tools.read_session,
    *memory_file_tools.MEMORY_FILE_TOOLS,
    *media_tools.MEDIA_TOOLS,
    notes.note,
    notes.read_notes,
    *skill_tools.SKILL_TOOLS,
    *installed_skill_tools.INSTALLED_SKILL_TOOLS,
    *library_tools.LIBRARY_TOOLS,
    checkpoint_tools.checkpoint_list,
    checkpoint_tools.checkpoint_restore,
    *studio.STUDIO_TOOLS,
    *starters.STARTER_TOOLS,
    *project.PROJECT_TOOLS,
    *questions.QUESTION_TOOLS,
    *export_tools.EXPORT_TOOLS,
    *github_tools.GITHUB_TOOLS,
    *widgets.WIDGET_TOOLS,
    *task_tools.TASK_TOOLS,
    *plugin_tools.PLUGIN_TOOLS,
]


# Which plugin a custom tool came from, by tool name (None for `custom/`). Set
# by the loader so a refusal below can say whose tool was refused.
_SOURCE: dict[str, str | None] = {}


def _load_custom_tools() -> tuple[list[tuple[SdkMcpTool, Path]], list[str]]:
    """Import every ``custom/*.py`` and collect its ``SdkMcpTool`` objects along with
    the file each came from (the file's mtime is the tool's freshness).

    A broken file is skipped with a warning rather than crashing the boot — the agent's
    home should never fail to start because of a half-written tool.
    """
    found: list[tuple[SdkMcpTool, Path]] = []
    warnings: list[str] = []
    from .. import plugins as _plugins

    # custom/ first, then each loaded plugin's tools/ — the same loader, the
    # same provenance: a plugin tool is a custom tool for policy.
    roots = [config.CUSTOM_TOOLS_DIR, *_plugins.tool_dirs()]
    files = [(d, f) for d in roots if d.exists() for f in sorted(d.glob("*.py"))]
    # Which plugin a file came from is known HERE; the engine used to guess it
    # from the warning's text, which missed a name collision entirely (Gate 12).
    plugin_of = {p.tool_dir: p.name for p in _plugins.loaded() if p.enabled and p.tool_dir}
    for d, f in files:
        if f.name.startswith("_"):
            continue
        owner = plugin_of.get(d)
        module_id = extensions.tool_module_id(f, owner)
        parent_id = extensions.extension_id("plugin", owner) if owner else None
        if not extensions.is_enabled(module_id, parent=parent_id):
            continue  # a disabled module is never imported/executed
        mod_name = (f"dream.tools.custom.{f.stem}" if d == config.CUSTOM_TOOLS_DIR
                    else f"dream.plugins.{d.parent.name}.tools.{f.stem}")
        try:
            source, snapshot = extensions.approved_module_source(f, owner)
            spec = importlib.util.spec_from_file_location(mod_name, f)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            # Execute the reviewed bytes, not a second file read or a cached
            # .pyc whose timestamp can agree while its code differs. This is
            # import authorization, not isolation of approved Python code.
            exec(compile(source, str(f), "exec"), mod.__dict__)
        except extensions.SettingsError as exc:
            sys.modules.pop(mod_name, None)
            label = f"plugin {owner}:" if owner else "custom tool:"
            warnings.append(f"{label} {exc}")
            continue
        except (Exception, SystemExit):
            sys.modules.pop(mod_name, None)
            # SystemExit too: a tool file with an unguarded `sys.exit(main())` at
            # module level would otherwise end the boot (Gate 12). KeyboardInterrupt
            # is deliberately NOT caught — Ctrl-C must still stop a boot.
            where = plugin_of.get(d)
            warnings.append((f"plugin {where}: tool '{f.name}' failed to load" if where
                             else f"custom tool '{f.name}' failed to load")
                            + f":\n{traceback.format_exc()}")
            continue
        found_before = len(found)
        for obj in vars(mod).values():
            # Only tools defined in THIS module (not imported into it), so a stray
            # `from ... import remember` can't shadow a built-in.
            if isinstance(obj, SdkMcpTool) and getattr(obj.handler, "__module__", "") == mod_name:
                found.append((obj, f))
                _SOURCE[obj.name] = plugin_of.get(d)
                obj._dream_extension_id = module_id
                obj._dream_plugin_id = parent_id
                obj._dream_source_path = snapshot["path"]
                obj._dream_module_sha256 = snapshot["sha256"]
        if len(found) == found_before and plugin_of.get(d):
            warnings.append(f"plugin {plugin_of[d]}: {f.name} defines no tool")
    return found, warnings


def staleness_note(
    stat: dict[str, Any], stale_days: int, now: datetime | None = None
) -> str:
    """The provenance tag a self-built tool wears in its own description. A tool that
    hasn't been used in a long while gets an explicit STALE flag: availability is not
    an argument — the question is whether today-you would build it better."""
    now = now or datetime.now(timezone.utc)

    def days_since(ts: str | None) -> int | None:
        if not ts:
            return None
        try:
            return max(int((now - datetime.fromisoformat(ts)).total_seconds() // 86400), 0)
        except Exception:
            return None

    built = (stat.get("first_seen") or "")[:10]
    idle = days_since(stat.get("last_used"))
    if idle is None:
        idle = days_since(stat.get("first_seen"))
    uses = int(stat.get("use_count") or 0)
    if idle is not None and idle >= stale_days:
        when = f"recorded history: unused in {idle}d" if uses else f"no use recorded in {idle}d (not evidence it was never used)"
        return (
            f"[self-built {built}; {when} — STALE: don't reach for this just because "
            "it exists; consider whether a fresh approach would be better]"
        )
    if not uses:
        return f"[self-built {built} · no use observed in Dream's recorded history]"
    last = "today" if idle == 0 else f"{idle}d ago" if idle is not None else "never"
    return f"[self-built {built} · used {uses}× · last {last}]"


def build(
    annotate: Callable[[str, Path], str | None] | None = None,
    extra_tools: list[SdkMcpTool] | None = None,
    wrap_tool: Callable[[SdkMcpTool], SdkMcpTool] | None = None,
) -> dict[str, Any]:
    """Build the MCP server. ``annotate(name, file)`` may return a provenance note to
    append to a custom tool's description — the staleness signal lives exactly where
    the model decides whether to reach for the tool. ``extra_tools`` are session-scoped
    tools (e.g. the MoE council tools) added on top of the base + custom set.
    ``wrap_tool`` binds engine/session middleware to each extension-guarded tool
    before it reaches either the SDK MCP server or a non-SDK backend. Returns
    dict with server config, tool ids, and warnings."""
    _SOURCE.clear()
    custom_pairs, warnings = _load_custom_tools()
    # De-dup: a custom tool may not claim a name already taken by a built-in or an
    # earlier custom tool. That includes the SDK builtins the policy auto-allows —
    # a self-built tool calling itself "Read" would otherwise wear Read's free pass.
    # First definition wins; collisions are reported.
    builtin = policy.builtin_names()
    claimed = {t.name for t in _BASE_TOOLS} | builtin
    custom: list[SdkMcpTool] = []
    for t, path in custom_pairs:
        if not extensions.tool_enabled(t):
            continue
        if t.name in claimed:
            why = "collides with a built-in" if t.name in builtin else "name already in use"
            where = _SOURCE.get(t.name)
            warnings.append((f"plugin {where}: tool '{t.name}' ignored" if where
                             else f"custom tool '{t.name}' ignored") + f" — {why}; rename it")
            continue
        # "__" is the MCP name separator; a custom tool that embeds it (e.g.
        # "evil__recall") could try to masquerade as a built-in's capability class.
        # Reject it outright — the permission classifier must never be spoofable.
        if "__" in t.name:
            where = _SOURCE.get(t.name)
            warnings.append((f"plugin {where}: tool '{t.name}' ignored" if where
                             else f"custom tool '{t.name}' ignored") + " — '__' is reserved")
            continue
        claimed.add(t.name)
        if annotate is not None:
            try:
                # Annotate from the pristine description so repeated builds in one
                # process never stack tags.
                base = getattr(t, "_dream_base_desc", None) or t.description
                note = annotate(t.name, path)
                t._dream_base_desc = base
                if note:
                    t.description = f"{base}\n{note}"
            except Exception:
                pass  # annotation is a nicety; never break a boot over it
        custom.append(t)
    # Provenance, not the name string, decides a custom tool's capability — declare
    # this boot's set before anything (the exempt list below included) classifies it.
    policy.declare_custom_tools(t.name for t in custom)
    tools = [extensions.guard_tool(t) for t in extensions.filter_tools(_BASE_TOOLS + custom + list(extra_tools or []))]
    if wrap_tool is not None:
        from functools import update_wrapper

        wrapped_tools = []
        for tool in tools:
            wrapped = wrap_tool(tool)
            if wrapped.handler is not tool.handler:
                update_wrapper(wrapped.handler, tool.handler)
            for attr in ("_dream_extension_id", "_dream_plugin_id", "_dream_mcp_server", "_dream_source_path", "_dream_module_sha256", "_dream_extension_guard"):
                if hasattr(tool, attr):
                    setattr(wrapped, attr, getattr(tool, attr))
            wrapped_tools.append(wrapped)
        tools = wrapped_tools
    server = create_sdk_mcp_server(config.MCP_SERVER_NAME, config.__dict__.get("VERSION", "0.1.0"), tools)
    # Tools the Claude backend may pre-approve (skip the permission callback):
    # only read-only + own-mind memory tools. Mutating tools and every self-built
    # custom tool are deliberately excluded so the mode policy still gates them.
    exempt_tool_ids = [
        config.tool_id(t.name)
        for t in tools
        if policy.capability(t.name) in policy.AUTO_CAPS
    ]
    return {
        "server": server,
        "tools": tools,  # the SdkMcpTool objects (for non-SDK backends)
        "tool_ids": [config.tool_id(t.name) for t in tools],
        "exempt_tool_ids": exempt_tool_ids,
        "names": [t.name for t in tools],
        "custom_names": [t.name for t in custom],
        "custom_count": len(custom),
        "warnings": warnings,
    }
