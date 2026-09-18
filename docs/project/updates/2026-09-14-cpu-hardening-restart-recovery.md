# DREAM-076 — CPU hardening and restart recovery

Recorded: `2026-09-14T05:13:29Z`
Work items: `DREAM-076, DREAM-073`
Outcome: `implemented`
Actor: Codex lead with authorized Astra contributors and fresh read-only evaluators.

## Request

Work continuously through six harness priorities for two hours without GPUs or
local model loads. Owner paused at approximately 03:39 UTC to reboot and resumed at
03:47 UTC. The expected resumed window was approximately 78 minutes, through
05:05 UTC; necessary qualification of the late Stop repair finished at 05:13. No
publication, private memory export, automatic owner app restart or live inference.

## Changes

Before reboot: role-aware bounded project recovery; content-free observed tool
outcomes; native Prompt Optimizer navigation; explicit failed delivery status;
verifier execution/image-submission prerequisites; preserved image metadata during
repeat advice; incomplete media status handling; stricter distribution audit and
nested private-directory build exclusions. Existing dirty work was preserved.

On resume, confirmed source changes remain and previous temporary evidence is
missing. Restored active tracking and took a new 763-file hash snapshot. This does
not reconstruct the lost original 755-hash baseline. The 20 pre-reboot changed paths
below are known from the last observed snapshot comparison, not a reconstructed
filesystem diff. New evidence uses ignored artifacts/harness-validation/
cpu-hardening-resume. Graph service recovered and fast indexing completed.

Resumed repairs replace directory force-inclusion with ordinary wheel selection
and source mapping, reject archive file/ancestor collisions, retain Gemini tool
IDs and reject malformed terminal success. Three stale test fixtures now match
the current asset, package and supported-effort contracts. The repeatable CPU
qualification runner includes the new contracts, isolates child settings and
validates JUnit structure/counters without confusing passing unittest subtests
with separately emitted cases. Public behavior docs and ADR-053–055 are updated.

A further independent memory audit reproduced the missing directory-sync boundary
and showed why simply inserting fsync would produce misleading partial failures.
The scoped repair now synchronizes directory ancestry and reports replacement
status separately from uncertain durability, keeping committed derived indexes
aligned. A fresh failure-injection gate passed. ADR-056 and the memory guide
describe the I/O, permission and filesystem limits. The existing manual external
review packet now points to current source contracts while preserving old history.

The final recovery review reproduced a separate Stop ownership error in actual App
methods. Studio Stop during a reconnected terminal approval dismissed the reader
but left the outer turn active. The lead added an outermost-turn cancellation
target for Studio while preserving the inner SIGINT target, plus three permanent
regression cases. This late repair extends verification beyond the approximate
05:05 target; it does not start another implementation phase. ADR-057 and the
runtime guide describe the distinction.

## Validation

Pre-reboot observations retained in conversation (original `/tmp` reports lost):
context gate30tests+12probes PASS; tool evidence gate29tests+9probes PASS; delivery
gate58tests/1browserdeselected+10probes PASS after image-metadata repair; media
gate83tests+20probes PASS; native gate5tests+13actualsoftwarechecks PASS and forced
exit7 correctly rejected after fixture environment/shutdown repair. Root's broader
completion run185passed before the final metadata repair; do not treat it as the
final candidate result. These groups overlap and must not be summed.

Package audit author8testmethods passed. Root19syntheticcanaries reproduced4nested
leaks before build exclusions and zero afterward. Candidate272members passed
layout audit;98frozen/hash-verified dependencies installed and compatibility check
passed. Installed import/CLI/source comparisons were unfinished. A fresh package
evaluator was probing force-included skill directories when interrupted and had
issued no verdict. At interruption, that possible exclusion bypass remained a
required check; the resumed failing and repaired gates below resolve it.

Resumed evidence under ignored `artifacts/harness-validation/cpu-hardening-resume`:

- Package gate round 1 FAILED: 12 of 28 skill canaries leaked and two archive
  ancestor collisions were accepted. Round 2 PASSED: all 28 excluded, 11 skills
  and memory implementation retained; 9 audit methods and 17 independent cases
  passed, all 268 packaged source files matched source bytes. Evidence: `package-gate`.
- Clean offline install initially FAILED for a missing cached cffi artifact.
  Reused-dependency compatibility was recorded separately. Authorized locked public
  dependency downloads then produced a fresh environment: 98 hash-locked runtime
  dependencies, all 99 installed versions including Dream matched the lock, five
  CLI checks/basic imports and dependency compatibility passed. The final source
  wheel refresh is recorded below. Evidence: `package-install`.
