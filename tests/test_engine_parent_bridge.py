"""The CLI bridge must call the real Engine policy and bound tool context."""
import pytest
from claude_agent_sdk import tool
from dream.core.engine import Engine
from dream.mcp.bridge import ParentBridgeClient
from dream.tools.context import ToolContext, ctx


async def test_engine_bridge_uses_parent_policy_context_toggle_and_cleanup(tmp_path):
    from dream import extensions
    attempts, calls = [], []
    permitted = False
    async def permission(name, arguments):
        attempts.append(name)
        return permitted
    engine = Engine(provider="codex", workspace=tmp_path, can_use_tool=permission)
    engine._tool_context = ToolContext(None, None, None, engine.session_id)
    @tool("bridge_fixture", "Test session boundary", {"value": str})
    async def fixture(arguments):
        calls.append(ctx().session_id)
        return {"content": [{"type": "text", "text": arguments["value"]}]}
    engine._session_tools = {"bridge_fixture": engine._wrap_tool(fixture)}
    engine._started = True
    client = None
    try:
        env = await engine._start_tool_bridge()
        client = ParentBridgeClient(env["DREAM_PARENT_BRIDGE_FILE"])
        assert [item.name for item in await client.list_tools()] == ["bridge_fixture"]
        denied = await client.call_tool("bridge_fixture", {"value": "no"})
        assert denied.isError and calls == [] and attempts == ["bridge_fixture"]
        permitted = True
        result = await client.call_tool("bridge_fixture", {"value": "yes"})
        assert not result.isError and result.content[0].text == "yes"
        assert calls == [engine.session_id]
        extensions.set_enabled("tool:bridge_fixture", False)
        assert await client.list_tools() == []
        assert (await client.call_tool("bridge_fixture", {"value": "disabled"})).isError
        assert calls == [engine.session_id]
    finally:
        if client:
            await client.close()
        discovery = engine._tool_bridge.discovery_path if engine._tool_bridge else None
        await engine._stop_tool_bridge()
        assert discovery is None or not discovery.exists()
