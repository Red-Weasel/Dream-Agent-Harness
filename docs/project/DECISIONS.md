# Architectural decision log

The entries below distinguish reconstruction of the September build from choices
made for this tracking task. The detailed source/evidence lives in linked guides.
Add a new dated entry to supersede a choice; preserve why the old choice existed.

## ADR-001 — One Engine, two working surfaces

Recorded: 2026-09-05. Disposition: existing September 4 implementation.
Work: DREAM-001. Original plan.

**Problem:** Studio opening independently was confusing and unreliable to the
operator. **Choice:** preserve the real CLI in a VTE terminal and attach Studio
plus a separate native WebKit browser in one GTK window, sharing the Engine.
**Alternative:** replace the CLI with a new web chat or start a second agent.
**Reason:** keep the familiar engine picker and one task/session while making
artifact delivery visible. **Tradeoff:** native Linux dependencies and explicit
browser context boundaries; `dream desktop` is a distinct launcher.

## ADR-002 — Shared contracts with model-aware profiles

Recorded: 2026-09-05. Disposition: existing September 4 implementation.
Work: DREAM-002. Runtime controls.

**Problem:** models differ in context, tools, vision and throughput. **Choice:**
provider adapters share session/tool contracts, with lean/balanced/frontier
profiles and exact overrides; admit all Dream-visible HTTP inputs together.
**Alternative:** a giant universal prompt or a separate harness per model.
**Reason:** preserve consistency while exposing measurable resource controls.
**Tradeoff:** estimates and provider-owned hidden context remain explicit;
per-model task quality needs evaluation, not a preset name.

## ADR-003 — Auto approval relies on an execution boundary

Recorded: 2026-09-05. Disposition: existing September 4 implementation.
Work: DREAM-003/016. Execution.

**Problem:** opaque scripts cannot be proven harmless by command-name matching.
**Choice:** contain routine shell execution at the OS boundary, retain meaningful
consequential approvals, and own child-process lifecycle. **Alternative:** allow
all Bash in Auto or keep prompting for every script. **Tradeoff:** host access is
an explicit bounded exception; trusted extensions/provider-native tools have
their own boundaries. Wider isolation is future work, not silently assumed.

## ADR-004 — Evidence review, evaluation and telemetry remain distinct

Recorded: 2026-09-05. Disposition: existing September 4 implementation.
Work: DREAM-004/006/014/017. Upgrade.

**Choice:** durable runs track goal contracts and independent evidence review;
Council retains attribution/dissent; task benchmarks measure quality; traces
record events. **Reason:** an agent saying “done,” advisor agreement, and a log
are different evidence from an executed acceptance test. **Tradeoff:** review
may block progress and citations still need substantive validation. Legal
readiness is a separate work item with expert review.

## ADR-005 — Learning produces reviewable candidates

Recorded: 2026-09-05. Disposition: existing September 4 implementation.
Work: DREAM-005/007/008. Extensions.

**Choice:** demonstrations create skill drafts; missing capabilities route through
research/reuse and bounded labs; new Python entry sources require digest review.
**Alternative:** automatically install and trust everything generated.
**Reason:** keep improvement inspectable and reversible. **Tradeoff:** operator
review remains necessary; a toy RL holdout is not evidence that a model learned
a new real-world domain or that transitive imports are contained.

## ADR-006 — A repository-native, portable tracking hub

Recorded: 2026-09-05. Disposition: implemented for the owner's current request.
Work: DREAM-011. [Workflow](WORKFLOW.md).

**Choice:** a root front door, one master register, one current handoff,
append-only Markdown updates and a standard-library checker. Agent instructions
and README link to the same hub. Existing plans remain in place with status
banners. **Alternatives:** a hosted tracker as the authority; copying status into
every provider's memory; replacing all historical plans; a large custom project
management application. **Reason:** a fresh human/model can orient without an
account, previous chat, extra dependency or wholesale documentation rewrite.
**Tradeoff:** Markdown still requires review. The checker detects omissions and
inconsistent structure, not semantic truth. Content snapshots handle the dirty
working tree without rewriting it; CI handles later commit diffs. Owner
acceptance and remote branch protection remain explicit events.

## ADR-007 — Media projects with local rendering and explicit provider handoffs

Recorded: 2026-09-05. Disposition: implemented for DREAM-023.

**Problem:** Dream could inspect media and author HTML, but lacked durable media
projects, generation jobs and repeatable video exports. The owner requires existing
subscriptions or host-local tools, without API keys or additional spending.
**Choice:** one workspace media service for CLI, model tools and Create; SQLite
revision/job metadata and immutable assets; deterministic Chromium/FFmpeg worker;
curated ComfyUI API workflows; explicit subscription browser handoff and returned
asset import. Add opt-in saved Browser state in a separate profile. Studio stays
ephemeral, with opaque generated previews. **Alternatives:** paid API aggregation,
a second standalone creative app, or implementing generation engines inside Dream.
**Tradeoff:** website sign-in/quotas and local model provisioning remain external
requirements. Handoff is visible and manual where automation is unverified;
custom-node compatibility and a general editing timeline are future extensions.
**Evidence:** design,
operator guide, [build record](updates/2026-09-05-media-build.md).


## ADR-008 — Model capabilities control local tuning

Recorded: 2026-09-05. Disposition: implemented for DREAM-026.

**Problem:** GLM had a standalone runtime but no MachX server dispatch, and
Dream exposed only GPU count and context during loading. **Choice:** connect
GLM to the shared server and use a metadata-only, versioned capability command
to select supported controls. Validate settings, quote server arguments, and
propagate generation values to the actual HTTP requests. Scope selections to
one model session. **Reason:** model-name strings and copied llama-server flags
do not establish backend support. **Tradeoff:** new architectures and tensor
formats still need implementation; settings are not saved as presets. Overflow
means client compaction or an error, with no implied GPU KV shifting. GLM server
prefix reuse and MTP remain unavailable. See controls
and [observed validation](updates/2026-09-05-machx-model-tuning.md).


## ADR-009 — Conversation first in Dream Desktop

Recorded: 2026-09-07. Work: DREAM-028. This updates ADR-001's presentation choice.

The owner reported hidden answers/tool activity and confusing setup. Desktop now
uses graphical startup plus full-width Chat, Terminal and Browser tabs. Chat uses
the existing App/Engine and authenticated Studio events, so there is one agent
session. Native setup reuses metadata recommendations, presets and MachX loading.
An interprocess load lock protects competing desktop launches; current resource
checks stay mandatory, and attached servers remain externally owned.

Alternatives were keeping the split terminal as the primary input or building a
second web-native engine. Reusing the session preserves tools and permission policy
while changing what the owner sees. The display transcript is bounded and private
to the running session; refresh replays display events, never actions. Pending
approvals are keyed and validated by the server, not authored by model output.

Verification combines regression/browser checks, real GTK/WebKit startup and
preview checks, and a bounded actual local-model task. Model/provider speed and
external skill dependencies remain distinct from interface verification.


## ADR-010 — Curated task guidance and bounded file intake (2026-09-09)

DREAM-029 responds to the owner's request to remove wasteful skills and guide
weaker models through actual workflows. A shared 238-skill catalog and text-only
file reader imposed discovery cost without reliable document support.

Decision: eight short Dream-owned workflows by default; shared client catalogs
are explicit opt-in and remain searchable without entering the wake index. Enabled
Dream plugin skills retain independent plugin controls, including when file roots
are overridden; disabled plugin children remain visible in the settings inventory. At
most two task-relevant workflows reach the first provider request; explicit user
selection/opt-out and extension enable settings take precedence. Curated tool
hints are priorities inside the existing schema budget, never permission grants.
Dynamic reveals expire per user turn. Original user messages remain distinct in
session storage. Evidence of opening instructions is not evidence of model compliance.

Files enter via authenticated bounded uploads and server-issued attachment IDs.
Writes use exclusive files and no-follow directory descriptors in the workspace.
Native read_file provides format-aware bounded extraction; source text and visual
interpretation are distinguished. Macro execution, automatic archive extraction,
implicit OCR and guessed binary decoding are excluded. Existing permissions still
control model file/tool access. Drafts use tab session storage when available;
files remain workspace artifacts until the user removes them separately.

Alternatives considered: keep a global catalog and ask the model to discover
procedures; inject every skill body; use a second model as a router; add separate
file tools for each extension. The selected approach avoids another inference
round and keeps the interface small, at the cost of a deliberately limited
deterministic matcher and explicit format/portability boundaries.

Verification and residual risks are recorded in the DREAM-029 dated handoff.
This decision does not claim uniform model quality, provider feature parity,
ChatGPT latency, owner acceptance or production release.

## ADR-011 — Measured turns and optional idle filing (2026-09-09)

DREAM-031 addresses repeated CPU work, opaque latency and weak-model tool mistakes
without running inference while the owner develops the inference engine.

Decision: explicit per-session performance choices, snapshotted before preparing
each user turn; no automatic model routing, reloading or persistent preset edits.
The active HTTP adapter currently changes output allowance and preserves effort
when it lacks a reported effort ladder. Direct output/effort changes rebase Custom.
Native arguments are checked with JSON Schema before approval/dispatch. The
already installed jsonschema/referencing packages become explicit dependencies;
remote reference retrieval is disabled. Compiled schemas and document extraction
have bounded caches, retaining permission/source checks and immediate extension
invalidation. Curated workflows gain progressive examples, not larger wake prompts.

Timing stores numeric evidence and schema fingerprints separately from run budgets.
Client latency, provider timing and cache reports are distinct. An absent cache
report remains unknown. A bounded offline fixture runner grades actual artifacts
and exported records without an inference adapter, and never executes recorded
code. Fixture success does not establish a model's accuracy or improvement.

