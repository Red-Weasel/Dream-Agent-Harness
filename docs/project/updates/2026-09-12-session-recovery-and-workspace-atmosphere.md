# DREAM-057/058 — Session recovery and workspace atmosphere

Recorded: `2026-09-12T19:37:35+00:00`
Work items: `DREAM-057, DREAM-058`
Outcome: `implemented`
Actor: Codex lead; Astra read_loop_review, session_errors_review, dream_workspace_atmosphere; fresh Astra session_fix_verifier.

## Request

Trace hundreds of repeated reads, the tool-round limit, ignored correction and
errors in the current session. Repair confirmed harness failures. Owner added
stronger Dream aesthetics throughout actual working views, beyond the Home art.
Acceptance is recorded in the master register for DREAM-057 and DREAM-058.
Baseline: /tmp/dream-session-read-loop-baseline.json. Preserve the existing dirty
tree, private memories and active owner work; no model loads, replay or restart.

## Changes

Read-only session audit found 108 calls in the first turn, 94 of them read_session;
82 targeted the current session. Only 3 calls read project files. Self-retrieval
created further records to read. The first turn hit 100 tool rounds, with roughly
35 minutes approval waiting and 31 minutes active work. Four failed history calls
used an excessive offset, excessive requested page size, or nonexistent IDs.
A later shell missing-directory message was inside a compound exit 0 command.
The queued correction appeared in desktop history before the turn ended, but
entered durable/model conversation only when the next request began.

Current-session history retrieval now stops at the latest durable user message.
Earlier requests remain recoverable. Past-session searches exclude the active
session before SQL LIMIT. Raw pages allow 12,000 Unicode code points instead of
1,200; serialized delivery bounds preserve valid JSON and continuation metadata.
Stored global turn IDs are labeled as IDs, rather than resembling local positions.
Conditional paging guidance replaces instructions to exhaust every page. The
HTTP limit message states the configured number and distinguishes rounds from
individual tool calls, context and runtime. Default 100 is unchanged.
See [ADR-042](../DECISIONS.md#adr-042--session-recovery-reads-a-bounded-past-2026-09-12).

The next user turn reached four see calls, then the server rejected image input
because the loaded DeepSeek4 had no vision sidecar. Architecture support had
enabled image tools but did not establish loaded readiness. Read-only MachX
source confirmed props does not expose this readiness. The exact rejection records negative loaded-image readiness separately from
architecture support. Retained images become explicit uninspected-image markers,
see is disabled, and the failed request is not retried. A new explicit text turn
can continue. Reconnect/model change resets this observed result; effort and
performance changes do not. No sidecar is installed or model reloaded.

Workspace styling now uses the bundled Dream eclipse artwork,
navy/violet/ember colors and opaque reading/editor panels. It changes presentation,
not model behavior or telemetry. Populated Chat, Projects/Skills banners and
Studio chrome carry the artwork; the artifact iframe retains its own colors.
Classic/quiet presentations, reduced motion, visible focus and drafts are retained.

## Validation

Initial round-metadata test failed as intended, then 9 backend checks passed.
Memory author observed 6 failing regressions before the repair; initial gate 87
passed,4 skipped. Lead combined recovery gate 116 passed in 4.94s. One earlier lead
invocation named a nonexistent test file and collected no tests; corrected above.
Fresh reviewer observed 44 existing passes and 6 independent concurrency, persistence,
identity, limit and transaction checks. Review found JSON escaping could exceed the
backend cap. Five new failing cases reproduced it; serialized-size page packing
repaired it. Actual main and delegated transport fixtures reconstruct escaped
content at 24,000- and 700-character caps with valid continuation offsets.

A second independent finding showed delegated missing-sidecar errors did not
retain negative readiness. Its failing reproduction now passes after shared
main/nonstream rejection handling. Vision author gate: 126 passed in 3.73s.
Final fresh independent gate: 68 passed in 2.75s, no remaining blocking findings.
Temporary independent tests and hashes are under /tmp/dream057* and
/tmp/test_dream057*. No comparative model performance claim follows from these fixtures.

Design author gate: 29 passed in 23.75s plus one final responsive browser probe
in 2.56s. Screenshots cover populated Chat, Projects, Skills and Studio at
390/720/1280 widths; lead inspected desktop Chat/Studio/Projects and narrow Chat/Skills.
Classic and quiet artwork removal, reduced motion, keyboard focus, retained drafts,
scroll behavior and artifact colors passed. Screenshots: /tmp/dream-atmosphere-*.png.
No before screenshot was taken.

Final integrated gate: 279 passed in 31.93s, exit 0, no skips or warnings. Log:
/tmp/dream-recovery-atmosphere-final.log. Twelve reviewed source/test hashes were
unchanged through the final run. Tracking passed with 71 dated records and
19 changed source paths. Intermediate tracking failures (latest-handoff link,
timestamp and new test attribution) were corrected.

Sandbox async fixtures stalled; task-owned test invocations were stopped and
bounded model-free tests ran with host approval. Graph transport failed for root,
including explicit project retry; reviewers could use the existing graph index.
No live inference, external model review, workload benchmark, application restart,
Git push or owner acceptance. Redacted audit: /tmp/dream-read-loop-audit.json.
Raw private transcripts and authentication tokens were not exported into source.

## Unfinished work

None within the implemented retrieval, observed-error recovery and CSS scope.
Live steering remains unimplemented: Queue waits for the active turn. Vision
still requires a suitable model load with its sidecar. No first-request vision
readiness guarantee is possible from the current MachX props response. Recovery
recognizes the verified exact error; unrelated errors retain their behavior.
Native daily-use, actual model quality and owner aesthetic acceptance remain open.
All task-owned agents/tests finished; no monitor, inference or server remains running
on this task's behalf. Public release was not updated by this work.

## Next steps

Owner restarts Dream normally to load backend changes, then checks the working
views and ordinary project continuation. For visual review, choose a load with its
vision sidecar. A future separately scoped steering change should persist accepted
corrections and apply them exactly once at a safe request boundary; this work did
not alter queue semantics. Do not replay old renders or edit inference-engine files
as part of resuming this handoff. No owner acceptance is recorded.

## Files changed

- `docs/project/updates/2026-09-12-session-recovery-and-workspace-atmosphere.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/DECISIONS.md`
- `docs/runtime-controls.md`
- `dream/config.py`
- `dream/core/backends/openai_compat.py`
- `dream/memory/store.py`
- `dream/tools/memory_tools.py`
- `tests/test_session_read_loop.py`
- `tests/test_session_turn_pages.py`
- `tests/test_session_recovery_listing.py`
- `tests/test_memory_file_tools.py`
- `tests/test_harness_backend_quality.py`
- `tests/test_missing_vision_sidecar.py`
- `dream/gui/static/theme.css`
- `dream/gui/static/workspace.css`
- `dream/gui/static/library.css`
- `tests/test_workspace_atmosphere.py`
