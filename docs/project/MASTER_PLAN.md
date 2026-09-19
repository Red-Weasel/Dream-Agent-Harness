# Dream — master development plan

This is the authoritative work queue. Created 2026-09-05 by reconciling the
existing September build plans and evidence; earlier dates below refer to those
records, not work newly performed during this documentation task.

## Direction and acceptance

The owner’s primary product goal is **one interface for whichever agent the owner
chooses**: open Dream and complete the work there, without tracking a growing
collection of client interfaces and upgrades. Native-client advantages identify
integration gaps for Dream to close. Preserve provider-specific strengths rather
than restricting every agent to a common minimum. Dream owns continuity across
agent changes and makes provider compatibility/setup understandable in one place.
This is the target experience, not a claim of existing universal feature parity.
See [DREAM-027 direction](updates/2026-09-06-unified-workspace-direction.md).

Make Dream a dependable daily harness for frontier and local models: preserve the
CLI, integrate the desktop, measure model/tool/environment behavior, bound side
effects, recover honestly, and keep user control visible. Additional agents,
instructions, hooks, and memory are justified by demonstrated outcomes.

“Competitive” requires repeated task results at stated model, context, latency,
cost, and approval settings. Feature count and a successful connection are not
comparative evidence. No single preset promises equivalent behavior across all
providers. The Council remains a core capability with a separate legal-readiness
acceptance bar.

## Work register

IDs never change or get reused. **Status is authoritative in this table.** Detailed
criteria below and the linked evidence explain it. Priority describes order,
not permission to start an external action or to load a model.

