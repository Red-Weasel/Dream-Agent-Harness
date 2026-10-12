"""Measure memory recall quality: recall@k and MRR over a golden query set.

Without measurement, memory upgrades are vibes. This harness makes them numbers.

Fixture mode (default) builds a throwaway store from ``eval/corpus.jsonl`` and runs
``eval/golden.jsonl`` against it — a deterministic measurement of the whole recall
pipeline (FTS + vectors + RRF + rerank + links + time filters). Live mode (``--db``)
runs the same golden queries against a real store, skipping queries whose expected
slugs don't exist there; the database is opened read-only (SQLite ``mode=ro``), so
no migrations, journal changes, or access-count bumps can touch it.

Usage:
    .venv/bin/python -m dream.eval                 # fixture mode, full pipeline
    .venv/bin/python -m dream.eval --keyword-only  # ablation: FTS alone
    .venv/bin/python -m dream.eval --no-rerank     # ablation: no cross-encoder
    .venv/bin/python -m dream.eval --db data/dream.db

Golden entries: {"query", "expect": [slug, ...], "kind"?, "since"?, "until"?}.
A query scores at the rank of the first expected slug in the results.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .memory.store import MemoryStore

EVAL_DIR = config.ROOT / "eval"


@dataclass
class QueryResult:
    query: str
    expect: list[str]
    rank: int | None  # 1-based rank of the first expected slug; None = miss
    got: list[str]
    abstained: bool = False
    latency_ms: float = 0.0
    violations: list[str] = field(default_factory=list)


@dataclass
class EvalReport:
    results: list[QueryResult] = field(default_factory=list)
    skipped: int = 0
    evidence: str = "synthetic fixture; shared-corpus qualification not performed"
    casesearch_status: str = "unavailable: source/API and authorized shared corpus not supplied"

    def recall_at(self, k: int) -> float:
        relevant = [r for r in self.results if r.expect]
        if not relevant:
            return 0.0
        hits = sum(1 for r in relevant if r.rank is not None and r.rank <= k)
        return hits / len(relevant)

    @property
    def mrr(self) -> float:
        relevant = [r for r in self.results if r.expect]
        if not relevant:
            return 0.0
        return sum(1.0 / r.rank for r in relevant if r.rank) / len(relevant)

    def render(self) -> str:
        lines = [
            f"queries: {len(self.results)}  skipped: {self.skipped}",
            f"recall@1: {self.recall_at(1):.2f}   recall@3: {self.recall_at(3):.2f}   "
            f"recall@5: {self.recall_at(5):.2f}   MRR: {self.mrr:.2f}",
        ]
        abstentions = [r for r in self.results if not r.expect]
        lines.append(f"abstentions: {sum(r.abstained for r in abstentions)}/{len(abstentions)}  "
                     f"violations: {sum(len(r.violations) for r in self.results)}")
        lines.append(f"evidence: {self.evidence}; CaseSearch {self.casesearch_status}")
        misses = [r for r in self.results if r.expect and r.rank is None]
        late = [r for r in self.results if r.rank is not None and r.rank > 1]
        if late:
            lines.append("\nhit but not first:")
            for r in late:
                lines.append(f"  rank {r.rank}: {r.query!r} → wanted {r.expect}")
        if misses:
            lines.append("\nmisses:")
            for r in misses:
                lines.append(f"  {r.query!r} → wanted {r.expect}, got {r.got[:5]}")
        return "\n".join(lines)


def load_jsonl(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("//"):
            out.append(json.loads(line))
    return out


def build_fixture_store(
    corpus: list[dict], db_path: str | Path, embedder=None, reranker=None
) -> MemoryStore:
    store = MemoryStore(db_path, embedder=embedder, reranker=reranker)
    for m in corpus:
        store.upsert_memory(
            kind=m["kind"],
            title=m["title"],
            body=m["body"],
            slug=m["slug"],
            tags=m.get("tags", ""),
            salience=float(m.get("salience", 1.0)),
            project=m.get("project", ""), lifecycle=m.get("lifecycle", {}),
            source_session=m.get("source_session"),
            created_at=m.get("created_at"), updated_at=m.get("updated_at"),
        )
    # Backdate after all inserts so autolinking saw the full corpus.
    for m in corpus:
        if m.get("created_at"):
            store.set_created_at(m["slug"], m["created_at"])
    return store


def run_eval(store: MemoryStore, golden: list[dict], k: int = 8, *,
             active: bool = False, retriever=None, evidence: str = "synthetic fixture; shared-corpus qualification not performed") -> EvalReport:
    """Same query/scope contract for an explicitly supplied local retrieval adapter.

    retriever receives query/kind/limit/since/until/scope keyword arguments and
    returns memory dictionaries with slug/project/lifecycle provenance. No adapter
    is invented for CaseSearch; absence is explicitly unavailable, never a score.
    Recency browse rows are excluded from evidence and abstention scoring.
    """
    from .memory.curation import active_status, lifecycle_json
    report = EvalReport(evidence=evidence)
    for g in golden:
        if not isinstance(g.get("expect"), list) or any(not isinstance(s, str) for s in g["expect"]):
            raise ValueError("expect must be a list of slugs; [] means abstention")
        expect = list(g["expect"])
        if expect and all(store.get_memory(s) is None for s in expect):
            report.skipped += 1
            continue
        scope = g.get("scope", store.default_scope())
        if scope is not None and (not isinstance(scope, (list, tuple)) or any(not isinstance(s, str) for s in scope)):
            raise ValueError("scope must be an explicit list of project keys or null")
        kwargs = dict(kind=g.get("kind"), limit=int(g.get("limit", k)),
                      since=g.get("since"), until=g.get("until"), scope=scope)
        start = time.perf_counter()
        if retriever is not None:
            hits = retriever(g["query"], **kwargs)
        elif active:
            hits = store.search_active_memories(g["query"], **kwargs)
        else:
            hits = store.search_memories(g["query"], bump=False, **kwargs)
        elapsed = (time.perf_counter() - start) * 1000
        hits = [h for h in hits if not h.get("via_recency")]
        got = [h["slug"] for h in hits]
        violations = []
        forbidden = set(g.get("forbidden", [])) | set(g.get("stale", []))
        for h in hits:
            if h["slug"] in forbidden:
                violations.append("forbidden:" + h["slug"])
            if scope is not None and h.get("project") not in scope:
                violations.append("scope:" + h["slug"])
            try:
                target_slug = json.loads(lifecycle_json(h.get("lifecycle"))).get("superseded_by")
            except ValueError:
                target_slug = None
            status = active_status(h, target=store.get_memory(target_slug) if target_slug else None)
            if status not in ("eligible", "conflict"):
                violations.append(status + ":" + h["slug"])
        if g.get("max_latency_ms") is not None and elapsed > float(g["max_latency_ms"]):
            violations.append("latency")
        rank = next((i for i, slug in enumerate(got, 1) if slug in expect), None)
        report.results.append(QueryResult(g["query"], expect, rank, got,
                                          not got, elapsed, violations))
    return report


def _make_models(keyword_only: bool, no_rerank: bool):
    embedder = reranker = None
    if not keyword_only and config.SEMANTIC_MEMORY:
        from .memory.embeddings import Embedder

        embedder = Embedder(config.EMBED_MODEL)
        if not no_rerank and config.RERANK:
            from .memory.embeddings import Reranker

            reranker = Reranker(config.RERANK_MODEL)
    return embedder, reranker


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate Dream's memory recall quality.")
    ap.add_argument("--db", help="run against an existing store instead of the fixture")
    ap.add_argument("--corpus", default=str(EVAL_DIR / "corpus.jsonl"))
    ap.add_argument("--golden", default=str(EVAL_DIR / "golden.jsonl"))
    ap.add_argument("--k", type=int, default=8, help="result depth per query (default 8)")
    ap.add_argument("--keyword-only", action="store_true", help="ablation: FTS only")
    ap.add_argument("--active", action="store_true", help="bounded active keyword evidence only")
    ap.add_argument("--no-rerank", action="store_true", help="ablation: skip cross-encoder")
    ap.add_argument(
        "--floor", type=float, default=0.0,
        help="exit 1 if recall@5 falls below this (for CI)",
    )
    args = ap.parse_args(argv)

    golden = load_jsonl(Path(args.golden))
    embedder, reranker = _make_models(args.keyword_only or args.active, args.no_rerank)

    if args.db:
        store = MemoryStore(args.db, embedder=embedder, reranker=reranker, readonly=True)
    else:
        corpus = load_jsonl(Path(args.corpus))
        tmp = tempfile.mkdtemp(prefix="dream-eval-")
        print(f"building fixture store ({len(corpus)} memories)...", file=sys.stderr)
        store = build_fixture_store(corpus, Path(tmp) / "eval.db", embedder, reranker)

    report = run_eval(store, golden, k=args.k, active=args.active,
                      evidence="local existing database; no shared-corpus qualification" if args.db
                      else "synthetic fixture; shared-corpus qualification not performed")
    print(report.render())
    if args.floor and report.recall_at(5) < args.floor:
        print(f"\nFAIL: recall@5 {report.recall_at(5):.2f} < floor {args.floor}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
