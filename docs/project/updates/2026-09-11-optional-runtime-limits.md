# DREAM-046 — Optional runtime limits

Recorded: `2026-09-11T19:38:56-05:00`
Work items: `DREAM-046`
Outcome: `implemented`
Actor: Codex lead, Astra author and independent reviewer.

## Request

Owner explicitly rejects the unrequested two-hour cutoff and expects overnight
work. Remove the default elapsed limit, preserve optional explicit finite limits,
and give accurate UI status. Also propagate observed nonzero Bash exit codes as
structured tool failures so the harness does not count failed media commands as
success. Review the media-session failure path without running
owner scripts or restarting the current job.

## Changes

Baseline /tmp/dream-optional-runtime-baseline.json; existing dirty tree preserved.
Astra optional-time author changed profiles/meter and new focused tests. Lead
changed the Runtime display and browser tests. A separate Astra media-audit author
changed native Bash failure reporting and its focused tests. Authors independently
reviewed each other's production changes. New reviewer spawn and old reviewer
followup were refused by the agent thread limit; no fresh-judge claim is made.

The default max_run_seconds is now null in all profiles. Optional positive finite
limits retain their enforcement and existing settings/model/environment/explicit
precedence. Saved null is accepted for active time. Meter snapshots preserve
observed elapsed/approval time with null remaining/maximum active values. Controls
shows Unlimited only when both corresponding values are explicitly null; absent
older-backend fields remain unreported. Token/tool and execution limits unchanged.
No owner runtime settings file was edited. Saved settings showed no override;
the prior process PID was gone when checking its environment, so the current
process's effective environment was not verified.

The media audit found observed nonzero Bash exit statuses rendered as FAILED but
returned as structured success. Native run_bash now returns err(description) for
nonzero exit codes, preserving output, truncation and host-execution labels. This
feeds the existing HTTP error flag, event and runtime failure-count path. Shell
pipeline semantics are unchanged; shell-masked failures still require correct
script exit handling.

Read-only session evidence: lines286,289,304 report exit127,2,1 while runtime
line218 reports16 tools and zero failures. There are150 newer rendered PNG frames
and an older457580-byte MP4; its recorded metadata is1280x720,30fps,5seconds, with
model-reported framing problems. The missing library during latest encoding is
the already-repaired alternatives mount in the older running source. No playback
or visual acceptance is claimed. Existing frames should be checked and reused.

## Validation

- Optional-time RED:7 failed,9 passed. Repaired core suite16 passed in0.08s,
  including simulated16-hour runs, optional caps, approval waits, settings and
  serialization. /tmp/dream-optional-time-author-evidence.json has exact artifacts.
- Author uv invocation failed on read-only cache before testing; existing .venv
  Python3.12.3 used. Two author broader sandbox runs stalled after118 tests at an
  Engine async test and were interrupted with exit130. Four independent UI setup
  errors were loopback-bind restrictions; none are treated as passed tests.
- Lead UI RED:1 failed,1 passed. GREEN4 passed in2.79s outside the Codex sandbox.
  Evidence /tmp/dream-optional-runtime-ui-red.log and corresponding green.log.
- Native RED initially included fixture constructor TypeErrors; corrected RED
  showed19 expected failures and9 passes. GREEN71 passed in0.41s across new native
  cases and existing readiness tests. /tmp/dream-shell-failure-author-red2.log and
  green.log preserve results.
- Lead runtime/profile/Engine/loop/settings/UI gate:190 passed in5.55s, with2
  deprecation warnings from Starlette httpx and AnyIO BlockingPortal aliases.
  /tmp/dream-optional-runtime-root-final.log. Eight modules run under a120-second
  bound outside the Codex sandbox; no skipped tests.
- Lead shell/readiness/Engine/containment/environment/friction gate:149 passed
  in5.50s, no skips or warnings. /tmp/dream-shell-failure-root-final.log. Seven
  modules run under a120-second bound outside the Codex sandbox.
- Root graph calls returned Transport closed; focused source reads used. The
  budget author successfully queried the project graph from its agent context.
- Final native author gate73 passed in0.40s after adding actual HTTP tool-result
  event cases. Independent non-author review30 passed in0.35s, no findings.
  Independent budget review89 passed, no findings; its broader sandbox attempt
  stalled after45 checks and was interrupted, not counted as completion.
- Lead's initial additional event selection matched no test names (exit5). Exact
  node selection then passed both new cases in0.31s:
  /tmp/dream-shell-failure-root-event-final.log. This supplements the earlier149;
  production source stayed unchanged. Combined root coverage:341 passed,2 warnings.
- All author source/test hashes stayed unchanged after root verification. Final
  seven source/test hashes are in /tmp/dream-optional-runtime-root-hashes.json.
  git diff --check passed. Final tracking check is recorded below.
- Final tracking passed: 60 dated records, all 12 session-changed source paths
  covered. Authors and lead have no owned test process left running.

## Unfinished work

Owner live acceptance and completed corrected-animation playback remain. Source
changes require a new Dream process if it started before the repair. No owner
session was restarted, no render/model/benchmark was run, no owner artifact or
configuration was changed, and nothing was published. Unlimited time alone does
not establish successful task completion or remove independent token/tool limits.

## Next steps

After loading the source fixes, continue from saved artifacts. Check the latest
script and frames, encode a playable corrected animation, verify it, and display
it in Studio. The older process workaround is the BLAS/LAPACK LD_LIBRARY_PATH
prefix already documented in execution.md. Do not change host alternatives or
rerender valid frames merely to bypass a loader error. Keep failed evidence and
the existing dirty tree; final source hashes and tracking evidence follow below.

## Files changed

- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-11-optional-runtime-limits.md`

- `dream/core/profiles.py`
- `dream/telemetry/runtime.py`
- `tests/test_runtime_unlimited.py`
- `dream/gui/static/controls.js`
- `tests/test_runtime_budget_ui.py`
- `docs/runtime-controls.md`
- `dream/tools/native.py`
- `tests/test_shell_failure_reporting.py`
- `docs/execution.md`
