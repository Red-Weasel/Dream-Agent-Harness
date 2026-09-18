# DREAM-043 — AppArmor installation verified

Recorded: `2026-09-11T18:51:00-05:00`
Work items: `DREAM-043`
Outcome: `implemented`
Actor: Codex lead.

## Request

Owner entered administrator password for the explicitly approved systemwide
Bubblewrap repair. Complete installation verification and restore Auto containment
in the existing session where possible without restarting the owner job.

## Changes

Approved profile installed at /etc/apparmor.d/bwrap-userns-restrict and loaded.
Root-owned UID 0, mode 0644 verified outside Codex's UID-remapping sandbox.
SHA-256 a964037f6cf0df1099f14226b037eaedde6237c86e715188e93eb460b30be859
matches the approved upstream AppArmor 4.0 candidate. No replacement collision
occurred. No sysctl/global namespace restriction was disabled.

## Validation

Owned installer session 10520 exited 0. Log /tmp/dream-apparmor-install.log reports
PROFILE_INSTALLED_AND_LOADED, QUALIFICATION_PASSED and
INSTALL_AND_QUALIFICATION_COMPLETE. Actual probe passed under the launching user and the
same unconfined AppArmor parent label used by Dream. All 11 checks passed:
network/PID namespaces differ from parent; workspace write/read succeeds; read-only
read succeeds; read-only/.git/outside writes fail and existing files survive;
INET/Unix socket creation fails; effective capabilities are zero; executed child
uses the restrictive unpriv_bwrap profile. Disposable fixtures were removed.
No model, render, benchmark or owner process restart was performed.

Authenticated read-only Dream status showed the original failed sandbox snapshot
still cached. Session ID and workspace match the continuing owner session, with
red_team false and no targets. The supported default-scope control returned ok with available=true. A subsequent
independent GET confirmed the same workspace, red_team=false, empty targets and
verified containment. The action re-established the same ordinary workspace scope,
refreshed its probe and cleared cached exact-command approvals; it granted no
command, enabled no exercise and restarted no process. Evidence:
/tmp/dream-apparmor-live-status.json. Tokens were used privately and never printed.
A separate permission_mode_status query returned HTTP 400; the combined client
script exited 1 after the successful refresh. The read-only follow-up confirmed
that refresh remained applied. No mutation was retried. Auto mode is the owner's
reported selection; the older process's mode was not independently read back.
Graph discovery returned Transport closed; focused source reads followed it.
Baseline /tmp/dream-apparmor-verified-baseline.json has 620 source hashes.

## Unfinished work

None within the approved installation/refresh scope. Owner's subsequent model
behavior is not a completed comparative evaluation. Previously queued approval
prompts may retain their original decision. Other source fixes still require a
later Dream process restart; this system repair and current status refresh do not.
No installer/test process remains active, and no unattended monitor is claimed.

## Next steps

Continue the existing owner job. Normal Auto shell policy now has valid containment
evidence. Preserve destructive-command approvals and investigate any new concrete
failure without replaying uncertain tool actions. Earlier blocked handoffs remain
as historical records; this entry records their resolved authentication/install.
Tracking check passed: 57 dated records, all 3 changed source paths accounted for.

## Files changed

- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-11-apparmor-verified.md`
