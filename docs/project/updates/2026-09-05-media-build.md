# DREAM-023 — Create workspace and media execution

Recorded: `2026-09-05T14:09:11-05:00`
Work items: `DREAM-023, DREAM-024, DREAM-025`
Outcome: `implemented`
Actor: Codex lead; delegated storage/service implementers and independent read-only reviewer. No alternate coding model identity is claimed.

## Request

The owner explicitly requested a plan followed by continuous implementation of
image/video generation, animation and rendering for software showcases, website
animations and explainers. Existing subscriptions/local tools only; no API keys
or additional spending. Dream remains the workspace, with Browser and free
external creative tools permitted. Narrated learning stays deferred; a real-time
avatar is separate future work. See plan.

## Changes

Implemented workspace media projects, immutable assets, editable composition
revisions and durable jobs. The same service powers `dream media`, two model tools
and authenticated Studio routes. Create provides scene editing, preview, canvas
and export controls, file import, soundtrack selection, source/history editing,
subscription handoffs, local workflow submission and job/recovery controls.

CPU Chromium/FFmpeg renders explicit frame times to MP4/WebM/PNG and exports
self-contained animation HTML. References stay project-scoped. ComfyUI accepts
curated local API workflows, including a source-qualified SVD template, and
persists submission intent/backend IDs. Browser provider jobs save prompts and
reference identities and validate returned media. Existing .blend files open in
installed Blender with Python auto-execution disabled. Browser sign-in persistence
is opt-in in a separate profile; Studio stays ephemeral with opaque previews.

Read operator guide, [ADR-007](../DECISIONS.md) and separate
[avatar feasibility](2026-09-05-avatar-feasibility.md). This is a media foundation,
not a full nonlinear editor or implemented conversational avatar. The initial
scene player supports text/images/video, three motion styles and one soundtrack.

Original dirty source was preserved. Work is on `codex/media-create-20260905`;
no commit, push, release or publication. Baseline source snapshot:
`/tmp/dream-media-build-20260905.json`; shared integration-file backup:
`/tmp/dream-media-integration-baseline.tar.gz`. Neither is a full private-state backup.

## Validation

Evidence is under `/tmp/dream-media-reports/` and
`/tmp/dream-media-showcase-20260905/`; these local fixtures contain synthetic
content and are not required to onboard a clean checkout. The reviewable showcase
outputs and original composition are also retained at `artifacts/media-validation/`.

Observed:

- Real 60-second software showcase: 1280×720, 24 fps, 1,440 MP4 frames; FFprobe
  verified dimensions/duration. CPU export completed in about 48.7 seconds in
  this sample. Reusable HTML, four-second WebM and PNG also succeeded; changing
  the composition preserved the original 60-second revision. Timing is not a
  hardware-independent performance promise. Final MP4 frame visually inspected.
- Actual MP4 import → video scene → MP4 export succeeded in the locked environment
  (`video-roundtrip.json`). GUI test creates/edits/saves/exports through real
  authenticated routes and a real background worker; desktop/narrow screenshots
  inspected, opaque preview excludes the session token, no page errors.
- Initial store tests 14 passed; provenance/source and terminal-timestamp review
  corrections brought these to 16. Final service/store focused run: 44 passed.
  Renderer focused run: 10 passed, including valid soundtrack and blocked playlist
  cases. Native checks: 2 media/profile tests and 6 existing boundary checks passed
  using system Python/GI on the host display with temporary profile state.
  `uv run --locked dream desktop --check` reported GTK3/VTE/WebKitGTK4.1 ready.
- Independent review reproduced and rechecked decoder playlist access, external
  task-cancellation state, and abandoned queued-worker recovery. All three fixed;
  recheck approved with 8 targeted regressions passed. Initial failures are kept
  in the local reports. An exact PNG-byte comparison was replaced by bounded
  decoded-pixel comparison after measuring 21/57,600 pixels with maximum RGB
  difference 4; scene text/timing/script isolation remain independently checked.
- Local wheel/source-distribution build succeeded. Distribution audit passed;
  media JS/CSS/player/CLI are present and runtime/private directory checks passed.
  Final wheel: `/tmp/dream-media-final-dist/dream-0.1.0-py3-none-any.whl`,
  160 files, 1,259,595 bytes, SHA-256
  `cf2fcf0f40e4982929e615acb62889fe040305da4215b20d9ded3293651910d5`.
  Pillow is now a direct declared dependency. No new paid dependency was added.
