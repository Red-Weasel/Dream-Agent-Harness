# DREAM-023 — Dream workspace and external-tool preference

Recorded: `2026-09-05T12:33:55-05:00`
Work items: `DREAM-023`
Outcome: `proposed`
Actor: Codex, requirements documentation.

## Request

The owner clarified that everything should ideally happen inside Dream, while
external creative tools and browsers are acceptable when useful. The owner
specifically mentioned Blender and reminded us that Dream already has a browser.
No additional software purchase is acceptable. Acceptance for this session:
record this answer to the prior handoff's open workflow question, preserving the
existing subscription/no-key/no-additional-spend requirements.

## Changes

Refreshed CURRENT, MASTER_PLAN and HISTORY with the clarification. Dream remains
the intended home for conversation, project state, previews, edits and assets.
Reuse its existing Browser tab for suitable subscription workflows. External
applications or a system browser can be considered when they add useful
capabilities or are needed for compatibility. Blender is a candidate named by
the owner, not an installed or verified Dream integration.

For the upcoming design, include a way to return external results to the same
project with editable sources retained where supported. The owner's acceptance
of these workflows does not prove automation support in any provider or app.

Read docs/desktop.md: the current general browser uses an ephemeral web context,
clears browsing state on close and has no host scripting bridge. Subscription
sign-in persistence, browser control and download/import need explicit design
and verification. The existing browser is useful infrastructure, not evidence
that signed-in automated media production already works.

## Validation

Inspected git status and saved a 373-path baseline using
`python3 scripts/check_project_tracking.py snapshot --output
/tmp/dream-media-workspace-20260905.json`. Reviewed current tracking and the
desktop browser documentation. Only four tracking documents changed; prior
handoffs and runtime work remain intact. Observed:
`python3 scripts/check_project_tracking.py check --snapshot
/tmp/dream-media-workspace-20260905.json` passed, with five dated records and
all four session-changed paths covered.
No browser/app launch, installation, account action, generation, renderer,
model load, external research or runtime test was performed.

## Unfinished work

The product question about allowing browser/external-tool handoff is resolved.
Pineapple check: provider login compatibility, automation, local workload support
and external asset return remain unverified. No new process requires cleanup or
reconciliation. No detailed implementation specification is approved by this
requirements update. DREAM-024 stays deferred and DREAM-025 stays a horizon goal.

## Next steps

Prepare the staged DREAM-023 design and implementation plan when requested,
using the recorded requirements and delegated defaults. Include existing-browser
reuse, external-tool integration, sign-in/session handling and asset return.
Keep provider-specific feasibility checks explicit. Resume from CURRENT and
take a fresh snapshot before subsequent edits.

## Files changed

- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/HISTORY.md`
- `docs/project/updates/2026-09-05-media-workspace.md`
