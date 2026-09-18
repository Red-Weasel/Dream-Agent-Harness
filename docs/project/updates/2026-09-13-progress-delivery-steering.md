# DREAM-067–074 — Progress, delivery, steering and installation

Recorded: `2026-09-13T14:48:08.552325+00:00`
Work items: `DREAM-014, DREAM-067, DREAM-068, DREAM-069, DREAM-070, DREAM-071, DREAM-072, DREAM-073, DREAM-074`
Outcome: `implemented`
Actor: Codex lead; Astra steering_discovery implemented steering and independently reviewed media/root delivery; Astra qualification_fresh_review implemented image validation and independently reviewed progress, readiness, identity and steering. Astra rocket worker remained separate.

## Request

Work through the owner's ranked improvement list. GPUs are occupied; no models
may be loaded. Use existing recorded evidence and CPU fixtures, retaining live
quality/calibration and daily-use acceptance as unverified. Preserve the existing
dirty tree, owner session, private state and ongoing isolated trial. Baseline:
`/tmp/dream-next-improvements-baseline.json` (726 source hashes). This record owns
only changes since that snapshot; the previous continuation handoff is unchanged.

## Changes

DREAM-067: unchanged text from the same exact read target now produces specific
progress advice even when paging arguments differ. New content, targets and
image-bearing observations are excluded. Original exact-call limits remain;
the addition itself is advisory and does not treat elapsed time as a stall.

DREAM-068: image delivery now checks actual supported format and decodes bounded
pixels before import. Independent review reproduced a truncated GIF being accepted
as a shorter animation and a renamed TIFF passing as PNG. A bounded GIF block parser
and format matching repair both gaps. Video remains stream-metadata validation.
Scheduled HTTP verifier findings/unavailable inspection no longer accompany an
unqualified successful terminal result. The bounded delivery_review summary is
separate from assistant text. Other failure reasons retain precedence, and ordinary
unreviewed chat stays ordinary. Original active requests plus corrections reach
verification and optional filing. An abandoned iterator clears its pending sweep;
reviewing an old artifact against an unrelated next request was reproduced and fixed.
No automatic repair generation or quality guarantee was added.

DREAM-069: ordinary active HTTP chat has a separate Steer action, with private
session-adjacent receipts, normal attributed user history, target validation and
request-boundary delivery. All issued tools settle before insertion. Pending,
included, submitted and retained describe delivery observations, not compliance.
Stop and failure preserve text without replay. A lost response retains the draft's
receipt ID and original target; stale targets cannot silently enter another worker's
queue. Native agents, workflows and autonomous runs retain clearly labelled
in-process after-turn queues. They do not gain live steering or durable fallback.

DREAM-070: computer-use guidance reuses valid screenshots/tokens already returned
by an action and reobserves when evidence is missing or stale. Blender guidance
checks camera framing between keys, not only at keyframes, and retains known-good
drafts. Both manifests advance to 1.0.1. These are evidence-informed lessons, not
measured improvements to another model's drawing or animation quality.

DREAM-071: passive media status reports selected workspace, configured shell roots,
tool-image configuration and host encoder discovery with provenance. Unknown actual
sandbox execution and endpoint image acceptance remain unknown. Mismatched bound
workspace scope is refused before media storage is opened. No model or renderer
is launched by the new readiness check.

DREAM-074: Controls/management report process version, import time and selected
source hashes at import versus disk now. Missing/unreadable files remain Unknown.
The diagnostic does not claim every loaded module matches or restart the owner.
A genuinely separate venv installed98 runtime distributions from frozen exported
lockfile hashes; candidate wheel, installed files and CLI were checked there.

DREAM-072 remains planned. Existing Projects Documents already supports editable,
selected compact handoff notes; public guidance now explains goal/artifacts/checks/
known defects/next action/source session. Automatic extraction and new recall ranking
were not implemented. DREAM-073 model/task calibration also remains planned until
representative live-use evidence is available. No second memory framework was added.

See [progress and delivery](../../public/progress-and-delivery.md),
[runtime controls](../../public/runtime.md),
[installation qualification](../../public/production-qualification.md),
[project notes](../../public/projects-and-skills.md), and ADR-049/050 in
[decisions](../DECISIONS.md). README now describes the new behavior.

## Validation

