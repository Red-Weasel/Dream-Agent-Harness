"""Plugin and skill discovery tools (Phase 12): search what is installed, and
offer what could be enabled as a Studio card — at most one card per session
unless asked again."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from .. import plugins
from ..core.backends.base import Event
from ..skills import loader
from . import installed_skill_tools
from .context import ctx, err, ok, studio

_OFFERED: dict[str, bool] = {"plugins": False, "skills": False}


def reset_offers() -> None:
    _OFFERED.update(plugins=False, skills=False)


def _ids(raw: Any) -> list[str]:
    """The ids a caller passed. A bare string is ONE id, never a list of its
    characters (Gate 12)."""
    if isinstance(raw, str):
        raw = [raw]
    elif not isinstance(raw, (list, tuple)):
        return []           # a scalar or a dict is not a list of ids
    return [str(i).strip() for i in raw if isinstance(i, (str, int)) and str(i).strip()]


def _again(raw: Any) -> bool:
    return raw if isinstance(raw, bool) else str(raw or "").strip().lower() in ("true", "yes", "1")


def _card(kind: str, items: list[dict[str, str]], again: bool) -> dict[str, Any]:
    """Render the offer: a Studio card when the panel is up, its text form
    otherwise — and only once per session unless `again`."""
    if _OFFERED[kind] and not again:
        return ok(f"A {kind} suggestion card was already shown this session; pass again=true "
                  "if the user asked to see it again. Otherwise say it in words.")
    _OFFERED[kind] = True
    lines = [f"[{kind} to enable]" if kind == "plugins" else "[skills to try]"]
    for it in items:
        lines.append(f"  • {it['id']} — {it['what']}")
    text = "\n".join(lines)
    emit = ctx().emit
    if emit is not None and studio() is not None:
        emit(Event("studio", {"op": "widget", "widget": f"suggest_{kind}",
                              "data": {"items": items}, "text": text}))
        note = ("Each button fills the user's composer with 'Enable plugin <id>'; enabling is a "
                "plugin.yaml edit, in effect at the next boot." if kind == "plugins"
                else "Each button fills the user's composer with 'Use skill <id>'.")
        return ok(text + f"\n\n(shown as a card in Studio — {note})")
    return ok(text + "\n\n(no Studio: offer it in words)")


@tool(
    "search_plugins",
    "Search the plugins installed under plugins/ by name and description — enabled "
    "and disabled alike, with what each one carries (skills, tools, agents, MCP).",
    {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
)
async def search_plugins(args: dict[str, Any]) -> dict[str, Any]:
    q = str(args.get("query") or "").strip()
    if not q:
        return err("search_plugins: give a word or two.")
    hits = plugins.find(q)
    if not hits:
        return ok(f"No plugin matches '{q}'." + ("" if plugins.loaded() else " (No plugins are installed.)"))
    lines = [f"{len(hits)} plugin(s) for '{q}':"]
    for p in hits:
        state = "enabled" if p.enabled else "disabled"
        lines.append(f"• {p.name} ({state}; {', '.join(p.parts) or 'no parts'})"
                     + (f" — {p.description}" if p.description else ""))
    return ok("\n".join(lines))


@tool(
    "search_skills",
    "Search the skills installed on this machine — Dream's own, plugin skills, and the "
    "curated roots — by name and description. skill_open a name for the full text.",
    {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
)
async def search_skills(args: dict[str, Any]) -> dict[str, Any]:
    q = str(args.get("query") or "").strip()
    if not q:
        return err("search_skills: give a word or two.")
    hits = loader.find(installed_skill_tools.installed(), q)[:25]
    if not hits:
        return ok(f"No installed skill matches '{q}'.")
    return ok(f"{len(hits)} skill(s) for '{q}':\n" + "\n".join(f"• {s.index_line}" for s in hits))


@tool(
    "suggest_plugin_install",
    "Offer plugins to enable, as a card: ids from search_plugins. One card per session "
    "unless again=true (the user asked to see it again). Calling this enables nothing.",
    {"type": "object", "properties": {
        "ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 6},
        "again": {"type": "boolean"}}, "required": ["ids"]},
)
async def suggest_plugin_install(args: dict[str, Any]) -> dict[str, Any]:
    ids = list(dict.fromkeys(i.lower() for i in _ids(args.get("ids"))))
    if not ids:
        return err("suggest_plugin_install: ids is empty.")
    known = {p.name: p for p in plugins.loaded()}
    items, missing = [], []
    for i in ids[:6]:
        p = known.get(i)
        if p is None:
            missing.append(i)
            continue
        what = (p.description or ", ".join(p.parts) or "no description") + (" (already enabled)" if p.enabled else "")
        items.append({"id": p.name, "what": what})
    if not items:
        return err(f"suggest_plugin_install: no installed plugin named {', '.join(missing)}. "
                   "search_plugins shows what is here.")
    res = _card("plugins", items, _again(args.get("again")))
    extra = list(missing) + ([f"{len(ids) - 6} id(s) past the first six"] if len(ids) > 6 else [])
    if extra and not res.get("is_error"):
        res["content"][0]["text"] += f"\n(not shown: {', '.join(extra)})"
    return res


@tool(
    "suggest_skills",
    "Offer installed skills to try, as a card: names from search_skills. One card per "
    "session unless again=true.",
    {"type": "object", "properties": {
        "ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 6},
        "again": {"type": "boolean"}}, "required": ["ids"]},
)
async def suggest_skills(args: dict[str, Any]) -> dict[str, Any]:
    ids = list(dict.fromkeys(_ids(args.get("ids"))))
    if not ids:
        return err("suggest_skills: ids is empty.")
    by_name = {s.name: s for s in installed_skill_tools.installed()}
    items, missing = [], []
    for i in ids[:6]:
        s = by_name.get(i)
        if s is None:
            missing.append(i)
            continue
        items.append({"id": s.name, "what": (s.description or "").strip()[:160] or "no description"})
    if not items:
        return err(f"suggest_skills: no installed skill named {', '.join(missing)}. search_skills shows them.")
    res = _card("skills", items, _again(args.get("again")))
    extra = list(missing) + ([f"{len(ids) - 6} id(s) past the first six"] if len(ids) > 6 else [])
    if extra and not res.get("is_error"):
        res["content"][0]["text"] += f"\n(not shown: {', '.join(extra)})"
    return res


PLUGIN_TOOLS = [search_plugins, search_skills, suggest_plugin_install, suggest_skills]
