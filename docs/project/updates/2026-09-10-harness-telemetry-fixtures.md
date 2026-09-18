# DREAM-035 — Isolate GPU telemetry in default test fixtures

Recorded: `2026-09-10T22:26:04-05:00`
Work items: `DREAM-035`
Outcome: `implemented`
Actor: Codex lead integration/docs; astra_telemetry_fixture_impl in isolated source checkout.

## Request

Follow phase13 acceptance
after accepted phase12. Correct two nominal model-free fixture paths that can
construct the real hardware sampler. Keep passing test counts separate from the
withdrawn no-GPU-probe qualification of the prior full run.

## Changes

Main baseline /tmp/dream035-telemetry-fixture-baseline.json has559 hashes.
Verified current-source isolation at /tmp/dream035-telemetry-fixture-workspace has
511 copied files and a symlink to existing dependencies. Manifest:
/tmp/dream035-telemetry-fixture-isolation.json. No HEAD restoration or install.

Astra implementer owns isolated copies of the four test paths below and a focused
new helper/regression path only after declaration. Main source remains unchanged
until a fresh independent gate approves the exact diff/hashes. Lead owns all docs.
No production behavior, monitor UI, model opt-in or hardware sampler rewrite.

## Validation

Prior full suite2829 passed/35 skipped/7 warnings in238.95s but a20s thread dump
showed an active sampler. Source inspection found /new fixture App.start unisolated
and PTY monitor child forcing DREAM_MONITOR=1 with no fake sampler. Interrupt child
already disables monitoring; its preliminary attribution was corrected in phase12.
Exact prior hardware accesses remain unrecorded. Fresh audit complete at /tmp/dream035-fixture-gpu-audit.md.

## Unfinished work

Phase13's seven test-file corrections passed independent gates and are integrated.
The final full suite remains failed because of a separate Desktop WebSocket
context-exit cancellation. No telemetry guard errors remain. Fresh lifecycle
investigation continues under DREAM-035; no universal hardware-isolation, live,
native, wheel/install or owner acceptance is claimed.

## Next steps

Use the preserved corrected full log and fresh lifecycle audit to declare the next
bounded correction before editing. Preserve both failed full-suite results. The
slow test's stale Preview patch calls no active executor path; source audit found
its actual default timeout is30s, not evidence of a product timeout defect.
No owned full-suite session remains active. Lead reconciles the audit, performs
isolated fixes and obtains a new independent gate before another justified run.

## Files changed

- `tests/conftest.py`
- `tests/test_permission_hardening.py`
- `tests/pty_driver.py`
- `tests/test_pty_e2e.py`
- `tests/telemetry_guard.py`
- `tests/test_telemetry_isolation.py`
- `tests/test_local_tuning.py`
- `docs/harness-review-packet.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/superpowers/plans/2026-09-10-harness-adaptation-loop.md`
- `docs/project/updates/2026-09-10-harness-telemetry-fixtures.md`

Fresh read-only audit confirmed both default-constructor paths and cleared the
interrupt child, whose parent sets DREAM_MONITOR=0. Permission fixture also omits
sampler shutdown; PTY child normally shuts down but still uses host defaults.
Explicit fake-root telemetry tests remain legitimate synthetic coverage. The audit
ran no tests/hardware/probes and changed no source. Its proposed guard must record
violations because App swallows monitor-start Exceptions, and child instrumentation
must be independent of the parent pytest monkeypatch.

Lead dispatched astra_telemetry_fixture_impl with the exact four-path scope and
prior-to-edit requirement for any helper/regression addition. Main source frozen.
The current-source isolation includes tasks.py now that phase11 corrected inventory.
No competing agent backlog or stale-HEAD reconstruction is used.

Scope refinement before new-file edits: approve isolated tests/telemetry_guard.py
for the shared parent/child constructor guard and tests/test_telemetry_isolation.py
for pre-discovery and swallowed-start regressions. Guard permits only explicit
owned fixture roots/executables, rejects missing/default/outside roots before
original construction, records attempts and makes teardown fail. PTY child installs
its own guard and verifies synthetic sampler lifecycle. This preserves the original
four-path ownership; no production or test_telemetry_gpu.py change is authorized.

Implementation progress: isolated guard/helper and regressions are written; main
source remains unchanged. Corrected permission fixture passed1 test in0.59s.
Safe guarded old-start and direct monitor regressions reached their bodies, then
hung during asyncio runner default-executor shutdown. Owned cancellation/timeout
runs are incomplete, not passes; contributor will record exact commands/statuses.
A minimal asyncio.to_thread(lambda:123) probe without Dream/pytest also printed its
result then failed to close within10s. This supports an environment executor issue,
not a demonstrated product/guard defect. A bounded automatic-review exception for
that minimal CPU probe is within the existing scope; no production threading edit.

