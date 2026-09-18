# DREAM-035 — Recovery and handoff audit

Recorded: `2026-09-11T01:38:23-05:00`
Work items: `DREAM-035`
Outcome: `blocked`
Actor: Codex lead; Astra CLI/journal authors and NEW independent judges; Council
original author, correction author and SAME independent three-attempt judge.

## Request

Continue iterative harness hardening with fresh Astra verification, preserving
main/advisor effort and deferring live models/GPU/benchmarks. Acceptance criteria
were declared before each phase in the shared adaptation plan. The original crash
and external Codex active-writer cause were not established as owner incidents.

## Changes

Integrated exactly the independently accepted phase18 CLI completion correction
and phase20 journal-write correction (five source/test paths). Phase18 reports
missing terminal results after owned cleanup rather than treating exit0 as success.
Phase20 stops failed writers, preserves original failure/run identity, separates
committed ledger from snapshot failure, and retains locks through owned cleanup.

Phase19 Council replacement was never integrated. Three formal SAME-judge FAILs
caught early acknowledgment, preparation input loss and then incorrect inference
of an owning committed row. The final rollback/competing-writer counterexample
still violates exact ownership. All three isolated candidates and reports remain
preserved. No main Council/memory source was overwritten or restored from HEAD.
Existing Council provider/model/effort controls remain, with known handoff limits
now disclosed in runtime-controls. The large pre-existing dirty tree is preserved.

The Alpha Omega skill at ~/.agents/skills/alpha-omega-loop/SKILL.md states:
“three FAILs at one gate = stop the loop and surface the findings + disagreement
to the founder.” Lead agrees with the remaining finding and has stopped Council
implementation. No fourth attempt started. This is a stopped review checkpoint,
not an automatic-approval rejection or a claim that the entire goal is complete.
The shared plan contains a concrete unimplemented commit-receipt redesign proposal.

## Validation

**Actual integrated main source:3,030 passed,35 skipped,7 warnings in234.73s,
exit0.** Log:/tmp/dream035-final-recovery-accepted.log. This covers accepted
phases1–18 and20, not any rejected phase19 source. Existing Python3.12 environment,
disposable Dream root, known-path telemetry guards and the existing temporary
Chromium software-rendering plugin were used under approved bounded escalation.

```sh
DREAM_ROOT="$(mktemp -d /tmp/dream035-final-recovery-accepted-XXXXXX)" PYTHONPATH=/tmp/dream035-astra-preview-diagnostic PYTHONDONTWRITEBYTECODE=1 timeout 360s .venv/bin/python -m pytest -q -ra --tb=short -p no:cacheprovider -p dream035_cpu_preview_diag > /tmp/dream035-final-recovery-accepted.log 2>&1
```

Skips:34 model-loading guards and one already-migrated fixture. Warnings:two
dependency deprecations, two asyncio marks on synchronous tests, three SDK
read-tool shadow notices. No failed main full run in this recovery cycle. Earlier
failed full runs remain in their own finalized handoffs. Session21136 ended0.
Known-path telemetry guards and --disable-gpu do not prove universal hardware I/O
isolation. Fake local services, owned fixture processes and temporary Git fixtures
are test mechanisms; no main Git or owner-process mutation occurred.

Independent gates:
- Phase18 NEW Astra PASS:99 focused tests/264 independent assertions across37
  fake spawns. /tmp/dream035-cli-completion-gate.md. Exact two hashes integrated.
- Phase20 NEW Astra PASS:91 focused tests/43 independent fault cases. Original
  duplicate sequence independently reproduced; successful storage bytes match.
  /tmp/dream035-journal-write-gate.md. Exact three hashes integrated.
- Phase19 first FAIL:209 selected pass, independent21 pass/7 fail (two lifecycle
  blockers). /tmp/dream035-council-context-gate.md.
- Phase19 second FAIL:228 selected and28 prior independent pass; new13 pass/1
  product fail (note/turn ID collision). /tmp/dream035-council-cleanup-gate.md.
- Phase19 third FAIL:234 selected and42 prior independent pass; new7 pass/1 fail
  (rollback plus competing identical commit falsely adopted as own source).
  /tmp/dream035-council-identity-gate.md. No weaker gate or replacement judge.

Guarded keyword-only original/candidate memory outputs were independently identical:
26 fixture memories,20 queries/0 skips, recall@5 .95/MRR .86. That evidence concerns
the isolated memory change, not an integrated feature or model quality. Author
TDD failures, managed threading timeouts, fixture mistakes, overlay warning and
correction attempts are preserved below and in their frozen reports. Root graph
calls repeatedly failed Transport closed; some agent lookups succeeded. Focused
reads followed recorded limitations. SQLite primary-source references supporting
and limiting the inference are recorded in the shared plan.

