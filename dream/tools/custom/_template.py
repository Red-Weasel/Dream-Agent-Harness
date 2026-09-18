"""Template for a self-authored tool. Copy me to ``something.py`` (no leading
underscore) and I'll be live next boot.

Anatomy:
- Decorate an async function with ``@tool(name, description, schema)``.
- The handler receives one dict of arguments and returns
  ``{"content": [{"type": "text", "text": ...}], "is_error": bool}``.
- Reach shared state (memory, browser, this session) via ``ctx()`` from
  ``dream.tools.context``; use ``ok()`` / ``err()`` helpers for returns.

Files starting with ``_`` (like this one) are ignored by the loader.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from ..context import ctx, ok  # noqa: F401  (ctx available for stateful tools)


@tool(
    "echo",
    "Example self-authored tool: echoes its input back.",
    {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
)
async def echo(args: dict[str, Any]) -> dict[str, Any]:
    return ok(f"echo: {args['text']}")
