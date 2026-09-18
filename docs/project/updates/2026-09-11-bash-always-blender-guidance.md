# DREAM-051 — Bash Always allow and Blender fallback guidance

Recorded: `2026-09-11T23:19:58-05:00`
Work items: `DREAM-051`
Outcome: `implemented`
Actor: Codex lead; Astra council_catalog read-only hardware/release investigator and permission reviewer.

## Request

Owner requested Always allow for Bash without waiting for their next restart,
asked what the Cycles fix actually changed, and whether software-rendered Eevee
and Blender4.0.2 are limiting quality. Scope: explicit remembered command approval,
retain consequential-command gates, clarify media guidance and actual installed
state. No approval sought for source changes; owner explicitly authorized them.
Preserved prior dirty work with /tmp/dream-always-blender-baseline.json (637 hashes).

## Changes

Native Bash has Always allow in sandbox (session) and Always allow outside sandbox
(session) for eligible commands. Exact command, Engine identity, workspace and
scope bound the remembered grant. Sandbox requires verified containment; it never
becomes host access if containment fails. Host cache hits reissue an exact-command
host authorization. Plan/invalid scope denials still win; recognized consequential
shell effects and red-team scopes have no Always choice. Changed context during
probe/queue/approval invalidates the answer. /new and exit discard grants. Terminal
uses a and ha; desktop accepts those same explicit choices. One-off choices remain.

This is not blanket permission for new commands, a persistent grant, a command
prefix policy, or a source pin for scripts. Existing Auto policy separately allows
routine contained work without prompting. Core policy changes are explanatory
comments/docstrings only; the remembered-choice implementation is in App.

Media status now explicitly explains that missing nvidia-smi is not evidence of
absent Intel/AMD GPUs, Intel Cycles uses oneAPI, and Cycles can also render on CPU.
The model-facing media reference explains software GL as a potentially slow
fallback, distinguishes host hardware from executor device visibility, and favors
verified Cycles for physically based photorealism where practical. No claim that
software rendering automatically lowers image quality or that upgrading fixes
scene quality/device configuration. README/operator docs updated.

Read-only contributor observation: installed Blender4.0.2+dfsg-1ubuntu8 remains.
PCI identifies two Intel8086:e223 devices on xe plus an Intel iGPU on i915; Intel's
own llm-scaler metadata maps e223 to B70. Its isolated namespace lacks /dev/dri and
xpu-smi initialization failed, so host GPU readiness was not established. Official
release research identified Blender5.2.1 LTS as an upgrade candidate; no installation
or Blender process ran. Prior DREAM-039 added diagnostics, not a verified render
or package replacement. Preserve existing frames and MP4.

## Validation

Final bounded host gate:160 passed,2 existing Starlette/AnyIO deprecation warnings
in4.25s; /tmp/dream-always-blender-gate.txt. Command: timeout120 .venv/bin/python
-m pytest -q tests/test_bash_session_approval.py tests/test_auto_bash_recovery.py
 tests/test_permission_hardening.py tests/test_backend_permissions.py
 tests/test_loop_workspace_writable.py tests/test_runtime_approval_budget.py
 tests/test_policy.py tests/test_blender_capabilities.py
 tests/test_blender_integration.py tests/test_media_service.py tests/test_media_routes.py.
GPU-disabled Chromium clicked both actual desktop choices through an authenticated
Studio server and confirmed no second prompt. Mock executor grants; no owner shell
command or live provider executed. Other permission/media fixtures remained green.

Initial tests before implementation:10 failed,4 passed. One later red-team scope
fixture lacked mandatory roots/expiry; corrected it to a changed read-root scope.
Intermediate restricted gate52 passed,1 skipped; final host gate had no skips.
Astra read-only permission review found no blocking defect and independently ran
44 approval tests passing. It emphasized that exact command approval does not pin
referenced script contents; this is documented. Not a fresh-context verifier.
Python compile and git diff --check passed. Lead graph access returned Transport
closed, so focused source reads were used. No GPU preflight-qualified render,
benchmark, package/driver change, live model call or owner restart was performed.
Final snapshot tracking passed:65 dated records,12 changed source paths checked.

## Unfinished work

Live Cycles registration, GPU-device availability in Dream's executor and actual
render qualification remain unverified. Software GL use is owner/model-reported;
this session did not measure the running renderer. Blender upgrade remains a
recommendation, not performed. Per-command grants will still prompt for a changed
command when Auto cannot independently allow it; consequential commands continue
to prompt. Unknown effects in arbitrary scripts cannot be proven safe by this
classifier. No cross-session persistence or CLI-provider permission bypass.

## Next steps

Owner may restart whenever ready; source edits are complete and do not need an
immediate restart. After loading this build, choose the appropriate Always option
when an eligible Bash prompt appears. Use sandbox scope when sufficient; host
choice is explicitly broader. Finish current work before qualifying a newer
Blender alongside the existing installation and testing Cycles/device access.
No active commands, model loads or locks owned by this task remain. No GitHub
publish or owner acceptance performed.

## Files changed

- `README.md`
- `docs/execution.md`
- `docs/media.md`
- `docs/project/CURRENT.md`
- `docs/project/DECISIONS.md`
- `docs/project/MASTER_PLAN.md`
- `dream/core/policy.py`
- `dream/tui/app.py`
- `dream/media/service.py`
- `skills/media/references/operations.md`
- `tests/test_bash_session_approval.py`
- `docs/project/updates/2026-09-11-bash-always-blender-guidance.md`
