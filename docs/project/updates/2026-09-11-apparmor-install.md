# DREAM-043 — Approved AppArmor repair

Recorded: `2026-09-11T18:22:29-05:00`
Work items: `DREAM-043`
Outcome: `blocked`
Actor: Codex lead.

## Request

Owner explicitly approved installing/loading the reviewed Bubblewrap AppArmor
profile systemwide. No further policy approval is required for this exact change.
Acceptance: hash/collision checks, install/load, then actual contained execution
with outside writes/socket/capability restrictions verified. Preserve owner job.

## Changes

Root owns approved system repair and project tracking. Baseline:
/tmp/dream-apparmor-install-baseline.json (619 hashes). Existing dirty work preserved.
Candidate SHA-256 a964037f6cf0df1099f14226b037eaedde6237c86e715188e93eb460b30be859
was rechecked. Prepared source repair from prior handoff remains unchanged.

## Validation

sudo -n true failed: administrator password is required. Preparing standard
Polkit authentication so the password is entered locally, never in conversation.
Polkit authentication was launched through pkexec; the authentication helper is
running and the install log is still empty. No target policy file exists yet.
Installation, kernel loading and live containment qualification remain pending.

Temporary installer /tmp/dream-install-approved-apparmor.py pins approved bytes,
refuses existing loaded/on-disk policy, installs exclusively, loads, and runs
/tmp/dream-apparmor-qualify.py as the launching user under the unconfined launch label.
It rolls back only the newly installed unchanged profile on failed qualification.
Both scripts pass Python syntax compilation. Fresh Astra read-only review found
an initially invalid outside-write fixture (missing parent directory); root fixed
it before execution. Final review found no blocker. Qualification now uses an
existing outside fixture, read-only mounts, .git, actual net/PID namespace IDs,
INET/Unix socket denial, zero effective capabilities and the expected child profile.
These remain test intentions until the authenticated run produces results.

Tracking check passed: 56 dated records, all 3 changed source paths accounted for.

## Unfinished work

Administrator authentication and actual system repair/qualification. Existing
pkexec request remains pending in owned exec session 10520, PID 167682; Polkit
helper PID 167706 was observed. Do not start a competing installer. Final observed
log is empty and no target policy file exists. User was asked whether the Ubuntu
password dialog is visible; no reply/authentication was received during this turn.
This is not a new permission request or an automatic-approval rejection. Source
policy approval is already granted. No unattended assistant monitor is claimed;
the approved installer itself will run its validation after authentication.

## Next steps

Resume by polling existing exec session 10520 and /tmp/dream-apparmor-install.log;
verify actual host state before any retry. Authenticate, inspect loaded-profile
collisions, install exact approved bytes,
load and run disposable checks. On failure, remove only this newly added profile.
No owner process restart, model work or public write is authorized by this step.

## Files changed

- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-11-apparmor-install.md`
