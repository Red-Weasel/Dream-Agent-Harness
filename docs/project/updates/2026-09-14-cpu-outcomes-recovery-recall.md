# DREAM-073 — CPU outcomes, recovery and recall

Recorded: `2026-09-14T14:13:46Z`
Work items: `DREAM-073, DREAM-074, DREAM-022, DREAM-070`
Outcome: `implemented`
Actor: Codex lead and authorized hosted-model contributors; fresh independent gates.

## Request

Continue the five ranked improvements without GPUs. Hosted models may be used
when needed to verify behavior. The owner's continuation authorizes the bounded
existing-code work described in the preceding plan; routine implementation choices
do not need another approval. No publication, local model load or owner app restart.

## Changes

Baseline captured before edits: 765 source hashes, 488 existing dirty status entries.
Lead owns shared tracking/docs, skill selection and model/task comparison work.
Deliverable contributor owns harness_eval.py and dedicated grader/Engine outcome
tests; native contributor owns the new native Engine qualification script/test;
memory contributor initially investigates recall and captures the before fixture,
then proposes a bounded repair before taking memory source ownership. Contributors
must not modify shared tracking or one another's files.

Acceptance is recorded in CURRENT and uses existing master IDs. The Alpha Omega
workflow gives each finished phase a fresh execution gate; failed gates retain
their findings and review the repair themselves. No benchmark is planned.

## Validation

Previous final suite and package evidence remain in the preceding handoff. New
checks do not inherit that PASS. Known restricted-process
AnyIO stalls require isolated approved loopback/thread test execution when reproduced.
All evidence and disposable state belong under ignored
`artifacts/harness-validation/cpu-next-pass`.

Interim evidence: deliverable author 29 CPU checks pass; independent gate pending.
Recall author 57 pass/2 optional-model skips; independent gate pending. Isolated
keyword-only BEFORE and AFTER both retain R@1 .80/R@3 .90/R@5 .95/MRR .86 across
20 cases. The new API requires caller-provided query alternatives and does not
claim primary-only semantic recall. Routing reproduced 16 failures/4 passing
controls before repair, then 96 focused tests passed. A restricted-process
integration run stalled after 93 tests; its exact owned process was terminated
and the isolated credential-free approved rerun passed with a 90-second timeout.
Native instrumentation has reached real Engine text and reconnect but has not
yet established interrupted durable state. No native PASS is claimed.

Code graph discovery initially returned results, then the service closed after
an approval timeout. Subsequent focused queries returned `Transport closed`;
contributors and lead used documented focused-read fallback.

## Unfinished work

None within this pass's bounded implementation and verification scope. All five
source phases passed independent execution gates, final combined CPU regression
and offline package checks. No owner runtime, local model, embedding or GPU workload
was started. Actual model quality/optimal settings, ordinary-chat/provider crash
recovery, document/recovery Engine task expansion and owner acceptance remain open.
No publication was performed. Runtime changes load at the next normal Dream restart.

## Gate results and preserved failures

| Phase | Independent verdicts | Evidence and limits |
|---|---|---|
| Deliverable contract | FAIL (1), PASS repair, PASS expanded probe | Initial grader checked path presence without binding saved bytes. Repair binds hash/size. Lead then found empty write-allowlist bypass; expanded same-judge gate rejects it. Final 33 tests plus unchanged independent probes passed. Read-only contracts and valid alternate filenames remain supported. |
| Native recovery | FAIL (4), FAIL (1), PASS | Missing durable-state/worker evidence, optional reports and boolean/integer confusion were found. The second candidate's self-written transcript marker was circular and rejected. Final real guided-workflow create/start/Stop/inspect/revise/start passed native execution in 3.49 s, five tests and 54 malformed-report probes. Ordinary chat/provider recovery remains unqualified. |
| Recall | FAIL (2), PASS | Malformed query/limit handling and open schema repaired; new store API validates its bounds independently. Same judge passed 76 relevant tests, four skips and two existing warnings, original probes and eight extra boundary/compatibility checks. |
| Skill routing | FAIL (2), PASS | Quoted single-word data and ambient Python keyword precedence repaired. Same judge passed 104 focused tests and all 14 unchanged adversarial probes. Name-collision regression found during repair was also fixed. Filename and lexical-routing limitations remain explicit. |
| Task comparison | PASS | 26 focused tests and 12 independent assertion groups; strict bounded supplied-evidence input, matching scope/coverage, failed/unknown outcome controls, CLI no-write/privacy checks. No live model timing or quality claim. |

Native fixture setup failures before the gates included child-report timing and
incorrectly calling recovery after an already-confirmed interruption. The workflow
service correctly requires explicit revise/start in that state. No production
runtime repair was needed. All failed probes and gate reports remain in the private
artifact folder; no evaluator was replaced to obtain a pass.

