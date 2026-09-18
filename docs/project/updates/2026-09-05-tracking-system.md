# DREAM-011 — Repository onboarding and durable development tracking

Recorded: `2026-09-05T09:04:41-05:00`
Work items: `DREAM-011`
Outcome: `verified`
Actor: Codex, executing this documentation/tracking session. No Fable execution is claimed.

## Request

The owner requested an intuitive repo-wide tracking system: current work, master
planning, dated history, future horizon, clear onboarding for an unfamiliar human
or model, and instructions that require contributors to leave a complete handoff.
This explicitly authorized the tracking rules added here. Existing owner
execution/model governance remains unchanged.

## Changes

- Added the root [front door](../../../START_HERE.md) and
  [shared agent entry](../../../AGENTS.md). README and CLAUDE point to one hub.
- Reconciled the September implementation into a [22-item master register](../MASTER_PLAN.md):
  baseline capabilities, real remaining acceptance, Gemini's account blocker,
  evaluation/local-model work, legal Council readiness, release preparation and
  a separate deferred global Claude cleanup audit.
- Added current state, workflow, an attributed decision log, history index and
  handoff template. Historical plans remain at their paths with visible banners;
  twelve old documents are labeled without rewriting their original bodies.
- Corrected stale entry guidance: bare Dream is the picker/terminal; desktop is
  explicit; signed-in Grok and xAI HTTP are distinct; locked installation is the
  development path; memory facts do not replace SQLite sessions/tasks; custom
  tools require reviewed source. Preserved the README origin story and labeled
  the August benchmark as historical.
- Added a standard-library checker with source snapshots for dirty trees, exact
  changed-path coverage, current/master alignment, timestamps, work IDs,
  portable local link targets, and append-only handoff checks.
- Added a lightweight tracking CI job and PR template. Bootstrap/manual cases
  explicitly report when only structure is checked. No remote setting was changed.
- Classified the existing root prompt drafts as unassessed inputs; did not load
  them as repo instructions or claim their product/model/tool descriptions are facts.

Design rationale: [ADR-006](../DECISIONS.md). The hub is human-readable Markdown;
no hosted account, extra dependency, runtime hook or giant prompt injection is
required. The checker catches bookkeeping omissions, while reviewers still judge
accuracy and completeness.

## Validation

Observed this session:

- `python3 -m unittest discover -s tests -p 'test_project_tracking.py' -v`:
  **12 passed**. Fixtures include missing records/current-state updates, unknown
  IDs, missing validation sections, timezone errors, stale handoffs, undocumented
  paths, rewritten/deleted history, and real temporary-Git source comparisons.
- Byte-for-byte comparison of `CLAUDE.md` from the original behavioral-guidelines
  marker through all owner governance: **unchanged**. Only development orientation
  and the explicitly requested tracking rules were changed above that marker.
- A pre-edit source-hash snapshot captured **357 files** and distinguished this
  session's changes from the large pre-existing dirty tree. The snapshot is local
  at `/tmp/dream-tracking-session-baseline.json`; it is not a source backup.
- `python3 scripts/check_project_tracking.py check --snapshot /tmp/dream-tracking-session-baseline.json`:
  **passed**, one dated record, **28 changed source paths covered**, including
  new/untracked docs, this record and current-state updates. Structural checks
  also validated the work register, timestamps and local onboarding/hub links.
- `python3 scripts/check_project_tracking.py check --base HEAD`: **passed with
  explicit bootstrap notice** because the old commit predates this tracking
  system. It did not pretend to validate all earlier uncommitted runtime changes.
- Workflow YAML parsed successfully in the existing venv. Both tracking and
  regression jobs are present; tracking checkout fetches comparison history and
  retains read-only permissions with persisted credentials disabled. This is
  configuration verification, not an observed GitHub Actions run.

Graph discovery was attempted; the MCP service did not return usable results.
Documentation/configuration reads and focused fallback inspection were used.
The first baseline command used unavailable `python`; it was rerun successfully
with `python3` before any edits. No code/model test was represented as passing
because that command failed.

The prior 1,697-pass runtime suite is recorded in current state as **previous
September-build evidence**, not a suite run for this tracking task. Runtime,
GPU, live-provider, live-screen-capture and full regression tests were not rerun
because this task changes docs and tracking only. Remote CI has not been run.

## Unfinished work

None within this session's implementation scope. DREAM-011 is verified and no
task remains active. Owner acceptance of the broader harness and a production
release remain unrecorded. The new workflow is local/uncommitted; remote checks
and branch protection are not active merely because files exist here. Automated
checks validate structure and changed-path coverage; reviewers still need to
assess the accuracy and completeness of the prose.

The original working tree remains uncommitted (`HEAD` was `1be2201` at start).
No staging, commit, push, release, model load, credentials change, global Claude
cleanup, or private runtime modification was performed. Existing prompt drafts
and runtime source were preserved. This task owns no running service or lock.

## Next steps

Use DREAM-012's concrete desktop acceptance protocol for the next owner-guided
trial. DREAM-013 requires eligible Gemini account access. DREAM-018 owns source
review/clean delivery and any later approved remote CI/release process. Horizon
items remain proposals, not autonomous instructions to start unrelated work.

A new contributor should read current state and this handoff, inspect Git status,
and take a fresh snapshot before editing. Do not reuse this session's baseline
as the start of an unrelated future session.

## Files changed

- `.github/PULL_REQUEST_TEMPLATE.md`
- `.github/workflows/harness.yml`
- `AGENTS.md`
- `CLAUDE.md`
- `README.md`
- `START_HERE.md`
- `docs/harness-architecture.md`
- `docs/harness-os-plan.md`
- `docs/project/CURRENT.md`
- `docs/project/DECISIONS.md`
- `docs/project/HISTORY.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/README.md`
- `docs/project/WORKFLOW.md`
- `docs/project/templates/update.md`
- `docs/project/updates/2026-09-05-tracking-system.md`
- `docs/specs/2026-07-03-dream-harness-design.md`
- `docs/specs/2026-07-05-cockpit-council-launcher-design.md`
- `docs/specs/2026-07-05-phase1-foundations-plan.md`
- `docs/specs/2026-07-05-phase2-cli-backends-plan.md`
- `docs/specs/2026-07-05-phase3-moe-plan.md`
- `docs/specs/2026-07-05-phase4-cockpit-plan.md`
- `docs/superpowers/plans/2026-09-04-dream-desktop.md`
- `docs/superpowers/plans/2026-09-04-integrated-harness.md`
- `docs/superpowers/specs/2026-07-08-local-subagents-design.md`
- `dream Project status 9-4-26.md`
- `scripts/check_project_tracking.py`
- `tests/test_project_tracking.py`
