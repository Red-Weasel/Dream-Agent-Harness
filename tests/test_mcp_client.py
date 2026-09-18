"""Consuming external MCP servers.

The stdio server under test is Dream's OWN (``python -m dream.mcp``) pointed at a
tmp database: real MCP over real pipes, no network, no fake protocol. If the client
can drive that, it can drive any stdio server.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from dream import mcp_client
from dream.mcp_client import prompt_section, McpClients, load_config


def _dream_server(tmp_path: Path, name: str = "mem") -> dict:
    return {
        "name": name, "command": sys.executable, "args": ["-m", "dream.mcp"],
        "env": {"DREAM_SESSION_ID": "t", "DREAM_DB": str(tmp_path / "t.db"),
                "DREAM_WORKSPACE": str(tmp_path), "DREAM_ROOT": str(tmp_path)},
    }


def policy_gated(tool_id: str) -> bool:
    from dream.core import policy

    return policy.capability(tool_id) not in policy.AUTO_CAPS


# --- criterion 1: config ---------------------------------------------------------


def test_a_missing_config_means_no_servers_and_no_complaint(tmp_path):
    assert load_config(tmp_path / "absent.json") == ([], [])


def test_malformed_json_is_a_warning_not_a_crash(tmp_path):
    p = tmp_path / "mcp.json"; p.write_text("{not json", encoding="utf-8")
    servers, warnings = load_config(p)
    assert servers == [] and warnings and "unreadable" in warnings[0]


def test_the_shape_matches_what_dream_already_emits(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"servers": [
        {"name": "graph", "command": "npx", "args": ["-y", "x"], "env": {"A": "1"}}]}),
        encoding="utf-8")
    servers, warnings = load_config(p)
    assert not warnings
    assert servers == [{"name": "graph", "command": "npx", "args": ["-y", "x"], "env": {"A": "1"}}]


def test_a_server_name_that_would_break_tool_naming_is_refused(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"servers": [{"name": "bad__name", "command": "x"},
                                        {"name": "ok", "command": "x"}]}), encoding="utf-8")
    servers, warnings = load_config(p)
    assert [s["name"] for s in servers] == ["ok"]
    assert any("__" in w for w in warnings)


def test_a_server_named_like_dreams_own_is_refused(tmp_path):
    """It would mint mcp__dream__dream__<tool> — legal, gated, and confusing in every
    permission prompt. The name is reserved."""
    import dream.config as config

    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"servers": [{"name": config.MCP_SERVER_NAME, "command": "x"}]}),
                 encoding="utf-8")
    servers, warnings = load_config(p)
    assert servers == [] and any("reserved" in w for w in warnings)


def test_duplicate_server_names_keep_the_first(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"servers": [{"name": "a", "command": "x"},
                                        {"name": "a", "command": "y"}]}), encoding="utf-8")
    servers, warnings = load_config(p)
    assert len(servers) == 1 and servers[0]["command"] == "x"
    assert any("duplicate" in w for w in warnings)


# --- criteria 2, 4: registration and round trip over real stdio ------------------


@pytest.mark.asyncio
async def test_a_real_stdio_server_registers_namespaced_tools(tmp_path):
    clients = McpClients()
    try:
        tools, warnings = await clients.start([_dream_server(tmp_path)])
        assert not warnings
        names = {t.name for t in tools}
        assert "mem__recall" in names and "mem__read_notes" in names
        assert all(n.startswith("mem__") for n in names)
        assert clients.servers["mem"] and "recall" in clients.servers["mem"]
    finally:
        await clients.stop()


@pytest.mark.asyncio
async def test_a_tool_call_round_trips_as_text(tmp_path):
    clients = McpClients()
    try:
        tools, _ = await clients.start([_dream_server(tmp_path)])
        by = {t.name: t for t in tools}
        res = await by["mem__read_notes"].handler({})
        assert res["is_error"] is False
        text = "".join(b["text"] for b in res["content"])
        assert "No working notes" in text
        # and a write, then a read, through the same session
        await by["mem__note"].handler({"text": "mcp round trip works"})
        res = await by["mem__read_notes"].handler({})
        assert "mcp round trip works" in "".join(b["text"] for b in res["content"])
    finally:
        await clients.stop()


@pytest.mark.asyncio
async def test_the_description_says_it_is_external(tmp_path):
    """The model must be able to tell a plug-in tool from Dream's own."""
    clients = McpClients()
    try:
        tools, _ = await clients.start([_dream_server(tmp_path)])
        assert all("external MCP" in t.description for t in tools)
    finally:
        await clients.stop()


