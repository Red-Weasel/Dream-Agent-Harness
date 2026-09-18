# DREAM-044 — Sandbox runtime links and approval wording

Recorded: `2026-09-11T19:06:04-05:00`
Work items: `DREAM-044`
Outcome: `implemented`
Actor: Codex lead and Astra contributor/reviewer.

## Request

Owner reports confusion about host-once approval and FFmpeg libblas.so.3 failure.
Acceptance: reproduce host/sandbox difference; restore system alternatives links
read-only without exposing all /etc; preserve filesystem/socket/capability limits;
make choices/reasons explicit; explain pipeline exit status. No unrelated system
ldconfig/installation, owner model/render call or process restart.

## Changes

Astra author owns dream/core/execution.py and new tests/test_execution_runtime_links.py.
Root owns approval wording in dream/tui/app.py, tests/test_auto_bash_recovery.py and
documentation. Existing dirty tree preserved; baseline /tmp/dream-ffmpeg-baseline.json
has 621 source hashes. /usr/lib/.../libblas.so.3 points via /etc/alternatives to an
installed library in /usr. Current sandbox hides that intermediate directory.

## Validation

Live session library error and misleading EXIT:0 observed. That echo follows a
pipeline ending in tail, so it reports tail's status, not ffmpeg. A subsequent
ffprobe failure gives the actual overall127. Graph transport failed; focused reads
followed. Host FFmpeg/FFprobe version checks passed; original sandbox failed both with127.
A temporary LD_LIBRARY_PATH pointing at the resolved BLAS/LAPACK directories made
both contained version checks pass without host changes. Root wording regressions
failed3 cases before repair, then all30 approval-recovery cases passed.

Implemented a trusted, read-only /etc/alternatives mount. Root integration passed
141 tests in12.82s, /tmp/dream-runtime-links-root-final.log. After the author's
last additional mount-readonly/hidden-shadow test, all8 final runtime-link tests
passed in0.58s, /tmp/dream-runtime-links-root-final-eight.log. Independent review
passed86 checks then8 final link tests and found no blocker. Actual FFmpeg/ffprobe
and which execute inside containment. No render/model call, hostcache rewrite,
installation or ownerprocess restart occurred. Source whitespace check passed.

UI choices now name in-sandbox versus outside-sandbox execution and explain that
outside approval is for one exact command, not a remembered permission. The actual
policy reason is displayed even when it is not a workspace-boundary warning.

## Unfinished work

Implementation and review complete. Current process retains old source and needs
a later restart for these changes. A tested temporary library-path prefix is in
docs/execution.md; no owner encode or current-session behavior is claimed.
The owner additionally requested Auto only ask for legitimate danger. That policy
change is separately tracked as DREAM-045; this wording fix does not broaden grants.

## Next steps

Proceed with DREAM-045's Auto risk distinction. Preserve the active owner task;
use the documented temporary library paths for this older process if needed.
Tracking passed:58 dated records and all8 changed source paths accounted for.

## Files changed

- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-11-sandbox-runtime-links.md`
- `dream/core/execution.py`
- `tests/test_execution_runtime_links.py`
- `dream/tui/app.py`
- `tests/test_auto_bash_recovery.py`
- `docs/execution.md`