| ID | Work item | Status | Priority | Dependency / evidence |
|---|---|---|---|---|
| DREAM-080 | README screenshot and Ko-fi support link | implemented | owner request | Screenshot of Dream + DeepSeek-V4.1 in the README, Support section and FUNDING.yml; [record](updates/2026-09-18-readme-screenshot-support.md). |
| DREAM-079 | V4.1 native tool-call support | blocked | owner repair request | Engine repair built; 654 reference/protocol checks, 50 upstream encoder tests, 15 prompt/token goldens and 22 Dream checks pass. Existing server remains on old executable; awaiting owner-approved reload and live tool loop. [Handoff](updates/2026-09-16-v41-native-tools.md). |
| DREAM-078 | Dream eclipse branding and immersive chat | implemented | owner visual acceptance | Original-inspired eclipse artwork, logo and circular silhouette icon integrated; bottom composer and artwork-framed messages verified in Chromium and native WebKit. [Evidence](updates/2026-09-15-dream-branding.md). |
| DREAM-077 | Readable model picker and V4.1 loading | blocked | engine qualification | Picker/discovery and RAM preflight repaired;186 focused tests pass. Actual32768-context load succeeded, reply timed out;4096-context comparison hit engine GPU OOM. [Evidence and next action](updates/2026-09-14-v41-loading-preflight.md). |
| DREAM-076 | Two-hour CPU reliability, context, evidence and release qualification | implemented | owner timed request | 2026-09-14:02:57–03:39 UTC, paused for owner reboot, resumed03:47; late Stop/reconnect repair qualification finished05:13. Final5179pass45skip1deselected7warnings52subtests;573hashesstable. Fresh phase gates, clean locked package, native callbacks and actual-App Stop qualified; noGPU/localmodels/publication or owner acceptance. [Handoff](updates/2026-09-14-cpu-hardening-restart-recovery.md). |
| DREAM-075 | Prompt Optimizer beside Chat | implemented | owner request | Six-section editable prompt drafting, selected attachments, optional reasoning guidance, material clarification questions, model-free formatting and explicit use in Chat; research-backed Astra/Fable guidance and isolated model calls. CPU contract/UI checks and non-author reviews passed; live model quality remains untested. |
| DREAM-067 | Evidence-aware read-loop recovery | implemented | owner next-list 1 | Detect unchanged same-target observations despite argument churn; guide a different approach without blocking new evidence, polling or authorized mutations. |
| DREAM-068 | Deliverable verification and polish feedback | implemented | owner next-list 2 | Inspect existing artifact/Studio completion gaps; add bounded structural checks and evidence-specific correction guidance; perceptual quality requires later live use. |
| DREAM-069 | Safe live steering | implemented | owner next-list 3 | Explicit supported HTTP next-round correction delivery with durable receipt; unsupported/native paths remain clearly queued and cancellation stays owned. |
| DREAM-070 | Application-specific computer and Blender workflows | implemented | owner next-list 4 | Use recorded verified failures and recoveries to improve skills/helpers; no GPU/model loads or unmeasured uplift claims. |
| DREAM-071 | Task environment readiness | implemented | owner next-list 5 | Expose actionable workspace/tool/vision/media prerequisites through actual execution boundaries without implicit model loads or host fallback. |
| DREAM-072 | Project handoff and selective memory | implemented | owner qualification next | Model-free editable sourced handoffs, explicit context selection, four memory tools save before optional vectors, stale-vector rejection and truthful shutdown. Final160passed8skipped, fresh independent review, keyword fixture unchanged; live/native acceptance pending. [Handoff](updates/2026-09-14-project-handoffs-durable-memory.md). |
| DREAM-073 | Model and task calibration | implemented | owner next-list 7 | Capability defaults, explicit overrides, artifact/workspace grading and offline matched-task comparison implemented. CPU pass adds actual Engine data controls, bounded recall alternatives and repaired skill routing; 5274 tests pass with independent phase gates. Representative model/task trials and learned optimal settings remain unmeasured. [Handoff](updates/2026-09-14-cpu-outcomes-recovery-recall.md). |
| DREAM-074 | Fresh installation and running-version qualification | implemented | owner next-list 8 | Fresh dependencies where available, running-version visibility and daily-use recovery evidence; no publication implied. |
| DREAM-065 | Reliable continuation and observed run ownership | implemented | owner priority1 | Implemented final-status parsing, durable next-action handoff, opt-in iteration cap, canonical run inspection with lock ownership and CLI/Projects integration. Independent74checks passed. Acceptance: no false running/completed state, no uncertain replay, continuation/recovery tests and independent review. |
| DREAM-066 | Model adaptation and production qualification | implemented | owner priority6 | HTTP effort repair and reusable offline qualification implemented; initial277fixture checks passed. DREAM-076 extends the six groups, rejects malformed reports and qualifies a clean98-dependency install plus Dream; earlier missing cached SDK artifacts are superseded by that install evidence. Actual provider/model quality/native owner acceptance remain separately unverified; no local model loads or benchmarks. |
| DREAM-055 | Skills workspace editor | implemented | owner acceptance next | Full instructions, create/edit private overrides, revision checks and discovery; combined343pass, independent review; [handoff](updates/2026-09-12-projects-skills-workspaces.md). |
| DREAM-056 | Persistent Projects workspace | implemented | owner acceptance next | Saved workspaces/conversations, documents and memory notes, explicit legacy links and idle-only context restoration;343pass, independent UI/lifecycle checks; [handoff](updates/2026-09-12-projects-skills-workspaces.md). |
| DREAM-057 | Session-history read-loop recovery | implemented | owner acceptance next | Bounded current-request retrieval, valid larger pages, explicit round limit and main/delegated missing-vision recovery; 279 combined passes, 68 independent checks; [handoff](updates/2026-09-12-session-recovery-and-workspace-atmosphere.md). |
| DREAM-058 | Dream atmosphere throughout working views | implemented | owner acceptance next | Bundled eclipse/floating-figure art in populated Chat, Projects/Skills banners and Studio chrome; responsive/classic/focus/draft checks and visual inspection; [handoff](updates/2026-09-12-session-recovery-and-workspace-atmosphere.md). |
| DREAM-059 | Working-theme visual polish | implemented | P1 | Actor: Astra + Codex; scope: shared CSS, navigation synchronization and browser checks. Locally implemented; native owner acceptance remains pending. Acceptance: clearer hierarchy, spacing, editors and controls without losing Dream artwork or narrow/classic usability. |
| DREAM-060 | Demonstration-informed computer and Blender skills | implemented | P1 | Actor: Codex + Astra; scope: private staged recording installs, two packaged/routed specialist skills, tests/docs. Model transfer remains unmeasured. Acceptance: accurate available-tool workflows and recorder limitations, reusable grounded guidance, source validation, independent skill exercise; no unmeasured model uplift claim. |
| DREAM-061 | Shared computer observation/action adapter | implemented | live qualification | Post-dispatch observation failure repair implemented: explicit partial evidence, no replay/retarget, null-token guard. Author114tests, independent17probes and isolated native before/after fixture passed. Live controllers stay pinned until normal restart. Existing coordinate strokes, advisory screen settling and prevalidated/cancellation-safe browser keys implemented. 66 control plus53 integration tests passed; independent17 checks. Actual JS Paint stroke/undo verified. Optional paced strokes passed author82+2 and independent36+10 checks with actual Paint deposition. Native client-origin and repeated-digit fixes passed99control tests and actual isolated qualification; independent27native+1browser probes passed. Complete repair source pinned for Opus continuation; owner daily-use/model uplift remain unqualified. Native daily-use/model uplift remain unqualified. |
| DREAM-062 | Structured recording evidence | implemented | qualification next | Astra implemented private manual frame-linked annotations, model reads/draft citations and Controls editor; independently reviewed. Acceptance: explicit action/state/outcome annotations alongside frames, bounded validated private persistence, no automatic OS keylogging. |
| DREAM-063 | Transferable skill exercises and Claude check | implemented | qualification next | Astra implemented skill exercises; host Claude simulator baseline8/8, assisted8/8 (no uplift); local-model tests deferred. Acceptance: useful held-out exercises and bounded authorized Claude comparison if available; local models deferred, actual evidence distinguished from review. |
| DREAM-064 | Navigation, density and catalog usability | implemented | qualification next | Astra implemented one visible navigation, persistent density/banners, sorting/filtering/keyboard access; native acceptance pending. Acceptance: one primary navigation, persistent density/artwork controls, sorting/filtering/keyboard catalog use, draft preservation, responsive checks. |
| DREAM-001 | Integrated CLI + desktop browser | verified | baseline | Desktop evidence |
| DREAM-002 | Provider profiles and context controls | verified | baseline | Runtime controls |
| DREAM-003 | Auto execution, hooks and process recovery | verified | baseline | Execution |
| DREAM-004 | Durable autonomous work and independent review | verified | baseline | Durable runs |
| DREAM-005 | Extension catalog, usage and portable tools | verified | baseline | Extensions |
| DREAM-006 | Council evidence and advisor isolation | verified | baseline | Upgrade evidence |
| DREAM-007 | Demonstration-to-skill workflow | verified | baseline | Extensions; fixture verification only |
| DREAM-008 | illuminati-handshake capability lab | verified | baseline | [Portable skill](../../skills/illuminati-handshake/SKILL.md) |
| DREAM-009 | Locked installation, fixtures and backup tooling | verified | baseline | Upgrade evidence |
| DREAM-010 | Harness explanation and operator report | verified | baseline | September report |
| DREAM-011 | Persistent onboarding, planning and handoff system | verified | baseline | [Tracking evidence and handoff](updates/2026-09-05-tracking-system.md) |
| DREAM-012 | Owner desktop acceptance and extended recovery trial | planned | next | DREAM-001, DREAM-003, DREAM-009 |
| DREAM-013 | Gemini live provider verification | blocked | next | Eligible account access; prior diagnostic retained |
| DREAM-014 | Comparative task evaluation and regression baseline | planned | next | Both real portrait baselines finished; first blind review found neither met full likeness. After owner-reported reset, requested/provider-confirmed Opus5 completed second blind review and resumed Fable saved scene with repaired controls. Astra baseline stopped for confirmed tool defects; original Opus continuation later exited without final result. Recovered preserved open scene under transient user services with verified Opus5 calls and a new checkpoint; exit cause unknown. Owner requested Astra restart with private action/frame/decision-summary recording; Astra GUI actions/new checkpoint verified on isolated:95; timestamped calls, pixels and decision notes recorded. Separate read-only portrait audit completed with counterexamples; first300second screen recording decoded and copied for owner review. Opus later delivered60s1080pMP4 decoded by lead; visual quality remains rough. Astra stopped at launch checkpoint; root supervisiongap acknowledged and continuation sent. [Recovery](updates/2026-09-12-rocket-worker-recovery.md). Lead owns trial-informed computer-use/Blender references, evidence ledger, selection.py and specialist routing tests; acceptance: verified causes separated from candidate techniques, independent review, frozen unseen transfer task, no uplift claim before measurement. Owner approved a single launch-shot realism pass from Astra checkpoint21: CPU renderer qualification, materials/light/environment/exhaust still review, then a short motion test; preserve the full60second blockout. First-shot v06 study delivered: CPU Cycles1080p still, verified2s/60frame motion and editable scene; original film preserved. Fresh review finds improvement but rigid plume, sparse environment and late camera cropping remain. No further render scheduled; broader realism/transfer evaluation planned. [Appearance pass](updates/2026-09-13-rocket-launch-appearance.md). |
| DREAM-015 | Local model performance and compatibility matrix | planned | next | DREAM-002; GPU preflight and model authorization |
| DREAM-016 | Stronger trust storage and extension/preview isolation | proposed | horizon | Threat model and compatibility design first |
| DREAM-017 | Council legal-readiness evaluation | proposed | horizon | DREAM-006, vetted sources and expert review |
| DREAM-018 | Reviewable source delivery and release readiness | planned | next | DREAM-011, DREAM-012; owner release decision |
| DREAM-019 | Review prompt drafts and instruction budgets | proposed | horizon | Source/authenticity review and DREAM-014/015 |
| DREAM-020 | Global Claude configuration cleanup audit | deferred | separate | Earlier request displaced by Dream build; fresh scope before modifications |
| DREAM-021 | General task dependency scheduling | proposed | horizon | Only if actual workflows exceed current durable task/loop needs |
| DREAM-022 | Memory, tasks, Library and compaction continuity | verified | baseline | September report; historical harness plan |
| DREAM-023 | Media creation: images, video, animation and rendering | implemented | qualification next | Owner authorized continuous planning/execution; implementation plan; no API keys or additional spend |
| DREAM-024 | Narrated screen learning and synchronized audio | deferred | horizon | Owner deferred on September 5 while improving separate voice-control software |
| DREAM-025 | Real-time photorealistic conversational avatar | proposed | horizon | Owner's long-term ambition; media foundations under DREAM-023; separate feasibility and latency evaluation before implementation |
| DREAM-026 | MachX GLM-5.3 server and advanced local model controls | verified | broader runtime qualification | [Loading qualification](updates/2026-09-05-machx-model-tuning.md); [reasoning and memory follow-up](updates/2026-09-05-machx-reasoning-memory.md); full-depth performance unmeasured |
| DREAM-027 | One Dream interface with native agent capability access | proposed | product direction | Owner clarification; preserve agent strengths, project continuity and managed compatibility; gap assessment next |
| DREAM-028 | Desktop usability and tool reliability polish | implemented | current | Graphical startup, visible chat/tools/approvals, skills and simpler Create verified; large-model latency and owner daily-use acceptance remain open |
| DREAM-029 | Curated skills, guided execution and file-first desktop | implemented | current | Eight compact workflows, file attachments/readers, Studio and failure recovery verified; live weaker-model quality/latency blocked by occupied GPUs |
| DREAM-030 | Respect engine host/GPU streaming during desktop loading | verified | current | Engine streaming capability preserved; actual DeepSeek resource preflight passes; model-free regression and native picker checks passed |
| DREAM-031 | Model-free performance and quality improvements | implemented | current | Turn/cache diagnostics, explicit performance modes, foreground scheduling, document reuse and offline quality evaluation; all live inference deferred by owner |
| DREAM-032 | Coordinated inference and guided project workflows | implemented | current | Shared endpoint ownership, guided creation with output checks, project context/recovery, capability contracts and CPU qualification |

