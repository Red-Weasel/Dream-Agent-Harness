"""Regression tests for the memory-store audit: crashed-session note recovery,
derived-slug collisions, WAL durability settings, index coverage, the clustering
prefilter, embedding-free read paths, and concurrent embedder construction."""

import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from dream.memory.embeddings import Embedder
from dream.memory.store import MemoryStore


def _store(**kw) -> MemoryStore:
    return MemoryStore(Path(tempfile.mkdtemp()) / "t.db", **kw)


def _ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


def _backdate(store: MemoryStore, session_id: str, hours: float) -> None:
    """Rewrite a session's whole activity trail into the past — what a session that
    died N hours ago looks like on disk."""
    ts = _ago(hours)
    store._conn.execute("UPDATE sessions SET started_at=? WHERE id=?", (ts, session_id))
    store._conn.execute("UPDATE working_notes SET ts=? WHERE session_id=?", (ts, session_id))
    store._conn.execute("UPDATE turns SET ts=? WHERE session_id=?", (ts, session_id))
    store._conn.commit()


# --- defect 1: notes from a crashed session are recoverable -------------------


def test_crashed_session_notes_are_recovered():
    """A session that died without end_session (no ended_at) still holds durable
    working notes; once it's plainly stale they must reach the stray sweep."""
    s = _store()
    s.start_session("dead")
    s.add_note("dead", "durable note from the crashed session")
    s.add_turn("dead", "user", "hi")
    _backdate(s, "dead", 48)

    texts = [n["note"] for n in s.stray_notes(exclude_session="current")]
    assert "durable note from the crashed session" in texts


def test_live_session_notes_are_not_stolen():
    """A concurrently-running second Dream instance must keep its notes: recent
    activity of any kind (note or turn) means the session is alive, not crashed."""
    s = _store()
    # Open and recently active — clearly live.
    s.start_session("live")
    s.add_note("live", "note from the live instance")

    # Open, old notes, but a recent turn: still live, just quiet for a while.
    s.start_session("quiet")
    s.add_note("quiet", "note from the quiet instance")
    _backdate(s, "quiet", 48)
    s.add_turn("quiet", "user", "still here")

    texts = [n["note"] for n in s.stray_notes(exclude_session="current")]
    assert "note from the live instance" not in texts
    assert "note from the quiet instance" not in texts


def test_crashed_session_notes_retire_once_consolidated():
    """Recovered notes go through the normal retire path, so the next sweep is clean."""
    s = _store()
    s.start_session("dead")
    s.add_note("dead", "recover me")
    _backdate(s, "dead", 48)

    strays = s.stray_notes(exclude_session="current")
    assert [n["note"] for n in strays] == ["recover me"]
    s.mark_notes_consolidated_ids([n["id"] for n in strays])
    assert s.stray_notes(exclude_session="current") == []


# --- defect 2: a derived slug must not clobber an unrelated memory ------------


def test_derived_slug_does_not_clobber_unrelated_memory():
    """Two unrelated titles that collide after the 60-char slug truncation must end
    up as two memories, not one silently overwritten."""
    s = _store()
    base = "Session 2026-08-01 debugging the memory store retrieval path"
    a = s.upsert_memory("episodic", base + " in recall", "the retrieval story")
    b = s.upsert_memory("episodic", base + " in dreaming", "the dreaming story")

    assert a["slug"] != b["slug"]
    assert s.get_memory(a["slug"])["body"] == "the retrieval story"
    assert s.get_memory(b["slug"])["body"] == "the dreaming story"
    assert len(s.all_memories()) == 2


def test_derived_slug_does_not_clobber_across_kinds():
    s = _store()
    a = s.upsert_memory("semantic", "Deploy", "the deploy host is rack-1")
    b = s.upsert_memory("procedural", "Deploy", "run the deploy script")

    assert a["slug"] != b["slug"]
    assert s.get_memory(a["slug"])["body"] == "the deploy host is rack-1"
    # Re-saving the second one stays in place: same kind + same title is the same memory.
    again = s.upsert_memory("procedural", "Deploy", "run the deploy script, twice")
    assert again["slug"] == b["slug"]
    assert len(s.all_memories()) == 2


