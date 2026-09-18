# DREAM-072 — Project handoffs and durable memory tools

Recorded: `2026-09-14T02:08:31Z`
Work items: `DREAM-072`
Outcome: `implemented`
Actor: Codex lead; Astra durable_memory and handoff_ui implementers; fresh Astra
handoff_memory_review independently reviewed behavior and failure recovery.

## Request

Owner approved the recommended harness improvements. This pass implements the
first priority, project continuity and memory saving, and prepares native-use
qualification with a dependency check and real terminal recovery test. Preserve
the existing dirty tree, use no local models/GPUs and do not publish private data.
Acceptance: editable sourced handoff, explicit save/context inclusion, saved memory
before optional vectors, recoverable failures, truthful shutdown wording and tests.

## Changes

Projects conversations have Draft project handoff; Documents has New handoff.
Capture reads bounded stored summary/user/assistant excerpts, preserving project,
session and turn IDs. Missing facts stay UNKNOWN and assistant reports remain
unverified. Review opens the existing Markdown editor as an unsaved draft. Save
and context inclusion remain explicit. Late responses cannot replace newer drafts
or follow project/session/navigation changes. No inference or archive mutation
occurs while drafting. Existing durable document storage is reused.

remember, memory_write, memory_append and memory_str_replace save authoritative
Markdown before SQLite commit without entering the embedding model. Immediate
keyword recall and manual links remain; vectors use existing backfill. File-write
failure rolls back the row, postfile commit failure identifies the recoverable
file, and derived MEMORY.md failure reports saved-with-warning. Edits clear stale
vectors/chunks/automatic links; in-flight vectors must match current row/content.
Backfill reports actual installations. File-tool timestamps, versions and caps
remain intact. See ADR-052 in ../DECISIONS.md.

Closing now states conversations are saved and describes optional consolidation
as a model pass that may take minutes. Declining does not imply discarded work.
README and public operator instructions reflect the new behavior.

## Validation

- Snapshot /tmp/dream-memory-handoff-baseline.json captured751 source hashes before
  edits. Existing modifications were preserved. Code graph discovery hung and was
  cancelled; focused source reads were the documented fallback.
- Root backend tests initially failed4/4 for absent handoff method/route. Added
  behavior and passed. Additional multiline test reproduced a9322character draft
  caused by quote-prefix expansion; bounded rendered excerpts repaired it.
- UI author observed missing-button failures before implementation;20 CPU Chromium
  tests passed including10 new tests. Root real API-to-browser-to-editor-to-file
  test saved and reopened the actual private Markdown without a model call.
- Final combined CPU gate:160 passed,8 skipped in24.25s. Covered project service,
  routes, lifecycle, documents, browser drafts, durable saves, memory files, recall,
  curation and store hardening. Used existing Python3.12 .venv.
- New memory regressions19 cover slow/failing embeddings, write/commit/index
  failures, timestamp/version preservation and stale edit/delete/backfill results.
- Required keyword-only fixture memory evaluation before and after unchanged:
  recall@1 .80, @3 .90, @5 .95, MRR .86 over20 queries. No live memory/model data.
- Fresh independent reviewer found no blockers. Final extension check24passed
  includes19 author tests plus5 independent service/persistence/tool probes.
  Separate independent CPU Chromium probe verified navigation/draft/discard.
  Private reviewer evidence lives under /tmp/dream-handoff-review/.
- Real terminal Ctrl-C/continued REPL/declined-consolidation exit:1passed3.21s.
  `.venv/bin/python -m dream desktop --check` reported GTK3/VTE/WebKitGTK4.1 ready.
  This verifies prerequisites, not complete owner desktop acceptance.
- Initial sandbox async/loopback tests stalled or could not bind. Owned test runs
  were stopped and exact isolated CPU suites passed with approved escalation.
  Initial UI fixture import/independent locator errors were corrected. uv cache
  permissions and unavailable dependency resolution prevented fresh uv setup;
  existing .venv was used. Ruff unavailable; node syntax check passed.
- Initial tracking check rejected a filename/date mismatch; corrected the new
  filename to match its UTC recorded date and updated its links. A second check
  required backtick-wrapped changed paths; formatting corrected.
- Tracking passed:83 dated records,17 changed source paths checked against the
  session snapshot. No app restart, local model/GPU load,
  Git commit/push, public release, real memory export or power-loss test occurred.

## Unfinished work

None within the bounded implementation pass. Native owner acceptance and live
model quality/latency remain unverified. Optional model consolidation may still
take minutes. No new persistent background indexing worker was introduced.
Internal upsert callers retain existing synchronous embedding behavior. Memory
file replacement flushes contents but does not fsync parent directories; rename
survival after sudden power loss is not guaranteed. Project handoffs are reviewed
excerpts, not automatically verified project summaries. Browser reload still
requires saving drafts. All contributing agents have finished; no task worker
or owned test process is intentionally left running.

## Next steps

After a normal restart, use Projects → open conversation → Draft project handoff,
review/edit, Save document, and optionally Include in project context. Verify
ordinary native workflows under DREAM-074/012, collecting attributable session
failures for planned DREAM-073 model/task calibration when models are available.
Do not replay old actions or restart an active owner session automatically.

## Files changed

- `README.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/DECISIONS.md`
- `docs/project/updates/2026-09-14-project-handoffs-durable-memory.md`
- `docs/public/projects-and-skills.md`
- `dream/projects/library.py`
- `dream/gui/project_library_routes.py`
- `dream/gui/static/library.js`
- `dream/memory/store.py`
- `dream/memory/longterm.py`
- `dream/tools/memory_tools.py`
- `dream/tools/memory_file_tools.py`
- `dream/tui/app.py`
- `tests/test_project_handoff.py`
- `tests/test_project_handoff_ui.py`
- `tests/test_durable_memory_save.py`