## Unfinished work

Phase19 is held at the three-failure checkpoint. The proposed next design returns
the owning INSERT ID only after acknowledged commit and notifies Engine before
JSONL append, removing commit inference. It is not implemented or gate-passed.
The final counterexample uses a rollback trigger and explicit competing interleave
only in disposable storage; no owner schema/runtime incident was observed. It
falsely attributes ownership but does not alter the raw text or dispatch a request.

Separate observed findings remain: CLI terminal result plus nonzero exit, lost
reconciliation notes and malformed persisted phases. Live/native/install/model
quality, benchmark and owner acceptance remain unqualified. No live model/provider,
GPU probe, install/build or owner-process work was performed. No test process or
owned fixture remains active. The broader persistent goal is not marked complete.

## Next steps

Review the stopped phase19 checkpoint and the explicit commit-receipt proposal in
docs/superpowers/plans/2026-09-10-harness-adaptation-loop.md. Preserve this handoff
and all failed evidence; any resumed implementation starts a new dated update and
source snapshot, uses the same Council judge, and retains the original controls.
No fourth source attempt is assigned. Proposed scope is not an active editing task.
The manual Claude/Grok review packet is updated; no external feedback was obtained.

This session's baseline remains /tmp/dream035-recovery-audit-baseline.json (567
hashes). Only the actual main changes are listed below; isolated candidate scope
is described in the history and reports. Do not use HEAD to replace dirty source,
replay uncertain operations or manipulate private owner sessions/locks.

## Files changed

- `dream/core/backends/cli_agent.py`
- `tests/test_cli_completion.py`
- `dream/core/run_state.py`
- `dream/core/loop.py`
- `tests/test_run_state_write_failure.py`
- `docs/runtime-controls.md`
- `docs/durable-runs.md`
- `docs/project/DECISIONS.md`
- `docs/harness-review-packet.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/superpowers/plans/2026-09-10-harness-adaptation-loop.md`
- `docs/project/updates/2026-09-11-harness-recovery-audit.md`

Root and independent durable auditor confirmed missing-result false completion.
Phase18 finite acceptance/two author paths are declared in the shared plan before
implementation. Durable reconciliation and Council context findings remain under
audit; they are separate scope, not folded into this correction.

Both fresh read-only audits finalized. Durable audit:
/tmp/dream035-durable-recovery-audit.md, with two independent scripts exiting0 and
zero telemetry attempts. It confirms CLI missing-result reachability through real
Loop.run; emitted error controls preserve uncertainty and prevent review. It also
reproduces lost reconciliation context at contract restart and after a pre-worker
callback failure, duplicate journal sequence numbers after injected ledger fsync
failure, and unsupported persisted phases dispatching workers. These are separate
unimplemented findings, not live incidents or authorization to repair owner data.

Council audit:/tmp/dream035-handoff-context-audit.md. Seven actual Engine configure/
ask and fake-HTTP admission scenarios,16 assertions, exit0 in0.64s. Full latest
user constraints can be removed by prefix slicing or crowded out by assistant
records; optional handoff text can repeatedly overflow a2048-token model; a long
first advisor can omit later dissent. Short/roomy-window controls preserve selected
model and effort. The successful probe replaces Engine in_thread with an inline
async wrapper after managed scheduling timeouts; it proves content/admission,
not thread lifecycle. Failed20/30/60s attempts,4s diagnostic and initial constructor-
guard setup failure are retained in the auditor's artifacts.

A new phase18 author spawn was rejected by runtime thread capacity, not automatic
approval review. The durable auditor finished/froze its report, then received an
explicit role change to AUTHOR in the isolated CLI workspace. A separate NEW
reviewer will gate that candidate. The Council auditor now writes a read-only
finite phase19 design in /tmp; it does not implement. No main source edited yet.
Root independently read source to confirm journal fsync-before-sequence ordering
and where current-invocation resume_note is omitted from contract negotiation.

Phase18 author progress: first red8 expected failures/10 controls passed. Minimal
post-EOF correction then yielded17 passed/1 failed; the remaining assertion expected
Engine timing error, but real Loop closes Engine on the emitted error and existing
GeneratorExit reports interrupted. Durable result is error, uncertainty true, no
worker_finished and no reviewer. Author is correcting that extra fixture expectation
to preserved Engine behavior, without changing product or original tests. Final
green/gate remain pending.

