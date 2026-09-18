# DREAM-035 — Preserve WebSocket cleanup under cancellation

Recorded: `2026-09-10T22:36:58-05:00`
Work items: `DREAM-035`
Outcome: `implemented`
Actor: Codex lead docs/integration; fresh Astra audit and isolated implementation.

## Request

Continue the owner-requested fresh-review loop from the failed full integration.
Acceptance is recorded in phase14 of the shared adaptation plan before source edits.

## Changes

Studio WebSocket cleanup now retains client/subscription ownership until both
workers settle, shields their join from repeated cancellation, and propagates the
first parent cancellation reason. Unexpected worker and cleanup errors remain
visible; simultaneous failures are grouped. Ordinary closed-socket handling,
authentication, replay, permissions and delivery retain focused coverage.

Exactly two independently reviewed files were integrated. Main baseline
/tmp/dream035-websocket-cleanup-baseline.json has562 hashes;514 current-source
files were copied to the isolated workspace, with existing dependencies linked.
Pre-existing dirty work remains intact. No Git checkout, build or installation.

## Validation

Prior corrected full suite:2839 passed/35 skipped/7 warnings/1 WebSocket context-exit
CancelledError in240.71s, exit1. No telemetry guard errors. Fresh read-only audit
reproduced a reasonless cancellation escape in actual unmodified Studio code under
controlled scheduling.100 TestClient sessions passed outside managed sandbox;
original exact scheduling remains unobserved. Root graph search returned Transport
closed; audit graph/source evidence and focused reads used. Exact report:
/tmp/dream035-desktop-delivery-audit.md. No source fix claimed yet.

## Unfinished work

None within the declared phase14 correction and verification scope. Live provider,
native Desktop, real wheel/clean install, model-quality and owner acceptance remain
unperformed. A permanently noncooperative child can keep cleanup pending; no
shutdown deadline is claimed. Constructor instrumentation does not establish
universal GPU isolation. Original failure scheduling remains unobserved.

## Next steps

Use this accepted checkpoint for the next normally started Dream application;
verify Council main/advisor effort and between-turn handoff under the existing
native/provider qualification scope. Do not restart or stop owner processes here.
Further harness changes should start from a concrete reproduced defect or a
declared qualification contract; existing model/GPU/benchmark holds remain.
Take a fresh snapshot before another changing phase, preserve prior failed logs,
and obtain a new independent gate for new source. All owned tests have exited.

## Files changed

- `dream/gui/server.py`
- `tests/test_desktop_delivery.py`
- `docs/harness-review-packet.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/superpowers/plans/2026-09-10-harness-adaptation-loop.md`
- `docs/project/updates/2026-09-10-harness-websocket-cleanup.md`

Audit report complete. Installed Python3.12 gather can replace the owner cancellation
with a bare child cancellation when its own cancellation races child settlement.
AnyIO4.15 checks cancellation provenance; Starlette1.6 context exit calls the portal
future result and exposes concurrent.futures.CancelledError if provenance is lost.
This is a reproduced valid interleaving; original exact scheduling remains inferred.
No dependency change is proposed. A shield alone is not proof for direct asyncio
cancellation; accepted correction must also join children and preserve that case.

Implementer pre-fix evidence: actual audit probe in isolated original handler
reproduced reasonless CancelledError and completed both children; log
/tmp/dream035-websocket-probe-original.log. Seven new targeted regressions in the
existing test_desktop_delivery.py failed old source in0.32s, covering cancellation
provenance, delayed-cleanup ownership and swallowed arbitrary ValueErrors from
hello/send/receive/cleanup; log /tmp/dream035-websocket-regressions-red.log. These
are expected red results, not passes. No new regression file is needed. Main
production/test hashes still exactly match the514-file isolation baseline.

Isolated candidate18 focused fake-socket cases passed0.26s. Coverage includes
original controlled scope race, cancellation before/during cleanup, repeated direct
cancel retaining first reason and cancelling()==3, slow AnyIO cleanup, ordinary
closed-transport errors, unexpected worker/cleanup errors and combined cancellation
plus cleanup failure. Log /tmp/dream035-websocket-fake-green.log. These are author
results; existing TestClient/owned loopback verification and fresh gate remain.

