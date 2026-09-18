# Teaching Dream reusable procedures

Dream now includes two short, source-authored workflows:

- **computer-use**: select the correct UI/tool route, inspect current state,
  locate targets again after layout changes, verify actions, and recover uncertain
  saves without blindly repeating them.
- **blender-animation**: preserve existing scenes and frames, qualify the actual
  renderer, make focused changes, check motion and visual output, and recover
  encoding failures without rebuilding valid work.

Use `Use the computer-use skill` or `Use the blender-animation skill` in a request.
Relevant UI actions and Blender tasks also select the workflows automatically.
Open **Skills** to read or customize them. They use existing tools and do not add
desktop control or image support to an endpoint that lacks those capabilities.

## From a recording to a reviewed skill

1. Use a small demonstration with a clear goal in a disposable project. State the
   application/version, starting state and expected saved result. Exclude private
   documents, credentials and unrelated windows from the selected region.
2. In **Controls → Learn**, start a recording yourself or import a selected video.
   Dream's current recorder uses X11 screen capture at two frames per second. It
   captures visible pointer movement, not an exact keyboard/click event log.
3. Pause at useful transitions. Describe the target by name, the reason for the
   action, and what visible result confirms it. Include a recovery example when
   useful. For Blender, retain a script or before/after scene files as well.
4. Extract and analyze the selected recording. Analysis uses the current model;
   selecting a cloud provider sends the selected evidence to that provider. Frame
   paths alone are not visual evidence: analysis needs working image inspection.
   **Annotate evidence** lets you link a frame/time to an application/version, target,
   action, before/after state and outcome. These are explicit human annotations,
   not captured keystrokes. Save checks the revision you opened and preserves
   separate events; a stale editor cannot overwrite newer evidence.
5. Review the draft's cited frames, steps, verification checks and uncertainty.
   Screenshots cannot prove every intervening shortcut or setting. Correct inferred
   steps and teach state-based decisions rather than fixed screen coordinates.
6. Install the reviewed draft, then enable it explicitly in **Extensions**. New
   recorded packages live under Dream's private `DATA_DIR/skills/learned-<id>`.
   Installation does not enable or replay the procedure. Originals stay intact;
   only cited frame evidence is copied into the installed package.

The two bundled workflows above were not derived from an owner recording. No new
owner recording or model training was performed. A small Claude comparison is
described below; it does not establish model uplift.
Recordings and private installed skills should remain outside source releases.

## What transfers between models

A skill supplies procedures, examples and checks at inference time. It can reduce
avoidable mistakes without changing model weights. It cannot guarantee better
perception, reasoning or execution, and cannot replace missing tools. Recording
an expert agent's screen does not expose its internal reasoning; short action
explanations and observable before/after states are the useful teaching material.

Research supports testing this approach, rather than assuming parity. A recent
computer-use preprint reports benefits from persistent skills under fixed models
and tools, with gains varying by task and iteration. It does not establish the
effect on Dream, Fable or DeepSeek. [Interaction traces to persistent skills](https://arxiv.org/abs/2609.04869).
The bundled packages use progressive disclosure: short entrypoints and references
loaded when needed, following the documented skill approach. [Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills).

When model qualification is requested, compare the same model, tools and task
with and without the skill, using fresh contexts and a held-out variation such
as a different viewport or scene. Judge saved artifact quality, successful actions,
preservation of existing work, recovery and unsupported claims. Speed and tool-call
count are secondary. The workflow references contain proposed cases; none are
presented as passed model benchmarks.

## Implementation status of the six priorities

These are engineering priorities, not measured performance gains.

| Rank | Improvement | Status |
| --- | --- | --- |
| 1 | Shared computer controls | Browser/X11 adapters plus coordinate strokes implemented; real Chromium checks and isolated native attach/click observed, broader native qualification ongoing. [Contract](computer-controls.md). |
| 2 | Structured recording evidence | Manual frame-linked action/state/outcome editor implemented; no automatic native event logger. |
| 3 | Focused skill exercises | Eight synthetic cases and an opt-in Claude runner implemented; real portrait/Blender diagnostics now running, local-model tests deferred. |
| 4 | Consolidated navigation | One primary destination navigation; narrow-window utilities under Tools. |
| 5 | Density/artwork preferences | Saved compact/comfortable spacing, collapsed banners and quiet mode. |
| 6 | Catalog usability | Sorting, filters, result counts and keyboard browsing for Projects/Skills. |

## Claude comparison, 2026-09-12

An authorized, tool-disabled Claude CLI comparison generated plans for eight
synthetic cases. A deterministic simulator replayed the actions and checked state,
preservation and verification. Baseline and assisted arms both passed **8/8**.
This is **no demonstrated improvement**: the unassisted arm already reached the
test ceiling. One run per arm cannot establish reliable transfer. The simulator
did not operate actual windows, render Blender frames or exercise Dream's adapter.

The CLI was 2.1.270, requested model `sonnet`, low effort. The returned usage keys
included `claude-sonnet-5` and `claude-haiku-4-5-20251001`; those are provider-reported
identifiers, not an independently verified architecture claim. Reported total API
cost was $0.055307. Fresh isolated directories, tools disabled, no persistence,
and synthetic prompts were used. Initial sandbox calls timed out; the authorized
host comparison completed. Snapshot skill hashes are retained with private evidence;
subsequent adapter-contract documentation changes were not rerun against Claude.

`scripts/skill_transfer_check.py --output /tmp/new-evidence-directory` prepares the
fixtures without inference. `--claude /absolute/path/to/claude` explicitly enables
the cloud comparison; it is never started by loading the skill. A future real-task
comparison should use held-out work and fixed tools before making improvement claims.

[Projects and Skills](projects-and-skills.md)