Optional filing moves behind an Engine-owned bounded idle queue. Required
verification remains awaited. New foreground input cancels and joins owned filing
before new request preparation, preserving unstarted jobs and never replaying
partially executed work. Each job captures turn text, context and accounting;
late usage updates session totals independently of lead-turn results. Shutdown
joins work before consolidation/client/store cleanup. UI statuses distinguish
queue completion from a claim that useful memory was saved.

Alternatives: keep every schema/extraction fresh, gate completion on all filing,
route tasks through another model, or benchmark immediately. The selected design
reduces known redundant work with deterministic checks while leaving live-model
comparisons deferred. Cancellation orders client tasks only; server computation
cancellation and cross-process coordination require separate qualification.

## ADR-012 — Coordinated local requests and inspectable project tasks (2026-09-10)

DREAM-032 extends the existing Studio and Engine. Guided reports, data analysis
and presentations use saved task drafts, versioned attempts and explicit Start.
The App queue carries task identity and claims the current version before actual
execution. An uncertain dispatch is retained for inspection; it is never blindly
replayed. Only a successful terminal event followed by a valid saved artifact can
reach ready for review. Downloaded artifacts retain immutable bytes. Format and
content checks do not establish factual accuracy or slide layout quality.
Animation continues to use the existing editor. CPU file and store operations
run outside the UI event loop.

Project context uses explicit pins and bounded keyword search with source hashes,
workspace containment and an inspectable context preview. Changed sources require
revalidation. Context stays within a small allowance and enters existing backend
admission. Recovery lists actual durable ledgers without modifying or locking
them; Prepare resume drafts the existing command for explicit execution.

Local HTTP requests share per-user, per-port flock coordination. Cancellation,
uncertain transport completion and ambiguous server timeouts retain a fence.
An operator can clear the exact request only after confirming the server idle.
Local optional filing drains its current request before yielding at a request
boundary. Launch paths share the Desktop lock, inherited by the spawned child,
so closing a launcher does not authorize another cooperating Dream load.
Unrelated engines, other ports/users and physical GPU scheduling remain outside
this contract. CPU fixtures cannot qualify real engine cancellation or residency.

Capabilities distinguish explicit reported facts from configured limits and
unknowns. Only an advertised ordered ladder changes reasoning choices. Diagnostics
read package/dependency facts, disposable settings fixtures and supplied artifacts,
then export allowlisted results without private runtime content or endpoint probes.

Alternatives included a second inference service, another media editor, automatic
embeddings, assuming that disconnect cancels server work, and full live benchmarks.
The chosen design reuses current services and requires no new model. Its tradeoffs
are bounded keyword retrieval, explicit uncertainty recovery, serialized local
requests and deferred live-model/release qualification. Evidence and remaining
limits are in the [DREAM-032 handoff](updates/2026-09-10-dependable-project-tasks.md).

## ADR-013 — Reported activity and inspectable generation settings (2026-09-10)

DREAM-033 keeps the existing Studio event bus and adds a distinct `agent_activity`
display event for compatible HTTP workers. Each invocation carries its own run ID;
its tools stay separate from lead tool accounting and permission state. Only
provider-returned reasoning is shown, with nonstreaming reasoning labeled after
response. Bounded sanitized activity is retained by the existing conversation
history. Feed detail is a browser preference, not a generation setting.

Controls exposes an allowlisted read-only settings summary from backend state.
Next-turn selection, last prepared selection and latest admission remain distinct.
Unknown launch values stay unknown. This avoids both additional model probes and
mislabeling cached selections as current server facts. The tradeoff is that an
existing Python session needs a new application process to produce the new events
and settings; static refresh alone cannot add backend capabilities. Live vision
transport and model performance remain unqualified. See the
session review and
[handoff](updates/2026-09-10-session-review-feed.md).

## ADR-014 — Council controls share the App lifecycle (2026-09-10)

DREAM-034 restores visible main/advisor selection without a second conversation
engine. Startup and authenticated Studio controls share the extended `MoeConfig`:
provider keys, per-advisor models, main/advisor effort, concurrency and timeout.
Saved older selections retain defaults for the additive fields. A provider may
serve both as main and as one independent advisor. Installed prerequisites and
cached local effort capabilities do not claim successful provider authentication.

Provider SDK scopes must open and close on their owning task. HTTP handlers queue
structured Council actions for the App lifecycle task and shield their completion
from a disconnected client. Conflicting input is rejected while the action is
pending. Consultation supports interruption; a connection handoff completes its
transition rather than exposing a misleading Stop action. Failed connection
attempts restore the prior backend; uncertain cleanup or failed rollback marks
the main unavailable and requires a new application instead of an unsafe retry.

Dream retains its session/store/workspace and sends bounded, labeled visible
conversation to the replacement provider. Private provider state is not copied.
Explicit advisor rounds retain read-only isolation and get fresh attributed
runtime budgets; answers join the transcript and main agent's next-turn context.
Selecting advisors alone does not load or invoke models. Supported effort flows
through native CLI/SDK/HTTP options, with terminal aliases normalized for Codex.

Alternatives were a new window for every provider change, dispatching handoffs on
HTTP tasks, and a separate Council engine. The chosen design preserves visible
continuity and existing isolation but requires a new Python application to obtain
the implementation, idle transitions, bounded context transfer and provider-side
model validation. CPU/browser evidence and unperformed native/live checks are in
the [build handoff](updates/2026-09-10-council-controls.md).


## ADR-015 — Model-bound admission and explicit completion ownership (2026-09-10)

DREAM-035 reuses capability normalization, request coordination and existing
iterator chains. Known context metadata bounds actual HTTP payload admission.
Changing model identity discards launch tuning/capabilities and usage-derived
state; selecting the same identity is a no-op. Explicit direct effort remains
user intent. Unknown capabilities stay unknown, and no new model probes occur.
Salvage compacts a deep copy and protects the actual current user request.

Remote and loopback streams use one completion observer. Only the primary choice
can complete the consumed response; contradictory terminal state fails before
tools dispatch. Length, filtering and unknown finishes cannot authorize actions
or optional passes. EOF without completion is a visible failed result, with
partial output retained. Local ambiguity keeps the existing reconciliation fence.

Worker iterator ownership was explored but NOT accepted. Three lifecycle gate
failures exposed cancelled cleanup and then an HTTP-stop regression. Those
source/test edits were withdrawn; the baseline cleanup problem remains. A future
correction must preserve cancellation of active I/O and same-task SDK cleanup.
It must not describe remote cancellation or exactly-once execution as proven.

Alternatives were guessing universal model settings, carrying old-model tuning,
replaying incomplete streams and relying on asynchronous garbage collection.
The chosen contracts avoid those ambiguous behaviors with no new service or
scheduler. Tradeoffs include conservative protocol rejection and no capability
inference for unknown servers. Bare adapters with no metadata retain their legacy
context/output heuristic; exact tokenizer and live-provider behavior are unqualified.
See the [hardening handoff](updates/2026-09-10-harness-hardening-loop.md) for
failed gates, regression evidence and remaining delivery/qualification work.


## ADR-016 — Cancellation preserves cleanup ownership (2026-09-10)

DREAM-035's resumed phase3 correction supersedes ADR-015's withdrawn cleanup
candidate. AutonomousLoop owns the worker iterator in one task, acknowledges
one event after delivery, and retains RunState ownership until the iterator and
its nested Engine/backend iterators close. Caller cancellation requests provider
interruption and waits through repeated cancellation for that owner to settle.

Compatible HTTP uses same-task AnyIO operation scopes, persistent interruption
generations shared by copied delegates, and explicit work checkpoints. Response
hooks separate raw I/O from closure. HTTPcore error shields and owned EOF closure
survive Stop, sibling cancellation and delegate deadlines. Batch children own
cancellation scopes and are joined. Cleanup may exceed the work deadline, but
buffered tools, later requests and successful completion cannot bypass an expired
deadline. Permission is revalidated against the original turn before dispatch.

Alternatives tested were bare async iteration, whole-worker Task.cancel, detached
cleanup, no-op HTTP interruption and asyncio timeout cancellation. Independent
counterexamples showed early lock release, aborted cleanup, late effects or
post-deadline actions. The same judge returned PASS on resumed attempt7 after
all seven counterexamples and179 combined regressions passed. Prior failed gates
remain recorded; the verdict is not owner acceptance or live qualification.

The tradeoff is deliberate waiting for cleanup, including operations that do not
settle promptly. This does not prove remote cancellation or exactly-once effects.
Local ambiguity remains fenced for explicit reconciliation; no action is replayed.
No new service, dependency, model probe or scheduler was introduced. See the
[resumed handoff](updates/2026-09-10-harness-cleanup-resumed.md).


## ADR-017 — Package inspection follows direct entry-page resources (2026-09-10)

DREAM-035 phase4 augments the fixed required-file inventory with direct local
script/link assets from the inspected Studio page. Council files remain required
independently. Source and wheel inspection share bounded standard-library HTML/URL
parsing, without extraction, execution or fetching. Unsafe paths and unsupported
base URLs fail closed. Unicode filename characters are preserved; ambiguous local
spellings are rejected before prefix filtering. Source symlinks and static paths
resolving outside the package cannot satisfy required assets.

The alternative of adding only Council to another fixed list would retain future
inventory drift. Browser execution, transitive dependency analysis and clean
installation remain separate work. The report keeps allowlisted status/counts and
adds a fixed assets_invalid code without disclosing filenames or HTML. Conservative
URL rejection is a documented compatibility limit. The fresh packaging/privacy
judge passed attempt2 after69 tests and31 extra assertions; its prior URL findings
remain in the [handoff](updates/2026-09-10-harness-package-assets.md). No real wheel
build, installation or production acceptance is claimed.


## ADR-018 — Shared numeric parsing before runtime startup (2026-09-10)

DREAM-035 phase5 replaces scattered central numeric conversions with one bounded,
standard-library registry. Config and offline diagnostics use identical defaults,
types and legacy unlimited aliases. Malformed, nonfinite and oversized values
produce fixed guidance naming only the known setting. The main entry point checks
before runtime imports; only exact root -h/--help bypasses validation for recovery.
Diagnostics exposes known setting IDs and failure codes without values or saved
runtime state. The helper is required by wheel inspection.

