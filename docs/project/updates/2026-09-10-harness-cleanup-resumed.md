# DREAM-035 — Resumed cancellation and cleanup correction

Recorded: `2026-09-10T23:49:18Z`
Work items: `DREAM-035`
Outcome: `verified`
Actor: Codex lead implementation; same independent cleanup judge from the prior iteration.

## Request

The user continued the persistent harness-hardening goal after the prior stopped
checkpoint. The previous [handoff](2026-09-10-harness-hardening-loop.md) and its
three failed verdicts remain intact. This correction returns to the same judge.
Scope/acceptance are in the iteration plan.
No persistent governance rules were changed.

## Changes

Baseline `/tmp/dream035-resumed-cleanup-baseline.json` contains 536 source hashes.
Retained previous accepted work and the pre-existing dirty tree. HTTP Stop now
cancels registered AnyIO operation scopes for pending opens, line reads,
error bodies, POSTs and retry waits. Scope cancellation propagates cancellation through
local request coordination; it does not mark an ambiguous server request idle.
Repeated Stop unregisters the scope before expiry and cannot cancel subsequent
outer response cleanup. Line iteration and closure stay in their owner task.

Reapplied the archived Loop/Engine candidate only as a combined review candidate
with actual HTTP interruption. AutonomousLoop owns worker iteration in one task,
acknowledges each event after logging/delivery, and waits through repeated caller
cancellation for cleanup before releasing its run lock. Engine explicitly closes
its nested iterators, including budget exits and consolidation. This is not
remote cancellation proof or exactly-once execution. Resumed candidate 7 passed the independent lifecycle gate.

## Validation

All new checks are CPU fixtures in `.venv`, with temporary state and model loading
disabled. No live server, credentials, GPU, benchmark, package install or owner
process operation. Graph discovery returned Transport closed; focused reads used.

- Nine new HTTP interruption tests failed against the no-op adapter, then the
  corrected adapter passed 47 interruption/stream/resilience/coordination tests.
- Added retry-wait cases and real HTTPX MockTransport through actual Engine and
  AutonomousLoop. It verifies blocked-read cancellation, same-task cleanup,
  repeated cancellation while cleanup is suspended, run-lock exclusion, no
  replay and uncertainty after stop.
- Lead command: `timeout 30 .venv/bin/python -m pytest -q
  tests/test_http_interrupt.py tests/test_worker_cleanup.py tests/test_loop.py
  tests/test_loop_run_budget.py tests/test_budget.py
  tests/test_task_guidance_integration.py tests/test_turn_timing.py
  tests/test_cli_owned_lifecycle.py tests/test_sdk_review_streaming.py
  tests/test_local_request_coordination.py tests/test_stream_completion.py --tb=short`.
  Observed 107 passed in 6.15s, with 3 existing SDK scoped-read shadow warnings.
  Ran as approved bounded CPU-only work outside the sandbox because prior
  sandbox asyncio/process wakeups repeatedly stalled.
- Same judge received `/tmp/dream035-resumed-cleanup.patch`, all prior criteria,
  the actual HTTP counterexample and new tests. Verdict pending.
- Resumed gate 1 FAIL: a queued read could outrun scheduled cancellation and
  dispatch a tool; natural HTTPX EOF could close inside that timed line read,
  aborting cleanup and releasing the run lock. Both are now regression cases.
  The first EOF test observed too soon; allowing stop propagation exposed the
  failure before correction. An import typo and completed-error lease forwarding
  defect were also found by focused tests and corrected.
- Candidate2 records persistent interruption generations shared by copied
  delegates, bounds a turn through ContextVar state, and checks before/after
  awaits and before event advancement. HTTPX hooks put cancellation around raw
  reads, leaving EOF closure outside. Ready-header ownership, local lease waits,
  Stop before a tool and explicit next turns have additional fixtures.
