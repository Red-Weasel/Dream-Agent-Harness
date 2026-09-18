"""Dream's tools: an in-process MCP server the agent reaches for.

Tools read shared runtime state (memory store, working memory, browser) through the
``context`` module, which the engine populates at boot. Drop a ``@tool`` into
``custom/`` and it's auto-loaded next boot — the agent can extend itself.
"""