The alternative of independently duplicating conversions in diagnostics could
report success for settings that crash startup. Silent defaults or clamping would
hide mistakes and retune user choices. The tradeoff is bounded syntax validation
for28 central settings, without claiming range suitability or coverage of every
lazy provider/tool setting. The first gate found an overbroad help-token exemption;
the same judge accepted its correction with108 tests and guarded routing/privacy
probes. See the [phase handoff](updates/2026-09-10-harness-numeric-configuration.md).
No real installation, live provider or production acceptance is claimed.


## ADR-019 — Verifier reports are optional attributed context (2026-09-10)

DREAM-035 phase6 places the previous verifier report in a named assistant message
before the untouched current user request. Filing excludes this prior background.
Under compact policy, plain verifier messages are removed completely before
ordinary history elision, with existing labeled working-note capture when available.
This prevents both the report and its residual stub from blocking fitting input.
Error-on-overflow preserves its existing refusal behavior; tool-call pairs cannot
be removed merely because an assistant message has the verifier name.

Prepending the report to the user erased attribution and protected optional text
from compaction. Merely stubbing a separate report still consumed boundary tokens.
The tradeoff is that optional reports can leave model context; bounded notes are
not a full archive, and ordering does not guarantee model obedience. Fresh review
passed166 tests,45 payload probes and16 boundary probes. The lead separately
passed167 including the browser fixture. See the [handoff](updates/2026-09-10-harness-verifier-context.md).
No provider behavior or production acceptance is inferred from CPU evidence.

## ADR-020 — Delegates validate selected completion before acting (2026-09-10)

DREAM-035 phase7 uses the existing primary-choice selector for nonstream delegate
responses and requires a valid message object. Explicit length, filter and other
unsuccessful/unsupported finish reasons return an incomplete failure before XML
recovery, tool dispatch or a clean summary. Earlier round effects and usage remain
visible. Omitted/null finish reasons retain legacy complete-JSON compatibility;
completed local response leases return idle while transport ambiguity stays fenced.

Checking truncation only when tools were absent allowed an incomplete response to
authorize actions. Selecting the first list entry also let a secondary choice
control execution. Reusing shared selection avoids a second protocol definition.
Strictly requiring finish_reason would reject existing complete-body adapters and
is outside this correction. The fresh gate passed227 tests and180 independent
probes; no live provider compatibility or exactly-once claim follows. See the
[handoff](updates/2026-09-10-harness-delegate-completion.md), including broader
fixture failures and their separately reviewed corrections.

## ADR-021 — Image configuration is separate from reported support (2026-09-10)

DREAM-035 phase8 exposes the current compatible-HTTP tool-image flag and actual
see registration in read-only Active settings, with explicit configuration and
not-permission attribution. Known vision that disagrees with the flag produces
fixed guidance. Missing or malformed facts remain unknown. Reading status does
not modify tools, settings, model state or encoding; model changes invalidate old
support while preserving the current adapter configuration and explicit effort.

Automatically trusting raw MachX fields or sidecar-load observations would invent
an unqualified provider contract. Hiding the mismatch leaves users unable to
explain why reported support and available tools differ. This bounded inspection
improves diagnosis without claiming automatic vision integration. The fresh gate
passed105 tests,100 independent state/configuration probes and4 extra browser
checks. See the [handoff](updates/2026-09-10-harness-vision-status.md). Live endpoint
acceptance, native qualification and model quality remain unperformed.

## ADR-022 — Screenshot argument checks precede preview loading (2026-09-10)

DREAM-035 phase9 advertises screenshot step bounds, required multi-capture code,
supported filename endings and exactly one nonempty destination. Direct handlers
check destination/code types and finite delays without coercing malformed values
into capture work. Raw filenames use the same pattern as the schema before path
resolution; resolved target extensions are still checked. Valid encoding, finite
delay behavior, permissions and Preview implementation remain unchanged.

Loading first can hide an actionable filename correction behind a preview failure.
Path.suffix alone is insufficient for raw validation: the first fresh gate found
trailing /, // and /. normalized into capture paths despite schema rejection.
The corrected candidate passed the same judge with155 tests and159 independent
probes. Explicit empty unused destination fields must now be omitted. Schemas do
not prove filesystem access, browser availability or live model retry savings.
The browser gate also emitted an unexplained post-run TargetClosedError; preserve
that diagnostic separately from the passed argument contract. See the
[handoff](updates/2026-09-10-harness-screenshot-contract.md).

## ADR-023 — Schema selection shares admission's estimate (2026-09-10)

DREAM-035 phase10 replaces selection's character counter with the unchanged
admission estimate of the entire serialized tools array. Catalog allowance counts
escaped UTF-8 text and reserves selected schemas and the empty discovery tool
together. A reproduced multilingual optional schema cost358 in selection but1153
in admission, causing avoidable refusal. The corrected fake turn preserves the
request and explicit settings while deferring that tool.

Keeping separate counters permits inconsistent choices. Changing the admission
formula would extend beyond the demonstrated defect. Mandatory full schemas and
discovery remain intact, so their floor can exceed the optional fraction target.
Five legacy behavioral tests now distinguish that floor from optional capacity;
fixed-window controls retain the original boundary cases. Per-line rounding is
conservative and the final array check still governs optional membership.

The fresh independent gate passed161 tests and702 additional cases/assertions.
The pre-existing first-return schema alias is a separate follow-up; no normal
production mutation was demonstrated. This correction establishes estimate
consistency, not tokenizer accuracy, quality, speed or production readiness.
See the [handoff](updates/2026-09-10-harness-schema-estimator.md).


## ADR-024 — Required user intent and optional Council context (2026-09-11)

Work: DREAM-035 phase19. Disposition: commit-receipt correction independently
passed after owner resumption; exact nine source/test paths integrated. Owner
acceptance is not recorded.

**Problem:** bounded prefix/suffix slices can silently remove the latest user's
constraint or a later advisor. Appending all that optional text to the protected
current request can repeatedly exceed a smaller model's admission budget.

**Choice:** retain exact required prior user records with source/chronology, separate
from optional history and advice. Use the compatible HTTP backend's actual window,
schemas, output reserve and admission math. Check required transfer within the
existing connection/rollback path; the next real request is still admitted normally.
Protect required user input through compaction/snipping, and retain unacknowledged
source across failed or partial turns. Identify every advisor in a retained
consultation; if metadata cannot fit, omit the whole consultation and show a
complete omission manifest outside model input. Returning an inserted turn ID from
storage preserves existing successful effects and identifies that exact row.
WorkingMemory keeps its ordinary None return and delivers the ID through an
optional internal synchronous callback after acknowledged commit, before JSONL.

**Alternatives:** increasing a fixed character cap worsens small-window admission;
truncating the latest real user request can remove constraints; keeping every
advisor header mandatory recreates overflow at zero optional room. Guessing native
SDK/CLI context capacity would invent unsupported capability evidence.

**Tradeoffs:** exact required text can force a visible refusal, while optional
context can be absent. Omissions do not preserve all dissent. Native private
context fit remains unknown, and repeated explicit native attempts may duplicate
quoted context. Existing read_session truncates records; IDs/paths identify sources
without promising an unrestricted full-recovery tool. No new provider calls,
automatic model switch, retrieval tool, schema migration or permission rule.

Evidence and final criteria: [recovery handoff](updates/2026-09-11-harness-recovery-audit.md)
and the shared adaptation plan. Seven fake-HTTP audit scenarios reproduced the
defects; those audits do not prove the proposed implementation or native behavior.

## ADR-025 — Failed journal appends stop the writer (2026-09-11)

Work: DREAM-035 phase20. Disposition: exact candidate integrated after a NEW
independent Astra gate. Owner acceptance is not recorded.

**Problem:** an append can write bytes and then fail at fsync before the in-memory
sequence advances. Automatically recording an error through that same object can
reuse the sequence and turn uncertain I/O into definite journal corruption.

**Choice:** after uncertain append I/O, reject subsequent writes through that
RunState instance and retain the original failure and exact bytes. Loop reports
an unpersisted storage failure without writing more diagnostic/outcome records.
The driver retains its lock through owned cleanup. A later explicit open applies
existing ledger validation; there is no automatic repair.

**Alternative:** incrementing sequence after a failed write guesses which bytes
exist; retrying can duplicate data; truncating or rewriting the tail destroys
evidence. A snapshot-only failure is different: once ledger fsync succeeds, its
record is committed and in-memory sequence/state must stay advanced.

**Tradeoffs:** an error may have no durable outcome record, and a partial ledger
may still refuse recovery. Report the known run ID/path so the owner can inspect
it. This does not add exactly-once execution, damaged-state migration, or a
reconciliation-note fix. Tests inject faults only into disposable files; no owner
storage or runtime data is involved.

ADR-025 evidence:91 fresh focused tests and43 independently authored fault cases
passed. The judge reproduced the original duplicate sequence [1,2,2], verified
byte-identical successful ledger/snapshot output, and tested the lock through
worker cleanup and repeated cancellation. Exact three accepted files integrated;
consolidated full regression remains pending phase19. See the
[recovery handoff](updates/2026-09-11-harness-recovery-audit.md).

ADR-024 checkpoint: three formal same-judge FAIL gates caught outer acknowledgment,
preparation cancellation, and progressively more precise committed-source ownership
errors. The final candidate passes234 selected tests and42 prior independent probes,
but rollback plus a competing identical insert can still be falsely attributed to
the owning write. Lead agrees with the finding. The next unimplemented proposal
uses an explicit acknowledged-commit receipt from storage before JSONL append;
it is recorded in the shared adaptation plan. No fourth attempt has started.

ADR-025 final integration verification:3030 full-suite tests passed,35 skipped,
7 warnings in234.73s, exit0. This covers accepted phases18/20 with the earlier
accepted phases; all phase19 source remains unintegrated.

