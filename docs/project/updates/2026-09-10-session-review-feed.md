# DREAM-033 — Current-session review and verbose activity feed

Recorded: `2026-09-10T21:00:01+00:00`
Work items: `DREAM-033`
Outcome: `implemented`
Actor: Codex lead with project_workspace, guided_workflows and capabilities_diagnostics contributors.

## Request

Review the owner's active DeepSeek settings and missing `see` behavior, inspect
tool/skill failures, and make Studio's feed more verbose. Acceptance includes
reported reasoning, inspectable tool calls, attributed worker activity and retained
history with no model loads, GPU operations, inference calls or active-session
restart. See the [master criteria](../MASTER_PLAN.md#dream-033-acceptance--current-session-audit-and-activity-feed).

## Changes

Detailed is the feed default, with expanded model-reported reasoning, named tools,
result previews and inspectable bounded arguments/results. Compact persists in
browser storage. Manual reasoning collapse survives subsequent chunks. Worker
cards separate concurrent runs and tools from lead counts and permission handling.
Failure explanations remain visible, including inside Compact worker cards.
Compatible HTTP workers emit actual request attempts, response receipt, returned
reasoning, tool start/result and terminal status. Nonstreaming reasoning is labeled
reported after response. Worker answers still return to the lead; this pass does
not duplicate them as new assistant answers. Display subscribers cannot abort work.
Activity is bounded and sanitized before live delivery and conversation retention;
reconnect is display replay, not execution.

Controls adds read-only settings from existing backend state: next-turn selection,
server-reported context, retained launch options, last prepared selection and last
admission calculation. Unknown values stay unknown. Stop contents and credentials
are excluded. Settings viewing does not initialize or modify performance state.
The media matcher now recognizes animated, animations and animating, fixing a
reproduced missed workflow on the owner's first request. Required package asset
checks include the feed files and activity helper.

The read-only review verified **250,000 context, 65,000 maximum output, two launch
GPUs, thinking on, low effort, prefill 256 and one server slot** from the existing
launch preset, process command line and log. It was not the prior 400,000/100,000
configuration. Existing logs show host expert storage and a loaded vision sidecar;
Dream's MachX multimodal default nevertheless removes `see`, while some tool and
verifier instructions request it. These facts identify a capability mismatch, not
a successful image-input qualification. The first two completed turns spent 663.7
and 414.3 seconds in verification. Four first-turn failures included screenshot
argument errors and a bwrap loopback prerequisite failure. No live resource sampling
or benchmark was run. Private transcripts and workspace contents were not copied
into source documentation.

See the sanitized audit,
desktop guide, settings guide
and [ADR-013](../DECISIONS.md#adr-013--reported-activity-and-inspectable-generation-settings-2026-09-10).

## Validation

Observed on September 10 in the existing `.venv`, with fake HTTP clients, temporary
state and GPU-disabled Chromium. The session-only `/tmp/dream_cpu_guard.py` blocks
GPU sampling, engine capability/health calls and live httpx transports; fixtures
provide their explicit substitutes. Read-only `/proc` access and CPU fixtures used
sandbox escalation without touching owner processes or model endpoints.

Lead regression: **141 passed, two deprecation warnings in 5.72s**, exit 0:

```bash
PYTHONPATH=/tmp:$PWD .venv/bin/python -m pytest -q -p dream_cpu_guard tests/test_active_settings.py tests/test_curated_skills.py tests/test_diagnostics.py tests/test_performance_backend.py tests/test_performance_modes.py tests/test_performance_controls.py tests/test_capability_integration.py tests/test_task_guidance_integration.py tests/test_runtime_controls.py tests/test_local_subagents.py tests/test_parallel_subagents.py tests/test_background_filing.py tests/test_background_usage.py
```

The warnings are existing Starlette/httpx and AnyIO alias deprecations.
Final lead integration: **68 passed in 17.97s**, exit 0:

```bash
PYTHONPATH=/tmp:$PWD .venv/bin/python -m pytest -q -p dream_cpu_guard tests/test_agent_activity.py tests/test_feed_verbosity.py tests/test_desktop_chat.py tests/test_desktop_companion.py tests/test_desktop_protocol.py
```

These two lead suites cover 209 test cases with disjoint selected test files.
Contributor checks additionally passed 69 directly affected worker/backend cases
and 56 feed/desktop cases; those overlap the lead runs and are not added to 209.
The first lead integration invocation did not execute because automatic permission
review timed out; its explicitly allowed one-time retry ran successfully. A
contributor's earlier browser run had 53 passes and one initial Page.goto fixture
setup timeout; its final 56-case rerun passed. No test was weakened for that retry.
`node --check` passed for feed.js and controls.js; Python compilation passed for
activity changes. No owned fixture processes remain.

Media routing reproduced red with three failures, then all 43 curated-skill tests
passed after the word-form fix. Initial feed checks exposed missing worker-history
retention while its contributor was still implementing it. Review also found
discarded status explanations and failed worker tools hidden inside Compact cards;
these received focused regressions. Additional bounds checks cover hostile text,
large results, concurrent IDs and numeric serialization. Scoped diff checks passed.
The lead visually inspected a 480px GPU-disabled fixture screenshot at
`/tmp/dream-feed-ui-review/test_detailed_default_preserve0/detailed-feed-mobile.png`.
It contains fixture content, not the user's active session.

Graph search_graph stalled and some contributor graph requests timed out or lost
transport; focused reads were used after that limitation. Later search_code calls
returned useful indexed locations. One attempted `dream/resources/skills` read was
corrected to checkout `skills/`; the former is the wheel destination, not evidence
that curated skills were missing. No dependencies were installed.

Private sanitized audit totals are at `/tmp/dream033-session-audit.json` (0600).
Source baseline: `/tmp/dream-session-review-20260910-baseline.json`, 510 hashes.
Pre-existing dirty work was preserved; no commit, release or owner acceptance.
Final snapshot: `/tmp/dream-session-review-20260910-final.json`.
Tracking check `.venv/bin/python scripts/check_project_tracking.py check --snapshot /tmp/dream-session-review-20260910-baseline.json`
passed with 20 dated records and 22 changed source paths checked. It was rerun after
recording this result. Scoped diff whitespace checks passed.

## Unfinished work

Vision metadata/transport/tool registration remains follow-up: do not enable it
solely by model filename or a loaded sidecar. Screenshot schemas still need to
express destination alternatives, filename suffixes and nonempty step code.
Sandbox prerequisite visibility, verifier scope/cost and prior findings diverting
a new question remain findings in DREAM-033, not silently applied runtime changes.

Live model quality, image understanding, GPU/RAM behavior, native GTK/WebKit and
real wheel installation are not qualified by these fixtures. No GPU probe, load,
live inference, running-session reload, engine edit or publication occurred.
Static Studio refresh can show feed changes; new Python activity/settings need a
new Dream application process. Do not terminate an owned model to obtain the UI
while it is doing other work. Existing-session activity cannot be retroactively
reconstructed if it was never emitted. No owner acceptance or production readiness
is claimed.

## Next steps

Use Detailed feed and Controls in the next normally started Dream application.
Keep live model work paused until the owner lifts the constraint. A next DREAM-033
follow-up should reconcile explicit image support with `see` exposure and tool
guidance using CPU transport fixtures, then repair screenshot schemas and verify
current-request priority. Live image qualification remains separate and authorized
only after the benchmark/load hold is lifted.

For recovery, read CURRENT and this handoff, take a new snapshot and preserve the
dirty tree. Do not blanket restore HEAD, replay tools or stop owner processes.
No owned fixture process or lock remains; the lead and contributor runs exited. DREAM-012/018 retain owner acceptance/release scope.

## Files changed

- `docs/desktop.md`
- `docs/project/CURRENT.md`
- `docs/project/DECISIONS.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-10-session-review-feed.md`
- `docs/runtime-controls.md`
- `docs/session-review-2026-09-10.md`
- `dream/agent_activity.py`
- `dream/core/backends/openai_compat.py`
- `dream/core/engine.py`
- `dream/diagnostics.py`
- `dream/gui/conversation.py`
- `dream/gui/static/controls.js`
- `dream/gui/static/feed.css`
- `dream/gui/static/feed.js`
- `dream/gui/static/index.html`
- `dream/skills/selection.py`
- `tests/test_active_settings.py`
- `tests/test_agent_activity.py`
- `tests/test_curated_skills.py`
- `tests/test_diagnostics.py`
- `tests/test_feed_verbosity.py`
