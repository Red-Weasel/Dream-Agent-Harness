"""Explicit MCP environments and ownership of hung server descendants."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from dream import mcp_client
from dream.core.execution import minimal_environment


SERVER = '''
import anyio, json, os
import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server
s = Server("environment-fixture")
@s.list_tools()
async def listing():
    return [types.Tool(name="environment", description="fixture", inputSchema={"type":"object","properties":{}})]
@s.call_tool()
async def call(name, arguments):
    names = ["ORDINARY_HOST_VALUE", "AWS_SECRET_ACCESS_KEY", "EXPLICIT", "CHOSEN", "PATH"]
    return [types.TextContent(type="text", text=json.dumps({k:os.environ.get(k) for k in names}))]
async def main():
    async with stdio_server() as (r,w):
        await s.run(r,w,s.create_initialization_options())
anyio.run(main)
'''


def test_minimal_environment_omits_unrecognized_names_not_only_secret_shaped(monkeypatch):
    monkeypatch.setenv("ORDINARY_HOST_VALUE", "private-fixture")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "private-fixture")
    monkeypatch.setenv("CHOSEN", "opt-in-fixture")
    env = minimal_environment({"EXPLICIT": "literal-fixture"}, inherit=("CHOSEN",))
    assert "ORDINARY_HOST_VALUE" not in env and "AWS_SECRET_ACCESS_KEY" not in env
    assert env["EXPLICIT"] == "literal-fixture" and env["CHOSEN"] == "opt-in-fixture"


def test_config_inheritance_is_explicit_and_validated(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"servers": [
        {"name": "good", "command": "fixture", "inherit_env": ["CHOSEN"]},
        {"name": "bad", "command": "fixture", "inherit_env": "*"},
        {"name": "bad2", "command": "fixture", "env": {"BAD=NAME": "value"}},
    ]}))
    configs, warnings = mcp_client.load_config(path)
    assert [c["name"] for c in configs] == ["good"] and len(warnings) == 2
    assert configs[0]["inherit_env"] == ["CHOSEN"]


@pytest.mark.asyncio
async def test_real_mcp_receives_only_explicit_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDINARY_HOST_VALUE", "private-fixture")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "private-fixture")
    monkeypatch.setenv("CHOSEN", "opt-in-fixture")
    script = tmp_path / "server.py"
    script.write_text(SERVER)
    clients = mcp_client.McpClients()
    try:
        tools, warnings = await clients.start([dict(name="fixture", command=sys.executable,
            args=[str(script)], env={"EXPLICIT": "literal-fixture"}, inherit_env=["CHOSEN"])])
        assert not warnings
        result = await tools[0].handler({})
        env = json.loads(result["content"][0]["text"])
        assert env["ORDINARY_HOST_VALUE"] is None and env["AWS_SECRET_ACCESS_KEY"] is None
        assert env["EXPLICIT"] == "literal-fixture" and env["CHOSEN"] == "opt-in-fixture"
        assert env["PATH"]
    finally:
        await clients.stop()
    assert not clients._workers and not clients.servers


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_hung_mcp_startup_reaps_detached_children(tmp_path, monkeypatch, cancel):
    script = tmp_path / "hung.py"
    pids = tmp_path / "pids"
    script.write_text(f'''
import os,signal,time
from pathlib import Path
signal.signal(signal.SIGTERM,signal.SIG_IGN)
if os.fork() == 0:
    os.setsid()
    Path({str(pids)!r}).write_text(str(os.getpid()))
    while True: time.sleep(.1)
while not Path({str(pids)!r}).exists(): time.sleep(.01)
with Path({str(pids)!r}).open("a") as f: f.write(" " + str(os.getpid()))
while True: time.sleep(.1)
''')
    monkeypatch.setattr(mcp_client, "START_TIMEOUT_S", 20 if cancel else .7)
    clients = mcp_client.McpClients()
    try:
        async with asyncio.timeout(8):
            starting = asyncio.create_task(clients.start([dict(name="hung", command=sys.executable,
                                                       args=[str(script)], env={})]))
            if cancel:
                while not pids.exists():
                    await asyncio.sleep(.02)
                starting.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await starting
            else:
                tools, warnings = await starting
                assert not tools and "did not answer" in warnings[0]
        assert pids.exists()
        assert all(not Path(f"/proc/{pid}").exists() for pid in pids.read_text().split())
    finally:
        await clients.stop()
