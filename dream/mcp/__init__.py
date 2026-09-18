"""Dream's standalone stdio MCP server.

A CLI coding-agent engine (codex, and later grok/gemini) has its own agent loop and
can't call Dream's in-process SDK tools. This package exposes Dream's memory tools
(and web_search) over the Model Context Protocol so such an engine can recall /
remember / note during its session — the identity-critical part. Run it with
``python -m dream.mcp``; the engine registers it scoped to the live session.
"""

from __future__ import annotations

from .server import (
    build_context_from_env,
    build_server,
    dispatch,
    serve,
)

__all__ = ["build_context_from_env", "build_server", "dispatch", "serve"]