Observed on September13 in this checkout, using synthetic providers and temporary
configuration. None of these gates loads models, uses GPU inference or runs a benchmark.
CPU Chromium fixtures explicitly disable GPU use.

- Final integrated gate: **310 passed,1 deselected,2 existing Starlette/httpx/AnyIO
  deprecation warnings in 4.86s**; `/tmp/dream-next-final-verified.log`. Command:
  `.venv/bin/python -m pytest -q tests/test_delivery_review_result.py tests/test_verifier_fork.py tests/test_verifier_context.py tests/test_verifier_prerequisites.py tests/test_verifier_scope.py tests/test_live_steering.py tests/test_live_steering_ui.py tests/test_observation_progress.py tests/test_loop_guard.py tests/test_repeat_guard_artifacts.py tests/test_salvage.py tests/test_runtime_identity.py tests/test_runtime_identity_ui.py tests/test_runtime_controls.py tests/test_run_supervision_views.py tests/test_media_readiness.py tests/test_media_decode_delivery.py tests/test_specialist_skill_routing.py tests/test_skill_loader.py tests/test_skill_transfer_check.py tests/test_http_interrupt.py tests/test_backend_resilience.py -k 'not late_error'`.
  The older hidden-preview late-error fixture was not run. No full-suite claim.
- Earlier integrated gate307 passed,15 deselected used a broad render filter that also
  excluded some harmless core cases. Those core cases were restored in the final
  gate above. Media service/store no-render regression is separate:
  `/tmp/dream-deliverable-discovery/implementation.md`; author65 passed,6 deselected
  at its stated checkpoint, followed by further GIF fixtures. Actual worker/export/
  video operations were excluded; do not add overlapping test counts.
- Progress: initial targeted failure reproduced, then 48 passed. Independent non-author
  review 11 private probes passed. Skill structure validators passed and79 skill
  routing/loader/transfer fixtures passed. `/tmp/dream-progress-review/report.md`,
  `/tmp/dream-observation-progress.log`, `/tmp/dream-next-skills.log`.
- Readiness first 4 cases failed before implementation; mismatched-scope refusal was
  repaired after the first3 passed/1 failed. Final included in310. Independent readiness/
  startup identity review 11 private checks passed; root browser identity gate 28 passed.
  `/tmp/dream-readiness-identity-review/report.md`. After that review, adding
  core/steering.py to the selected identity list was covered by root final fixtures.
- Media independent same-judge recheck rejects the unchanged truncated-GIF and
  TIFF-as-PNG repros. It accepts24 legitimate GIF variants and rejects all149 strict
  prefixes of the150-byte fixture;27 author cases also passed. Evidence:
  `/tmp/dream-steering-discovery/media-independent-check.py` and original
  `media-probe.py`. These checks do not establish video playback or visual polish.
- Steering author27 checks passed after fixing retry-target and stale-fallback races;
  another 84 compatibility/media checks passed at its earlier snapshot. ActualEngine
  session storage, Stop, tool batches, finalization, capacity, context compaction,
  HTTP/API and GPU-disabled Chromium were exercised. Scope and failed attempts:
  `/tmp/dream-steering-discovery/implementation-handoff.md`.
- Root delivery-result tests reproduced 10 failures before the initial fix and one
  lost-original-scope case later. Independent review then reproduced an abandoned
  old-review path attaching to a new task; cleanup repaired it. Root36related
  cases passed afterward. A separate independent non-author review passed17 private
  probes and12 author cases on final root source:
  `/tmp/dream-delivery-result-review/report.md` and `hashes.json`.
- Final independent steering review:14 private interaction probes passed in 0.37s,
  including the unchanged abandoned-generator reproduction. One actual Studio/CPU
  Chromium response-loss test passed in 1.10s: the server saved text before response
  loss; unchanged retry after target change retained original identity, logged once
  and queued nothing. `/tmp/dream-live-steering-review/report.md` and
  `reviewed-sha256.txt`. Initial private-browser-cache setup failed; selecting the
  existing browser binary fixed that fixture setup.
- Package: `/tmp/dream-next-package-qualification/report.json` and
  `/tmp/dream-next-package-final.log`.269 wheel members,265 candidate-source byte
  matches and265 installed byte matches; all10 curated skills discovered. Nine
  synthetic private-directory canaries excluded in a disposable source copy.
  Hash-locked runtime dependencies installed fresh, `uv pip check` passed, and
  installed isolated imports/--help/status/runs/profile passed. No development
  runtime dependency reuse. Build dependencies resolved separately. Exclusion
  tests do not audit arbitrary source secrets or Git history.

