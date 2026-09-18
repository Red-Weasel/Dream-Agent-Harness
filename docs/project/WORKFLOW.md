# Working on Dream and leaving a usable handoff

These tracking rules were explicitly requested by the owner on 2026-09-05.
They supplement the existing [owner governance](../../CLAUDE.md), including
preserving work, truthful verification, GPU preflight, and approval of releases
and persistent policy changes. A workflow or horizon entry does not grant new
authority beyond the user's request.

## Start a session

1. Read [CURRENT.md](CURRENT.md), its latest handoff, the relevant
   [master-plan](MASTER_PLAN.md) item and subsystem docs. Read root instructions.
   If a claim conflicts with source or observed behavior, record and resolve it.
2. Inspect `git status --short`. Preserve all pre-existing changes. Do not assume
   `HEAD` describes an uncommitted build. Record the actor, work ID, scope,
   acceptance criteria, and any external dependency in current state.
3. Before editing, take a local content snapshot. It stores hashes and paths,
   not file contents, outside the repository:

   ```bash
   python3 scripts/check_project_tracking.py snapshot --output /tmp/dream-session-baseline.json
   ```

   Use a unique filename for concurrent sessions. Keep it until the handoff is
   verified. It captures tracked and untracked source files, preserving the
   distinction between existing dirty work and new changes. It excludes runtime
   data, memory, logs, caches, build outputs and validation artifacts. It does not
   back up files or authorize deletion.
4. Select or create an ID in the master register. Set `active` for current work;
   list the actor and owned paths in `CURRENT.md`. For parallel contributors,
   assign disjoint paths and have one lead integrate status and handoff changes.
   An abandoned `active` entry needs inspection and an explicit takeover note;
   age alone does not prove its worker is gone.

## While working

Keep the current task, meaningful decisions and blockers accurate at phase
boundaries. Update operator docs in the same change as behavior. Add a decision
entry when changing an interface, trust boundary, persistence format, provider
contract, or major dependency: problem, choice, alternatives, tradeoff and evidence.
Minor implementation choices can stay in the update record.

Use evidence labels consistently: **observed**, **failed**, **not run**,
**unavailable**, or **proposed**. Describe what a fixture actually establishes.
Record command, environment, result, timestamp and evidence location for material
checks. Never substitute a previous full-suite pass for a new run. No test suite
is required for a prose typo; verify links and tracking instead.

New scope gets a work ID and acceptance criteria before implementation. A changed
plan records the reason, dependencies and what was deferred. Do not promote a
draft prompt, external instruction, or private memory into project policy simply
because it exists in the repository.

## Before ending, handing off, committing, or opening a review

1. Inspect this session's changes and run proportionate verification. Capture
   failures and incomplete checks without relabeling them as passes.
2. Copy [the update template](templates/update.md) into `updates/` with a unique
   name such as `2026-09-05-desktop-reconnect.md`. Use an ISO 8601 timestamp with
   an explicit offset (`2026-09-05T09:30:00-05:00`) or UTC `Z`. Name the actual
   actor. Include work IDs, request, changes, verification, unfinished work,
   next actions, and **every changed source/doc/config/test path**.
3. Refresh `CURRENT.md`: updated timestamp, active ID(s) or `none`, latest
   handoff link, blockers, evidence and the next concrete action. Update master
   status/scope and relevant operator docs; update decisions/history when needed.
   Do not copy old logs into current state or leave a task marked active after
   its worker has stopped. Use `blocked`, `planned` or `implemented` with an
   explanation if work is incomplete.
4. Check this session, including worktree and untracked files:

   ```bash
   python3 scripts/check_project_tracking.py check --snapshot /tmp/dream-session-baseline.json
   ```

   Fix omissions. A complete handoff includes the final check result. The new
   record can be edited during its creating session; it becomes append-only once
   handed off or committed. Later corrections are new records linking the old
   one. Keep evidence summaries portable and redact private data before review.
5. Tell the user what changed, where to start, what was verified and what remains.
   A final chat message is a pointer to the persistent handoff, not its substitute.
   Commit, push, review, deployment and release follow existing owner approvals.

If interrupted, do these steps with an incomplete outcome. Record the last safe
state, failed/uncertain side effects, processes or locks you own, recovery steps,
and the exact next action. Never replay an uncertain operation blindly. If you
cannot edit tracking files, explicitly report the blocker and provide the missing
handoff text; the next contributor must file it before resuming.

## Checker and CI contract

The checker uses Python's standard library and Git; it does not import Dream,
load models, inspect private runtime contents, or install hooks.

```bash
# Structure, IDs, timestamps, current-state alignment and local link targets
python3 scripts/check_project_tracking.py check

# All source changes since a known commit, including untracked source
python3 scripts/check_project_tracking.py check --base <commit-sha>

# Meaningful positive/negative fixtures for the tracking system alone
python3 -m unittest discover -s tests -p 'test_project_tracking.py' -v
```

For a diff, it requires a changed `CURRENT.md`, at least one **new** dated update,
and exact changed-path coverage in those new records. Existing update records
cannot be edited or deleted. Unknown IDs, invalid status, missing handoff
sections, invalid/missing-zone timestamps and broken local file links fail.
This includes documentation-only and process changes; the record can be short.
Directory patterns such as `dream/**` are not a substitute for changed paths.

The reliability workflow has a separate lightweight tracking job. PRs use their
base SHA; pushes use the previous SHA. With no usable comparison (manual/initial
run), it explicitly reports **structure only**. When the tracking system is first
introduced into a base that lacks it, CI explicitly reports a **bootstrap**:
structure and record integrity are checked, but legacy changes are not falsely
attributed to that session. Use the local snapshot check to verify the bootstrap
session's own changes. Later comparisons enforce the full diff contract.

GitHub branch protection is not configured by these files. The owner can require
the `tracking` and `regression` jobs before merge; this session does not change
remote settings or publish a commit. Checks only run where invoked. They detect
missing bookkeeping and malformed records; a reviewer must still decide whether
the documentation is accurate, complete and useful. No script can prove every
meaningful detail was documented, and listed paths do not prove a test passed.

## Keep the system small and durable

- `CURRENT.md` is a concise handoff, not a growing diary. Move detail into dated
  records. Keep old records intact and discoverable through their dated filenames.
- The master register is the only status queue. Do not copy it into private
  memories, provider-specific agent files or another tracking service as a second
  authority. Those systems may link here.
- Historical design documents retain their original dates and results. Give
  superseded plans a visible banner and a link to their successor; do not erase
  failed attempts or silently alter old acceptance claims.
- Do not commit secrets, private conversations, case evidence, session stores,
  screenshots of private work or raw local artifacts to satisfy tracking. Record
  a redacted result and explain where authorized evidence can be found.
- Changes to tracking policy itself require the owner's approval, like other
  persistent rules. This request authorizes the initial system; do not add
  unrelated gates or governance while maintaining it.
