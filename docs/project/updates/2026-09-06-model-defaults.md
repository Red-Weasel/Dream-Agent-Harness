# DREAM-026 — Model recommendations and saved launch settings

Recorded: `2026-09-06T17:36:12.870384+00:00`
Work items: `DREAM-026`
Outcome: `verified`
Actor: Codex; model_defaults_review performed independent read-only review.

## Request

The owner reported incorrect GLM loading defaults and approved deriving recommendations from model metadata, publisher guidance, engine capabilities and hardware, while remembering explicit choices. Acceptance: a sourced summary with Enter to load, Advanced/reset, exact-model persistence, validated launch/request propagation, no model restart during development. This is separate from the earlier harness-only shared-setup stage; that stage remains unactivated.

## Changes

All supported MachX defaults now reach the UI, including GLM max output 16384 and prefill 256. Previously only thinking/effort followed reported values while max output was forced to 4096. The bounded GGUF reader inspects scalar metadata and skips tokenizer arrays without reading tensors. Known GLM-5.3-Flash metadata selects a documented coding starting profile (temperature/top-p 1/1), with publisher provenance; other controls keep backend values. Unknown models use labelled fallbacks. Discovery does not fetch model cards or execute templates.

Context starts conservatively at 32768, reduces for small detected hosts and respects the model metadata limit. It is not an optimal-fit solver. GPU count comes from existing discrete-device telemetry and backend limits. The default summary labels each source. Advanced can retain/change GPUs/context and tune/reset individual controls. Current physical/backend limits and supported reasoning controls are revalidated. Live preflight and engine allocation checks remain in force.

Successful loads save private mode-0600 exact-model presets using canonical path and all shard sizes/mtimes. Atomic replacement and revision checks preserve concurrent edits. Identity captured before selection is checked again before launch and saving, so a changed file is not given another model's preset. Cancelled/failed attempts do not save; reset is provisional until successful loading. Corrupt files are left intact and reported. Presets are scoped to new loads; attaching to an existing server does not change it. No live preset file was created during development.

## Validation

Observed: 127 focused tests passed after review fixes. Command used the existing `.venv/bin/python -B`, `PYTHONDONTWRITEBYTECODE=1`, disabled unrelated pytest plugin autoload, enabled `pytest_asyncio.plugin`, and tested test_model_presets, test_local_tuning, test_sampling, test_effort, test_backend_resilience, test_compaction. Evidence: `/tmp/dream-defaults-final-tests.txt`. No dependency synchronization/install was run. Initial tests failed on ignored defaults/missing preset/picker functionality; metadata spelling, literal glob shard identity, related speculative defaults, normalization and changed-model identity regressions were also observed failing before fixes.

Sandbox execution stalled even on a minimal asyncio thread example; only the owned test process was interrupted. Model-free tests then ran with approved local IPC access. Independent review found three issues (cross-field default validation, discarded preset normalization and identity drift); all received regression tests and fixes. No native provider run or inference request was used.

Actual read-only GLM capability probe: architecture glm5next, output 16384, prefill 256, thinking on/effort max, max GPUs 2. The actual GGUF reports name `GLM 5.3 Flash` and training context 1048576. Recommendations on this host: 2 GPUs, context 32768, output 16384, temperature/top-p 1/1. Evidence: `/tmp/dream-glm-default-capabilities.json`, `/tmp/dream-glm-recommendation.json`. No tensors or model/GPU allocation were loaded for these probes. Prior context-200000 results remain historical evidence, not a new benchmark or an automatically seeded preference.

Observed final tracking check passed: 11 dated records, all 11 session-changed paths covered. Reviewer follow-up confirmed all three fixes, with four targeted regressions passing and no additional important findings.

## Unfinished work

No model launch, manual owner acceptance, full regression suite, non-POSIX preset-write qualification, throughput/quality benchmark or maximum-fit computation was performed. Publisher profiles are versioned code and currently recognize only the positively identified GLM-5.3-Flash; other models use metadata limits/backend defaults. Intel telemetry is the existing host probe; unrecognized GPU environments retain engine auto and preflight. This does not activate the shared-setup staging package stored under `~/Dream-rebuilds/harness-20260906T161238Z`; that package's audit and restore-test incident remain recorded there.

## Next steps

Restart Dream when the owner is ready, select GLM and inspect the sourced summary. Enter loads after normal preflight; Advanced can choose 200000 context. After a successful load, the next selection of the same exact model restores those settings. Keep existing model/Claude processes undisturbed until the owner chooses to restart them. Review remaining capacity/quality qualification under DREAM-015 separately. No commit, push or release was performed.

## Files changed

- `dream/local/settings.py`
- `dream/local/launcher.py`
- `dream/local/model_defaults.py`
- `dream/local/model_presets.py`
- `tests/test_model_presets.py`
- `tests/test_local_tuning.py`
- `docs/runtime-controls.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/HISTORY.md`
- `docs/project/updates/2026-09-06-model-defaults.md`
