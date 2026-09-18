"""Plugins: one directory that installs skills, tools, agents, and MCP servers.

Phase 12. Until now there was one extension point (``dream/tools/custom/``);
skills, agents, and MCP servers each had their own place. A plugin is a
directory under ``ROOT/plugins/`` with a ``plugin.yaml`` naming it and any of
``skills/``, ``tools/``, ``agents/``, ``mcp.json`` beside it. Each part is loaded
by the loader that kind already has — this module only finds them and hands
them over. A broken plugin is a warning, never a failed boot.

The manifest is a flat ``key: value`` file. PyYAML is on the machine but not a
declared dependency, and a name, a description, and an enabled flag need no
more than that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass
class Plugin:
    name: str
    path: Path
    description: str = ""
    version: str = ""
    default_enabled: bool = True
    capabilities: list[str] = field(default_factory=list)
    skills: int = 0          # SKILL.md packages found
    tools: int = 0           # .py files under tools/
    agents: list[dict[str, Any]] = field(default_factory=list)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        from .extensions import extension_id, is_enabled

        return is_enabled(extension_id("plugin", self.name), self.default_enabled)

    @property
    def parts(self) -> list[str]:
        out = []
        if self.skills:
            out.append(f"skills:{self.skills}")
        if self.tools:
            out.append(f"tools:{self.tools}")
        if self.agents:
            out.append(f"agents:{len(self.agents)}")
        if self.mcp_servers:
            out.append(f"mcp:{len(self.mcp_servers)}")
        return out

    @property
    def skill_dir(self) -> Path | None:
        d = self.path / "skills"
        return d if d.is_dir() else None

    @property
    def tool_dir(self) -> Path | None:
        d = self.path / "tools"
        return d if d.is_dir() else None


def parse_flat_yaml(text: str) -> dict[str, str]:
    """``key: value`` lines; comments and blanks skipped; quotes stripped. Enough
    for a manifest; anything nested is ignored rather than guessed at."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or ":" not in s or s.startswith("-"):
            continue
        key, _, val = s.partition(":")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key.strip().lower()] = val
    return out


def _declared_name(d: Path) -> str:
    """The `name:` a directory's manifest claims, lowercased, or "" — read here
    only to decide load order (see discover)."""
    for fname in ("plugin.yaml", "plugin.yml"):
        f = d / fname
        if f.is_file():
            try:
                return parse_flat_yaml(f.read_text(encoding="utf-8", errors="replace")).get(
                    "name", "").strip().lower()
            except OSError:
                return ""
    return ""


def _truthy(v: str) -> bool:
    """`enabled:` with nothing after it reads as "not disabled" — an empty value
    is a typo, and a typo must not silently switch a plugin off."""
    return v.strip().lower() not in ("false", "0", "no", "off")


def _agent_specs(d: Path, plugin: str) -> tuple[list[dict[str, Any]], list[str]]:
    """``agents/<name>.md``: frontmatter name/description/tools, body = prompt."""
    from .memory.longterm import parse_markdown

    specs, warnings = [], []
    for f in sorted(d.glob("*.md")):
        try:
            fm, body = parse_markdown(f.read_text(encoding="utf-8", errors="replace"))
        except OSError as e:
            warnings.append(f"plugin {plugin}: agent {f.name} unreadable: {e}")
            continue
        name = (fm.get("name") or f.stem).strip().lower()
        if not _NAME_RE.match(name):
            warnings.append(f"plugin {plugin}: agent {f.name} has a bad name {name!r}")
            continue
        if not body.strip():
            warnings.append(f"plugin {plugin}: agent {name} has no prompt (the body is empty)")
            continue
        from .core.subagents import builtin_names

        if name in builtin_names():
            warnings.append(f"plugin {plugin}: agent {name!r} is a built-in subagent's name; skipped")
            continue
        tools = tuple(t.strip() for t in (fm.get("tools") or "").split(",") if t.strip())
        specs.append({"name": name, "description": (fm.get("description") or "").strip()
                      or f"{name} (from plugin {plugin})",
                      "prompt": body.strip(), "tools": tools, "plugin": plugin})
    return specs, warnings


