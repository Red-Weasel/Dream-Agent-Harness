# DREAM-027 — One interface across agents

Recorded: `2026-09-06T11:00:02-05:00`
Work items: `DREAM-027`
Outcome: `proposed`
Actor: Codex, requirements documentation only.

## Request

The owner clarified that Dream’s central value is a seamless interface for the
agent of choice, reducing fatigue from continually changing clients and upgrades.
The desired experience is to open Dream and accomplish the work there. This
corrects the previous recommendation to routinely alternate between Dream and
native clients: those clients’ advantages should inform Dream’s integration work.

## Changes

Recorded the direction and proposed acceptance outcomes in the existing master
register as DREAM-027. Refreshed current state and history. Preserve native agent
strengths, project/task continuity and clear compatibility management; avoid a
common interface that silently loses useful provider-specific capabilities.
External engines/tools remain permissible implementation components under the
owner’s previous direction. User-visible context switching and manual media
handoffs are gaps to reduce, not the intended completed product experience.

DREAM-026 was already occupied by intervening GLM/MachX work, which was read and
preserved. No source, provider settings, credentials, models or running server
was changed. This record specifies product intent; it is not universal parity
verification or an approved mechanism for automatic software upgrades.

## Validation

Observed: repository entry points, latest current state, master register, prior
reasoning handoff and dirty tree inspected. Snapshot:
`/tmp/dream-unified-direction-20260906.json`. Observed tracking check passed: 10 dated records, all four session-changed
paths covered, prior handoffs preserved. No runtime tests, code audit, live provider calls or benchmarks
were performed for this requirements clarification.

## Unfinished work

DREAM-027 capability inventory and implementation breakdown remain to be done.
Provider-private context, authentication and media interfaces must be qualified;
seamlessness is the target, not an existing verified capability. Existing account,
media and avatar limitations remain recorded under their original work items.

## Next steps

Assess representative native-client versus Dream workflows, identify where users
must leave Dream or lose capability/context, then prioritize integration work.
Validate the resulting experience with real coding, research, review and media
tasks. No additional spending/API keys; no unrelated upgrades or model loading.

## Files changed

- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/HISTORY.md`
- `docs/project/updates/2026-09-06-unified-workspace-direction.md`