def test_same_title_and_kind_still_updates_in_place():
    s = _store()
    first = s.upsert_memory("semantic", "The user likes concision", "v1")
    second = s.upsert_memory("semantic", "The user likes concision", "v2")
    assert first["slug"] == second["slug"]
    assert s.get_memory(first["slug"])["body"] == "v2"
    assert len(s.all_memories()) == 1


def test_empty_slug_still_derives_from_the_title():
    """The remember tool forwards whatever the model sent: an empty slug means
    'no slug', not 'a memory whose slug is the empty string'."""
    s = _store()
    m = s.upsert_memory("semantic", "Coffee order", "flat white", slug="")
    assert m["slug"] == "coffee-order"


def test_explicit_slug_still_updates_in_place():
    """Consolidation depends on this: an explicit slug means 'update that memory'."""
    s = _store()
    s.upsert_memory("semantic", "Original title", "v1", slug="fixed")
    s.upsert_memory("semantic", "Completely different title", "v2", slug="fixed")
    assert len(s.all_memories()) == 1
    assert s.get_memory("fixed")["body"] == "v2"


# --- defect 3: WAL durability settings ---------------------------------------


def test_wal_uses_normal_synchronous_and_a_busy_timeout():
    s = _store()
    assert s._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert s._conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    assert s._conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000


# --- defect 5: the hot query patterns must use indices -----------------------


def _plan(store: MemoryStore, sql: str, params) -> str:
    rows = store._conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    return " | ".join(r[-1] for r in rows)


