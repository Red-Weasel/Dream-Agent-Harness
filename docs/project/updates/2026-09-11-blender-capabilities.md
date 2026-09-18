# DREAM-039 Blender/Cycles capability detection

Recorded: `2026-09-11T14:40:39-05:00`
Work items: `DREAM-039`
Outcome: `implemented`
Actor: Codex lead; Astra probe implementer and separate Astra API reviewer.

## Request

Owner authorized fixing the Blender/Cycles discovery gap while their current job
finishes before restarting. Installed Cycles files were found in the earlier
read-only investigation; they are not proven damaged or render-qualified.
Owner also renewed the request to publish the current README and source on main,
and requested read-only monitoring of recent errors in the active Dream session.

Acceptance: passive status never launches Blender; resource-confirmed diagnostic
checks actual Cycles registration and engine assignment in a separate factory
session with no render or preference save. Preserve registration errors and label
device/headless-render evidence unverified. Do not run the actual probe while the
owner's work is active. No package replacement or driver installation is needed
to implement the harness fix.

## Changes

Root owns MediaService, model-tool and CLI integration plus media operating docs
and integration tests. Astra author owns the new diagnostic module, Blender-side
script and unit fixtures. API reviewer is read-only. The existing dirty tree and
GitHub publication snapshot remain untouched outside these scoped source edits.

Implemented passive status, explicit `probe_blender` action through mutating tools,
CLI and authenticated POST routes, and a fixed no-render Blender-side script.
The probe checks session-local registration and engine assignment, captures
registration errors and keeps device/headless render evidence unverified.
Factories use temporary Blender configuration, disabled autoexec and stripped
Blender user/system path overrides. Cycles native initialization may use resources.
No installed Blender/Cycles files were replaced. README now includes current Chat
controls and Blender diagnostics; publication has not occurred.

## Validation

Baseline /tmp/dream-blender-capabilities-baseline.json contains 602 source hashes.
Root's graph query returned Transport closed; focused known-file reads followed.
Delegated graph access worked. No live Blender/model/render probe.

Six integration cases failed before wiring the new status/action. Author CPU
fixtures exposed and repaired an output-limit pipe-drain hang. Fresh independent
review then reproduced an escaped writer defeating the timeout; after repair the
same probe returned in0.352s for a0.1s deadline plus0.25s cleanup grace. A fixture
also reproduced inherited BLENDER_SYSTEM overrides before they were stripped.
The repaired diagnostic passed the bounded-return gate. Universal descendant
cleanup was not established: a child can survive after the parent exits first.
This limitation is documented; no process-containment claim is made.

Final combined fixture run: **62 passed, 2 warnings in2.56s**, exit0,
/tmp/dream-blender-tests2.log. Command:
`.venv/bin/pytest -q tests/test_blender_capabilities.py tests/test_blender_integration.py tests/test_media_service.py tests/test_media_routes.py`.
Twenty-four of these are dedicated probe fixtures; no actual Blender ran.
Warnings are existing Starlette/httpx and AnyIO deprecations. The first sandboxed
combined attempt stalled after30 cases at the HTTP client boundary and was
interrupted; the bounded rerun outside that sandbox completed. Initial logs remain
/tmp/dream-blender-integration-red.log and /tmp/dream-blender-tests.log.

Read-only live monitoring found one run_script refusal at14:43:21CDT in session
20260911-141259-1e42: bubblewrap loopback RTM_NEWADDR Operation not permitted.
Exactly one such refusal among44 tool results; subsequent explicitly approved
uncontained run_bash results exited0. Transcript grew115 to118 records over three
observations and advanced through14:48:54. EGL warnings were followed by successful
surfaceless fallback and exit0. The particular host restriction remains unknown;
no sandbox weakening, current-job replay or live probe was performed. Loaded Python
version cannot be inferred from the isolated Codex /proc namespace.

## Unfinished work

None within the diagnostic implementation. Real Blender registration/rendering
and GPU compatibility remain untested. The owner subsequently reported missing
see despite screenshot guidance; that separate vision repair is next. GitHub
publication remains blocked under DREAM-037 pending exact payload approval.

## Next steps

Owner will finish current work and restart when ready. Only after genuine resource
preflight, use the explicit no-render diagnostic if needed. Do not launch it during
the current job. Preserve private runtime logs and the original dirty checkout.

## Files changed

- `dream/media/blender.py`
- `README.md`
- `dream/media/blender_probe.py`
- `dream/media/service.py`
- `dream/media/cli.py`
- `dream/tools/media_tools.py`
- `tests/test_blender_capabilities.py`
- `tests/test_blender_integration.py`
- `skills/media/references/operations.md`
- `docs/media.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-11-blender-capabilities.md`
