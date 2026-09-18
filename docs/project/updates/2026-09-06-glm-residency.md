# DREAM-026 — GLM loading-policy diagnosis

Recorded: `2026-09-06T23:26:53.787078+00:00`
Work items: `DREAM-026`
Outcome: `active`
Actor: Codex; read-only engine/process investigation and Dream tracking correction.

## Request

Investigate the owner's observed 54.8 GB system RAM and about 16 GB per GPU after
loading the 199 GB GLM. Accept those readings as reported. The prior settings
change did not address residency. Acceptance requires a resource-checked loading
policy and observed allocation evidence; model mapping alone is insufficient.

## Changes

Reopened DREAM-026 for loading residency. Corrected current guidance without
rewriting previous dated handoffs. No production code, provider setup, engine
binary, model allocation or runtime configuration changed.

## Validation

Read the live engine process 303465 command line: six-shard GLM Q4_K_XL selection,
2 GPUs, context 32768, temperature/top-p 1.0, thinking on and effort max.
All six shards appeared in its maps. VmRSS was 9,728,848 kB and VmLck zero at the
sample; these are process metrics, not a replacement for the owner's system RAM
reading or a measurement of all file-cache residency. No IE_G5_* override appeared
in its environment. The latest matching Dream startup log records 176.82 GiB mmap
expert banks, a 10.00 GiB host staging/load reserve, GPU expert caches 10.00/9.99 GiB,
and 34/31 cache slots per layer. No new generation request was sent.

Engine source inspected at `~/machx-inference-engine`:
- `src/engine/engine.cpp:816`: fixed 10240 MiB expert-cache default; host admission
  checks staging reserve rather than all expert weights.
- `src/model/glm5next.cpp:805`: host banks remain on mmap by default;
  IE_G5_PIN_BANKS=1 opts into host USM copies.
- `src/model/glm5next.cpp:822`: optional pinning has a 40 GiB free-memory floor,
  per-stage cap, and 1.37 overhead estimate; insufficient room or allocation
  failure leaves individual layers on mmap. Enabling it does not guarantee full
  residency. Those comments describe past observations, not a new benchmark.
- `src/model/glm5next.cpp:1917`: 10 GiB cache default is also in runtime allocation.
- `src/model/glm5next.cpp:4520`: warm_banks skips unpinned banks.

Graph search returned no indexed source matches for the relevant engine literals
and focused GLM/GGUF symbols; a broader GLM query returned generated compiler
artifacts. Focused source reads/rg were used after those insufficient results.
The initial broad host inspection encountered an automatic approval-review timeout
before execution. Narrow read-only process checks subsequently succeeded.
No model test, throughput benchmark, new file-cache residency sweep, restart,
unload or inference was performed. Tracking initially rejected the Current work field format and an older current-state timestamp; both were corrected before rerunning the snapshot check.

## Unfinished work

The loading issue is diagnosed but not fixed. No implementation worker is running.
The user's earlier rebuild restriction limited modifications to Dream's homegrown
harness; the necessary allocator policy lives in the separate engine checkout.
Do not silently change that external engine or stop the running model.

## Next steps

Confirm extending this fix to the separate MachX engine checkout, then implement
and test a coordinated GPU/host residency plan with explicit streaming fallback
status. Preserve OS/other-process headroom. Dream should display and persist the
supported policy through the same backend contract as its other load controls.
Do not promise full residency merely from flipping the existing pin flag or using
more memory. Stage and run model-free checks first; an authorized reload with fresh
resource preflight is necessary to verify actual residency and performance.
Recovery: no runtime changes to reverse; preserve the existing process and dirty
source tree. Resume from this record and take a fresh tracking snapshot.

## Files changed

- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/HISTORY.md`
- `docs/project/updates/2026-09-06-glm-residency.md`