def discover(root: Path | None = None) -> tuple[list[Plugin], list[str]]:
    """Every plugin under ``root`` (default ``config.PLUGINS_DIR``), loaded or
    listed, plus the warnings. Never raises."""
    root = Path(root or config.PLUGINS_DIR)
    plugins: list[Plugin] = []
    warnings: list[str] = []
    try:
        if not root.is_dir():
            return plugins, warnings
        dirs = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError as e:
        return plugins, [f"plugins root {root} unreadable: {e}"]
    seen: set[str] = set()
    # A directory whose own name IS its declared name goes first, so a plugin
    # cannot squat another's name by sorting earlier in the alphabet.
    dirs.sort(key=lambda d: (_declared_name(d) or d.name.strip().lower()) != d.name.strip().lower())
    for d in dirs:
        try:
            manifest = d / "plugin.yaml"
            if not manifest.is_file():
                manifest = d / "plugin.yml"
            found = manifest.is_file()
        except OSError as e:   # a directory this process cannot traverse
            warnings.append(f"plugin dir {d.name}: unreadable ({e.__class__.__name__}), skipped")
            continue
        if not found:
            warnings.append(f"plugin dir {d.name}: no plugin.yaml, skipped")
            continue
        try:
            meta = parse_flat_yaml(manifest.read_text(encoding="utf-8", errors="replace"))
        except OSError as e:
            warnings.append(f"plugin dir {d.name}: plugin.yaml unreadable: {e}")
            continue
        name = (meta.get("name") or d.name).strip().lower()
        if not _NAME_RE.match(name):
            warnings.append(f"plugin dir {d.name}: bad name {name!r} (lowercase letters, digits, - _)")
            continue
        if name in seen:
            warnings.append(f"plugin dir {d.name}: it declares the name {name!r}, which the "
                            f"directory of that name already has; skipped")
            continue
        seen.add(name)
        pl = Plugin(name=name, path=d, description=meta.get("description", "").strip(),
                    version=meta.get("version", "").strip(),
                    default_enabled=_truthy(meta.get("enabled", "true")),
                    capabilities=[c.strip() for c in meta.get("capabilities", "").split(",") if c.strip()])
        if not pl.enabled:
            plugins.append(pl)
            continue
        try:
            if pl.skill_dir:
                pl.skills = sum(1 for _ in pl.skill_dir.rglob("SKILL.md"))
            if pl.tool_dir:
                pl.tools = sum(1 for f in pl.tool_dir.glob("*.py") if not f.name.startswith("_"))
            if (d / "agents").is_dir():
                pl.agents, w = _agent_specs(d / "agents", name)
                warnings.extend(w)
            if (d / "mcp.json").is_file():
                from .mcp_client import load_config

                pl.mcp_servers, w = load_config(d / "mcp.json")
                warnings.extend(f"plugin {name}: {x}" for x in w)
        except Exception as e:  # a plugin's insides must never cost the boot
            warnings.append(f"plugin {name}: {type(e).__name__}: {e}")
        plugins.append(pl)
    return plugins, warnings


# --- what the rest of Dream asks ---------------------------------------------------------

_LOADED: list[Plugin] = []
_WARNINGS: list[str] = []


def load(root: Path | None = None) -> tuple[list[Plugin], list[str]]:
    """Discover once at boot and remember the result for /plugins and the tools."""
    global _LOADED, _WARNINGS
    _EXTRA_WARNINGS.clear()
    _LOADED, _WARNINGS = discover(root)
    return _LOADED, _WARNINGS


def loaded() -> list[Plugin]:
    return list(_LOADED)


def warnings() -> list[str]:
    return list(dict.fromkeys([*_WARNINGS, *_EXTRA_WARNINGS]))


def owner(path: Path) -> Plugin | None:
    """Resolve provenance by package path, including a declared name differing
    from its directory. Never infer ownership from a tool's claimed name."""
    try:
        target = path.resolve()
        return next((p for p in _LOADED if target.is_relative_to(p.path.resolve())), None)
    except OSError:
        return None


def skill_dirs() -> list[Path]:
    return [p.skill_dir for p in _LOADED if p.enabled and p.skill_dir]


def tool_dirs() -> list[Path]:
    return [p.tool_dir for p in _LOADED if p.enabled and p.tool_dir]


def agent_specs() -> list[dict[str, Any]]:
    """Every enabled plugin's agents. Two plugins claiming one name: the first
    wins and the second is named in a warning (Gate 12)."""
    out, taken = [], set()
    for p in _LOADED:
        if not p.enabled:
            continue
        for a in p.agents:
            if a["name"] in taken:
                w = (f"plugin {p.name}: agent {a['name']!r} is already provided by another "
                     "plugin; skipped")
                if w not in _EXTRA_WARNINGS:
                    add_warnings([w])
                continue
            taken.add(a["name"])
            out.append(a)
    return out


def mcp_servers() -> list[dict[str, Any]]:
    from .extensions import filter_mcp_configs

    out = []
    for p in _LOADED:
        if p.enabled:
            out.extend(filter_mcp_configs([{**s, "_dream_plugin": p.name, "_dream_source": str(p.path / "mcp.json")}
                                           for s in p.mcp_servers], plugin=p.name))
    return out


def find(query: str, limit: int = 10) -> list[Plugin]:
    """Name-and-description search over every plugin, enabled or not."""
    words = [w for w in re.split(r"\W+", query.lower()) if w]
    if not words:
        return []
    scored = []
    for p in _LOADED:
        hay = f"{p.name} {p.description}".lower()
        score = sum(2 if w in p.name else 1 for w in words if w in hay)
        if score:
            scored.append((-score, p.name, p))
    return [p for _, _, p in sorted(scored)[:limit]]


_EXTRA_WARNINGS: list[str] = []


def add_warnings(more: list[str]) -> None:
    """Warnings the OTHER loaders raised about plugin parts (a tool that failed
    to import, an MCP name collision). /plugins is where a person looks for
    them, so the engine hands them back here (Gate 12)."""
    _EXTRA_WARNINGS.extend(w for w in more if w)


def status_lines() -> list[str]:
    """What /plugins prints."""
    if not _LOADED and not _WARNINGS and not _EXTRA_WARNINGS:
        return [f"no plugins under {config.PLUGINS_DIR}"]
    lines = []
    for p in _LOADED:
        state = "" if p.enabled else " (disabled)"
        parts = ", ".join(p.parts) or "no parts"
        ver = f" v{p.version}" if p.version else ""
        lines.append(f"{p.name}{ver}{state} — {parts}" + (f" — {p.description}" if p.description else ""))
    for w in (*_WARNINGS, *_EXTRA_WARNINGS):
        lines.append(f"! {w}")
    return lines