ADR-024 resumption: the owner explicitly requested continued implementation.
The proposed interface now retains ordinary WorkingMemory.log_turn's None return
and delivers storage's acknowledged INSERT ID through an internal callback before
JSONL. It rejects a pre-existing transaction without altering it and cleans up only
its owned transaction. Callback failure is observable and skips JSONL; missing
receipt support cannot trigger a write retry. The same judge will verify these
contracts, including updated test interfaces with all original behavioral controls
preserved. See the [resumed handoff](updates/2026-09-11-harness-commit-receipt-resume.md).

ADR-024 accepted receipt correction: same-judge PASS299 selected/65 independent
cases; byte-identical guarded keyword evaluation. Root integrated all nine exact
accepted source/test hashes. The original three FAILs remain preserved. Callback
failure is incomplete capture despite committed DB; ambiguous commit failure
issues no inferred receipt. This boundary does not qualify every other store
method, power-loss durability or live provider behavior. Main full passed3120 tests,
35 skipped,7 warnings in237.17s, exit0. Phase21 remains isolated.

## ADR-026 — Validate recovery before claiming a saved run (2026-09-11)

Work: DREAM-035 phase21. Disposition: three exact source/test hashes integrated
after SAME-judge round3 PASS340 selected/131 independent checks. Main full passed
3408 tests/35 skips/7 warnings in242.02s, exit0; owner acceptance is not recorded.

Problem: malformed persisted phase values could bypass reconciliation and dispatch
a worker. Structural validation also needed to precede completion returns and
budget hooks. The first two gates exposed numeric and Unicode values accepted by
the decoder but rejected by the writer after claim.

Choice: validate each canonical record's envelope and present known fields in
RunState; validate complete Loop state and final consistency before resumed claim
or hooks. Require finite decoded floats and writer-compatible JSON/UTF-8 text.
Preserve generic partial journals, ordinary unknown fields, legacy missing hashes,
valid crash prefixes and typed stale results during running. A conservative true
uncertainty flag still gates a saved done state; clean done remains no-replay.

Alternatives: runnable defaults can hide damage; snapshot repair can override
canonical evidence; validating only the final record hides malformed history.
A universal historical transition schema would exceed the needed boundary and
reject generic journals. This change does not add one.

Tradeoff: reopening serializes decoded records to check encoding compatibility,
adding recovery work without a latency claim. Invalid recovery leaves stored bytes
unchanged and returns a bounded diagnostic; lock creation/acquisition remains
allowed. No repair, normalization, migration, authentication or exactly-once
execution is implied. Both failed candidates/reports remain frozen. See the
[resumed handoff](updates/2026-09-11-harness-commit-receipt-resume.md).

## ADR-027 — Recovery input remains run-scoped context (2026-09-11)

Work: DREAM-035 phase22. Disposition: exact three paths integrated after a NEW
independent gate,407 selected/49 independent passes. Combined full verification
with phase23 is pending. Owner acceptance is not recorded.

Problem: an accepted recovery note could disappear during contract restart,
callback failure, review rejection or a later invocation, losing ongoing
restrictions such as an instruction not to repeat a completed action.

Choice: project ordered immutable notes from canonical resume_requested ledger
events, including valid legacy entries. Prepare the projection before append and
publish it at the existing fsync boundary. Deliver each exact submission once in
contract, worker and reviewer prompts, labelled with source sequence/timestamp
and current versus historical status. Notes remain unverified caller reports;
they do not replace evidence, permissions, acceptance criteria or fresh
reconciliation for a new uncertain operation.

Alternatives: retiring a note on worker success loses ongoing constraints; keeping
only the latest note drops unrelated restrictions; a state list repeated in every
record adds a second representation and does not recover pre-fix notes. A separate
note file would add another commit boundary.

Tradeoffs: retained context grows with run history and existing admission may
refuse an oversized prompt. No silent summary/truncation or model-obedience claim.
Python meaningful resume_note now requires a run ID, non-string input rejects
before claim/hooks, and whitespace cannot release need_input. Clean completed
runs accept no new note. Stored timestamp text is attribution, not authenticated
time. See the [resumed handoff](updates/2026-09-11-harness-commit-receipt-resume.md).


## ADR-028 — Respect incomplete results across consumers (2026-09-11)

Work: DREAM-035 Phase24. Disposition: four exact source/test paths integrated
after NEW Astra PASS,643 selected/96 independent checks. Seventeen existing
model-loading checks skipped; zero warnings. Combined main full remains pending.

Problem: Grok's explicit MaxTurns result retained is_error=false, so DONE text
could advance durable and guided completion and timing could report completed.

Choice: preserve raw adapter data and usage; Engine marks non-error explicit
non-success subtypes incomplete and emits one bounded quoted explanation. Loop
and guided consumers reject completion through their existing error paths.
Missing/null subtype retains legacy compatibility, while Council acknowledgement
still requires explicit success and normal closure. App owns its Engine iterator
with aclosing after reproduced cancellation escaped cleanup in another context.

Alternatives: rewriting provider tags obscures evidence; treating every missing
tag as failure breaks legacy adapters; changing only timing leaves durable false
completion. Standalone renderer/HTML or a new shared predicate would expand the
required scope without fixing additional reviewed normal-flow behavior.

Limits: single-terminal consumer semantics, existing length behavior, no automatic
retry/replay/model/effort/permission changes. Partial effects still require
reconciliation. Fixture ownership/cleanup checks do not qualify live providers
or every uncooperative repeated-cancellation schedule. See the
[resumed handoff](updates/2026-09-11-harness-commit-receipt-resume.md).


## ADR-029 — Reconcile CLI completion with owned process exit (2026-09-11)

Work: DREAM-035 Phase23. Disposition: exact CLI source/new test integrated after
SAME-judge round3 PASS157 selected/54 independent cases. Combined full is running.

Problem: a streamed success report could precede nonzero exit or failed cleanup,
allowing false completion and acknowledgement of required context. The first gate
found suppressed stderr-close errors; the second found aclose suppressing errors
attached to GeneratorExit. Both failures remain frozen and pass unchanged now.

Choice: retain the first terminal report until normal EOF and owned process/stderr
settlement, stream other activity, reconcile exit/fatal/duplicate evidence, and
preserve first usage without fabricating pending usage on aborted consumption.
Keep the turn guard until cleanup settles under repeated cancellation. Treat
GeneratorExit as close control flow; operational cleanup errors stay observable.
Wait for owned cleanup completion then retrieve its result synchronously, based
on observed installed-Python generator exhaustion/reuse controls.

Alternatives: trusting early terminal text misses later exit failure; ignoring
cleanup faults hides uncertainty; yielding an error from finally breaks async
generator closure. A broad supervisor/adapter-schema rewrite exceeds this boundary.

Tradeoffs: terminal publication waits for cleanup while partial activity streams.
No remote-effect rollback, new session, retry, provider flag or universal Python
version claim. Main regression includes existing synthetic lifecycle fixtures;
live provider and descendant supervision qualification stays separate. See the
[resumed handoff](updates/2026-09-11-harness-commit-receipt-resume.md).


ADR-027/028/029 combined integration verification: accepted phases1–24 passed
3643 main regression tests,35 existing skips and7 warnings in249.68s, exit0.
All17 resumed accepted source/test hashes verified after the run. This includes
existing synthetic lifecycle/browser fixtures, not installed live providers.
Phase25 remains isolated. Full details and skips are in the resumed handoff.


## ADR-030 — Apply destination profiles without replacing native sessions (2026-09-11)

Work: DREAM-035 Phase25. Disposition: exact Engine/App/new-test integrated after
NEW PASS499 selected/43 independent checks, zero skips/warnings. Main full25
verification remains pending; the earlier3643 pass covers1–24.

Problem: after /model B, actual HTTP payload/admission still used A's exact-model
profile. An offline reproduction showed32768/4096 instead of B's4096/512 limits.

Choice: validate named model, keep same-model selection a true no-op, resolve B
from its own settings and permit only dynamically read profile changes. Refuse
construction-bound differences before the single native set_model call. After
success, synchronously publish the Engine and real HTTP profile references. App
persists/displays the canonical selected name. Preserve explicit effort, native
session/client, tools/permissions, previous meters and queued filing snapshots.

Alternatives: automatically routing through Council replaces private native
conversation state; assigning the entire profile leaves construction-bound
consumers stale; retaining A's resolved values violates destination provenance.
A general live-reconfiguration framework is unnecessary for this correction.

Compatibility: direct Engine None/blank selection now rejects with Council
provider-default guidance; terminal no-argument /model remains read-only. Allowed
fields are context/output/schema budgets, ordinary-run token/tool/time budgets
and subsequent filing policy. Other differences require Council/new application.
Local state stays unchanged on native failure/cancellation, without claiming an
uncertain remote SDK change was rolled back. See the
[resumed handoff](updates/2026-09-11-harness-commit-receipt-resume.md).

## ADR-031 — Require completed SDK Council receipts (2026-09-11)

Work: DREAM-035 Phase26. Disposition: four exact paths integrated after third
SAME-judge PASS252 selected/169 independent checks. Two earlier FAILs remain
preserved; their probe sets pass unchanged. Combined main verification is pending.

Problem: text without a ResultMessage, or with explicit incomplete subtype/reason,
was returned as ordinary advice. Independent review also found cancellation lost
when read/close finalizers replaced it with ordinary exceptions.

Choice: require one successful receipt after normal query exhaustion and same-task
bounded close; reject missing, duplicate and contradictory receipts through the
existing attributed unavailable path. Retain first reported usage once and all
existing Council row/storage/sibling/model/effort behavior. Recover retained
cancellation context only with a new task cancellation at the relevant boundary;
preserve the original cancellation and bounded quoted secondary notes. Historical
context alone is not a new cancellation. Two old success fixtures gain real
terminal receipts while preserving every original assertion.

