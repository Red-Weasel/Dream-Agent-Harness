# DREAM-026 — GLM server memory defaults and allocation reporting

Recorded: `2026-09-06T19:54:38.557541-05:00`
Work items: `DREAM-026`
Outcome: `implemented`
Actor: Codex; independent read-only reviewer glm_residency_review.

## Request

The owner authorized fixing the separate MachX engine after the loading-policy
diagnosis. Trace how pinning became disabled; address the 199 GB GLM loading with
54.8 GB system RAM and about 16 GB per GPU. Preserve running workloads and prior
dirty work. Acceptance includes actual residency verification on the next load;
that hardware qualification is not claimed by this record.

## Changes

Git blame/show traced the opt-in host pinning default to engine commit 40c49b92,
August 29, 2026. Its recorded reason was to avoid 200+ GB used RAM. Dream's server
integration inherited the default; the later model recommendations did not change
it. This is source-history evidence, not a new assertion about the owner's intent.

The GLM Engine server path now enables host pinning within a shared live RAM
budget and sizes each GPU expert cache from usable VRAM after weight/context
reserves. Existing manual cache requests are honored or rejected explicitly.
Host budgeting preserves the existing 40 GiB floor, adds staging reserve, accounts
for the 1.37 USM overhead allowance and divides the resulting cap across stages.
Zero caps remain zero. Per-layer live checks remain; allocation failure leaves
banks mapped and reports it. This may still produce PARTIAL host residency.
The GPU cache duplicates selected host weights; cache capacity includes empty and
quarantined slots and must not be added as unique resident model bytes.

Server policy is passed into each model instance without changing global research
runner defaults. Minimum viable cache is checked before weight upload; a stage
with no expert layers avoids division by zero. Environment overrides are finite,
bounded and visible in metadata. Per-layer progress prevents silent long loads.
Actual host pinned/mapped bank bytes and GPU cache capacity are exposed in /props.
Dream displays the loading policy before launch and actual allocation totals after
readiness. Old/malformed backend reports show unavailable status, not full residency.

Engine paths changed outside Dream, with original source copies under
`/tmp/machx-residency-fix/before` and reviewed copies under `stage`:

- include/ie/glm5_memory_policy.hpp (new)
- include/ie/glm5next.hpp
- include/ie/engine.hpp
- src/model/glm5next.cpp
- src/engine/engine.cpp
- src/server/capabilities.cpp
- src/server/openai_server.cpp
- tests/unit/glm5_memory_policy_test.cpp (new)
- tests/unit/server_capabilities_test.cpp
- tests/CMakeLists.txt
- SERVER_CONTROLS.md
- MASTER_DEV_PLAN.md

Edits were applied with content-hash checks against concurrent changes. The
installed engine executable was backed up and its copy verified before building.
Provider-native setups and the earlier staged shared-setup rebuild were untouched.

## Validation

Observed red/green: new pure policy test initially could not compile without its
implementation; final strict host compilation and execution passed. Eight Dream
residency tests first failed for missing functionality and then passed. Additional
malformed-counter regressions passed after review.

Engine build succeeded with two CPU jobs via the existing oneAPI environment.
Five host-only CTest targets passed in 0.07 seconds: glm5_memory_policy_test,
glm5_server_test, server_capabilities_test, serve_options_test and
engine_options_validation_test. Existing compiler warnings remain, including
missing ChatTurn field initializers; this is not a warning-free build claim.
The rebuilt executable read the actual GLM metadata and reported host_banks
pinned_auto, expert_cache_bytes 0 (auto), host_floor_gib 40, no explicit pin cap,
and pin_overhead 1.37. No allocator/model load is used by that capability command.

Final Dream focused regressions: 120 passed in 0.93 seconds, using the existing
venv, disabled plugin autoload, explicit pytest_asyncio, no bytecode/cache writes.
Covered model residency, presets, local tuning, sampling, backend resilience and
compaction. Model-free async tests ran outside the sandbox because its asyncio
worker execution was previously unreliable. No packages were installed.

Independent review checked staged-vs-original engine changes, pin/CPU-miss layout,
stage ownership, arithmetic boundaries, and Dream status handling. No blocking
issues remained. Lead confirmed all 12 engine source/doc paths exactly matched
the reviewed staged versions after build. Evidence under /tmp/machx-residency-fix:
`build.log`, `ctest.log`, `dream-tests.log`, `glm-capabilities.json`.

Installed executable SHA256:
59abad27689077e98c4b4ab15fb497ccc45d2c364feda47a04befa930877c85e

No model was loaded, restarted, stopped or sent an inference request. No actual
post-change RAM/VRAM, output-quality or throughput measurement was performed.
The full Dream suite, GPU tests and owner acceptance were not performed.
The first tracking check rejected a UTC-date/filename mismatch after UTC midnight; the record was corrected to the local September 6 date. Final tracking passed: 13 dated records and all nine session-changed Dream paths covered.

## Unfinished work

Implementation and model-free verification are complete. The original hardware
symptom remains unverified until a fresh GLM load. A policy budget is not proof of
full residency or better throughput. No worker or build process remains active.

Recovery: original engine executable is `/tmp/machx-residency-fix/ie.before`,
SHA256 449c5466e7e61e627ad21cc9c73773b6839f2da9c2f0929c29b65dedab076b47.
Original source files are in `before`; hashes in `hashes.json`. Compare current
files against `stage` before restoring to preserve later work. Restore only this
change's files and the executable if needed; do not revert the dirty repository
or touch provider configurations. No running model needs recovery from this task.
These temporary recovery files should be retained until owner validation.

## Next steps

Restart Dream when convenient and load the same GLM model. The new binary applies
on its next invocation; existing processes do not hot-reconfigure. Perform fresh
GPU/process/RAM/temperature preflight before any agent-driven model load and obtain
applicable authorization for stopping an existing process. Capture the displayed
allocation report, verify partial/full host residency, and run a bounded correctness
and performance check. Keep DREAM-026 implemented until hardware evidence and
owner feedback resolve the original complaint. No automatic reload is queued.

## Files changed

- `dream/local/machx.py`
- `dream/local/model_defaults.py`
- `dream/local/launcher.py`
- `tests/test_model_residency.py`
- `docs/runtime-controls.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/HISTORY.md`
- `docs/project/updates/2026-09-06-glm-memory-fix.md`