The first automatic-review request timed out rather than rejecting the probe; one
retry was allowed. Minimal standard-library asyncio probe outside the managed
sandbox exited0 and printed both result123 and closed, versus managed exit124.
No Dream threading change follows. Sync guarded math/lifecycle24 passed with1
explicitly deselected async case, and corrected permission1 passed. The bounded
six-file focused selection now runs outside sandbox under automatic approval,
with guard/telemetry/monitor/permission/interrupt/PTY cases. Exact true status and
log /tmp/dream035-telemetry-fixture-green.log remain pending. No full rerun yet.

Isolation completeness correction before judging: root copy selection omitted
.gitignore, which tests/test_source_inventory.py reads. Lead copied its unchanged
baseline bytes and added its hash to the isolation manifest, now512 files. This
changes no main source or candidate behavior; no test failure was needed to spot
the missing fixture input. Implementer still owns only six declared test paths.

Isolated implementation DONE_WITH_CONCERNS, frozen for fresh gate. Report:
/tmp/dream035-telemetry-fixture-impl.md; author diff/hashes are linked there. Lead
independently verified all512 baseline files, exactly4 changed existing tests and
2 declared new helpers, main originals, AST and exact six-file diff at
/tmp/dream035-telemetry-fixture-lead.diff with -lead-hashes.json. Main remains
unchanged; astra_telemetry_fixture_gate now verifies spec, test quality and scope.

Final focused six-file selection passed67 in13.37s, exit0, no warnings/skips. It
includes the real PTY and all interrupt cases. Exact command/env are in the author
report; log /tmp/dream035-telemetry-fixture-green.log. All pre-existing monitor
rate/TTFT/pp/session/exit assertions remain, with deterministic Fixture GPU/42%/
fan777 output and independent child start/stop/snapshot/release/counter checks.
Permission fixture keeps its assertions, adds a nonempty prior approval, disables
unrelated GUI/monitor and always shuts down. No production behavior changes.

Safe exact old /new AST replay completed every original assertion, recorded1
forbidden default constructor attempt and0 real sampler initializations, then
failed guard teardown as intended, exit1. Log:
/tmp/dream035-telemetry-fixture-old-replay.log. No actual default sampler was used
to obtain red evidence. Parent/child guard is bounded constructor instrumentation;
it does not establish universal GPU isolation or protect arbitrary collection-time
code. Unchanged interrupt children already set DREAM_MONITOR=0.

Author's initial owned replay cancellation exited130. Subsequent45/60/90s attempts
were incomplete; shell wrappers ending with cat masked timeout status, now clearly
corrected in the report. No pass is assigned to them. Standard-library minimal
managed probe exited124, approved outside equivalent exited0. Final commands keep
true exit statuses. No production threading workaround and no author-owned test
session remains active. Final independent review/integration/full run pending.

Independent judge progress, final verdict pending: fresh67-test selection passed
in13.79s, exit0. Fifteen additional argument cases stopped before original init.
Exact old /new assertions completed then guard recorded1 attempt/0 real init.
An adversarial actual PTY child swallowed an imported-alias default constructor
error, yet exited unsuccessfully with counter1/original init0. Positive child
reported counter0/start1/stop1/snapshots30/released. These are independent findings,
not author evidence. Candidate remains frozen while the final report is written.

Final independent phase13 gate PASS for the declared sampler-constructor contract,
zero blocking findings. Report /tmp/dream035-telemetry-fixture-gate.md. Judge67 tests
passed13.79s without skips/warnings, plus15 adversarial cases, exact old fixture
and positive/negative actual PTY-child probes. All512 baseline entries/six hashes
verified before and after review. Limits explicitly exclude arbitrary GPU I/O,
collection-time code, malicious fixture executables and descendant-path sandboxing.
No owner acceptance or universal hardware-isolation claim.

Lead checked each main original and candidate hash, then integrated exactly the
six accepted test files. Production source remains at accepted phase12. The first
full-suite sandbox-exception request timed out in automatic review before execution;
its response allowed one retry, which was approved. No safety finding was given.
Only the command was retried; file integration was not repeated. Owned exec66930
now runs the guarded full suite. Command: DREAM_ROOT from mktemp -d
/tmp/dream035-final-guarded-integration-XXXXXX, PYTHONPATH set to
/tmp/dream035-astra-preview-diagnostic, PYTHONDONTWRITEBYTECODE=1,
timeout360s .venv/bin/python -m pytest -q -ra --tb=short -p no:cacheprovider
-p dream035_cpu_preview_diag -o faulthandler_timeout=20, log
/tmp/dream035-final-guarded-integration.log. Existing model guards remain enabled;
no --with-models. New parent/PTY telemetry guards apply; browser --disable-gpu
remains explicit. Integration result pending, not inferred from focused passes.

First guarded full integration did not pass:2840 test bodies passed,35 skipped,
7 existing warnings and1 teardown error in245.29s, exit1. The error is exactly
 tests/test_local_tuning.py::test_launch_order_and_session_scope: guard recorded
one default constructor attempt and rejected teardown. Source trace:
serve_and_run -> _choose_model_settings -> model_defaults.recommend ->
inspect_hardware -> GpuSampler(). Its preflight was already faked, but recommendation
capacity inspection was not. The guard blocked sampler initialization; the caught
error allowed the fixture body to pass, demonstrating why teardown checks matter.
The20s diagnostic recurred without a sampler thread and remains unattributed.

