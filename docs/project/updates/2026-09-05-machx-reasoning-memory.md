# DREAM-026 — Model-specific reasoning and GLM memory accounting

Recorded: `2026-09-05T17:03:20-05:00`
Work items: `DREAM-026`
Outcome: `verified`
Actor: Codex, with dream_reasoning implementation and reasoning_review read-only review workers.

## Request

The owner reported the missing reasoning-effort selector and questioned whether
the 199 GB GLM model was loaded, given roughly 17/16 GB GPU usage and 57.9 GB
headline system RAM. This continues [DREAM-026](../MASTER_PLAN.md#dream-026-acceptance)
and corrects the gap left by the [previous implementation](2026-09-05-machx-model-tuning.md).

## Changes

The previous GLM helper hardcoded Reasoning Effort: Max. The paired MachX engine
now propagates validated effort from CLI/defaults and HTTP requests to GLM,
DeepSeek, GPT-OSS and recognized Qwen effort templates. Unsupported combinations
fail before model allocation or HTTP streaming. The capability report gives
model-specific choices and reasoning/thinking defaults, including applicable
environment overrides. GLM accepts low/high/max, as its actual GGUF and the
[official model card](https://huggingface.co/zai-org/GLM-5.3-Flash) specify.

Dream now displays those effort choices and explicit thinking on/off defaults.
GLM's off option is labelled as a local empty-closed-think prompt convention;
the vendor template always opens thinking. Non-reasoning templates and GPT-OSS
no longer receive an ineffective on/off control. Selection goes unchanged to
server args and HTTP, including max, while the cloud provider's existing effort
mapping remains intact. Session effort survives default initialization and does
not follow a model switch. Review found and fixed med-to-medium normalization
for the existing generic /effort command; the model-specific loading editor
exposes the complete supported level set. See runtime controls.

No memory-policy change was needed. Read-only inspection of the owner's live
PID 1723092 found all six Q4_K_XL shards mapped, totaling 199,707,321,347 bytes.
A mincore check without reading or faulting in weights found 191,230,578,688
bytes of those files already resident in Linux cache. Linux's available-memory
accounting includes reclaimable file cache, so headline used RAM can be much
smaller. Approximately 10 GiB of expert cache per GPU plus fixed weights,
context and workspaces explains the GPU allocations. No smaller model was
substituted, and no pinning, cache flushing or forced weight upload was done.

## Validation

Evidence is in the paired engine checkout at
`results/machx-reasoning-20260905/`. Snapshot before edits:
`/tmp/dream-reasoning-20260905-baseline.json`. Existing dirty changes were preserved.

- **Observed:** regression tests failed before implementation for the missing
  capability field, model effort controls and raw MachX max. The added med alias
  regression also failed before correction.
- **Observed:** C++/SYCL engine build passed, including the final capability-default
  adjustment. Five host CTest targets passed: openai_proto, server_capabilities,
  glm5_server, serve_options and server_controls. Tests cover model-specific
  choices, non-reasoning templates, environment defaults, request overrides and
  validation. Evidence: `build-final.log`, `host-tests-final.log`.
- **Observed:** GLM low/high/max prompts matched an independent render of the
  actual embedded GGUF Jinja template byte for byte. Evidence: `template-oracle.json`.
  Initial oracle harness attempts lacked Jinja in Dream's venv and then lacked
  the loopcontrols extension; system Jinja with that extension passed without
  installing a dependency.
- **Observed:** actual rebuilt engine capability output fed Dream's editor,
  which rejected medium for GLM and selected low plus thinking off correctly.
  The resulting command arguments retained both values. Evidence:
  `actual-editor.json`, `actual-editor.txt`.
- **Observed:** `ie serve <GLM shard 1> --reasoning-effort medium` returned code 2
  before GPU initialization. Evidence: `invalid-model-effort.json`.
- **Observed:** `uv run --locked pytest -q`: **1816 passed, 35 skipped,
  7 warnings in 178.65s**. This ran before the final med alias regression was
  added. After that review fix, the five-file focused command
  `uv run --locked pytest -q tests/test_local_tuning.py tests/test_sampling.py tests/test_effort.py tests/test_backend_resilience.py tests/test_compaction.py`
  passed **108 tests in 0.53s**. Evidence: `dream-full-suite.log`, `dream-focused-final.log`.
- **Observed:** review checked effort consumers, unsupported-value admission,
  prompt lifetimes and immutable metadata access. Fixed both the med alias and
  capability/environment default mismatch. Engine and scoped Dream diff checks passed.
- **Observed:** the live server reported the six-part GLM model and context
  200000. Its maps covered all six files. Shard file-cache measurement is saved
  as `shard-residency.json`. These are point-in-time observations, not a guarantee
  that the OS will retain all pages under later memory pressure.
- **Observed:** final snapshot tracking passed with 9 dated records and 9 changed source paths covered.
- **Unavailable for lead:** code graph transport closed; targeted reads followed
  failed graph calls. Worker graph access worked for focused discovery/review.

## Unfinished work

None within this follow-up's implementation scope. The owner's current server
was left running with the previous loaded binary. Restart Dream and reload the
model to activate the rebuilt effort handling. No new model loads or generated
requests were run in this follow-up. Prompt parity and propagation were tested;
comparative reasoning quality and full-depth 200000-token performance were not.
The generic /effort menu retains its existing ladder; use the loading selector
for GLM low and the complete model-matched set. No commit or release was made.

## Next steps

Restart Dream, reload GLM, and select thinking plus low/high/max in Model tuning.
The selected default applies to each request unless explicitly overridden.
Continue broader model/performance qualification under DREAM-015 with fresh
GPU preflight; memory display alone should not be used as a shard-load check.

## Files changed

- `dream/local/settings.py`
- `dream/local/launcher.py`
- `dream/core/backends/openai_compat.py`
- `tests/test_local_tuning.py`
- `tests/test_sampling.py`
- `docs/runtime-controls.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/CURRENT.md`
- `docs/project/updates/2026-09-05-machx-reasoning-memory.md`

Paired engine changes and its memory explanation are documented in that
checkout's `SERVER_CONTROLS.md`; they are outside this repository's snapshot check.
