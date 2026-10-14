"""DREAM-294: on the Claude path Dream's own tools load whole into every request and an external MCP server's tools
wait behind Claude Code's tool search. Read through a real MCP client session, so the `_meta` mark is checked as it
goes over the wire to the CLI. No model, no CLI."""
from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server
from mcp.shared.memory import create_connected_server_and_client_session

from dream.core.backends.anthropic import ALWAYS_LOAD_META, always_load, sdk_options
from dream.tools import registry


async def _handler(args):
    return {"content": [{"type": "text", "text": "ok"}]}


def _tool(name):
    return SdkMcpTool(name=name, description=f"[rea · external MCP] {name}",
                      input_schema={"type": "object", "properties": {}}, handler=_handler)


async def _marks(server):
    async with create_connected_server_and_client_session(server["instance"]) as client:
        listed = (await client.list_tools()).tools
    return {tool.name: bool((tool.meta or {}).get(ALWAYS_LOAD_META)) for tool in listed}


async def test_dream_tools_load_whole_and_external_mcp_tools_wait():
    built = registry.build(extra_tools=[_tool("rea__compare_web_captures")])
    marks = await _marks(built["server"])
    assert set(marks) == set(built["names"])
    assert marks.pop("rea__compare_web_captures") is False
    assert marks and all(marks.values()), [name for name, loaded in marks.items() if not loaded]


async def test_always_load_marks_only_the_named_tools_and_keeps_their_other_meta():
    from claude_agent_sdk import ToolAnnotations

    kept = SdkMcpTool(name="read_file", description="r", input_schema={"type": "object", "properties": {}},
                      handler=_handler, annotations=ToolAnnotations(maxResultSizeChars=500))
    server = always_load(create_sdk_mcp_server(name="review", tools=[kept, _tool("other")]), ["read_file"])
    async with create_connected_server_and_client_session(server["instance"]) as client:
        listed = {tool.name: tool.meta for tool in (await client.list_tools()).tools}
    assert listed["read_file"] == {"anthropic/maxResultSizeChars": 500, ALWAYS_LOAD_META: True}
    assert not (listed["other"] or {}).get(ALWAYS_LOAD_META)


def test_claude_code_tool_search_is_on():
    options = sdk_options(system_prompt="s", mcp_servers={}, preapproved_tool_ids=[], agents=None,
                          can_use_tool=None, model=None, effort=None, cwd=".")
    assert options.env["ENABLE_TOOL_SEARCH"] == "true"
