# DREAM-035 — Reported vision and configured image handling

Recorded: `2026-09-10T20:18:00-05:00`
Work items: `DREAM-035`
Outcome: `implemented`
Actor: Codex lead; vision_status_gate fresh independent status/privacy reviewer.

## Request

Continue the model-adaptation loop after the accepted
[delegate checkpoint](2026-09-10-harness-delegate-completion.md). Follow phase8's
predeclared contract in the iteration plan.

## Changes

Baseline /tmp/dream035-vision-status-baseline.json contains548 source hashes.
Lead owns only capability/generation status reporting and its tests/docs. Report
actual tool-image configuration and see registration separately from normalized
vision facts; add bounded disagreement guidance. No automatic capability enabling,
image encoding/dispatch, provider schema, permission or effort behavior changes.

## Validation

Read-only prior audit reproduced disagreement in both directions using synthetic
metadata; Engine supplies no provider_metadata and raw MachX vision keys remain
unverified. Root graph attempts timed out or returned Transport closed; focused
reads followed. No implementation/test verdict yet. The preceding full suite's
2666 passes are phase7 evidence, not a test of this new change.

## Unfinished work

None within the bounded phase8 implementation contract. Live vision transport,
native Desktop qualification and owner acceptance remain pending.
No owned fixture process or lock active. No model/GPU/provider/benchmark/build.

## Next steps

Take a new baseline and implement phase9's screenshot contract after its declared
CPU reproduction. Use a fresh independent tool-contract judge.

## Files changed

- `dream/core/backends/openai_compat.py`
- `tests/test_vision_status.py`
- `tests/test_active_settings.py`
- `docs/capability-contract.md`
- `docs/runtime-controls.md`
- `docs/harness-review-packet.md`
- `docs/superpowers/plans/2026-09-10-harness-adaptation-loop.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/DECISIONS.md`
- `docs/project/updates/2026-09-10-harness-vision-status.md`

Candidate1:28 new cases failed on missing configuration rows before implementation;
then28 passed0.32s. Production change is bounded status reporting only. Matrix
includes known/unknown/malformed metadata, registration independent of the image
flag, state and file/network guards, stale model facts and explicit effort. Added
two CPU Chromium Controls cases for both mismatch directions, actual On/Off versus
Supported/Unsupported rendering, no mutating controls/calls and480px layout.
Broader focused CPU/browser run active; fresh independent gate follows its result.

Lead combined run completed105 passed,2 existing Starlette/AnyIO deprecation
warnings in20.94s: timeout90 .venv/bin/python -m pytest -q
tests/test_vision_status.py tests/test_active_settings.py
tests/test_provider_capabilities.py tests/test_capability_integration.py
tests/test_model_adaptation.py tests/test_performance_backend.py
tests/test_runtime_controls.py tests/test_studio_controls.py --tb=short.
Fresh vision_status_gate reviewing; source frozen, backend hash
57d023b1e27f3fa593e8be146ac9d02bb1ee71f3a432911dc1782bb505fb3f2f.

Read-only screenshot audit independently found invalid destination extension can
reach preview loading before its field correction. Six malformed cases passed
the current schema validator; only bad extension reached the synthetic loader.
Evidence /tmp/dream035-screenshot-schema-audit.py. Future contract pending; no
screenshot source edit, capture or live browser operation occurred in that audit.

Final checkpoint `2026-09-10T20:32:00-05:00`: fresh vision_status_gate PASS with no
blocking findings. Independently105 tests passed2 deprecation warnings19.75s;
64 metadata/configuration/registration probes and36 model/effort/performance
transitions passed. Four extra CPU browser tests passed3.27s at360px, including
unknown/malformed facts, missing see, GET-only observation and no overflow/errors.
Probes blocked file/socket/subprocess operations, checked detached reports and
private canaries, and preserved backend/provider state. Evidence:
/tmp/dream035-phase8-independent-gate.py and
/tmp/test_dream035_phase8_browser_gate.py. Reverting the docstring and nine added
lines reproduced the exact phase7 backend hash; no dispatch, permission or image
encoding changes. A first reconstruction omitted the docstring and mismatched;
that inspection mistake was corrected, not a source failure. Graph inventory
worked for the judge; search hit automatic approval-review timeout, then focused
reads followed. No live/native/full-suite rerun/model/GPU/build/install/benchmark.
The preceding2666-pass full suite is phase7 evidence;105 focused cases cover this
bounded status change. Tracking passed29 dated records/11changed paths. No owned
fixture process or lock remains.
