# DREAM-026 — MachX GLM server and advanced model tuning

Recorded: `2026-09-05T16:07:54-05:00`
Work items: `DREAM-026`
Outcome: `verified`
Actor: Codex, with supervised glm_backend, server_controls and dream_controls workers.

## Request

The owner authorized fixing MachX loading GLM-5.3-Flash UD-Q4_K_XL and adding
advanced controls after Model → GPUs → Context. The target is two Arc Pro B70s
and 200000 context. Scope follows [DREAM-026 acceptance](../MASTER_PLAN.md#dream-026-acceptance).

## Changes

The paired engine checkout now dispatches `glm5next` to its existing host-streaming
runtime, budgets resident/context/cache memory per physical GPU, and avoids
counting Level Zero/OpenCL aliases as additional cards. It preserves reasoning,
tool-call metadata and special chat tokens. Runtime failures propagate as errors.
The original failure was a missing server backend plus an inappropriate
all-weights VRAM gate, not proof that streaming experts must fit entirely in VRAM.

Dream probes model capabilities before loading. Its advanced editor exposes
supported sampling, stops, CPU threads, prefill, concurrency, thinking, cache and
overflow controls. Validation rejects malformed or unsupported choices; selected
values reach both server startup and HTTP requests. Stops and paths are safely
quoted. Streaming errors end a turn as failed without committing partial replies
or executing pending tools. See runtime controls
and [ADR-008](../DECISIONS.md#adr-008--model-capabilities-control-local-tuning).

The shared fix applies by architecture, not filename. Unsupported architectures,
tensor formats and resource requirements still need separate implementation or
qualification. No existing unrelated kernel, media or harness changes were reverted.
The source baseline was `/tmp/dream-machx-20260905-baseline.json`.

## Validation

Observed on Ubuntu 24.04, Python 3.12 and the local oneAPI 2026.1 source build.
Evidence is local under the paired engine's `results/machx-server-20260905/`.
No paid providers, private conversation replay or actual model tool execution
was used. All model runs followed live RAM/VRAM/temperature checks and the
existing single-flight runner with a 220G memory cap and bounded timeout.

- **Observed:** `uv run --locked pytest -q` repeated full run: **1804 passed,
  35 skipped, 7 warnings in 180.10s**, `dream-full-suite-rerun.log`.
- **Failed initially:** full suite had one CancelledError in the pre-existing
  `test_a_browser_that_disconnects_unsubscribes_cleanly`. The unchanged GUI file
  matched the session baseline; an isolated 12-run check reproduced it once.
  Its file passed 22 tests and the repeated full suite passed. No speculative
  unrelated GUI fix was made. Evidence: `dream-full-suite.log` and
  `/tmp/dream-disconnect-repro.log`.
- **Observed:** focused `.venv/bin/python -m pytest -q tests/test_local_tuning.py`: **38 passed in 0.26s**. Actual
  rebuilt capability probing and a scripted Rich editor session passed, including
  sampling and escaped stops. Evidence: `dream-editor.txt`.
- **Observed:** source engine build passed; nine host CTest targets passed for
  request parsing/defaults, capabilities, GLM template/tools, context validation,
  stops and memory guards. Physical GPU counting and numerical sampling-penalty
  GPU tests passed. Logs: `host-tests-final.log`, `gpu-tests-final.log`.
- **Failed initially, then passed:** oneAPI compilation had a frontend crash on
  `openai_server.cpp`; retry with the same flags at two jobs built successfully.
  Earlier incremental build also ran before the new API was complete and failed;
  final builds passed. No compiler-bug fix is claimed.
- **Observed:** live context-8192 HTTP requests passed repeated deterministic
  greeting, SSE equivalence, stop strings, reasoning, synthetic tool call/result
  round trip, invalid temperature, oversized input and streamed runtime errors.
  Evidence: `live-small.json`, `live-extra.json`, `glm-small-server.log`.
- **Observed:** the GLM prompt helper matched the actual GGUF Jinja template on
  reasoning, Unicode and reordered/ambiguous tool-result fixtures. A standalone
  greeting matched through the next role marker after aligning special-marker
  tokenization in a temporary reference runner with `IE_G5_CPU_MISS=0`.
  The first unmodified plain-text runner comparison was invalid for chat parity
  and is not counted as an agreement. Evidence: `glm-aligned-reference.log`.
- **Observed at 16:04 local time:** Dream's actual `machx.serve` and `wait_ready`
  loaded the six-part GLM Q4_K_XL on both B70s at context **200000**. `/props`
  reported 200000; two requests returned `Hello!`. This used normal CPU/GPU
  expert execution with eight CPU threads. The test used isolated temporary
  Dream state and port 11439; selected startup defaults applied to requests
  that omitted them. Clean shutdown returned exit 0. Evidence:
  `live-dream-200k.json`, `glm-dream-200k-server.log`.
- **Observed:** after model shutdown, the cards returned to approximately
  26/34 MiB allocated, with no engine process left. Final checks own no model,
  process or lock. Evidence: `gpu-preflight-final.json`.
- **Observed:** `python3 scripts/check_project_tracking.py check --snapshot /tmp/dream-machx-20260905-baseline.json` passed: 8 dated records, 10 changed source paths covered. Engine and scoped Dream `git diff --check` also passed.
- **Unavailable:** indexed code graph transport closed during follow-up discovery;
  focused source reads were used after graph attempts failed.

## Unfinished work

None within the implemented loading/editor scope. Full-depth 200000-token
prompt quality and throughput, broad model/quantization compatibility and native
manual desktop acceptance were **not run**. GLM server prefix reuse, MTP,
INT8 KV, vision and deferred tool loading remain unavailable. Knobs only appear
where supported, and selections last for the launch/session rather than saved
presets. No commit, release, owner acceptance or comparative speedup is claimed.
The initial unrelated browser teardown flake remains outside this scope.

## Next steps

Restart `dream` or `dream desktop`; select GLM, 2 GPUs, 200000 context, then edit
Model tuning or press Enter to start. For later performance/quality expansion,
use DREAM-015 with fresh resource preflight and a stated workload. See the
paired engine's `SERVER_CONTROLS.md` for its CLI and architecture boundaries.

## Files changed

- `dream/local/settings.py`
- `dream/local/launcher.py`
- `dream/local/machx.py`
- `dream/core/backends/openai_compat.py`
- `tests/test_local_tuning.py`
- `docs/runtime-controls.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/CURRENT.md`
- `docs/project/DECISIONS.md`
- `docs/project/updates/2026-09-05-machx-model-tuning.md`

Paired engine changes are recorded in that checkout's `SERVER_CONTROLS.md` and
local implementation plan. They are outside this repository's snapshot check.
