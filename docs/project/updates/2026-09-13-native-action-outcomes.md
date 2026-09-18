# DREAM-014 / DREAM-061 — Rocket evidence and native action outcomes

Recorded: `2026-09-13T06:38:43-05:00`
Work items: `DREAM-014, DREAM-061`
Outcome: `implemented`
Actor: Codex lead; Astra continuation executor; Astra implementation and independent review agents.

## Request

Owner requested rocket status, then explicitly continued harness tuning. Acceptance:
verify actual outputs/status, resume authorized Astra mission after a supervision
gap, reproduce misleading native post-action errors, repair the narrow outcome
contract with focused checks and fresh independent review. No benchmark runs.

## Changes

Lead owns tracking/operator docs and output verification. Implementer owns only
dream/computer.py and tests/test_computer_action_outcomes.py. Fresh verifier owns
private evidence only. Active rocket worker remains on a pinned controller in its
isolated desktop; source changes do not hot-replace that controller. Existing dirty
tree preserved against /tmp/dream-rocket-status-sep13-baseline.json.

Root-cause inspection found Computer.act combines dispatch and subsequent
observation in one exception path. Actual SaveAs closes its dialog, then capture
fails, causing a misleading action-failed result despite a saved checkpoint.
Implemented contract: separately report dispatched input, missing after-observation
and unverified application outcome; no replay, implicit retarget, or cancellation
swallowing. Normal successful result shape remains unchanged. Partial results have
bounded observation_error, null screenshot/token and action_dispatched=true.
Clear any partially created reference after failed observation. Independent review
also reproduced a null-token bypass after a consumed observation; empty/null tokens
are now refused before dispatch. Updated wrapper description and operator/skill
route documentation to match the actual contract.

Graph search found no computer implementation symbols; graph search_code located
dream/computer.py. Tool-wrapper symbol graph worked. Focused reads used for the
missing implementation symbols. This is an indexing limitation, not proof that
the repository lacks the implementation.

## Validation

Opus model result reports a completed turn after1757turns, with1755controller calls
and last activity at07:10:36Z. It saved f9_lunar_mission.mp4 (4087601bytes),
f9_lunar_animatic.mp4 and editable f9_lunar_rec_v2.blend. Root ffprobe confirmed
1920x1080 H264,30fps,1800frames,60seconds; ffmpeg full decode completed exit0.
Root inspected frames at5/30/55seconds: small subjects, crude geometry/materials,
poor framing. No cinematic-quality or full-brief acceptance claim. Opus itself
reported unanimated landing legs, weak deep-space framing, detached plume ramps
and unfinished materials; these remain actor reports until inspected individually.
Hash-verified review copies are in the owner's private Desktop area under
Dream Rocket Recordings/Opus/2026-09-13-output. No Git publication.

Astra completed only a launch blockout with keyframes1/250 and a tracking camera.
Saved checkpoint986860bytes, SHA256
9a98ebfa55e5c265b918e7b81080abe1008d857d1e3eab03cd014e614961e0b2.
It stopped at the lead-assigned checkpoint at05:08Z. Lead failed to issue the next
work block; this was a supervision gap, not model runtime exhaustion or productive
overnight work. Followup sent to resume the mission, preserve idle recording and
use a new recording prefix. Worker subsequently confirmed actual BlenderPID2618273 and saved02title,
issued fresh observation/input and resumed mission work. It stopped the exact
old recorder after PID/argv verification and started a new nonoverwriting
session2 prefix. Gap05:08Z toapproximately11:40Z is marked idle. A read-only
systemctl status call hung and was canceled; no service exit status inferred.

Additional repair validation:

- Before repair:3targeted cases failed/8passed. Reviewer's null-token case then
  failed1/14passed before the guard was corrected. Both failures preserved.
- Author new tests15passed; existing controls plus new outcomes114passed in93.68s
  under approved host execution. Earlier restricted browser fixtures could not
  bind localhost; a loose-selection run stalled and was interrupted with exit130.
  These were incomplete/failed runs, not reported as passes.
- Fresh reviewer17private probes passed across native action types, wrapper image
  absence, desktop/browser normal/partial outcomes, token replay, stale state,
  dispatch/typing failures and cancellation. Its first probe error used the wrong
  error-envelope spelling, then was corrected. Author15tests independently passed.
- Actual isolated:97 Xvfb/Tcl fixture: before and after clicks both wrote an
  acceptance receipt and destroyed their window. Baseline produced generic uncertain
  BadDrawable; repaired source returned dispatched=true/unverified with missing
  screenshot/token and structured error. No fallback target, replay or live-trial
  interaction. All fixture processes exited0; cleanup report retained.
- Reviewed computer source SHA256:
  3f1916671c0039acb7173f2a98f7a8e36bda24130a9db3abbb6dfb0c46d08644.
  Author log /tmp/dream-action-outcomes-suite.log; independent evidence under
  /tmp/dream-native-transition-review/. No full repository suite or new model
  benchmark run. Tracking initially rejected multi-ID formatting; fixed to the
  required single field. Final tracking check passed:78dated records,8changed source paths.

## Unfinished work

Both artifacts' full brief acceptance and skill uplift remain unqualified.
Native outcome repair is locally implemented and reviewed; no owner daily-use
acceptance or active-controller replacement. Astra resume/idle recording handling
is verified, with fresh controller call82 at11:42:26Z. Never infer
progress from a retained desktop/controller or recorder alone.

Private evidence directories remain /tmp/dream-blender-trial/ for trials,
/tmp/dream-opus-output-review-sep13/ for sampled video frames, and
/tmp/dream-native-transition-review/ for independent repair checks.

## Next steps

DREAM-061: owner normal restart loads repaired source; inspect genuine task
outcomes after closed dialogs. Production daily-use acceptance remains open.
DREAM-014: verify resumed Astra action stream and mission milestones at no more
than30minute intervals during active supervision; preserve checkpoints and report
any pause explicitly. No blanket autonomous overnight-monitoring claim.

## Files changed

- `dream/computer.py`
- `dream/tools/computer_tools.py`
- `tests/test_computer_action_outcomes.py`
- `docs/public/computer-controls.md`
- `skills/computer-use/references/tool-routes.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-13-native-action-outcomes.md`