- Broad retained-phase/lifecycle command ran 299 tests:298 passed and one existing
  CLI process-start fixture timed out before its interrupt action, with 3 SDK
  warnings in 12.32s. The complete CLI module recheck passed 12 tests in 4.01s.
  The startup timeout is not claimed resolved or silently counted as a pass.
  Broader command extends the107-case command above with test_model_adaptation,
  test_capability_integration, test_provider_capabilities, test_compaction,
  test_local_tuning, test_performance_backend, test_performance_modes,
  test_active_settings, test_council_handoff, test_salvage,
  test_backend_resilience, test_harness_backend_quality,
  and test_inference_coordination, using timeout40.
- Additional shared-state compatibility command: `timeout 30 .venv/bin/python
  -m pytest -q tests/test_parallel_subagents.py tests/test_background_filing.py
  tests/test_background_usage.py tests/test_verifier_fork.py tests/test_idle_work.py
  tests/test_round_stats.py tests/test_local_subagents.py --tb=short`.
  Observed 58 passed in 3.77s. No model loads or live provider calls.
- One automatic approval-review request timed out before execution; its one
  allowed retry completed. No safety rejection or external transmission occurred.
- Snapshot tracking passed at checkpoint: 24 dated records, 11 changed paths.
  An earlier check caught CURRENT's timestamp preceding this handoff and was
  corrected. Recheck after subsequent edits. Same-judge candidate 2 pending.

Resumed gate attempt2 FAIL: HTTPcore error cleanup executes inside raw-read
advancement under an AnyIO shield. asyncio timeout cancellation bypassed that
shield, aborting closure and releasing the run lock with a late effect possible.
Both prior probes passed; judge suite113 passed with1 CLI startup timeout, whose
isolated retry passed. New installed-HTTPcore regression reproduced the defect.
Candidate3 replaces operation timeouts with same-task AnyIO CancelScope.cancel,
which honors HTTPcore nested shields; own cancellation is translated after scope
exit. No timeout/config/dependency changes. Lead70 focused tests passed in1.65s.
Same judge receives candidate3; source frozen until verdict.

Read-only intent audit also reproduced carried verifier findings being stored
inside the current user message and labeled The user in filing input. A synthetic
9000-character report blocked an otherwise admissible short request at2048-token
context. No actual model diversion measured. Separate attributed background
messages are a later-phase hypothesis; no intent correction implemented yet.

Resumed gate attempt3 FAIL: a stopped delegate unwound its parent batch, whose
existing fut.cancel() aborted a sibling's shielded HTTPcore cleanup. Gather was
present but completed after aborted cleanup, permitting a late child effect.
All three older probes passed; judge115 passed with3 SDK warnings in6.57s.
The user explicitly authorized continued iterations; lead continues bounded
corrections with the same judge, without resetting or weakening the gate. No
failed candidate is accepted and no new phase starts before PASS.

Candidate4 gives each batch coroutine its own same-task AnyIO cancellation scope,
shields parent awaits against direct child Task.cancel propagation, and cancels
scopes before joining every child. Already-cancelled queued children do not start
tools. New real copied-backend/HTTPcore sibling fixture failed before correction;
93 focused tests passed afterward in4.05s. Two additional queued/semaphore-wait
cases passed with the complete18-case cleanup file in1.45s. Same-judge review
pending; production source frozen. No live/model/GPU/benchmark operation.

Resumed gate attempt4 FAIL: the actual _run_subagent asyncio deadline bypassed
HTTPcore shields during error cleanup. Real _run_subagent/_subagent_loop probe
reproduced incomplete cleanup and late effect. Four older probes pass; judge123
focused cases passed8.00s with3 SDK warnings. New real deadline regression failed
before correction. Candidate5 replaces asyncio.timeout with AnyIO fail_after,
preserving work deadlines while allowing shielded cleanup to settle.

