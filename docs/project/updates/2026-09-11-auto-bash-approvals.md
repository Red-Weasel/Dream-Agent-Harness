# DREAM-043 — Auto Bash approval diagnosis and recovery

Recorded: `2026-09-11T18:04:26-05:00`
Work items: `DREAM-043`
Outcome: `blocked`
Actor: Codex lead, Astra source author, fresh Astra independent verifier.

## Request

Stop repeated Bash approval prompts in Auto. Acceptance: identify the actual cause,
recover stale failed sandbox evidence after repair, retain exact host approval when
unavailable, and prepare the required system repair for explicit owner approval.

## Changes

The primary cause is verified in host kernel logs. At 17:56:53 CDT, /usr/bin/bwrap
entered AppArmor's unprivileged_userns profile and was denied setpcap and net_admin.
Dream's launch processes use the unconfined label. An isolated successful probe
inherited the vscode profile, which explicitly permits user namespaces. Therefore
that independent success did not qualify the failing Dream launch context. The
session's loopback RTM_NEWADDR failure and host-once executions match these denials.
Upstream Bubblewrap sets up loopback before applying Dream's seccomp filter; no
change to containment arguments or shell auto-grant is justified.

Source repair: before native Bash authorization outside Plan, refresh missing,
failed or mismatched sandbox evidence. A successful probe then uses normal Auto
policy. Healthy saved evidence avoids an extra permission-stage probe. Engine,
mode, workspace or scope changes while awaiting the probe discard its result and
refuse the call. Cancellation preserves the old evidence. Provider-native/spoofed
tool names cannot inherit Dream's evidence. Deletion/publication still ask;
unavailable containment still requires exact host-once approval. The executor
retains its separate live probe. Controls and execution docs describe the new
saved-evidence source. This code needs a new Dream process to take effect.

Prepared exact upstream AppArmor 4.0 profile, not installed:
/tmp/dream-bwrap-userns-restrict.proposed
SHA-256: a964037f6cf0df1099f14226b037eaedde6237c86e715188e93eb460b30be859.
Review/install/rollback notes: /tmp/dream-apparmor-repair-review.md.
Source and rationale are linked in docs/execution.md. Proposed target is
/etc/apparmor.d/bwrap-userns-restrict. It permits Bubblewrap setup and stacks a
child profile denying capabilities. Its attachment covers all /usr/bin/bwrap
callers, not only Dream. No global sysctl change is proposed. Kernel loading and
post-install behavior remain untested. No on-disk target/attachment collision was
found; the loaded-profile list requires privileged inspection before installation.

Astra author owned dream/tui/app.py and the new recovery tests. Root owned Controls
wording, documentation, host diagnosis and proposal. Fresh reviewer independently
reviewed both source and proposed profile without editing. Existing dirty work was
preserved; baseline /tmp/dream-auto-bash-baseline.json has 617 source hashes.

## Validation

Harmless isolated /bin/true sandbox probe passed outside Codex. Read-only process
labels, installed profiles and matching kernel denials establish why Dream differs.
Downloading the upstream profile initially failed sandbox DNS; the approved bounded
external download succeeded. Both root and reviewer ran parser 4.0.1 with
-Q -K -j1 -I /etc/apparmor.d: exit 0, syntax compilation only, no kernel load.

Regression RED: 15 failed and 9 passed before source repair. Initial GREEN: 24
passed; three additional cancellation/non-Auto cases bring new coverage to 27.
An intermediate author/root log filename collided; its interleaved counts are not
used as evidence. Root reran into a unique log. Final root command:

```sh
timeout 90 .venv/bin/pytest -q \
  tests/test_auto_bash_recovery.py tests/test_execution_readiness.py \
  tests/test_execution_readiness_ui.py tests/test_permission_hardening.py \
  tests/test_execution_engine_integration.py tests/test_execution_foundation.py \
  tests/test_mode_shortcut.py
```

**150 passed in 7.89 seconds**, exit 0, no skips/warnings. Evidence:
/tmp/dream-auto-bash-root-integration.log. Tests ran outside Codex's sandbox with
disposable workspaces; no model/GPU or owner job was invoked. Independent reviewer:
42 passed in 0.51 seconds, all 27 new cases included, no review blocker. Separate
Controls evidence: 23 passed in 0.50 seconds. git diff --check passed for source
changes. Final source/test hashes: /tmp/dream-auto-bash-root-hashes.json.
Graph discovery returned Transport closed; focused source reads followed it.

Author's separate final suite passed 103 tests in 1.05 seconds:
/tmp/dream-auto-bash-author-final.log. Its earlier sandbox run had 92 passes,
2 skips and 11 socket setup errors; the external final run resolved the test
prerequisite. Tracking passed: 55 dated records, all 7 changed paths accounted for.
No author, reviewer or root test process remains active.

## Unfinished work

Source repair is implemented and verified; the user-visible root cause remains
until the system profile is approved, installed and tested from Dream's launch
context. CLAUDE.md rules 1 and 10 require explicit approval for persistent rule and
access-permission changes. No policy file, sysctl, running process or owner setting
was changed. No public write occurred. Syntax validation is not live qualification.

## Next steps

Ask the owner to approve installing/loading the reviewed profile for /usr/bin/bwrap
systemwide. Before installation, inspect loaded bwrap/unpriv_bwrap profiles as root;
do not overwrite an existing policy. Then verify namespace setup and actual outside
write/socket/capability denials in disposable tests. Roll back only the newly
installed profile if it fails qualification. Preserve the owner job; the source
refresh fix needs a later Dream restart. No unattended monitor remains.

## Files changed

- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-11-auto-bash-approvals.md`
- `dream/tui/app.py`
- `dream/gui/static/controls.js`
- `tests/test_auto_bash_recovery.py`
- `docs/execution.md`
