# DREAM-052 — Image-inspired design plan

Recorded: `2026-09-12T00:04:43-05:00`
Work items: `DREAM-052`
Outcome: `planned`
Actor: Codex lead; fresh Astra design_plan_review reviewer (read-only).

## Request

Owner requested a plan to improve Dream's aesthetics and consider harness
experience enhancements, supplying Home, Studio, Terminal and Chat inspiration
images. Owner explicitly clarified that these are guides, not exact layouts.
Acceptance: concrete visual direction and tokens, prioritized screen work,
implementation touchpoints, real-data/backend dependencies and validation gates.
Planning only; no implementation, asset generation or application mutation.

## Changes

Created visual-direction specification and phased delivery plan under the existing
DREAM-052 register. Palette, type, layout, artwork and motion treatments retain
Dream identity while keeping reading/work surfaces quiet. One primary navigation,
persistent workspace/model/effort/mode and actionable errors take priority over
ornamental dashboard features. Chat/Studio first, navigation/Home next, then
inspector and separately scoped memory/Council/artifact enhancements.

Plan maps existing GTK3/VTE/WebKit and vanilla web modules, preserves current
APIs, permits only explicit future allowlisted navigation messages, and avoids
framework migration. Unknown/stale telemetry remains honest. Current Council's
sequential behavior is not represented as simultaneous editing. No fake progress,
project samples or memory gauges copied from reference images. Asset creation,
licensing and native compatibility are implementation tasks, not completed work.

The supplied images were visually reviewed from the conversation. They were not
copied into source or used as whole-page backgrounds. The plan/spec retain
September11 filenames because drafting began before midnight; this handoff uses
the actual September12 completion date.

## Validation

Read current docs and relevant source boundaries. Lead graph search failed with
Transport closed; focused source reads used instead. Verified22 explicitly named
existing source/test paths; new proposed files are labeled. No code, GPU, model,
renderer or browser was launched for this planning session. No tests are claimed
for an unimplemented UI. Existing dirty tree preserved via
/tmp/dream-design-plan-baseline.json (639 source hashes).

Fresh-context Astra review found one concrete rollout gap: native GTK/WebKit tests
were scheduled too late despite early GTK CSS and Studio playback changes. Plan
now requires native fixture checks or explicitly pending native acceptance in the
first Chat/Studio slice. Reviewer otherwise found the plan ready for owner review.
Lead checked reference mapping, priorities, current-vs-proposed features, known
file paths and native/web ownership. Final snapshot tracking passed:66 dated records,5 changed source paths checked. git diff --check also passed.

## Unfinished work

Design and implementation remain proposed. No UI reskin, new navigation, generated
artwork, fonts, mockup prototype, runtime feature, comparative timing measurement,
accessibility certification, live provider qualification or owner acceptance.
The proposed performance thresholds require a same-device baseline before use.
Concurrent resident Council editing, memory-save pipeline improvements and output
lineage remain backend work; drawing those controls does not implement them.

## Next steps

Owner review of the plan and visual direction. When implementation is requested,
start DREAM-052 Task1 and Task2 as a cohesive Chat/Studio slice; preserve the dirty
tree, verify existing behavior and qualify native GTK/WebKit from the first slice.
No running processes or locks owned by this planning task. No publication.

## Files changed

- `docs/superpowers/specs/2026-09-11-dream-visual-direction.md`
- `docs/superpowers/plans/2026-09-11-dream-design-upgrade.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/CURRENT.md`
- `docs/project/updates/2026-09-12-dream-design-plan.md`