Root's additional CLI observation (terminal result followed by nonzero process exit)
remains open beyond phase18. Its current success event is already emitted before
exit; fixing contradictory terminal/exit evidence may require a separate result
delivery contract. Do not treat the missing-result patch as complete CLI lifecycle
qualification or silently discard this observed case.


Phase18 author frozen:99 focused tests passed9.18s under approved escalation,
18 new tests passed. Managed22 failures/77 passes were unchanged trusted-system
ownership refusals, not repaired by policy changes. Root verified516 original
files, exactly one product change plus one new test, and main originals. NEW
astra_cli_completion_gate independently verifies the frozen candidate.

Council design finalized and lead read it. Phase19's eight finite criteria and
seven disjoint source/test paths are declared BEFORE authoring in the shared plan.
Lead accepts whole-consultation omission with complete visible advisor manifest
when even metadata cannot fit; full latest user intent remains mandatory.
Private native admission remains unknown. WorkingMemory change is limited to
returning the existing persisted turn ID; required keyword-only fixture evaluation
is explicitly included as regression verification, with no model benchmark.
Shared baseline remains567 hashes for concurrent phases18/19; one lead handoff.

Phase18 NEW independent Astra gate PASS:99 fresh focused tests in8.00s,
264 independent assertions across37 in-memory fake spawns; forbidden/network/
telemetry attempts0. Report:/tmp/dream035-cli-completion-gate.md. Original parser+
Engine wrapper+actual Loop false done was replayed against the corrected candidate;
new error preserves uncertainty and blocks reviewer/worker_finished. Exact516-file
scope and both accepted hashes verified. Root read the report, checked both main
originals again, and integrated exactly cli_agent.py plus new test_cli_completion.py.
Operator durable-run docs now describe missing-result errors and preserve the open
terminal-result-plus-nonzero limitation. Consolidated full waits for separate
phase19 gate, not a substituted old pass.

Phase19 TDD before production edit:3 actual Engine/fake-HTTP counterexamples failed
in0.46s under approved escalation using ordinary threading. Managed red attempt
timed out exit124 and remains recorded. Initial keyword-only baseline failed
because isolation omitted static eval corpus/golden files; using their verified
read-only main fixture paths then passed20 queries/0 skips, recall@5 .95/MRR .86.
No embedding/model evaluation was run. Source inspection corrected a design
assumption: store.add_turn returns None. The scoped WorkingMemory return-ID change
must capture its actual inserted row under existing RLock without confusing a
newer row from another connection. Root flagged that race before implementation
and requested a second-connection control and trigger/UPDATE checks.


Phase20 finite six-item storage-failure contract declared before source edits.
A separate isolated author owns run_state.py, loop.py and one new fault-test file,
disjoint from phase19. This addresses the reproduced duplicate-sequence failure,
not malformed-state validation or reconciliation-note persistence. Lead will
consolidate only independently accepted candidates and preserve failed evidence.

Lead clarified two existing criteria before final candidate review: phase19
acknowledgment waits for clean exhaustion plus terminal success, so later cleanup
failure does not drop source; phase20 cancellation keeps CancelledError with an
explicit API exception note for unpersisted storage failure, without a universal
UI-rendering claim. A post-fsync close failure retains committed seq/state but
disables that writer. Both remain within their originally declared paths.

Author progress (not independent acceptance): phase20 observed28 failing new cases
and one passing control before its implementation;29 new cases now pass, with
existing durable regressions still underway. Phase19 is running broader context
regressions and preserving failed attempted user sources by stored ID through
subsequent continuations. Its earlier compaction wrapper compatibility failure is
being corrected without removing existing lifecycle assertions. Both gates remain
pending; no additional main source has been integrated.

Phase20 author reached90 focused new/existing passes in5.15s, then identified an
additional criterion3 case before freezing: the first ledger record may commit but
its snapshot fail before Loop records workspace ownership. The result must retain
the known journal identity without treating snapshot-only failure as a poisoned
writer. A separate red/green case is being added; the earlier count is not a final
gate. Root tracking passed38 dated records/9 changed paths.

Lead rechecked main hashes during authoring: engine.py, openai_compat.py,
working.py, loop.py and run_state.py still match the shared pre-edit baseline.
cli_agent.py matches accepted phase18 hash fb6a4cb3f7d2c788f4570027911a7b36d53eba00eeb4666c0edcb10fcfb20d61.
Phase19 author reports52 targeted passes and206 broader passes before its latest
boundary additions; final consolidated author check and new gate remain pending.
Omission notices use real system events reaching existing terminal/Studio handlers;
that source observation is not live visual qualification.

