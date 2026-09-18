# DREAM-055/056 — Project and skill workspaces

Recorded: `2026-09-12T19:15:17+00:00`
Work items: `DREAM-055, DREAM-056`
Outcome: `implemented`
Actor: Codex lead (UI, shared Engine context, document storage, integration, docs); Astra contributors (skill backend and project lifecycle); independent Astra workspace_pages_review and dream_native_design.

## Request

Replace Skills popup with a real page for full instructions, editing and creation.
Build persistent Projects for returning to a workspace and continuing conversations.
Owner expanded Projects to manage documents, consolidated summaries and project memory;
also asked about Hermes learning and the tool feed's argument character counts.
Acceptance: real pages, persisted edits and history, safe project transitions, truthful
context restoration, explicit memory selection and retained unsaved drafts.

Baseline: /tmp/dream-workspace-pages-baseline.json. The large existing dirty tree was
preserved; no root Git staging/reset, owner restart or new publication occurred.
Graph discovery failed with Transport closed and one prior hanging request; focused
source fallback followed. One independent reviewer could access an existing index,
but the new files were absent. Root's later retry still returned Transport closed.

## Changes

Skills shows full SKILL.md, supports new skills and private overrides, preserves safe
support files, compares SHA revisions, and refreshes discovery. Existing originals
and extension trust/enabled settings remain. Bundles with unsafe references, symlinks,
special/hidden entries or excessive size are refused. Managed files remain private.

Projects stores explicit workspace identities and conversation associations. It has
Conversations, Documents, Memory, Files & context and Instructions views. Older
sessions without workspace provenance can be explicitly linked; ownership is never
guessed. Catalog revision checks protect settings; workspace identity is immutable.

An idle-only App consumer operation owns old stop/new start, preserving provider,
model and effort. Active/queued/background/recording work blocks a switch. Grants,
old Studio handles, uploads and pending preview requests are cleared after successful
switch. No opening action generates a model response or runs consolidation. Saved
conversation text is bounded and retained on failed first send, consumed after known
success; this is a new provider session, not exact native resumption or tool replay.
Startup/new-chat association also covers Council-only conversations.

Private Markdown documents are authoritative, revision-checked and atomically saved.
Selected notes (6,000 formatted characters total) enter common Engine project context
alongside saved instructions and existing pins, including ordinary autonomous turns.
Original summaries and latest-source linked legacy memory records are read-only;
users can copy them into editable project notes. Legacy merged provenance is explicitly
limited. Global memory is not migrated or repartitioned; no new embedding workload or
automatic learning policy is enabled. See [operator guide](../../public/projects-and-skills.md)
and ADR-041. Sessions and consolidation remain distinct persistence operations.

Read tool labels now distinguish request characters from returned characters; request
length is not a file-read limit. No repeated-read root cause was inferred from that label.

## Validation

Observed model-free gates in existing Python3.12 environment, synthetic data/providers:
- Skills author gate:99passed,2existing Starlette warnings,0.80s.
- Project author final gate:197passed,12.09s, including32 new project tests and native
  SDK/AnyIO owner-task lifecycle fixtures. /tmp/dream-project-library-final2.log.
- Root earlier UI/API gates:6passed6.86s;66passed25.30s;69passed6.09s.
- Real persistence probe:1passed1.98s after correcting two fixture defects (wrong
  Store method name, then ambiguous nested heading locator). Earlier expanded gates
  each had34passed/1failed; failures were not counted as passes.
- Fresh independent review reproduced two UI races, then2 Chromium probes passed2.34s:
  one connected document editor preserves drafts; delayed old conversation reads
  cannot replace a newer selection.16 backend subset checks passed0.26s.
- Separate independent reviewer found pending-switch typing, a late save refresh,
  stale workspace responses and missing Files remount. Three corrected Chromium
  probes passed2.53s; Files remount source-reviewed. See temporary independent probes.
- Independent documents review reproduced understated context accounting and deep
  JSON500. Both repaired with regression cases; malformed project JSON also handled.
