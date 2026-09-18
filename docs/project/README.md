# Dream project hub

Start at [the repository front door](../../START_HERE.md).

| Document | Owns | Update when |
|---|---|---|
| [CURRENT.md](CURRENT.md) | Present state, active handoff, blockers, next action | Every work session that changes the repo |
| [MASTER_PLAN.md](MASTER_PLAN.md) | Work IDs, priority, status, acceptance, dependencies | Scope, status, acceptance, or priority changes |
| [WORKFLOW.md](WORKFLOW.md) | Contributor procedure and tracking contract | Owner-authorized process changes |
| [DECISIONS.md](DECISIONS.md) | Architectural choices, reasons, tradeoffs | A consequential design decision changes |
| [HISTORY.md](HISTORY.md) | Historical plan index and chronology | A milestone or historical document is added |
| [updates/](updates/) | Dated, append-only change records and handoffs | Every completed or interrupted change session |
| [templates/update.md](templates/update.md) | Copyable handoff form | The recording contract changes |

Current facts live in `CURRENT.md`; item status lives in `MASTER_PLAN.md`.
Subsystem guides own operational details. Dated reports and old plans are
snapshots. If these disagree, inspect implementation and evidence, fix the live
documents, and leave a correction record rather than choosing the newest claim
without checking it.

Keep this hub small enough to read. Put detailed implementation plans next to
the subsystem or under `docs/specs/`, link them from a stable work item, and
record their disposition here. Do not copy the entire architecture into every
handoff. No hosted tracker, paid service, or extra model context is required.
