# DREAM-077 — Readable model loading picker

Recorded: `2026-09-14T22:40:22.768195+00:00`
Work items: `DREAM-077`
Outcome: `implemented`
Actor: Codex.

## Request

Make the model loading dropdown much wider so the full model label is readable;
inspect whether available local models reach it. Preserve the dirty tree.
Baseline: `/tmp/dream-model-picker-20260914.json`, 787 source hashes.

## Changes

The native onboarding panel now fills available horizontal space. Its model cell
wraps long labels with word/character wrapping instead of ending at an ellipsis
and a 38-character width hint. The 480-pixel wrapping hint allows the existing
720-pixel minimum window width. No discovery or loading behavior was changed.
Operator documentation describes the wider dropdown and wrapping.
Graph search located onboarding and discovery. An initial file-filter search
returned no matches; graph text search and exact snippets resolved the symbols.

## Validation

Observed on this date, existing project `.venv` and system Python GTK3:

- Actual `scan_models()` found five choices: DeepSeek R1 Distill Qwen 1.5B,
  DeepSeek V4 Flash Vision experimental, DeepSeek V4 Flash 0731, GLM 5.3 Flash
  Q3_K_XL and GLM 5.3 Flash Q4_K_XL. `python -m dream.desktop.startup catalog`
  included all five. Scans inspect filesystem metadata only. Mounted roots are
  bounded and the scanner covers GGUF weights, not every model format/location.
- Isolated native Onboarding fixture used the actual long DeepSeek Vision
  label. At window width 1400, the picker increased from 374 to 1231 pixels.
  Final width 720 gave a 551-pixel picker with the entire label wrapped and no
  ellipsis. Wide and narrow screenshots were visually inspected.
  Temporary script and images: `/tmp/dream-picker-check.py`,
  `/tmp/dream-picker-before.png`, `/tmp/dream-picker-after.png`,
  `/tmp/dream-picker-narrow.png`. Menu opening also exercised; programmatic popup
  emitted GTK's no-trigger-event warning. No session or model started.
- Initial unconstrained GI import selected GTK4 and failed its init signature;
  explicit GTK3 resolved this. Sandbox display access was unavailable; approved
  local-display checks succeeded. Initial sandbox pytest run stopped progressing
  after three dots and was interrupted, exit130; cause not proven.
- With local socket access, `.venv/bin/pytest -q tests/test_models.py
  tests/test_model_scan_bounds.py tests/test_desktop_startup.py -k
  'not attach_passes_workspace'` passed58, deselected1 in5.37s. The deselected
  test was then run explicitly and passed1 in0.33s. Total59 passed, no remaining
  test exclusions in these three files. No full-suite rerun for this layout edit.
- `python3 scripts/check_project_tracking.py check --snapshot
  /tmp/dream-model-picker-20260914.json` passed: 88 dated records, five changed
  source paths. `git diff --check` passed.

## Unfinished work

No remaining implementation within scope. Owner's running Dream was not restarted;
reopen it to load the native Python change. Native checks used an isolated panel,
not the owner's active session. The scan proves those five choices are returned,
not that every possible model on the machine was found or can load successfully.
No model load, GPU workload, publication or owner acceptance performed.
All task-owned test processes and temporary windows exited.

## Next steps

Owner reopens Dream normally and checks the dropdown under DREAM-077. If a specific
model remains missing, compare its path and format against configured GGUF roots
and scan bounds. Preserve the dirty tree and take a fresh baseline before editing.

## Files changed

- `dream/desktop/onboarding.py`
- `docs/desktop.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-14-model-picker-width.md`