| DREAM-033 | Session audit and verbose Studio feed | implemented | current | Session audit, Detailed feed, attributed worker activity, read-only settings and media routing; CPU verified, follow-up findings documented |
| DREAM-034 | Restore Council selection and main-agent handoff | implemented | current request | Main/advisor model and effort controls; 272 CPU/browser tests passed; native/live and owner acceptance pending |
| DREAM-035 | Iterative harness hardening and model adaptation | implemented | live quality qualification next | Accepted1–35 plus CLI integration repair. Final combined4097pass/35skip/7warn;13 reviewed hashes unchanged. Prior full4fail/2errors repaired under same judge; all earlier gate failures preserved. Learned/native/live/install/model quality and owner acceptance remain open. |

| DREAM-036 | Model-neutral Dream tagline | implemented | current request | Your models. Your workspace. Current banner, README, metadata and introductory wording updated; rendered banner, syntax, metadata and literal checks passed. |

| DREAM-037 | Publish public-safe harness and rewritten README to GitHub | released | Remote verified; history cleanup separate | Main07bec6a,543 audited files; README/About verified;4478passed43skipped7warnings; historical exposure tracked under DREAM-054. |

| DREAM-038 | Chat feed scrolling and permission-mode shortcut | implemented | owner verification next | Automatic follow/manual history/same-frame resume; desktop Shift+Tab and confirmed mode button. Final55pass/2warnings, fresh independent review; backend restart/native owner acceptance pending. |

| DREAM-039 | Blender/Cycles capability detection | implemented | owner qualification next | Passive status and explicit no-render probe;62fixturepass/2warnings; independent timeout repair. No real Blender/render qualification; descendant containment not claimed. |

| DREAM-040 | Restore usable vision tool and truthful screenshot guidance | implemented | owner runtime verification next | Declared/configured image transport, truthful disabled/deferred guidance and model-switch history repair;235focusedpass, independent90pass. Live endpoint images unverified; no owner restart. |

| DREAM-041 | Observe model/tool friction in live sessions | implemented | scoped fixes next | Read-only213-record DeepSeek audit,3saved renders,2fresh reviewers; loop-guard/edit-guidance defects, script recoveries, scope uncertainty and controlled comparison plan. No model ranking or unattended monitor. |

| DREAM-042 | Runtime and tool recovery fixes | implemented | owner live qualification | Approval-aware budget and remaining-time UI; mutation-aware repeat recovery, exact-edit guidance and Studio presentation instructions. Final 390 tests passed; fresh review 252 passed and two budget defects repaired. No owner restart or publication. |

| DREAM-043 | Auto Bash approval recovery | implemented | owner ongoing use | Approved profile installed/loaded; all 11 real containment checks passed. Open session refreshed and available=true verified without restart. Source repair previously passed 150 tests. |

| DREAM-044 | Sandbox runtime links and approval wording | implemented | restart for source changes | Trusted read-only alternatives mount restores FFmpeg/ffprobe/which; clearer approval labels/reasons.141 root checks plus8 final link checks passed; independent review passed. |

| DREAM-045 | Auto routine-work approvals | implemented | restart; owner live acceptance remains | Contained native Auto permits bounded image-output cleanup. Broader destructive/host/publication/system boundaries remain.184 root tests;127 independent tests and10 adversarial probes. Snapshot scan and naming convention are not artifact provenance or atomic limits. |
| DREAM-046 | Optional runtime limits and shell failure reporting | implemented | new process; owner live acceptance remains | No default time cutoff; explicit finite limits preserved; Unlimited status; nonzero Bash exits become structured errors. Root341 passed with2 warnings; independent89 budget/30 shell passed. Corrected media playback remains unverified. |
| DREAM-047 | Studio media playback | implemented | restart; native WebKit acceptance remains | Bounded scoped media inlining and data/blob media CSP; errors visible.63 tests passed; actual owner MP4 played in temporary Studio Chromium, files unchanged. |
| DREAM-048 | Live Studio version diagnosis | implemented | full application restart; verify native playback/vision | Host PID started14:10 CDT; live session still reports7200s and unreported vision. No new source defect established; Council review/polish usage explained. |
| DREAM-049 | Memory save-path assessment | implemented | recommendations only | Session logging already persists; remember awaits optional embedding/linking before Markdown/index write. Propose durable save before indexing, incremental updates, phase timings and verified compact memories; exact delay unmeasured. |
| DREAM-050 | Council model catalog and active work | implemented | owner request; existing lifecycle handoff | Friendly model/effort selection; members perform tool-enabled turns in the current workspace; sequential team relay restores main and stops on failures. |
| DREAM-051 | Bash always approval and Blender status clarification | implemented | owner request | Session-scoped exact-command grants distinguish sandbox/host; preserve danger and scope gates; clarify Cycles/GPU evidence without replacing packages. |
| DREAM-052 | Dream visual design and workspace experience | planned | Remaining backend follow-through; visual implementation delivered | Design plan; branded native/web workspace implemented and fixture-verified; durable-first memory, concurrent editing and arbitrary output lineage remain. |

