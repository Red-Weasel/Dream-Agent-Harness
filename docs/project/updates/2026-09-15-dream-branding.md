# DREAM-078 — Original-inspired eclipse branding and chat

Recorded: `2026-09-15T19:50:53-05:00`
Work items: `DREAM-078`
Outcome: `implemented`
Actor: Codex. No delegated contributors.

## Request

Make Dream visibly reflect the supplied UI references: eclipse/wordmark logo,
circle-with-woman app icon, prominent artwork in empty chat, composer centered
at the bottom and messages covering the image while leaving an artwork border.
No recent-project or recent-run cards in Chat. Later supplied original eclipse JPG
and cosmic memory image refine the artwork identity.

## Changes

Added a dedicated final presentation stylesheet and an original-inspired eclipse
SVG logo. Generated a new silhouette/eclipse background and transparent circular
app icon with the built-in image generator; copied both final PNGs into the repo.
The prior background and SVG remain intact. Branding notes
record the exact final prompts, measured asset sizes, references and consumers.

Chat now has a large welcome scene, non-submitting Research/Build/Create draft
actions, glass conversation cards with artwork margins, and a bottom composer.
Draft actions preserve existing input. Feed controls appear with conversation.
Quiet/classic modes and existing renderer/permission/artifact hooks are preserved.
The native header gets the eclipse lockup/tagline and selected navigation styling.
Native window icon, favicon and installer use the new circular icon. Updated the
existing per-user desktop entry at `~/.local/share/applications/dream-desktop.desktop`;
no other external user files were changed. Owner app/session was not restarted.

Graph tools were used first for discovery; broad symbol results and missing asset
references required focused source reads. Existing dirty files were read before
editing. Baseline snapshot has792 source hashes; changes listed below belong to
this session, not the entire dirty tree.

## Validation

- Browser checks use local Studio servers, fixture output and Chromium with GPU
  disabled. No model inference or owner data was used.
- Workspace design/atmosphere/theme/usability/navigation/feed suite:27 passed.
- New branding tests plus desktop chat regressions:18 passed. Together these are
  45 distinct passing checks, not a full-suite claim.
- After integrating final silhouette assets: branding/workspace design/atmosphere
  passed11 checks. After the native short-window fix: branding/feed passed12 checks
  in9.07seconds. These repeat some of the45 checks above.
- System Python `tests/test_desktop_design.py`:8 passed.
- Actual GTK/WebKit fixture runs: final1600-wide and720-wide windows, native logo,
  welcome scene, composer and short-window layout inspected. `/tmp/dream-native-brand-check.py`
  final report: stage3, errors[], owned_child_running false. No CSS load failure.
- Initial native probe used an artifact fixture and remained maximized, so it did
  not establish narrow-chat evidence. Replaced that probe with an empty-chat fixture
  and explicitly unmaximized. Inspection then found a clipped welcome heading in
  the short native viewport; compact welcome sizing and hiding empty-feed controls
  repaired it. Final screenshots show the full heading and composer.
- Screenshots: `/tmp/dream-brand-welcome-1600.png`,
  `/tmp/dream-brand-conversation-1600.png`, corresponding720/390 variants,
  `/tmp/dream-brand-native-wide.png`, `/tmp/dream-brand-native-narrow.png`.
- PNG inspection: background1672×941 RGB; icon1254×1254 RGBA with transparent corner.
  Original/source assets were not altered. Installed desktop entry now references
  the new icon. Application-menu visual cache refresh itself was not inspected.
- `.venv/bin/python -m hatchling build -t wheel -d /tmp/dream-brand-wheel` could not
  run: `No module named hatchling`. No dependencies installed; no wheel claim.
- Final tracking: passed,91 dated records and17 changed source paths checked
  against `/tmp/dream-branding-20260915.json`. `git diff --check` passed.

## Unfinished work

None within implementation scope. Owner visual acceptance is pending. No full
suite, package build, live inference or production-grade qualification is claimed.
DREAM-077 engine loading/reply failures remain separate and unchanged. All owned
fixture processes exited; no locks or running background work remain.

## Next steps

Owner: reopen Dream to pick up the native header/icon, then open a new conversation
to see the welcome. Review DREAM-078 against the supplied references. If future
packaging is requested, use an environment with the declared hatchling build backend
and verify the static branding assets in the wheel. Preserve this handoff; record
future feedback or acceptance in a new update.

## Files changed

- `dream/gui/static/index.html`
- `dream/gui/static/workspace.js`
- `dream/gui/static/workspace.css`
- `dream/gui/static/theme.css`
- `dream/gui/static/branding.css`
- `dream/gui/static/dream-mark.svg`
- `dream/gui/static/dream-eclipse-v2.png`
- `dream/gui/static/dream-app-icon.png`
- `dream/desktop/window.py`
- `dream/desktop/style.css`
- `scripts/install-desktop.py`
- `tests/test_dream_branding.py`
- `docs/desktop.md`
- `docs/dream-branding.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-15-dream-branding.md`
