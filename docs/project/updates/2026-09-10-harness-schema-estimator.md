# DREAM-035 — Schema selection and admission share estimated costs

Recorded: `2026-09-10T21:19:34-05:00`
Work items: `DREAM-035`
Outcome: `verified`
Actor: Codex lead integration/docs; astra_schema_implementer; independent astra_schema_gate.

## Request

Continue after the accepted [screenshot/Astra checkpoint](2026-09-10-harness-screenshot-contract.md)
under phase10's contract.
Use fresh Astra implementation and a separate fresh independent judge, as requested.

## Changes

Main-workspace baseline /tmp/dream035-schema-estimator-baseline.json has552 hashes.
Implementer owns isolated copies of tool_budget_schemas.py and new schema estimator
tests; context_budget.py only if an interface change is demonstrated necessary.
No main-workspace source edit until reviewed diff/hash integration. Lead owns all
shared tracking/docs. Existing dirty source and installed dependencies are retained.

Ruling: an isolated source snapshot is used because HEAD omits the current build.
No new commits or competing agent backlog. Content diffs and hashes, not Git HEAD,
will establish exactly what gets integrated. No other source writer is active.

## Validation

Astra's pure-module reproduction prices a multilingual optional schema at358 in
selection but1153 in admission, causing a4096-window refusal that deferral avoids.
This is estimated-budget evidence. Fake-backend, implementation and independent
gate results are recorded below. Prior2767 full-suite passes cover phase9.

## Unfinished work

None within the accepted estimator contract. A pre-existing first-return nested
schema alias is under a separate read-only audit; normal-path impact is unproven. Native/provider/clean-install and
unattributed prior Playwright diagnostic remain unqualified. No model/GPU/live
provider/benchmark/build/install/commit or owner-process operations.

## Next steps

Take a fresh phase11 baseline and correct only the verified source-inventory
ignore rule with an independent gate. Retain separate alias audit findings.

## Files changed

- `dream/core/tool_budget_schemas.py`
- `dream/core/context_budget.py`
- `tests/test_schema_estimator.py`
- `tests/test_deferred_schemas.py`
- `tests/test_schema_deferral.py`
- `tests/test_schema_budget_real.py`
- `tests/test_runtime_profiles.py`
- `docs/runtime-controls.md`
- `docs/harness-review-packet.md`
- `docs/superpowers/plans/2026-09-10-harness-adaptation-loop.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/DECISIONS.md`
- `docs/project/updates/2026-09-10-harness-schema-estimator.md`

Scope update before edits: isolated tests/test_deferred_schemas.py may update its
three estimator-arithmetic tests only. They assert the obsolete chars/4 formula,
sum-of-individual-schemas representation and empty cost0. Lead verified account()
always applies the counter to the entire tools array, so empty [] currently costs1
under unchanged admission. Align these arithmetic assertions with that existing
contract; preserve zero-tool selection and all pinned/discovery/budget assertions.
Any further existing-test change needs separate evidence and scope reconciliation.

Isolated checkout contains505 verified source files and a read-only dependency
symlink, at /tmp/dream035-schema-estimator-workspace. Brief:
/tmp/dream035-schema-estimator-brief.md. Actor astra_schema_implementer owns only
declared isolated paths; report/diff pending. Its first four-file baseline could
not collect because the main source snapshot omitted dream/memory/tasks.py.
Lead confirmed .gitignore:21 memory/ ignores that source file, then copied the
unchanged dependency and added hash98dd1523b967f17e40524ddfbbdba271082e6fb3047704124b7f5b555566f77e
to the isolated manifest. This is an isolation/inventory failure, not a code-test
regression. A separate fresh Astra read-only inventory audit investigates scope;
no ignore-rule or packaging change is authorized in phase10.

Unmodified two-file baseline (deferred_schemas/schema_deferral)33 passed. Exact pure
reproduction matched358/1153 and -19remaining; normal fake ask adds the existing
user ID suffix, giving history15 and -23remaining/refusal4119 with no HTTP payload.
The new regression will preserve that normal message handling rather than removing
existing IDs to match the pure arithmetic. Implementation remains isolated.

Compatibility scope before edits: lead reviewed
/tmp/dream035-schema-test-compat-proposal.diff for exactly five existing tests:
schema_deferral's fitting/native and large/catalogued cases; schema_budget_real's
array ceiling and small-window/native preservation cases; runtime_profiles'
small-context discovery case. New accounting makes full native schemas1802 at a
1638 target, fat pins+empty discovery1747 at1638, and actual8K mandatory floors
1406/1364 above1228. Mandatory schemas were always non-negotiable; an unconditional
percentage assertion now contradicts that policy rather than proving a safe fit.

