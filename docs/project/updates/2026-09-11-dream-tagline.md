# DREAM-036 — Dream tagline

Recorded: `2026-09-11T13:33:51-05:00`
Work items: `DREAM-036`
Outcome: `implemented`
Actor: Codex lead.

## Request

Keep Dream and replace Claude-specific current branding with a new tagline.
Default offered: Your models. Your workspace. Optional alternatives were offered
while locating the current wording; no response received before implementation.

## Changes

Completed bounded copy change. Snapshot596 hashes at /tmp/dream-tagline-baseline.json;
existing dirty status preserved at /tmp/dream-tagline-initial-status.txt.
Acceptance: consistent current branding, unchanged Dream name and provider-identity
rules. Historical specifications and prior handoffs stay intact.

## Validation

Rendered banner, Python AST/TOML parsing, Dream package name, tagline consistency,
provider-identity guard and absence of old current-branding phrases all passed.
Actual rendered capture: /tmp/dream-tagline-banner.txt. Existing models and runtime
were not loaded or restarted. No new tests were added for this copy-only change. Graph search_code
did not finish within the bounded attempt and was terminated; focused known-file
reads and literal searches followed. Guessed assets/launcher/test paths were absent;
no files changed from those reads. No full suite or model/provider calls needed
for copy-only edits.

## Unfinished work

None within this scope. The new banner appears when the application renders it
from the updated source. No claim about the already running application was made.

## Next steps

Continue from the owner’s next request. Prior DREAM-035 results remain in its
separate handoff; no earlier handoff or historical specification was rewritten.

## Files changed

- `README.md`
- `pyproject.toml`
- `dream/__init__.py`
- `dream/tui/render.py`
- `dream/core/system_prompt.py`
- `docs/project/MASTER_PLAN.md`
- `docs/project/CURRENT.md`
- `docs/project/updates/2026-09-11-dream-tagline.md`

Final tracking passed:46 dated records,8 changed source paths checked against the
596-file session baseline. All prior dirty work and dated handoffs were preserved.
