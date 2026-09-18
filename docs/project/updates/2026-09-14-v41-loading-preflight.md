# DREAM-077 — V4.1 loading preflight

Recorded: `2026-09-14T19:03:35.851712-05:00`
Work items: `DREAM-077`
Outcome: `implemented`
Actor: Codex.

## Request

Diagnose and repair why DeepSeek V4.1 will not load in Dream. Owner supplied
Claude's independent engine-side observations. Preserve earlier discovery work;
verify the actual Dream startup boundary. Baseline:
`/tmp/dream-v41-loading-20260914.json`, 790 source hashes.

## Changes

The owner's startup-status record at `/tmp/dream-desktop-490jnv_k/startup-status.json`
contained the exact refusal: "MachX streams experts from host RAM, but available
RAM is below the weight size plus headroom. Free memory before loading."
Dream required its entire indexed 510.3 GB checkpoint plus headroom in RAM.

Audited installed engine `Ds41Forward::init_resident` and `Engine::ds41_load`:
V4.1 has GPU slots, dynamically capped pinned RAM and mmap/disk-backed experts.
It leaves40 GiB of host RAM available, sizes expert slots after allocating dense
weights/context, enumerates all visible Arc cards, and serves one request at a
time. This differs from the older GGUF streaming assumption.

Dream now gates the disk-backed preflight by the exact `deepseek_v41` architecture
and engine-reported streaming planner. It checks more than40 GiB host RAM and the
existing8 GiB per-card free-VRAM floor; busy/hot/unknown GPUs and existing servers
still refuse. All visible devices must be checked instead of selecting fewer
cards than the engine uses. Other architectures retain the prior weight checks.
CLI V4.1 loads use that same guard without a force override. Native/CLI option
validation restricts parallel to1, including saved selections. Operator notes
explain the actual residency behavior. No engine source or defaults were changed.

Graph discovery located Dream's preflight. Engine graph search's automatic
permission review timed out; focused read-only engine source/docs supplied the
missing evidence. No rejection or external mutation resulted.

## Validation

- Initial model-free regressions reproduced four failures: RAM rejection,
  incomplete GPU topology check, unrestricted parallel control and accepted
  multiple-slot native selection.13 existing guard cases passed.
- Final combined CPU command: `.venv/bin/pytest -q tests/test_v41_loading.py
  tests/test_directory_models.py tests/test_models.py tests/test_model_scan_bounds.py
  tests/test_model_presets.py tests/test_desktop_startup.py tests/test_local_preflight.py
  tests/test_local_tuning.py tests/test_model_residency.py`:186 passed in6.12 seconds.
- Actual preflight: two idle Arc Pro B70 cards, about31.9 GiB free each,
  temperatures43–46 C and235 GiB available host RAM, no inference server.
- Real `startup.run()` with temporary session/preset data at port18135 loaded
  V4.1 on two cards, ctx32768, parallel1. Log: resident in52 seconds; `/props`
  reported load_s51, expected architecture and placement. `/health` returned ok.
  `App.run` was replaced by an HTTP probe, so this is actual startup/engine
  qualification, not a complete native conversation test.
- The separate 16-token nonthinking HTTP reply timed out after120 seconds.
  `/health` still responded with one in-flight request. `/admin/shutdown` returned
  200 but the process did not exit; Dream's existing owned-child cleanup sent
  SIGTERM then SIGKILL, resulting exit-9. Only test PID1024460 was stopped.
  All186 tests pass independently of this live failure; no reply success claimed.
- First live evidence: `/tmp/dream-v41-live-check/check.py`, `report.json`,
  `startup-status.json` and `var/logs/machx.log` in that directory. Post-cleanup
  telemetry: both cards idle with no processes and about26 MiB VRAM used each.
- Comparison at4096 context, otherwise the same Dream path and request, failed
  during loading after35.96 seconds. Engine log: `terminate called after throwing
  an instance of 'sycl::_V1::exception'`, `level_zero backend failed with error: 39
  (UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY)`. Owned process exited-6. Evidence:
  `/tmp/dream-v41-live-4096/check.py`, `report.json`, `var/logs/machx.log`.
  This is not evidence that smaller context caused the error; allocation failure's
  cause remains unestablished. No further model loads were attempted.
- Final GPU cleanup verified: both Arc cards idle, no processes, about26 MiB
  VRAM used each, temperatures46–47 C.
- Initial tracking check caught this draft's UTC date crossing midnight against
  its local-date filename. Timestamp converted to explicit Central offset; no
  previously delivered handoff changed. Final tracking passed:90 dated records,
  eight changed paths, using the session snapshot. `git diff --check` passed.

## Unfinished work

The original loading refusal is repaired and one actual load completed. The
subsequent reply timeout and second-load allocation failure remain unresolved.
The complete live loading-and-chat acceptance has not passed. The supplied Claude
performance results were not independently reproduced. Installed engine source
also explicitly refuses tool definitions and image input for this architecture;
this loading change does not implement those engine features or establish Dream
agent/tool compatibility. No owner acceptance or production claim.

## Next steps

Engine follow-up: compare the two preserved scripts/settings and engine logs
against Claude's direct-server result. Diagnose the first generation stall and
allocation failure before another load; do not attribute them to context or
hardware without evidence. Both temporary test processes exited.
Refresh the startup catalog/settings before the owner's next launch. Native panel
changes load at the normal application restart. No owner session was restarted.

## Files changed

- `dream/desktop/startup.py`
- `dream/local/settings.py`
- `dream/local/launcher.py`
- `tests/test_v41_loading.py`
- `docs/desktop.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-14-v41-loading-preflight.md`