Ruling: authorize the proposed five-test correction, preserving original native
schema/discovery assertions. Fitting-case windows must establish their precondition;
add independent fixed16K controls for the previously used native/fat cases. If the
mandatory floor exceeds target, assert exactly full mandatory schemas plus empty
discovery and no optional schemas/catalog prose; otherwise keep the original
ceiling and explicit budget bound. This does not authorize shrinking mandatory
schemas, removing assertions or increasing runtime limits. Fresh gate must verify
these compatibility changes independently. Before them, combined82 passed/5 failed;
35 new regressions,71 other focused cases and180 separate deterministic probes
passed. One broader run stalled after60 progress dots and was interrupted; no
success count is claimed for it. Exact command/cause evidence remains requested.

The same test file's stale <=15% module comment is authorized for correction to
the mandatory-floor exception; no additional assertion changes. Fresh source
inventory audit completed: exact missing Python source tasks.py, all164/163 counts
and actual snapshot failure verified. Its /tmp synthetic one-line /memory/ control
detects source mutations and preserves7/7 private exclusions. Phase11 is planned
in the shared plan after phase10; no ignore-rule change or wheel claim yet.

Isolated candidate complete, DONE_WITH_CONCERNS: only tool_budget_schemas.py changes
production behavior (shared admission estimate, escaped catalog text cost and full
array framing for allowance). context_budget.py remains unchanged. Six authorized
source/test paths differ; isolated/source hashes match the reported manifest.
Report /tmp/dream035-schema-estimator-report.md; diff
/tmp/dream035-schema-estimator-candidate.diff; hashes
/tmp/dream035-schema-estimator-hashes.json. Production candidate hash
22b3e51ecbbc293ba309ae1c92368b97aa53d343916c153a6fe47b7a2728fd7d.

Final author run161 passed,0 skipped,2 existing asyncio-marker warnings in3.98s,
including35 new regressions.180 deterministic arithmetic probes passed. Earlier
unmodified remaining baseline22 passed2warnings2.15s; initial new regression30
failed/2 mandatory controls passed0.38s. The exact interrupted exploratory command
was pytest -q test_compaction.py test_performance_backend.py
test_harness_backend_quality.py test_local_tuning.py test_tool_validation.py
(all under tests/), owned session93414 exit130. Its60 progress dots do not establish
a count or cause. Later bounded settings/error selections passed; no launcher run
is claimed complete. The final test-module comment changed after testing, with no
executable/assertion change; all six files passed AST and whitespace checks.

Fresh astra_schema_gate now verifies spec and code quality against the exact diff,
brief and proposed operator note /tmp/dream035-schema-operator-note.md. Candidate
and main source remain frozen; no integration yet. In particular, the judge must
verify the mandatory-floor compatibility assertions rather than merely accepting
the author's/lead's rationale. No new model quality or package evidence is claimed.

Final gate PASS, zero blocking findings. Fresh Astra independently ran161 tests,
no skips,2 existing asyncio-marker warnings in3.94s. It also passed336 boundary
cases,336 escaped-catalog cases,12 original/candidate fake turns,16 cache/ranking
assertions and2 actual reveal-overflow turns,702 additional cases/assertions.
It verified all506 baseline hashes, six candidate hashes and exact diff. Report:
/tmp/dream035-schema-estimator-gate.md. The judge accepted the five compatibility
corrections, including original fixed-window controls and exact mandatory floors.

The first judge probe stopped on nested first-return aliasing. Original/candidate
comparison confirmed it predates this change; cache-hit copies are detached and
no normal production mutator was demonstrated. The original failed probe remains
/tmp/dream035-schema-gate-probes-first-attempt.log. A fresh separate Astra audit
traces possible impact without changing this gate's scope or rewriting its result.

Lead verified all six original/main and candidate hashes before copying only the
reviewed files. context_budget.py remains unchanged. Main-workspace verification
repeated the report's exact161-test command with DREAM_ROOT set to
/tmp/dream035-schema-main-runtime and -p no:cacheprovider:161 passed,0 skipped,
2 existing asyncio-marker warnings in6.21s, exit0. Log:
/tmp/dream035-schema-main-tests.log. No new full-suite run is claimed.
Runtime controls, manual review packet and ADR-023 explain shared estimates,
optional membership changes and mandatory-floor exceptions. Main graph search
returned Transport closed; focused reads followed. No main model/provider/browser
run, build, install, commit, publication or owner-process operation occurred.

The first final tracking attempt rejected a mistakenly future-dated lead timestamp,
so it could not recognize the new handoff. Corrected to actual local time; no
source omission was hidden by this clerical failure.
Final phase10 tracking passed:31 dated records and13 changed source paths.
