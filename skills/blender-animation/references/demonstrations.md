# Blender demonstrations and evaluation

For a user-requested teaching example, use a synthetic scene and exclude personal
assets. Do not begin capture merely because the animation skill was loaded.

Capture the initial scene and chosen source of truth, Blender version, engine,
device evidence, fps and render dimensions. Preserve the script or before/after
`.blend` versions as well as screenshots. Show meaningful key poses, curve changes,
the render camera and a cheap draft with its settings. Include the visual defect,
the focused correction and what observation confirms the correction.

Dream's `demonstration_read` exposes sampled frame paths; inspect them with `see`
when available: join its returned `directory` with `frames[i].file` for the image
path; retain the relative frame name in draft citations. Sampled frames do not
prove the precise shortcut, interpolation
setting or Python command used between captures. Obtain scene/script evidence or
label those steps as inferred. `demonstration_draft` accepts steps with an action,
supporting frames, `basis` of `visible`, `inferred` or `user-confirmed`, and an
optional `verify` check. It creates a reviewable draft, without installing or
replaying it. User-started recording remains separate from these analysis tools.

Controls → Learn → Annotate evidence stores manual application/version and
frame-linked before/action/after/outcome notes. Use their saved `evidence_ids`
when drafting; user-confirmed basis requires matching saved human action evidence.
Read annotations with `event_offset`; follow `shortened_fields` through
`event_field`, `field_offset` and `next_field_offset`, using `evidence_revision`
to reject an edit made while paging. These are annotations, not captured input events.

## Transfer evaluation

When evaluation is authorized, compare the same model and tools on disposable
fixtures with and without the skill. These are proposed cases, not passed tests.

| Case | Observable outcome |
| --- | --- |
| Existing scene needs smoother camera motion | Keeps assets and framing, changes relevant keys/curves, checks motion between keys. |
| Valid frame sequence but outdated MP4 | Identifies revision mismatch and re-encodes valid frames without rebuilding the scene. |
| No NVIDIA utility; host has another GPU family | Distinguishes tooling, device access and render evidence before choosing a backend. |
| Blender installed but probe/render unavailable | Reports exact qualification gap; does not invent a successful render. |
| Current GUI has unsaved changes | Establishes scene authority before processing the disk copy. |
| Different Blender version or viewport | Rechecks API identifiers or visible controls instead of replaying old coordinates. |

Record artifact validity, preservation of existing work, motion/visual checks,
unsupported claims, resource use and outcome. Only inspected run evidence supports
claims of Fable/DeepSeek improvement. A loadable skill is a packaging result, and
a demonstration is instructional evidence; neither establishes weight training or
reliable transfer on its own.

The repository's opt-in `scripts/skill_transfer_check.py` also covers existing
frame inventories and changed engine identifiers through synthetic action-plan
replay. Its state transitions can expose redundant renders, incomplete encodes
and unsupported assignments. It never launches Blender or validates real pixels,
motion, FFmpeg output or GPU access. Use its private report as simulator evidence
only; actual Blender transfer still needs authorized scene runs with the target
model and installed runtime. See the computer-use evaluation reference for the
same-model comparison's scope and limitations.
