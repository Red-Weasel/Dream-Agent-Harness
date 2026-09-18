# DREAM-014/061 — Real visual trials and shared pointer controls

Recorded: `2026-09-12T19:54:24-05:00`
Work items: `DREAM-014, DREAM-061`
Outcome: `active`
Actor: Codex lead; Astra canvas_controls implementation, stroke_review independent
review, visual_trial_preflight qualification and bridges, astra_portrait_trial drawing.

## Request

Continue the two distinct demanding computer-use tests: portrait recreation in
Paint and a detailed Falcon 9 animated mission. Qualify common controls, execute
actual tasks, preserve failures and distinguish model comparison from skill uplift.
The Blender primary track is GUI-only; code-generated scenes would measure a
separate productivity track. No local inference loads or owner-app restart.

## Changes

Added shared coordinate clicks, drag and strokes with bounded points, observed
state/pixel checks, retained browser hit nodes and cancellation-safe button release.
Independent review reproduced and repaired hover-driven target replacement.
[ADR-045](../DECISIONS.md) records the contract and limitations.

Qualified separate JS Paint contexts and a disposable native Blender desktop.
Private authenticated trial bridges expose the same adapter to Astra and Fable.
Paint export copies existing canvas pixels identically for both arms; it does not
synthesize artwork or qualify native Save As. Native window selection is confined
to the exact disposable Blender process. It is not an OS filesystem sandbox.
Reference pixels, prompts and bridge/adapter hashes are frozen privately. Public
docs retain source links and hashes rather than image bytes or raw model traces.

## Validation

Observed during this session: author pointer suite 44 passed after race repair;
independent retained race/cancellation probes plus suite 46 passed. Root focused
integration command `.venv/bin/python -m pytest -q tests/test_computer_controls.py
tests/test_tool_validation.py tests/test_policy.py tests/test_schema_budget_real.py
tests/test_tool_context_standalone.py --tb=short` passed 97 tests with two existing
async-mark warnings in 14.71s. Full suite and owner native daily-use acceptance
were not run. Previous full-suite results are not substituted for this gate.

Actual Chromium bridge qualification passed 11 checks. Exact requested and
provider-reported claude-fable-5-1 responded; actual reference-image/tool round-trip
then passed. Provider usage includes ancillary Haiku calls. Native bridge passed
seven contract checks and actual owned-window listing. Dream's X11 adapter captured
and clicked the isolated Blender Quick Setup; the normal splash was inspected.
Software GL/CPU is the isolated trial environment, not a recommendation that the
owner's machine has no GPU. Openbox was extracted privately, not installed.

Both portrait baselines are now performing actual drawing actions and have saved
intermediate PNGs. Fable's separate GUI-only Blender baseline is constructing scene
geometry. No final blind score, finished mission video or skill uplift is claimed.
Real Paint errors include unsupported CTRL spelling and stale help-text refusals;
Blender also exposes pixel-change refusals and render-window transitions. Diagnose
these without altering the frozen running arms. Root graph calls failed Transport
closed; focused reads followed. An initial `python` command was absent; the project
venv interpreter succeeded. A host-launch approval review timeout occurred before
execution; a separated permitted retry succeeded. No rejection remains outstanding.

Private evidence: /tmp/dream-visual-trial/, /tmp/dream-blender-trial/,
/tmp/dream-blender-preflight/, /tmp/dream-visual-preflight-20260913-001/.
Session baseline: /tmp/dream-live-visual-baseline.json. Midpoint source checks
confirmed both Paint arms still match the frozen adapter and bridge hashes.
Midpoint tracking passed: 75 dated records and 11 changed paths. Initial checks
caught current/active-item mismatch, the required handoff-label spelling and a
provisional timestamp mismatch; corrected before the pass. Recheck after later
changes.

### Integrated repair follow-up

Real diagnostic failures led to advisory screen settling, fresh observation fallback
for animation/missing-frame callbacks, complete browser-key prevalidation with
aliases and cancellation-safe key release. Author final control gate66passed75.70s;
independent final17probes passed24.37s against exact reviewed source. Actual JS Paint
stroke/CTRL+z changed stored canvas nonwhite pixels0→513→0. Viewport brush-preview
pixels were separately shown not to belong to stored artwork.

Root's first new integration run in the restricted environment had51pass/1skip/1fail:
a fresh-process helper timed out at10s and a sandbox capability was unavailable.
The same host integration gate passed53tests in3.08s with two existing warnings.
Logs: /tmp/dream-visual-controls-integration.log and
/tmp/dream-visual-controls-integration-host.log. Earlier candidate failures included
invalid Clear key acceptance, animation evidence loss, absent animation callbacks,
and a private-module test-patching error that attempted xdotool against synthetic
window123. The latter was stopped with default-deny native test I/O; no successful
owner-window action was observed. Preserve all diagnostic failures in the private
report rather than presenting only final passes.

