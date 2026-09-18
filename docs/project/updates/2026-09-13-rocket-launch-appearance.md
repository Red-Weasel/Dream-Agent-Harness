# DREAM-014 — Launch-shot appearance development

Recorded: `2026-09-13T23:38:11Z`
Work items: `DREAM-014`
Outcome: `implemented`
Actor: Codex lead owns tracking, reference research, independent review and delivery;
Astra rocket worker owns only its isolated scene/controller/recording; a new Astra
launch_visual_review context independently judges rendered appearance.

## Request

Owner approved the proposed Blender-first next stage: finish one convincing launch
shot, inspect an actual still, qualify a short motion sample, and establish an
appearance before broader film work. Preserve the existing60second animation.
Preserve the original GUI-only trial and recorded computer-use evidence.
GPUs remain occupied; no GPU rendering or local model loads. This is an artifact
production pass, not authorization for new harness features or public release.

Snapshot `/tmp/dream-rocket-lookdev-baseline.json` contains750 source hashes;
pre-existing dirty status is recorded separately. Root changed only shared tracking.
Private assets, images, clips and action traces remain outside the repository.

## Changes

Delivered a new launch-shot appearance study from checkpoint21: CPU Cycles,
new launch environment, materials, lighting and procedural exhaust. Six revisions
produced a1920x1080 still, two-second motion preview and editable Blender scene.
Worker owns only :95 and its existing private trial directory. Root and reviewer
must not drive that desktop concurrently or touch owner/Opus:94 windows.

