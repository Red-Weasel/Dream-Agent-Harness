"""Deterministic context/recall measurements; never a live-model quality score."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import time


def main():
    from dream import config
    source_root = Path(__file__).resolve().parents[1]
    output = source_root / "artifacts" / "harness-validation" / "measurements.json"
    with tempfile.TemporaryDirectory(prefix="dream-harness-report-") as temporary:
        root = Path(temporary)
        # Fixture memory writes mirror to files. Redirect before constructing any
        # store, and use built-in schemas only (no executable extension imports).
        for name, relative in {
            "ROOT": ".", "DATA_DIR": "data", "MEMORY_DIR": "memory", "LOG_DIR": "logs",
            "SEMANTIC_DIR": "memory/semantic", "EPISODIC_DIR": "memory/episodic",
            "PROCEDURAL_DIR": "memory/procedural", "MEMORY_INDEX_FILE": "memory/MEMORY.md",
            "IDENTITY_FILE": "memory/IDENTITY.md", "THREADS_FILE": "memory/THREADS.md",
            "INSTRUCTIONS_FILE": "memory/INSTRUCTIONS.md",
        }.items():
            setattr(config, name, root / relative)
        config.SEMANTIC_MEMORY = config.RERANK = False
        os.environ["DREAM_EXTENSION_SETTINGS"] = str(root / "extensions.json")
        from dream.core.backends.openai_compat import OpenAICompatBackend
        from dream.core.profiles import PROFILES
        from dream.core.providers import get_provider
        from dream.core.subagents import local_subagents
        from dream.core.tool_budget_schemas import measure
        from dream.tools.registry import _BASE_TOOLS
        from dream.tools.native import NATIVE_TOOLS
        from dream.tools.demonstration_tools import DEMONSTRATION_TOOLS
        from dream.tools.capability_tools import CAPABILITY_TOOLS
        from dream.eval import build_fixture_store, run_eval, load_jsonl
        tools = list(_BASE_TOOLS) + list(NATIVE_TOOLS) + list(DEMONSTRATION_TOOLS) + list(CAPABILITY_TOOLS)
        contexts = []
        for window in (8192, 16384, 32768, 131072):
            backend = OpenAICompatBackend(provider=get_provider("machx"), model="fixture", system_prompt="Fixture instructions.",
                tools=tools, permission_cb=None, subagents=local_subagents(), profile=PROFILES["lean" if window <= 32768 else "frontier"])
            backend.n_ctx = window
            started = time.perf_counter()
            schemas = backend._request_tools()
            elapsed = time.perf_counter() - started
            cost = measure(schemas)
            contexts.append({"window": window, "schema_tokens_estimated": cost,
                             "share": cost / window, "loaded": len(schemas),
                             "deferred": len(backend._deferred_now), "assembly_ms": round(elapsed * 1000, 3)})
        corpus, golden = source_root / "eval/corpus.jsonl", source_root / "eval/golden.jsonl"
        store = build_fixture_store(load_jsonl(corpus), root / "fixture.db")
        try:
            started = time.perf_counter()
            recall = run_eval(store, load_jsonl(golden))
            recall_result = {"mode": "keyword-only", "queries": len(recall.results), "skipped": recall.skipped,
                             "recall_at_1": recall.recall_at(1), "recall_at_5": recall.recall_at(5), "mrr": recall.mrr,
                             "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}
        finally:
            store.close()
        report = {"format": "dream-harness-measurements/v1", "context": contexts, "memory_recall": recall_result,
                  "fixtures": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (corpus, golden)},
                  "model_loaded": False, "live_provider_benchmark": False,
                  "limits": "Built-in schemas only; estimates are not tokenization. Local assembly/recall timings are single-run measurements, not model throughput."}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if all(row["share"] <= .15 for row in contexts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
