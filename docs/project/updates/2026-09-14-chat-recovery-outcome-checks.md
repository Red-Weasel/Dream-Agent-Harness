# DREAM-065 / DREAM-073 — Chat recovery and outcome checks

Recorded: `2026-09-14T15:30:00Z`
Work items: `DREAM-065, DREAM-073`
Outcome: `implemented`
Actor: Codex lead; Sol Engine-fixture contributor; Terra chat-recovery auditor.

## Request

Continue authorized CPU-only improvements. Phase 1 extends actual Engine document
and ambiguous-edit recovery checks and repairs reproduced outcome-checker gaps.
Valid outcomes must pass; false completion, wrong artifacts, absent inspection and
forbidden mutations must fail. Phase 2 audits ordinary-chat interruption and
provider failure through production persistence, then repairs demonstrated defects.
Each phase requires a fresh independent execution gate. No GPU/local model loads,
owner windows/restart, private-memory export or publication.

## Changes

Baseline /tmp/dream-cpu-chat-baseline-0914.json captured 773 source hashes before
edits; preserve the existing dirty tree. Lead owns grader, evidence-strictness
tests and shared documentation. Sol owns tests/test_engine_task_outcomes.py.
Terra initially owns only ignored chat audit probes and has no source ownership.
Evidence is under artifacts/harness-validation/cpu-chat-pass/.

Document grading now checks required fact types; Python equality previously let
12500.0 satisfy an integer budget. Recovery inspection must show both exact original
settings between refusal and retry; empty, metadata-only, incomplete and numeric
near-match text no longer establish inspection. Existing correct native records
remain accepted. These checks grade supplied evidence; provenance is not authenticated.

## Validation

Graph-first search returned Transport closed; focused source reads were used.
Initial restricted pytest stalled before output and was interrupted (exit 130).
The isolated credential-free approved CPU runner reproduced four failed controls
and two passing controls. Initial repair passed 46 focused checks. An added numeric
near-match control failed (one failed/six passed), then exact-line repair passed 47
focused checks in 0.88s. No local models were loaded. Gates and combined suite pending.

## Unfinished work

None within the bounded implementation/CPU verification scope. Both fresh execution
gates and final combined suite passed. Live provider/native-desktop cancellation,
representative model quality, owner acceptance and publication remain unperformed.
A storage failure that prevents every corrective write cannot guarantee a durable
downgrade; hard crashes can lose buffered tokens. UNKNOWN is not a liveness check.
No implementation worker or final-suite process remains active. Preserve existing
work and take a new baseline before another changing session.

## Next steps

Next DREAM-065/073/015 evidence: actual provider interruption/continuation and
representative task outcomes before automatic setting recommendations. No GPU
workload or model trial is implied by this handoff; runtime changes load at the next
normal Dream restart. Owner acceptance and release remain separate.

## Files changed

- `dream/harness_eval.py`
- `tests/test_harness_evidence_strictness.py`
- `tests/test_engine_task_outcomes.py`
- `docs/harness-evaluation.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-14-chat-recovery-outcome-checks.md`

- `dream/core/engine.py`
- `dream/core/chat_recovery.py`
- `tests/test_chat_recovery.py`
- `dream/projects/library.py`
- `dream/gui/static/library.js`
- `tests/test_project_chat_recovery.py`
- `tests/test_chat_crash_recovery.py`
- `docs/public/projects-and-skills.md`
- `docs/project/DECISIONS.md`

## Phase 2 implementation checkpoint

Actual Engine audit observed error/partial text missing from the persisted transcript,
and cancellation after assistant text indistinguishable from clean completion.
Terra took explicit ownership of engine.py, chat_recovery.py and its dedicated tests.
Lead integrated status into Project transcript responses, restored context, handoff
drafts and a visible fixed-text notice in Conversations. Status does not authorize
replay or claim task correctness; legacy/unmatched/stale records stay unknown.

