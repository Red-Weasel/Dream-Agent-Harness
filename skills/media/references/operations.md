# Media operations

Inspect schemas if needed with `tool_schema`. The common structure is
`media_read(action, payload)` or `media_create(action, payload)`.

- `media_read("status")` reports local services and prerequisites.
- `media_create("create", {"title":"..."})` returns the project and initial
  composition. Read it with `media_read("get", {"project_id":"returned-id"})`.
- `media_create("save", {"project_id":"...", "composition":{...},
  "expected_revision":N})` stores edits. Start from the returned composition;
  preserve IDs/assets and use the observed revision. On conflict reread and merge.
- `media_create("import", {"project_id":"...", "path":"workspace/file.png"})`
  adds an existing asset. Confirm its type and intended rights/use; a filename
  does not establish what an image depicts.
- `media_create("render", {"project_id":"...", "format":"mp4"})` submits an
  export; `html` is a preview/export option. Read the returned job status with
  `media_read("jobs", {"project_id":"..."})` and deliver only completed output.
- `media_create("cancel", {"job_id":"..."})` requests cancellation. Inspect the
  final job state before claiming it stopped; reconcile interrupted effects.

Generation through `generate` needs the reviewed local workflow and actual
`resource_confirmed` / `workflow_confirmed` prerequisites. If absent, report the
specific missing service, model or authorization; do not invent confirmation.
Subscription `handoff` stores project_id, provider and prompt. It is not generation.
`complete_handoff` needs the returned job_id and a real workspace output path.
Inspect imported output before treating the handoff as complete.

Use `image_metadata` for image dimensions/format and `see` for pixels when vision
is available. For deterministic cropping/resizing, an installed image library
through `run_bash` can create a new output from a preserved source. Check dimensions
and inspect the result. Do not promise vision, OCR, transcription or generation
capabilities that the active provider/tools do not expose.

## Show progress in Studio

Once a usable draft exists, create a small HTML preview inside the selected
workspace with relative image/video paths and call `show_to_user` on that HTML.
Label it as a draft and state what remains unverified. Use `show_html` for private
checks; it does not open or update the user's Studio. Discover a deferred signature
once with `tool_schema(name="show_to_user")` when listed. For final HTML delivery,
call `done`; also identify the actual media output file. A successful shell render
or screenshot save alone does not present the artifact to the user. Follow any
user preference to withhold drafts rather than forcing a preview every edit.

## Blender and Cycles

Start with `media_read("status")`. Its Blender entry reports executable presence;
version, engines and devices remain unverified until inspected. A file named
Cycles or a static RNA enum is not proof of a working or missing render engine.

After actual resource preflight, use
`media_create("probe_blender", {"resource_confirmed":true})`. No project ID is
needed. Never invent this confirmation, and do not run it while an important
model/render job is using the hardware. The probe starts a separate factory Blender
process with user configuration isolated and Python auto-execution disabled.
It renders nothing and does not save preferences or explicitly enumerate devices.
Cycles registration can initialize native code, so this is not a passive status call.

Read `cycles.build_enabled`, `cycles.registration`, `cycles.error` and the individual
`engines` results. The probe attempts session-local Cycles registration when needed
and tests actual engine assignment. Preserve any registration failure; don't turn
an import/driver error into an unsupported-engine claim. A selectable engine still
does not prove rendering works. The diagnostic process does not enable Cycles in
another Blender process: a generated render script may also need
`addon_utils.enable("cycles", default_set=False, persistent=False)` and must check
its return/error before choosing `CYCLES`.

Choose the reported selectable Eevee identifier instead of guessing from a version
comparison. Blender4.2 changed it to `BLENDER_EEVEE_NEXT`; Blender5.0 changed it back
to `BLENDER_EEVEE`. Do not assume NVIDIA: CUDA/OptiX, HIP and oneAPI serve different
GPU families. A missing `nvidia-smi` only establishes missing NVIDIA tooling;
it says nothing about Intel or AMD GPUs. Detect actual hardware and driver/device
support, then verify the executor can access the relevant devices. An isolated
shell lacking `/dev/dri` does not prove the host has no GPU. CPU availability is
not proof of a working GPU backend.

Software GL (such as llvmpipe) executes Eevee rendering on CPU and can be very
slow for animation. Use it as an explicitly reported fallback when a usable GPU
render path cannot be established; do not choose it solely because an NVIDIA
utility is missing. For physically based photorealism, start with a verified
Cycles pipeline when practical. Cycles supports CPU rendering as well as supported
GPU backends. An older Blender can produce high-quality work; an upgrade alone
will not repair lighting, materials, animation or device access. Preserve working
scenes and renders when qualifying a newer installation.

`devices` and `headless_render` remain `unverified`. Neither a successful probe nor
an installed engine proves a headless OpenGL/EGL context works. Only after these
checks and the existing resource/permission requirements should a short render
qualify the intended pipeline. Reuse a successful sample; don't repeat the same
failing probe or silently switch device, engine or installation.
