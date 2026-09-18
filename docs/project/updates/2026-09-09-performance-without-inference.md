# DREAM-031 — Performance and quality without inference

Recorded: `2026-09-09T23:12:57-05:00`
Work items: `DREAM-031`
Outcome: `implemented`
Actor: Codex lead with document_reuse, offline_quality and performance_modes contributors.

## Request

Implement the preceding performance/quality recommendations while the owner
improves the inference engine. Explicit constraint: no GPU work, model loading,
live-model requests or real benchmarks. Acceptance is the CPU-verifiable portion
of [DREAM-031](../MASTER_PLAN.md), with model qualification deferred by the owner.
Preserved the pre-existing dirty tree, including DREAM-029/030 changes.

## Changes

- Session Custom/Quick/Balanced/Thorough controls preview output allowances and
  apply explicitly next turn. Current HTTP adapters preserve reasoning effort;
  unsupported CLI/SDK controls are labelled. Manual effort/output changes rebase
  Custom. No model or saved preset changes.
- Per-turn first activity/answer, request, phase and outcome measurements; empty
  headers excluded. Server timing/cache reports are separate from client latency
  and schema fingerprints. Expandable Chat cards survive reconnect. Timing
  records contain no prompt/answer/tool-argument text.
- Stable within-turn schema selection and cached argument validators. Invalid
  arguments fail before handler/approval. Dynamic dictionary keys are redacted
  from validation locations; remote schema references cannot retrieve resources.
- Optional filing has a bounded idle queue, visible outcomes, captured source
  context/meters, cancellation/join before foreground work, and cleanup before
  consolidation/shutdown. Required verification remains awaited. Late usage
  counts toward session totals once without creating another lead turn.
- Bounded document extraction reuse plus keyword excerpts with provenance.
  Real DOCX paging/query required one extraction, XLSX read/query one, PDF
  read/query two Poppler processes total. Errors/source changes invalidate reuse.
- Five CPU/native-tool quality fixtures and external-record grading, with eight
  progressive workflow examples. Review caught and corrected malformed example
  JSON, wrong recovery target/value acceptance, boolean-as-integer grading,
  stale direct output limits and missing background usage in session totals.

Operator details: performance, files,
evaluation, [ADR-011](../DECISIONS.md).

## Validation

Environment: existing Python 3.12.3 `.venv`, Linux; fixtures use temporary state
and fake transports. Browser fixtures use Chromium `--disable-gpu`. No real
inference requests, model loads, GPU sampling or inference-engine edits occurred.

Final combined regression: **493 passed, 1 deselected, 4 warnings in 29.65s**,
exit 0. Warnings were existing Starlette/AnyIO deprecations and two asyncio marks
on synchronous schema tests.
Exact reviewed allowlist and command are saved in
`/tmp/dream-performance-20260909-evidence/pytest-files.txt` and
`pytest-command.txt`; result evidence is `cpu-regression.log` and
`cpu-regression.xml` in that directory. This is a targeted suite, not a full-suite
or native-desktop run. One existing fixture subprocess-timeout test was excluded.

Earlier scoped evidence (overlapping, not additive): 165 backend/permission/runtime
checks passed; 43 subagent/loop checks passed; 39 schema/profile checks passed;
54 document checks passed. Default 8K schema measured 1,226 tokens against a 1,228
token ceiling. The standalone offline self-test/export and regrade passed all
five cases; a deliberately corrupted data output failed 4/5 with exit 1.
Final standalone `python -m dream.harness_eval --export` passed **5/5**; records
are `final-offline-records.json` in the evidence directory. `node --check` for both
changed scripts, Python compileall, and `UV_CACHE_DIR=/tmp/dream-performance-uv-cache
uv lock --check --offline` passed (111 packages, no upgrade/download).
Final visual fixture capture passed with no page errors; desktop (800px) and
mobile (390px) timing cards were inspected in `timing-desktop.png` and
`timing-mobile.png` in the evidence directory.
The first reconciliation reused an existing snapshot filename (refused), which
left two paths undocumented; a fresh final snapshot and exact list corrected
those omissions. Final tracking check: **passed, 58 changed source paths**.
`git diff --check` also passed.

