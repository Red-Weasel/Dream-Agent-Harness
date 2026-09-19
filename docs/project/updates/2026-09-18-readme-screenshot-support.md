# DREAM-080 — README screenshot and Ko-fi support link

Recorded: `2026-09-18T19:10:38-05:00`
Work items: `DREAM-080`
Outcome: `implemented`
Actor: Claude Code (Opus 5) at the owner's request; published as Red-Weasel.

## Request

The owner asked for their screenshot of Dream running DeepSeek-V4.1-Flash on the Dream GitHub page, and a working
donate button.

## Changes

- `README.md`: the screenshot under the intro artwork, captioned; a Support section ("Buy me a coffee") linking the
  owner's Ko-fi page.
- `.github/FUNDING.yml`: GitHub's Sponsor button, pointing to Ko-fi.
- `docs/public/images/dream-deepseek-v41.png`: the screenshot, cropped to the Dream window (desktop bar and dock
  removed).

## Validation

- The Ko-fi page was opened in a browser: its support panel (one-time and monthly tips) is live.
- `python3 scripts/check_project_tracking.py check --base HEAD` passes on the change.
- Documentation-only change; no tests were run.

## Unfinished work

None within this session's scope. If the Sponsor button does not appear, the repository's Sponsorships feature
must be enabled in its settings (owner action).

## Next steps

None required.

## Files changed

- `.github/FUNDING.yml`
- `README.md`
- `docs/public/images/dream-deepseek-v41.png`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-18-readme-screenshot-support.md`