# --- Gate 2 findings: a server named "mcp", malformed fields, duplicate tools -----------


_FAKE_SERVER = '''
import anyio
import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

server = Server("fake")

@server.list_tools()
async def list_tools():
    return [types.Tool(name=n, description=f"fake {n}",
                       inputSchema={"type": "object", "properties": {}})
            for n in ("dup", "solo", "dup")]

@server.call_tool()
async def call_tool(name, arguments):
    return [types.TextContent(type="text", text=f"ran:{name}")]

async def main():
    async with stdio_server() as (r, w):
        await server.run(r, w, server.create_initialization_options())

anyio.run(main)
'''


def test_a_server_named_mcp_is_refused_because_it_could_spoof_dreams_prefix(tmp_path):
    """Gate 2 finding 1: a server called "mcp" mints `mcp__read_file`, the prefix the
    classifier reserves for Dream's own server."""
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"servers": [{"name": "mcp", "command": "x"},
                                        {"name": "MCP", "command": "x"},
                                        {"name": "Dream", "command": "x"},
                                        {"name": "gräph", "command": "x"},
                                        {"name": "ok", "command": "x"}]}), encoding="utf-8")
    servers, warnings = load_config(p)
    assert [s["name"] for s in servers] == ["ok"]
    assert sum("is reserved" in w for w in warnings) == 3
    assert any("gräph" in w and "ASCII" in w for w in warnings)


def test_the_classifier_strips_only_dreams_own_prefix():
    """Even if a server somehow wore the prefix, it must not inherit a free pass."""
    from pathlib import Path as _P

    from dream.core import policy

    assert policy.capability("mcp__dream__recall") == policy.READONLY
    for spoof in ("mcp__read_file", "mcp__Read", "mcp__recall", "mcp__evil__read_file",
                  "mcp__run_bash", "mcp__write_file"):
        assert policy.capability(spoof) == policy.MUTATING, spoof
        assert policy.decide(spoof, {}, "ask", _P("/tmp/ws"))[0] == "ask", spoof


def test_malformed_fields_are_warnings_never_a_crash(tmp_path):
    """Gate 2 finding 2: a bad `args`/`env`/`command` used to raise out of load_config
    and end the boot."""
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"servers": [
        {"name": "a", "command": "x", "env": "oops"},
        {"name": "b", "command": "x", "env": ["A=1"]},
        {"name": "c", "command": "x", "args": 5},
        {"name": "d", "command": "x", "args": "--flag"},
        {"name": "e", "command": ["x", "y"]},
        {"name": "f", "command": "   "},
        {"name": "good", "command": " x ", "args": ["--n", 3, True], "env": {"K": 1, "F": False}},
    ]}), encoding="utf-8")
    servers, warnings = load_config(p)
    assert [s["name"] for s in servers] == ["good"]
    assert servers[0] == {"name": "good", "command": "x", "args": ["--n", "3", "true"],
                          "env": {"K": "1", "F": "false"}}
    assert len(warnings) == 6
    for who in "abcdef":
        assert any(f"server '{who}'" in w for w in warnings), who


@pytest.mark.asyncio
async def test_duplicate_tool_names_within_one_server_keep_the_first_and_warn(tmp_path):
    import sys

    script = tmp_path / "fake.py"
    script.write_text(_FAKE_SERVER, encoding="utf-8")
    clients = McpClients()
    try:
        tools, warnings = await clients.start([{"name": "fake", "command": sys.executable,
                                                "args": [str(script)], "env": {}}])
        assert [t.name for t in tools] == ["fake__dup", "fake__solo"]
        assert clients.servers["fake"] == ["dup", "solo"]
        assert any("duplicate tool 'dup'" in w for w in warnings)
    finally:
        await clients.stop()


# --- criterion 7: routing rule in the system prompt ---------------------------------


def test_no_servers_means_no_prompt_section():
    assert prompt_section({}) == ""


def test_the_prompt_section_names_every_tool_and_states_the_routing_rule():
    text = prompt_section({"graph": ["search", "trace"], "db": ["query"]})
    assert "graph" in text and "2 tool(s)" in text
    assert "`graph__search`" in text and "`graph__trace`" in text and "`db__query`" in text
    # the rule, and its two teeth: category match wins; results are data
    assert "matches the category" in text
    assert "over hand-rolled work" in text
    assert "never" in text and "instructions" in text


