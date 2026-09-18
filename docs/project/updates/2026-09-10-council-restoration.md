# DREAM-034 — Council restoration investigation

Recorded: `2026-09-10T21:03:04Z`
Work items: `DREAM-034`
Outcome: `planned`
Actor: Codex; no delegated contributors.

## Request

Restore the ability to select a main orchestrator and bring other models into
the same Dream experience. Proposed acceptance is recorded in MASTER_PLAN.

## Changes

Recorded the new request and pending design review. Runtime code remains unchanged.
Observed: core MoeConfig, independent consultations, council fan-out and terminal
role picker still exist. Desktop startup catalog explicitly skips `moe`, despite
a comment saying configuration remains in shared controls. Desktop run constructs
App without a Council config. App `/council` refuses a non-Council session, and
Engine registers Council tools/guidance only when booted with that config.
Main-model changes retain the same backend; `/new` clears display history.

Proposed: startup and in-session Council selectors, configurable main/provider
model and advisors, individual/full consultation actions, attributed outcomes,
and between-turn main-provider handoff with Dream-visible continuity. The existing
advisor role remains read-only. No provider-private history portability is claimed.

## Validation

Observed `.venv/bin/python -m pytest -q tests/test_moe.py tests/test_moe_tools.py
tests/test_council_evidence.py`: **34 passed in 0.36s**, exit 0. This verifies the
existing Council fixtures, not the proposed UI or live provider interoperability.
Initial `uv run --locked pytest ...` failed before tests because its default
cache was read-only. The existing environment ran successfully without syncing.
Graph symbol discovery/snippets located the Council and picker; an unscoped
search_code returned noisy artifact matches, so subsequent queries were scoped
to Python and subsystem paths. Focused source reads confirmed current behavior.
Baseline captured 518 source hashes at
`/tmp/dream-council-20260910-baseline.json`. Tracking check
`python3 scripts/check_project_tracking.py check --snapshot
/tmp/dream-council-20260910-baseline.json` passed with 21 dated records and all
three session-changed documentation paths covered.

## Unfinished work

Implementation has not started. The applied brainstorming skill requires design
approval before implementation; the proposed scope was presented through an
asynchronous approval question. No answer has been recorded at this writing.
No owned process or lock remains. No model loading, GPU probing, live inference,
benchmark, native UI test, restart, commit, publication or owner acceptance.

## Next steps

Resolve DREAM-034 scope review, record the approved design, and implement/verify
the startup, in-session advisor controls and main-provider handoff. Test failed
handoff rollback, active-turn exclusion, transcript/context continuity, Council
config propagation, advisor failures, and browser reconnect using CPU fixtures.
Preserve existing owner processes and the dirty tree. Read CURRENT and take a
fresh baseline before resuming; this snapshot is hashes, not a content backup.

## Files changed

- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-10-council-restoration.md`