Fable portrait attempt finished307calls/26errors and a finalPNG decoded by lead.
Its declared completion does not meet the full visual brief; independent blind
scores are pending. CLI reports$51.043287 list-price usage, not a verified charge.
Astra portrait and native Fable Blender continue. Read-only research and source
inspection distinguish SDK-managed history from Dream's explicit HTTP history;
no context-retention behavior was changed. See the observed-friction report.

Cleanup of the completed preflight server first hit an automatic review deadline
without execution; the permitted retry stopped exactly that verified server and
its process exited0. Active trial servers were not stopped. Production Computer is
35587318…a756b0. Existing servers retain original c86b25c7…5d35, and an independently
verified import-only launcher can load that archived module for the future Blender
counterpart. Dependency/config/lock hashes are recorded separately. No trial
bridge, prompt or archived source was modified.

## Latest trial and skill findings

Both portrait attempts finished with saved actual PNGs. Astra:637calls,329actions,
304explicit observations,3errors; Fable:307calls,262actions,30observations,26errors.
Lead inspected both final artifacts. Fresh blind reviewer viewed the reference and
anonymous outputs before artist identities/traces; its frozen scores were52.50/100
for Astra and33.75/100 for Fable. Neither met the full likeness brief. Preserve
this initial assessment, not a universal model ranking. Private packet:
/tmp/dream-portrait-blind-20260912/astra-initial-judgment.md.

The independent Fable judge returned provider session limit before producing a
review. The same provider limit interrupted Fable Blender after355calls; result
reports an API error and$65.18310525 list-price usage, not verified billing.
Checkpoint f9_lunar_v1.blend exists, but final animation/playback is unverified.
Provider reports reset at2026-09-12 21:40 America/Chicago. This is not a Dream
runtime cutoff. Preserve the failed result and use new output paths for a later
attempt; no model request retry before the reported reset.

Astra's separate GUI-only Blender attempt now runs on isolated :95, using the
same frozen original controls and official reference pack. Fable is preserved
on :94. The second desktop changes the prior sequential setup intention;
shared-host contention limits timing comparisons. Astra saved an early checkpoint
untitled.blend containing body/interstage work; it is not a finished scene.
Native repeated-character and crop-origin issues are under separate isolated
investigation, not established model errors or taught workarounds.

Fifteen private airbrush runs reproduced sparse deposition at1:1 zoom. This led
to optional paced strokes in the four previously scoped control files, currently
under independent review. Author gate82passed, followed by two paced/untimed
repeated-cancellation variants. Twelve actual new Paint cases demonstrated marks
through the middle of a200px path and stationary dwell, with original canvas PNGs.
A requested1000ms move took about1687ms including backend/check overhead. All marks
remained opaque black; density is not blending. Evidence:
/tmp/dream-airbrush-diagnostic/ and /tmp/dream-paced-stroke-evidence/.
No loaded baseline controller was changed.

Lead corrected skill tool routes and drafted a focused visual-craft reference,
plus Blender GUI field verification, full-sequence blockout and checkpoint guidance.
The evidence ledger separates reproduced failures from candidate artistic methods.
Both skill frontmatters validated. The user was explicitly informed that Paint
used headless Chromium running JS Paint, with no visible owner window and no
desktop Paint installation. Saved artifact links were provided. Initial routing check12passed/1failed showed
that adding the reference exceeded combined injection space. Shortened the shared
entrypoint; all13routing checks then passed with both workflows complete under the
unchanged4,000-character cap. This is packaging/routing evidence, not model uplift.
New source-authored skill content has not undergone held-out behavioral evaluation.
The unseen portrait stays sealed until skill and generic-checklist bytes are frozen.

## Final repair review and resumed work

Paced controls passed independent36private probes plus10selected regressions.
Untimed traces matched the prior integrated version. Native fixes subsequently
passed99control tests in98.64s, actual isolated4000-character entry, one-chunk
cancellation and nine Blender repeated/mixed-digit cases. Native independent review
passed27private probes plus one browser-preservation case on final unchanged hashes.
Additional restricted fake-capture/source selections stalled and were interrupted,
not counted as passes. Source computer.py is11fa7b3f…358ff. Evidence:
/tmp/dream-native-input-diagnostic/REPORT.md and
/tmp/dream-native-controls-review/REPORT.md. ADR-046 records design and limitations.

