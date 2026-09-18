# DREAM-035 — Package asset validation

Recorded: `2026-09-10T19:01:39-05:00`
Work items: `DREAM-035`
Outcome: `verified`
Actor: Codex lead; fresh Codex packaging/privacy judge.

## Request

Continue the authorized harness-hardening loop. Phase3 passed its independent
gate and417 lead regressions; see the [previous handoff](2026-09-10-harness-cleanup-resumed.md).
Phase4 criteria are in the iteration plan:
require Council assets and direct local script/link references from the inspected
entry page in source and wheels, with bounded nonexecuting parsing and redaction.

## Changes

Active scope: diagnostics and its fixture tests/operator docs. Baseline
/tmp/dream035-assets-baseline.json contains539 hashes. Preserve all prior dirty
work. Implemented shared bounded entry-page resource parsing with HTMLParser and URL
parsing from the standard library. Council files remain invariant requirements;
future direct local assets are discovered from the inspected HTML. Source and
wheel checks share normalization/rejection. Source file/parent symlinks cannot
make an outside static resource satisfy the package check. Fixed redacted codes
report invalid input without echoing paths. Fresh review pending.

## Validation

Graph discovery again returned Transport closed; focused source reads used.
An earlier read-only audit reproduced a wheel containing the real index but no
Council assets being reported valid. Before implementation31 new cases failed and5 passed. The first failure output
was excessive because pytest used full byte fixtures as IDs; explicit short IDs
fixed that test-output issue. After implementation and source-symlink regressions,
`timeout20 .venv/bin/python -m pytest -q tests/test_diagnostics.py
 tests/test_diagnostic_assets.py --tb=short` passed53 cases in0.32s in the sandbox.
`.venv/bin/python -m dream.diagnostics --json` exited0: source assets present,
missing=0; wheel and records explicitly not_run. No private values exported.
No live calls, models, GPU work, benchmark, install, build or publication.

Phase4 attempt1 FAIL: Unicode strip removed a filename's nonbreaking space,
and prefix filtering silently skipped backslash/dot-segment spellings that resolve
to assets. Both source and wheel falsely passed. Judge53 tests and privacy/bounds/
symlink controls otherwise passed. New expanded regressions reproduced12 failures
with42 controls passing. Candidate2 trims ASCII URL padding only and rejects
ambiguous local paths before prefix filtering; the actual Unicode filename remains
required. Lead69 diagnostic/asset cases passed0.41s. Same judge reviews candidate2;
source frozen. No browser, network, model, build or install operation.

Final attempt2 PASS: the fresh judge independently passed69 tests in0.35s, all
retained counterexamples/bounds/symlink/export controls, and31 extra assertions
for valid and missing Unicode filenames, URL attributes/encoding and unsafe
references. Python compilation passed all three changed Python files. Focused
source discovery found no additional callers outside these tests. Tracking passed
25 dated records and9 source paths at the phase checkpoint. [ADR-017](../DECISIONS.md)
records the final contract. No owned fixture process remains.

## Unfinished work

Phase4 candidate2 passed independent review after the first review found URL interpretation gaps. Phase5 numeric
configuration validation and carried verifier provenance remain queued. Live
provider/native UI, clean-install and production acceptance remain unqualified.
No owned process/task/lock persists from the preceding CPU regression run.

## Next steps

Continue numeric-environment and verifier-provenance phases under separate
criteria with fresh judges. Preserve accepted lifecycle and package evidence.

## Files changed

- `dream/diagnostics.py`
- `tests/test_diagnostics.py`
- `tests/test_diagnostic_assets.py`
- `docs/diagnostics.md`
- `docs/superpowers/plans/2026-09-10-harness-adaptation-loop.md`
- `docs/project/CURRENT.md`
- `docs/project/DECISIONS.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-10-harness-package-assets.md`