Ten Project consumer tests failed before implementation with missing recovery data.
The first integration run passed 9 and failed 1 because a test overconstrained wording
rather than meaning: existing “Task success is unknown” already distinguished protocol
completion from verification. The assertion now accepts that equivalent wording.
The combined Project consumer/handoff/library check passed 25 in 2.95s, including
private software Chromium visibility and no-generation-on-open controls.
The intermediate tracking check reported seven newly edited paths absent from the
active handoff; this entry adds them rather than claiming that check passed.


## Phase 1 independent gate

Fresh Sol outcome_gate returned PASS with zero findings. Its independently executed
focused suite passed 80 in 5.39s, and nine additional adversarial controls passed.
The restricted run timed out at100s in a worker thread; the credential-scrubbed
approved rerun passed. Evidence: cpu-chat-pass/outcome-gate/verdict.json and its
adjacent probe. This establishes fixed Engine/tool/grader behavior, not model quality.

The partial-reply Project recovery regression failed before integration: saved
assistant_partial rows were absent from restored context and handoff. The repair
shares the existing bounded assistant allowance and clearly labels partial replies;
it does not displace the separate user-context budget. Combined Project checks then
passed 26 in 2.85s, including software-browser display/no-dispatch checks.

Phase 2 source is still being hardened before its independent gate. Lead review
identified ownership-claim timing, cleanup failure downgrades, explicit success
subtype, and repeated cancellation during durable writes. No phase 2PASS claimed.

A new actual-child Engine crash check passed 1 in 0.46s. The finite backend writes
one synthetic artifact and yields assistant_done, then calls os._exit(23), bypassing
all finally cleanup. Reopened Project state reports UNKNOWN from its unmatched
started record, retains the partial assistant report, and preserves the artifact
contents and mtime through transcript/restored-context/handoff reads. This is an
actual isolated process exit, not native UI or real-provider crash qualification.

Phase 2 author finished69 focused checks in 1.94s. Repaired author regression failures
included absent status, length/cancellation timing, missing explicit success subtype,
cleanup failure, partial persistence, malformed parser data and an error after the
SQLite commit but before JSONL completion. Start ownership is claimed before awaits;
joined writes retain ownership until completion, and terminal corrections avoid a
stale success row after cancellation or mirror-log failure. Records with correction
tails conservatively read UNKNOWN. The independent phase 2 gate is now running.
Initial gate spawn hit the thread limit while the author was still finishing; after
the author completed, a new fresh Sol gate was spawned successfully. No reviewer
was reused or replaced after a verdict.

The frozen candidate wheel passed offline layout/privacy, exact source payload and
private installed-target checks while phase 2 review continued. It contains 274 archive
members (3, 689, 687bytes) with 270 selected source files matching source, wheel and
installed bytes. SHA256 d861f1f2bb85795d62e3b25c90f793f106855203a37f2abd62c7ce486db316cf.
Existing qualified dependencies were reused read-only; this is not a fresh dependency
install or release. Evidence: cpu-chat-pass/package/verdict.json and audit.log.
If a later gate changes source, this candidate must be refreshed before final claims.

## Phase 2 independent gate

Fresh Sol chat_gate returned PASS with zero source findings: 76 checks in 4.13s and
six adversarial/iterator checks in 0.43s. Restricted attempts timed out at100s or
could not bind loopback; approved private CPU reruns passed. Repeated cancellation
finished the owned writer, left a conservative non-success tail and released the
in-process claim. The temporary-anext test also passed with unraisable warnings
promoted to errors. Report: cpu-chat-pass/chat-gate/verdict.json. A report wording
error (“bytes” instead of “characters”) was corrected without a source change.
Final report SHA-256 0ad2c6bb53e7ae48594ff359d769b0f094f5baf6d56b3c707be81ef0eaf94d40.

