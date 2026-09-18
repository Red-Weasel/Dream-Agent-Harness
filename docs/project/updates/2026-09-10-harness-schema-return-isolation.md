# DREAM-035 — Isolate returned tool schemas from registered constraints

Recorded: `2026-09-10T21:46:34-05:00`
Work items: `DREAM-035`
Outcome: `verified`
Actor: Codex lead implementation/docs; independent astra_schema_return_gate.

## Request

Implement the phase12 contract
after phase11's fresh independent PASS. Detach both cold and cached tool-schema
returns from registered constraints, preserving explicit source changes and behavior.

## Changes

Baseline /tmp/dream035-schema-return-baseline.json preserves the current dirty tree.
Lead owns only the cold-return copy boundary in openai_compat.py and new
 tests/test_schema_return_isolation.py, plus the shared docs listed below. No other
source writer is active. Acceptance precedes edits in the shared phase12 plan.

The exact original/candidate Astra audit showed synthetic nested mutation can
change registered validation constraints. No ordinary production mutation was
identified; cache keys/fingerprints and admission still track actual source content.
This is defensive object isolation, not an observed user-input or permission exploit.

## Validation

Independent audit /tmp/dream035-schema-alias-audit-report.md and probe are prior
reproduction evidence, not a post-fix test result. Graph-first lookup timed out;
focused source reads completed after a delay. First snapshot attempt reused an
existing filename and failed safely with File exists; the new unique baseline
above succeeded without overwriting prior evidence. Tests and gate pending.

## Unfinished work

None within the accepted schema-return contract. Fresh fixture-isolation audit
now investigates unintended hardware telemetry startup before another broad run. No live model/provider/GPU/browser/benchmark/build/install qualification.
No main Git staging/commit/publication or owner-process operation.

## Next steps

Lead records the fixture audit and declares a narrow next-phase isolation contract
before changing tests. Preserve the full-suite diagnostic and correct CPU claims. Broader
production/native/install acceptance remains unperformed.

## Files changed

- `dream/core/backends/openai_compat.py`
- `tests/test_schema_return_isolation.py`
- `docs/harness-review-packet.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/superpowers/plans/2026-09-10-harness-adaptation-loop.md`
- `docs/project/updates/2026-09-10-harness-schema-return-isolation.md`

Baseline contains557 hashes. Initial24 failures were a test setup error: _pinned
is a frozenset, not a mutable set. Replaced fixture .add with a union assignment;
no production change for that failure. Log /tmp/dream035-schema-return-red.log.
Corrected pre-fix regression then produced12 failures/12 passes in0.37s. Failures
showed cold-return description/constraint aliasing, invalid arguments reaching
permission, and old returned fingerprints changing with source edits. Hot-return
and normal-turn controls passed. Log:
/tmp/dream035-schema-return-red-corrected-fixture.log.

Production correction is exactly return deepcopy(sent) at _request_tools's cold
return. Existing cache and hit copying stay unchanged. No _tool_schema, validation,
permission, selection, registry or transport rewrite. Combined185 tests passed,
0 skips,2 existing asyncio-marker warnings in3.88s, exit0. Command: DREAM_ROOT set
to /tmp/dream035-schema-return-runtime, PYTHONDONTWRITEBYTECODE=1, timeout45s,
.venv/bin/python -m pytest -q -p no:cacheprovider, with new return-isolation tests
plus the exact161-test selection in phase10's implementer report. Log:
/tmp/dream035-schema-return-green.log. No new full-suite result is claimed.

Exact two-file diff /tmp/dream035-schema-return-candidate.diff and hashes
/tmp/dream035-schema-return-hashes.json reconstruct the baseline backend exactly.
New24 cases cover cold/hit, full-fit/deferred, description/properties/required,
validation before permission, authoritative constraint edits and two ordinary fake
turns with output256/effort high. Main source is frozen for a new fresh Astra gate.

Phase11 judge corrected its temporary report: two existing tracking tests staged
and committed disposable fixture repositories, contrary to its blanket no-commit
statement and assignment constraint. No main Git mutation occurred. Its PASS and
candidate evidence are unchanged; no extra run followed that clarification.