Phase20 author frozen with91 passed in5.49s, no warnings/skips. Report:
/tmp/dream035-journal-write-impl.md. Root read the production diff, verified all
three candidate hashes and original main hashes, and wrote independent lead diff/
hash artifacts. NEW astra_journal_write_gate is reviewing the frozen candidate.
No main journal source is integrated yet. All failed TDD and refinement results
remain in the author report. Snapshot-only first creation now retains known run
identity in the candidate; actual acceptance remains pending.

Phase19 author frozen:209 passed in2.76s, no reported warnings/skips; keyword-only
before/after output byte-identical (26 memories/20 queries,0 skips, recall@5 .95,
MRR .86). Report:/tmp/dream035-council-context-impl.md. Root checked all seven
candidate and main-original hashes, read the production diff, and wrote lead
diff/hash artifacts. NEW astra_council_context_gate is independently checking it,
including disclosed outer-Engine cleanup and optional-snip review boundaries.
All511 other baseline files unchanged per author; fresh integrity remains the
judge responsibility. Neither phase19 nor phase20 is integrated yet.

Phase20 NEW independent Astra gate PASS:/tmp/dream035-journal-write-gate.md.
Fresh91 focused tests in4.95s and43 independent cases in3.28s passed; baseline57
existing cases passed, while the independent duplicate probe failed as expected
with [1,2,2] and its normal control passed. No candidate gate failure or correction,
warning, skip, timeout or escalation. All515 unchanged originals/exact three
hashes/patch and successful storage-byte compatibility verified. Root rechecked
main originals and integrated exactly loop.py, run_state.py and the new fault test.
Operator durable-run docs and ADR-025 now reflect accepted behavior. Phase19
remains under its NEW judge; combined full has not run after these integrations.

Phase19 fresh judge observed a criterion6 blocker before its formal gate report:
five actual Engine/HTTP cleanup faults (timing emit, meter timing record, bridge
error, foreground-finalizer error and bridge cancellation) propagate after pending
handoff is already cleared. Its first independent run is5 failed/9 passed in0.99s;
clean completion control passes. Probe:/tmp/dream035-council-gate-probes/test_independent.py;
log:/tmp/dream035-council-gate-independent-first.log. No phase19 integration.
The same judge will review correction after completing and freezing its findings.

To overlap correction with completion of the first independent gate, root made an
exact518-file copy of the frozen phase19 candidate at
/tmp/dream035-council-cleanup-workspace (manifest:-cleanup-isolation.json).
NEW astra_council_cleanup_author owns only engine.py and test_council_context.py
there for criterion6. The original candidate remains unchanged for the judge.
The same astra_council_context_gate will review the correction after its first
verdict is frozen. No author edits main or the reviewer original. Latest tracking
passed38 dated records/12 changed paths after accepted phase20 integration.

The fresh Council judge additionally reproduced real IdleWorkQueue preparation
cancellation: before _ask source capture, cancellation joins owned job cleanup,
then a later retry/second handoff admits one HTTP request missing the new raw
constraint. Probe at test_independent.py:504; log:
/tmp/dream035-council-gate-preparation-cancel.log (1 failed/27 deselected,1.05s).
The prior outer-cleanup failure also loses the original required tail through a
later actual request. Correction scope adds only a single private-to-public ask
call in test_explicit_advice_reaches_next_main_turn, all assertions retained.
This boundary adjustment was declared before author edit.

Phase19 first formal independent gate FAIL is frozen at
/tmp/dream035-council-context-gate.md, SHA256
892649e028751247d08286087f89c972078557b2d29a89909f58e8434f67a6e0.
Fresh selected209 passed2.35s; independent28 cases yielded7 failed/21 passed1.60s
with no reported warnings/skips. Two criterion6 blockers; no extra blocker for
explicit model-directed optional snips. Independent original/candidate keyword
evaluation is byte-identical and scope/hashes passed. This is the first failed
phase19 gate, not seven correction attempts. New author corrects only the two
confirmed lifecycle gaps; same judge must gate the new exact candidate.

Correction author observed7 failing cleanup/preparation cases and3 controls before
its first10-pass correction. Additional red1-case capture cancellation showed a
queued AnyIO thread write could lose input; the candidate now joins its owned
capture through repeated cancellation. Another red1-case JSONL append failure
left the raw SQLite row committed without a retained ID. Lead authorized Engine-
only verified committed-row retention under existing RLock, preserving the original
error and rejecting uncommitted/mismatched IDs. WorkingMemory stays untouched.
These are author refinements before the second formal independent gate, not
additional formal gate failures.

