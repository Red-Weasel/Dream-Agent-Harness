# DREAM-045 — Auto routine-work approvals

Recorded: `2026-09-11T19:22:40-05:00`
Work items: `DREAM-045`
Outcome: `implemented`
Actor: Codex lead and Astra author/reviewer.

## Request

Owner explicitly requests Auto run routine work and only prompt for legitimate
danger. Acceptance: the actual bounded frames cleanup can proceed automatically
under verified containment; broader recursive/outside/sensitive deletion,
publication, system operations and host access remain gated. No policy weakening
for unknown executors or unavailable containment. Do not execute owner commands.

## Changes

Astra author changed dream/core/policy.py and tests/test_auto_cleanup_policy.py;
Codex lead integrated and updated operator/tracking docs. Independent Astra reviewer
performed adversarial checks without editing production source. Baseline:
/tmp/dream-auto-risk-baseline.json. The existing dirty tree was preserved.

Contained native Auto now recognizes direct nonrecursive image cleanup in proper
workspace subdirectories with a frames/renders/outputs path component. Literal
filenames or leaf extension globs qualify after a bounded scan. Unknown executors,
missing containment, other modes and red-team scopes retain their prior contracts.
The helper inspects all compound segments rather than trusting the first benign
cleanup effect. It refuses reviewed uncertain wrappers and shell-state changes.

The owner asked for fewer routine approvals. This narrow exception addresses the
observed command while retaining explicit host and consequential-operation prompts.
It was chosen instead of a universal shell grant. Directory names and extensions
are conventions, not verified generated-file provenance. The 1,024-entry scan is
classification-time evidence, not an atomic deletion-count enforcement mechanism.
Opaque companion scripts remain subject to actual OS containment, not a claim of
complete static effect analysis.

The model continued an incorrect host library-cache repair during this work.
Lead supplied DREAM-044's tested resolved-library-path prefix and advised against
ldconfig/update-alternatives repair: the missing sandbox alternatives mount was
already diagnosed. No host cache changes or owner command approvals were made.

## Validation

- Observed author RED regressions before repair, then 92 passed and 2 skips in
  0.49 seconds under the Codex sandbox. Skips were real-containment prerequisites:
  /tmp/dream-auto-cleanup-author-final6.log. They were not treated as passes.
- Review found concrete bypasses involving changed cwd, leading redirects,
  multicall/execution wrappers, backslash-newline verbs, and shell glob settings.
  Each was repaired; the actual logged cleanup/render/inspection shape still
  classifies allow against a disposable workspace. No rendering command executed.
- Independent final review: 127 passed in 0.98 seconds, plus 10 separate adversarial
  policy probes all requiring approval, with fixtures preserved. Evidence:
  /tmp/dream-auto-cleanup-reviewer-final-tests.log and
  /tmp/dream-auto-cleanup-reviewer-adversarial.log.
- Lead combined final gate: 184 passed in 5.79 seconds, exit 0, no skips or warnings,
  using .venv Python outside the Codex filesystem sandbox with a 120-second limit.
  Modules: test_auto_cleanup_policy, test_policy, test_policy_hardening,
  test_permission_hardening, test_auto_bash_recovery,
  test_execution_engine_integration, test_execution_foundation, and
  test_execution_runtime_links. Evidence: /tmp/dream-auto-cleanup-root-final.log.
- Code graph search returned Transport closed; focused source reads used.
- An earlier tracking check failed because CURRENT's timestamp preceded this
  handoff. Timestamp alignment repaired; final tracking check recorded below.
- Final tracking check passed: 59 dated records and all 6 session-changed paths
  covered. Author/reviewer policy and test hashes remained unchanged after the
  lead's combined gate; hashes are in /tmp/dream-auto-cleanup-author-hashes.json.

## Unfinished work

Owner live acceptance remains. The running Dream process has not loaded these
Python changes. No model call, owner deletion, rerender, encode, publication,
system library/cache edit, or application restart was performed for DREAM-045.
The lexical recognizer cannot establish every effect of arbitrary shell scripts.

## Next steps

After preserving/completing the current job, restart Dream to load the source
fixes. Continue from saved artifacts; do not rerender merely to qualify this repair.
For the older running process, prefix ffmpeg/ffprobe with the verified BLAS/LAPACK
library path in docs/execution.md. Source author and reviewer are finished; the
lead's bounded test process exited successfully. Preserve the existing dirty tree.

## Files changed

- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-11-auto-routine-work.md`
- `dream/core/policy.py`
- `tests/test_auto_cleanup_policy.py`
- `docs/execution.md`
