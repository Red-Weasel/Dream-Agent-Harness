# DREAM-047 — Studio media playback

Recorded: `2026-09-11T19:56:09-05:00`
Work items: `DREAM-047`
Outcome: `implemented`
Actor: Codex lead and Astra implementation agent.

## Request

Owner reports black small video player after done reports queued/hidden clean.
Verify and repair visible media delivery, preserving frame isolation. Acceptance:
scoped media reaches visible srcdoc, media-only data/blob permitted, real playback
verified, missing/oversized media reports failure. No owner project edits/restart.

## Changes

Baseline /tmp/dream-video-playback-baseline.json. Existing dirty tree preserved.
Astra authored bundle.py/studio.py/new asset tests; root authored the CSP change,
actual browser playback test and docs, and independently reviewed the author patch.
Additional reviewer/author spawn was refused by thread cap; the available fresh
agent was reassigned implementation. No separate fresh-judge claim is made.

Observed cause: the HTML referenced rocket_launch.mp4 relatively; visible Studio
received raw HTML in isolated srcdoc, without access to that relative file. Its
CSP also omitted media-src, so even data media was denied. Hidden preview uses a
different file-loading path. Clean hidden console therefore did not prove playback.

Visible delivery now embeds local video/audio/source/track and video posters.
Only the page's resolved subtree is read, with MIME restrictions, 8 MiB per asset
and 32 MiB total raw media. Component descriptors with O_NOFOLLOW, final nonblocking
open and regular-file checks prevent replacement races and FIFO hangs. Oversized,
missing or invalid media reports a tool error; pages without local media retain
exact HTML. Existing export bundling and owner files remain unchanged.

The visible CSP permits media-src data: blob:, without allowing network or
same-origin access. Local media needs no serving endpoint or session credential.
This preserves the isolated generated-page boundary. Codec support remains a
browser prerequisite; data delivery does not guarantee every codec is supported.

## Validation

- Author first RED: 14 failing media-preparation cases; first GREEN: 14 passed.
  Lead review found check/open replacement races and picture-source misclassification;
  author repaired these with deterministic final/ancestor symlink and FIFO probes.
  Final author media/export subset: 24 passed, /tmp/dream-media-assets-author-green2.log.
- Author broader browser run: 21 passed, 11 failed with Chromium launch errors in
  the Codex sandbox. These were not counted as completed browser qualification.
- Root browser RED: video.play rejected with NotSupportedError before the CSP fix;
  remote media remained blocked. /tmp/dream-playback-root-red.log (1 failed, 1 passed).
  First GREEN plus existing artifact security: 18 passed in 1.30 seconds.
- Root final combined host gate: 63 passed in 22.32 seconds, no skips or warnings.
  Seven modules: studio_media_assets, studio_media_playback, bundle, studio_tools,
  gui_artifacts, studio_panel_phase6 and studio_panel_phase8. Actual file preparation,
  emitted/retained delivery, browser decoding/time advancement and opaque-origin/
  remote-media denial are exercised. /tmp/dream-media-playback-root-final.log.
- A synthetic one-second 32px H.264 clip was generated CPU-only for the fixture;
  no owner rerender or model/GPU job was run.
- Read-only owner MP4 metadata: H.264/yuv420p, 1280x720, 5 seconds, 514209 bytes.
  GStreamer avdec_h264 decoder is installed. This alone does not qualify WebKit.
- Root loaded the actual owner's HTML/media through repaired preparation into a
  temporary real Studio server and Chromium. Observed videoWidth1280,height720,
  duration5, currentTime0.302976, readyState4, error null, displayWidth712px.
  Both owner file hashes remained unchanged. Evidence:
  /tmp/dream-owner-video-playback.json and /tmp/dream-owner-video-studio.png.
  Root inspected the screenshot: rocket visible, player filled available width.
  The temporary server/browser was closed. No screenshot or private clip added to repo.
- Final author source/test hashes stayed unchanged after root gate. Five final
  source/test hashes: /tmp/dream-media-playback-final-hashes.json. Diff check passed.
- Root graph calls failed Transport closed; focused reads used. Author graph
  queries worked with the full project identifier. Final tracking below.
- Final tracking passed: 61 dated records and all 9 changed source paths covered.

## Unfinished work

The owner's running native GTK/WebKit window was not restarted or directly tested.
Successful Chromium playback and an installed decoder do not prove native WebKit
playback. Animation quality/takeoff timing was not graded; this task repairs delivery.
No owner artifacts, approval prompts, model, session or system configuration were
changed, and nothing was published. POSIX descriptor support is required for the
new scoped media reads, consistent with the tested Linux desktop.

## Next steps

Restart Dream when ready to load backend media preparation; refresh alone only
loads the static CSP. Ask it to show rocket_launch_viewer.html again without
rerendering. The existing rocket_launch.mp4 can be opened directly in a local
player meanwhile. Verify playback in the owner's native window after restart.
All owned test/server/browser processes exited; preserve the dirty tree and logs.

## Files changed

- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-11-studio-media-playback.md`
- `dream/gui/bundle.py`
- `dream/tools/studio.py`
- `dream/gui/static/index.html`
- `tests/test_studio_media_assets.py`
- `tests/test_studio_media_playback.py`
- `docs/desktop.md`
