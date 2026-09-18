# DREAM-035 — Model adaptation and failure-boundary hardening

Recorded: `2026-09-10T22:49:49Z`
Work items: `DREAM-035`
Outcome: `blocked`
Actor: Codex lead (implementation); fresh Codex research, reliability and delivery auditors; independent adaptation, stream and cleanup gate agents (read/verify only).

## Request

The owner requested continuous researched, tested, independently judged harness
improvement toward production quality and model adaptation, with varied fresh
reviewers, external-model feedback or a review file, and benchmarks deferred.
Acceptance is in [master plan](../MASTER_PLAN.md) and the
iteration contract.
Perfection or worldwide superiority cannot be established by these fixtures.

## Changes

Preserved the pre-existing dirty tree, including DREAM-034 Council controls.
Baseline: `/tmp/dream-harness-loop-20260910-baseline.json` (531 content hashes).
Phase1 binds actual HTTP admission/output to known model context, invalidates
old-model settings on model changes, preserves same-model settings and explicit
effort, and protects the current request during salvage without mutating history.
Phase2 rejects incomplete or contradictory response streams before dispatching
collected tool calls. Choice zero controls completion; non-success finishes skip
actions and optional passes. Local request uncertainty remains fenced.
Phase3 was rejected at all three gate attempts. Its final candidate fixed the
cleanup races but lost cancellation of stalled HTTP responses. It was withdrawn:
Engine, Loop, the budget fixture and operator behavior docs were restored; the
new cleanup tests were removed from the active suite. Phase1/2 remain applied.
The rejected source/test patch is retained for review at
`artifacts/harness-validation/dream035-phase3-rejected.patch`; it is NOT accepted
implementation. `git apply --check` confirms the archived patch applies to this
checkpoint without applying it. Exact source copies and judge repros also remain
in `/tmp/dream035-phase3-rejected/`.
Research and the external feedback fallback are in review packet.

## Validation

All runs below used `.venv/bin/python -m pytest -q`, disposable state and the
existing model-loading opt-in guard. No `--with-models`, live provider or GPU work.
Checks took place on 2026-09-10; exact gate history/contracts are in the plan.

- Phase1 lead: 155 passed in 2.98s across test_model_adaptation,
  test_capability_integration, test_provider_capabilities, test_compaction,
  test_local_tuning, test_performance_backend, test_performance_modes,
  test_active_settings, test_council_handoff and test_salvage. Initial five
  regressions failed; gate1 found same-model performance reset and salvage
  request loss/history mutation. Three added failures reproduced them. Same
  gate attempt2 PASS: 70 focused tests plus 43 independent adversarial checks.
- Phase2 lead: 79 passed in 0.84s across test_stream_completion,
  test_backend_resilience, test_harness_backend_quality,
  test_local_request_coordination, test_salvage, test_model_adaptation and
  test_inference_coordination. Eight initial failures; gate1 found secondary
  choice completion and overwritten finish/post-terminal generation bypasses.
  Six added failing tests drove the fix. Same gate attempt2 PASS: 72 focused
  tests, seven original probes and 38 additional edge cases passed.
- Phase3 lead: 66 passed in 5.37s across test_worker_cleanup, test_loop,
  test_loop_run_budget, test_budget, test_task_guidance_integration,
  test_turn_timing, test_cli_owned_lifecycle and test_sdk_review_streaming.
  Six new cleanup fixtures failed before implementation. Three existing SDK
  scoped-read shadow warnings remain. Independent lifecycle gate1 FAIL: repeated cancellation or cancellation
  during error cleanup aborted closure and released ownership. Two added red
  fixtures drove whole-turn task ownership/shielded waiting; 68 focused tests
  passed in 5.50s, then eight cleanup fixtures passed in 0.75s with actual AnyIO
  scope/task assertions. Same-judge attempt2 FAIL: cleanup inside iterator advancement could still be
  aborted. Two more red tests drove a one-event acknowledgement/cooperative
  stop design. Lead70 passed in5.71s, then12 focused cleanup cases passed in1s.
  Same-judge attempt3 FAIL: actual OpenAICompatBackend cancellation regressed.
  The independent blocked-transport comparison showed accepted phase2 closed
  and cancelled its read; candidate3 did neither until fixture release. The
  judge also ran72 focused tests (pass5.80s,3 existing warnings), demonstrating
  why passing the existing suite was insufficient. Candidate withdrawn; its
  test results are historical evidence, not evidence for active source cleanup.
