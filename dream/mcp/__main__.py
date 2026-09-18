"""``python -m dream.mcp`` — run Dream's stdio MCP server.

A CLI engine registers this (e.g. ``codex mcp add dream -- python -m dream.mcp``,
scoped, with ``DREAM_SESSION_ID`` in its env) so it can reach Dream's memory over MCP.
"""

from __future__ import annotations

import anyio

from .server import serve


def main() -> None:
    anyio.run(serve)


if __name__ == "__main__":
    main()
