# DREAM-032 — Dependable project tasks

Recorded: `2026-09-10T13:56:34+00:00`
Work items: `DREAM-032`
Outcome: `implemented`
Actor: Codex lead with guided_workflows, project_workspace and capabilities_diagnostics contributors.

## Request

The owner approved the next harness upgrade pass with "do it". Implement guided
creation, workflow execution/recovery, project context, shared local inference
coordination, explicit model capabilities and CPU qualification. Preserve the
Studio and existing skills/tool/permission behavior. The owner is improving the
inference engine, so no GPU probes, model loads or live-model benchmarks are
permitted in this pass. See the [acceptance criteria](../MASTER_PLAN.md#dream-032-acceptance--dependable-project-tasks)
and implementation plan.

## Changes

Create now has guided reports, data analysis and presentations with required
inputs, saved drafts, explicit Start and durable attempts. The connected App
queues a typed task envelope and claims its exact version before execution.
Chat retains normal streaming and tool events. Missing terminal events leave an
unknown outcome; unsuccessful results fail. A successful terminal result still
requires a saved artifact that passes format/content checks. Downloads retain
immutable checked bytes. The existing animation editor remains available.
The default review shows the goal, sources and filename; detailed instructions
and save location expand on demand. File/store operations run off the UI loop.

Project adds workspace keyword search, explicit fact/constraint/decision/task/file
pins, source fingerprints and context preview. Up to 2000 characters enter the
existing Engine guidance/admission path, with loaded-source notices and warnings.
Changed file pins require revalidation. Read-only recovery inspects authoritative
run ledgers and prepares the existing resume command without auto-execution.

Local HTTP requests share a cross-process, per-user/port lease. Waiting cancellation
does not affect the owner. Cancelled/disconnected requests, early stream endings,
HTTP 408 and 5xx responses retain uncertainty without automatic retry. Controls
can clear only the displayed request ID after explicit server-idle confirmation;
an active lease cannot be cleared. Local optional filing drains its current
request before yielding at a request boundary. Desktop/CLI/direct model launches
share an inherited lock; the child retains ownership after the parent closes its
copy. No process is stopped to acquire either lock.

Capability metadata survives the existing model-scoped launch envelope and the
existing props request. Controls distinguishes reported support, unknowns and
configured limits. Performance modes use only an explicitly reported reasoning
ladder and preserve the current turn. Diagnostics inspects dependency/asset facts,
disposable settings compatibility, supplied wheel structure/hashes and supplied
offline records. Redacted exports use private files and refuse overwrite.

See [ADR-012](../DECISIONS.md#adr-012--coordinated-local-requests-and-inspectable-project-tasks-2026-09-10),
guided tasks, project workspace,
coordination, capabilities
and diagnostics.

## Validation

Observed on 2026-09-10 in the existing `.venv`, using temporary files/SQLite,
fake HTTP and owned CPU child processes. Browser checks launched Chromium with
`--disable-gpu`. Sandbox AnyIO/thread wakeups stalled several tests; bounded CPU
checks were rerun outside that restriction. Contributors identified and stopped
only their own stalled fixture processes. No owned process remains.

Final targeted regression: **472 passed, 2 warnings in 36.89s**, exit 0. The warnings
are existing Starlette/httpx and AnyIO alias deprecations. The session-only guard
`/tmp/dream_cpu_guard.py` rejects real GPU sampling, MachX capability/health calls
and live httpx transports; tests supply explicit fixtures. Exact command:

```bash
PYTHONPATH=/tmp:$PWD .venv/bin/python -m pytest -q -p dream_cpu_guard tests/test_shared_load_lock.py tests/test_desktop_startup.py tests/test_provider_capabilities.py tests/test_capability_integration.py tests/test_diagnostics.py tests/test_local_request_coordination.py tests/test_inference_coordination.py tests/test_idle_work.py tests/test_background_filing.py tests/test_background_usage.py tests/test_backend_resilience.py tests/test_performance_backend.py tests/test_performance_modes.py tests/test_performance_controls.py tests/test_runtime_controls.py tests/test_runtime_profiles_settings.py tests/test_turn_timing.py tests/test_timing_integration.py tests/test_tool_validation.py tests/test_harness_eval_offline.py tests/test_document_reuse.py tests/test_task_guidance_integration.py tests/test_curated_skills.py tests/test_tool_skill_reliability.py tests/test_parallel_subagents.py tests/test_gui_artifacts.py tests/test_chat_attachments.py tests/test_project_workspace.py tests/test_project_workspace_routes.py tests/test_project_workspace_engine.py tests/test_project_workspace_controls.py tests/test_project_workspace_ui.py tests/test_guided_workflows.py tests/test_guided_workflows_routes.py tests/test_guided_workflows_runtime.py tests/test_guided_workflows_ui.py tests/test_media_ui.py tests/test_studio_controls.py
```

Additional observed evidence:

- `.venv/bin/python -m dream.harness_eval --export /tmp/dream-032-offline-records-20260910.json`: **5/5** offline native-tool cases passed.
- `.venv/bin/python -m dream.diagnostics --records /tmp/dream-032-offline-records-20260910.json --export /tmp/dream-032-final-diagnostics-20260910.json`: all performed checks passed; real wheel explicitly not run. Final 472-test run also covers the added guided/project asset requirements.
- `/tmp/dream032_final_checks.py`: 22 Python source files parsed and four JavaScript syntax checks passed. Scoped `git diff --check` passed.
- `UV_CACHE_DIR=/tmp/dream-032-uv-cache uv lock --check --offline`: resolved 111 packages, exit 0; no dependency changes in this pass.
- `/tmp/dream_visual_check.py`: final local fixture screenshots `/tmp/dream-032-guided-desktop.png` and `/tmp/dream-032-guided-mobile.png` captured and visually inspected at 1280x900 and 390x844. Browser fixtures also check project search/pins/resume drafting and explicit Controls recovery.

Failures found and corrected: missing integration methods; a legacy test provider
without base_url; blocking workflow I/O; ambiguous HTTP timeout retries; database
symlink substitution/private permissions; invalid PPTX main content types; JSON
NaN/Infinity/overflow acceptance; and missing new assets in the synthetic wheel
fixture. An intermediate broad run had 429 passes and two failures, including a
contributor's still-red UI-thread regression. These were resolved before the final
472-test run. Early graph search hung and some later calls returned Transport
closed; focused source reads were the fallback. Useful search_code results were
used when available. An attempted nonexistent update-template path was corrected
to the documented project template; no history was rewritten.

Baseline: `/tmp/dream-workflows-20260910-baseline.json`, 471 source hashes. Final
path snapshot: `/tmp/dream-workflows-20260910-final.json`; these are hashes, not
content backups. The pre-existing dirty tree was preserved. No commit or release.
Final tracking check: `.venv/bin/python scripts/check_project_tracking.py check --snapshot /tmp/dream-workflows-20260910-baseline.json` passed with 19 dated records and 57 changed source paths checked. The check was rerun after recording this result.

## Unfinished work

None within this CPU implementation scope. Live model quality, latency, tool/skill
compliance and real engine cancellation/load lifecycle remain unqualified. Native
GTK/WebKit was not opened. No new model load, GPU probe, endpoint request,
inference-engine edit, paid action or publication occurred. The real wheel build
and clean install were not run; hatchling was unavailable in the existing environment.
Owner acceptance and production release are not claimed.

Locks coordinate cooperating Dream clients sharing a user and port, not unrelated
engines, other ports/users or physical GPUs. Engine behavior around inherited
descriptors still needs live lifecycle qualification. A cancelled local request
can require operator reconciliation even if the server stopped; no cancellation
capability is inferred. Workflow checks establish parsable content, not factual
truth or presentation layout. Private directory/identity checks are not isolation
from a malicious same-OS-user process. State persistence uses Linux/proc facilities.
Project search is bounded keyword retrieval with selected document extraction,
without OCR, embeddings or automatic transcription. Resume does not restore every
provider's private conversation state. No owned test process/lock remains active.

## Next steps

Start Dream Desktop normally when the owner is ready. Use Create → Guided tasks,
Project and Controls for the new flows. Keep model loads and real benchmarks
paused until the owner explicitly lifts the constraint. Then qualify one isolated
model/session with fresh resource checks against DREAM-032/031/029 acceptance,
including startup, RAM/GPU streaming, tools, skills, artifacts and cancellation.

For recovery, inspect CURRENT and the dirty tree, take a fresh snapshot and preserve
existing source. Inspect uncertain server activity before clearing its exact request
in Controls. Inspect guided attempts/conversation/files before creating a new
attempt; do not replay blindly or delete held lock files. Separate release and
owner-acceptance work remains DREAM-018/012; no backlog entry authorizes publishing,
spending, stopping owner processes or lifting the benchmark hold.

## Files changed

- `docs/capability-contract.md`
- `docs/desktop.md`
- `docs/diagnostics.md`
- `docs/guided-workflows.md`
- `docs/inference-coordination.md`
- `docs/performance.md`
- `docs/project-workspace.md`
- `docs/project/CURRENT.md`
- `docs/project/DECISIONS.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-10-dependable-project-tasks.md`
- `docs/runtime-controls.md`
- `docs/superpowers/plans/2026-09-10-dependable-project-tasks.md`
- `dream/core/backends/openai_compat.py`
- `dream/core/capabilities.py`
- `dream/core/engine.py`
- `dream/core/idle_work.py`
- `dream/core/inference_coordination.py`
- `dream/desktop/startup.py`
- `dream/diagnostics.py`
- `dream/gui/project_routes.py`
- `dream/gui/server.py`
- `dream/gui/static/controls.js`
- `dream/gui/static/index.html`
- `dream/gui/static/media.js`
- `dream/gui/static/projects.css`
- `dream/gui/static/projects.js`
- `dream/gui/static/workflows.css`
- `dream/gui/static/workflows.js`
- `dream/gui/workflow_routes.py`
- `dream/local/launcher.py`
- `dream/local/load_lock.py`
- `dream/local/machx.py`
- `dream/local/settings.py`
- `dream/projects/__init__.py`
- `dream/projects/recovery.py`
- `dream/projects/workspace.py`
- `dream/tui/app.py`
- `dream/workflows/__init__.py`
- `dream/workflows/service.py`
- `dream/workflows/store.py`
- `dream/workflows/validation.py`
- `tests/test_capability_integration.py`
- `tests/test_diagnostics.py`
- `tests/test_guided_workflows.py`
- `tests/test_guided_workflows_routes.py`
- `tests/test_guided_workflows_runtime.py`
- `tests/test_guided_workflows_ui.py`
- `tests/test_inference_coordination.py`
- `tests/test_local_request_coordination.py`
- `tests/test_project_workspace.py`
- `tests/test_project_workspace_controls.py`
- `tests/test_project_workspace_engine.py`
- `tests/test_project_workspace_routes.py`
- `tests/test_project_workspace_ui.py`
- `tests/test_provider_capabilities.py`
- `tests/test_shared_load_lock.py`
