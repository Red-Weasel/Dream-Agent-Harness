# DREAM-014 — Hard visual tasks and corrected rocket prompt

Recorded: `2026-09-12T19:08:10-05:00`
Work items: `DREAM-014`
Outcome: `planned`
Actor: Codex lead; fresh Astra hard_visual_review, read-only evaluation review.

## Request

Replace easy simulator-only exercises with demanding saved-artifact tasks:
portrait recreation in Paint and a detailed Falcon 9 animated lunar journey.
Owner then asked to correct the rocket prompt. Acceptance: concrete tasks,
artifact criteria, fair skill/model comparisons and truthful readiness/results.

## Changes

Prepared a selected public portrait source, Paint prompt and weighted likeness,
proportion/shading/control/finish rubric. Added a Falcon 9 Block 5 reference brief,
complete 60-second film prompt, source/playback checks and revision follow-up.
Corrected the rocket premise per the owner's follow-up: booster recovery stays
on Earth; a conceptual lunar-return spacecraft makes a lunar flyby and returns a
capsule by reentry/parachute splashdown. Public exterior dimensions/hardware are
sourced to SpaceX's May2025 guide. No claim the conceptual payload is qualified.

Comparison protocol separates model ranking from same-model skill transfer,
uses anonymous independent artifact judges and records tools, effort, resources,
failures and interventions. Added an unscored JSON run template. No runtime
limits, production code, skill behavior, training or app installation changed.

## Validation

Fresh Astra reviewed the design and identified the same stroke-tool gap and
mission-premise issue; incorporated explicit setup status and corrected mission.
Read-only PATH check found Claude/Codex/Blender/xdotool and X11, but no common
native Paint applications on PATH. Catalog source lists Fable5.1; that does not
verify endpoint access. Root code graph returned Transport closed; focused
catalog source read followed. One guessed model_presets.py search path was absent;
model_catalog.py supplied the actual alias evidence.

SpaceX PDF text was retrieved through web tools; public portrait source page was
retrieved. Sandbox image download failed DNS; authorized host page and direct
image downloads returned HTTP403. No portrait pixels, crop or checksum were
claimed inspected. No native browser controls, model calls, drawing or render
was run. No inference cost incurred by evaluation runs; review agent usage is
separate. Run-template JSON parses; both weighted rubrics total 100. Fresh Astra reviewed
the final five task files with no blockers. Initial tracking failed because the
UTC timestamp crossed midnight while the filename used the local date; corrected
the record to its equivalent America/Chicago timestamp, preserving the instant.
Final tracking/link check passed: 74 dated records and 9 changed source paths
checked against `/tmp/dream-hard-visual-baseline.json`.

## Unfinished work

Actual portrait/animation comparisons remain unperformed. Shared strokes and
canvas/export support, actual Paint environment and exact Fable/image/tool access
must be qualified. Reference photo bytes and additional official rocket exterior
views remain unfrozen. No sealed holdout has been selected. No generated artifact
or model winner/skill improvement is claimed. The bounded read-only reviewer completed; no recording/model/render was started.

## Next steps

DREAM-014: qualify a disposable drawing route with drag/stroke/undo/export before
fair Paint trials, obtain identical reference bytes, verify exact model readiness,
then run a first diagnostic pair. Use the corrected rocket brief for animation.
Keep local-model loads deferred. Preserve prior dirty source and existing owner
sessions. Source-only documentation work needs no production regression rerun.

## Files changed

- `docs/evaluations/visual-craft/README.md`
- `docs/evaluations/visual-craft/portrait.md`
- `docs/evaluations/visual-craft/falcon9.md`
- `docs/evaluations/visual-craft/comparison.md`
- `docs/evaluations/visual-craft/run-template.json`
- `docs/public/teaching-skills.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/CURRENT.md`
- `docs/project/updates/2026-09-12-hard-visual-tasks.md`