Failures/limits: new regression tests first failed for missing behavior and were
made green. Initial `uv run --locked` failed on the read-only default cache; a
fresh task cache could not fetch hatchling with restricted DNS. Existing `.venv`
was used without package upgrades. Several sandbox AnyIO/native read tests stalled;
CPU checks outside the sandbox completed. Approval review timed out on initial
contributor escalations; allowed retries succeeded. Only contributor-owned stuck
fixture processes were canceled (exit 130/143); no user/engine process was touched.
Graph search hung/returned Transport closed; focused source reads were used after
attempting graph tools. No wheel build ran (hatchling absent from the environment).

## Unfinished work

None within the CPU-only implementation scope. Real model quality, speed,
workflow adherence, desktop model-startup and native WebKit qualification remain
unperformed by explicit owner constraint. No claim of universal LLM improvement,
ChatGPT-equivalent latency, production release or owner acceptance is made.

Current performance modes affect output allowance only on the HTTP adapter;
reported reasoning ladders are supported by the helper/UI but not sourced by that
adapter. Client cancellation does not prove server computation has stopped, and
this queue does not coordinate separate Dream processes. Document queries cover
selected extraction, with keyword rather than semantic relevance; cold concurrent
reads can duplicate parsing, and stat identity checks are not atomic snapshots.
JSON Schema cache size is bounded; hostile schema computation is not sandboxed.

## Next steps

DREAM-031/029: once the owner explicitly releases the GPU constraint, arrange an
isolated model comparison with fresh resource ownership checks. Use supplied
fixture tasks and export real records; compare correctness, failed/repeated tool
calls, time to answer, token usage and cache evidence with guidance on/off. Do not
load or benchmark now. DREAM-030's streaming-loader fix remains in place; no retry
was performed this session. Continue normal owner acceptance/release review
separately.

Recovery: no contributor remains editing source and no known owned test worker
remains after verification. Read CURRENT and inspect the dirty tree before resuming.
Baseline `/tmp/dream-performance-20260909-baseline.json` has 439 source hashes.
`/tmp/dream-performance-20260909-evidence/before.tar` is a local source backup
created after initial tracking edits, before lead implementation; contributors may
have begun disjoint work. Never blanket-restore it or HEAD. The final handoff lists
exact session paths; no commit, publish, model reload or release was performed.

## Files changed

- `docs/desktop.md`
- `docs/files.md`
- `docs/harness-evaluation.md`
- `docs/performance.md`
- `docs/project/CURRENT.md`
- `docs/project/DECISIONS.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-09-performance-without-inference.md`
- `docs/runtime-controls.md`
- `docs/superpowers/plans/2026-09-09-performance-without-inference.md`
- `dream/core/backends/openai_compat.py`
- `dream/core/engine.py`
- `dream/core/idle_work.py`
- `dream/core/performance.py`
- `dream/core/tool_validation.py`
- `dream/eval_fixtures/harness_cases.json`
- `dream/files/reading.py`
- `dream/gui/conversation.py`
- `dream/gui/static/controls.css`
- `dream/gui/static/controls.js`
- `dream/gui/static/index.html`
- `dream/gui/static/turn-timing.css`
- `dream/gui/static/turn-timing.js`
- `dream/harness_eval.py`
- `dream/telemetry/meter.py`
- `dream/telemetry/turn.py`
- `dream/tools/native.py`
- `dream/tui/app.py`
- `pyproject.toml`
- `skills/coding/SKILL.md`
- `skills/coding/references/examples.md`
- `skills/data-analysis/SKILL.md`
- `skills/data-analysis/references/examples.md`
- `skills/documents/SKILL.md`
- `skills/documents/references/examples.md`
- `skills/library/SKILL.md`
- `skills/library/references/examples.md`
- `skills/media/SKILL.md`
- `skills/media/references/examples.md`
- `skills/research/SKILL.md`
- `skills/research/references/examples.md`
- `skills/verifying/SKILL.md`
- `skills/verifying/references/examples.md`
- `skills/writing/SKILL.md`
- `skills/writing/references/examples.md`
- `tests/test_background_filing.py`
- `tests/test_background_usage.py`
- `tests/test_document_reuse.py`
- `tests/test_harness_eval_offline.py`
- `tests/test_idle_work.py`
- `tests/test_performance_backend.py`
- `tests/test_performance_controls.py`
- `tests/test_performance_modes.py`
- `tests/test_timing_integration.py`
- `tests/test_tool_validation.py`
- `tests/test_turn_timing.py`
- `tests/test_turn_timing_ui.py`
- `uv.lock`