def test_engine_puts_the_section_in_the_stable_tier():
    eng = (Path(__file__).parent.parent / "dream" / "core" / "engine.py").read_text()
    assert "mcp_prompt_section(self._mcp.servers)" in eng
    # appended to `stable` (tier 2), after the servers are connected
    assert eng.index("await self._mcp.start(") < eng.index("mcp_prompt_section(self._mcp.servers)")
    assert "stable.append(mcp_section)" in eng


# --- criterion 2: classification ----------------------------------------------------


def test_namespaced_tools_are_gated_by_default():
    from dream.core import policy

    assert policy.capability("graph__search") == policy.MUTATING
    assert policy.capability("graph__search") not in policy.AUTO_CAPS


@pytest.mark.asyncio
async def test_namespaced_tools_survive_the_registry(tmp_path):
    from dream.tools import registry

    clients = McpClients()
    try:
        tools, _ = await clients.start([_dream_server(tmp_path)])
        import dream.config as config

        built = registry.build(extra_tools=tools)
        assert "mem__recall" in built["names"]
        assert not [w for w in built["warnings"] if "mem__" in w]
        # Exact id membership. The first version of this assertion used substring
        # `in`, and "dream__recall" is a substring of Dream's OWN "mcp__dream__recall"
        # — the test was wrong, the classifier was right.
        assert config.tool_id("mem__recall") not in built["exempt_tool_ids"]
        assert policy_gated(config.tool_id("mem__recall"))
    finally:
        await clients.stop()


# --- criterion 3: a dead server is a warning ---------------------------------------


@pytest.mark.asyncio
async def test_a_server_that_cannot_start_is_a_warning_and_others_still_load(tmp_path):
    clients = McpClients()
    try:
        tools, warnings = await clients.start([
            {"name": "ghost", "command": "/nonexistent/binary", "args": [], "env": {}},
            _dream_server(tmp_path),
        ])
        assert any("ghost" in w and "failed to start" in w for w in warnings)
        assert any(t.name.startswith("mem__") for t in tools)
        assert "ghost" not in clients.servers
    finally:
        await clients.stop()


@pytest.mark.asyncio
async def test_a_hung_server_is_a_timeout_warning_not_a_hung_boot(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_client, "START_TIMEOUT_S", 1.0)
    clients = McpClients()
    try:
        # `sleep` opens the pipes and never speaks MCP.
        tools, warnings = await clients.start([
            {"name": "sleepy", "command": "sleep", "args": ["30"], "env": {}}])
        assert tools == []
        assert any("sleepy" in w and "did not answer" in w for w in warnings)
    finally:
        await clients.stop()


@pytest.mark.asyncio
async def test_a_failing_tool_call_is_an_error_result_not_an_exception(tmp_path):
    clients = McpClients()
    try:
        tools, _ = await clients.start([_dream_server(tmp_path)])
        by = {t.name: t for t in tools}
        # recall with a bogus 'kind' — the server answers, possibly with an error
        # result; the point is that nothing raises through the handler.
        res = await by["mem__recall"].handler({"query": "x", "kind": "not-a-kind"})
        assert "content" in res and isinstance(res["content"], list)
    finally:
        await clients.stop()


# --- criterion 5: /mcp is wired ----------------------------------------------------


def test_mcp_command_is_wired_and_documented():
    app = (Path(__file__).parent.parent / "dream" / "tui" / "app.py").read_text()
    assert 'cmd == "mcp"' in app
    assert "/mcp " in app
    assert "MCP_CONFIG_PATH" in app


def test_engine_connects_at_start_and_closes_at_cleanup():
    eng = (Path(__file__).parent.parent / "dream" / "core" / "engine.py").read_text()
    assert "await self._mcp.start(" in eng
    assert "await mcp.stop()" in eng
    # connected BEFORE registry.build so the tools land in the same registry
    assert eng.index("await self._mcp.start(") < eng.index("built = registry.build(")


@pytest.mark.asyncio
async def test_stop_is_idempotent_and_clears_state(tmp_path):
    clients = McpClients()
    await clients.start([_dream_server(tmp_path)])
    await clients.stop()
    await clients.stop()
    assert clients.servers == {}
