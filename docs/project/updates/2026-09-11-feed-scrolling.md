# DREAM-038 Feed scrolling and permission-mode shortcut

Recorded: `2026-09-11T14:25:37-05:00`
Work items: `DREAM-038`
Outcome: `implemented`
Actor: Codex lead; fresh Astra mode-shortcut implementer, independent Astra UI
reviewer and separate read-only Astra Blender investigator.

## Request

Owner reports that new feed content requires manual scrolling. Keep the newest
content visible automatically, preserve manual reading of history, and resume
following after returning to the bottom. Owner also confirmed that the explicit
local-request recovery control resolved the earlier Stop blocker.
The owner subsequently requested restoring Shift+Tab permission-mode cycling.

## Changes

The feed now retains follow state before content insertion and scrolls after
layout. Large updates and new assistant blocks no longer disable following or
override deliberate history reading. A resize observer covers wrapping and
expanded content. Movement is captured before incoming events as well as browser
scroll events, covering a same-frame return to the bottom. Sending a message
retains its explicit move to the latest content.

Shift+Tab in the desktop composer and a compact mode button use authenticated
runtime controls to cycle the same permission state as the terminal. Plain Tab,
other modifiers, composition and repeat events do not cycle; dialogs retain
normal navigation. Pending changes are not duplicated. Unacknowledged changes
display Mode unconfirmed and are never retried automatically. New status requests
required an explicit update to one existing Stop test's expected control sequence.

Original dirty work and prepared GitHub snapshot are preserved. Publication remains
separately blocked under DREAM-037; these changes are not in its prepared commit.

## Validation

Graph-first search failed with Transport closed; focused known-source reads
followed. Baseline: /tmp/dream-feed-scroll-baseline.json, 599 source hashes.
No inference, model load or owner application restart.
The first browser attempt could not bind its localhost server in the sandbox.
An approved isolated run then reproduced two scroll failures before the fix.
After the fix, all nine new scroll cases passed in a combined run with 34 passes
and one existing chat-send test failure at an immediate asynchronous assertion.
The send assertion passed on isolated rerun in 0.91s. The original failure remains
recorded at /tmp/dream-feed-scroll-green.log; rerun evidence is
/tmp/dream-feed-scroll-send-recheck.log. No implementation repair was attributed
to that isolated asynchronous failure.

Fresh review initially failed on the same-frame resume race. Root reproduced it
in a new regression before correcting event ordering. All ten scrolling cases
then passed in 6.41s, /tmp/dream-feed-scroll-race-green.log. The unchanged independent
probe /tmp/dream_scroll_review.py confirmed zero gap after same-frame resume,
subsequent updates, mobile-to-wide reflow and tool collapse, and confirmed that
scrolling upward pauses following. Its earlier claimed native Ctrl+End reproduction
was corrected: text arrived before the browser's scroll animation began, not after
reaching the bottom. That old-target animation limitation is not claimed fixed.

Mode tests first reproduced the missing shortcut/backend route. Root's review
also found stale mode display after a lost acknowledgment; its added test failed
before repair. Final author checks passed six selected tests in 3.62s. Independent
/tmp/dream_mode_review.py verified all four mode transitions, five key guards,
actual permission-policy decisions, and read-only mode recovery on page reload.

Two combined runs each passed 54 tests and failed the existing preview-height
check, with two dependency deprecation warnings. The first button styling change
did not fix it because an inherited 30px minimum height remained. The subsequent
24px minimum keeps the button compact. Logs: /tmp/dream-chat-ui-final.log and
/tmp/dream-chat-ui-final2.log. The isolated layout recheck passed in 1.16s;
its mobile preview screenshot was visually inspected. Final combined run:
**55 passed, 2 warnings in 27.32s**, exit0, /tmp/dream-chat-ui-final3.log.
Command: `.venv/bin/pytest -q tests/test_feed_scroll.py tests/test_feed_verbosity.py tests/test_desktop_chat.py tests/test_mode_shortcut.py tests/test_runtime_controls.py tests/test_desktop_companion.py`.
The two warnings are existing Starlette/httpx and AnyIO BlockingPortal deprecations.
Seven final source/test hashes were recorded before that run at
/tmp/dream-chat-ui-final-hashes.json and verified unchanged afterward. Scoped
tracked whitespace checks passed. Final tracking passed with 49 dated records
and all 11 session-changed paths covered.

Blender investigation was read-only. Current media status uses executable presence,
not engine/device evidence. Installed package metadata reports Blender4.0.2 and
Cycles add-on source exists; this does not prove registration or rendering works.
A vendor-aware capability report and an explicit resource-checked probe are proposed,
not implemented. No Blender startup, render, preference change or GPU workload ran.

## Unfinished work

None remaining in the scoped implementation. Loaded application and native desktop
acceptance are unverified. The badge shows last acknowledged mode; a change in
the terminal is reflected on reconnect or the next mode action. Existing Ctrl+End
animation can target the old bottom if content arrives before it starts scrolling.
Blender capability discovery remains proposed work, not a shipped fix.

## Next steps

Refresh Studio for the static scrolling changes.
Start a new Dream application process when ready to load the Python mode-control
route; refreshing the page alone cannot add it to an already loaded backend.
No restart or live configuration change is performed by this work. All contributors
have returned; root owns final reconciliation and tracking. Do not overwrite the
prepared publication checkout or publish without resolving DREAM-037 approval.

## Files changed

- `dream/gui/static/index.html`
- `dream/gui/static/companion.js`
- `dream/gui/static/companion.css`
- `dream/tui/app.py`
- `tests/test_feed_scroll.py`
- `tests/test_mode_shortcut.py`
- `tests/test_desktop_chat.py`
- `docs/desktop.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-11-feed-scrolling.md`