- Root final combined gate:343passed,2existing Starlette warnings,47.39s; log
  /tmp/dream-workspace-pages-final-gate.log.30 final source/test hashes unchanged.
- Wheel built offline through uv:255files;216 package files match local source,
  new assets present and no database/session-ledger runtime files found. SHA256
  0272cb5431514d22b00a7cf51842795fadaccfb10bf0cef9cc5cc20ed6286701. Package report in /tmp/dream-workspace-pages-package-report.json.

Sandbox limitations: temporary loopback and asynchronous threadpool fixtures could
not run normally. A stalled owned test was stopped (exit130); host synthetic retries
passed. One automatic approval review timed out before execution, permitted retry
succeeded. A mistyped test filename produced no collected tests; corrected immediately.
Direct hatchling was absent from the venv and sandbox uv cache locking failed; approved
offline uv build succeeded using cached dependencies. No network dependency install.

Native WebKit/live model/GPU rendering, comparative benchmarks and owner acceptance
were not run. Chromium viewport checks at1280,720,390 passed and screenshots were
inspected by the independent reviewer. Existing terminal/native SDK fixtures do not
substitute for native desktop visual qualification. The previous full4478-test release
gate is historical; it is not represented as a new full-suite run.

## Unfinished work

No implementation work intentionally left within the requested page scope. Owner
acceptance and a normal Dream restart remain. Supporting-file editing, exact native
session resume, semantic retrieval of the new unselected notes, legacy memory scope
migration, and faster/deferred consolidation are not implemented by these pages.
Existing recall continues separately. No universal model-quality gain is claimed.
New source and README changes are local, not pushed. Earlier DREAM-054 Git history
rewrite still requires explicit authorization and remains untouched.

## Next steps

Owner opens the next normal Dream process, saves a workspace in Projects, links any
older relevant session, and reviews Documents/Memory. Use Skills to edit or create a
private skill. If a save reports a conflict, retain/copy the draft and reopen the
saved record; do not overwrite unknown changes. Future memory optimization remains
under existing DREAM-052 rather than a second backlog. No model or owner server was started. Temporary fixture servers/browsers exited
with their tests; no task-owned monitor or model process remains. Tracking check passed70dated records and38changed source paths against the
original snapshot; final whitespace diff check passed.

Hermes research: official [memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory)
and [skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)
document persistent notes, searchable sessions, reusable procedural skills and review
writes. This supports an architectural description, not a controlled performance
comparison or model-weight improvement claim. No benchmark was run.

## Files changed

- `README.md`
- `docs/extensions.md`
- `docs/project/CURRENT.md`
- `docs/project/DECISIONS.md`
- `docs/project/MASTER_PLAN.md`
- `docs/public/projects-and-skills.md`
- `docs/public/runtime.md`
- `dream/config.py`
- `dream/core/engine.py`
- `dream/gui/project_document_routes.py`
- `dream/gui/project_library_routes.py`
- `dream/gui/server.py`
- `dream/gui/skill_routes.py`
- `dream/gui/static/attachments.js`
- `dream/gui/static/companion.js`
- `dream/gui/static/feed.js`
- `dream/gui/static/index.html`
- `dream/gui/static/library.css`
- `dream/gui/static/library.js`
- `dream/gui/static/workspace.js`
- `dream/projects/documents.py`
- `dream/projects/library.py`
- `dream/projects/workspace.py`
- `dream/skills/editor.py`
- `dream/tui/app.py`
- `tests/test_desktop_chat.py`
- `tests/test_diagnostics.py`
- `tests/test_feed_verbosity.py`
- `tests/test_project_documents.py`
- `tests/test_project_library.py`
- `tests/test_project_library_lifecycle.py`
- `tests/test_project_library_routes.py`
- `tests/test_project_workspace_engine.py`
- `tests/test_project_workspace_ui.py`
- `tests/test_skill_editor.py`
- `tests/test_skill_editor_routes.py`
- `tests/test_workspace_library_ui.py`
- `docs/project/updates/2026-09-12-projects-skills-workspaces.md`