| DREAM-053 | Local generation timeout and failure diagnostics | implemented | Owner next-session qualification | [Failure repair](updates/2026-09-12-local-stream-failure.md); local read-timeout precedence, durable failure metadata and fixture storage isolation; 118 checks passed. Exact stopped request reconciled after host verification, no replay. |

| DREAM-054 | Remove historical personal/runtime content from public main ancestry | planned | Explicit history-rewrite approval | Prepared parentlessf086641 with identical reviewed tree; private history backup preserved. Main currently07bec6a; exact lease required, cached/forked/downloaded copies separate. |

Statuses: `proposed` (idea), `planned` (scoped), `active` (being worked), `blocked`
(named external dependency), `deferred` (deliberately parked), `implemented`
(code present), `verified` (stated checks observed), `accepted` (owner explicitly
accepts the stated scope), `released` (identified delivery observed), `cancelled`.
A verified item can still have limitations and follow-up work. **No item here is
owner-accepted or released merely because it passed engineering checks.**

## Recent verified milestones — DREAM-011 / DREAM-023

Actor: Codex. Scope: onboarding and tracking documentation, historical plan
disposition, lightweight local/CI check and PR guidance. Existing runtime and
private state are preserved.

Result: [dated handoff](updates/2026-09-05-tracking-system.md) records 12 passing
focused tests and a successful snapshot check covering 28 session-changed paths.
DREAM-023 media foundations are now implemented and locally verified. See the
[media build](updates/2026-09-05-media-build.md): final locked suite 1,766 passed,
35 skipped; real media exports and package/tracking checks passed. No worker is
active; account/model qualification and DREAM-012 owner acceptance remain.
The next contributor starts from the user's current request and `CURRENT.md`.

Acceptance criteria:

- A new contributor finds current state, master plan, architecture, evidence,
  blockers and next action from the root README or AGENTS/CLAUDE entry.
- One work register distinguishes plans, implementation, verification, acceptance
  and release. Prior plans are labeled historical and linked rather than erased.
- Each changing session leaves a timestamped, attributed record of scope,
  changed paths, actual checks, unfinished work and next steps.
- Local checks can compare against a pre-session snapshot without claiming
  pre-existing dirty work. CI checks committed diffs when a comparison base exists.
- Missing records, missing current-state updates, undocumented changed paths,
  malformed timestamps, unknown work IDs and rewritten history are detected by
  focused verification. Limits of automated documentation checks are explicit.

## Baseline criteria and remaining boundaries

| Items | Implemented acceptance | Remaining acceptance belongs to |
|---|---|---|
| 001 | One native window, real CLI, shared Engine, visible Studio/browser delivery and reconnect states | 012: extended owner workflow on the actual desktop |
| 002 | Profile overrides, bounded Dream-visible HTTP context, searchable deferred tools, bounded delegation | 014/015: measured task quality and throughput by model |
| 003 | Contained routine shell work, meaningful consequential approval, bounded hooks, owned child cleanup | 016: broader host/plugin/preview trust boundaries |
| 004 | Durable contract/ledger/resume; interrupted actions reconciled; review cannot silently pass when absent | 012/014: longer real-world recovery and outcome trials |
| 005 | Consistent toggles and observed usage; changed Python entry source needs renewed approval | 016: protected trust store and transitive dependency isolation |
| 006 | Attributed independent advice, dissent and bounded source checks retained | 013/017: eligible Gemini connectivity and legal validity evaluation |
| 007 | Visible bounded capture design, imported/demo fixtures, reviewable skill drafts | 012/015: consenting real capture and model-assisted analysis trial |
| 008 | Research/reuse guidance, workspace labs, CPU RL starter with holdout, explicit promotion | Domain-specific environment/reward/held-out result for each new capability |
| 009 | Locked runtime and packaged skills; model-free regressions; fixture backup/restore | 018: clean delivery, real recovery drill, remote CI, approved release |
| 010 | Illustrated and Markdown system report with evidence and limitations | Live docs change with behavior; this report stays a dated snapshot |
| 022 | Memory-file synchronization, persistent tasks, recoverable compaction notes and versioned artifacts | Real private-store recovery under 018; general DAG only under 021 |

## Next milestone criteria

**DREAM-012 — daily-work acceptance.** Record platform, launch command, provider,
profile and test task. Exercise terminal + browser together, local HTML delivery,
questions/approvals, interrupt, reconnect, child exit, and one failed-operation
recovery. Record screen capture only with the user's applicable consent. Attach
redacted evidence and unresolved defects; ask for owner acceptance of the concrete
result when needed. An owner trial is not simulated by automated fixtures.

**DREAM-013 — Gemini.** Once the account is eligible, rerun the bounded Council
fixture and parent-tool bridge checks. Verify both successful output and owned
cleanup. Record provider/model/version and error/success; do not turn account
failure into an unsupported security exception.

**DREAM-014 — evaluation.** Before running, freeze task set, environments,
acceptance graders and a sealed holdout. Compare the same model and permissions
where possible; log deviations. Report outcome success, latency, token/cost
availability, approval burden, retries and recovery. Keep connectivity,
deterministic regression, and comparative quality reports distinct. Publish no
ranking without reproducible supporting runs.

**DREAM-015 — local matrix.** Start with user-approved local models and GPU
preflight. Record actual server context, profile, tool/vision compatibility,
prompt processing, tokens/s, cancellation and memory pressure. Adjust presets
from measured task outcomes. Do not assume model-name text proves capabilities
or load several large models concurrently by default.

**DREAM-018 — delivery.** Inventory the existing dirty source set and legacy
tracked runtime files without deleting or staging broadly. Produce a reviewable
source-only change set, locked clean-environment results, wheel audit and a
documented restore drill. Confirm required tracking/reliability CI on the actual
revision. Record owner acceptance and approved commit/release identifiers only
after those events happen. Remote branch protection and publication are separate
owner-controlled actions.

## Horizon and intake

**023 — implemented media foundations:** the owner authorized continuous
execution of the media plan.
Dream now has editable media projects, immutable assets/revisions, durable jobs,
Create controls, CLI/model tools, CPU animation/render/export, curated local
ComfyUI workflows, subscription browser handoffs and Blender launch/import.
No API keys or additional spending. Saved browser sessions are explicitly opt-in.
See media guide and [build evidence](updates/2026-09-05-media-build.md).
Local generation still needs a running backend, checkpoints and current resource
qualification; account sign-in/quotas and child-Codex media retrieval are not
verified by browser handoff tests. This is the first delivered media foundation,
not a claim that all animation/editing or live-avatar ambitions are implemented.

**024 — deliberately deferred:** optional microphone/system-audio capture,
timestamped transcription and alignment with demonstration frames. The owner is
improving separate voice-control software. Do not include narration in DREAM-023
or start capture/transcription work unless the owner resumes it.

