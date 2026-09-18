"""Link participation in recall (reserved slots, auto-linking) and the eval harness."""

import tempfile
from pathlib import Path

import dream.config as config
from dream.eval import EVAL_DIR, build_fixture_store, load_jsonl, run_eval
from dream.memory.store import MemoryStore


def test_link_reserve_on_full_recall():
    """Linked memories surface even when ranked hits fill the limit."""
    s = MemoryStore(Path(tempfile.mkdtemp()) / "t.db")  # FTS-only is enough
    s.upsert_memory("semantic", "Gadget alpha", "gadget alpha specs, see [[widget-note]]", slug="g-a")
    s.upsert_memory("semantic", "Gadget beta", "gadget beta specs", slug="g-b")
    s.upsert_memory("semantic", "Gadget gamma", "gadget gamma specs", slug="g-c")
    s.upsert_memory("semantic", "Gadget delta", "gadget delta specs", slug="g-d")
    s.upsert_memory("semantic", "Widget note", "entirely about garden plants", slug="widget-note")

    hits = s.search_memories("gadget alpha specs", limit=3)
    slugs = [h["slug"] for h in hits]
    assert len(hits) == 3  # limit still respected
    assert "widget-note" in slugs  # association surfaced despite full recall
    assert slugs[0].startswith("g-")  # top ranked hit never displaced
    linked = next(h for h in hits if h["slug"] == "widget-note")
    assert linked.get("via_link") is True


def test_autolink_grows_the_graph(session_embedder, monkeypatch):
    monkeypatch.setattr(config, "AUTOLINK_THRESHOLD", 0.60)
    s = MemoryStore(Path(tempfile.mkdtemp()) / "t.db", embedder=session_embedder)
    s.upsert_memory(
        "semantic", "Inference server",
        "The rack server hosts the local inference model for the home network.",
        slug="a",
    )
    s.upsert_memory(
        "semantic", "Server location",
        "The local inference server for the network lives in the rack.",
        slug="b",
    )
    assert "a" in s.linked_slugs("b")  # discovered, not written by hand
    # Updating a body refreshes manual links without destroying auto links.
    s.upsert_memory(
        "semantic", "Inference server",
        "The rack server hosts the local inference model for the home network. GPU: 3080.",
        slug="a",
    )
    assert "b" in s.linked_slugs("a")


def test_eval_harness_floor(session_embedder):
    """The golden set must keep scoring — this is the regression tripwire for every
    future recall change. Floor is intentionally below measured (0.95 @5 full
    pipeline, 0.90 without rerank) to absorb embedder-version jitter, not misses."""
    corpus = load_jsonl(EVAL_DIR / "corpus.jsonl")
    golden = load_jsonl(EVAL_DIR / "golden.jsonl")
    store = build_fixture_store(
        corpus, Path(tempfile.mkdtemp()) / "eval.db", embedder=session_embedder
    )
    report = run_eval(store, golden)
    assert report.skipped == 0
    assert report.recall_at(5) >= 0.80
    assert report.mrr >= 0.70
