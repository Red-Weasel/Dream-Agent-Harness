"""Phase 12: plugins — one directory installs skills, tools, agents, and MCP
servers; a broken one is a warning; /plugins lists them; four tools search and
offer them."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream import config, extensions, plugins
from dream.core.backends.base import Event
from dream.tools import context as tool_context, installed_skill_tools
from dream.tools.context import ToolContext, set_context, set_studio
from dream.tools.plugin_tools import (
    PLUGIN_TOOLS, reset_offers, search_plugins, search_skills, suggest_plugin_install, suggest_skills,
)


def _plugin(root: Path, name: str, **parts) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "plugin.yaml").write_text(parts.pop("manifest", f"name: {name}\ndescription: {name} does things\nversion: 1.2\n"))
    if parts.get("skill"):
        s = d / "skills" / "greet"
        s.mkdir(parents=True)
        (s / "SKILL.md").write_text("---\nname: greet-people\ndescription: Greets people warmly\n---\nSay hello.\n")
    if parts.get("tool"):
        t = d / "tools"
        t.mkdir()
        (t / "shout.py").write_text(
            "from claude_agent_sdk import tool\n\n@tool('shout', 'Shouts.', {'type': 'object', 'properties': {}})\n"
            "async def shout(args):\n    return {'content': [{'type': 'text', 'text': 'LOUD'}]}\n")
    if parts.get("bad_tool"):
        t = d / "tools"
        t.mkdir(exist_ok=True)
        (t / "broken.py").write_text("this is not python (\n")
    if parts.get("agent"):
        a = d / "agents"
        a.mkdir()
        (a / "greeter.md").write_text("---\nname: greeter\ndescription: Greets\ntools: read_file, recall\n---\nYou greet people.\n")
        (a / "mute.md").write_text("---\nname: mute\n---\n\n")
    if parts.get("mcp"):
        (d / "mcp.json").write_text('{"servers": [{"name": "calc", "command": "python", "args": ["-m", "calc"]}]}')
    return d


@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = tmp_path / "plugins"
    monkeypatch.setattr(config, "PLUGINS_DIR", root)
    monkeypatch.setenv("DREAM_SKILL_DIRS", str(tmp_path / "no-skills"))
    installed_skill_tools.installed(refresh=True)
    yield root
    plugins.load(tmp_path / "none")
    installed_skill_tools.installed(refresh=True)


def test_discovery_loads_every_part_and_a_broken_plugin_is_a_warning(tree):
    _plugin(tree, "full", skill=True, tool=True, agent=True, mcp=True)
    _plugin(tree, "off", manifest="name: off\nenabled: false\n", tool=True)
    _plugin(tree, "Bad Name", manifest="name: Bad Name\n")
    (tree / "nomanifest").mkdir()
    _plugin(tree, "dup", manifest="name: full\n")
    _plugin(tree, "badtool", bad_tool=True)
    loaded, warnings = plugins.load(tree)
    by = {p.name: p for p in loaded}
    assert set(by) == {"full", "off", "badtool"}
    assert by["full"].parts == ["skills:1", "tools:1", "agents:1", "mcp:1"] and by["full"].version == "1.2"
    assert by["off"].enabled is False and by["off"].parts == []
    assert any("no plugin.yaml" in w for w in warnings) and any("bad name" in w for w in warnings)
    # a plugin cannot squat another's name by sorting earlier: the directory
    # whose own name IS the declared name wins, whatever the alphabet says
    assert any("dup" in w and "already has" in w for w in warnings)
    assert by["full"].path.name == "full"
    assert any("mute has no prompt" in w for w in warnings)
    # the parts reach the loaders that already exist
    assert plugins.skill_dirs() == [tree / "full" / "skills"]
    assert set(plugins.tool_dirs()) == {tree / "full" / "tools", tree / "badtool" / "tools"}
    assert [a["name"] for a in plugins.agent_specs()] == ["greeter"]
    assert plugins.agent_specs()[0]["tools"] == ("read_file", "recall")
    assert [s["name"] for s in plugins.mcp_servers()] == ["calc"]
    # /plugins lines
    lines = plugins.status_lines()
    assert any(l.startswith("full v1.2 — skills:1, tools:1, agents:1, mcp:1") for l in lines)
    assert any("off (disabled) — no parts" in l for l in lines)
    assert any(l.startswith("! ") for l in lines)


def test_the_skill_the_tool_and_the_agent_load_through_the_existing_loaders(tree):
    from dream.core import policy, subagents
    from dream.tools import registry

    _plugin(tree, "full", skill=True, tool=True, agent=True, bad_tool=True)
    plugins.load(tree)
    # skills: through config.skill_dirs → installed()
    names = {s.name for s in installed_skill_tools.installed(refresh=True)}
    assert "greet-people" in names
    # Approve only these synthetic sources so this exercises loading and syntax
    # error reporting beyond the import trust gate.
    for name in ("shout", "broken"):
        review = extensions.review_module(f"tool:plugin/full/{name}")
        assert review["source"] == (tree / "full" / "tools" / f"{name}.py").read_text()
        extensions.trust_module(review["id"], review["sha256"])
    # tools: through the registry's custom-tool walk, classed as custom (MUTATING)
    built = registry.build()
    assert "shout" in built["names"] and "shout" in built["custom_names"]
    assert policy.capability("mcp__dream__shout") == policy.MUTATING
    assert any("broken.py" in w for w in built["warnings"])
    # agents: in the roster, with only the tools that exist
    subs = subagents.local_subagents()
    assert "greeter" in subs and subs["greeter"].tool_names == ("read_file", "recall")
    assert "greeter" in subagents.subagents()
    # a plugin agent cannot replace a built-in one
    (tree / "full" / "agents" / "verifier.md").write_text("---\nname: verifier\n---\nI am fake.\n")
    _, warnings = plugins.load(tree)
    assert subagents.local_subagents()["verifier"].prompt != "I am fake."
    assert any("verifier" in w and "built-in" in w for w in warnings)


def test_mcp_servers_merge_and_a_taken_name_is_a_warning(tree, tmp_path):
    from dream.core.engine import merge_mcp_configs

    _plugin(tree, "full", mcp=True)
    plugins.load(tree)
    root_cfg = [{"name": "calc", "command": "other", "args": [], "env": {}}]
    merged, warnings = merge_mcp_configs(root_cfg, plugins.mcp_servers())
    assert [s["command"] for s in merged] == ["other"] and any("calc" in w and "taken" in w for w in warnings)
    merged, warnings = merge_mcp_configs([], plugins.mcp_servers())
    assert [s["name"] for s in merged] == ["calc"] and not warnings


@pytest.fixture
def ws(tmp_path, tree):
    emitted: list = []
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=tmp_path, emit=emitted.append))
    set_studio(object())
    reset_offers()
    yield emitted
    set_studio(None)
    reset_offers()
    tool_context._CTX = None


def _t(res):
    return res["content"][0]["text"]


def _bad(res):
    return bool(res.get("is_error"))


@pytest.mark.asyncio
async def test_search_and_suggest_tools_and_the_one_card_rule(tree, ws):
    emitted = ws
    _plugin(tree, "deckmaker", skill=True, manifest="name: deckmaker\ndescription: Builds slide decks\n")
    _plugin(tree, "off", manifest="name: off\nenabled: false\ndescription: Off for now\n")
    plugins.load(tree)
    installed_skill_tools.installed(refresh=True)
    out = _t(await search_plugins.handler({"query": "slide deck"}))
    assert out.startswith("1 plugin(s)") and "deckmaker (enabled; skills:1)" in out
    assert "off (disabled" in _t(await search_plugins.handler({"query": "off"}))
    assert "No plugin matches" in _t(await search_plugins.handler({"query": "zebra"}))
    assert _bad(await search_plugins.handler({"query": " "}))
    out = _t(await search_skills.handler({"query": "greet warmly"}))
    assert "greet-people" in out
    # suggest: a card once; the second call refuses unless again
    res = await suggest_plugin_install.handler({"ids": ["deckmaker", "nope"]})
    assert not _bad(res) and "shown as a card" in _t(res) and "(not shown: nope)" in _t(res)
    assert emitted[-1].data["widget"] == "suggest_plugins" and emitted[-1].data["data"]["items"][0]["id"] == "deckmaker"
    res = await suggest_plugin_install.handler({"ids": ["deckmaker"]})
    assert "already shown this session" in _t(res) and len(emitted) == 1
    res = await suggest_plugin_install.handler({"ids": ["deckmaker"], "again": True})
    assert "shown as a card" in _t(res) and len(emitted) == 2
    assert _bad(await suggest_plugin_install.handler({"ids": ["nope"]}))
    assert _bad(await suggest_plugin_install.handler({"ids": []}))
    res = await suggest_skills.handler({"ids": ["greet-people"]})
    assert "shown as a card" in _t(res) and emitted[-1].data["widget"] == "suggest_skills"
    # no Studio: words, not a card
    set_studio(None)
    reset_offers()
    res = await suggest_skills.handler({"ids": ["greet-people"]})
    assert "no Studio" in _t(res) and len(emitted) == 3


@pytest.mark.asyncio
async def test_gate12_a_plugin_cannot_end_the_boot_or_steal_a_name(tree, ws):
    """Gate 12 blocking finding: a tools/*.py with `sys.exit()` at module level
    escaped the loader and killed the boot. Plus the fixed observations."""
    from dream.core import subagents
    from dream.tools import registry

    d = _plugin(tree, "suicidal")
    (d / "tools").mkdir()
    (d / "tools" / "worse.py").write_text("import sys\ndef main():\n    return 3\nsys.exit(main())\n")
    # a manifest with NO name still owns its own directory's name
    (tree / "owner").mkdir()
    (tree / "owner" / "plugin.yaml").write_text("description: no name line\n")
    _plugin(tree, "aaa-squatter", manifest="name: owner\n")
    # two plugins claiming one agent name: the first wins, the second is warned
    for n in ("agent-a", "agent-b"):
        pd = _plugin(tree, n)
        (pd / "agents").mkdir()
        (pd / "agents" / "ghost.md").write_text(f"---\nname: ghost\ndescription: from {n}\n---\nI am {n}.\n")
    # an empty `enabled:` is a typo, not an off switch
    _plugin(tree, "typo", manifest="name: typo\nenabled:\n")
    loaded, warnings = plugins.load(tree)
    by = {p.name: p for p in loaded}
    assert by["owner"].path.name == "owner", "a nameless manifest still owns its directory"
    assert any("aaa-squatter" in w and "already has" in w for w in warnings)
    assert by["typo"].enabled is True
    # Exercise SystemExit recovery for an explicitly approved fixture module.
    review = extensions.review_module("tool:plugin/suicidal/worse")
    assert review["source"] == (d / "tools" / "worse.py").read_text()
    extensions.trust_module(review["id"], review["sha256"])
    built = registry.build()                       # would have raised SystemExit
    assert any("worse.py" in w for w in built["warnings"])
    specs = subagents.local_subagents()
    assert specs["ghost"].prompt == "I am agent-a."
    assert any("already provided by another plugin" in w for w in plugins.status_lines())
    # a tool that failed to import is visible where a person looks for it
    plugins.add_warnings([w for w in built["warnings"] if "plugin" in w.lower()])
    assert any(l.startswith("! ") for l in plugins.status_lines())


@pytest.mark.asyncio
async def test_gate12_ids_and_again_are_typed(tree, ws):
    _plugin(tree, "deckmaker")
    plugins.load(tree)
    # a bare string is ONE id, not a list of characters
    res = await suggest_plugin_install.handler({"ids": "deckmaker"})
    assert not _bad(res) and "deckmaker" in _t(res) and "not shown: d," not in _t(res)
    # "false" is not a reason to show a second card
    res = await suggest_plugin_install.handler({"ids": ["deckmaker"], "again": "false"})
    assert "already shown this session" in _t(res)
    res = await suggest_plugin_install.handler({"ids": ["deckmaker"], "again": True})
    assert "shown as a card" in _t(res)
    # ids past the sixth are said, not dropped in silence
    reset_offers()
    res = await suggest_plugin_install.handler({"ids": ["deckmaker"] + [f"x{i}" for i in range(8)]})
    assert "id(s) past the first six" in _t(res)


def test_the_four_are_registered_read_only_and_taught(tmp_path):
    from dream.core import policy, system_prompt
    from dream.memory.store import MemoryStore
    from dream.tools.registry import _BASE_TOOLS

    names = {t.name for t in _BASE_TOOLS}
    assert all(t.name in names for t in PLUGIN_TOOLS)
    for t in PLUGIN_TOOLS:
        assert policy.capability(f"mcp__dream__{t.name}") == policy.READONLY, t.name
    text = system_prompt.build_system_prompt(MemoryStore(tmp_path / "p.db"), "s")
    assert "search_plugins" in text and "suggest_skills" in text


@pytest.mark.asyncio
async def test_the_panel_renders_the_offer_cards_and_buttons_fill_the_composer(tmp_path):
    from playwright.async_api import async_playwright

    from dream.gui.bus import EventBus
    from dream.gui.server import StudioServer

    prompts: list[str] = []
    srv = StudioServer(EventBus(), on_prompt=prompts.append, session={"workspace": str(tmp_path)})
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1600, "height": 900})
            await page.goto(url, wait_until="load")
            await page.wait_for_function("document.getElementById('stat').textContent === 'live'", timeout=5000)
            srv.bus.publish(Event("studio", {"op": "widget", "widget": "suggest_plugins",
                                             "data": {"items": [{"id": "deckmaker", "what": "<b>Builds</b> decks"}]}}))
            await page.wait_for_selector(".vcard.suggest_plugins button", timeout=5000)
            assert "<b>Builds</b> decks" == await page.text_content(".vcard.suggest_plugins .what")
            await page.click(".vcard.suggest_plugins button")
            await page.wait_for_function("document.getElementById('input').value === 'Enable plugin deckmaker'", timeout=5000)
            assert prompts == [], "the button fills the composer; only the user sends"
            srv.bus.publish(Event("studio", {"op": "widget", "widget": "suggest_skills",
                                             "data": {"items": [{"id": "greet-people", "what": "Greets"}]}}))
            await page.wait_for_selector(".vcard.suggest_skills button", timeout=5000)
            await page.fill("#input", "")
            await page.click(".vcard.suggest_skills button")
            await page.wait_for_function("document.getElementById('input').value === 'Use skill greet-people'", timeout=5000)
            await browser.close()
    finally:
        await srv.stop()
