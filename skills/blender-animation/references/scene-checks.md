# Scene edits and draft checks

## Preserve the actual source

Compare scene/script/output names, timestamps and available revision evidence.
Do not assume the newest filename is the requested revision. Inspect existing
render metadata and representative pixels first when the user reports a bad video;
the scene may already be correct and the encode may be stale.

Before editing a loaded scene, obtain its object/collection names, active camera,
render settings, frame range, linked assets and relevant actions/keyframes through
the chosen script or GUI route. Unsaved GUI edits are absent from the disk file.
If the task targets the open session, establish which state is authoritative before
using a headless copy. Avoid startup scripts that delete all objects or regenerate
every material when the user asked for polish.

Use explicit object/data references for scripted edits. Operators can depend on
active object, selection, mode and editor context; inspect those prerequisites
when an operator is necessary. Save a new `.blend` version and the script/settings
needed to reproduce it. Keep linked asset paths valid from the deliverable location.

## Motion

For a new multi-stage animation, establish a rough sequence covering every requested
event before spending heavily on surface detail. Use simple geometry and draft
settings to inspect the full timing, object continuity and camera transitions.
Keep detailed modeling and final rendering as later passes over that saved sequence.
An attractive launch frame alone cannot verify a complete journey or landing.

Translate the requested action into a small set of timed poses or events at the
scene's fps. Inspect existing animation before adding keys. Set keyframes for the
properties actually intended to move, and check the action assigned to the object.
Evaluate start, end and transition frames through Blender's scene evaluation rather
than changing a transform once and assuming animation exists.

Choose interpolation for the motion: constant for discrete switches, linear for
constant-rate movement, and eased curves for acceleration/deceleration. Inspect
between keys for overshoot, unintended pauses, Euler flips, collisions and clipping.
For loops, check position and velocity at the seam and whether the export duplicates
the endpoint. Do not stretch an existing action by changing output fps without
checking how its timing changes.

## GUI editing and checkpoints

When the task requires GUI-only work, use the current editor, mode and selection
as action prerequisites. A shortcut that edits an object in the 3D viewport may
type into a field or affect a different editor when focus changes. After numeric
entry, commit and inspect the displayed persisted value before building dependent
geometry or keyframes. A successfully dispatched type action does not prove that
the intended field accepted the whole value. Reobserve and reacquire field focus
before correcting a mismatch; do not assume a reported number is a Blender rule.

Save an early named .blend checkpoint and subsequent meaningful revisions through
the permitted route. If an external interruption occurs, resume from the actual
saved scene and current visible state. Inspect existing objects, keys and output
settings before repeating construction. A checkpoint proves recoverable work only
after it can be reopened; file existence does not establish a finished animation.

## Camera, materials and light

Evaluate through the active render camera, at final aspect ratio. Check framing,
silhouette, occlusion, clipping planes and focal length across the motion. A pleasing
viewport angle does not establish the render camera. Verify depth of field on the
subject before using it to conceal a weak composition.

When playback exposes a cut-off subject, record the frame or time, active camera
and affected object before editing. Inspect that frame and its neighboring camera
keys through the render camera. A launch can fit at both keys and leave the frame
between them. Adjust the camera transform, tracking or lens that causes the miss,
then check the same interval again. A GUI camera view is provisional evidence;
confirm the corrected interval in the next permitted draft. Keep the previous
draft for comparison and avoid rerendering unrelated intervals just to inspect
one camera correction.

Inspect material nodes and inputs in the installed Blender version. Preserve
textures and intended color-space assignments. Test roughness, metal response,
transparency and normals under the actual lighting. Check exposure/color management
before compensating by increasing light power or saturation everywhere.

Adjust light direction, size and contrast to separate the subject and reveal form.
Inspect contact shadows, reflections and background contrast. Use the same camera,
engine and material setup for comparison; changing all of them hides the cause of
an improvement or regression.

## Render, recover and deliver

Use a reduced-resolution still for composition/material checks and a short segment
for motion checks. Choose frames around the change, plus endpoints when they matter.
Label draft settings and restore intended final settings for the final render.
Lower samples can hide or introduce issues; a draft is not final-quality evidence.

Inspect actual images with `see` when vision is available. For video, inspect motion
through available playback or adequately spaced frames; one still proves neither
timing nor smoothness. If vision is unavailable, report successful file/scene checks
and the remaining visual gap without claiming the composition looks right.

Before encoding, check intended frame range, contiguous filenames, dimensions and
consistent scene revision. Reuse valid frames. If frames are missing, render only
what is missing under matching settings when feasible; avoid mixing incompatible
revisions. Write a new encode rather than overwriting the only known-good output.

Match the input pattern's padding and first frame number explicitly. A sequence
starting above frame one is still reusable; do not rename or rerender it solely
to fit an encoder default. Derive expected duration from the included frame count
and input fps. Preserve a manifest of revision, range, dimensions and settings
when provenance would otherwise become ambiguous. If a few frames are absent,
fill only those gaps from the matching source before encoding.

After encoding, use installed `ffprobe` or an equivalent to inspect container,
streams, dimensions, fps and duration; decode/play representative portions when
available. Confirm the file matches the current output rather than an older MP4.
Report audio as unverified unless inspected. A successful process exit and a queued
Studio preview each prove less than a checked, playable final artifact.