Final integration contract, after this gate: run the repository's default full
pytest suite with existing model-loading guards and no --with-models. Use disposable
DREAM_ROOT, existing dependencies, a360s outer limit,20s faulthandler output and
/tmp log. Reuse the reviewed temporary Chromium launch wrapper that appends
--disable-gpu; it does not change tracked browser code. This requests CPU/software
rendering but does not measure hardware-device use. Owned loopback/browser and
subprocess fixtures are within the existing CPU test scope; no owner process,
actual provider/model, benchmark, package build or installation is authorized.
Existing project-tracking tests may stage/commit their synthetic disposable Git
fixtures; no main Git staging/commit occurs. Retain skips, warnings and failures.
Prior sandboxed Chromium launch failed with Operation not permitted; a required
sandbox exception for the same owned test workload may be requested through the
automatic reviewer. Do not change production or weaken fixtures merely to pass.

Fresh phase12 gate PASS, zero blocking findings. Independent185 tests passed,
0 skips,2 existing warnings in3.86s. Eight old/current cold/hit full/deferred
nested-enum cases verified recursive object detachment. Two authoritative nested
edits changed selection/compiled validation and reached unchanged admission.
Three old/new full/deferred/empty pairs produced equal complete payloads/context
reports over12 genuinely cold-start fake turns. Report:
/tmp/dream035-schema-return-gate.md; probe/log paths are recorded there. Exact old
backend reconstruction and frozen candidate hashes passed. No correction cycle.

The automatic reviewer allowed the final CPU integration sandbox exception.
Owned exec session93483 runs the default suite with the declared temporary runtime,
model-loading guards and Chromium --disable-gpu wrapper. Exact command:
DREAM_ROOT from mktemp -d /tmp/dream035-final-schema-integration-XXXXXX,
PYTHONPATH=/tmp/dream035-astra-preview-diagnostic, PYTHONDONTWRITEBYTECODE=1,
timeout360s .venv/bin/python -m pytest -q -ra --tb=short -p no:cacheprovider
-p dream035_cpu_preview_diag -o faulthandler_timeout=20, output
/tmp/dream035-final-schema-integration.log. Run still active; no success claim yet.

Final default-suite result:2829 passed,35 skipped,7 warnings in238.95s, exit0.
Skips are34 model-loading guards and1 migration-state fixture. Warnings retain
Starlette/httpx deprecation, AnyIO alias deprecation,2 asyncio-marker warnings and
3 SDK evaluator allowed-read-tool callback-shadow warnings. Log:
/tmp/dream035-final-schema-integration.log. No Future-exception/TargetClosedError
was reported, but faulthandler emitted a20s thread dump at72% and the suite resumed.

Qualification correction: that dump includes a running GpuSampler daemon at
telemetry/gpu.py:172. The --disable-gpu browser wrapper does not isolate Dream's
telemetry sampler. Source inspection found permission_hardening's /new fixture
calls App.start with only Engine/_welcome mocked, allowing real default monitor
startup. The interrupt-path owned REPL driver also starts an App with no monitor
isolation. Exact hardware accesses are not captured by the dump, but the intended
CPU-only/no-GPU-probe qualification is NOT established and its earlier wording is
withdrawn. The suite's test counts stand separately. No extra hardware probe was
run to investigate; a fresh Astra read-only fixture audit is active.

When this gap became apparent, lead attempted Ctrl-C through its owned session;
write_stdin reported exit0 and the complete summary was already present. The suite
had finished, so no interrupted-run count is claimed. No owner process was signaled.
A future broad rerun must first isolate telemetry startup in those test paths.

Fresh audit correction to the preliminary interrupt-driver attribution: its parent
sets DREAM_MONITOR=0, so that startup path is already isolated. The second verified
unisolated path is test_pty_e2e.py forcing DREAM_MONITOR=1 and tests/pty_driver.py
starting the real App with no fake sampler. The /new unit fixture is the likely
parent-thread source, but the dump alone cannot prove roots or exact origin.
No hardware read or default sampler construction was used to investigate.

Final phase12 tracking passed33 dated records/7 changed source paths. Phase13
fixture-isolation acceptance is now planned in the shared plan before edits.
