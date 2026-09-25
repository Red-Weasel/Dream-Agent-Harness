---
name: blender-animation
description: Create, animate, render or polish Blender scenes and existing .blend projects with verified motion and output artifacts.
---

# Blender animation

Inspect existing scenes, scripts and outputs before rebuilding or rerendering.
Skill loading alone does not establish improved task performance.

1. Inspect workspace `.blend` files, scripts, assets, frames and video. Identify
   the deliverable, authoritative revision, fps, frame range and dimensions.
   Preserve source in a new version or authorized working copy.
2. Prefer [live Blender](references/live.md) when `blender__*` tools are listed:
   MCP for scene and code, screenshots or computer use to look, the owner
   steering in chat. Else run inspected Blender Python via `run_bash`;
   reuse the authoritative script. Use the GUI when requested or needed for
   interactive state, only with actual desktop controls. `media_create` opening
   a `.blend` supplies neither GUI controls nor a render.
   To model a real object from photos, follow
   [reference modeling](references/reference-modeling.md).
3. Read [runtime qualification](references/runtime.md) before choosing a renderer
   or launching Blender. Check resources, version, engine and accessible device;
   only an actual render qualifies that pipeline. Do not assume NVIDIA or force
   software GL because its utilities are missing.
4. Make a focused change. Read
   [scene and render checks](references/scene-checks.md) for motion, camera,
   material, lighting and recovery decisions. Keep existing IDs, collections,
   keyframes and assets that still serve the brief.
5. Check a cheap still or short draft with `see` when available; verify timing
   through playback or sampled frames before expanding the render. Reuse valid
   frames when only encoding or delivery failed.
6. Deliver the editable source and requested media with verified paths, format,
   duration and dimensions. For a Studio preview, use a workspace HTML wrapper
   referencing the output, then `show_to_user` or final `done` when listed. State
   any unverified motion, audio, rendering or appearance.

For requested teaching or transfer checks, read
[demonstration cases](references/demonstrations.md). Recording and evaluation
require their own request.