Primary reference: [SpaceX Falcon9](https://new.spacex.com/vehicles/falcon-9).
The target remains slender70m vehicle proportions with recognizable fairing,
dark interstage and nine-engine first-stage configuration. This is not a certified
engineering replica or reconstruction of a particular launch site.

Method clarification during this session: the current request is production film
finishing. Lead explicitly told the owner that Blender scripting may now build
materials/effects efficiently, separately from the earlier GUI-only comparative
trial. No user instruction prohibited scripting for this production stage. The
worker remains sole scene producer and must retain script source, new scene/output
names, CPU settings and actual rendered evidence. New production results cannot
be used as evidence of GUI-only skill improvement. Checkpoint21 remains unchanged.

## Validation

- Lead viewed the earlier full-film contact sheet and read checkpoint21's review
  and continuation handoff. The existing image is a diagram-like Workbench blockout;
  resolution and decoding evidence do not establish realism.
- Independently hashed preserved checkpoint21 and its matching1080p v02 movie;
  values match the worker's prior handoff. Private manifest:
  `/tmp/dream-rocket-lookdev-lead/preserved-inputs.json`.
- Fresh independent baseline review identified pad/scale, exhaust-ground
  interaction, early-liftoff composition and directional/environmental lighting as
  priorities above tiny surface details. No scene changes or renders by reviewer.
- Worker reported exact retained scene/controller processes alive. An approval
  deadline blocked an initial read-only process call; a retry succeeded. Restricted
  process namespace initially hid the retained PIDs; exact host verification
  resolved that observation. No uncertain scene input was replayed.
- Worker observed Cycles present in the actual GUI and selected Device CPU. This
  established availability/selection before the successful CPU tests below.
  Recorder restarted with a new session4-realism prefix, then was stopped when
  production moved to headless scripts. No idle recording is claimed as work.
- First production frame observed: `production-launch-v01/launch-hero-preview.png`,
  1280x720 frame45. Render log completed in13.33seconds; settings record Blender4.0.2,
  CyclesCPU,4threads,32samples,denoisingfalse. Lead viewed the actual PNG. Sky,
  materials and launch structures improve on the blockout, but the pointed
  football-shaped fairing, smooth steam masses, rectangular/hidden plume and flat
  lighting do not meet the desired appearance. Another still revision requested;
  no moving render authorized by lead at this review point. Original files retained.
- Revisionsv02/v03/v04 repaired the fairing profile, opaque rectangular flame,
  tower overlap, steam opacity and coarse foreground vegetation. Lead viewed all
  three actual images. v04 rendered in68.83seconds, CPU256samples,4threads at720p.
  Fresh non-author review agrees majorv01 defects improved but identifies smooth
  plume/puff shapes and repeated terrain/hard horizon as remaining realism gaps.
  Lead authorized a2second motion preflight while still refinement continues;
  this is permission for technical motion verification, not photographic approval.
- OpenImageDenoise selection failed because this Blender build exposes no Cycles
  denoiser enum. Failure retained; subsequent renders explicitly disable denoising.
  No GPU or alternate hidden denoiser used. Code graph discovery for a private
  shader review did not return and was cancelled; a focused read confirmed volume
  material output has no surface shader. Excessive density, not a surface link,
  explained the opaque appearance in that inspected setup.

- v05 start/middle/end inspection caught a detached exhaust at frame89 and an
  unnatural straight-edged sky shadow. v06 repaired visible root attachment and
  reduced haze. Lead and fresh reviewer inspected the actual endpoint PNG.
  Reviewer still identified rigid flame shape and tight end-frame composition.
- Worker started the v06 motion preflight: frames30 through89, 30fps, 640x360,
  48samples, CyclesCPU,4threads, no denoising. A1920x1080 frame45 at384samples
  follows in the same sequential job. At00:14UTC the actual log had reached
  frame42; delivery and decoding are still pending. Runtime record was reconciled
  to the actual v06 scene, PID336418 and service, removing stale v01/v04 fields.

- Final v06 job completed with ALL_DELIVERABLES_DONE and Blender quit. Hero still
  decoded at1920x1080; worker log reports384samples and5m20.42s. Lead viewed the
  actual image. Fresh reviewer confirms substantial improvement over Workbench,
  usable hero framing and no apparent earlier sky wedge, but still CG appearance.
- Lead independently ran ffprobe count_frames and full ffmpeg decode on the final
  MP4: H264,640x360,30fps,60decoded frames,2.000seconds, decode exit0. Independently
  counted60unique source-frame hashes. Six sampled frames show coherent ascent
  and attached exhaust root. Full-speed playback appearance/flicker is unverified.
- Recomputed original scene/movie hashes after rendering; both unchanged. Root
  preservation/video evidence: /tmp/dream-rocket-lookdev-lead/verification.json.
- Delivered13 files with copy hashes checked to the new Desktop folder
  Astra Rocket Launch Preview. Includes Launch-Still-1080p.png,
  Launch-Motion-2s.mp4, Launch-Scene.blend, Before-and-After.png, original frame,
  motion contact sheet, source scripts, settings, log and review notes. All assets
  are procedural. Scripts retain private build paths; the saved scene can be
  edited directly. No artifact, recording or private memory was added to Git.
- A root tracking-edit command initially failed because python was absent; retry
  with python3 succeeded. No partial edit occurred from the failed invocation.

## Unfinished work

First-shot study is delivered; the full photoreal film is unfinished. Current
exhaust remains a smooth glowing column with a bright horizontal band. Vapor
and terrain need detail, and late-motion framing crops the pad while narrowing
nose clearance. Denoising remains unavailable in this Blender build. These are
visible limitations, not failed file delivery. No photographic-quality, exact
SpaceX replication, GUI-only skill uplift or owner acceptance is claimed.

No further render is scheduled. Worker finalized handoff-production-v06.md,
progress.json and production-provenance.json. Exact service check observed
MainPID0, inactive/dead, Resultsuccess and ExecMainStatus0. Its first read-only
approval attempt timed out; one retry succeeded. Retained :95 GUI is preserved.
Broader DREAM-014 follow-up is planned.

## Next steps

Owner can inspect Desktop/Astra Rocket Launch Preview. Continue from
Launch-Scene.blend, improving exhaust breakup/ground interaction and camera
clearance before longer production rendering. Keep original checkpoint21/movie
unchanged. Recheck start/middle/end and actual output after each material change.
Do not load GPUs or expand to the full film without resolving first-shot quality.

Tracking check passed with82 dated records and3 changed source paths against
/tmp/dream-rocket-lookdev-baseline.json. No code tests required for this artifact
and tracking-only pass. No Git commit, push or application restart performed.

## Files changed

- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-13-rocket-launch-appearance.md`
