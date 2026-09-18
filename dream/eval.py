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


@dataclass
class EvalReport:
    results: list[QueryResult] = field(default_factory=list)
    skipped: int = 0

    def recall_at(self, k: int) -> float:
        if not self.results:
            return 0.0
        hits = sum(1 for r in self.results if r.rank is not None and r.rank <= k)
        return hits / len(self.results)

    @property
    def mrr(self) -> float:
        if not self.results:
            return 0.0
        return sum(1.0 / r.rank for r in self.results if r.rank) / len(self.results)

    def render(self) -> str:
        lines = [
            f"queries: {len(self.results)}  skipped: {self.skipped}",
            f"recall@1: {self.recall_at(1):.2f}   recall@3: {self.recall_at(3):.2f}   "
            f"recall@5: {self.recall_at(5):.2f}   MRR: {self.mrr:.2f}",
        ]
        misses = [r for r in self.results if r.rank is None]
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
        )
    # Backdate after all inserts so autolinking saw the full corpus.
    for m in corpus:
        if m.get("created_at"):
            store.set_created_at(m["slug"], m["created_at"])
    return store


def run_eval(store: MemoryStore, golden: list[dict], k: int = 8) -> EvalReport:
    report = EvalReport()
    for g in golden:
        expect = list(g["expect"])
        if all(store.get_memory(s) is None for s in expect):
            report.skipped += 1
            continue
        hits = store.search_memories(
            g["query"],
            kind=g.get("kind"),
            limit=int(g.get("limit", k)),
            since=g.get("since"),
            until=g.get("until"),
            bump=False,
        )
        got = [h["slug"] for h in hits]
        rank = next((i for i, s in enumerate(got, 1) if s in expect), None)
        report.results.append(QueryResult(g["query"], expect, rank, got))
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
    ap.add_argument("--no-rerank", action="store_true", help="ablation: skip cross-encoder")
    ap.add_argument(
        "--floor", type=float, default=0.0,
        help="exit 1 if recall@5 falls below this (for CI)",
    )
    args = ap.parse_args(argv)

    golden = load_jsonl(Path(args.golden))
    embedder, reranker = _make_models(args.keyword_only, args.no_rerank)

    if args.db:
        store = MemoryStore(args.db, embedder=embedder, reranker=reranker, readonly=True)
    else:
        corpus = load_jsonl(Path(args.corpus))
        tmp = tempfile.mkdtemp(prefix="dream-eval-")
        print(f"building fixture store ({len(corpus)} memories)...", file=sys.stderr)
        store = build_fixture_store(corpus, Path(tmp) / "eval.db", embedder, reranker)

    report = run_eval(store, golden, k=args.k)
    print(report.render())
    if args.floor and report.recall_at(5) < args.floor:
        print(f"\nFAIL: recall@5 {report.recall_at(5):.2f} < floor {args.floor}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
