# DREAM-035 — Commit interface review at stopped checkpoint

Recorded: `2026-09-11T01:48:00-05:00`
Work items: `DREAM-035`
Outcome: `blocked`
Actor: Codex lead; fresh Astra reviewer `/root/astra_commit_interface_review`.

## Request

Use fresh Astra context to challenge and verify the harness. This bounded session
reviews the proposed storage commit receipt after phase19's three formal FAILs.
Acceptance is a source-grounded compatibility review, explicit limits, preserved
failed evidence and a checked handoff. It is not a fourth implementation or gate.

Before documentation edits, root recorded scope as this new update, CURRENT.md,
the shared adaptation plan and the manual harness review packet. The reviewer
owned only /tmp/dream035-commit-interface-review.md. Product source and tests are
read-only. Baseline /tmp/dream035-commit-interface-baseline.json contains570 hashes;
the pre-existing dirty tree contained359 status entries. MASTER remains blocked.

## Changes

The fresh reviewer supports saving the actual INSERT cursor ID and delivering it
only after acknowledged commit. It identified three decisions to settle before
implementation. These are source-review findings, not newly executed failures.

- Current add_turn unconditionally commits the shared connection. If an earlier
  INSERT succeeds and a later statement fails without ending that transaction,
  another invocation can commit both writes. Its own receipt can still identify
  its own row correctly; absence of an earlier receipt cannot prove that earlier
  row will never become committed. Transaction cleanup is a distinct concern.
- A callback can fail after DB commit. The proposed contract must specify whether
  JSONL is skipped, preserve the callback error and report incomplete capture.
  One callback invocation does not guarantee delivery through process death.
- Ordinary log_turn callers currently discard its None return. Pending Council
  capture requires real receipt support; a permissive mock can accept a keyword
  without invoking the callback. Retrying after TypeError could repeat a write.

Root agrees with these distinctions. The shared proposal now recommends an
explicit transaction-entry/failure contract, callback-first transcript ordering,
and observable missing-receipt failure. These remain design choices to verify in
the resumed candidate. No broader rewrite of store methods is assigned.

Root's earlier mention of MemoryStore._write was an unverified helper guess.
The reviewer found no such helper and root corrected the assignment. The actual
precedent is add_note, which retains its cursor, commits and returns its ID.

## Validation

Observed fresh read-only report:
/tmp/dream035-commit-interface-review.md, SHA256
7ede8d86651b59b9d32ecfc129cae1bc10cd7c3171061af406160f7f418b5b70.
The report includes exact inspected source hashes and primary SQLite/Python links.
Root read the report and focused add_turn, add_note, upsert_memory, delete_memory,
WorkingMemory.log_turn and the round-stats stub. Root's graph attempts returned
Transport closed; focused source reads followed. Reviewer graph results were
partially successful, with later inconsistent results disclosed in its report.

Root's focused callsite audit found WorkingMemory is the only production direct
add_turn caller; five Engine log_turn calls ignore the return. Inspected direct
test callers also ignore it. The None-returning round-stats stub has no pending
Council transfer. Unknown external overrides are not qualified by this audit.

No product tests, prototype, model calls, benchmarks or new gate ran this session.
The prior accepted-source full remains3030 passed/35 skipped/7 warnings in234.73s,
exit0 at /tmp/dream035-final-recovery-accepted.log. It is previous evidence, not a
new run or a pass for the isolated Council candidate.

Final read-only SHA256 comparison passed:567 baseline files unchanged, exactly
three existing documentation paths changed plus this new handoff. All product
source and test hashes remain unchanged, including accepted main fixes. The two
phase19 new source/test paths remain absent in main; the review report's frozen
hash matches. No previous dated handoff was modified.

`python3 scripts/check_project_tracking.py check --snapshot
/tmp/dream035-commit-interface-baseline.json` passed with39 dated records and four
changed paths. No validation failure occurred in this documentation-only session.

## Unfinished work

Phase19 remains stopped after three same-judge FAILs. The lead agrees with the
last reproduced ownership failure. The Alpha Omega skill explicitly says
"three FAILs at one gate = stop the loop and surface the findings + disagreement
to the founder." This review does not reset that count or replace the judge.
No fourth source attempt, owner runtime operation or external transmission ran.
No author, test process or lock is owned by this session after review completion.

The new transaction scenario is a source-based deduction, not an observed owner
incident or executed reproduction. Existing effort controls remain unchanged.
Native/provider/model quality, installation and owner acceptance are unqualified.

## Next steps

Review the concrete stopped-checkpoint proposal and fresh compatibility findings.
Any resumed implementation needs a new source snapshot and dated record; retain
the same Council judge and all42 prior plus8 identity probes. Verify the proposed
receipt/failure contracts and required keyword evaluation before considering
integration. Do not edit the finalized recovery handoff or replay owner sessions.
The manual Claude/Grok packet includes these questions; no external feedback was
received. The broader goal remains incomplete.

## Files changed

- `docs/project/updates/2026-09-11-harness-commit-interface-review.md`
- `docs/project/CURRENT.md`
- `docs/superpowers/plans/2026-09-10-harness-adaptation-loop.md`
- `docs/harness-review-packet.md`
