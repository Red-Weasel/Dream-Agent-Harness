# DREAM-037 Updated publication candidate

Recorded: `2026-09-11T15:09:36-05:00`
Work items: `DREAM-037`
Outcome: `blocked`
Actor: Codex lead.

## Request

Owner renewed publication of current source and README on GitHub main while
requesting Chat controls, Blender diagnostics and the vision repair. Prepare those
concrete updates without bypassing the earlier automatic approval rejection.

## Changes

Refreshed the isolated candidate at /tmp/dream-github-publish-20260911 from the
previous reviewed source allowlist, adding only named source/tests and dated
handoffs for DREAM-038/039/040 and publication tracking. Keep original Git metadata,
dirty work and all local runtime files intact. The original candidate305ea82 and
its public base remain in the isolated clone history. Source and README include
current controls and diagnostics, scoped test evidence and live-runtime limits.

The proposal still removes16 old runtime-backup paths from the public main tree;
those files already exist in remote history. This does not delete local originals
or rewrite public history. No new runtime/session files are included.

## Validation

The candidate has618 allowlisted files. All source, working-tree and staged bytes
matched; the refreshed source changes36 files relative to candidate305ea82. A
bounded credential-pattern scan of those36 files found no matches; this is not a
security certification. The previously reviewed ten evidence artifacts are unchanged.
Staged whitespace checks passed. Clone tracking passed52 dated records and all36
changed paths against candidate305ea82;
source snapshot checks are recorded in the vision handoff. The earlier automatic
review rejected the public push before execution because the broad source snapshot
and16 runtime-backup removals require explicit payload approval. This record does
not reinterpret generic renewed publication requests as that missing approval.

## Unfinished work

No public push, About edit, remote CI or owner acceptance is claimed. The local
candidate is prepared for commit; request approval for its exact resulting hash
before any public write.

## Next steps

After owner approval, check remote main has not moved and push without force.
Update About and verify the remote README/commit. Preserve the isolated clone and
/tmp/dream-github-publication-manifest.json until publication is resolved.

## Files changed

- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-11-github-candidate-refresh.md`