Alternatives: trusting text ignores explicit SDK outcome evidence; treating all
exception contexts as current cancellation misclassifies recovered failures;
detached close breaks AnyIO ownership. A new Council result schema or shared
supervisor is unnecessary for this boundary.

Limits: fake SDK/AnyIO/actual App/Engine fixtures establish the tested contracts.
Cancellation identity erased by provider code is not reconstructed. Completion
receipts do not prove advice correctness or provider billing. No live/provider,
release or owner acceptance follows. See the
[resumed handoff](updates/2026-09-11-harness-commit-receipt-resume.md).

## ADR-032 — Settle the main SDK response before completion (2026-09-11)

Work: DREAM-035 Phase27. Disposition: two exact paths integrated after SAME-judge
round2 PASS493 selected/108 independent checks. The first criterion5 FAIL remains
frozen; its eight product failures now pass unchanged. Combined accepted1–27
verification passed3874 tests,35 skipped and7 warnings with all24 hashes unchanged.
This also supplies the previously pending combined verification for ADR-030/031.

Problem: missing terminal evidence or an explicitly incomplete reason could accept
DONE and acknowledge context. Early consumer exit could finalize the SDK response
in another task; independent review exposed current/historical cancellation mixups.

Choice: require explicit completed receipt evidence after normal per-response
exhaustion and same-task owned closure. Preserve the installed SDK first-terminal
response boundary and long-lived client. Stream partial activity, retain known
first usage on consumed failures and publish no success after read/close failure.
Distinguish current cancellation from inherited exception context and retain bounded
secondary diagnostics. GeneratorExit never causes another yield.

Alternatives: waiting for process EOF would break native session continuity;
trusting result text loses terminal semantics; traversing every historic exception
misclassifies an ordinary failure. No generic consumer or SDK dependency change.

Limits: finite actual Engine/Loop and SDK/AnyIO controls qualify these schedules.
No hard bound on uncooperative close, remote rollback, provider correctness or live
qualification follows. See the resumed handoff for exclusions and preserved REDs.

## ADR-033 — Require completed SDK reviewer evidence and exact cancellation provenance (2026-09-11)

Work: DREAM-035 Phase28. Disposition: three exact paths integrated after SAME-judge
round3 PASS,344 selected and226 independent checks. Both formal failures and the
superseded provisional PASS remain preserved. Combined full28/29 passed3991 tests,35 skipped,7 warnings with27 hashes unchanged.

Problem: PASS text without completed terminal evidence could finish durable work.
Review also found that cancellation-count inference could revive an unrelated old
exception, and that retaining a witness after successful read could misclassify a
later async-generator consumer error.

Choice: require exactly one successful receipt after query exhaustion and same-task
closure; preserve first reported evaluator usage and existing scoped read tools.
A local await observer records the exact latest cancellation injected before SDK
handling, forwarding native await behavior in the same task. Recover only that
identity when retained in a failed operation; end the witness on successful await.
Direct cancellation remains direct and cleanup retains established primary errors.
No retry, task replacement, SDK dependency or generic consumer/parser change.

Alternatives: text alone ignores terminal outcome evidence; task counts and even
identical traceback signatures do not prove cancellation identity. A new supervisor
or detached cleanup would change ownership beyond this correction.

Limits: finite SDK/AnyIO and actual Engine/Loop controls qualify the observed
contracts. Erased identity, uncooperative cleanup and live model quality remain
unqualified. The successful-read athrow finding is not a claim ordinary collect_review
uses athrow. Council/main provenance is separately tracked as29. This is engineering
acceptance, not owner acceptance or a production release.

## ADR-034 — Preserve exact SDK cancellation identity across Council and main (2026-09-11)

Work: DREAM-035 Phase29. Disposition: exact three paths integrated after NEW Astra
PASS all six criteria,449 selected/40 unchanged audit/7 new independent checks.
Combined full verification passed3991 tests,35 skipped,7 warnings with27 hashes
unchanged; no owner or production acceptance follows.

Problem: a consumed current cancellation can leave an operational error carrying
an unrelated old cancellation. Counting cancellation requests incorrectly revived
that old exception in both Council and main SDK paths.

Choice: extend the accepted evaluator's per-await identity observer to the two
existing SDK functions. Successful reads end the witness before processing/yield;
failed reads/closes recover only the exact latest observed cancellation retained
in the error context. Preserve direct cancellation and the established primary
cancellation through cleanup. Council holds its read failure until close settles,
and recovers wrapped cleanup cancellation inside its existing five-second timeout.
This lets that timeout settle its own request while preserving external Stop.
Main retains the installed first-terminal boundary and connected native client.

Alternatives: cancellation counts, error text and traceback signatures cannot
establish exception identity. Detached cleanup or a new supervisor would change
ownership beyond this correction. No SDK dependency or shared-helper change.

Limits: finite same-owner/repeated-query/AnyIO and actual Engine/Loop controls
preserve first usage, required context and failed completion behavior. Erased
identity cannot be reconstructed; uncooperative cleanup and live provider behavior
remain unqualified. Both prior adapter regressions and all failed artifacts are
preserved. See the resumed handoff for exact report, source and log hashes.

## ADR-035 — Read long stored turns through explicit raw-character pages (2026-09-11)

Work: DREAM-035 Phase31. Disposition: exact two paths integrated after NEW Astra
PASS all six criteria,51 selected and seven independent checks. Combined main
rerun passed4026/35skips/7warnings, with an earlier unexplained screenshot failure
preserved. This is engineering acceptance, not owner acceptance.

Problem: storage preserved a long interrupted request, but ordinary at/window
reads repeatedly returned the same clipped prefix. A known-tail keyword could
retrieve a matching excerpt, but could not enumerate an unknown remainder.

Choice: retain default excerpts and add a hint when clipped. Explicit offset/chars
mode requires an exact at turn in the requested session and returns raw text with
identity, range, total length and next_offset. Strict integer validation precedes
store access; a neighboring record never substitutes for an absent ID. Pages cap
content at1200 Unicode characters, preserve whitespace and end with null continuation.
Only read_session schema/handler changes; existing store methods remain unchanged.

Alternatives: increasing the default excerpt enlarges every response and retains a
finite cutoff. Keyword search requires knowing what to seek. An unbounded dump
loses the existing output bound; a new persistence API is unnecessary for this fix.

Limits: one full selected record still materializes in host memory, and escaped
JSON plus metadata can exceed the content character cap. No automatic replay,
session activation, provider-private restoration or model obedience is established.
The independent judge reconstructed raw control/Unicode text and inspected exact
page results in actual Engine requests using a finite scripted transport.


## ADR-036 — Preserve exact session identity in recovery locators (2026-09-11)

Work: DREAM-035 Phase33. Disposition: integrated after same-judge round2 PASS,
49 checks including all seven unchanged independent probes.

Problem: recent listings omitted the ID required to reopen a session. Adding a
locator exposed an older lookup defect: trimming an opaque ID could select a
different stored session with the same trimmed name.

Choice: include JSON-safe read_session arguments in each recent entry and use the
exact supplied ID in lookup and raw pages. No trimmed fallback, including when
the exact archive is absent. Store contents, search, ordering and paging stay in
the existing implementation. Duplicate titles do not substitute for identity.

Alternative: exact-first followed by trimmed lookup retains convenience for
manually padded inputs but can open the wrong session after the exact one is
removed. Callers must now supply the stored ID without adding padding.

Evidence: the independent gate first failed two identity probes; both pass
unchanged after repair. Separate actual Engine routes reconstruct Unicode tails
and preserve current input, archive rows and workspace files. This does not
restore provider-private state or authorize replay. See the single-model handoff.


## ADR-037 — Scope page verification and refuse known missing inspection tools (2026-09-11)

Work: DREAM-035 Phases32/35. Disposition: independent gates PASS; integrated.

Problem: unconditional enumeration of every reachable page state can exceed the
requested task, while an image-filtered adapter could still solicit a visual PASS.

Choice: automatic review carries the exact latest actual user request, baseline
page/console/rendered checks, required behaviors and named states. Directed review
adds its specific check. Stop after required coverage; preserve explicitly broad
requests and keep incomplete scope unverified. Compatible-HTTP verifier dispatch
requires all four registered/allowed inspection tools before requesting inference.
Unavailable tools yield attributed unverified findings; registration does not prove
endpoint acceptance. Existing exact-PASS handling and bounded routes remain.

Alternative: removing verification would discard required evidence; a generic
reduced round budget could truncate valid broad work. Neither follows from the
uncontrolled historical timing observations. No automatic vision or host fallback.

Evidence: Phase32 passed41 selected/24 independent probes; Phase35 passed96
selected/49 independent probes, each with one browser test intentionally deferred
to combined regression. Existing execution guidance passed43 targeted checks.
Native SDK preflight, actual image support and model latency/quality remain unproven.

## ADR-038 — Attribute measurements without weakening input retention (2026-09-11)

Work: DREAM-035 Phase34. Disposition: SAME-judge round3 PASS after two failures;
329 selected,10 unchanged original probes and4 additional independent probes.

Problem: old timing lacked per-request settings and reliable shared-meter turn
identity, and first-result timing could hide later errors. A proposed repair that
recorded only after acknowledgment broke recovery on bookkeeping failure.

Choice: freeze bounded prepared HTTP selection and actual sent request settings.
Native effective unknowns stay null. Preserve every existing fallible success gate
before required-input acknowledgment, including timing.finish and the original
turn_timing record. That immutable revision0 explicitly ends at pre_acknowledgement.
A later acknowledgment failure attempts a separate same-turn revision1 correction
and updates local status; no prior snapshot mutates and the primary exception
remains primary. Resolve by session/positive turn and highest revision. Recording
is best effort, not proof of durability. Unknown pre-allocation or resumed-review
identity is null rather than another turn's ID.

Separate task_assessment records reference only committed guided format checks
or independent loop reviews, with task_success:null. No invented ordinary-chat
grade or automatic default tuning. New metadata excludes raw request content and
model directory paths; basename plus bounded-original hash retains attribution.

