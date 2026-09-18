# DREAM-048 — Live Studio version diagnosis

Recorded: `2026-09-11T20:59:03-05:00`
Work items: `DREAM-048`
Outcome: `implemented`
Actor: Codex lead and fresh Astra read-only vision investigator.

## Request

Owner reports Studio still black, supplies verifier-unavailable screenshot, confirms
MP4 plays directly, and asks how to use a frontier Council model for review/polish.
Diagnose actual live process before further code changes. Explain existing controls.

## Changes

Documentation only. Baseline /tmp/dream-live-version-baseline.json. No production
source, owner session, process, settings or model changes.

Host discovery identifies PID3350484, started2026-09-11T19:10:45Z (14:10CDT).
Authenticated read-only localhost API confirms session20260911-141259-1e42,
workspace~/Desktop/<project> and the same DeepSeek model. Live profile
reports max_run_seconds7200, visionnull; capability vision is unreported. These
observations establish that the live process predates DREAM-040/046/047 repairs.
The process inherited a different cwd; its explicit workspace is correctlyPayback.
No unrelated workspace files were opened or modified.

Correction to earlier process-absent observations: the Codex executor's PID
namespace hides host processes. Absence there was not evidence Dream exited.
The actual host process remains. Earlier handoffs are preserved.

Astra traced the see refusal to the verifier prerequisite check against the full
registered tool set, before inference. It is not schema deferral. Latest transcript
has335 records, runtime244; final verification phase0.0005195s with no verifier
request phase. On current source, attaching without model vision metadata or an
explicit vision setting can still leave see unavailable. Restart installs code;
it does not itself establish endpoint image acceptance.

Council source confirms independent read-only advisors with model/effort selectors,
Apply Council, Ask selected advisor and Ask council. To make edits, change the main
agent between turns. No consultation/model switch was performed by this agent.

## Validation

Observed host process and two live GET responses; no mutation requests. First host
process query timed out in automatic approval review. Reviewer explicitly allowed
one retry, which succeeded. No unsafe-action conclusion drawn from that timeout.
Tokens remained private and were not printed. Root graph transport failed; focused
source reads followed. Fresh agent independently examined source and session logs.
Owner reports direct MP4 playback success. Prior Chromium playback evidence remains
valid but does not establish playback in the old native window.
No new code tests or native playback were run for this read-only diagnosis.
Tracking passed: 62 dated records and all 3 changed documentation paths covered.

## Unfinished work

Full application restart and native playback acceptance remain. Verify current
Tool images enabled/See tool registered and image support when reconnecting the
local server. Do not claim that a model name alone qualifies vision transport.
No new current-source failure was established.

## Next steps

Exit the old Dream application, reopen the Payback workspace and present existing
rocket_launch_viewer.html without rerendering. Use Council advisors for review,
or select a frontier model as main for actual project edits. Preserve artifacts.

## Files changed

- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-11-live-studio-version.md`
