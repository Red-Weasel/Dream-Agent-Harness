"""Build and run Dream's stdio MCP server.

With DREAM_PARENT_BRIDGE_FILE, proxy the live parent session's full tool set and
preserve rich results. Bridge mode never creates a separate memory/tool context.
Without it, retain the standalone memory interface described below.

The server owns no model loop of its own — it's a thin bridge. Each exposed Dream tool
is an ``SdkMcpTool`` (name, description, input_schema, async handler); we register an
MCP tool that mirrors its schema and, when called, invokes the Dream tool's own
``.handler(args)`` and flattens the result to text. The live ``ToolContext`` (memory
store + this session's working memory) is built from the environment at startup so the
handlers read exactly the same state the in-process tools do.

The tool dispatch is factored into a plain ``async def dispatch(name, args) -> str`` and
``build_context_from_env()`` so tests (and any embedder) can drive recall/remember
against a tmp DB without spawning stdio.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import mcp.types as mcp_types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .. import config
from ..core.backends.base import content_to_text
from ..memory.store import MemoryStore
from ..memory.working import WorkingMemory
from ..tools import memory_tools, notes, web
from ..tools.context import ToolContext, set_context

# The Dream tools this MCP server exposes to a CLI engine, in list order. Only the
# mind-facing tools travel over MCP; ``browse``/``see`` are skipped this cycle because
# they need the live browser process, which the standalone server doesn't own.
_EXPOSED = [
    memory_tools.recall,
    memory_tools.remember,
    memory_tools.forget,
    notes.note,
    notes.read_notes,
    memory_tools.recall_sessions,
    web.web_search,
]

_TOOLS_BY_NAME = {t.name: t for t in _EXPOSED}


def build_context_from_env() -> ToolContext:
    """Open the memory store this session points at and install a ``ToolContext`` so the
    delegated Dream handlers find their state. Reads ``DREAM_SESSION_ID`` (the live
    session), ``DREAM_DB`` (defaults to ``config.DB_PATH``), and ``DREAM_WORKSPACE``.

    The MCP server runs as a separate process against the SAME db the live engine
    already opened, so we do NOT start a session here — we join the one in flight.
    """
    session_id = os.environ.get("DREAM_SESSION_ID") or "mcp"
    db = os.environ.get("DREAM_DB") or str(config.DB_PATH)
    workspace = os.environ.get("DREAM_WORKSPACE")

    store = MemoryStore(db)
    working = WorkingMemory(store, session_id)
    context = ToolContext(
        store=store,
        working=working,
        browser=None,  # web_search needs no browser; browse/see aren't exposed.
        session_id=session_id,
        workspace=Path(workspace) if workspace else config.ROOT,
    )
    set_context(context)
    return context


async def dispatch(name: str, args: dict[str, Any] | None) -> str:
    """Invoke an exposed Dream tool by name and return its text content.

    Both the stdio server's ``call_tool`` handler and the tests call this, so an
    unknown tool or a handler failure comes back as a clear error string rather than an
    exception that would kill the loop.
    """
    tool = _TOOLS_BY_NAME.get(name)
    if tool is None:
        available = ", ".join(sorted(_TOOLS_BY_NAME))
        return f"Error: unknown Dream tool '{name}'. Available tools: {available}."
    try:
        result = await tool.handler(args or {})
    except Exception as e:  # a tool crash must not take the server down
        return f"Error: {name} failed: {type(e).__name__}: {e}"
    content = result.get("content", result) if isinstance(result, dict) else result
    return content_to_text(content)


def build_server(bridge=None) -> Server:
    """Assemble the low-level MCP ``Server`` with ``list_tools``/``call_tool`` wired to
    the exposed Dream tools. Building the context is the caller's job (``serve`` does it)
    so the server object can be constructed in-process by tests without any env."""
    server: Server = Server(config.MCP_SERVER_NAME, version=config.__dict__.get("VERSION", "0.1.0"))

    @server.list_tools()
    async def _list_tools() -> list[mcp_types.Tool]:
        if bridge is not None:
            return await bridge.list_tools()
        return [
            mcp_types.Tool(
                name=t.name,
                description=t.description,
                inputSchema=t.input_schema,
            )
            for t in _EXPOSED
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None):
        if bridge is not None:
            try:
                return await bridge.call_tool(name, arguments)
            except Exception as exc:
                from .bridge import BridgeError
                message = str(exc) if isinstance(exc, BridgeError) else 'Parent tool bridge failed; outcome may be uncertain'
                return mcp_types.CallToolResult(isError=True, content=[mcp_types.TextContent(type='text', text=message)])
        text = await dispatch(name, arguments)
        return [mcp_types.TextContent(type="text", text=text)]

    return server


async def serve() -> None:
    """Boot the context from the environment and run the server over stdio until the
    client (the CLI engine) closes the pipe."""
    from .bridge import ParentBridgeClient
    bridge = ParentBridgeClient.from_environment()
    # A configured-but-invalid bridge raises above. Never open a second memory
    # context or silently expose the standalone seven tools on bridge failure.
    if bridge is None:
        build_context_from_env()
    server = build_server(bridge)
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        if bridge is not None:
            await bridge.close()
