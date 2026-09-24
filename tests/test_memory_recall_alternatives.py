"""Caller-supplied lexical alternatives for one bounded recall request."""

from __future__ import annotations

import pytest

from dream.memory.store import MemoryStore
from dream.tools import context as tool_context
from dream.tools.context import ToolContext, set_context
from dream.tools.memory_tools import recall


@pytest.fixture
def store(tmp_path):
    memory_store = MemoryStore(tmp_path / "memory.db")
    # The Engine scopes its store to the workspace's project (DREAM-108); so does this one.
    from dream.memory.project import project_key
    memory_store.project = project_key(tmp_path)
    set_context(ToolContext(
        store=memory_store,
        working=None,  # type: ignore[arg-type]
        browser=None,  # type: ignore[arg-type]
        session_id="recall-alternatives",
        workspace=tmp_path,
    ))
    yield memory_store
    tool_context._CTX = None
    memory_store.close()


def _text(result):
    return result["content"][0]["text"]


@pytest.mark.asyncio
async def test_recall_uses_a_caller_supplied_alternative_and_names_it(store):
    store.upsert_memory(
        "semantic",
        "Dietary constraints",
        "Alex is vegetarian and has a peanut allergy.",
        slug="diet",
    )
    store.upsert_memory(
        "semantic",
        "Meeting reminder",
        "Keep the Tuesday planning meeting in mind.",
        slug="meeting",
    )

    result = await recall.handler({
        "query": "food restrictions for dinner",
        "alternative_queries": ["dietary constraints"],
    })

    text = _text(result)
    assert "diet" in text
    assert "matched alternative: 'dietary constraints'" in text
    assert "meeting" not in text


@pytest.mark.asyncio
async def test_recall_keeps_a_primary_exact_match_before_alternative_hits(store):
    store.upsert_memory("semantic", "Dinner plan", "Dinner reservation is Friday.", slug="dinner")
    store.upsert_memory("semantic", "Dietary constraints", "Vegetarian, peanut allergy.", slug="diet")

    result = await recall.handler({
        "query": "dinner",
        "alternative_queries": ["dietary constraints"],
    })

    text = _text(result)
    assert text.index("slug: dinner") < text.index("slug: diet")
    assert "matched alternative" not in text.split("slug: dinner", 1)[0]


@pytest.mark.asyncio
async def test_recall_without_alternatives_keeps_the_primary_search_path(store, monkeypatch):
    store.upsert_memory("semantic", "Dinner plan", "Dinner reservation is Friday.", slug="dinner")

    def alternative_path(*args, **kwargs):
        raise AssertionError("primary-only recall must not use alternative retrieval")

    monkeypatch.setattr(store, "search_memories_with_alternatives", alternative_path)
    result = await recall.handler({"query": "dinner"})
    assert "slug: dinner" in _text(result)


def test_alternative_union_respects_limit_and_bumps_each_memory_once(store):
    store.upsert_memory("semantic", "Dietary constraints", "Vegetarian, peanut allergy.", slug="diet")
    store.upsert_memory("semantic", "Dinner plan", "Dinner reservation is Friday.", slug="dinner")

    hits = store.search_memories_with_alternatives(
        "dietary", ["dietary constraints", "dinner"], limit=2
    )

    assert [hit["slug"] for hit in hits] == ["diet", "dinner"]
    assert store.get_memory("diet")["access_count"] == 1
    assert store.get_memory("dinner")["access_count"] == 1


@pytest.mark.asyncio
async def test_recall_does_not_present_alternative_recency_as_a_match(store):
    store.upsert_memory("semantic", "Unrelated", "latest unrelated fact", slug="unrelated")

    result = await recall.handler({
        "query": "no matching subject",
        "alternative_queries": ["also absent"],
    })

    text = _text(result)
    assert "Nothing matched 'no matching subject' directly" in text
    assert "matched alternative" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [
    {"query": ""},
    {"query": "x" * 257},
    {"query": "dinner", "limit": "bogus"},
    {"query": "dinner", "limit": 0},
    {"query": "dinner", "limit": -2},
    {"query": "dinner", "limit": 1.5},
    {"query": "dinner", "limit": True},
    {"query": "dinner", "unexpected": "value"},
])
async def test_recall_rejects_invalid_primary_limit_and_unknown_arguments(store, args):
    result = await recall.handler(args)
    assert result["is_error"] is True


def test_store_alternative_api_canonicalizes_and_bounds_its_own_queries(store, monkeypatch):
    store.upsert_memory("semantic", "Dietary constraints", "Vegetarian, peanut allergy.", slug="diet")
    original_search = store.search_memories
    calls = []

    def recording_search(query, *args, **kwargs):
        calls.append(query)
        return original_search(query, *args, **kwargs)

    monkeypatch.setattr(store, "search_memories", recording_search)
    hits = store.search_memories_with_alternatives(
        "missing", ["  dietary constraints  ", "DIETARY CONSTRAINTS"], limit=1
    )

    assert [hit["slug"] for hit in hits] == ["diet"]
    assert calls == ["missing", "dietary constraints"]
    with pytest.raises(ValueError, match="alternative_queries"):
        store.search_memories_with_alternatives("missing", ["a", "b", "c", "d"])
    with pytest.raises(ValueError, match="limit"):
        store.search_memories_with_alternatives("missing", [], limit=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("alternatives", ["dietary constraints", [""], ["a", "b", "c", "d"]])
async def test_recall_rejects_malformed_or_unbounded_alternatives(store, alternatives):
    result = await recall.handler({"query": "dinner", "alternative_queries": alternatives})
    assert result["is_error"] is True
    assert "alternative_queries" in _text(result)