**025 — long-term ambition:** an agent-driven photorealistic avatar that can
converse with the owner in real time. Separate acceptance from offline video
generation: interactive latency, sustained animation, synchronized speech,
interruptions, identity consistency and shared resource use need measurement on
stated hardware. No real-time feasibility or universal hardware support is
established. Capture the goal without resuming DREAM-024 or authorizing voice,
model-loading or avatar implementation work in the current media build. See the
[feasibility record](updates/2026-09-05-avatar-feasibility.md).

**016:** protect trust decisions and durable audit state from workspace writers;
design extension/MCP and preview isolation with explicit compatibility tradeoffs.
**017:** jurisdiction/as-of/source provenance, current legal authority and
citator integration, adversarial citation evaluation, expert review; no automatic
filing. **019:** assess root prompt drafts as input material, remove unsupported
identity/tool claims in any future approved adaptation, measure instruction cost
and quality, keep provider-neutral core plus small verified overrides.
**020:** global Claude skills/hooks/plugins audit is separate from Dream extension
usage; first inventory provenance and observed usage, then propose reversible
cleanup. **021:** add dependency scheduling only after documenting a concrete
workflow the current task store and durable loop cannot handle.

New ideas receive the next unused ID, a clear user problem, acceptance criteria,
dependencies and a status. Do not turn the horizon into an unattended work order.
Changes in priority or scope need a dated update; acceptance/release need explicit
evidence of the owner decision or delivery. Detailed subplans must link back here.

## DREAM-026 acceptance

Actor: Codex with supervised workers. Connect existing GLM-5.3 streaming runtime to MachX server; add advanced tuning after model/GPU/context selection in Dream; validate and propagate supported sampling, threading, batching and overflow controls; reject unsupported features honestly. Preserve prior dirty work. Verify focused tests, Dream regression, and local GPU checks where available. Baseline: `/tmp/dream-machx-20260905-baseline.json`. Lead owns project tracking; Dream worker owns launcher/settings/backend tests and runtime-controls documentation.

Observed DREAM-026 results: capability-gated editor and request propagation; GLM context-200000 load via Dream and repeated short generation; 1804 passed, 35 skipped in the full Dream suite. See the linked build record for scope, initial intermittent failure and unperformed full-depth checks. Owner acceptance and release are separate.

DREAM-026 follow-up: reasoning choices/defaults now match implemented model templates and reach the actual prompt. GLM low/high/max matches the vendor GGUF; thinking off is a labelled local convention. Live process audit confirmed all six shards mapped, with file-cache residency explaining the lower headline RAM display. See the dated follow-up for validation and the existing server reload requirement.

## DREAM-027 acceptance — Unified agent workspace

Owner direction: selecting an agent should not require adopting another workspace,
repeating the project brief or personally coordinating each integration’s updates.
Existing local/subscription and no-additional-spend requirements remain in force.

Next assessment should map representative coding, research, review and media tasks
across native clients and Dream, recording actual access, missing capabilities,
authentication, artifacts, context continuity and interruption/resume behavior.
Use those gaps to choose implementations that preserve native strengths. Native
sessions hosted within Dream are an option to assess, not a selected replacement
architecture or a promise that every provider supports embedding.

Proposed acceptance outcomes:

- Select a working signed-in/local agent and complete the task from Dream.
- Change agents while preserving explicit project context, files and task state;
  do not imply provider-private histories transfer automatically.
- Access provider-specific features through appropriate controls, alongside shared
  Dream tools. Show actual unsupported features and actionable recovery.
- Present sign-in, compatibility/version health and update/recovery in Dream;
  assess changes before replacing working integrations. This entry does not
  authorize unattended global upgrades or configuration changes.
- Validate representative workflows, including cross-agent review and media return,
  with observed outcomes. Model-name switching alone does not meet this goal.

The requirements are recorded; capability inventory and implementation breakdown
remain next work. No universal parity or new runtime behavior is claimed here.

DREAM-026 defaults follow-up (2026-09-06): owner approved model-aware recommendations and exact-model saved loading choices. Enter accepts a sourced summary; Advanced/reset remain available; successful loads save atomically with conflict and model-identity checks. GGUF context is an upper bound; initial context remains a labelled conservative host-aware budget, not a proven maximum fit. See [defaults handoff](updates/2026-09-06-model-defaults.md). No model loads or provider configuration changes were performed.

DREAM-026 residency correction (2026-09-06): the owner reports insufficient RAM/VRAM
residency after the defaults change. Current engine source and startup log confirm
fixed 10 GiB per-stage expert caches with host pinning opt-in and absent from the
live process environment. Earlier mapped-shard/file-cache evidence is historical
and does not establish the full residency the owner expects. See
[loading-policy diagnosis](updates/2026-09-06-glm-residency.md). Runtime correction
and allocation/performance qualification remain open; no reload was performed.

DREAM-026 server-memory correction is implemented and rebuilt after explicit owner
authorization to modify MachX. Automatic per-GPU expert caches, bounded default-on
host pinning, strict overrides and actual allocation reporting replace the inherited
10 GiB/mmap server defaults. Five host CTest targets and 120 Dream focused tests
passed; actual GLM metadata confirms the policy. No model reload, new allocation
measurement or owner acceptance occurred. See [memory fix](updates/2026-09-06-glm-memory-fix.md).

DREAM-026 September 7 correction: exact loader projection replaces the duplicate
weight estimate that caused the owner's next load to fail. Six host tests passed;
the actual GLM loaded at 2 GPUs / ctx 32768 and answered a bounded prompt correctly.
It remains running on port 11435. Allocation evidence and partial-residency limits
are in [weight-plan handoff](updates/2026-09-07-glm-weight-plan.md). This verifies
the startup regression, not full residency, sustained performance or owner acceptance.


## DREAM-028 acceptance — Desktop usability and reliability

Actor: Codex, lead integration; supervised contributors own disjoint backend and media paths.
Owner reports widespread failures and confusing settings. The authorized pass must
make startup/model selection understandable, stream readable answers in the desktop,
show tool calls/results/errors, support installed skills, and simplify animation
creation with safe defaults and useful recovery. Preserve existing dirty work.

Engineering acceptance: focused regression tests for reproduced defects; browser
and native desktop checks of chat, creation, errors and reconnect; bounded live
local-model chat/tool/skill check when the existing server is available. Model-free
fixtures alone do not establish live model reliability. No spend or publication.
Baseline: `/tmp/dream-polish-20260907-baseline.json`.

DREAM-028 engineering result: the native graphical picker loaded the six-shard GLM,
then real chat, file/shell tools and installed skill opening completed successfully.
Chat refresh retained results without rerunning tools. A real 15-second CPU animation
exported successfully. The large model needed about 89 seconds to load, 119 seconds
for a short reply and 478 seconds for the four-tool task; the requested ease of use
is improved, but ChatGPT-like response speed and broad daily-use acceptance are not
established. See the [desktop polish handoff](updates/2026-09-07-desktop-polish.md).

## DREAM-029 acceptance — Optimized harness and skills

Actor: Codex lead with disjoint skill, file-handling and review contributors.
Owner authorizes pruning wasteful Dream skills and creating a compact practical
set that guides weaker models through tasks. Improve file support, execution
reliability and desktop discoverability while preserving provider capabilities.

