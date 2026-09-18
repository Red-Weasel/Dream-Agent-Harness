# DREAM-061 / DREAM-062 / DREAM-063 / DREAM-064 — Six approved improvements

Recorded: `2026-09-12T23:50:39+00:00`
Work items: `DREAM-061, DREAM-062, DREAM-063, DREAM-064`
Outcome: `implemented`
Actor: Codex lead/controller; Astra ui_six, record_evidence, skill_claude_check authors;
fresh computer_review and record_ui_review independent reviewers.

## Request

Execute the six ranked improvements. Defer local-model testing, build transferable
skills, and use Claude for a bounded comparison where available. Acceptance covers
shared action contracts, reviewed recording evidence, useful skill exercises and
navigation/density/catalog behavior. Native/model quality and owner acceptance
are qualification limits, not inferred from fixtures.

## Changes

1. Added four common computer tools in the shared registry, owned by ToolContext
   and closed with Engine cleanup. Separate Chromium contexts or explicit X11
   windows return DOM/state plus immutable screenshots. Fresh one-use observation
   IDs, retained nodes, focus/geometry/navigation and link/form checks reject known
   stale actions. Invalid targeting fields are refused. Text JSON is bounded and
   paged; actions report dispatch, not task success. Normal mutating permission
   rules remain. Desktop input is not sandboxed, Wayland is unsupported, and X11
   screenshots are screen crops that may contain overlays.
2. Added private manual frame-linked annotations, application/version, target,
   before/action/after/outcome, basis and confirmed outcome. Controls uses actual
   authenticated frame images, revision checks and preserved drafts. Model draft
   steps can cite saved IDs but cannot invent matching human confirmation through
   this API. Encoded fields page losslessly with revision checks. No OS logger or
   owner capture was started.
3. Improved both specialist skills and added eight deterministic simulator cases
   plus an explicitly opt-in Claude runner. Cloud baseline 8/8 and assisted 8/8 tied.
4. Consolidated destination navigation; utilities collapse into Tools below 900px.
5. Added persistent compact/comfortable, artwork-banner and quiet preferences.
6. Added project/skill filters, sorting, counts and Arrow/Home/End/Enter browsing.

README/operator docs reflect implemented behavior and limitations. See
[ADR-044](../DECISIONS.md), [computer controls](../../public/computer-controls.md),
[teaching skills](../../public/teaching-skills.md) and
[workspace guide](../../public/projects-and-skills.md). Existing dirty changes,
recordings, memory and owner processes were preserved. No source publication.

## Validation

Observed final model-free host fixture gates on 2026-09-12:

- `timeout 120 .venv/bin/python -m pytest -q tests/test_computer_controls.py tests/test_demonstration_evidence.py tests/test_learning_workflows.py tests/test_skill_transfer_check.py tests/test_curated_skills.py tests/test_specialist_skill_routing.py tests/test_task_guidance_integration.py tests/test_tool_validation.py tests/test_provider_capabilities.py tests/test_schema_budget_real.py tests/test_engine_parent_bridge.py tests/test_guided_workflows_routes.py --tb=short`: **191 passed, 4 existing warnings in 12.88s**. Log `/tmp/dream-six-final-core.log`. Warnings: Starlette/httpx and AnyIO alias deprecations; two synchronous schema tests with inherited asyncio marks.
- `timeout 120 .venv/bin/python -m pytest -q tests/test_workspace_usability.py tests/test_theme_polish.py tests/test_workspace_navigation_state.py tests/test_workspace_library_ui.py tests/test_workspace_design.py tests/test_studio_controls.py tests/test_feed_scroll.py tests/test_workspace_atmosphere.py tests/test_desktop_chat.py tests/test_desktop_companion.py tests/test_mode_shortcut.py tests/test_project_workspace_ui.py --tb=short`: **86 passed in 65.41s**. Log `/tmp/dream-six-final-ui.log`.
- Offline `uv build --wheel --offline --out-dir /tmp/dream-six-wheel` passed. The 266-entry wheel matches 24 selected changed production/package files byte-for-byte and contains no checked private runtime/learned-package paths. This is not a full privacy audit or clean-install qualification. Evidence `/tmp/dream-six-wheel-verification.json`.
- Author JavaScript syntax checks and specialist validation passed. Lead inspected
  final 1280/390 project screenshots; author and fresh reviewer inspected 390/720/1280
  plus mobile Chat/Skills/Controls fixtures. `/tmp/dream-usability-*.png` stays local.
- Fresh computer reviewer reproduced changed-link dispatch and overwritten-image
  evidence, then later failed-observation/new-handle reuse and effective form-action
  omissions. All repaired with regressions; final independent 23 passed in 5.94s.
- Fresh recording/UI reviewer reproduced both late-editor and pending-save identity
  races. Author repaired them; independent 23 passed in 6.48s before final transport
  additions. Author final 33 passed in 3.40s covers evidence/learning and encoded paging.
  Lead final combined gate includes all these checks. Fresh reviewer then independently
  verified the final transport repair: 5 passed in 0.33s, with no remaining blockers
  in the assigned recording/UI scope.

