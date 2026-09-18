# DREAM-029 — Guided workflows, file attachments and harness reliability

Recorded: `2026-09-09T19:42:08-05:00`
Work items: `DREAM-029`
Outcome: `implemented`
Actor: Codex lead; desktop_startup (skills), media_usability (file readers),
polish_review (backend reliability, independent review and workflow trial).

## Request

The owner requested a cohesive harness, removal of wasteful skills, short processes
that help weaker models, broad file handling and ChatGPT-like ease of use. Earlier
feedback also asked to retain Studio. Acceptance is in the
[master register](../MASTER_PLAN.md#dream-029-acceptance--optimized-harness-and-skills).
Implementation is complete; universal model improvement, performance, owner
acceptance and production release are not established by these checks.

## Changes

Eight default workflows: coding, research, writing, documents, data-analysis,
media, library and verifying. A deterministic matcher supplies at most two bodies
and 4,000 characters before inference, with visible notices, explicit selection
and opt-out. Raw user requests remain distinct from harness-added guidance.
Shared client catalogs are opt-in and searchable without entering wake context.
Enabled Dream plugins retain independent controls; an empty DREAM_SKILL_DIRS
suppresses curated/shared file roots but does not disable enabled plugin skills.
Disabled plugin children stay visible in settings. Wheel resources also work when
DREAM_ROOT points elsewhere. See extensions and
[ADR-010](../DECISIONS.md#adr-010--curated-task-guidance-and-bounded-file-intake-2026-09-09).

Skill audit: answer-with-precision and unslop were merged into writing; grilling
and grill-me removed duplicate/compulsory questioning and another client's tool
syntax. create-design-system, interactive-prototype, wireframe and make-tweakable
became one coding Studio reference. make-a-deck, export-pptx, save-as-pdf and
save-as-standalone-html became document export references. animated-video became
media operations; recovering merged into verifying. Library was shortened while
preserving identity/version instructions. illuminati-handshake remains at its
historically linked path as an optional capability-lab package. Global skills,
learned skill storage and owner configurations were not deleted.

Observed size: previous 17 entrypoints plus their 15-entry discoverable index were
60,761 UTF-8 bytes / 9,289 words. Eight default entries plus index are 11,203 bytes /
1,627 words (about 82% fewer bytes). The default index itself is 829 bytes / 813
characters, with a 1,600-character cap. All nine retained entrypoints including the
optional package total 11,826 bytes. These are text measurements, not token or
model-quality benchmarks. References load only when needed.

Desktop chat now accepts up to eight 25 MiB attachments through a button, drop or
paste. Chips show readiness and support retry/removal. Tab refresh retains text
and completed attachments; failed sends retain drafts. Upload transport and reads
are bounded, writes use exclusive no-follow descriptors, and attachment IDs are
validated against the selected workspace. Colliding uploads preserve both files.
Form upload failures no longer discard selected files or submit partial answers;
server events produce one user bubble. Studio is again named in navigation.
Truncated answers retain partial text and offer an explicit Continue action.
See the desktop guide.

read_file handles Unicode text/code/data, PDF text, DOCX, XLSX and PPTX extraction,
raster metadata and ZIP/TAR listings. Paging, truncation, missing dependencies,
corruption and unsupported formats are explicit. ZIP central-directory bounds
are checked before allocating ZipFile entries; Office XML and expanded content
are bounded. No OCR, macro execution, automatic archive extraction or audio
transcription is implied. Image attachments use the actual see tool; live relative
image paths cannot silently select another workspace's same-named file. Image reads
are bounded and threaded. See exact formats and limits.

HTTP tool priorities remain inside existing budgets; schema reveals expire next
user turn. Truncation, loop guards and exhausted subagent/tool rounds report
non-success while preserving useful partial output. The small-window schema
regression was fixed by removing redundant discovery descriptions, preserving all
names and native argument constraints. The actual full 8K schema set fell from
1,297 to 1,227 estimated tokens under the unchanged 15% ceiling. The complete-name
enum still grows with the catalog, leaving narrow 8K headroom. A fresh-process
AnyIO import-order failure in shared tool threading was also fixed.

## Validation

Evidence root: `/tmp/dream-harness-20260909-evidence`. All model-independent tests
used isolated fixtures; host execution was necessary for local socket/thread
wakeups, browsers and owned subprocess checks under this environment.

- Initial full locked suite: **8 failed, 1,984 passed, 35 skipped, 7 warnings** in
  200.97 seconds. Failures exposed plugin discovery, small-context schemas and
  obsolete collision/index assertions, plus an owned CLI disconnect timeout.
  Plugin/budget causes were fixed; assertions now check documented semantics.
  Host lifecycle rechecks passed (isolated disconnect and all 12 lifecycle tests),
  without changing lifecycle code or relaxing the trusted-interpreter gate.
- Final `uv run --locked --no-sync pytest -q --tb=short`: **1,996 passed, 35
  skipped, 7 warnings in 197.00s**, exit 0; full-suite-final.log. No application
  source changed afterward. A later test-only visual assertion is recorded below.
- Focused skills/extensions: 108 passed. File readers/native permissions: 80
  passed. Backend reliability: 134 passed, then 63 budget/backend checks passed.
  Final image/attachment checks: 11 passed; broader attachment/Engine/context
  integration: 15 passed before the added cross-workspace case. Earlier desktop
  focused suite: 31 passed. New failures were observed before their fixes.
- Final test-only visual check: browser attachment retry/refresh/Studio test
  passed (1 test, 1.57s) with added assertions for visible retained chat text and
  dismissed error banner at 420px. Screenshot inspected after its entrance
  animation; the saved user bubble and filename are visible.
- Native `/usr/bin/python3 tests/desktop_smoke.py --output <evidence>/native`:
  seven GTK/WebKit checks passed, including Studio render, real ask_frame click,
  reconnect, Browser rejection/recovery, narrow layout and clean quit. Screenshots
  were inspected. This native fixture did not perform inference.
- `uv build --offline --out-dir <evidence>/dist-final` built source and wheel.
  Wheel inspection confirmed nine skill entrypoints, attachment assets and reader;
  unpacked-wheel imports outside the checkout with relocated DREAM_ROOT found all
  eight curated workflows. No top-level private runtime directories were included.
  python-multipart became explicit; uv.lock changed dependency declarations only.
- Independent data-analysis forward trial used actual Dream read_file/write_file
  handlers and local Python CSV/Decimal on messy invoices. Correct known paid
  total 250.50; duplicate counted once with disclosure; missing amount remained
  unknown. Raw input was preserved and outputs reopened. Artifacts and limitations
  are in skill-trial/. This was an agent-led workflow trial, not weaker-model
  inference or a test of run_bash transport.
- No live model was available at port 11435. Read-only Qwen3 preflight refused
  startup: candidate-ppl PID 3726582 occupied about 30.4 GiB on each Arc GPU.
  No model was loaded and no unrelated process was stopped. Prior handoff PIDs
  are historical; recheck identity before any action.
- Graph service returned Transport closed (including a retry); focused source
  reads were used. A sandbox AnyIO wakeup stall and sandbox trusted-interpreter
  refusal were environment limits; host checks succeeded. One broad agent test
  attempt was interrupted during local tuning; the final locked suite supersedes
  that attempt. A trial's first uv invocation attempted dependency resolution and
  failed DNS; it was rerun with the installed interpreter. The first package-check
  script misread discover's tuple return and was corrected; the wheel was sound.

Tracking: `python3 scripts/check_project_tracking.py check --snapshot
/tmp/dream-harness-20260909-baseline.json` passed: 16 dated records and 87 changed
source paths. An initial record used an unsupported Outcome label; changing it
to the standard implemented status fixed the check without weakening its rules.
Final git diff --check also passed.

## Unfinished work

Live weaker-model quality and responsiveness remain unqualified while GPUs are
occupied. Document extraction does not establish rendering/visual fidelity. The
reader has explicit source/output/process bounds but is not an OS parser sandbox;
PDF helpers have no separate address-space limit. No paid provider, ComfyUI model,
new animation export or universal provider matrix was exercised in this pass.
No owner daily-use acceptance, commit, publication or release occurred.

All source contributors are stopped. Tests/native fixtures own only temporary
processes and clean up on completion. Existing dirty work was preserved: baseline
`/tmp/dream-harness-20260909-baseline.json` captured 421 hashes; source backup is
`<evidence>/before.tar`. Never blanket-restore that archive or HEAD. Three known
fixture session logs from an initial missing test isolation setting were moved to
`<evidence>/test-session-logs` only after matching their fixture contents; tests now
bind a temporary sessions directory. No private user history was removed.

## Next steps

DREAM-029: once resources are available, recheck GPU/process ownership and run a
bounded small-model task matrix through dream desktop: model load, attached PDF/
spreadsheet/image, workflow notice, tool arguments/results, truthful recovery and
saved artifact. Compare guided vs unguided task correctness, first-text latency,
round count and tool failures. Owner can then assess ordinary daily work. Keep
DREAM-028 latency, DREAM-023 media qualification and release/owner-acceptance items
separate in the master register. No new model load should bypass occupied-GPU
preflight. Start with `dream desktop`; choose a model and workspace, attach a file,
and use Studio for outputs.

## Files changed

- `START_HERE.md`
- `docs/desktop.md`
- `docs/extensions.md`
- `docs/files.md`
- `docs/project/CURRENT.md`
- `docs/project/DECISIONS.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-09-harness-quality.md`
- `docs/superpowers/plans/2026-09-09-harness-quality.md`
- `dream/config.py`
- `dream/core/backends/openai_compat.py`
- `dream/core/engine.py`
- `dream/core/system_prompt.py`
- `dream/core/tool_budget_schemas.py`
- `dream/files/__init__.py`
- `dream/files/reading.py`
- `dream/gui/server.py`
- `dream/gui/static/attachments.css`
- `dream/gui/static/attachments.js`
- `dream/gui/static/companion.js`
- `dream/gui/static/index.html`
- `dream/gui/uploads.py`
- `dream/skills/loader.py`
- `dream/skills/selection.py`
- `dream/tools/context.py`
- `dream/tools/installed_skill_tools.py`
- `dream/tools/native.py`
- `dream/tools/vision.py`
- `pyproject.toml`
- `skills/animated-video/SKILL.md`
- `skills/answer-with-precision/SKILL.md`
- `skills/coding/SKILL.md`
- `skills/coding/manifest.json`
- `skills/coding/references/studio.md`
- `skills/create-design-system/SKILL.md`
- `skills/data-analysis/SKILL.md`
- `skills/data-analysis/manifest.json`
- `skills/data-analysis/references/spreadsheets.md`
- `skills/documents/SKILL.md`
- `skills/documents/manifest.json`
- `skills/documents/references/exports.md`
- `skills/documents/references/formats.md`
- `skills/export-pptx/SKILL.md`
- `skills/grill-me/Skill.md`
- `skills/grill-me/openai.yaml`
- `skills/grilling/Skill.md`
- `skills/grilling/interface display name.txt`
- `skills/illuminati-handshake/SKILL.md`
- `skills/interactive-prototype/SKILL.md`
- `skills/library/SKILL.md`
- `skills/library/manifest.json`
- `skills/library/references/design.md`
- `skills/library/references/tools.md`
- `skills/make-a-deck/SKILL.md`
- `skills/make-tweakable/SKILL.md`
- `skills/media/SKILL.md`
- `skills/media/manifest.json`
- `skills/media/references/operations.md`
- `skills/recovering/SKILL.md`
- `skills/research/SKILL.md`
- `skills/research/manifest.json`
- `skills/save-as-pdf/SKILL.md`
- `skills/save-as-standalone-html/SKILL.md`
- `skills/unslop/SKILL.md`
- `skills/verifying/SKILL.md`
- `skills/verifying/manifest.json`
- `skills/wireframe/SKILL.md`
- `skills/writing/SKILL.md`
- `skills/writing/manifest.json`
- `tests/test_chat_attachments.py`
- `tests/test_curated_skills.py`
- `tests/test_desktop_chat.py`
- `tests/test_document_reading.py`
- `tests/test_guidance.py`
- `tests/test_harness_backend_quality.py`
- `tests/test_local_subagents.py`
- `tests/test_loop_guard.py`
- `tests/test_openai_truncation.py`
- `tests/test_runtime_profiles.py`
- `tests/test_salvage.py`
- `tests/test_schema_budget_real.py`
- `tests/test_skill_catalog.py`
- `tests/test_studio_routes_phase6.py`
- `tests/test_task_guidance_integration.py`
- `tests/test_tool_context_standalone.py`
- `tests/test_tool_skill_reliability.py`
- `uv.lock`
