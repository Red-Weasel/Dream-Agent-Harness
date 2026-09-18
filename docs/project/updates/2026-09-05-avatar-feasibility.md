# DREAM-025 — Conversational avatar feasibility

Recorded: `2026-09-05T14:07:00-05:00`
Work items: `DREAM-025`
Outcome: `proposed`
Actor: Codex, read-only feasibility research during DREAM-023 implementation.

## Request

The owner’s end goal is a photorealistic avatar that Dream can animate and operate
in real time during a conversation. This remains a separate horizon item; the
current media build does not implement live narration capture or an avatar.

## Changes

Recommended experimental architecture: the selected Dream conversation engine
streams text to speech; a speech/viseme clock drives a persistent avatar renderer;
voice activity and interruption stop queued speech and facial motion together.
Avatar identity/assets live in the media project store. A live renderer should
have its own real-time scheduler, rather than waiting behind offline export jobs.

Two candidate paths deserve a bounded comparison:

- A rigged 3D character in a real-time renderer offers explicit gaze, expression,
  body motion and camera control. Blender can author the asset. Skin, hair,
  lighting, rigging and the runtime still determine photorealism.
- A portrait-driven pipeline may reach a convincing face sooner, with less freedom
  for full-body motion and viewpoint. [LivePortrait](https://github.com/KlingAIResearch/LivePortrait)
  supplies portrait animation/retargeting code. [MuseTalk](https://github.com/TMElyralab/MuseTalk)
  is an audio-driven lip-synchronization candidate. Neither is integrated or
  performance-qualified for Dream here. Their dependency/model licenses and
  supported accelerator paths need review before selecting assets or checkpoints.

These are candidate components, not a promise that a chat/VLM subscription itself
provides a continuous avatar video stream. Do not build the live experience around
repeated asynchronous video-generation requests.

Proposed qualification fixture: a synthetic character and a fixed 10-minute
conversation, including interruptions, pauses, head turns and resumed speech.
Measure median/p95 speech-start latency, face/audio offset, rendered fps, dropped
frames, VRAM/temperature and quality at 720p and 1080p. Initial engineering targets
for evaluation: 30 fps, lip/audio offset within 80 ms, p95 speech start below two
seconds, interruption response below 250 ms. These are proposed targets, not
observed results or approved product guarantees.

## Validation

Observed on the host with read-only `xpu-smi discovery -j`: two Intel Arc Pro B70
discrete GPUs and an Intel integrated GPU. At 18:58 UTC, `xpu-smi stats` reported
about 26.4 MiB and 34.3 MiB discrete memory usage (~0.08% and ~0.11%). Sysfs sensor
values ranged from 39°C to 66°C in an earlier sample. This is a time-specific
snapshot, not a claim of continuously available resources. Complete per-process
GPU utilization and a real model memory estimate remain unqualified.

The initial restricted sandbox could not see `/dev/dri`; host-visible inspection
resolved device identity. Installed ComfyUI’s default loopback port refused
connections and its standard model folders contained no checkpoint files. No
model, avatar, speech service or substantial GPU workload was loaded. No account,
API key, subscription quota or additional purchase was used for this research.

## Unfinished work

Choose and qualify the avatar model/rig/runtime after voice-control integration
requirements are known. Test actual Intel accelerator compatibility and latency;
VRAM capacity alone does not establish a viable real-time pipeline. DREAM-024
narrated learning remains deferred and is not a prerequisite for offline media.

## Next steps

DREAM-025: use the above fixture to compare one rigged and one portrait-driven
candidate after model/dependency selection and current GPU preflight. Keep this
as a separate implementation milestone with measured acceptance; preserve the
owner’s no-additional-spend constraint.

## Files changed

- `docs/project/updates/2026-09-05-avatar-feasibility.md`
