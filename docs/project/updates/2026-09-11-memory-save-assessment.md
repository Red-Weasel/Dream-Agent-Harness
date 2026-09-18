# DREAM-049 — Memory save-path assessment

Recorded: `2026-09-11T21:37:32-05:00`
Work items: `DREAM-049`
Outcome: `implemented`
Actor: Codex lead; read-only assessment and recommendations.

## Request

Owner asks whether explicit session-to-memory requests are useful, why they take
time, and whether memory should be reworked. Assess existing behavior before
recommending changes. This is not authorization to rewrite or delete memories.

## Changes

Documentation only; baseline /tmp/dream-memory-assessment-baseline.json.
No memory files, database, runtime settings or implementation changed.

WorkingMemory.log_turn commits turns to SQLite then appends session JSONL.
Session preservation therefore does not require promoting it to long-term memory.
remember calls upsert_memory, which commits the row, awaits optional embedding,
chunk embedding and automatic links, then returns for Markdown write and index
regeneration. This serializes optional search work before authoritative-file save
and acknowledgment. Source also embeds unchanged content rather than skipping it
based on an exact content/version match. No full vector rebuild per remember was
established; backfill/sync are separate paths.

Engine.consolidate is an additional model pass: reads notes, recalls/deduplicates
facts, considers conflicts and optionally distills a skill. Natural-language
'commit this session' is a model instruction, not evidence a particular API ran.
The most recent inspected owner transcript still ends with the animation handoff;
it does not contain the reported memory request. Its exact slow phase is unknown.

Recommendations, not activated rules: first commit a compact recoverable handoff
and durable facts; index changed content afterward with recoverable job status;
avoid re-embedding unchanged records and regenerating the whole index per note;
show saving/summarizing/indexing phases separately. Preserve source/session and
verification status and avoid promoting model guesses into global facts. Existing
provenance tags do not independently verify a model's assertions.

For this incident, save project artifact locations, owner-confirmed external MP4
playback, remaining native Studio acceptance and the next action. Do not promote
'no GPU' or 'system libraries broken' claims as host facts. Those were unsupported
or contradicted by the actual sandbox diagnosis.

## Validation

Read memory_tools.remember, MemoryStore.upsert_memory/_store_embedding,
longterm.write_markdown and index documentation, WorkingMemory.log_turn, Engine.consolidate,
and runtime/performance docs. Root graph returned Transport closed; focused reads
used. Tracking passed: 63 dated records and all 3 changed documentation paths.
No memory/model call, embedding load, benchmark, test or data mutation was
performed. Findings are source-path observations, not measured latency attribution.

## Unfinished work

Measure an actual save's phases before claiming the dominant cost. Any memory
implementation change requires the existing before/after fixture memory evaluation
and recovery tests. No proposal was implemented or benchmark improvement claimed.

## Next steps

Use short verified project handoffs rather than copying whole transcripts into
long-term facts. If implementing optimization, start with durability and indexing
separation, then incremental work and explicit status; preserve existing memories.

## Files changed

- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-11-memory-save-assessment.md`