Acceptance: audited bundled skill set with justified removals/consolidation;
small default context and deterministic task-relevant guidance; explicit skill
selection works; representative document/data/image files reach the agent through
usable attachment and reading paths; clear Studio navigation; truthful failures,
permissions and cancellation preserved; regression, browser, packaging and
bounded model checks where available. Broad model-quality and production claims
require evidence and are not established by fixtures alone. No publication or spend.
Baseline: `/tmp/dream-harness-20260909-baseline.json`.

DREAM-029 engineering result: eight default workflows, bounded guidance and file
intake/readers, explicit Studio navigation and truthful incomplete results are
implemented. Final locked suite: 1,996 passed / 35 skipped / 7 warnings; native
Studio and package relocation passed. See the [dated handoff](updates/2026-09-09-harness-quality.md).
Live weaker-model comparison remains unavailable while GPU preflight refuses
startup; no universal quality, latency, owner acceptance or release claim.

## DREAM-030 acceptance — Streaming model loading

Use the engine-advertised memory planner rather than treating all model bytes as
VRAM residency. DeepSeek streaming must pass the weight-size check when host/GPU
resources are available. Preserve occupied, hot, unknown and insufficient resource
checks, including existing-server refusal. Explain RAM/GPU split in the picker.
See [verification](updates/2026-09-09-streaming-load.md). No actual model load or
400,000-token allocation was performed for this fix.

## DREAM-031 acceptance — Performance and quality without GPU use

Implement the approved performance recommendations using deterministic fixture
verification. Expose honest turn timings/cache evidence; reduce redundant schema
and document work; prioritize foreground requests over optional filing; explicit
Quick/Balanced/Thorough choices preserve model and permissions; stronger skill
examples and offline evaluation cases with verifiable outputs. Preserve supported
provider controls and error/cancellation behavior. No model loads, real inference,
GPU tools, engine edits or live benchmarks in this pass. Measured model quality
and speed comparisons remain explicitly deferred.

## DREAM-032 acceptance — Dependable project tasks

Implement the owner-approved next upgrade pass without GPU probes, live inference,
model loading, engine edits or real benchmarks. Competing Dream clients coordinate
local loads/requests; uncertainty/cancellation is visible and never causes blind
replay. Guided Studio tasks provide validated inputs, explicit execution, durable
status, inspectable output checks and recovery. Project-wide CPU search and pinned
context remain workspace-bound and survive restart. Adapter capabilities expose
known/unknown support truthfully. Expand fault/replay checks and provide redacted
CPU diagnostics/release checks. Preserve model choice, skills, permissions, existing
artifacts and dirty source. Real-model/server and owner release acceptance deferred.

DREAM-032 engineering result: guided tasks, project context/recovery, coordinated
local requests/launches and explicit capability/diagnostic contracts are implemented.
Final CPU regression: 472 passed, two deprecation warnings; offline cases 5/5.
See the [handoff](updates/2026-09-10-dependable-project-tasks.md). Live model/native
acceptance and real wheel qualification remain deferred; no production release claim.

## DREAM-033 acceptance — Current session audit and activity feed

Review the owner's active DeepSeek session from local logs and saved launch metadata
without inference calls, hardware probes, model loads or restarting the session.
Report effective settings with evidence, trace missing see/tool/skill capability
gaps, and implement the requested more verbose Studio feed. Show only reported
reasoning and actual tool activity, preserve Compact choice and existing streaming,
permissions and history. Verify with CPU fixtures; document audit findings and limits.

DREAM-033 engineering result: read-only session audit, Detailed/Compact feed,
attributed compatible-HTTP worker events/history, selected generation settings and
media word-form routing are implemented. CPU fixtures passed; see the
[handoff](updates/2026-09-10-session-review-feed.md). Vision transport/exposure,
screenshot schemas, sandbox prerequisites, verifier cost and current-request
priority remain audit follow-ups. Active session/model untouched; no owner or
production acceptance claimed.

## DREAM-034 acceptance — Council selection and session continuity

Owner requested selecting one main orchestrator and bringing other models into
Dream as council members. Approved scope: visible startup and in-session Council
controls; explicit main provider/model; advisors that can be enabled/disabled;
individual consultation and full council actions; attributed answers/failures;
main-provider handoff between turns retaining Dream-visible history and workspace;
supported effort selection for the main and each advisor, added at owner request.
Provider-private state is not assumed portable. Existing read-only advisor
isolation remains; selecting an advisor does not load local weights or invoke it.

Implemented by Codex lead with disjoint core, startup and Studio contributors and
independent review. Same-provider main/advisor selection is retained. Authenticated
controls serialize handoffs on the App lifecycle task, preserve bounded visible
context, and report failed recovery explicitly. Effort reaches supported native
adapters; unsupported/unreported values are not offered as available.
Observed: 272 combined CPU/browser fixtures passed. Native GTK/WebKit interaction,
live provider authentication/inference and owner acceptance remain unperformed.
No model/GPU operation or owner process was changed. New Python controls require
a new Dream application. See the [build handoff](updates/2026-09-10-council-controls.md)
and the earlier [investigation](updates/2026-09-10-council-restoration.md).

## DREAM-035 acceptance — Iterative harness hardening and model adaptation

Owner requests continuous planning, research, implementation, reflection and
fresh-context independent judging toward a capable production-grade harness.
Use varied reviewer criteria across iterations, primary research and external
model feedback where available; otherwise provide a source-only Claude/Grok
feedback packet. Defer benchmarks and model/GPU work until specific concepts
require them and applicable authorization is established. Preserve dirty work,
owner processes, explicit settings, permissions and provider-native capabilities.

Each implementation phase defines concrete pass criteria before editing, verifies
a reproduced gap with focused CPU fixtures, and receives independent pass/fail
review. A failed gate returns to the same judge after correction; a passed phase
gets a fresh judge for the next phase. Research hypotheses, implementation,
fixture evidence, live qualification and owner acceptance remain distinct.
Model adaptation must use attributable capability evidence, handle unknown or
conflicting reports honestly, respect user overrides and invalidate stale state
when models change. Production readiness also requires recovery/security tests,
clean installation/package evidence and eventual representative live qualification.
Perfection or world superiority is an aspiration, not a verifiable completion
claim or permission to remove existing constraints.

Actor: Codex lead. Discovery contributors are read-only research and reliability
audit agents. Lead owns tracking, the external feedback packet, and bounded
implementation after its exact phase criteria/file scope are recorded. Baseline:
`/tmp/dream-harness-loop-20260910-baseline.json`.

DREAM-035 checkpoint 2026-09-10: phases1/2 passed independent gates;225 focused
CPU regressions passed after phase3 withdrawal. Phase3 hit the Alpha Omega
three-failure circuit breaker, including a confirmed HTTP cancellation regression
in its final candidate. No disagreement with the judge. Candidate was archived
and withdrawn; original cleanup bug remains. Owner checkpoint review precedes a
resumed adapter-owned interruption correction. Phases4/5 remain planned. See
[hardening handoff](updates/2026-09-10-harness-hardening-loop.md).


