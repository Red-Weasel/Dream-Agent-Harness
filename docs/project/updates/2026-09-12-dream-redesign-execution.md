# DREAM-052 — Branded workspace implementation

Recorded: `2026-09-12T00:47:14-05:00`
Work items: `DREAM-052`
Outcome: `implemented`
Actor: Codex lead (web, history, Council activity and integration); Astra dream_native_design (native shell, consolidation status, native fixtures and independent web review).

## Request

Owner authorized execution of all six design-plan phases and explicitly requested
Dream identity, colors, floating woman and orange circle. Acceptance: functional
native/web workspace, preserved session/drafts/approvals/media boundaries, real-data
inspector and meaningful backend additions. Existing dirty checkout preserved using
/tmp/dream-redesign-execution-baseline.json (642 source hashes); no owner process
restart or deployment. The six-phase objective is incomplete because substantial
backend follow-through remains.

## Changes

Shared navy/violet/ember web tokens and matching native palette. Original generated
Dream eclipse/floating-adult-woman image copied into source, plus matching native
SVG identity and compact onboarding. GTK sidebar owns native Terminal/Browser and
sends allowlisted view selection only into the authenticated matching session.
Web views do not gain host-command access. Pending native selection waits for session
identity; mismatched sessions are ignored. Contextual dialogs close on navigation.

Chat keeps workspace, model, reported effort, permission mode and composer. The
workspace path expands. Effort opens supported HTTP performance controls or the
existing Council/main-provider controls; handoff lifecycle restrictions remain.
Classic presentation switches without reloading or clearing drafts. Home reads
existing selected-workspace recovery records. Ctrl+K searches fixed destinations
and never turns search text into a model request or command.

Studio expansion and split control retain the same iframe. Narrow Chat hides the
preview to give approvals enough room; a persistent approval-return button prevents
an inspector/Home view from stranding the decision. Source comparison is read-only
and uses already-preserved versions. Preview media events report metadata/playback
or errors separately from visual-review evidence, accepting only the active frame's
window as source. Frame network/origin restrictions are unchanged.

An authenticated bounded /api/media/history read reuses the existing media store:
registered asset ID/path/hash/time/provenance, unreported verification. No new project
database and no automatic archival of arbitrary files. Context uses runtime facts,
observation/stale labels, pinned context, recent loaded-feed failures, existing media
readiness and real save/Council states. Inspector polling stops while hidden.

Consolidation records idle/consolidating/saving/saved/failed/unavailable with source,
session/workspace, timestamps and bounded errors. It claims Saved only after the
existing successful save path, and preserves failed/interrupted outcomes. Council
editing emits bounded stable task/member lifecycle records, with the selected model,
sequential workspace ownership, timestamps and failure detail; display replay does
not repeat work. Existing sequential execution and permissions remain authoritative.
README, desktop guide, ADR-039 and the plan describe delivered vs unfinished scope.
An external review brief is available for Claude/Grok or another independent reviewer.

## Validation

Baseline:24 existing Chat/companion/playback tests passed in15.33s. New navigation,
Council lifecycle and media-history fixtures failed before their implementations.
Intermediate combined tests exposed duplicate Skills controls, lost preview area,
missing-session null handling and a stale timing fixture assumption about the
existing permission-mode status read. These were corrected and retested. A transient
agent edit introduced an indentation error; it was fixed before final compilation
and regression checks. Default-sandbox memory test run was interrupted after socket
restrictions stalled it; approved isolated fixture runs used host loopback access.
One automatic approval review timed out without executing a mixed history-test
command; a smaller local fixture and later approved integration runs completed it.

Final integration gate: 132 passed, 2 existing deprecation warnings in 38.41s;
log /tmp/dream-design-verified-gate.txt. Earlier broad gate130passed/1failed (stale read-only startup expectation), follow-up41passed, and
final Council/history unit17passed. These overlap and are not summed.

