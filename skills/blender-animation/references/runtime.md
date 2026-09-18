# Runtime qualification

These are Dream tool contracts, not a promise that every model exposes them.
Inspect the session's tool inventory and use `tool_schema` for listed deferred
signatures. Other harnesses need equivalent, actually available controls.

## Inspect before launching

`media_read(action="status")` passively checks Blender executable presence. It
does not launch Blender or qualify version, engines, devices or headless rendering.
Inspect an existing script as text before executing it. Opening a supplied `.blend`
must not implicitly enable embedded Python; use disabled automatic execution and
explicitly reviewed script inputs. Keep the user's default installation/preferences.

After actual resource preflight,
`media_create(action="probe_blender", payload={"resource_confirmed":true})`
launches an isolated factory process with a fixed diagnostic script. Only set the
confirmation when the required check has actually occurred. The probe may initialize
native Cycles code; it renders nothing and does not enumerate/qualify devices.
Do not launch it alongside important resident model/render work without established
resource headroom and applicable authorization.

Read its Blender version, `cycles.build_enabled`, `cycles.registration`,
`cycles.error` and individual `engines` results. An error is not proof an engine is
unsupported. A selectable engine is not proof a render works. Registration lasts
only in that process. A later script may need session-local Cycles registration
with `addon_utils.enable("cycles", default_set=False, persistent=False)` and must
check errors before assigning `CYCLES`.

Use the observed selectable Eevee identifier. Do not hardcode an identifier from
a remembered version rule. Use installed API information or authoritative matching
version documentation for changed properties, nodes or animation APIs.

When a remembered identifier fails, inspect the current process's RNA enum items
and the relevant object's exposed properties before the next assignment. Node
socket names and action storage can also change; inspect the actual node/action
instead of replacing one guessed name with another. Keep capability discovery
separate from rendering. A configuration task needs no render merely to prove an
enum can be assigned. Qualify a renderer only when the requested output needs it.

## Choose and qualify the device

Record the executable/version, engine, intended CPU/GPU device, executor and actual
render settings alongside the draft. Establish what hardware the host has and what
the process can access. Missing `nvidia-smi` establishes missing NVIDIA tooling;
it says nothing about Intel/AMD devices. CUDA/OptiX, HIP and oneAPI have different
hardware/driver requirements. A container without device access cannot characterize
the host's GPU capability.

Choose a working path appropriate to the brief. Cycles can render on CPU or
supported GPUs; Eevee needs a usable graphics context. Software GL such as llvmpipe
can render through CPU and be slow. Report it as a fallback when justified by
observed access failures. Do not infer image quality solely from CPU versus GPU.

An inexpensive actual sample, after resource checks, qualifies only the exact
configuration tested. Log its outcome and preserve diagnostics. A headless-context
failure calls for inspecting context/device access, not repeatedly launching the
same render or silently altering engines. Stop expanding the workload when the
sample fails or resources are unknown.

## Tool boundaries

`media_create(action="open_external", payload={"project_id":id,"path":path})`
opens an existing workspace `.blend` with automatic Python execution disabled.
Use an observed project ID and schema. It neither animates the file nor provides
mouse/keyboard control. Dream's `eval_js` targets HTML preview, not Blender.

`media_create(action="render")` exports Dream media compositions; it is not a
general Blender scene renderer. For Blender work, an available permitted shell can
run an inspected script with the chosen Blender binary and write to a new workspace
output directory. Blender command-line arguments are order-sensitive: load the
intended scene before executing its modification script or rendering. Confirm paths
and installed command help rather than guessing a universal render command.

Import an existing output with
`media_create(action="import", payload={"project_id":id,"path":path})`
when the task needs a Dream media asset. This registers the file, not the quality
of its contents. `media_read(action="jobs")` tracks Dream jobs; an independently
launched shell render needs its own returned process/log evidence.
