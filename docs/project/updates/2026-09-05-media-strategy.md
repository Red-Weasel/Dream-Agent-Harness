# DREAM-023 / DREAM-024 — Media strategy discussion; narration deferred

Recorded: `2026-09-05T10:53:06-05:00`
Work items: `DREAM-023, DREAM-024`
Outcome: `proposed`
Actor: Codex, planning with the owner; no media implementation claimed.

## Request

The owner wants image generation, video generation, animation and rendering in
Dream and explicitly requested collaborative strategy: clarify whether separate
tools mean UI tabs, third-party integrations, or both. The owner explicitly
deferred voice narration while improving separate voice-control software.

## Changes

Recorded DREAM-023 as proposed/current planning and DREAM-024 as deferred.
Refreshed current state and history. This is a discussion record, not an approved
implementation specification or a completed media feature.

Proposed approach for discussion:

- Dream owns the experience: real CLI left, Studio/Browser plus a Create area
  right. Create could have Images, Video and Animation views, shared assets,
  previews and jobs. Rendering/export is available within projects. This layout
  remains a proposal; the owner has not selected it.
- Dream owns one job interface for both model tools and UI controls. Generation,
  animation composition and rendering are distinct capabilities underneath it.
  A chat/VLM model plans and reviews; specialized backends generate pixels or
  render timelines. A skill teaches their use but does not supply the backend.
- Candidate local generation adapter: ComfyUI with curated, versioned workflows.
  Candidate cloud adapter: an official image/video API with observable jobs,
  such as fal, subject to owner access, budget and model selection. Keep worker
  environments separate from Dream's harness and MachX environment.
- For animation, assess a deterministic timeline/render contract around Dream's
  existing HTML animation starter and Chromium/FFmpeg. Remotion is a candidate
  if its composition tooling justifies another dependency. Choose one initial
  rendering path; do not build several overlapping render engines at once.
- Shared assets should retain prompts/settings, provider/model/workflow versions,
  references, output hashes and variants, using the existing Library where its
  contracts fit. Jobs retain backend IDs and status across restart. Unknown
  submission outcome requires reconciliation before resubmission; cancellation
  is confirmed by the backend rather than assumed to prevent cost or completion.
- Coordinate local GPU memory with MachX, with resource preflight and explicit
  model/process ownership. Intel support at the backend level does not establish
  compatibility or speed of every video model/custom node. Do not promise
  simultaneous large-model inference and generation before measurement.

Alternative strategies: external-tool handoff (less integration, more switching),
Dream-owned UI plus specialized engines (recommended), or building all generation
and rendering engines inside Dream (much greater scope and maintenance).

Proposed delivery order after design decisions: shared jobs/assets and one image
workflow end-to-end; deterministic animation/render/export; one video-generation
workflow; broaden providers and polish editing only after those vertical paths
are verified. An image-to-video result should be reusable as a timeline asset
without manual path copying. Representative acceptance should exercise GUI and
CLI parity, output validity, restart/reconnect, cancellation, duplicate-submission
prevention, reproducible settings where supported, and actual resource behavior.

## Validation

Read-only planning research used current official documentation:

- [ComfyUI server routes](https://docs.comfy.org/development/comfyui-server/comms_routes)
  document workflow submission, progress and result/queue operations. This
  supports an adapter strategy without requiring the user to operate its node UI.
- [ComfyUI requirements](https://docs.comfy.org/installation/system_requirements)
  list Intel Arc/XPU support; [manual installation](https://docs.comfy.org/installation/manual_install)
  explains separate environments. No local compatibility run was performed.
- [Remotion renderer](https://www.remotion.dev/docs/renderer) documents still,
  frame and video rendering. It is a candidate, not an installed Dream integration.
- [fal queue documentation](https://fal.ai/docs/documentation/model-apis/inference/queue)
  documents image/video results and job lifecycle. In-progress cancellation may
  still complete; returned media URLs have provider-specific access/retention
  behavior. Review those terms and access requirements before selecting it for
  private material. No account or paid request was used in this session.

Source context was inspected in the preceding media-capability discussion:
Dream has image inspection, frame-based demonstrations, Studio, an animation
starter, generic file/shell tools and export helpers; a dedicated generation
backend was not found in built-in source. This session updated planning only.

Observed: `python3 scripts/check_project_tracking.py check --snapshot
/tmp/dream-media-planning-20260905-1048.json` passed: two dated records, all four
session-changed paths covered, with prior history preserved. No model, renderer,
provider or full runtime regression was run.

## Unfinished work

Open product decision: should the first usable release require fully local
generation, or use cloud generation while local generation is qualified? Which
existing services have usable API access? UI arrangement, first image/video
backend, initial model/workflow, budget and renderer choice remain unapproved.

No implementation worker, recording, provider request or GPU process was started.
Voice narration is deliberately deferred, not a blocker to media creation.
Standing owner governance and prior handoffs are unchanged.

## Next steps

Continue the discussion with the concrete Dream UI/backend distinction and a
recommended integration strategy. Resolve local/cloud priority first, then
provider access and representative output tasks. Produce the reviewed design
and implementation breakdown after those decisions. Preserve narration's
deferred status until the owner resumes it.

## Files changed

- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/HISTORY.md`
- `docs/project/updates/2026-09-05-media-strategy.md`
