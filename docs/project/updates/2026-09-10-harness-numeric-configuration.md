# DREAM-035 — Numeric environment validation

Recorded: `2026-09-10T19:20:25-05:00`
Work items: `DREAM-035`
Outcome: `verified`
Actor: Codex lead; independent operations/privacy judge numeric_gate.

## Request

Continue the authorized harness improvement loop after the accepted
[package phase](2026-09-10-harness-package-assets.md). Phase5 criteria in the
iteration plan
cover28 central numeric settings, safe startup errors and truthful offline
validation without runtime/model activity or disclosure of supplied values.

## Changes

Scope/baseline /tmp/dream035-numeric-baseline.json (541 hashes) recorded before
implementation. Preserve pre-existing dirty work and accepted phases. Shared typed parser, startup preflight and current-environment diagnostics are
implemented. Defaults/aliases remain centralized; reports expose known names only.
No environment values are persisted or changed. Review candidate source frozen.

## Validation

Root graph discovery returned Transport closed; focused source reads/AST inventory
found26 direct numeric declarations plus2 unlimited-alias settings. Previous audit
reproduced import traceback and diagnostic false reassurance. Results follow.
No model, GPU, live provider, benchmark, build, install or external transmission.

Compatibility check found145 passes,1 failure and2 setup errors: the existing
runtime-profile capacity test calls _bounded_task with its original two arguments,
but phase3 had made scope mandatory. Lead is restoring the optional call form
without changing batch ownership. Two Studio fixtures could not bind loopback
inside the sandbox; an approved CPU-fixture retry is planned. The initial test
socket guard also needed SSL imported before patching socket.socket; corrected.

Phase5 candidate1 implemented. After correcting an SSL/socket guard fixture,
5 startup/diagnostic cases failed and help passed before implementation. A shared
28-setting registry now supplies config defaults/conversion and diagnostic checks.
Main preflight emits only a known name/fixed guidance before runtime imports;
NaN/infinity/oversized values fail. Defaults, types and legacy aliases are retained.
The module is required in wheel fixtures. Lead initial75 cases passed; broader
compatibility exposed the helper-signature regression and sandbox loopback bind
errors described above. Corrected/approved combined run175 passed5.16s. Same
cleanup judge independently passed the optional-scope correction and38 runtime/
cleanup tests2.87s. No prior handoff rewritten. Fresh operations/privacy review
of numeric changes pending, source frozen. No live provider/model/GPU operations.

## Unfinished work

Phase5 passed its operations/privacy gate on candidate2. Verifier attribution/admission,
noncentral environment settings, model-specific ranges, native/live qualification
and clean installation remain unqualified. No fixture process or lock is active.

## Next steps

Address carried verifier provenance/admission under a new phase contract and
fresh baseline. Preserve all previous failure evidence.

## Files changed

- `dream/environment.py`
- `dream/core/backends/openai_compat.py`
- `dream/config.py`
- `dream/__main__.py`
- `dream/diagnostics.py`
- `tests/test_numeric_environment.py`
- `tests/test_diagnostics.py`
- `docs/diagnostics.md`
- `docs/superpowers/plans/2026-09-10-harness-adaptation-loop.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/DECISIONS.md`
- `docs/project/updates/2026-09-10-harness-numeric-configuration.md`

Phase5 attempt1 FAIL: any help token bypassed preflight, and three malformed-env
Desktop spellings reached native subprocess creation (blocked by the judge's probe;
no child launched). Judge101 focused tests and472 independent parser cases passed,
as did all28 invalid-name/privacy checks and the missing-helper wheel control.
Lead adds mixed/subcommand help regressions and restricts the exemption to exact
root -h/--help. Same judge reviews the correction; prior evidence is preserved.

Candidate2: new help-route cases first produced5 failures and3 passes. Exact-root
help exemption corrected the bypass;108 numeric/diagnostics/assets/entrypoint
cases passed1.29s. Same-judge re-review pending; source frozen.

Final phase5 verdict: PASS, candidate2. Same judge independently reran108 tests
in1.29s, original3 bypass reproductions,8 malformed routing probes, both root help
controls and28 simultaneously invalid diagnostic settings. No runtime import or
external operation reached guarded probes; no supplied value/unknown key leaked.
Earlier472 independent parser cases remain relevant to unchanged parser code.
Accepted within this contract; no owner or production acceptance. Tracking passed
26 dated records and13 changed source paths after final reconciliation.
