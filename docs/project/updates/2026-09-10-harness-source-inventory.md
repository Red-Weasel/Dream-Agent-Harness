# DREAM-035 — Keep private memory excluded without hiding source

Recorded: `2026-09-10T21:32:43-05:00`
Work items: `DREAM-035`
Outcome: `verified`
Actor: Codex lead implementation/tracking; independent astra_inventory_gate.

## Request

Continue the phase11 contract
after phase10's independent PASS and exact integration. Preserve private root
memory while restoring source completeness and change detection.

## Changes

Baseline /tmp/dream035-source-inventory-baseline.json contains554 hashes. Lead owns
only .gitignore, new tests/test_source_inventory.py and the shared docs listed below.
Acceptance precedes edits: change only memory/ to /memory/, verify all164 existing
Python source files are inventoried, preserve tasks.py's existing content, test
current/future source inclusion and mutation detection, and retain private root
memory/data/runtime exclusions. No checker policy change or package claim.

The newly visible dream/memory/tasks.py is pre-existing source with SHA256
98dd1523b967f17e40524ddfbbdba271082e6fb3047704124b7f5b555566f77e. It is listed for
tracking coverage, not authored or edited by this phase. Existing dirty work stays.

## Validation

Prior fresh read-only Astra audit reproduced164 on-disk versus163 inventoried
Python files. Actual synthetic Git and tracking.snapshot showed that anchoring
memory/ exposes current/future source and detects mutations while preserving7/7
private exclusions. Evidence /tmp/dream035-ignore-audit-le5iuvdw/audit-result.json.
Implementation and independent verdict are recorded below. Graph-first discovery
returned Transport closed; focused checker/test reads followed.

## Unfinished work

None within the accepted inventory contract. The separate alias audit is complete,
with a declared phase12 correction planned next. No model/GPU/provider/browser/benchmark,
build/install, staging/commit/publication or owner-process operations.

## Next steps

Lead takes a fresh phase12 baseline and implements the declared return-isolation
contract, then uses a new fresh Astra judge. No owner acceptance or wheel qualification claimed.

## Files changed

- `.gitignore`
- `tests/test_source_inventory.py`
- `dream/memory/tasks.py`
- `docs/harness-review-packet.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/superpowers/plans/2026-09-10-harness-adaptation-loop.md`
- `docs/project/updates/2026-09-10-harness-source-inventory.md`

Before correction, the new regression reproduced2 failures for missing source and
undetectable mutation, while the private-exclusion control passed. Log:
/tmp/dream035-source-inventory-red.log. After only memory/ to /memory/, all3 passed
in0.05s, exit0 with no warnings/skips. Command: DREAM_ROOT set to
/tmp/dream035-source-inventory-runtime, PYTHONDONTWRITEBYTECODE=1, timeout30s,
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_source_inventory.py.
Synthetic Git uses no staging/commits and actual tracking.snapshot, with only
rev-parse HEAD stubbed. Main inventory now164/164; no ignored untracked Python
source under dream/. tasks.py hash preserved. Evidence:
/tmp/dream035-source-inventory-main.json; exact two-file candidate
/tmp/dream035-source-inventory-candidate.diff and source hashes
/tmp/dream035-source-inventory-hashes.json. Main source is frozen for fresh gate.

Separate Astra alias audit verified that synthetic nested cold-return mutation
can change registered validation constraints; cache-hit copies remain detached.
No ordinary production mutator was identified, and four normal fake turns kept
schemas intact. Proposed narrow return-copy correction is not implemented here.
Report /tmp/dream035-schema-alias-audit-report.md preserves a moving-baseline
ImportError and exact original/candidate reproduction. No permission-bypass claim.

Phase12 return-isolation acceptance is now planned in the shared plan, after this
gate. Root graph timed out after5s and the focused source read stalled before
completing successfully; no backend edit occurred. Tracking before this fresh gate
passed32 dated records and8 changed source paths.

Final fresh gate PASS, zero blocking findings. Independent3 focused tests passed,
14 synthetic source paths and30 private paths passed inclusion/exclusion checks.
Source mutation/add/rename/delete checks passed. Original-rule replay reproduced
2 failures/1 pass; removing private guards reproduced1 failure/2 passes. Combined
regression15 passed. Exact candidate hashes, only8 declared baseline changes and
main source inventory164/164 verified; no source changed during the judge's check.
Report /tmp/dream035-source-inventory-gate.md. Judge tracking32 records/8 paths
passed. Package/clean-install qualification remains unperformed.

Lead final tracking passed32 records/8 changed source paths. Evidence clarification:
the judge also ran existing project-tracking tests, whose two Git fixtures stage
and commit synthetic temporary repositories. No main repository staging or commit
occurred. Its blanket no-commit wording is being corrected in the temporary report.
