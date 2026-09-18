# DREAM-042 Runtime and tool recovery fixes

Recorded: `2026-09-11T17:45:13-05:00`
Work items: `DREAM-042`
Outcome: `implemented`
Actor: Codex lead, Astra budget and guard authors, fresh Astra recovery verifier.

## Request

Owner approved the runtime budget fix after a 7,200-second wall-time cutoff that
included 3,683 seconds of approval waiting. Complete repeat-guard and edit-recovery
repairs identified by the session audit. Investigate Studio not opening while
DeepSeek works. Preserve the continuing job and existing files.

## Changes

Acceptance criteria: exclude actual approval waits from active runtime, including
cancellation and overlapping waits; expose remaining time and distinguish limits;
preserve token/tool bounds and optional wall-time enforcement; allow observation
and rerendering after changed inputs while bounding unchanged repetition; give
correct recovery advice for zero and ambiguous edit matches; distinguish private
preview from visible Studio presentation.

Implemented active time accounting, an optional separate wall limit, and remaining
budget in Controls → Runtime. Approval callbacks count as a union of wait intervals.
Completed foreground timing stays frozen for display, while checks for queued
background work continue against the live clock. SDK-observed budget refusals
remain failures even when the provider subsequently reports success. Raw provider
subtype and usage survive, and failure state resets on the next turn.

Known mutations reset observation history; file writes also permit rerunning shell
commands. A handler failure can still follow a partial mutation, so actual handler
attempts are distinguished from earlier schema, permission and budget refusals.
Captures cannot reset capture repetition, shells cannot reset shell repetition,
and file-mutator histories stay bounded. This is conservative invalidation, not
filesystem change detection. Unknown custom mutators and external edits are not
tracked. Zero-match text edits request a fresh read and exact current text;
ambiguous matches request more surrounding context. Atomic refusal is preserved.

Studio trace through 258 records shows only private show_html calls at records 103
and 140, with no show_to_user, done or media presentation call. This does not
establish a visible Studio defect. Private-preview results now explain how to
present; media instructions encourage clearly labelled drafts while work continues.
A model still has to call presentation; arbitrary writes do not open Studio.

Budget author owned runtime, profiles, engine/backend integration and budget tests.
Guard author produced disjoint tests and a temporary proposal; root integrated its
backend patch after ownership was released. Root owned file recovery, Studio,
UI, integration and documentation. An initial third-agent attempt hit the thread
cap; after the guard author finished, a fresh independent recovery reviewer checked
the integrated work. No separate fresh Studio-only reviewer is claimed.
Baseline: /tmp/dream-recovery-fixes-baseline.json (612 source hashes).

## Validation

Observed failures before repair: zero-match edit advice; private-preview guidance;
two missing budget-panel browser cases; nine repeat-guard regressions (15 cases
already passed). The initial browser run could not bind a sandbox server. Bounded
outside-sandbox runs used isolated fixtures. After implementation, one UI assertion
incorrectly rejected an existing read-only permission_mode_status query; correcting
that assertion preserved the no-write check. Combined UI/file/Studio delivery
checks passed 82 tests with two existing warnings in 8.23 seconds. Actual-source
new/existing repeat-guard checks passed 28 tests in 0.33 seconds.

The budget author's initial 242 passing tests missed two independent-review
findings: frozen display timing also froze queued-filing enforcement, and a later
SDK success could hide a budget denial. Reproductions failed before repair. Both
are corrected, including approval callbacks unwinding after foreground completion.
The author then passed 260 tests and one additional Engine→SDK outcome regression.
The fresh reviewer passed 252 tests in 13.89 seconds and reported no remaining
blocker. Reviewed source hashes stayed unchanged. Earlier sandbox thread hangs
were interrupted; an incorrect test filename ran no tests before correction.

Final root integration: **390 passed, 2 warnings in 13.82 seconds**, exit 0, using
`timeout 120 .venv/bin/pytest -q` with runtime approval/profile/settings, loop budget,
turn timing, permission hardening, SDK completion, Engine bridge, HTTP interruption,
timing integration, background filing/usage, idle work, local vision, repeat guard,
loop guard, budget UI, private preview, native file, Studio and desktop delivery
modules. Tests were model-free; isolated browser fixtures ran outside the sandbox
with GPU disabled. Warnings are existing Starlette/httpx and AnyIO deprecations.
Evidence: /tmp/dream-recovery-final.log; final source/test hashes:
/tmp/dream-recovery-final-hashes.json. Author evidence:
/tmp/dream-approval-budget-review-final.log and
/tmp/dream-approval-budget-engine-sdk.log. Earlier failures remain in
/tmp/dream-budget-ui-red.log, /tmp/dream-budget-ui-red2.log,
/tmp/dream-budget-ui-green.log (contains the corrected fixture assertion failure),
/tmp/dream-repeat-guard-red4.log and /tmp/dream-recovery-ui-integration.log.

Graph discovery was attempted first; root calls still returned Transport closed.
Focused reads followed that failure. No owner runtime restart, configuration edit,
model inference, Blender probe/render, benchmark or public write occurred.

Tracking check passed: 54 dated records and all 23 session-changed source paths.
All 13 final source/test hashes and all six budget-author reviewed hashes match.

## Unfinished work

Owner live acceptance remains pending. New Python behavior needs a new Dream
process after the current job finishes. Runtime checks remain cooperative at
existing boundaries; they do not forcibly stop an already-running command or native
provider generation. Studio draft guidance and image transport tests do not prove
that this model will present correctly or accept actual images. Host sandbox
availability, session scope/approval ambiguity and full tool-log retention remain
separate audit follow-ups. No claim of a perfected or production-qualified harness.

## Next steps

Let the current DeepSeek job finish, then restart Dream and check the repaired
budget, actual image inspection, rerender recovery and visible Studio delivery in
a small owner task. No unattended monitor or test process remains. Preserve the
dirty tree, failed evidence and existing public candidate cbdb38e; this change was
not published and is not included in that earlier candidate.

## Files changed

- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-11-runtime-tool-recovery.md`

- `dream/telemetry/runtime.py`
- `dream/core/profiles.py`
- `dream/core/engine.py`
- `dream/core/backends/openai_compat.py`
- `dream/core/backends/anthropic.py`
- `dream/tools/files.py`
- `dream/tools/studio.py`
- `dream/gui/static/controls.js`
- `tests/test_runtime_approval_budget.py`
- `tests/test_repeat_guard_artifacts.py`
- `tests/test_native_files.py`
- `tests/test_preview_presentation_guidance.py`
- `tests/test_runtime_budget_ui.py`
- `skills/media/SKILL.md`
- `skills/media/references/operations.md`
- `docs/runtime-controls.md`
- `docs/capability-contract.md`
- `docs/durable-runs.md`
- `docs/media.md`
- `README.md`