- Full CPU run recorded 5,111 passed, 25 failed, 46 skipped, 7 warnings and 52
  passing subtests. Seven failures passed with corrected short temporary paths.
  The remaining 18 were stale fixtures; the three-file repair plus related coverage
  passed 176 checks. The final full-suite result is recorded below. Evidence:
  `integrated-tests`.
- Gemini author regressions: 11 RED before repair; 126 GREEN after. Fresh gate:
  126 regressions and 41 private actual-backend/App consumer probes passed.
  Restricted subprocess attempts were interrupted; approved CPU process-access
  reruns passed. Evidence: `native-outcomes`, `gemini-gate`.
- Native post-reboot recheck: 5 system-Python tests and all 13 actual software
  GTK/WebKit/VTE stages passed in 11.42 seconds, child exit zero. Private Xvfb was
  downloaded/extracted without system installation and reaped afterward. D-Bus and
  WebVTT warnings remain; subtitles and real Engine behavior were not qualified.
  Evidence: `native-recheck`.
- Qualification runner round 1 FAILED malformed-JUnit acceptance. Round 2 FAILED
  valid pytest subtest-count handling. Round 3 PASSED all six actual groups:
  492 visible testcase elements, 541 declared tests including passing subtests,
  no skips/failures/errors, stable listed sources. Original 12 and additional 8
  report probes, actual child isolation, source changes/missing files, timeout and
  exit-7 rejection passed. Evidence: `qualification-gate`. These counts overlap
  other suites and must not be added together.
- Memory author regression: 8 RED, 2 GREEN before repair; final 68 passed and one
  owner-memory-dependent legacy migration test explicitly deselected. Fresh gate:
  41 passed including 15 independent fault-injection cases, same deselection.
  Current-file/index alignment, stale version retries without duplicate append,
  combined fsync/commit failure, derived-index warnings, pre-replacement rollback,
  directory retry ordering and cleanup passed. Stalled sandbox fixture processes
  were terminated and reaped before approved reruns. Evidence: `memory-durability-audit`
  and `memory-durability-gate`. No actual power-loss trial was performed.
- Final wheel refresh PASSED: SHA256
  `70b28f6632099c64dbacfe64d73a26a0c0e6579fbcf02c277ad9a5921e3ee9b2`;
  all 268 packaged source files match source and installation, unchanged after
  checks. Clean locked installation has no reused-dependency path, all five CLI
  checks/basic imports pass, 99 installed versions match the lock and compatibility
  passes. Eleven skill documents are bundled and ten curated skills discoverable.
  No final download or publication. Evidence: `package-install/final`.
- Fresh Prompt Optimizer audit PASSED: 61 service/route/lifecycle regressions,
  10 actual CPU Chromium UI checks and five independently written service/browser
  boundary probes. Selected-source attribution, preserved original input, stale
  target responses and composer append/no-autosend passed. Initial sandbox bind
  errors and approval timeout are preserved separately from the successful retry.
  No material source defect was reproduced. Evidence: `optimizer-recovery-audit`.
- Pre-Stop full CPU suite PASSED: **5,176 passed, 45 skipped, one explicitly
  deselected, seven warnings and 52 passing subtests**, in 478.86 seconds, exit
  zero. The deselected legacy migration case accesses owner memory directories;
  skips comprise 34 opt-in model and 11 GTK/GI prerequisite cases. All 573 checked
  runtime/test/skill/script/config files remained unchanged; aggregate SHA256
  `7642240597f8089685a0355ebeb10367c625e0a17c09c322a17e74ddc6d0d4eb`.
  A fresh short temporary root isolated HOME/DREAM/XDG and credentials, disabled
  optional processing and hid accelerators. Actual commands, environment, JUnit,
  warnings and per-file hashes: `integrated-tests/final`.
- Final qualification manifest PASSED all six actual groups: 506 visible cases,
  555 reported tests including passing subtests, zero skips/failures/errors,
  unchanged listed sources. The final manifest adds the memory durability test
  and its two implementation sources; parser/environment logic matches accepted
  round 3. Evidence: `qualification-gate/final-manifest-review.json` and
  `real-six-gates-final-manifest.json`.