Tradeoff: normal elapsed time excludes later acknowledgment/record completion;
old consumers ignoring corrections see only the pre-ack observation. A failed
recorder can leave no durable correction. Those limits are explicit in performance
docs. Independent probes injected real recording OSError and retained actual
sources through failures. No model-speed, correctness or production claim follows.


ADR-038 integration correction: the first combined suite exposed ordinary Loop
closure relabeling observed CLI failures as interruption, plus an incomplete
Engine test stub. Split GeneratorExit from actual CancelledError at the three
existing Engine boundaries: closure preserves known failure; cancellation remains
interrupted. Use the existing disposable real Engine in the terminal-evidence
fixture. Keep prior CLI exit/retention assertions and all original probes. Same
judge passed511 selected,14 unchanged independent and6 new next-read cancellation
checks. This narrow repair changes no acknowledgment, containment or CLI receipt
rule. Original full failure stays recorded; combined rerun remains required.


## 2026-09-11 — Council active work uses serialized provider handoffs (DREAM-050)

Problem: private read-only advisors cannot polish project files, and model-ID
text fields require memorizing provider identifiers. The owner explicitly asked
for named model dropdowns and Council members that actively hop in.

Choice: expose offline provider model catalogs and model-specific effort; keep
custom IDs. Active assignment and team relay use the existing main Engine
handoff on its lifecycle task, ordinary turn tools/permissions, then restore the
original main. Shared workspace writers take turns. Keep private consultation
isolation intact for explicit review. No automatic model loading or new time cap.

Alternative: simultaneous resident per-member editing backends. This needs
separate tool contexts, session ownership, workspace conflict handling and resource
qualification; it is not implemented or represented as available. The current
roster still has one member slot per provider plus the main.

Tradeoff: handoff reconnect cost and bounded Dream-visible context transfer;
provider-private sessions do not transfer. Restoring a backend can fail and must
be surfaced without replay. Catalog suggestions do not establish account access.
Evidence: offline catalog/CLI argv tests, real Engine/AnyIO scope round-trip with
fake backends, browser interaction/recovery tests, and retained consultation
isolation checks. No live provider or GPU invocation during this implementation.


## 2026-09-11 — Explicit remembered native Bash approval (DREAM-051)

Owner explicitly requested Always allow for Bash while preserving prompts for
legitimately dangerous actions. Add separate sandbox and host remembered choices
for the exact command in this session, Engine instance, workspace and execution
scope. Plan/invalid-scope denials precede lookup; recognized consequential shell
effects and red-team sessions have no remembered choice. Recheck captured
session/mode/scope/capability after permission waits. Host cache hits reissue an
exact-command host authorization, never reuse a sandbox grant as host permission.

This intentionally extends the earlier once-only native boundary contract. It
does not create prefix/global/persistent host authorization or pin script contents.
Changing referenced code can change the behavior of an approved command; the
operator receives that exact-command scope, not a proof of safety. /new and exit
clear grants. Provider-owned executors retain their independent approval controls.
Model-free tests cover UI choices, repeated commands, changed commands, executor,
workspace, scope, Engine replacement, Plan, stale answers and consequential commands.


## ADR-039 — Branded workspace views retain one authenticated session (2026-09-12)

Work: DREAM-052. The owner authorized the six-phase design plan and eclipse/floating
figure branding. Native GTK owns Terminal and Browser; allowlisted view messages
carry the current session ID to the authenticated loopback Studio page. No message
carries a host command or arbitrary navigation URL. Web navigation reuses existing
controls, closes obsolete modal views and preserves composer drafts and iframe state.
Alternative: duplicate native/web apps or replace the terminal; rejected because it
would split session ownership. Tradeoff: GTK and web palettes are maintained separately.

Presentation can switch back without reloading. Dashboard facts come from current
runtime APIs and retained events; absent values are explicit. Output history reuses
registered media assets, while comparison reads preserved source revisions without
writing files. Consolidation status describes real lifecycle phases; it does not
claim deferred indexing or durable-first ordering. Council activity remains sequential.
Fixture-backed browser and native checks establish those boundaries; no live model,
GPU render, owner restart or publication was part of this implementation.


## ADR-040 — Local generation read limits are explicit (2026-09-12)

Work: DREAM-053. A runtime profile's idle default overrode the documented unlimited
local HTTP read setting. Buffered tool arguments can leave a stream silent while
local inference continues. MachX and canonical loopback endpoints now use
DREAM_LLM_READ_TIMEOUT_S directly; hosted adapters keep profile defaults unless the
explicit read setting overrides them. Connect/write/pool behavior is unchanged.
Alternative: increase the implicit cap; rejected because another long generation
would encounter the same hidden limit. Tradeoff: a stalled local stream may need
operator Stop or an explicit limit. No timeout implies permission to replay it.

The active client's actual read timeout is observable. Failures persist a bounded
exception category, stage and effective timeout without sensitive response content.
Canonical endpoint classification prevents remote names beginning with 127 from
receiving loopback behavior. Real buffered loopback fixtures cover default success,
explicit timeout, uncertain ownership and one request only. Owner session evidence
supports this diagnosis but lacks the old exception class; live acceptance remains.


## ADR-041 — Private project and skill workspaces (2026-09-12)

Work: DREAM-055/056. Popup catalogs could select skills or inspect pins but could
not serve as a place to maintain instructions or return to a body of work. The new
pages use existing navigation and retain drafts. Skills save private overrides;
Projects explicitly associate workspaces and archived sessions in a private catalog.
An idle-only serialized App operation owns backend stop/start, preserves provider
selection, resets permission grants and preview handles, and restores bounded
conversation text without replay. Older records require explicit user association.

Project documents are bounded authoritative Markdown with metadata and atomic
revision-checked saves. User-selected notes enter the common Engine project context
path; other documents remain private references. Existing consolidated summaries
and latest-source linked memory records are read-only originals with editable-copy
actions. This avoids treating ambiguous legacy provenance as verified project scope.
No automatic migration, new embedding workload, or background learning policy is
introduced. Alternatives were global memory import or loading every document into
every prompt; those would mix unrelated work or impose uncontrolled context cost.
Tradeoff: exact provider-native continuation and automatic semantic retrieval of the
new private documents are not implemented. Existing recall remains available.

Hermes research informed the separation of compact memory, searchable session
archives and procedural skills; its documentation does not establish comparative
quality for Dream. Sources: official Hermes persistent-memory and skills guides.
Implementation evidence and limitations belong to the dated workspace-pages update.


## ADR-042 — Session recovery reads a bounded past (2026-09-12)

Work: DREAM-057. A live request repeatedly read its own stored tool calls/results,
creating more records on every retrieval. Increasing the round limit would extend
that loop. Past-session recall now excludes the active session before limiting
results. Explicit current-session recovery is bounded in SQL through the latest
durable user message; earlier requests remain recoverable, while the current
request's generated records cannot feed back into that same request.

This relies on the Engine's serialized user-request lifecycle: queued corrections
are logged when processing starts. It does not add live steering or change that
queue. A future concurrent writer for the same session must carry an immutable
request boundary instead of advancing it while older requests run. Archived raw
records are retained unchanged, including interrupted sessions and past retrieval
transcripts. Pages use up to 12,000 Unicode code points to avoid excessive small
reads; window snippets remain compact and continuation guidance is conditional.

The tool-round ceiling remains 100 by default. Its message reports the configured
number and distinguishes rounds from individual calls, context and elapsed time.
Fixture evidence establishes the retrieval boundary, not model task quality.


For the verified MachX missing-sidecar rejection, loaded-image readiness is an
observed negative fact distinct from architecture vision support. Shared main and
delegated handling disables images and labels retained pixels as uninspected,
without retrying the failed request. Reconnect/model change resets the observation;
performance/effort changes do not. Generic server errors do not disable vision.
This preserves truthful failure and text continuation without loading hardware or
claiming that file presence or architecture metadata establishes endpoint readiness.


## ADR-043 — Private recorded skills and bounded specialist guidance (2026-09-12)

Work: DREAM-060. Recorded skill installation previously targeted the source-tree
`skills/` directory, which is outside the default curated catalog and belongs to
the shareable harness. New reviewed installs target `DATA_DIR/skills`, matching
private editor overrides. They remain disabled until explicitly enabled. The
installer stages the package outside discovery and validates cited evidence against
the recording manifest and directory before publishing it. Failed validation leaves
no installed package. Existing recordings and installs are not migrated or deleted.

Computer-use and Blender-animation are shipped source-authored workflows, not
recorded behavior or trained capabilities. They join the default catalog with
conservative task routing and explicit invocation. The per-request maximum stays
at two workflows and 4,000 characters; supporting references load separately.
The catalog's ten-entry wake index remains below its 1,600-character check. The
entire on-disk entrypoint corpus check grows from 14,000 to 16,000 characters,
without expanding request injection. Capability hints are not permissions and
cannot provide missing image or desktop controls. Alternative: external-only
skills would require manual discovery for these routine known failure modes.

Recording is still an explicit user operation. Sampled screen frames do not log
exact actions; uncertain steps require labeling and review. There is no automatic
replay, background learning, model load or measured Fable/DeepSeek uplift here.
See the teaching guide and dated polish/skills update for verification and limits.


## ADR-044 — Observed computer targets and manual teaching evidence (2026-09-12)

Work: DREAM-061/062/063/064. Shared computer tools enter the existing registry so
SDK, CLI bridge and compatible HTTP backends receive the same action contract.
Controllers belong to ToolContext and close at Engine cleanup. Browser contexts
are separate from Studio/native browser sessions; desktop attaches an explicit X11
window. Observation is read-only; opening/actions/closing retain ordinary mutating
permissions. An OS window or network browser is not confined by the Bash sandbox.
No blanket desktop permission, global input hook or Wayland control is added.

