# DREAM-037 — GitHub source and README update

Recorded: `2026-09-11T13:41:27-05:00`
Work items: `DREAM-037`
Outcome: `active`
Actor: Codex lead.

## Request

Owner explicitly requested updating GitHub and its README. This authorizes the
source commit and non-force push to Red-Weasel/Dream-Agent-Harness.

## Changes

Prepare current public source from the tested working checkout, including the
Dream tagline and README feature/validation summary. Publish from an isolated
checkout based on remote main, preserving the owner's dirty local index/worktree.
Remote main is12 commits behind the local base; the snapshot includes that work
and the subsequent uncommitted implementation without rewriting remote history.

Runtime data and raw root prompt/conversation exports are excluded. Sixteen
runtime-backup paths already existed in remote main; remove them from the new
source tree, preserve local originals. Earlier public commits still contain them.
No history rewrite, model load, runtime restart, dependency install or release.

## Validation

Pending publication manifest, outgoing content review, tracking/link checks and
remote commit verification. Existing full result4097pass/35skip/7warnings and
tagline checks remain scoped evidence; no new full run is claimed. Baseline597
source hashes: /tmp/dream-github-baseline.json. Remote main began at
d1660a595365c8c0432274b0fe6b19cb23bad5cc.

## Unfinished work

Finish candidate audit, commit and push, then verify GitHub README and head.

## Next steps

Use /tmp/dream-github-publish-20260911 for publication; never force-push or
reset the original checkout. Preserve excluded local files and original records.

## Files changed

- `README.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-11-github-update.md`

Preparation check initially caught an unchanged CURRENT timestamp predating this
new handoff. Timestamp corrected before staging. Both failed checks were tracking
bookkeeping, not test failures. Remote-base tracking uses its documented bootstrap
mode because remote main predates the tracking system; session snapshot covers
the four current publication-document edits.

Publication preparation:606 allowlisted files match the working source and staged
Git blobs;16 existing remote runtime paths are staged only as deletions. Original
local data and Git metadata remain unchanged. Ten explicitly linked evidence
JSON files contain synthetic cases, sanitized aggregates or hash manifests and
were read before inclusion; other artifacts remain local. Known credential-pattern
scan has no matches after correcting word-boundary false positives in task-related
HTML/Markdown anchors. This is a bounded scan, not a security certification.

Root and isolated-bootstrap tracking checks passed47 dated records; root snapshot
checks all4 current changed source/doc paths. Full staged diff check reports three
existing historical-document whitespace findings, including an intentional Markdown
hard break; no source/README whitespace defect found. Historical text was retained.
An initial staging request also named ignored data paths; explicit source staging
and tracked-deletion staging completed separately. Final index has no private
runtime paths and no paths outside the606-file allowlist.

GitHub About text was confirmed to contain the original Claude-only wording.
Prepared replacement: Dream. Your models. Your workspace. A multi-model agent
harness with memory, shared tools, and a desktop workspace.
