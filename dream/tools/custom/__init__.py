"""Self-extension zone.

Any ``*.py`` file here (not starting with ``_``) that defines ``@tool`` functions is
auto-loaded into Dream's MCP server at boot. This is how the agent gives itself new
capabilities that persist. See ``_template.py`` for the shape.
"""