Phase19 correction author frozen:/tmp/dream035-council-cleanup-impl.md.
228 selected tests passed2.27s, no skips/warnings. Exact accepted main CLI source
was overlaid read-only in memory for18 completion cases, all passed0.23s with one
anyio assertion-rewrite warning. This overlay is combined integration evidence,
not a pass for the older isolated CLI. Initial wrapper2fail/2teardown errors and
managed20s timeout are retained. Correction changes exactly3 paths; all515 others
and original candidate unchanged. Root read correction source and verified/froze
the full seven-path final delta against main originals in -corrected-lead artifacts.
SAME astra_council_context_gate is rechecking; original FAIL stays frozen. No
phase19 integration or new full-suite claim.

Same-judge correction progress (verdict pending):228 selected cases passed2.30s;
all28 unchanged original independent probes passed1.11s. The prior cleanup replay
now retains required IDs[1,2] and refuses the undersized destination with zero
requests. Real idle-preparation cancellation retains the new raw source, and the
second handoff delivers it. New capture race/persistence-discrimination checks
and final integrity are still underway; these passes are not yet an accepted gate.

Same-judge correction review reproduced a new committed-row identity blocker:
real WorkingMemory.note writes leave connection last_insert_rowid2 while the next
real turns INSERT also receives ID2. A subsequent JSONL append failure falsely
rejects that committed source. After retry/handoff an actual admitted request
omits its tail; the no-notes control retains it and refuses the small destination.
Log:/tmp/dream035-council-cleanup-gate-capture.log. A separate judge fixture
thread-affinity error is being corrected and is not attributed to product code.
Root confirmed turns AUTOINCREMENT schema after a failed graph lookup, then
predeclared a new exact518-file identity correction copy with only engine.py and
test_council_context.py owned by the same author. Old candidates stay frozen.
Same judge will finish its second verdict and review the next exact correction.

Second formal phase19 gate FAIL frozen:/tmp/dream035-council-cleanup-gate.md,
SHAb1c323dac3cc578b80bf50e6903224ba93a05aee5e731a0ff1f5536602711420.
Fresh228 selected and28 original independent cases passed; new capture14 cases
yielded13 passed/1 product failure. Judge secondary-connection affinity fixture
failure remains preserved separately and passed after fixture correction. This is
the second formal failed phase19 gate. Root read the full report and primary
SQLite identity/AUTOINCREMENT/connection-change documentation; links and exact
contract implications are in the shared plan. The same author corrects identity
in a new frozen-baseline copy, and the same judge will perform the third gate.

Identity correction frozen after234 selected passes2.42s, no warnings/skips.
Its pre-edit red2failed/5controls and subsequent7passed are retained. Report:
/tmp/dream035-council-identity-impl.md. Exactly2 changed paths/516 unchanged;
earlier candidates intact. Root read the production delta and verified all seven
final candidate/main-original hashes, freezing -final-lead diff/hashes. The SAME
judge has received this third formal gate; no phase19 integration before verdict.

Third formal phase19 gate FAIL frozen:/tmp/dream035-council-identity-gate.md,
SHA4f84f28677b940a64919ade2dfb8ce6af17fb366be68745fc0d22e7eb3c338df.
Root read the complete report, agrees with its exact ownership finding and applied
the three-failure stop. No fourth implementation or new judge was started. Root
then completed accepted-source full verification:3030 passed/35 skipped/7 warnings
in234.73s, exit0, session21136 ended. Operator limits, ADR dispositions, master
status and current state are updated. The shared plan's explicit commit-receipt
proposal is a review artifact only; all Council source remains unintegrated.

Final bookkeeping first failed because CURRENT still named DREAM-035 as active
while its register status was blocked. CURRENT now has Current work:none, with
the stopped DREAM-035 checkpoint explicit in its body. The first final hash helper
also expected the newer candidate key in the older CLI manifest, which uses new;
that helper failed with KeyError before completing verification. The corrected
read-only helper uses each actual manifest schema; no product source was changed.

The next bookkeeping check exposed CURRENT timestamp predating the newer handoff;
both active-session headers are now synchronized. Corrected final source hashing
passed:all five accepted hashes match, and every one of the seven phase19 main
original/new-file-absence states is preserved. Neither metadata failure is a
product-test or independent-gate failure.

Final tracking passed:38 dated records and13 actual main changed paths checked
against the shared567-hash baseline. All accepted hashes and unintegrated Council
original states were verified. This handoff is finalized; later work must use a
new dated record. No active test process or author/judge implementation remains.