Failures and limitations retained: initial `uv pip sync -r` syntax was corrected;
first offline dependency attempt in the preceding task was not this fresh online
install. This task's initial offline wheel build failed because hatchling was not
cached; isolated build dependency download resolved it. Exact logs are
`/tmp/dream-next-build-offline-failure.log` and
`/tmp/dream-next-clean-install.log`. Restricted thread-backed/Starlette fixtures
stalled and were interrupted, then passed under approved host CPU/localhost
execution. One agent's automatic approval review timed out before a test process
started; its explicitly allowed single retry succeeded. No permission block remains.
Graph-first tools returned insufficient results/Transport closed or did not return
within the bounded attempt; focused source reads followed. Fresh-agent creation hit
the thread limit. Reviewers were independent non-authors using existing Astra
threads; no fresh-context verification claim is made for this pass.

Tracking passed:80 dated records and all35 changed source/doc/test paths covered
against `/tmp/dream-next-improvements-baseline.json`. Rechecked after recording
this result.

## Unfinished work

No owner Dream process was restarted; Python fixes load on a normal future restart.
No Git push, publication, owner-memory export or model/GPU load occurred. Live
provider image acceptance, native live steering, native desktop startup, model
compliance, visual quality and owner daily-use acceptance remain unqualified.

DREAM-014 is still a separate active isolated CPU/software-GL trial on:95, with a
pinned controller. A display/process exit during this pass was observed; cause is
unknown. The worker recovered from checkpoint08 into its own new session and
saved09 then 10. Latest checkpoint at this record's inspection:
`/tmp/dream-blender-trial/astra-blender-resumed-1/artifacts/astra-resumed-10-parachute-rig.blend`,
1,232,916 bytes, SHA256
`55e20951ddd2dbc91a7cebe826b50af9e7aa5719fcd727b5187ed1b6899b4ec0`.
Lead viewed its frame1770 screenshot: canopy, suspension strips and capsule are
visible in frame. It remains rough geometry and an incomplete mission. Owner
windows and the separate Opus :94 display were not used. Worker owns only its isolated
processes and private trial directory; do not replay an uncertain input.

## Next steps

DREAM-072: use the current Documents handoff route, then assess a bounded automatic
capture proposal against its existing storage/provenance rather than adding another
memory store. DREAM-073: gather normal session successes/failures with exact model,
settings, action/result and artifact evidence; retain private transcripts and do not
claim calibration until representative comparisons are possible. DREAM-074/015:
owner may restart normally when convenient and qualify everyday use, actual provider
support and native startup. DREAM-014 worker continues only its assigned isolated
CPU checkpoints; inspect its current notes before takeover or continuation.

## Files changed

- `README.md`
- `docs/project/CURRENT.md`
- `docs/project/DECISIONS.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-13-progress-delivery-steering.md`
- `docs/public/production-qualification.md`
- `docs/public/progress-and-delivery.md`
- `docs/public/projects-and-skills.md`
- `docs/public/runtime.md`
- `docs/superpowers/plans/2026-09-13-progress-delivery-steering.md`
- `dream/__init__.py`
- `dream/core/backends/openai_compat.py`
- `dream/core/engine.py`
- `dream/core/steering.py`
- `dream/gui/server.py`
- `dream/gui/static/controls.js`
- `dream/gui/static/index.html`
- `dream/management.py`
- `dream/media/providers.py`
- `dream/media/service.py`
- `dream/runtime_identity.py`
- `dream/tools/media_tools.py`
- `dream/tui/app.py`
- `skills/blender-animation/manifest.json`
- `skills/blender-animation/references/scene-checks.md`
- `skills/computer-use/manifest.json`
- `skills/computer-use/references/tool-routes.md`
- `tests/test_delivery_review_result.py`
- `tests/test_live_steering.py`
- `tests/test_live_steering_ui.py`
- `tests/test_media_decode_delivery.py`
- `tests/test_media_readiness.py`
- `tests/test_observation_progress.py`
- `tests/test_runtime_identity.py`
- `tests/test_runtime_identity_ui.py`