Scope extension BEFORE correction: approve only that test function in isolated
 tests/test_local_tuning.py to inject explicit synthetic recommendation hardware at
model_defaults.inspect_hardware, preserving all prompt-order, GPU/context/sampling
and session-cleanup assertions. Verify the fake is actually used and no guard
violations occur. Do not stub away recommend/selection, weaken the guard, change
production defaults or broadly alter launcher tests. Author returns exact one-file
correction to the SAME astra_telemetry_fixture_gate before integration. Main is
frozen at the six integrated files while this correction is isolated.

Isolated corrective candidate complete:70 local-tuning/preset tests passed0.66s,
exit0, with active constructor guard and approved outside-sandbox execution.
Only test_launch_order_and_session_scope changed: synthetic two-discrete-GPU32GiB/
64GiB-RAM capacity lookup, called-once assertion, all8 original assertions retained.
Exact report /tmp/dream035-telemetry-fixture-correction.md; root independently
verified incremental -correction-lead.diff and -correction-lead-hashes.json.
Prior six accepted hashes are unchanged in main and isolated trees. Correction
remains isolated and frozen for SAME astra_telemetry_fixture_gate. No full-suite
pass is inferred from70 focused passes; first guarded full error stays recorded.

Correction safety clarification: real inspect_hardware also reads /proc/meminfo
after catching a sampler error. The corrective fake replaces that whole function.
Judge was instructed to use a fail-before-body sentinel for any original-path
counterexample; a blocked sampler alone is insufficient to prevent the RAM read.
Its fresh70-test selection has exited0; final correction verdict still pending.

After correction approval, final integration will use a NEW log path
/tmp/dream035-final-guarded-corrected.log to preserve the failed full log. Verbose
pytest test names will accompany the existing20s faulthandler diagnostic so the
remaining slow fixture can be identified in that already-required run. No timeout
increase, fixture skip, hardware probe or production change is proposed.

Same judge correction PASS:70 fresh tests passed0.36s, no skips/warnings, plus
safe exact original/corrected AST comparisons with inspect_hardware replaced by
a fail-before-body sentinel. Real recommendation was called once in both; only the
old fixture reached the sentinel. No real inspection/RAM read or sampler init ran.
All8 original assertions and prior six hashes verified. Report:
/tmp/dream035-telemetry-fixture-correction-gate.md. Earlier67 gate and guarded full
failure remain separate results; no retrospective pass is assigned.

Lead integrated only the reviewed tests/test_local_tuning.py hash after checking
old edfeccb3c5346794e061d498a35c9fb4c89be02ce07200e369c3f88c322ff160 and new
f7ed6a95f3c1278030e162a56249267a2f9d717d0345b5a68d277ec0ce020ee1.
Automatic review approved the final guarded corrected run, owned exec8962.
Command: DREAM_ROOT from mktemp -d /tmp/dream035-final-guarded-corrected-XXXXXX,
PYTHONPATH=/tmp/dream035-astra-preview-diagnostic, PYTHONDONTWRITEBYTECODE=1,
timeout360s .venv/bin/python -m pytest -v -ra --tb=short -p no:cacheprovider
-p dream035_cpu_preview_diag -o faulthandler_timeout=20; output
/tmp/dream035-final-guarded-corrected.log. This is the same guarded full scope with
verbose test names; prior failed log remains intact. Result pending.

Final corrected guarded integration exited1:2839 passed,35 skipped,7 existing
warnings and1 failure in240.71s. No telemetry guard error remained. Failure:
test_desktop_delivery.py::test_delivery_uses_browser_connections_and_preserves_tool_results[1-tool1],
concurrent.futures.CancelledError during Starlette TestClient WebSocket context
exit. This is not assigned a flaky/environment cause without evidence. Fresh
read-only Astra audit astra_desktop_delivery_audit investigates exact lifecycle
source; no new source edits authorized yet. Report pending at
/tmp/dream035-desktop-delivery-audit.md.

Verbose log attributes the recurring20s dump to
test_a_timed_out_script_still_returns_the_log_lines_it_printed, which eventually
passes. Its wrapper supplies timeout_s=1, so a separate fresh read-only Astra
audit examines the timeout boundary before deciding whether a change is warranted.
Report pending /tmp/dream035-script-timeout-audit.md. Root graph search failed
Transport closed, then focused source read confirmed the test's wrapper/assertions.
No source changes or full-suite retry follows solely from this failure.

Phase13 reconciliation: all seven integrated test hashes match the independently
reviewed candidates. Tracking passed34 dated records/12 changed paths. Scope
implemented; final full-suite failure remains visible and is not converted to a
pass. Source-only timeout audit /tmp/dream035-script-timeout-audit.md found the test
patches unused Preview.run_script while the active core executor defaults to30s.
The diagnostic identifies a stale test accelerator, not a proven runtime timeout
defect. No additional tests or browser processes were used for that audit.
