# History and historical plan index

This is a map of previous work, not a second backlog. Current work and priorities
live in the [master plan](MASTER_PLAN.md). New session handoffs live in
[updates/](updates/), one dated file per changing session; corrections are new
entries. The dates below come from the documents, not newly reconstructed commit
timestamps. Much of the September implementation remains uncommitted.

## Milestones

| Date | Record | How to use it now |
|---|---|---|
| 2026-07-03 | Original harness design | Historical product/architecture intent |
| 2026-07-05 | Foundations, CLI backends, Council/MoE, Cockpit, launcher design | Historical phase plans; read current adapter/desktop guides before implementing |
| 2026-07-08 | Local subagents design | Earlier concurrency rationale; current profiles/overrides may supersede details |
| 2026-08 | README benchmark narrative | Historical sealed 12-task result; not rerun or expanded by the September build |
| 2026-09-02 | Harness OS Alpha/Omega plan | Phases 1–13 recorded complete there; preserve original gates and failures |
| 2026-09-03 | Architecture and reuse assessment | Dated counts and architecture snapshot; not today's source inventory |
| 2026-09-04 | Status audit | Pre-upgrade findings; consult later evidence before calling them current defects |
| 2026-09-04 | Desktop build plan | Implemented and locally verified; owner acceptance is separate |
| 2026-09-04 | Integrated harness plan | Implemented and locally verified; limitations are carried into the master register |
| 2026-09-04 / 05 UTC | Upgrade results, desktop evidence | Final locked suite recorded at 2026-09-05T03:27:41.657165Z: 1,697 passed, 35 skipped |
| 2026-09-04 | Full harness report and illustrated version | Dated explanation of implementation, choices and practical limits |
| 2026-09-05 | [Tracking system and handoff](updates/2026-09-05-tracking-system.md) | New contributor front door, master register, rules and validation |
| 2026-09-05 | [Media strategy discussion](updates/2026-09-05-media-strategy.md) | Proposed creation workspace and specialized backends; narration deferred by owner; no media implementation yet |
| 2026-09-05 | [Media orientation and requirements intake](updates/2026-09-05-media-orientation.md) | Context-reset review of project foundations; product questions precede upgrade planning |
| 2026-09-05 | [Media requirements and subscription access](updates/2026-09-05-media-requirements.md) | Owner priorities and no-key/no-additional-spend requirements; documented subscription routes; avatar horizon item |
| 2026-09-05 | [Dream workspace and external-tool preference](updates/2026-09-05-media-workspace.md) | Existing Browser tab preferred; external creative tools acceptable without additional software spending |

| 2026-09-05 | [Media build and validation](updates/2026-09-05-media-build.md) | Create projects, durable media jobs, CPU exports, local workflow adapters and browser handoffs; qualification limits recorded |
| 2026-09-05 | [Avatar feasibility](updates/2026-09-05-avatar-feasibility.md) | Candidate architecture and future latency fixture; no avatar implementation |

| 2026-09-06 | [Unified workspace direction](updates/2026-09-06-unified-workspace-direction.md) | One Dream interface across agents; native capability gaps become integration priorities |

## Reconciled loose ends

The old Harness OS plan ends with **Phase 14**, a small-context schema-budget
proposal. Its old 16K measurements and tool counts should not be read as the
current runtime. The later integrated build measured schema costs of 1,087 / 1,633 /
3,272 / 13,107 estimated tokens at 8K / 16K / 32K / 128K windows, respectively.
That work is carried by DREAM-002; real model latency/quality remains DREAM-015.
These later measurements do not prove every sentence in the old proposal became
the implementation. Use current runtime docs and tests for the actual contract.

The CLI/GUI disconnect has an explicit launch contract: `dream desktop` creates
the combined window; bare `dream` preserves terminal-only use. DREAM-012 carries
extended owner acceptance rather than reopening the already implemented plan.

The earlier request to audit global Claude skills/hooks/plugins was displaced by
the Dream build. Dream's extension usage is not a global Claude usage audit.
DREAM-020 keeps that separate request discoverable without treating it as
completed or authorizing a wipe of the user's other installations.

`Dream-5.1.md` and `Dream-Design-Sys-Prompt.txt` are existing root prompt inputs.
No verified integration or approval is established by their presence. DREAM-019
tracks future assessment. Do not adopt their product claims or instructions as
facts about this repository.

The HTML report is a dated standalone artifact; its current source is the paired
Markdown. Its original renderer was a local temporary script, not a supported
repository build command. Maintain live operator docs for behavior changes;
future republication of that report should include a reproducible renderer.

| 2026-09-06 | [Local model defaults and presets](updates/2026-09-06-model-defaults.md) | Sourced loading recommendations, saved exact-model choices, metadata probe and model-free regressions |

| 2026-09-06 | [GLM loading-policy diagnosis](updates/2026-09-06-glm-residency.md) | Fixed GPU expert caches and opt-in host pinning confirmed; residency fix remains open |

| 2026-09-06 | [GLM server memory correction](updates/2026-09-06-glm-memory-fix.md) | Automatic GPU caches, bounded host pinning and actual allocation reporting; engine rebuilt, hardware reload pending |

| 2026-09-07 | [Exact GLM weight plan and successful live load](updates/2026-09-07-glm-weight-plan.md) | Corrected source/destination precision mismatch; real GLM loaded and answered; server left running |