DREAM-035 resumed checkpoint 2026-09-10: phase3 accepted on same-judge attempt7.
All seven prior counterexamples and179 combined CPU regressions passed; preceding
failed gates remain in the resumed handoff. Lead broader retained-phase gate passed417 cases before phase4 package-asset
implementation. No live/production acceptance.


### DREAM-035 open findings — September 11 recovery cycle

These are reproduced engineering findings, not owner runtime incidents. Current
source remains governed by the shared adaptation plan and dated recovery handoff.
Phases1–17 and2,978 passing tests do not establish these uncovered contracts.

| Finding | Evidence | Next action |
|---|---|---|
| CLI EOF without terminal result can become durable done | Actual backend/Engine-wrapper fake process and independent actual Loop.run; errors block review | Phase18 integrated after NEW Astra PASS99 tests/264 assertions; accepted-source full3030 passed |
| Latest user intent, small-model admission and later advisor dissent can be lost during Council handoff | Seven actual Engine/fake-HTTP scenarios; short and roomy controls | Phase19 integrated after resumed same-judge PASS299 selected/65 independent; main full3120 passed; required context and exact commit receipts preserved |
| CLI terminal result followed by nonzero process exit still appears successful | Root six-case fake-process audit; fresh source audit /tmp/dream035-cli-exit-contract-audit.md confirms early result also freezes timing | Phase23 integrated after SAME round3 PASS157 selected/54 independent; both priorFAILs fixed with unchanged probes; main full3643 passed |
| Existing Grok incomplete subtype may still advance Loop/workflow on exit zero | Fresh CLI audit inspected MaxTurns mapping and consumers; no new execution by audit | Phase24 integrated after NEW PASS643 selected/96 independent;17 model-loading skips,0 warnings; main full3643 passed |
| /model retains previous exact-model runtime profile | Constructed Engine/HTTP backend switches A to B but uses A32768/4096 instead of saved B4096/512; no provider/store opened | Phase25 integrated after NEW PASS499 selected/43 independent; preserve native session/effort, apply dynamic settings or refuse before switching; combined main full3874 passed |
| SDK Council accepts missing/incomplete terminal evidence as ordinary advice | Root actual Council with fake real SDK messages: missing terminal, MaxTurns subtype, max_turns reason return bare text; success/error controls; author actual App/Engine counterfactual confirms3 failures/3 controls | Phase26 integrated after SAME round3 PASS252 selected/169 independent; both prior failure sets pass unchanged; two fixture protocols preserve existing assertions |
| Main SDK missing/incomplete terminal permits durable DONE and known incomplete reason clears context | Actual installed response boundary + Engine/Loop finite probes; two early-stop controls additionally finalize nested response in another task | Phase27 integrated after SAME round2 PASS493 selected/108 independent; current cancellation and historical context controls preserved; combined full3874 passed |
| SDK evaluator accepts missing/incomplete terminal PASS | Actual SDKReviewBackend/collect_review and Loop._evaluate with two inspected unchanged synthetic evidence files | Phase28 integrated after SAME round3 PASS344 selected/226 independent; allfive earlier failing cases pass unchanged; combined verification with29 pending |
| SDK cancellation count promotes a retained OLD cancellation after current cancellation is consumed | Fresh corrected audit8fail32pass; actual direct/public Council and main read/close,16 CURRENT identity controls pass,40 same-task closures | Phase29 integrated after NEW PASS all6 criteria,449 selected/40 unchanged audit/7 new independent; exact3 paths preserve28; combined main3991 passed/35 skipped/7 warnings; all27 hashes unchanged |
| Reconciliation note omitted at contract restart or lost after committed resume plus callback failure | Real Loop.run with disposable ledger and fake workers | Phase22 integrated after NEW PASS407 selected/49 independent; canonical legacy notes survive and reach attributed worker/reviewer prompts; main full3643 passed |
| Failed ledger fsync followed by automatic error recording duplicates sequence numbers | One injected disposable-ledger fsync failure yields1,2,3,3,4 | Phase20 integrated after NEW Astra PASS91 tests/43 independent cases; accepted-source full3030 passed |
| Unsupported persisted phase can bypass reconciliation | Four malformed values versus exact running/review controls; numeric and Unicode parser failures caught by gate | Phase21 integrated after round3 PASS340 selected/131 independent; validation before claim/hooks/dispatch, generic journals and valid crash prefixes preserved, no repair |

The latter schema case requires damaged persistence; its occurrence in owner data
is unknown. None of these findings permits scanning, rewriting or deleting private
sessions/locks, automatically replaying tools, or weakening approval boundaries.

DREAM-035 consolidated checkpoint 2026-09-11T07:50:38-05:00: accepted1–29 combined main verification
passed3991 tests,35 skipped,7 warnings in277.97s. All27 reviewed hashes stayed exact;
tracking passed41 dated records/36 changed paths. No further implementation phase
started before handoff. Next qualification uses actual Engine with existing model
profiles; current source supports configured adaptation, not learned optimization.
Owner acceptance and native/live/install/model-quality qualification remain open.

DREAM-035 checkpoint 2026-09-11T09:04:41-05:00: Phase30 actual Engine/profile qualification and
Phase31 bounded stored-turn pages passed NEW independent gates and were integrated.
Combined unchanged rerun4026 passed,35 skipped,7 warnings in278.12s; all30 reviewed
hashes unchanged. First full screenshot failure remains preserved and unexplained
after focused5/isolated1/full rerun passes. No benchmark/live/install/model-quality
or owner acceptance claim. See the latest Engine/profile handoff for exact evidence.

DREAM-035 planning checkpoint 2026-09-11T09:36:58-05:00: owner requested prioritized optimization
and hardening suggestions. Two fresh read-only Astra reviews informed the
ranked plan.
Priorities: concrete task acceptance; model/task effort calibration; context/recovery;
tool/skill relevance; useful Council/repair calls; demonstrated cache misses and CPU
cost; versioned learning after outcome evidence. Execution dependencies and resource,
ownership, security and release hardening are explicit. No runtime proposal activated,
no benchmark/model/test run, no source changes. Existing implemented1–31 unchanged.

DREAM-035 baseline checkpoint 2026-09-11T10:30:43-05:00: baseline
records September10 three started/two active sessions,109 records and7 turn timings.
No historical task outcomes independently graded; per-turn effort unavailable.
Verification occupied663.7/970.6s and414.3/540.2s in two completed local turns, so
scoped verification is now the first observed latency investigation. Current native
capture5/5, grader/Engine20, effort/runtime91 and software UI4 passed; no model-choice
benchmark or source changes. Existing idle Council effort controls verified; thinking
on/off remains separate launch-only UI. Exact evidence/limits in the dated handoff.

DREAM-035 single-model checkpoint `2026-09-11T12:50:14-05:00`: priorities1–4 and the necessary CLI
integration repair are independently reviewed and integrated. Corrected combined
regression passed4097, skipped35, warnings7, in288.09s.
All13 source/test hashes stayed unchanged. The first full4fail/2error result remains
preserved, along with original phase-gate failures. See the [handoff](updates/2026-09-11-single-model-priorities.md).
No comparative model result, learned optimum, clean install or owner acceptance
follows; next qualification remains DREAM-014/015 rather than another backlog.