- Host GPU discovery: two Intel Arc Pro B70 devices plus Intel integrated graphics.
  Default ComfyUI loopback health check refused connection; no checkpoint files
  were found in its standard model folders. Installed SVD node source was inspected
  without importing/loading the backend. HTTP adapter tests use explicit fake
  transport with real validation/store, not claimed model inference.

Final locked regression observed: `uv run --locked pytest -q` exited 0 with
**1,766 passed, 35 skipped, 7 warnings in 178.40s**, completed 2026-09-05T14:16:10-05:00.
Evidence: `/tmp/dream-media-reports/locked-final-pytest.log`. The first clean
locked run passed 1,765 tests with 35 skips and exposed one recording-status
header overflow caused by the new Create button; responsive wrapping corrected it. Both the exact failing test and Create UI
acceptance passed together (2 passed in 5.33s) before the final full rerun.
Tracking passed (7 dated records), covering 38 session-changed source paths;
final reconciliation retained the same 38-path coverage.

Failed/intermediate checks: initial restricted uv launch could not access cache or
PyPI; initial test run recorded 1,728 passed/35 skipped with a WebSocket teardown
failure and strict raster comparison failure. Focused desktop retry passed.
During a later full run, aligning the locked launcher replaced the drifted venv
and exposed missing Chromium revision 1223; that overlapping run was invalidated
(29 failed/1,717 passed/35 skipped/20 errors), not used as final evidence. The
matching browser was then installed and a fresh locked suite started. An initial
video round trip failed only at missing-browser launch; its locked rerun passed.
`xvfb-run` was unavailable; actual host-display native tests passed instead.
Offline dependency resolution lacked metadata; permitted online `uv lock` resolved
111 packages. The final environment reports SDK 0.2.152, Playwright 1.60.0,
Starlette 1.6.0, httpx 0.28.1, Pillow 12.3.0 and pytest 9.1.1.

## Unfinished work

No local model generation, subscription sign-in/quota consumption, child-Codex
image retrieval or real Blender editing session was performed. The cloud path is
an explicit browser handoff; ComfyUI needs a running service, selected checkpoints,
backend inputs and current resource qualification. Provider subscriptions do not
establish universal automation. Automatic cross-registration with Library, custom
Comfy nodes, automatic reference upload/model installation, advanced timeline
editing, narration capture and live-avatar operation are not implemented here.

No GPU process was killed or displaced. Owner acceptance and release are not
recorded. Background fixture jobs finished; temporary job/log/output files remain
for inspection. For an interrupted real job, use explicit recovery; reconcile
unknown external outcomes before considering another submission.

## Next steps

Open Create from Studio or use `dream media --help`, try the synthetic showcase,
then perform DREAM-012 owner acceptance on an actual software demo. Next generation
qualification needs an available ComfyUI installation/checkpoint workflow or a
signed-in existing subscription. DREAM-025 has its own feasibility fixture;
DREAM-024 remains deferred. Remote publication remains a separate owner decision.

## Files changed

- `docs/desktop.md`
- `docs/media.md`
- `docs/project/CURRENT.md`
- `docs/project/DECISIONS.md`
- `docs/project/HISTORY.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-05-avatar-feasibility.md`
- `docs/project/updates/2026-09-05-media-build.md`
- `docs/superpowers/plans/2026-09-05-media-create.md`
- `docs/superpowers/specs/2026-09-05-media-create-design.md`
- `dream/__main__.py`
- `dream/core/policy.py`
- `dream/desktop/browser.py`
- `dream/desktop/window.py`
- `dream/gui/media_routes.py`
- `dream/gui/server.py`
- `dream/gui/static/index.html`
- `dream/gui/static/media.css`
- `dream/gui/static/media.js`
- `dream/media/__init__.py`
- `dream/media/cli.py`
- `dream/media/composition.py`
- `dream/media/player.html`
- `dream/media/providers.py`
- `dream/media/render.py`
- `dream/media/service.py`
- `dream/media/store.py`
- `dream/tools/media_tools.py`
- `dream/tools/registry.py`
- `pyproject.toml`
- `scripts/audit_distribution.py`
- `tests/desktop_media_profiles.py`
- `tests/test_media_render.py`
- `tests/test_media_routes.py`
- `tests/test_media_service.py`
- `tests/test_media_store.py`
- `tests/test_media_ui.py`
- `uv.lock`
