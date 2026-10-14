"""2026-10-12, the MachX session's harness runs: inside a task container there is no SearXNG, and web_search's error
told the model to start it by hand. The model should be told plainly that search is unavailable here and retrying
will not help; the user keeps the diagnosis and the way to start it."""
from __future__ import annotations

import pytest

from dream.tools import web, widgets
from dream.web import searxng


@pytest.fixture
def search_down(monkeypatch):
    async def not_up():
        return False
    monkeypatch.setattr(searxng, "ensure_up", not_up)


TOOLS = [(web.web_search, {"query": "population of Lyon"}), (widgets.image_search, {"query": "Lyon"})]


@pytest.mark.parametrize("tool,args", TOOLS)
async def test_search_with_nothing_to_start_says_retrying_will_not_help(search_down, tool, args, monkeypatch,
                                                                        tmp_path):
    from dream import config
    monkeypatch.setattr(config, "SEARXNG_DIR", tmp_path / "no-searxng")     # a task container: nothing to start
    result = await tool.handler(args)
    text = result["content"][0]["text"]
    assert result.get("is_error")
    assert "Web search is unavailable" in text and "Retrying the search will not help" in text
    assert "not reachable at" in text and "python -m searx.webapp" in text      # the user's diagnosis stays


@pytest.mark.parametrize("tool,args", TOOLS)
async def test_a_searxng_that_may_still_be_starting_allows_one_retry(search_down, tool, args, monkeypatch, tmp_path):
    """(gate round 1) ensure_up gives up after 25 s while a SearXNG it started may still be coming up, and a later
    search then works: the model is told one retry may help, not that none will."""
    from dream import config
    monkeypatch.setattr(config, "SEARXNG_AUTOSTART", True)
    monkeypatch.setattr(config, "SEARXNG_DIR", tmp_path)
    text = (await tool.handler(args))["content"][0]["text"]
    assert "may still be starting" in text and "retry the search once" in text and "will not help" not in text
