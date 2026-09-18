# DREAM-065/068/067/053 — Four reliability priorities

Recorded: `2026-09-14T18:18:33.394049+00:00`
Work items: `DREAM-065, DREAM-068, DREAM-067, DREAM-053`
Outcome: `implemented`
Actor: Codex lead; provider_qualification and deliverable_checks builders; independent phase judges recorded below.

## Request

Implement priorities 1–4: bounded hosted reliability checks, runtime deliverable
checks, recovery from repeated failures, and actionable sanitized diagnostics.
Acceptance was recorded in CURRENT before changes. No GPU or local model loads,
owner application restart, memory modification, publication or benchmark campaign.
Baseline: /tmp/dream-four-priorities-0914.json, 779 source hashes. Large existing
dirty tree preserved. Graph service failed for lead; contributors could use the
exact project identifier. Focused fallback used where service unavailable.

## Changes

Hosted qualification script uses actual CLI/SDK adapters with synthetic private
sessions, opt-in live calls, bounded owned subprocesses and explicit continuation.
CPU controls cover false completion, dropped/rate-limited fixtures and cleanup.
Phase 1 gate found unhashable malformed terminal metadata; explicit string guards
and four regressions repaired it. Initial FAIL retained; same judge PASS: 53 related
and 15 adversarial checks in phase1-gate/recheck-verdict.json.

Studio done validates bounded page bytes and local static dependencies before
preview. Large dependency files get metadata-only inspection. PPTX validation
checks internal references and rejects duplicate or unsafe archive entries.
The fresh phase 2 gate found safe sibling-path rejection and two HTML attribute
semantics mismatches. Four regressions reproduced them; normalization after joining
the page parent, first-attribute handling and case-insensitive rel tokens repair
them. Same-judge recheck PASS: 138 checks (127 related, 10 independent, 1 software
browser semantics); initial FAIL preserved in phase2-gate/verdict-initial.json.

Compatible HTTP tool loops advise after identical failures across changed
arguments. New evidence and repairs reset the advisory; no new execution limit,
permission bypass or automatic replay is added.

Timing records retain fixed failure-signal counts. The timing card offers recovery
hints and reconstructs a content-free diagnostic download from allowlisted fields.
See performance/runtime-controls/harness-evaluation docs and ADR-061.

## Validation

Author observed: phase1 49 checks; phase2 123 checks; phase3 19 checks;
phase4 24 combined browser/Engine/privacy checks (overlapping focused suites,
not additive totals). Tests first reproduced missing behavior. Final combined CPU suite: 5,402 passed, 45 skipped, 1 deselected, 7 warnings, 52 passing
subtests, 473.57 seconds (runner 474.58 seconds), exit 0. All 612 source hashes stayed unchanged;
JUnit 5,499 counts = 5,402 + 45 + 52, zero errors/failures. Exactly excluded owner-memory test:
tests/test_memory_files.py::test_the_live_three_memories_migrate_with_no_loss.
Model/native dependency skips remain visible. Source was frozen while read-only
remaining reviews ran. Phase3 later passed19 checks plus an independent
adversarial probe, with no source edits or findings. All final gate hashes match
the unchanged suite source. The phase3 report timezone label was inconsistent;
original evidence is preserved and lead observation/correction recorded in
final-evidence.json.

Offline package refresh passed 271 source/wheel/installed comparisons, 275 archive
members, 3,694,058 bytes, distribution layout audit PASS. Wheel SHA256:
343749d26c2be4c3e6e83dedd0970d4bb2190e60749c65ea11c65ec837cd321c.
Existing qualified dependencies reused read-only; no fresh dependency install.
Private target wheel installation/CLI/import checks passed, no publication.

Phase 4 reviewer independently passed 38 regression and 23 adversarial checks including
actual software-Chromium diagnostic download; no findings. Gate records prior
unrelated reused context, source hashes and no source edits.

Live initial Sol/Terra first/continue/Stop/continue succeeded. Sonnet resumed after
an unclassified Stop error; Opus failed before Stop and on the following turn.
Original records preserved in ignored phase1 evidence. SDK local-cleanup true was
an evidence bug, corrected to UNKNOWN in handoff/final runner. Exact causes and
Claude server model identities are unavailable. Later runner hardening was tested
with CPU fixtures; initial live records do not claim to run that final version.
No server-side cancellation or representative quality/latency claim.

Evidence: artifacts/harness-validation/four-priorities/; no raw owner content
exported. Dependency/GPU/model/browser flags isolated in temporary test state.

Final tracking: `python3 scripts/check_project_tracking.py check --snapshot
/tmp/dream-four-priorities-0914.json` passed (87 dated records,19 changed source
paths). `git diff --check` passed. The final evidence manifest reconciles all four
gates against current source; no source mismatches. Full suite process exited0;
no owned worker remains running.

## Unfinished work

None within the implemented four-priority scope. Static presence does not establish
media decoding, factual accuracy, perceptual quality or absence of concurrent file
changes. External/dynamic dependencies, native desktop acceptance and broader
provider behavior remain separate qualification work. No owner acceptance or
release claimed.

Fresh reviewer slots were unavailable after phases1/2. Automatic approval review
rejected a separate hosted Sol process for copied auth and external source payload;
it never executed and was not retried. A safer native alternative completed gates:
native_engine_recovery reviewed phase3, and phase2 builder deliverable_checks
reviewed phase4. Neither authored its reviewed phase, but their contexts contain
prior unrelated work. The approval request for the rejected command is no longer
needed. No credentials were copied for these native review gates.

## Next steps

Load changes at the next normal owner-selected restart. Qualify native playback
and real interrupted tasks under DREAM-065/068/053 before broader claims or default
model tuning. No workers remain active; preserve the dirty tree and take a new
snapshot before edits. Final tracking and whitespace checks are recorded below.

## Files changed

- `scripts/check_hosted_reliability.py`
- `tests/test_hosted_reliability.py`
- `dream/tools/studio.py`
- `dream/workflows/validation.py`
- `tests/test_runtime_deliverable_checks.py`
- `dream/core/backends/openai_compat.py`
- `tests/test_failure_loop_recovery.py`
- `dream/telemetry/turn.py`
- `dream/telemetry/failures.py`
- `dream/gui/static/turn-timing.js`
- `tests/test_failure_diagnostics.py`
- `tests/test_failure_diagnostics_ui.py`
- `docs/performance.md`
- `docs/runtime-controls.md`
- `docs/harness-evaluation.md`
- `docs/project/DECISIONS.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-14-four-priority-reliability.md`
