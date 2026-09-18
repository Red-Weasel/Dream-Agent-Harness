# DREAM-023 — Media orientation and requirements intake

Recorded: `2026-09-05T11:13:17-05:00`
Work items: `DREAM-023, DREAM-024`
Outcome: `proposed`
Actor: Codex, repository orientation and requirements analysis.

## Request

After resetting context, the owner requested a planning-document review and
questions about image generation, video generation, animation and rendering.
The owner will answer before requesting an upgrade plan. Acceptance for this
session: recover project context, distinguish existing foundations from proposed
media work, and identify product decisions needed for that plan.

## Changes

Reviewed START_HERE, CURRENT, MASTER_PLAN, WORKFLOW, CLAUDE, the decision log,
history, integrated harness upgrade and previous media handoff. Consulted focused
desktop/runtime/architecture documentation and relevant source. Refreshed shared
tracking only. Preserved the existing dirty tree and prior handoffs.

Observed foundations:

- The documented desktop shares one Engine across the real CLI and Studio.
- Library.create accepts files or bytes and records stable identity, content
  hashes, MIME metadata and versions. It reads a source file into memory, so
  reuse for large videos needs evaluation; large-media suitability is unverified.
- animations.jsx provides Stage/Sprite timing, a scrubber and stageSeek controls.
  Preview.screenshot can run JavaScript and capture successive images. These
  inspected paths do not establish a finished video encoder or render job system.
- Inspected export tools cover bundled HTML and PowerPoint. The focused media
  backend search found demonstration FFmpeg capture/extraction and animation
  seeking, consistent with the prior finding that generation integration is new
  work. This was a focused review, not an exhaustive extension/account audit.

The previous Create workspace, shared assets/jobs and specialized backend
approach remains proposed. No new provider, renderer, interface, persistent rule
or implementation design was selected. DREAM-024 narration remains deferred.

## Validation

Observed git status before changes and saved a 371-path content-hash baseline:
`python3 scripts/check_project_tracking.py snapshot --output
/tmp/dream-media-orientation-20260905.json`.

Dream was absent from the project index. Indexed it with codebase-memory-mcp in
fast mode with repository artifact persistence disabled. Used graph searches,
symbol snippets and indexed text search. The index reported excluded directories,
including dream/tools; focused direct reads there supplied the missing context.
No claim of complete graph coverage is made.

Observed: `python3 scripts/check_project_tracking.py check --snapshot
/tmp/dream-media-orientation-20260905.json` passed: three dated records and all
four session-changed paths covered, with prior history preserved.
No runtime tests, native
desktop launch, generation, rendering, model load, paid request or current
provider-documentation research was performed. Historical runtime test results
remain historical evidence. No runtime code changed.

## Unfinished work

Owner requirements are still open:

1. First representative creative tasks and their priority.
2. Fully local, cloud or hybrid generation requirements, private-material limits
   and whether this workstation is the intended local worker.
3. Existing services with usable API access and the acceptable generation budget.
4. Meaning of animation: motion graphics, character animation, 3D or a combination.
5. Prompt-led revisions versus manual controls and timeline/layer editing depth.
6. Reference-image editing, product/character consistency and asset reuse needs.
7. Expected duration, resolution, aspect ratio, export format and acceptable wait.

Pineapple check: usable API access and local generation performance remain
unknown. The owner can establish access; approved compatibility trials would
establish performance. No running worker, model or rendering process needs
cleanup. No side effect requires reconciliation.

## Next steps

Collect the owner's answers, research current backend options against those
requirements, then prepare the DREAM-023 design and staged implementation plan
when requested. Keep narration deferred. Resume from CURRENT and this record;
take a fresh tracking snapshot before subsequent edits. No implementation,
provider selection, owner acceptance or release is recorded by this handoff.

## Files changed

- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/HISTORY.md`
- `docs/project/updates/2026-09-05-media-orientation.md`
