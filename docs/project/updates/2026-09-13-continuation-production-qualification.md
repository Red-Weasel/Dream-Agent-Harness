# DREAM-065/066 — Continuation and production qualification

Recorded: `2026-09-13T13:43:49+00:00`
Work items: `DREAM-065, DREAM-066`
Outcome: `implemented`
Actor: Codex lead; Astra continuation and qualification implementers; separate fresh Astra reviewers.

## Request

Implement priorities 1 and 6: reliable continuation/supervision and model adaptation
with production qualification. Preserve existing dirty work, avoid benchmarks and
local-model loads, and obtain independent review. The separate rocket trial remains
DREAM-014. Snapshot: /tmp/dream-priorities-1-6-baseline.json.

## Changes

The autonomous worker now carries saved NEXT intent and uses only its final
standalone STATUS block outside fenced examples. CLI, TUI and Python have no
implicit iteration cap; explicit positive limits still work. Canonical run
inspection projects saved state and observed kernel ownership into CLI and Projects.
It neither creates files nor resumes work. Uncertain effects require reconciliation.

Fresh review reproduced four defects and lead repaired them: unclosed fenced
examples treated as control, replaced lock inodes reported free, deeply nested JSON
crashing public listings, and symlink ancestors followed during inspection. Shared
strict ledger parsing and anchored no-follow descriptors preserve the read boundary.

HTTP effort uses exact current reported native levels before adapter aliases,
rejects incompatible choices before changing settings and keeps invalid stored
text private in status. Model changes clear capability evidence. The fixed
qualification runner produces explicit results and listed-file hashes using
synthetic fixtures and temporary child configuration.

Installed-wheel and synthetic-canary checks cover the package, not Git history.
Operator docs and ADR-047/048 describe the contracts and their limits. No runtime
policy file, owner setting, active desktop controller or private memory was changed.

## Validation

Observed on Python 3.12.3, September 13, 2026:

- Integrated continuation command: `.venv/bin/python -m pytest -q tests/test_continuation_supervision.py tests/test_run_supervision_views.py tests/test_autonomous_default_continuation.py tests/test_project_workspace.py tests/test_durable_autonomy.py tests/test_run_state_validation.py tests/test_run_state_write_failure.py tests/test_loop_mode.py`. Result: 405 passed, one skipped in8.16s. Log: /tmp/dream-continuation-integrated.log. Skip was trusted bubblewrap unavailable in restricted execution; host rerun of test_loop_mode.py passed all4 in0.36s.
- Fresh continuation review: 74 passed in2.76s, including27 independent probes, with six production source hashes rechecked. /tmp/dream-continuation-independent/review-notes.md and final.log. Initial failing probes remain in first/deep/ancestor logs. Unsupported exploratory plain-quote/duplicate-key assertions were explicitly excluded, not presented as established protocol defects.
- Author qualification runner: `.venv/bin/python scripts/qualify_production.py --output /tmp/dream-production-qualification.json`; 277 passed, no failures/skips. HTTP100, CLI/SDK147, tools30. Listed source consistency verified. Additional settings/profile/council regression99 passed in6.08s. New qualification tests39 passed. Earlier iterations failed before repairs; one nonexistent test filename and restricted localhost fixture failure were corrected and rerun. Prior runner could inherit Codex model catalog settings; final runner isolates child config, and final277 evidence supersedes that isolation claim.
- Fresh qualification review:30 independent probes passed. Exact native effort payloads, aliases, model changes, secret-safe invalid settings, JUnit failure/skip/timeout interpretation and child-only environment isolation exercised. /tmp/dream-qualification-independent/review.md. Independent full-runner recheck failed: all three groups hit their90second watchdog in restricted execution without a completed JUnit report. Source consistency passed. Cause is unknown; this is not an independent reproduction of277passes. Collection-only diagnostic collected39tests in0.07s. Root reran the same fixed runner with host fixture access:277passed, zero skips/failures/errors, listed source consistency verified at13:44:32Z. Report: /tmp/dream-production-qualification-root.json. The restricted timeout cause remains unproven.
- Actual offline wheel build:267 members;263 code/asset/skill members byte-match source. SHA25671e5d0711e09845405d88f858d24fecea4fc4e8f1e1a33d5ab6c5c14a5ab75be. Disposable source copy used only packaged production files and synthetic canaries in nine excluded private directories. Rebuilt wheel contains no canaries and every member matches the original payload.
- A new temporary venv installed the wheel with no dependencies, then reused the existing environment's dependency path. Import resolved to that venv's installed Dream. Bundled10skills, help/status/runs/profile all passed against temporary state. Fully fresh offline resolution failed because the SDK distribution was not cached; this is not a clean-machine dependency qualification. Report: /tmp/dream-package-qualification/report.json. Initial build/venv attempts could not write uv's restricted cache; host offline build and temporary-cache venv succeeded.
- Code graph transport closed for lead and scoped symbol searches were insufficient for reviewers; focused source reads were used. No benchmark, live provider call, model load, owner desktop acceptance or publication was performed for this work.
- Tracking check initially reported missing handoff/path coverage before this record existed. Final tracking passed:79dated records and21changed source paths. The first completed-record check caught a trailing period in the required Latest handoff link; correcting that formatting resolved it. Scoped git diff --check passed.

## Unfinished work

No known blocking defect remains in reviewed implementation scope. A completely
fresh dependency installation, live provider/image acceptance, native daily use,
local-model quality and owner production acceptance remain unverified. None was
substituted with a fixture pass. Publication and the separately proposed Git-history
cleanup were not performed. Existing runtime processes keep their imported code.

DREAM-014 Astra trial remains active on its isolated :95 display. Lead confirmed
checkpoint07 and fully decoded its earlier rough timing MP4; visual sampling found
a camera cutoff near15seconds and the worker is repairing it. It is a blockout,
not an accepted cinematic animation. Do not replace its pinned controller or touch
Opus :94/owner windows. Resume from the latest private saved scene if interrupted;
check existing files and active worker ownership before sending new actions.

## Next steps

Load these harness changes at the owner's next normal restart. Run the new fixed
qualification command after future adapter edits. Continue authorized DREAM-014
supervision separately. DREAM-012/015 own actual daily-use/provider/model checks;
obtain applicable model/GPU prerequisites before those checks. Owner acceptance
and any release decision remain separate.

## Files changed

- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/DECISIONS.md`
- `docs/durable-runs.md`
- `docs/runtime-controls.md`
- `docs/public/production-qualification.md`
- `docs/superpowers/plans/2026-09-13-continuation-production-qualification.md`
- `dream/__main__.py`
- `dream/core/backends/openai_compat.py`
- `dream/core/loop.py`
- `dream/core/run_state.py`
- `dream/management.py`
- `dream/projects/recovery.py`
- `dream/tui/app.py`
- `scripts/qualify_production.py`
- `tests/test_autonomous_default_continuation.py`
- `tests/test_continuation_supervision.py`
- `tests/test_production_qualification.py`
- `tests/test_project_workspace.py`
- `tests/test_run_supervision_views.py`
- `docs/project/updates/2026-09-13-continuation-production-qualification.md`
