# DREAM-030 — Respect MachX streaming memory during desktop loading

Recorded: `2026-09-09T20:48:59-05:00`
Work items: `DREAM-030`
Outcome: `verified`
Actor: Codex, no delegated contributors.

## Request

The owner showed DeepSeek loading refused because 153.4 GiB of weights exceeded
63.7 GiB free VRAM, despite successful RAM/GPU split loading in the terminal after
answering yes. Preserve that supported engine behavior in graphical startup.

## Changes

Confirmed root cause: MachX capabilities already reports memory_planner=streaming
for the actual deepseek4 GGUF. Dream's model_settings dropped that field, and
launch_preflight recognized only GLM's host-bank policy. Desktop therefore raised
the same warning that the terminal allows the owner to acknowledge.

Desktop now preserves the fresh engine memory planner and uses it to recognize
streamed expert weights. Host RAM and per-card GPU headroom checks remain; the
engine validates exact weights/context/cache allocation during startup. The native
picker displays the RAM/GPU explanation. Missing or resident planner values do not
gain a blanket bypass based on model name; older explicit GLM host-bank metadata
remains supported. Unknown host RAM is refused explicitly. Existing resource,
server-ownership and load-lock checks are unchanged. No persistent force preference
or new confirmation is needed for this supported streaming model. The shared
terminal preflight behavior was not changed. See desktop guide.

## Validation

- Before fixing, four new regressions failed: three streaming architectures hit
  full-weight VRAM refusal, and model_settings lacked memory_planner.
- Final `.venv/bin/python -m pytest -q tests/test_desktop_startup.py
  tests/test_local_preflight.py tests/test_model_residency.py
  tests/test_model_presets.py tests/test_local_tuning.py`: **125 passed**. Fixture
  coverage includes occupied/hot/unknown GPUs, existing server, insufficient RAM
  and VRAM, model metadata propagation, picker note and legacy GLM compatibility.
- `/usr/bin/python3 tests/desktop_startup_native.py`: passed native selection,
  workspace, advanced values, invalid input, recommendations and busy recovery;
  fixture launch interception, no model/session started.
- Actual model metadata read and resource preflight passed without loading weights:
  deepseek4, streaming planner, 228.7 GiB available host RAM, 63.7 GiB free VRAM
  across two cards. Evidence `/tmp/dream-loading-20260909-preflight.json`.
  Read-only paired-engine capability source confirms streaming is advertised for
  DeepSeek4, Qwen4 experimental and GLM5Next. No engine files were modified.
- Python compilation passed. Full suite was not rerun for this narrow fix; the
  earlier DREAM-029 full-suite result remains historical, not this fix's result.
- Graph transport returned closed; focused source reads used. An initial uv command
  failed on read-only cache access, then the installed interpreter worked. An
  initial targeted command named nonexistent test_preflight.py and ran no tests;
  corrected to existing test_local_preflight.py. No dependency installation.
- Final tracking passed against the session snapshot: 17 dated records, all six
  changed source paths covered. git diff --check passed.

## Unfinished work

No actual model load or inference was started. The screenshot's 400,000-token
context and 100,000-token output settings have not been allocation/performance
qualified. The engine retains that validation. Passing this preflight establishes
that the incorrect weight-residency refusal is removed, not that every context
fits. No GPU process was stopped, no settings saved, no commit or release made.

Existing dirty work preserved. Snapshot:
`/tmp/dream-loading-20260909-baseline.json`, 438 hashes. No owned processes remain.

## Next steps

DREAM-030: owner can retry Load model and start. Reopening Dream refreshes the
picker's explanation. If the engine itself rejects allocation, inspect its actual
allocation error before changing context or cache settings. DREAM-029 real-model
quality and DREAM-028 responsiveness remain separate qualification work.

## Files changed

- `docs/desktop.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-09-streaming-load.md`
- `dream/desktop/startup.py`
- `tests/test_desktop_startup.py`