The first combined suite was stopped after 116.94 seconds because the lead's
isolated environment omitted PLAYWRIGHT_BROWSERS_PATH. A first-failure probe
reproduced missing cached Chromium (three passed, one error, one deselected).
All 600 checked source hashes remained unchanged; no partial full-suite PASS is
claimed. The corrected run points to the existing browser installation, keeps
private runtime/cache state and disables optional models. Initial logs are retained
under `final-suite/attempt1-missing-browser`.

Package refresh passed independently. Wheel SHA-256:
`6f308f16dd4d9615edd276d2202cd3da44d65fdd4e66c538c9c431f2f107b06a`.
Distribution audit (`package/audit.log`) accepted 273 archive members (3,686,285 bytes), with private
runtime paths excluded. All 269 selected Dream/skill source files stayed unchanged
and matched both wheel and installed-target bytes. A new private target installation
used offline `--no-deps`; the previous qualified clean environment supplied read-only
dependencies. This is dependency reuse, not a fresh dependency installation.
Isolated installed comparator help, synthetic comparison and harness-eval list
commands passed; imports resolved from the installed target and ten curated skills
were discovered. Evidence: `package/verdict.json`. No publication occurred.

Final evidence audit found stale package-next-step wording and missing durable
recall/skill gate verdict files. The wording was corrected and each original judge
persisted its completed review without rerunning or changing the candidate:
`recall-gate/verdict.json` and `skill-gate/verdict.json`, with adjacent REPORT.md
files. Package archive counts are supported by the audit log cited above.

The second combined run completed with 5,273 passed, one failed, 45 skipped,
one deselected, seven warnings and 52 passing subtests in 479.96 seconds.
Its sole failure was the fixture's AF_UNIX pathname exceeding the OS limit under
the lead's long private temporary prefix. The exact test passed in 0.19 seconds
with a shorter private path. All 600 source hashes remained unchanged. The runner
now uses a short private prefix and is executing a final full pass; no production
source was changed for this setup issue. Evidence and the unchanged focused probe
are retained under `final-suite/attempt2-unix-socket-path`.

Final combined run passed **5,274 tests, 45 skips, one deselected, seven warnings
and 52 passing subtests** in 470.28 seconds, exit zero. JUnit records 5,371 tests
including subtests and skips, with zero failures/errors. Skips are 34 model opt-ins
and 11 system GTK/GI prerequisites; native guided recovery was separately executed
with the system bindings. The sole deselection is
`tests/test_memory_files.py::test_the_live_three_memories_migrate_with_no_loss`,
which accesses owner memory. That case was not run.

Command: `python3 artifacts/harness-validation/cpu-next-pass/final-suite/run.py`.
The private runner used a credential-free environment, short private HOME/Dream/XDG
and temporary paths, software GL, offline model flags and the existing cached
browser binary. It ran the full pytest suite with the one explicit exclusion and
an outer timeout, then cleaned its owned process group. All 600 inventoried source
hashes stayed identical; aggregate SHA-256:
`6efa893c5605aa48d54402ce98934cae983db00ae08bcf6e40e9841ffe314af1`.
Evidence: `final-suite/{pytest.log,junit.xml,result.json,before.json,after.json}`.

No owned native fixture remains running; the full-suite child has exited.
Existing dirty files were preserved. The initial snapshot is hash-only and is
not a content backup. Recovery should start from CURRENT and this handoff, preserve
the failed evidence, inspect the current source before editing, and use a new
snapshot for a later pass. Never replay an uncertain owner action or restart an
owner model as part of reading these records.

## Next steps

Later representative model/task trials can use the new comparison interface, with
actual artifact grades and whole-task duration. Expand ordinary-chat/provider
recovery and document/recovery Engine qualification under the existing work IDs.
No optimal settings have been learned or automatically applied.

Final checks: `git diff --check` passed. Snapshot tracking passed with 85 dated
records and all 17 changed source/doc/test paths covered. A fresh final evidence
audit reconciled suite, gate, package and public-API claims with no remaining
findings. The 600 source hashes were checked again after the suite and still match.

A separate optional read-only host process-group existence probe did not execute:
automatic permission approval timed out. No unsafe-action verdict was returned.
Cleanup evidence remains the runner's completed child wait/process-group cleanup
and the native reviewer's independent no-process observation; no owner action is
blocked or waiting for permission.

## Files changed

- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-14-cpu-outcomes-recovery-recall.md`
- `dream/harness_eval.py`
- `tests/test_engine_task_outcomes.py`
- `dream/memory/store.py`
- `dream/tools/memory_tools.py`
- `tests/test_memory_recall_alternatives.py`
- `dream/skills/selection.py`
- `tests/test_skill_request_boundaries.py`
- `scripts/native_engine_recovery_qualification.py`
- `tests/test_native_engine_recovery_qualification.py`
- `dream/calibration.py`
- `tests/test_calibration_comparison.py`
- `docs/harness-evaluation.md`
- `docs/public/projects-and-skills.md`
- `docs/project/DECISIONS.md`