Author final focused selection53 passed/2 existing dependency deprecations in0.60s,
exit0, outside managed sandbox after automatic approval: test_desktop_delivery.py
and test_gui_server.py. Log /tmp/dream035-websocket-integration-outside.log. Actual
owned loopback idle/reconnect/connected shutdown and new TestClient history, pending
permission, retained show, and real permission answer passed. Managed equivalent
timeout124 after45s is incomplete, not a pass. Candidate exact original audit probe
now reports CANCELLATION SUPPRESSED, CLEAN2 HELLO1.

Lead independently verified514 baseline files, exactly the two declared candidate
paths changed and each main original matches. Exact /tmp/dream035-websocket-cleanup-lead.diff
and -lead-hashes.json freeze the review boundary. Fresh read-only
astra_websocket_cleanup_gate now checks tests and independent cancellation schedules.
Candidate/main remain frozen; report pending /tmp/dream035-websocket-cleanup-gate.md.
No full integration pass is inferred from author results.

Independent gate progress, final verdict pending:53 fresh focused tests passed
in0.57s with2 dependency warnings. Twenty-six additional cases passed, including
both workers delayed with reversed release order, six direct cancellations, mixed
AnyIO/direct ordering, two concurrent cleanup failures, simultaneous worker failures
and hello cancellation. Original source fails its independent probe at premature
resource release. Candidate514-file scope and main source originals verified.
No integration until the judge's final report/verdict.

Fresh phase14 gate PASS, zero blocking findings. Report
/tmp/dream035-websocket-cleanup-gate.md. Independent53 tests passed0.57s with2
dependency warnings;26 adversarial cases and exact actual-handler old/new provenance
probe passed candidate. Original fails reason preservation and early ownership
release. Judge initially overexpected main docs to match the isolation snapshot;
corrected integrity check accounts for the four declared lead doc changes. This
was a judge-script assumption failure, not candidate failure.

Lead checked both main originals and both accepted candidate hashes, then integrated
exactly dream/gui/server.py and tests/test_desktop_delivery.py. Prior telemetry
guards remain intact. Full guarded verification is next; no full pass is claimed.
No shutdown deadline is established for a permanently noncooperative worker.

Full-suite automatic approval review timed out before execution; its response
explicitly permitted one retry. That retry was approved and launched owned exec28016.
Command: DREAM_ROOT from mktemp -d /tmp/dream035-final-websocket-integration-XXXXXX,
PYTHONPATH=/tmp/dream035-astra-preview-diagnostic, PYTHONDONTWRITEBYTECODE=1,
timeout360s .venv/bin/python -m pytest -q -ra --tb=short -p no:cacheprovider
-p dream035_cpu_preview_diag; log /tmp/dream035-final-websocket-integration.log.
Existing model-loading and telemetry guards remain active, no --with-models;
Chromium diagnostic wrapper retains --disable-gpu. No universal GPU sandbox claim.
No source changes during this run. Prior logs preserved; result pending. The
previous20s diagnostic was explained by the actual30s script timeout, so a repeated
faulthandler dump is unnecessary; no product timeout or test limit changed.

Final integrated verification observed at 2026-09-10T23:03:43-05:00: **2858 passed,35 skipped,
7 warnings in242.89s**, exit0, log /tmp/dream035-final-websocket-integration.log.
No telemetry guard error, failed WebSocket test, or TargetClosedError appears in
that log.35 skips are34 explicit model-loading opt-ins plus1 migration-state case.
Warnings are2 dependency deprecations,2 sync tests with async markers, and3 existing
SDK evaluator read-tool-shadow warnings. No warning cleanup is disguised as a fix.

Lead verified all nine integrated phase13/14 hashes still equal their accepted
independent-gate candidates. No source changes occurred during the full run. Prior
failed full logs remain intact; no retrospective pass or universal hardware/model
qualification is claimed. Phase14 implemented and independently verified; no owner
acceptance, commit, publication or perfection claim. Final tracking check follows.

Final tracking passed35 dated records and all7 changed source paths against
/tmp/dream035-websocket-cleanup-baseline.json. Links/current-state alignment passed.
No active contributor or owned test process remains; implementation, evidence and
remaining qualifications are reconciled in CURRENT.md and the shared master plan.