At initial gate reconciliation, twelve reviewed source/probe SHA-256 values matched.
The later added query probe changed its evidence-file hash; the first consolidated
manifest was not refreshed immediately (see final evidence audit below). The final full CPU suite is
running with private state, disabled models, existing software Chromium, short
Unix-socket temp paths and the single owner-memory-dependent exclusion. Its results
are not yet known. A supplementary same-judge phase 1 probe is checking valid native
query-excerpt formatting; no new source failure is asserted before that evidence.

The supplementary valid-alternative probe passed: native read_file(query="30")
returned its real source-offset excerpt with both original settings, and the
unchanged recovery grader accepted it. Phase 1 now has10 independent adversarial
controls. Updated outcome-gate report SHA-256:
26de3f4749e5edec31fb1f943f806ed3bed10a20257d89f60bc1c2bab6faa8d4.
The exact graph project ID was correct; the reviewer's connection briefly worked
before later calls also returned Transport closed. Lead calls remained unavailable.
No source change followed this supplementary probe, so the running combined suite
and candidate wheel still refer to the reviewed implementation.

## Independent package/evidence audit and correction

The fresh package_evidence_audit independently passed all 270 source/wheel/installed
payload comparisons, 274 archive members, new chat module inclusion and private-path/
scanned-literal exclusion. It returned FAIL on one evidence bookkeeping issue:
the consolidated reviewed-source-hashes.json still referenced the probe before the
supplementary query-format check, although the gate report held the correct new hash.
The handoff's present-tense hash claim was stale too. The old manifest was preserved,
the consolidated manifest refreshed from both verified gate reports, and the earlier
sentence corrected with this chronological explanation. All 12 current source/probe
hashes now match. This did not change the tested source or wheel. Same-auditor recheck
is pending. The suite was still incomplete at audit; no full-suite PASS was claimed.

## Final combined verification — 2026-09-14T15:59:55Z

The final isolated CPU run completed with exit 0: 5, 325 passed, 45 skipped, 1 deselected,
7 warnings and 52 passing subtests in 473.61s (runner474.56s including bookkeeping).
The excluded test is exactly tests/test_memory_files.py::test_the_live_three_memories_migrate_with_no_loss;
it depends on owner memory and was not run. Optional model/native dependency skips
remain visible in pytest.log. All 605 inventoried source hashes stayed unchanged.
Evidence: cpu-chat-pass/final-suite/result.json,junit.xml,pytest.log,before.json,after.json.
No prior suite result was substituted for this run.

The same final evidence auditor passed the repair in package-evidence-audit-recheck.json
(SHA256 a435244c25aa5c4db8ff20278512316d4f3b8c2763cad17aff79657b3352bd8d).
Its original FAIL report remains unchanged. The recheck happened before suite
completion and correctly labels that earlier suite state as running. The completed
runner result above supersedes that temporal limitation. PID visibility in another
sandbox namespace could not establish process death; the root runner completed
normally and returned its final result.

Final source/package evidence remains for the exact reviewed implementation. No
owner app was restarted or private state exported, and no GPU/local model loaded.
Final tracking passed: 86 dated records and 16 changed source/doc/test paths checked
against the original snapshot. git diff --check passed. All 605 tested source hashes
and 270 package source hashes still match current files. Combined source aggregate:
6fe1593adbdb36842082ed55e22347f0c941b8aa336b32983fb4c89748794679.

Final independent evidence audit passed after checking the completed JUnit result,
all 605 current source hashes, all 12 review hashes, and all 270 packaged/installed
source files. Report: cpu-chat-pass/final-evidence-audit.json (SHA-256
c50d74bf89b2122a68372b94c5c1a523a234272072bbb551c0357b325eacc37b).
A final prose-spacing edit accidentally inserted spaces into two ISO timestamps;
the tracking check caught the malformed dates. The timestamps were restored before
the final tracking rerun. This affected documentation only, not tested source.

Final tracking rerun passed (86 dated records, 16 changed paths); whitespace check passed.