def test_hot_queries_use_indices():
    s = _store()
    notes = _plan(
        s,
        "SELECT id, note, ts, consolidated FROM working_notes WHERE session_id=? ORDER BY id",
        ("x",),
    )
    assert "SCAN working_notes" not in notes and "USING INDEX" in notes, notes

    pairs = _plan(
        s, "DELETE FROM reconciled_pairs WHERE slug_a=? OR slug_b=?", ("a", "a")
    )
    assert "SCAN reconciled_pairs" not in pairs, pairs

    # Ordered index walk (LIMIT stops it early) instead of reading every session
    # into a temp b-tree just to sort it.
    sessions = _plan(s, "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (5,))
    assert "idx_sessions_started" in sessions, sessions
    assert "TEMP B-TREE" not in sessions, sessions

    prev = _plan(
        s,
        "SELECT * FROM sessions WHERE id!=? AND ended_at IS NOT NULL "
        "ORDER BY started_at DESC LIMIT 1",
        ("x",),
    )
    assert "TEMP B-TREE" not in prev, prev


# --- defect 4: the clustering prefilter must not change the answer -----------


def _reference_clusters(rows, low, high=None, skip_pairs=None):
    """The pre-prefilter implementation, kept here as the equivalence oracle."""
    mat = np.vstack([np.frombuffer(r["embedding"], dtype="float32") for r in rows])
    sims = mat @ mat.T
    n = len(rows)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if rows[i]["kind"] != rows[j]["kind"]:
                continue
            s = float(sims[i][j])
            if s < low or (high is not None and s >= high):
                continue
            if skip_pairs and tuple(sorted((rows[i]["slug"], rows[j]["slug"]))) in skip_pairs:
                continue
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [g for g in groups.values() if len(g) >= 2]


def _at_cosine(base, other, c: float):
    """A unit vector whose cosine to ``base`` is exactly ``c``."""
    perp = other - float(base @ other) * base
    perp /= np.linalg.norm(perp)
    v = c * base + np.sqrt(1.0 - c * c) * perp
    return (v / np.linalg.norm(v)).astype("float32")


def _corpus(n: int = 120, dim: int = 48):
    """Fixture corpus: random unit vectors plus planted neighbor families at known
    cosines, so the merge band (>=0.93), the reconcile band ([0.83, 0.93)) and the
    below-threshold noise are all represented — including a cross-kind near-twin
    that must never be clustered."""
    rng = np.random.default_rng(11)
    vecs = rng.standard_normal((n, dim)).astype("float32")
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    kinds = ["semantic"] * n
    # Two families around rows 0 (semantic) and 1 (procedural), plus a cross-kind twin.
    kinds[1] = "procedural"
    plants = [(2, 0, 0.99), (3, 0, 0.95), (4, 0, 0.88), (5, 0, 0.84),
              (6, 1, 0.96), (7, 1, 0.86), (8, 0, 0.995)]
    for idx, base_idx, cos in plants:
        if idx >= n:
            continue
        vecs[idx] = _at_cosine(vecs[base_idx], vecs[idx], cos)
        kinds[idx] = kinds[base_idx]
    if n > 8:
        kinds[8] = "procedural"  # near-twin of a semantic row, but a different kind
    for i in range(9, n, 7):
        kinds[i] = "procedural"
    return [
        {"id": i, "slug": f"s{i}", "kind": kinds[i], "embedding": vecs[i].tobytes()}
        for i in range(n)
    ]


def _norm(clusters):
    return sorted(sorted(c) for c in (clusters or []))


def test_similarity_clusters_match_reference_implementation():
    rows = _corpus()
    cases = [
        (0.93, None, None),
        (0.83, 0.93, None),
        (0.5, None, None),
        (0.83, 0.93, {tuple(sorted(("s0", "s2")))}),
        (0.99, None, None),
    ]
    for low, high, skip in cases:
        got = MemoryStore._similarity_clusters(rows, low, high, skip_pairs=skip)
        want = _reference_clusters(rows, low, high, skip_pairs=skip)
        assert _norm(got) == _norm(want), (low, high, skip)
    # The corpus must actually exercise clustering, or the comparison proves nothing.
    assert _norm(MemoryStore._similarity_clusters(rows, 0.93, None))
    assert _norm(MemoryStore._similarity_clusters(rows, 0.83, 0.93))
    # s8 is a near-twin of s0 in a different kind: never the same cluster.
    merged = _norm(MemoryStore._similarity_clusters(rows, 0.93, None))
    assert not any(0 in c and 8 in c for c in merged)


def test_similarity_clusters_handles_unstackable_embeddings():
    rows = _corpus(4)
    rows[1]["embedding"] = b"\x00\x01\x02"  # ragged: can't be stacked
    assert MemoryStore._similarity_clusters(rows, 0.9) is None


# --- defect 6: read paths must not drag the embedding blob along -------------


def test_read_paths_omit_the_embedding_blob():
    s = _store()
    made = s.upsert_memory("semantic", "A fact", "the sky is blue", slug="a", tags="sky")
    s._conn.execute(
        "UPDATE memories SET embedding=? WHERE slug=?",
        (np.zeros(384, dtype="float32").tobytes(), "a"),
    )
    s._conn.commit()

    assert "embedding" not in made
    assert "embedding" not in s.get_memory("a")
    for rows in (
        s.top_memories(),
        s.all_memories(),
        s.all_memories(kind="semantic"),
        s.recent_memories(),
    ):
        assert rows, "expected the fixture memory"
        for r in rows:
            assert "embedding" not in r
            # Everything callers actually read must survive the column list.
            for col in ("id", "slug", "kind", "title", "body", "tags", "salience",
                        "facet", "created_at", "updated_at", "access_count",
                        "last_accessed", "source_session"):
                assert col in r, col

    hits = s.search_memories("sky blue")
    assert hits and all("embedding" not in h for h in hits)
    # The recency fallback (nothing matched) feeds the same callers.
    fallback = s.search_memories("zzzz nothing matches this")
    assert fallback and all(h.get("via_recency") for h in fallback)
    assert all("embedding" not in h for h in fallback)


# --- defect 7: the embedder must be constructed once under concurrency -------


class _FakeTextEmbedding:
    constructed = 0
    _count_lock = threading.Lock()

    def __init__(self, model_name=None, **kw):
        with self._count_lock:
            _FakeTextEmbedding.constructed += 1
        time.sleep(0.05)  # wide enough for the racing threads to pile in

    def embed(self, texts):
        return iter([np.ones(4, dtype="float32")])


def test_embedder_builds_the_model_once_under_concurrency(monkeypatch):
    import types

    fake = types.ModuleType("fastembed")
    fake.TextEmbedding = _FakeTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", fake)
    _FakeTextEmbedding.constructed = 0

    e = Embedder("fake-model")
    start = threading.Barrier(8)
    results = []

    def worker():
        start.wait()
        results.append(e.available())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(results)
    assert _FakeTextEmbedding.constructed == 1, _FakeTextEmbedding.constructed
