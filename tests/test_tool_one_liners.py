"""Every built-in tool has its one catalog line (description discovery's catalog, dream.core.tool_one_liners), and
a tool added without one is caught here, not by a model that cannot find it in the catalog. (Some lines are for tools
that register only with a feature on: GitHub, computer use, media.)"""

from dream.core import discovery
from dream.core.tool_one_liners import ONE_LINERS
from dream.tools import registry


def test_every_built_in_tool_has_a_one_liner():
    names = set(registry.build()["names"])
    assert names - set(ONE_LINERS) == set(), "add a line to dream/core/tool_one_liners.py for each new tool"


def test_a_one_liner_is_one_short_line_and_is_what_the_catalog_shows():
    for name, line in ONE_LINERS.items():
        assert line and "\n" not in line and len(line) <= discovery.DESCRIPTION_CHARS, name
        schema = {"function": {"name": name, "description": "A long description. " * 20}}
        assert discovery.catalog_line(schema) == f"{name} — {line}"
    assert sum(len(f"{n} — {l}") + 1 for n, l in ONE_LINERS.items()) <= discovery.CATALOG_CHARS, "all fit"