- Fresh read-only documentation/evidence audit PASSED: README, development,
  qualification and CURRENT match their cited reports and preserve scope limits.
  No source, tests or models were changed by that reviewer. This is not a source
  content or Git-history privacy audit. Evidence: `final-evidence-review/REPORT.md`.
- Current-candidate keyword-only memory fixture COMPLETED, exit zero: 26 synthetic
  memories, 20 queries, zero skipped, recall@1 0.80, @3 0.90, @5 0.95 and MRR 0.86.
  One food-restrictions query missed. The author omitted the required pre-repair
  fixture evaluation and retained no exact prior source copy; no before/after
  comparison or recall improvement is claimed. The child used copied public
  fixtures, private HOME/DREAM/XDG/TMP, scrubbed credentials, hidden accelerators
  and disabled semantic/rerank processing. Evidence:
  `memory-durability-audit/implementation/keyword-eval`.
- Read-only recall follow-up isolated the miss: the expected row is present but
  absent from the whole keyword candidate set. Incidental query words match three
  unrelated rows; limits 5, 8 and 26 return the same three. The saved dietary wording
  has no query-token overlap. This is a lexical coverage finding under DREAM-073/022,
  not a top-five ranking cutoff or demonstrated regression. Raw probe and finding:
  `memory-durability-audit/implementation/keyword-eval/food-restrictions-finding.md`.
- Actual Engine data probe COMPLETED four controlled cases in 1.55 seconds. All
  scripted transports report protocol success; independent artifact grading rejects
  absent/wrong output. The existing data grader accepts correct totals plus an
  unrelated overwrite, while a separate before/after file-scope check rejects it.
  Only the correct control meets the complete external contract. A fresh judge
  verified actual Engine/tool traversal and explicit preservation instructions in
  captured requests. No model did arithmetic, no automatic task grading was added,
  and native startup was bypassed. Original four sandbox thread timeouts are retained;
  approved isolated rerun passed with five hashes unchanged and fixture reaped.
  Evidence: `engine-outcome-probe`, `engine-probe-judge`.
- Final recovery boundary review FAILED its Stop contract: actual App `_run_turn`,
  `_read_answer` and `_runtime_control` returned `interrupted: true` while the
  outer turn continued after a reconnected approval. Initial sandbox probe stalled,
  then was identified and reaped before an approved 30-second-bounded retry.
  The author's permanent controls recorded two failures and one SIGINT-control
  pass before repair. Same fresh judge after repair PASSED independent Stop/SIGINT
  App probes and 26 focused interrupt/cleanup checks in 8.35 seconds, no skips or
  warnings. Reader cleanup, idle Stop, outermost nested turn and subsequent-turn
  behavior passed. The earlier missing-subtype discrepancy was inspected and
  resolved as explicitly tested compatibility behavior; it was not changed.
  Evidence: `stop-repair/red.log`, `recovery-boundary-review/post_fix_review.md`.
- Post-Stop wheel/install refresh PASSED: wheel SHA256
  `86a87fad44238d051f98c6e159d4e4d0b8c34c6b231129d89384183879cc0087`.
  All 268 packaged source/installed files match; only App changed from the prior
  wheel. All 271 recorded build-input hashes stayed unchanged. The previously
  qualified private clean dependency environment was retained and Dream alone
  reinstalled offline, with no downloads. Five CLI checks/imports, 11 bundled
  skill documents/10 discoverable skills, all 99 installed versions and dependency
  compatibility passed. One reporting assertion initially confused skill-document
  count with discoverable-directory count; it was corrected and all checks rerun,
  preserving the failed reporting attempt. Evidence: `package-install/post-stop-final`.
- Post-Stop six-group manifest PASSED again, 506 visible cases/555 reported tests,
  no skips/failures/errors and unchanged listed source hashes. This selected
  fingerprint does not include App or its interrupt tests; the dedicated Stop
  gate and full-suite inventory cover those. Command:
  `.venv/bin/python scripts/qualify_production.py --output artifacts/harness-validation/cpu-hardening-resume/qualification-gate/post-stop-manifest.json`.
- **Final post-Stop full CPU suite PASSED: 5,179 passed, 45 skipped, one explicitly
  deselected, seven warnings and 52 passing subtests**, in 480.15 seconds, exit
  zero (wrapper 481.51 seconds). Same explicit owner-memory exclusion, 34 model
  opt-in and 11 GTK/GI skips. All 573 inventoried source hashes stayed identical;
  aggregate `c88ed683205be57c96bf3615d683a88396f08497dce9a45ead6b829711f72f62`.
  Only App and its interrupt tests differ from the prior full-suite candidate.
  Reused the verified command/environment shape with a new short private root,
  credential-free child, hidden accelerators and bounded process execution.
  Owned pytest process group was empty after cleanup. Evidence:
  `integrated-tests/post-stop-final/REPORT.md`, command receipt, log, JUnit and
  before/after inventories alongside it.