Actions require a fresh, single-use observation. DOM destination/focus/viewport
checks and retained node handles catch known stale browser actions. Failed refresh
invalidates the prior token before replacing handles. Desktop checks cover focus,
window title/geometry and immediate dispatch focus; these cannot make X11 input
atomic against another user or isolate an on-screen crop from overlays. Screenshots
are immutable private observation files. Bounded serialized JSON and element paging
prevent normal transport truncation from corrupting the response. Dispatch is not
reported as task success, and uncertain operations are never automatically replayed.

Teaching evidence uses explicit human annotations tied to frames/time, app/version
and before/action/after/outcome. The private sidecar has revision checks and bounded
fields; it does not replace original recordings. Drafts may cite preserved evidence
IDs; model-facing draft creation cannot invent a matching human confirmation.
Large escaped fields use lossless field paging with revision checks. Failed saves
or delayed reads preserve newer editor state. This improves reviewability without
claiming screenshots capture exact input events.

The approved Claude exercise uses fresh tool-disabled contexts and synthetic action
plans replayed in a deterministic simulator. Both baseline and assisted arms scored
8/8: no demonstrated uplift, no actual computer/Blender task performance claim.
Local-model trials remain deferred. Navigation/density/catalog preferences affect
presentation and discovery only; they do not grant execution or trigger inference.
The dated six-priority update records actual tests, failures and native limits.


## ADR-045 — Shared pointer paths for visual work (2026-09-12)

Work: DREAM-061/014. DOM-only clicks cannot operate a drawing canvas. Add browser
coordinate clicks, two-point drags and 2–128-point strokes; support scoped X11
pointer paths through the same adapter. Coordinates are bounded integers in the
observed viewport or native window, with one-use observation IDs. Browser pointer
actions check observed pixels, destination state and retained hit targets before
pressing. Per-point checks detect viewport/navigation changes. Native checks cover
focus, title and geometry, with the existing non-atomic X11 limitation.

Button release remains controller-owned during failure and repeated cancellation.
Short action-dispatch bounds do not impose a runtime budget on an agent task.
A fresh review reproduced a mouseenter handler replacing the intended control;
retaining and rechecking the actual hit node repaired that race. The focused
integration gate passed 97 tests. These checks do not establish that every moving
web interface is usable: live JS Paint runs have exposed stale refusals from
noninteractive toolbar help text, which are being investigated separately.

The paired Paint diagnostic uses a private export bridge that copies the existing
canvas into PNG. It cannot synthesize artwork and is identical for both arms.
This is an evaluation accommodation, not a new production export API or proof of
native Save As. Blender stays on a separately identified, isolated process and
GUI-only task track. Public docs retain reference links and redacted findings;
raw model traces, screenshots, credentials and trial artifacts stay private.

Follow-up within this implementation: observations use bounded best-effort sampling
before issuing a fresh capture. Readiness is advisory: continuously animated or
missing-animation-callback pages still return coherent visual/DOM evidence when
obtainable. Later pixel/state guards remain unchanged. Whole browser key chords
are validated before pressing anything, with documented aliases, and all attempted
keys are released through failure/cancellation cleanup. This addresses actual
Paint delayed-help and held-Control reproductions. Quiet sampling cannot promise
future stability. Final66control/53integration checks and independent17probes
passed. Running diagnostic model sessions retain their original imported module;
a verified frozen-module launcher preserves that version for their counterpart.


## ADR-046 — Qualify timing and coordinates through real application behavior (2026-09-12)

Work: DREAM-061/014. Preserve omitted-duration pointer behavior and add optional
bounded paced interpolation for time-dependent brushes. Actual JS Paint marks
confirmed that endpoints alone do not create continuous airbrush coverage. Requested
motion time excludes backend/check overhead; more paint does not establish blending
or better artwork. Validate plans before input and retain release/state guards.

Native X11 typing at1ms lost repeated digits in actual Blender4.0.2. Use12ms and
128-character chunks, checking title/focus/client geometry before and after chunks.
Retain4000-character support with a derived per-action deadline (at most95s), not a
new agent runtime ceiling. Cancellation waits for the bounded in-flight chunk to
finish and restore its generated keys/modifiers; later chunks do not start. Command
or X-server failures can still leave uncertain partial text. Callers need sufficient
transport headroom and must not replay an uncertain request.

Decorated-window xdotool geometry disagreed with its own correctly translated
mouse coordinates. Obtain absolute client coordinates from xwininfo underLC_ALL=C;
require that dependency and reject missing/malformed geometry. Do not silently fall
back to the reproduced incorrect origin or guessed decoration offsets. Native screen
crops may still include overlays; this is not atomic desktop isolation.

Final native control gate99passed, actual isolated Blender/4000-character/cancel
checks passed, and independent27native+1browser probes passed on unchanged hashes.
Some extra restricted-runtime fixtures stalled and were interrupted, recorded as
incomplete. The Opus continuation uses separately pinned reviewed bytes, fresh logs
and existing saved work. Its changed model and controller prevent causal skill-uplift
attribution. Original baseline files and findings remain intact.


## ADR-047 — Observe run ownership and retain explicit continuation intent (2026-09-13)

Work: DREAM-065. Saved running status survived driver death and did not establish
current ownership. CLI and Projects now use one bounded canonical ledger reader
and observe the existing kernel lock. An acquired shared lock fences its inode
during inspection; a replaced path invalidates the ownership result. Anchored
no-follow directory traversal prevents symlink ancestors from supplying authority.
Unknown ownership stays unknown. This avoids stale snapshots and elapsed-time
heuristics without adding a supervisor or replaying uncertain work.

Only a final standalone worker STATUS/NEXT block outside fenced examples controls
continuation. NEXT survives a checkpoint as attributed, unverified intent. The
implicit twelve-iteration ceiling is removed; users may still set a positive
invocation limit. Other limits, Stop, permission waits and reconciliation remain.
Independent tests cover process death, lock replacement, malformed nested data,
symlink ancestry and explicit resume limits. Ownership is not productive progress.

## ADR-048 — Validate native effort and qualify contracts separately from models (2026-09-13)

Work: DREAM-066. Explicit effort could be accepted but omitted by compatible HTTP,
or forwarded despite a conflicting known ladder. Preserve exact current native
labels before adapter aliases and reject unsupported settings before mutation.
Unknown support remains unknown; a permitted request is not a provider guarantee.
Model changes clear capability reports and revalidate retained explicit effort.
Malformed stored choices remain private while status explains how to recover.

A fixed offline qualification runner records outcomes and listed source hashes.
Skipped, unavailable and failed checks cannot become successful qualification.
Separate wheel builds, synthetic privacy canaries and installed CLI checks test
packaging. Reused dependencies and unavailable offline distributions remain stated
limitations. This provides repeatable contract evidence without benchmarks or
claims of live model quality, owner acceptance or production perfection.

## ADR-049 — Save scoped corrections at HTTP request boundaries (2026-09-13)

Work: DREAM-069. The old after-turn prompt queue could not correct an ongoing
ordinary HTTP chat. An explicit Steer action now targets one session and turn,
stores a private receipt, captures attributed user text, and inserts it only
after the complete current response/tool batch. Finalization closes intake before
verifier, filing or salvage work. Original requirements and ordered corrections
remain protected during the active turn; the review receives both.

Pending/included/submitted/retained describe observable delivery stages, not model
compliance or guaranteed non-execution. Unchanged failed-send drafts retain their
receipt and target; stale targets cannot silently enter another worker's queue.
Stop retains recoverable text without replay. Native agents, workflows and loops
keep the existing explicitly labelled in-process queue. This avoids concurrent
adapter calls and unqualified native input injection. Synthetic HTTP, durable
storage and CPU Chromium fixtures cover delivery, cancellation and retry boundaries.

## ADR-050 — Separate output evidence from a successful response (2026-09-13)

Work: DREAM-068/071/074. Scheduled verifier findings previously accompanied a
successful terminal result, allowing consumers to ignore known unmet checks.
The HTTP result now carries scoped review status and makes a non-passing scheduled
review incomplete. No-review chat remains successful when its transport succeeds;
other terminal failure reasons retain precedence. No automatic repair generation
is introduced. A verifier PASS remains reported evidence, not owner acceptance.

Image delivery requires supported-format bytes, bounded container/pixel checks
and complete decoding before import. GIF container validation distinguishes a
truncated animation from a legitimately shorter one. Video stream metadata and
visual polish remain separate checks. Passive media readiness reports configured
scope and host lookup provenance; it never equates those with sandbox execution or
endpoint image acceptance.

Selected source hashes captured at package import and compared during status help
diagnose stale processes. Unknown source reads stay unknown; this is not an
attestation of all loaded code. These changes use existing result/storage/status
paths and avoid a second execution or memory framework.

## ADR-051 — Prepare prompts separately from task execution (2026-09-13)

Work: DREAM-075. Prompt drafting should be usable beside Chat without executing
an unfinished request, replacing a user's draft or changing model permissions.
The optimizer therefore returns editable text and optional material questions.
Use in Chat transfers the draft and registered attachments; Send remains explicit.
Quick structure is deterministic and separate from model optimization.

Model requests use the current provider/model through an isolated, tool-free
transport owned by the App's existing serialized input and Stop lifecycle.
Session, workspace, provider, model and profile identity are pinned and checked
before dispatch. The authenticated route validates registered upload IDs and
rejects stale completion. The browser also checks draft revisions and context.
Evaluator environment overrides cannot silently select a different provider.

Only bounded selected-text excerpts enter the optimizer; unsupported attachments
remain labelled metadata. Drafts stay in the page and files use existing private
uploads. No new memory database, automatic model loading, task execution
or global prompt history is introduced. This favors a small inspectable workflow
over automatic rewriting of every message. Reasoning guidance is text; actual
effort remains an independent model control. Fixture checks establish contracts,
not semantic preservation or demonstrated model-quality improvement.

## ADR-052 — Save project evidence before optional memory processing (2026-09-13)