## DREAM-067–074 implementation checkpoint — September13

067–071 and074 are implemented locally and independently reviewed. Read-loop advice,
image decoding/container validation, truthful scheduled-review completion, ordinary
HTTP live steering, specialist skill refinements, passive environment facts and
source-version visibility passed targeted CPU gates. A fresh locked dependency
environment and candidate wheel passed installation/privacy checks. Live model
quality, native daily use and owner acceptance remain separate under DREAM-015.

072 remains planned: existing editable Project Documents handoffs are documented;
automatic capture and new recall ranking were not added.073calibration remains
planned pending representative use and later model trials. No model/GPU loads,
owner restart or publication. Exact evidence, failed attempts, reviewer limitations
and recovery: [implementation handoff](updates/2026-09-13-progress-delivery-steering.md).

## September 14 CPU qualification and next evidence

This checkpoint supersedes the September 13 planning status above; the register
remains authoritative. DREAM-072 and 075 are implemented, and DREAM-076's runtime
candidate passed the complete 5,179-test CPU suite, clean locked installation,
fresh source gates and actual native software UI checks. Detailed failures, skips
and scope are in the [restart handoff](updates/2026-09-14-cpu-hardening-restart-recovery.md).
DREAM-073 now has content-free outcome evidence, not learned optimal settings.

The following order comes from a fresh independent review. Expected impact is a
prediction; no comparative model result or automatic policy change follows.

| Rank | Existing work | Next bounded step | Expected value / evidence limit |
|---|---|---|---|
| 1 | DREAM-073 / 066 / 015 | Promote the privately probed actual-Engine data contract, including workspace-preservation checks, then extend document and recovery grading with false-success controls. | High value for choosing useful improvements; four scripted data controls distinguish protocol success from final-file correctness. No model judgment or automatic runtime grader was added. |
| 2 | DREAM-074 / 015 / 065 | Exercise actual App/Engine on the private native display with a finite scripted backend: Stop, reconnect, inspect durable state, explicitly continue once. | High reliability value; a separate actual-App probe found and repaired reconnect/approval Stop ownership. Native UI plus real Engine recovery remains unqualified. |
| 3 | DREAM-073 | Compare attributable model/task outcomes and completion time, including repair and verification, before recommending settings. | Potentially high latency/quality value, low confidence about winning settings until representative use. Preserve explicit overrides and unknowns. |
| 4 | DREAM-029 / 070 / 073 | Audit mixed requests, quoted/negated skill mentions and decisive-step retention under small context admission. | Moderate expected context/first-attempt value; change only reproduced routing failures before expanding guidance. |

Keep protocol completion, artifact checks, independent task grade and owner
acceptance separate. Faster failed work cannot establish a better setting.
These steps refine existing work; they are not a second backlog, permission to
load a model, or authorization to publish. The current manual review packet offers
source-only questions for Claude/Grok without exporting runtime records.

Additional observed recall gap under DREAM-073/022: the current keyword-only
fixture found the expected memory in the top five for 19 of 20 queries. The missed
food-restrictions query has no lexical overlap with its saved dietary-constraints
memory; increasing the result limit from 5 to 26 still returns only three unrelated
matches. Before changing retrieval, add held-out paraphrase/distractor acceptance
cases and compare candidate coverage and precision. Do not special-case the one
public query. No pre-repair fixture result was recorded, so this is an observed
limitation, not evidence that this pass caused or repaired a recall regression.

## September 14 continuation — CPU outcomes, recovery and recall

Recorded `2026-09-14T15:08:32Z`. This supersedes the next-step status in the preceding
checkpoint; earlier observations and failures remain historical evidence.
The five bounded phases are implemented and independently reviewed: artifact hash
binding/workspace preservation, actual native guided-workflow Stop/reconnect and
explicit continuation, caller-written recall alternatives, quoted/negated/mixed
skill routing, and an offline supplied-trial comparator. No settings are applied
automatically and no local model/GPU load occurred.

Final combined verification passed 5,274 tests, 45 skips, one owner-memory-dependent
exclusion, seven warnings and 52 passing subtests with 600 stable source hashes.
The refreshed wheel passed privacy/source/install checks using existing qualified
dependencies. The [handoff](updates/2026-09-14-cpu-outcomes-recovery-recall.md)
preserves gate failures, two test-runner environment failures and recovery evidence.

Representative hosted-model/task quality and latency, ordinary-chat/provider crash
recovery, document/recovery Engine task expansion and owner acceptance remain open
under DREAM-073/015. The keyword baseline was captured before and after this pass
and remains unchanged; supplied query alternatives help different wording without
claiming automatic semantic recall or a learned optimum.

## September 14 ordinary-chat and outcome qualification

Recorded `2026-09-14T15:59:55Z`. DREAM-065 now persists ordinary-chat started/terminal status
and bounded partial replies, joins cancelled writers, and surfaces conservative
recovery status in Project conversations/restoration/handoff drafts. An actual child
Engine abrupt exit retained the saved effect and UNKNOWN status without replay.
DREAM-073 now covers actual Engine document and ambiguous-edit recovery outcomes;
strict document types and meaningful reread evidence reject false-positive grades.

Both fresh execution gates passed. Final combined CPU suite:5,325 passed,45 skipped,
1 owner-memory-dependent exclusion,7 warnings and52 passing subtests;605 source hashes
unchanged. Package source/privacy/install evidence passed, including a corrected
stale probe hash caught by a separate independent evidence audit. See the
[handoff](updates/2026-09-14-chat-recovery-outcome-checks.md) for initial failures,
exact checks and limits. No model-quality uplift, native provider cancellation,
automatic policy change, owner acceptance or publication is claimed.

## Four reliability priorities — September 14

Recorded `2026-09-14T18:46:43.340480+00:00`. DREAM-065 adds an opt-in bounded hosted-adapter
qualifier with isolated synthetic continuation and Stop checks. DREAM-068 checks
static page dependencies and PPTX references, including safe sibling paths and
browser attribute semantics. DREAM-067 adds advisory recovery for repeated identical
failures across changed arguments. DREAM-053 adds fixed failure categories,
recovery hints and a sanitized diagnostic download in the timing card.

All four independent gates passed. Phases 1/2 used fresh reviewers and repaired
one and three findings respectively; phases 3/4 reused native contexts independent
of the reviewed implementations after fresh slots were unavailable. Final CPU suite:
5,402 passed,45 skipped,one owner-memory exclusion,7 warnings and52 passing subtests;
612 stable source hashes. Offline package audit matched271 source/wheel/installed
files. Actual Sol/Terra smoke checks passed; Sonnet/Opus failures remain recorded,
not reclassified as successful interruption. No local GPU/model loads, owner app
restart, general quality claim or publication. See the
[handoff](updates/2026-09-14-four-priority-reliability.md) for exact evidence and limits.