- Final whitespace checks passed on affected tracked paths. Tracking check:
  `python3 scripts/check_project_tracking.py check --snapshot /tmp/dream-resumed-baseline.json`
  passed, 84 dated records and 27 resumed-session changed source paths. Pre-reboot
  coverage remains the separately labelled observed inventory, not a recreated diff.

No GPU or model was loaded. The first root fixture rebuild used an incorrect relative
copy path and retained old configuration; only the separately corrected rebuild
and independent round-2 artifact count as repaired package evidence. Graph discovery
intermittently hung; cancelled calls and focused source fallback were explicit.
The final index refresh's automatic approval review timed out; its one retry
succeeded, indexing 9,986 nodes and 61,629 edges without writing a graph artifact.
The index excludes some tools/media/script paths and is not exhaustive discovery.
After the Stop repair, a second successful refresh recorded 7,655 nodes/52,283
edges; an exact `_run_turn` query returned its repaired source. No graph artifact
was added to the public tree.

## Unfinished work

None within this pass's implementation and current-source qualification scope.
The late Stop repair, fresh review, final full suite, wheel/install refresh,
six-group manifest and tracking checks are complete. All owned contributors and
fixture processes finished; no old reader, renderer or uncertain task was resumed.
The memory author did not run the required pre-change keyword fixture and retained
no exact pre-repair source copy; a before/after comparison cannot be claimed.
No old temporary fixture should be assumed alive or recoverable after reboot.
No production-grade, universal compatibility, owner acceptance or model uplift
claim. Broad goal remains unfinished; this pass does not mark it achieved.

## Next steps

The next implementation work belongs to the master register: DREAM-073/066/015
promotion of the probed actual-Engine data contract with explicit file-preservation
checks, then document/recovery task grading and DREAM-074/015/065 native App/Engine
recovery. Preserve the original failure probes and final candidate evidence.
Model/task calibration and held-out skill routing follow that evidence. Model
trials remain deferred while GPUs are occupied; do not infer optimum settings
from the CPU regression pass.
Changed Python code loads on the owner's next deliberate Dream restart. No restart
or release is scheduled by this handoff. For a further reboot, use the durable
ignored baseline/report copies; `/tmp` evidence may disappear again.
Keep new evidence ignored/private and do not publish memory or local artifacts.

## Files changed

- `README.md`
- `docs/project/DECISIONS.md`
- `docs/public/production-qualification.md`
- `docs/public/progress-and-delivery.md`
- `docs/public/projects-and-skills.md`
- `docs/public/development.md`
- `docs/public/runtime.md`
- `docs/harness-review-packet.md`
- `dream/core/backends/gemini_adapter.py`
- `dream/memory/longterm.py`
- `dream/memory/store.py`
- `dream/tui/app.py`
- `tests/test_interrupt_paths.py`
- `tests/test_memory_directory_durability.py`
- `scripts/qualify_production.py`
- `tests/test_cli_exit_reconciliation.py`
- `tests/test_curated_skills.py`
- `tests/test_diagnostics.py`
- `tests/test_gemini_adapter.py`
- `tests/test_local_tuning.py`
- `tests/test_production_qualification.py`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `dream/core/backends/openai_compat.py`
- `dream/core/engine.py`
- `dream/desktop/window.py`
- `dream/gui/static/turn-timing.js`
- `dream/media/providers.py`
- `dream/projects/library.py`
- `dream/telemetry/turn.py`
- `pyproject.toml`
- `scripts/audit_distribution.py`
- `scripts/native_workflow_qualification.py`
- `tests/test_context_selection_quality.py`
- `tests/test_delivery_attempts.py`
- `tests/test_distribution_audit.py`
- `tests/test_media_completion_status.py`
- `tests/test_native_workflow_qualification.py`
- `tests/test_tool_outcome_evidence.py`
- `tests/test_turn_timing_ui.py`
- `tests/test_verifier_execution_evidence.py`
- `docs/project/updates/2026-09-14-cpu-hardening-restart-recovery.md`