Work: DREAM-072. Project continuity must not depend on a model producing a summary
or an embedding call returning. Projects therefore drafts bounded, attributed
handoffs from already saved session excerpts. Missing facts remain UNKNOWN;
assistant claims are not promoted to verified checks. The existing document editor
owns review, save, revision checks and explicit context inclusion. No new database,
automatic global memory policy or model-generated handoff is introduced.

Memory write tools opt into atomic Markdown replacement before SQLite commit and
skip synchronous embedding. Lexical rows and manual links commit together. If
file replacement fails, the database transaction rolls back; if database commit
fails afterward, the authoritative file remains recoverable and the error states
that partial outcome. Optional vector indexing uses existing backfill. Edits
invalidate stale vectors, and late embedding results must match the current row
identity and content. Backfill counts successful installations rather than attempts.

Existing internal callers keep their embedding behavior. Long-term memory files
flush contents before replacement but do not fsync the parent directory, so power-
loss persistence of the rename is not guaranteed. Optional model consolidation
remains separate and may take minutes. Shutdown wording distinguishes that process
from the already saved conversation.

## ADR-053 — Require execution evidence for completion (2026-09-14)

Work: DREAM-076. A successful response is insufficient when its latest delivery
attempt failed, its scheduled verifier never inspected the output, or a provider
reported incomplete media. The HTTP adapter records delivery attempts separately
from review and makes unresolved delivery failures incomplete. Verifier PASS
requires successful required inspection calls and submission of inspected image
content to a successful verifier request. Metadata survives tool-repeat advice.
These prerequisites do not establish artistic quality or owner acceptance.

Explicit incomplete ComfyUI history stays pending before output import. Legacy
status-absent history keeps its existing output-based behavior. Gemini completion
requires the protocol's explicit success status; a zero exit cannot fill in missing
terminal evidence. Tool results retain their IDs for UI activity correlation.
Errors, usage and partial text remain available. No implicit repair generation,
extra model budget or automatic replay is introduced.

Bounded project recovery reserves separate user and assistant excerpts, preserving
recent corrections despite tool and assistant floods. Timing adds fixed-category
observed tool outcomes and review status without collecting argument/result text.
Unknown flags remain unknown. Counts describe observed events, not deduplicated
calls, measured recovery or an automatic model-selection policy.

## ADR-054 — Apply privacy exclusions to bundled skills (2026-09-14)

Work: DREAM-076. Synthetic builds demonstrated that directory force-inclusion
bypassed exclusions for private files inside skills. Replace force-inclusion with
ordinary selected source paths and map `skills` into `dream/resources/skills`.
This follows [Hatch's file-selection contract](https://hatch.pypa.io/latest/config/build/).
Exclude private state at every depth while preserving the `dream/memory` Python
implementation. A second build layer is unnecessary; the existing wheel builder
can enforce the intended selection.

The independent repaired build excludes all 28 synthetic private canaries while
retaining 11 skill documents and memory code. The archive audit also rejects file
ancestors that collide with child paths in either insertion order. Installed byte
comparison and a clean hash-locked dependency environment remain separate checks.
Neither exclusions nor layout checks certify arbitrary source content or Git
history free of personal information.

## ADR-055 — Retain CPU contracts in repeatable qualification (2026-09-14)

Work: DREAM-076. One-off regression evidence can disappear on reboot and leaves
future maintainers without the same checks. Extend the existing fixed qualification
groups with delivery, continuity and package fixtures, and include their primary
source files in the before/after fingerprint. Keep native UI and real installation
qualification separate; fixture coverage is not a complete release audit.

The runner uses private HOME/XDG directories, removes inherited Python import
overrides, disables optional memory processing and hides accelerator devices in
the child. The parent environment stays unchanged. This reduces accidental owner
configuration and resource use; it is not an OS sandbox for modified test code.
Fixture watchdogs govern tests only and do not set model runtime limits. Reports
retain failed, skipped, unavailable and changed-source outcomes as such.

## ADR-056 — Report memory replacement and directory durability separately (2026-09-14)

Work: DREAM-076, following ADR-052. Flushing file contents does not also confirm
the containing directory entry; Linux documents that separate requirement in
[fsync(2)](https://man7.org/linux/man-pages/man2/fsync.2.html).
The memory writer now synchronizes the resolved directory ancestry on each
attempt, flushes the new file before replacement, and synchronizes a held parent
directory descriptor afterward. Repeating ancestry checks covers a retry after
an earlier attempt created directories but failed to synchronize them.

A failure after replacement is a distinct partial-result error. The durable-first
store path still commits matching keyword rows, manual links and stale-vector
invalidation before raising that error outside its transaction. Otherwise a naive
fsync insertion would leave new Markdown paired with old search content while
reporting a generic failure. If the database commit also fails, the message states
both outcomes and identifies synchronization recovery. Pre-replacement failures
retain rollback and the old file. Versioned tool retries reject stale versions.

This adds directory I/O and requires permission/support for directory descriptors
and fsync. Unsupported paths fail explicitly. The scope covers memory Markdown
and its derived MEMORY.md writes, not deletion or migration source unlinks.
Independent injected-failure tests establish ordering, cleanup and result handling;
they do not prove actual power-loss survival or every filesystem's behavior.

## ADR-057 — Keep Studio Stop attached to the owning turn (2026-09-14)

Work: DREAM-076. A terminal approval reader temporarily owns the SIGINT target.
When Studio reconnects during that read, cancelling the same target answers the
prompt with a denial and lets the outer turn continue. The UI reports an interrupt
even though it did not stop the turn. An actual-App probe reproduced this boundary.

Keep a separate target for the outermost active `_run_turn`, restored on every
exit path. Studio Stop prefers that target; the existing inner target remains the
fallback outside a turn. The signal handler still targets the terminal reader,
preserving Ctrl-C's established approval-denial behavior. This repairs the existing
Stop contract without changing permission policy or enabling automatic continuation.

Two author regressions failed before repair while the SIGINT control passed.
After repair, independent App probes and 26 interrupt/cleanup regressions passed,
including nested turns, reader release and a subsequent turn. A full native
App/Engine reconnect and real provider cancellation still need separate evidence.

## ADR-058 — Explicit recall alternatives and bounded task admission (2026-09-14)

Work: DREAM-022/070/073. Keyword-only retrieval misses some requests with different
wording. A global synonym table would encode unproven equivalences; loading another
model is outside this CPU-only pass. The recall tool instead accepts up to three
caller-written lexical alternatives, validates query/limit bounds and attributes
alternative hits. It deduplicates results and activates each returned memory once.
The top primary direct hit stays first; alternative hits can precede broader
primary matches. Valid primary-only calls preserve their existing retrieval path.
The unchanged public keyword fixture is evidence of that limit, not semantic uplift.

Blank recall queries are now explicit errors; low-level blank-query browsing remains
unchanged. Conventional quoted task examples and negated actions no longer drive
implicit skill selection. Shortened skill guidance requires opening the full
entrypoint before following it. These are bounded admission heuristics, not language
understanding or new execution permissions. Independent probes and focused tests
are recorded in the CPU outcomes handoff, including initial failed gates.

## ADR-059 — Compare whole-task evidence without applying settings (2026-09-14)

Work: DREAM-073/066. Protocol success and first-token latency cannot identify useful
settings. The offline comparator consumes an explicitly selected bounded bundle,
regrades fixed artifact records, checks workspace preservation and saved-content
hashes, and compares whole-task elapsed time only across matched samples and scopes.
It keeps unknowns and failures visible. Full model, harness, environment and fixture
identities prevent comparisons based only on a display name. No session discovery,
private export, provider call or settings mutation is attached to this command.

The alternative was automatic online experimentation or ranking all successful
turns by response speed. Those would require task acceptance, cost authority and
representative trials that this pass does not have. The chosen interface supports
future evidence collection while preserving explicit overrides. Its remaining
limits are material: provenance, effective settings and whole-task duration are
caller supplied, omitted trials cannot be detected, and fixed CPU fixtures do not
measure general model quality. Descriptive sample averages are not an optimum.

## ADR-060 — Durable ordinary-chat outcomes (2026-09-14)

Work: DREAM-065. A real Engine CPU probe showed that provider errors and cancellation
were visible to the live UI but absent from the saved conversation. Assistant text
alone could not distinguish a completed reply from an interrupted operation.
Ordinary chat now writes a unique started/terminal pair through the existing durable
transcript API. Protocol completion is recorded only after owned iteration and
cleanup succeed. Missing, stale, malformed or unmatched status remains unknown;
no process liveness, task correctness or permission to replay is inferred.

Projects reads bounded status rows from the same SQLite snapshot as its transcript
or handoff, derives fixed explanatory text, and retains explicit continuation.
Stored explanations are not trusted as instructions. This uses existing rows rather
than adding a database or migrating legacy records. Hard crashes may leave only the
started record; partial text is best-effort bounded evidence, not an atomic record
of every streamed token. Hosted-provider cancellation and native process-crash
qualification remain separate from synthetic Engine tests. Exact verification is
recorded in the chat recovery/outcome handoff.

## ADR-061 — Bounded failure signals and static delivery evidence (2026-09-14)

Work: DREAM-053/067/068. Raw failure text helps the local operator, but copying it
into a diagnostic export can expose project data. Timing now retains fixed
message-signal categories and event counts. The UI reconstructs the download from
allowlisted categories instead of serializing the source event. These signals are
heuristics, not root-cause proof, and never authorize retries or permission changes.
The alternative, exporting raw transcripts with regex redaction, has a larger and
less predictable disclosure surface.

Repeated identical failures under changed arguments receive advisory recovery
text; unknown effects still require inspection. Static page dependencies and PPTX
package relationships are checked before clean delivery/format claims. Presence
and structure remain separate from playback, factual accuracy and visual quality.
A bounded hosted-adapter qualification uses synthetic private sessions; passing it
does not establish general model quality or remote cancellation after client Stop.