Fresh skill review repaired GUI-only import overgeneralization, no-reference polish
assumptions and automatic routing gaps. Early route fixes misclassified prose and
physical painting; further restriction missed regional polish. All findings were
preserved and repaired. Final30focused routing cases passed in0.26s and exact15
independent probes passed15/15. Two broader unchanged workflow fixture invocations
stalled and were interrupted; no broad regression pass is claimed. The small paced
argument note in tool-routes was added afterward from the reviewed tool contract.
Artistic skill uplift remains unmeasured. Evidence:
/tmp/dream-visual-skill-review/accepted-recheck.md.

The owner reported quota reset and requested switching resumed Claude work toOpus5.
The exact requested/provider-reported claude-opus-5 completed a new anonymous
portrait review, scoring72.5/27.5. Both reviewers preferred Astra but disagreed on
its absolute quality and marginal recognizable likeness. Preserve both initial
scores; no consensus completion claim. The failed Fable review remains separate.
Evidence: /tmp/dream-portrait-blind-opus5-20260913/initial-judgment.md.

After independent native and private launch reviews, old Fable bridge1545419 exited0;
new pinned bridge/session88104 and exact Opus5/high model/session6506 started on
existing isolated Blender:94/PID1463407. Initial live stream confirms model identity
and real reference/window/tool calls. It saved f9_lunar_opus5_v1.blend1,105,364bytes,
SHA256c0d98c3d922db2071b6b16a5f062d35c2ed2d822946b9ddd43904e362dcb9f31.
Do not treat this initial checkpoint as a complete mission or reopened-file proof.
The byte-identical inherited Fable checkpoint is separately retained. Bridge115s
and client120s waits cover nominal95s typing plus observation; uncertain transport
errors do not authorize replay. Launch audit:
/tmp/dream-opus-resume-review/report.md.

Root verified and gracefully closed both completed Paint contexts, Astra:95 and
hidden diagnostic:96. All eight unused desktop process identities disappeared;
Opus's Blender process remained. Astra checkpoint918,684bytes was hashed before
cleanup. Records: /tmp/dream-completed-desktop-cleanup-result.json. No owner window
or running local inference process was controlled. Only the Opus trial desktop is
left open. The user was told to leave its Xephyr window open during execution.

## Unfinished work

DREAM-061 scoped repairs are implemented and reviewed. DREAM-014 remains active:
Opus is continuing the existing scene. A read-only monitor completed its bounded
first30actions throughseq41/02:32:27Z, with no further errors afterseq20. Later
dy-only scrolling succeeded; correct crop/object selections were inspected. No
post-repair numeric edits occurred in that bounded interval; checkpoint filename
entry succeeded. The observer has finished, so no unattended monitoring is claimed.
Private report: /tmp/dream-opus-monitor/report.md.
Do not start another controller or send input to the same window. Consult
/tmp/dream-blender-trial/opus5-blender-continuation/status.json, model-stream.jsonl,
calls.jsonl and artifacts/ before recovery. Its bridge/model sessions88104/6506
are task-owned; verify PID/argv before any eventual cleanup. A controller restart
is not an owner Dream restart. OriginalFable/Astra logs remain immutable evidence.

## Next steps

Early continuation errors: extra x/y arguments on scroll were rejected; closing
Save As produced an uncertain Bad Drawable, then the model verified artifacts and
reselected the main window without replay. These are remaining interface issues,
not failures of the repaired digit timing. Continue inspecting actual numerical
edits, saved scene progress and full animation/render/playback delivery. Finished
mission media and held-out same-model skill comparison remain incomplete. Freeze
skills/control checklist before opening the sealed portrait. No owner acceptance,
new Git push, local-model load or task-wide cutoff occurred.

Final tracking passed75dated records/20changed paths after correcting a combined
handoff section heading and CURRENT active-item list. The initial check failed
with23findings downstream of those metadata mismatches; no source work was lost.

## Files changed

- `skills/computer-use/SKILL.md`
- `skills/computer-use/references/tool-routes.md`
- `skills/computer-use/references/visual-craft.md`
- `skills/blender-animation/references/scene-checks.md`
- `dream/skills/selection.py`
- `tests/test_specialist_skill_routing.py`
- `dream/computer.py`
- `dream/tools/computer_tools.py`
- `tests/test_computer_controls.py`
- `docs/public/computer-controls.md`
- `docs/public/teaching-skills.md`
- `docs/evaluations/visual-craft/README.md`
- `docs/evaluations/visual-craft/observed-friction.md`
- `docs/evaluations/visual-craft/falcon9.md`
- `docs/evaluations/visual-craft/portrait.md`
- `docs/evaluations/visual-craft/comparison.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/DECISIONS.md`
- `docs/project/updates/2026-09-12-live-visual-controls.md`
