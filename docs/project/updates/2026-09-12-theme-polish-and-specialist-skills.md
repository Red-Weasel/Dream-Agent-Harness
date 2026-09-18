# DREAM-059 / DREAM-060 — Working-theme polish and specialist skills

Recorded: `2026-09-12T22:35:34+00:00`
Work items: `DREAM-059, DREAM-060`
Outcome: `implemented`
Actor: Codex lead; Astra theme_polish, workflow_skills and recorded_skill_install authors;
independent fresh_skill_judge and fresh_theme_judge reviewers.

## Request

Polish the actual working harness, preserve Dream's eclipse/figure identity, rank
improvements by expected impact, and investigate teaching other models reusable
computer-use and Blender procedures through the recorder. Acceptance is local
implementation and proportionate verification, not model parity or owner acceptance.
The existing dirty tree was preserved under the session hash baseline.

## Changes

Polished shared panels, typography, helper text, focus/primary accents, artwork
banner proportions and narrow control layouts. Skill actions now follow the editor
instead of covering it. Navigation has one selection owner across sidebar/top row,
including Escape, drawer events and classic Projects/Skills. Preserved drafts,
quiet/classic modes and artifact content. No new raster asset was generated.

Added packaged computer-use and blender-animation workflows with five progressive
references, supported-tool routing and explicit invocation. Ten curated workflows
now fit a 1,092-character wake index. The request cap remains two workflows and
4,000 characters; both specialist entrypoints fit together. The on-disk corpus
check increases from 14,000 to 16,000 characters, reflecting added bundled content.
Skills teach current-state actions, recovery and artifact checks; they neither add
missing tools nor establish measured uplift.

New reviewed recording installs stage outside discovery, validate cited frames and
publish under private DATA_DIR/skills, disabled. Original recordings remain intact;
invalid evidence leaves no partial install. No migration or recording was performed.
The teaching guide distinguishes sampled frames from action logs, provides explicit
record/analyze/review/install/enable steps, and ranks six suggested improvements.
See [ADR-043](../DECISIONS.md) and [guide](../../public/teaching-skills.md).

## Validation

Observed on 2026-09-12, model-free isolated fixtures:

- `timeout 90 .venv/bin/python -m pytest -q tests/test_specialist_skill_routing.py tests/test_curated_skills.py tests/test_task_guidance_integration.py tests/test_skill_loader.py tests/test_skill_catalog.py tests/test_learning_workflows.py --tb=short`: **104 passed in 1.45s**. Log `/tmp/dream-polish-skills-python.log`.
- `timeout 120 .venv/bin/python -m pytest -q tests/test_theme_polish.py tests/test_workspace_navigation_state.py tests/test_workspace_library_ui.py tests/test_workspace_design.py tests/test_studio_controls.py tests/test_feed_scroll.py tests/test_workspace_atmosphere.py --tb=short`: **53 passed in 51.30s**. Log `/tmp/dream-polish-skills-browser.log`.
- `uv build --wheel --offline --out-dir /tmp/dream-polish-wheel`: passed using the host cache. Wheel has 264 entries; all nine specialist package files and six modified UI files match source bytes. Named private runtime/learned-package paths absent. This is a package-layout check, not a full privacy or clean-install audit. Evidence `/tmp/dream-polish-wheel-verification.json`.
- Lead inspected final Skills/editor screenshots at 1280/390; fresh reviewer inspected Skills at 390/720/1280 and narrow Runtime controls. Author inspected additional working-page and Controls captures. Evidence `/tmp/dream-polish-*.png` remains local. Fixture screenshots do not establish native owner acceptance.
- Independent skill review exercised three paper scenarios: changed viewport, uncertain save, and existing frames with failed encoding/missing vision. Found relative frame paths needed explicit directory joining; both references corrected. No measured model transfer follows.
- Independent theme review found Escape and classic library selection mismatches. Both were reproduced as failures, repaired, and covered in the final browser gate. Red logs `/tmp/dream-navigation-escape-red.log` and `/tmp/dream-navigation-classic-red.log`.

Earlier failures preserved: initial specialist routing had 7 failures before catalog
integration; the old eight-skill corpus limit failed after integration. Author
fixture setup used the wrong Learn label once; root navigation test initially
omitted a required imported fixture. Sandbox runs could not use the uv cache/bind
fixture ports or qualify the synthetic extraction Python. Host-isolated fixture
runs resolved those environment limits; tests were not weakened to bypass them.
The recorder review's sandbox run was 8 passed/1 environment failure; the complete
recorder suite passes in the final host gate. Graph-first root calls repeatedly
returned Transport closed, so focused source reads followed; reviewers' graph
access worked. An end-snapshot filename was already occupied; a new unique final
snapshot was created without overwriting the earlier evidence.

Source baseline `/tmp/dream-polish-skills-baseline.json`; final snapshot
`/tmp/dream-polish-skills-final-snapshot.json`. Initial tracking found the current-state timestamp preceded this new handoff;
timestamp corrected. Final snapshot tracking check passed for all 30 changed paths.

## Unfinished work

None within the implemented polish/package scope. Native daily-use acceptance,
actual recorder demonstration with an expert agent, reliable image/desktop tool
availability on each endpoint and Fable/DeepSeek skill transfer remain unverified.
No inference, model load, Blender render, owner capture, restart, publication or
memory export occurred. All fixture processes finished; no task-owned live worker
or recording remains. Existing private data and pre-existing dirty work are intact.

## Next steps

Owner can inspect after normal Dream restart and invoke either specialist skill.
For subsequent teaching, choose one disposable task and explicit recording; review
observations/inferences before installing/enabling its private draft. The ranked
suggestions are proposals, not authorization to run a benchmark or alter external
services. Further model qualification remains DREAM-014/015. Reopen DREAM-059/060
only for observed defects; do not claim production perfection or owner acceptance.

## Files changed

- `docs/extensions.md`
- `docs/project/CURRENT.md`
- `docs/project/DECISIONS.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-12-theme-polish-and-specialist-skills.md`
- `docs/public/teaching-skills.md`
- `dream/config.py`
- `dream/demonstrations.py`
- `dream/gui/static/companion.js`
- `dream/gui/static/controls.css`
- `dream/gui/static/library.css`
- `dream/gui/static/theme.css`
- `dream/gui/static/workspace.css`
- `dream/gui/static/workspace.js`
- `dream/skills/selection.py`
- `pyproject.toml`
- `skills/blender-animation/SKILL.md`
- `skills/blender-animation/manifest.json`
- `skills/blender-animation/references/demonstrations.md`
- `skills/blender-animation/references/runtime.md`
- `skills/blender-animation/references/scene-checks.md`
- `skills/computer-use/SKILL.md`
- `skills/computer-use/manifest.json`
- `skills/computer-use/references/demonstrations.md`
- `skills/computer-use/references/tool-routes.md`
- `tests/test_curated_skills.py`
- `tests/test_learning_workflows.py`
- `tests/test_specialist_skill_routing.py`
- `tests/test_theme_polish.py`
- `tests/test_workspace_navigation_state.py`