- Sandbox asyncio/process fixture runs timed out (including a 79-test attempt
  after 77 dots). The lead reran those bounded CPU fixtures with approved
  sandbox escalation; completed results above are separate evidence. No timed
  out run is counted as a pass. A snapshot overwrite was refused as designed;
  a new revision filename was used instead.
- Graph discovery repeatedly returned Transport closed or stalled; bounded
  retries were followed by focused source reads. No graph completeness claim.
- Automated approval review rejected the attempted packet-only Grok consultation
  before execution because exact payload disclosure/sensitivity was not verified.
  Nothing was sent; no alternate transmission was attempted. No Claude/Grok
  feedback was received. The requested source-only review file is available.
- After withdrawing phase3, the accepted combined regression command passed
  **225 tests in4.35s**: `timeout 40 .venv/bin/python -m pytest -q
  tests/test_model_adaptation.py tests/test_capability_integration.py
  tests/test_provider_capabilities.py tests/test_compaction.py
  tests/test_local_tuning.py tests/test_performance_backend.py
  tests/test_performance_modes.py tests/test_active_settings.py
  tests/test_council_handoff.py tests/test_salvage.py
  tests/test_stream_completion.py tests/test_backend_resilience.py
  tests/test_harness_backend_quality.py tests/test_local_request_coordination.py
  tests/test_inference_coordination.py tests/test_loop.py
  tests/test_loop_run_budget.py tests/test_task_guidance_integration.py --tb=short`.
  This is focused regression, not a full suite or model-quality benchmark.
- Restored Engine/Loop files match their phase2 content byte-for-byte. No rejected
  candidate was loaded into the owner's running application. Python source
  compilation passed for both retained production modules and both new test files.
- Final tracking passed:23 dated records and13 changed source paths accounted
  for against the baseline. An initial final check caught CURRENT still naming
  a blocked item as active; Current work was corrected to none.

## Unfinished work

The Alpha Omega skill's three-failure circuit breaker stopped this iteration.
Phase3 cleanup abandonment remains an unresolved baseline defect. The withdrawn
candidate additionally had an HTTP stop regression and must not be reapplied
as accepted code. No judge or build agent remains active. Phases4/5 are planned,
not implemented: offline wheel asset false positives and numeric env startup errors.
Research hypotheses about user-intent priority, actionable tool errors, cache
stability and evidence freshness need concrete repository evidence before edits.
Live model quality, native application layout, hardware capacity, real wheel/clean
installation, owner acceptance and release remain unqualified. No benchmark was
run. Build tools are unavailable in the current venv; no dependency install made.
No owner process, provider session or live lock was changed. Fixture subprocesses
were owned, bounded test children. Do not blindly resume uncertain model actions.

## Next steps

Owner review of the three-failure checkpoint is next. Proposed resumed work:
define adapter-owned interruption of in-flight I/O that preserves same-task SDK
cleanup, retain the explicit iterator ownership regressions, then return the
combined correction to independent judgment. The existing HTTP interrupt is a
no-op and the read timeout defaults to none; documenting an indefinite Stop wait
is insufficient. There is no disagreement with the blocking finding. Do not
start another phase to bypass this gate. Preserve accepted phase1/2 changes and
all pre-existing dirty work; the archived candidate is evidence, not a restart command.
No owner acceptance, commit, publication or production release is recorded.

## Files changed

- `dream/core/backends/openai_compat.py`
- `dream/core/inference_coordination.py`
- `tests/test_model_adaptation.py`
- `tests/test_stream_completion.py`
- `tests/test_backend_resilience.py`
- `docs/capability-contract.md`
- `docs/runtime-controls.md`
- `docs/harness-review-packet.md`
- `docs/superpowers/plans/2026-09-10-harness-adaptation-loop.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/DECISIONS.md`
- `docs/project/updates/2026-09-10-harness-hardening-loop.md`

Evidence artifact (outside source snapshot): `artifacts/harness-validation/dream035-phase3-rejected.patch`.