Lead also reproduced natural EOF close abortion under that deadline (even with
fail_after), then shielded owned raw-stream and raw-iterator closure in the same
task. Added real HTTPcore natural-EOF regression and a positive blocked-request
deadline control: timeout still returns incomplete and closes its response.
98 HTTP/delegate/ownership/coordination/stream/resilience cases pass in3.72s.
No live operations. Same-judge candidate5 pending; source frozen. All failures
remain part of this phase's evidence; continuous correction does not reset gate.

Resumed gate attempt5 FAIL: after shielded EOF cleanup outlasted a deadline,
buffered tool calls still dispatched and another request returned success. All
five prior probes and stale-clone/new-turn/backpressure controls passed; judge126
focused tests passed8.48s with3 SDK warnings. Response-cleanup and tool-cleanup
post-deadline regressions both failed before correction.

Candidate6 checks cancellation and the effective deadline before/after HTTP work,
before each subagent tool dispatch and before reporting subagent completion.
It preserves same-task shielded cleanup but cannot resume work afterward. An
additional synchronous-ready cleanup fixture covers expiry before its scheduled
timer callback runs. Lead100 focused tests passed3.79s; with the extra callback
race, the complete24-case cleanup file passed2.10s. Same-judge candidate6 pending;
source frozen. No gate reset or production acceptance, and no live operations.

Resumed gate attempt6 FAIL: Stop during permission_cb could be followed by a
True response and tool execution. Six older probes and recovery controls pass;
judge129 focused tests passed8.66s with3 SDK warnings. Lead reproduced both Stop
and expired-deadline approval dispatch; normal permission approval remained green.

Candidate7 captures/checks interruption generation at _exec_tool entry and checks
that same generation plus work cancellation/deadline after permission, immediately
before handler dispatch. Checks remain outside generic tool-error conversion.
151 HTTP/delegate/ownership/coordination/permission/schema/provenance tests passed
in5.47s. Same judge reviews candidate7; production source frozen. All prior FAILs
preserved. No new phase, external operation or production acceptance.

Resumed attempt7 PASS: same judge verified all seven prior counterexamples,
recovery/backpressure/task-context controls and179 combined tests in10.45s with3
existing SDK warnings. No blocking findings remain within the phase3 contract.
Lead retained-phase regression passed417 tests in14.87s with the same3 SDK
warnings. It includes all modules from the299-case broader command above, all
seven additional shared-state compatibility modules, and test_permission_hardening,
test_tool_validation and test_tool_provenance, with timeout40. No startup timeout
occurred in this final run; prior timeouts remain recorded, not retroactively
resolved. Python compilation passed for all six changed Python files. No owned
fixture task/process remains. Final tracking passed24 dated records and12 changed
source paths against the resumed baseline. [ADR-016](../DECISIONS.md) records the final contract.

## Unfinished work

The combined cleanup correction passed independent review and the broader
retained-phase regression. Phase4 package-asset completeness and
phase5 numeric-environment validation remain planned, not implemented. Live
provider/native UI quality, clean installation, model adaptability effectiveness
and production acceptance remain unqualified. No world-superiority claim.
No external feedback packet was transmitted; the previous approval rejection and
manual Claude/Grok fallback remain unchanged.

## Next steps

Move to phase4 and a fresh packaging/privacy judge. Preserve all prior failure evidence.
Preserve original handoffs and dirty work; do not blanket restore HEAD or replay
uncertain operations. No owner acceptance, publication or commit is recorded.

## Files changed

- `dream/core/backends/openai_compat.py`
- `dream/core/engine.py`
- `dream/core/loop.py`
- `tests/test_http_interrupt.py`
- `tests/test_worker_cleanup.py`
- `tests/test_loop_run_budget.py`
- `docs/durable-runs.md`
- `docs/project/DECISIONS.md`
- `docs/superpowers/plans/2026-09-10-harness-adaptation-loop.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-10-harness-cleanup-resumed.md`
