# DREAM-075 — Prompt Optimizer beside Chat

Recorded: `2026-09-13T18:07:14Z`
Work items: `DREAM-075`
Outcome: `implemented`
Actor: Codex lead integrated server/App lifecycle and documentation; Astra
qualification_fresh_review authored the service and reviewed root integration;
Astra steering_discovery authored UI and reviewed the service. Existing threads
were reused; these are non-author reviews, not fresh-context reviews.

## Request

Build a left-navigation Prompt Optimizer with optional reasoning guidance, selected
files, material clarifiers and the owner's six-section format. Research official
Astra/Fable 5.1 guidance. Keep it simple and usable beside Chat. Preserve dirty
work against `/tmp/dream-prompt-optimizer-baseline.json` (739 source hashes).
GPUs remain occupied; no live model calls, loads, benchmarks or owner restart.

## Changes

Prompt Optimizer opens beside Chat on desktop and fills the available content area
on narrow screens. Enter a draft and ideal outcome, attach registered files, and
optionally specify context, constraints, verification and source-only behavior.
Reasoning and target style affect writing guidance; actual model/effort controls
remain separate. The prepared prompt is editable. Copy and Use in Chat never Send;
existing chat text requires explicit Append, and attachment adoption is atomic.

Quick structure formats without inference or file content reads. Optimize uses an
isolated tool-free request to the current provider/model. Selected supported text
excerpts are bounded; images/unsupported documents stay labelled metadata-only.
The service validates response structure and terminal completion, not semantic
faithfulness. The original draft stays available for review. No fallback provider,
automatic Quick substitution, memory commit or additional project access is added.

The authenticated route checks origin, size, workspace/session and upload IDs.
App's existing serialized input loop owns optimization and Stop cleanup. Provider,
model, profile, session, workspace and task generation are checked before dispatch;
late responses cannot cross sessions or overwrite edited drafts. Completed
clarification answers survive subsequent refinements; unanswered questions do not
block submission. At most three retained answers are supported, with explicit
removal when making room for more.

See [ADR-051](../DECISIONS.md), the [operator guide](../../public/prompt-optimizer.md),
[research note](../../research/prompt-optimizer-guidance.md) and
implementation plan.

## Validation

- Graph discovery was attempted and failed with `Transport closed`. Focused named
  source reads followed; no claim of a current graph audit.
- Root route tests failed before implementation; initial service collection failed
  before the new module existed. Service review probes reproduced malformed output,
  incomplete HTTP terminals, conventional GOAL heading rejection and oversize
  Quick output before repair. Agent service gate:39 passed.
- Root integrated service/API/App/Council gate:85 passed in0.58s, synthetic transports,
  `/tmp/dream-prompt-optimizer-integration.log`. The restricted run stalled in
  existing threaded fixtures; only its identified pytest process was interrupted
  (65 passed before interruption), then the bounded host run passed. No Dream or
  model process was interrupted.
- Independent root integration review:22 existing tests plus2 private provider/
  workspace-switch probes passed, `/tmp/dream-prompt-optimizer-integration-review/`.
  No blocker found. Runtime-meter attribution was advisory, not implemented.
- Existing attachment/navigation/companion gate initially23 passed,1 failed because
  the older test still addressed removed duplicate Studio/Chat button labels.
  Updated its three selectors to the existing primary navigation. Recheck24 passed
  in17.55s, `/tmp/dream-prompt-optimizer-regressions-final.log`.
- Initial UI gate9 passed with real CPU Chromium and synthetic responses. Root
  visually inspected desktop1280 and mobile390 screenshots and caught an incorrect
  flex display on the retained Chat column; final repair/check is recorded below.
- Offline wheel built with uv; direct venv build failed because hatchling was not
  installed in that environment. New optimizer service/assets and touched UI bytes
  matched the first wheel. Final post-review packaging is recorded below.
- Final integrated gate: **119 passed in29.44s**, all9 affected service/API/App/
  Council/attachment/navigation/companion/UI test files. CPU-only headless Chromium,
  fake provider transports; `/tmp/dream-prompt-optimizer-final.log`.
- Final UI gate10 passed, including a real browser-to-StudioServer Quick request
  and registered upload, with the provider callback forbidden and task callback
  untouched. Root inspected the repaired1280 screenshot and earlier390 screenshot.
  Evidence: `/tmp/dream-prompt-optimizer-ui/` and
  `/tmp/pytest-of-<user>/pytest-2043/test_navigation_keyboard_and_r0/`.
- Independent non-author service review:8 private probes passed, no blockers in
  examined scope. File confinement, unsupported content, output validation,
  terminal outcomes and cancellation cleanup were challenged.
  `/tmp/dream-prompt-optimizer-independent/`. These do not measure model quality.
- Final offline wheel rebuilt after the CSS repair. All8 changed package files
  matched source bytes, `/tmp/dream-prompt-optimizer-wheel/report.json`.
  No installation or publication performed.
- Tracking: `.venv/bin/python scripts/check_project_tracking.py check --snapshot
  /tmp/dream-prompt-optimizer-baseline.json` passed:81 dated records,21 changed
  source paths. Earlier checks correctly required a new handoff and latest-link
  reconciliation before passing. All changes stay within this feature's listed
  scope and retain the pre-existing dirty tree.

## Unfinished work

None within the implemented feature scope. Live model prompt quality and native
desktop acceptance remain untested. Browser navigation does not cancel the owned
provider request; use Stop. Shared HTTP transports can retry transient failures.
No new private memory store or public export was created.

Separate DREAM-014 rocket work continues in its owned :95 CPU/software-GL desktop
and private trial directory. Worker reports checkpoint19 and a decoded60-second
480x270 timing animatic; these are not newly verified final-quality deliverables
by this feature's lead. Read that worker's current notes before any takeover.

## Next steps

Owner can use the feature
after a normal Dream restart when convenient. Model-quality comparisons remain
deferred until an available model and normal usage supply evidence. No publication,
owner acceptance or restart is recorded or implied.

## Files changed

- `README.md`
- `docs/project/CURRENT.md`
- `docs/project/DECISIONS.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-13-prompt-optimizer.md`
- `docs/public/prompt-optimizer.md`
- `docs/research/prompt-optimizer-guidance.md`
- `docs/superpowers/plans/2026-09-13-prompt-optimizer.md`
- `dream/gui/server.py`
- `dream/gui/static/attachments.js`
- `dream/gui/static/index.html`
- `dream/gui/static/prompt_optimizer.css`
- `dream/gui/static/prompt_optimizer.js`
- `dream/gui/static/workspace.js`
- `dream/prompt_optimizer.py`
- `dream/tui/app.py`
- `tests/test_chat_attachments.py`
- `tests/test_prompt_optimizer.py`
- `tests/test_prompt_optimizer_lifecycle.py`
- `tests/test_prompt_optimizer_routes.py`
- `tests/test_prompt_optimizer_ui.py`
