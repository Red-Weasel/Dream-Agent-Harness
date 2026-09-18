# DREAM-034 — Council selection, handoff and effort controls

Recorded: `2026-09-10T21:56:04Z`
Work items: `DREAM-034`
Outcome: `implemented`
Actor: Codex lead with council_core, council_startup and council_ui contributors;
council_review performed independent read-only review.

## Request

Owner approved restoring Council: choose a main/orchestrator, bring advisors into
the running conversation, ask one or all, and hand off the main between turns
without losing Dream-visible history/workspace. Owner additionally requested an
effort setting; the implementation includes main and per-advisor effort.
Acceptance is in [MASTER_PLAN](../MASTER_PLAN.md#dream-034-acceptance--council-selection-and-session-continuity).
This build follows the [investigation](2026-09-10-council-restoration.md), whose
record remains unchanged. It does not repair or unlock the earlier active-writer
Codex session: that session was still active during the read-only investigation.

## Changes

- Startup restores optional advisor selection alongside the chosen main, model
  and supported effort. Same-provider main and advisor instances may use different
  models. Local main effort stays in existing Advanced settings. Existing model
  attach/load paths and saved advanced settings are preserved.
- Studio Council offers main/model/effort, enabled advisors with their own model
  and effort, concurrency and timeout, Apply, and Ask one/all. Attributed answers
  join the existing transcript and reach the main agent's next turn. Unsupported
  effort is explained; provider availability is local prerequisite metadata, not
  authenticated readiness. Selecting advisors alone invokes no models.
- Authenticated runtime controls queue structured actions on the App lifecycle
  task and reject conflicts. Client disconnect cannot cancel a half-completed
  handoff. Consultation is interruptible; configuration hides Stop. Shutdown
  settles queued controls and closes admission. Explicit rounds receive fresh
  attributed budgets instead of a previous main turn's expired meter.
- Engine preserves session/store/workspace, sends bounded labeled visible history
  to the new main, and retains advisor context. SDK scope cleanup is ordered;
  failed connect attempts restore the old backend. Uncertain cleanup or failed
  rollback marks the main unavailable, blocks work and visibly requires a new
  application. Bridge limits update between turns without replacing its ledger.
- Effort reaches supported CLI/SDK/HTTP adapters. Codex preserves native low and
  maps legacy terminal med to medium and max/ultra to xhigh. Unsupported Grok/
  Gemini changes fail before mutating state. Claude terminal effort uses lifecycle
  reconfiguration. Panel state reflects the current terminal effort selection.
- Strict additive MoeConfig fields preserve older saved selections. Read-only
  advisor restrictions remain. Existing legal-review mode is retained; the panel
  explains its source-artifact requirement and disables Ask instead of bypassing it.

See [ADR-014](../DECISIONS.md#adr-014--council-controls-share-the-app-lifecycle-2026-09-10),
runtime controls,
desktop and the completed
implementation plan.
Baseline: `/tmp/dream-council-build-20260910-baseline.json`. All changes remain
uncommitted, distinct from the extensive dirty work present at that snapshot.

## Validation

Observed on 2026-09-10 around 21:52–21:55 UTC, using the existing virtualenv:

```bash
timeout 90s .venv/bin/python -m pytest -q \
  tests/test_council_config.py tests/test_council_handoff.py \
  tests/test_council_effort.py tests/test_council_runtime.py \
  tests/test_council_startup.py tests/test_moe.py tests/test_moe_tools.py \
  tests/test_council_evidence.py tests/test_cli_agent.py \
  tests/test_native_env_scrub.py tests/test_sdk_review_streaming.py \
  tests/test_desktop_startup.py tests/test_picker.py \
  tests/test_desktop_protocol.py tests/test_tui_new_commands.py \
  tests/test_council_ui.py tests/test_studio_controls.py \
  tests/test_feed_verbosity.py --tb=short
```

**272 passed, 3 warnings in 34.36s.** Warnings are existing Claude SDK evaluator
read-tool allow rules shadowing callbacks; the focused suite includes SDK review
scope checks. CPU fixtures and GPU-disabled Chromium used temporary state and
mock provider transports, outside the sandbox after its async-thread wakeup
hang was independently reproduced. No live model/provider request was used.
This final run includes the late effort alias, shutdown and disconnected-client
regressions. Earlier contributor runs overlap this suite and are not additive.

Observed: `node --check dream/gui/static/council.js`, Python compileall for all
changed application Python modules, and `git diff --check` on changed source/test
paths passed. Startup contributor separately parsed GTK CSS successfully and ran
53 startup/picker cases. Studio contributor ran 42 browser cases; 12 cover Council,
including unavailable main, Stop response accuracy, persistence, hostile text and
responsive controls. Lead inspected the desktop fixture screenshot; browser
screenshots remain at `/tmp/dream-council-desktop.png` and
`/tmp/dream-council-mobile.png`, not committed.

Failed/unavailable checks: TDD runs reproduced missing controls, effort propagation,
rollback/cleanup issues, stale budget, effort drift, unsupported effort mutation,
oversized integer timeout, misleading Stop and shutdown admission before fixes.
The final suite above is green. An initial combined command misspelled
`test_tui_new_commands.py`; collection stopped with no tests and was corrected.
Core also corrected an early nonexistent test filename. `uv run --locked` was
unavailable because its existing cache lock was not writable; no dependency
installation was attempted. Bounded sandbox fixture runs timed out or were
interrupted after confirmed thread wakeup stalls. Two escalation review attempts
timed out; explicitly permitted retries succeeded. Graph calls intermittently
stalled, including a final search_graph attempt terminated without useful output;
previous indexed discovery and focused source reads were used. Two early fixture
transcript artifacts were removed by the core contributor after exact-content
verification; later fixtures isolated their session paths.

Tracking initially caught a CURRENT timestamp preceding the new handoff; both
were corrected. `python3 scripts/check_project_tracking.py check --snapshot
/tmp/dream-council-build-20260910-baseline.json` passed: 22 dated records and
29 changed source paths checked. No earlier handoff was edited.

## Unfinished work

None within the approved implementation scope. Native GTK/WebKit interaction,
live provider authentication/model support, inference quality, actual GPU/model
residency behavior, packaging/clean install and owner acceptance remain unperformed.
They are not established by fixtures. No GPU probes, model loads, live prompts,
owner-process shutdown, release or publication occurred. No owned test process
or fixture lock remains. The owner's no-model/no-GPU hold is unchanged.

The existing Dream application cannot gain new Python lifecycle controls through
a static page refresh. Use a newly started Dream application; the running one was
left untouched. If a future handoff reports incomplete cleanup, open a new
application as instructed rather than retrying an uncertain backend transition.
The feature transfers bounded visible context, not provider-private state, and
offers one advisor per provider. Local concurrency still obeys existing request
coordination. Legal-review consultation still needs vetted source artifacts.

## Next steps

Owner can use the next normally started Dream application to inspect Council,
select main/advisor effort and evaluate the workflow. DREAM-034 native/live
qualification and acceptance remain pending; no benchmark or model operation is
authorized by this handoff. Resume from CURRENT and this record with a fresh
snapshot, preserve dirty work, and re-check actual provider state before any
recovery action. DREAM-033 findings and DREAM-012/018 packaging work retain their
separate scope.

## Files changed

- `docs/desktop.md`
- `docs/runtime-controls.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/DECISIONS.md`
- `docs/project/updates/2026-09-10-council-controls.md`
- `docs/superpowers/plans/2026-09-10-council-controls.md`
- `dream/core/backends/anthropic.py`
- `dream/core/backends/cli_agent.py`
- `dream/core/council_config.py`
- `dream/core/engine.py`
- `dream/core/moe.py`
- `dream/desktop/onboarding.py`
- `dream/desktop/startup.py`
- `dream/desktop/style.css`
- `dream/gui/static/council.css`
- `dream/gui/static/council.js`
- `dream/gui/static/index.html`
- `dream/tools/moe_tools.py`
- `dream/tui/app.py`
- `dream/tui/council.py`
- `dream/tui/picker.py`
- `tests/test_council_config.py`
- `tests/test_council_effort.py`
- `tests/test_council_handoff.py`
- `tests/test_council_runtime.py`
- `tests/test_council_startup.py`
- `tests/test_council_ui.py`
- `tests/test_picker.py`