Claude comparison: `python3 scripts/skill_transfer_check.py --output
/tmp/dream-skill-transfer-20260912-host --claude
~/.nvm/versions/node/v24.15.0/bin/claude --timeout 90` completed. CLI 2.1.270,
requested sonnet / low effort, fresh tool-disabled contexts, one run per arm. Reported
usage keys were claude-sonnet-5 and claude-haiku-4-5-20251001. Both arms passed 8/8
simulated action cases; no demonstrated uplift. Total reported cost $0.055307.
Only synthetic case/skill prompts were sent, not repository/private owner files.
Private raw results/prompts/hashes are under that evidence directory. Computer
adapter documentation was refined after the comparison snapshot; no new inference
was run for those prose changes. This is action-plan simulation, not actual CUA,
Blender, rendering, perception or reliable cross-model transfer evidence.

Failures/limits retained: two initial sandbox Claude arms timed out 90s each, with
no scores. Host comparison succeeded. Sandbox uv cache/loopback/trusted Python
restrictions affected author fixtures; isolated host runs resolved them without
weakening checks. Author UI tests initially used retired selectors/dialogs; those
were updated to the intended new routes while preserving behavior assertions.
Recorder race tests failed before repair; oversized escaped evidence reproduced
60k+ JSON characters before bounded field paging. Error-envelope tests failed before
changing undersized-cap responses to tool errors. Root attempted one patch against
nonmatching skill-reference text; reread and applied the correct insertion. Root
final combined approval review timed out before execution with no safety finding;
a single separate retry succeeded. An author combined write/test approval also
timed out unexecuted and was split into workspace edit then pure fixture test.
No unresolved approval remains. Root graph calls returned Transport closed; focused
reads followed. Fresh reviewers had graph access but some new symbols were absent.
Ruff was unavailable; syntax, execution, tests and package checks were used.

Baseline `/tmp/dream-six-improvements-baseline.json`; final source hashes and
handoff snapshot remain under `/tmp/dream-six-improvements-*`; final source hashes
are `/tmp/dream-six-final-hashes.json`. Tracking passed: 73 dated records and
43 changed source paths checked against the session baseline.

## Unfinished work

None within the implemented feature scope. Native X11 actions have mocked-I/O
checks only; no owner desktop or synthetic X11 runtime was exercised. Wayland,
iframe/shadow-root control, drag/file chooser/native accessibility and automatic
input-event recording remain unsupported. Live local-model comparisons are deferred
by owner instruction. Native visual acceptance, model-driven task quality and
production-perfect claims remain unqualified. No owner restart, local-model load,
Blender render, owner recording, memory export, install or Git push occurred.

## Next steps

Owner may restart Dream normally and inspect Context preferences, Projects/Skills
catalogs, and Learn → Annotate evidence. Use either specialist skill for suitable
work. Qualify native X11 against a selected disposable application before relying
on native input. Further real task/model comparisons belong to DREAM-014/015;
this handoff does not authorize unrequested local-model loads or publication.
All task-owned test/comparison processes completed; no active recording or model
worker remains. Preserve the dirty tree and private evidence when resuming.

## Files changed

- `README.md`
- `docs/extensions.md`
- `docs/project/CURRENT.md`
- `docs/project/DECISIONS.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-12-six-priority-computer-skills.md`
- `docs/public/computer-controls.md`
- `docs/public/projects-and-skills.md`
- `docs/public/teaching-skills.md`
- `dream/computer.py`
- `dream/core/engine.py`
- `dream/core/policy.py`
- `dream/demonstrations.py`
- `dream/gui/static/controls.js`
- `dream/gui/static/library.css`
- `dream/gui/static/library.js`
- `dream/gui/static/theme.css`
- `dream/gui/static/workspace.css`
- `dream/gui/static/workspace.js`
- `dream/gui/workflow_routes.py`
- `dream/tools/computer_tools.py`
- `dream/tools/context.py`
- `dream/tools/demonstration_tools.py`
- `dream/tools/registry.py`
- `scripts/skill_transfer_check.py`
- `skills/blender-animation/SKILL.md`
- `skills/blender-animation/references/demonstrations.md`
- `skills/blender-animation/references/runtime.md`
- `skills/blender-animation/references/scene-checks.md`
- `skills/computer-use/SKILL.md`
- `skills/computer-use/manifest.json`
- `skills/computer-use/references/demonstrations.md`
- `skills/computer-use/references/tool-routes.md`
- `tests/test_computer_controls.py`
- `tests/test_demonstration_evidence.py`
- `tests/test_desktop_chat.py`
- `tests/test_desktop_companion.py`
- `tests/test_mode_shortcut.py`
- `tests/test_project_workspace_ui.py`
- `tests/test_skill_transfer_check.py`
- `tests/test_workspace_library_ui.py`
- `tests/test_workspace_navigation_state.py`
- `tests/test_workspace_usability.py`