Astra native checks:14 passed (8 new boundary tests plus6 existing). Desktop
protocol/bridge/startup/design run81passed,7 GI skips in venv; the native tests were
also run separately with system Python. Memory/runtime regression148passed,20skipped,
2 existing deprecation warnings;7 focused save-status cases. Model-free retrieval
fixture evaluation before/after unchanged (recall@1 .80,@3 .90,@5 .95,MRR .86).

Real GTK/WebKit fixture at720×520 verified advancing tiny32px H.264 playback, parent
origin isolation, reconnect, artifact delivery from Browser, native URL refusal,
all seven web destinations, retained composer draft, closed obsolete modals, all
four visible/reachable approval choices, and a returned Deny without command execution.
Report/screenshots: /tmp/dream-native-combined-final/. Earlier visual review caught
clipped approvals despite passing DOM hit tests; final screenshots verify the fix.
Native WebVTT encoder warning observed; subtitles were not tested.

Responsive Chromium captures: /tmp/dream-home-1600.png,1280,720,390 variants and
matching dream-chat captures. New illustration decoded before screenshots. Generated
asset original remains in ~/.codex/generated_images/01a08d16-2ec1-7c22-9336-2af033c0a170/.
Python compilation and JavaScript syntax checks passed. Graph discovery again returned
Transport closed; focused source reads were used and files read before edits.
Fresh additional agent creation hit the thread limit; the Astra contributor independently
reviewed lead web code and identified the modal/approval/classic-presentation fixes.
No additional fresh reviewer is claimed. No live provider/model or GPU render run.

Final tracking check passed: 68 dated records, all 40 changed source paths covered
against /tmp/dream-redesign-execution-baseline.json. git diff --check passed.

## Unfinished work

Task6 is only partially implemented. Durable-first memory and deferred indexing
with retry/shutdown ownership remain. Concurrent editing still needs independent
provider contexts, isolated writers, cancellation and conflict recovery; Council
currently edits sequentially. Durable lineage for arbitrary Studio output files is
not supplied by registered-media history or in-memory source comparison.

Initial visual-before/after resource measurements, exhaustive accessibility/zoom
qualification, native subtitle playback, live-provider concurrency qualification,
owner aesthetic acceptance and publication were not performed. No perfection or
production-wide readiness claim. The new design is in source; existing owner
processes keep their loaded version until the owner restarts.

## Next steps

Continue DREAM-052's remaining backend contracts under the same master register;
start with durable-first memory save ordering and deferred indexing lifecycle before
concurrent writers. Use docs/reviews/dream-design-review.md for independent review.
Do not discard the pre-existing dirty tree or stage/publish it wholesale. Keep the
session snapshot for attribution. No fixture child or owner-model process should be
restarted to resume source work; all task-owned fixtures finish through their own
cleanup. Owner may inspect the new appearance on their next normal Dream restart.

## Files changed

- `README.md`
- `START_HERE.md`
- `docs/desktop.md`
- `docs/project/CURRENT.md`
- `docs/project/DECISIONS.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-12-dream-redesign-execution.md`
- `docs/reviews/dream-design-review.md`
- `docs/superpowers/plans/2026-09-11-dream-design-upgrade.md`
- `dream/core/engine.py`
- `dream/desktop/dream.svg`
- `dream/desktop/onboarding.py`
- `dream/desktop/style.css`
- `dream/desktop/window.py`
- `dream/gui/conversation.py`
- `dream/gui/media_routes.py`
- `dream/gui/static/companion.js`
- `dream/gui/static/dream-eclipse.png`
- `dream/gui/static/index.html`
- `dream/gui/static/theme.css`
- `dream/gui/static/workspace.css`
- `dream/gui/static/workspace.js`
- `dream/media/service.py`
- `dream/media/store.py`
- `dream/tui/app.py`
- `dream/tui/council.py`
- `tests/test_council_work.py`
- `tests/test_desktop_companion.py`
- `tests/test_desktop_design.py`
- `tests/test_media_history.py`
- `tests/test_memory_save_status.py`
- `tests/test_turn_timing_ui.py`
- `tests/test_workspace_design.py`
