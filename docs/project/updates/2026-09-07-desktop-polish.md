# DREAM-028 — Desktop usability and reliability polish

Recorded: `2026-09-07T22:52:53-05:00`
Work items: `DREAM-028`
Outcome: `implemented`
Actor: Codex lead; desktop_startup, media_usability and tool_skill_reliability contributors; independent polish_review.

## Request

Owner reported widespread bugs, hidden CLI-only answers and confusing animation
settings. Requested a cohesive desktop that starts, loads a model, streams readable
answers, exposes reliable tool calls and uses skills with ChatGPT-like ease of use.
Acceptance is in [MASTER_PLAN.md](../MASTER_PLAN.md). Implementation and bounded live
qualification are recorded here; owner daily-use acceptance is not recorded.

## Changes

- Graphical startup uses the existing shared App/Engine, model metadata,
  recommendations and saved settings. It shows workspace/model choices, optional
  advanced settings, progress and errors. Full-width Chat/Terminal/Browser tabs
  replace the constrained split. Explicit desktop CLI arguments remain supported.
- Startup checks GPU use, temperatures, VRAM and host memory, supports the actual
  GLM streaming policy, excludes auxiliary GGUFs, and serializes owned model loads
  across windows with a per-user/port flock. Unknown resource readings block a new
  load. Attached servers are not owned or stopped by the attaching session.
- Removed desktop suppression of chat and tools. Added readable user/assistant
  messages, expandable thinking and results, explicit permission choices, Stop,
  elapsed activity, error recovery and Skills search. Composer is always available.
  Failed sends retain the draft; an accepted user event is the source of truth.
- Reconnect replays a bounded private in-memory display transcript, pending
  permissions and the last explicit preview. It does not rerun tools or replay
  artifact scripts. Cleared stale permission cards and repaired interrupted tool
  status and reconnect preview behavior found by independent review.
- GUI permission answers use token authentication and exact pending request/choice
  validation. Shutdown confirmation remains available in Chat before Studio stops.
  Bus subscribers now wake safely across threads. Transcript retention errors do
  not prevent live delivery. Simultaneous GUI/terminal input is no longer dropped.
- Joined fragmented streamed tool names and preserved MCP isError. Installed-skill
  reading accepts bracketed/JSON content without falsely treating it as an error.
  Enabled plugin metadata and shared user roots make installed skills discoverable;
  the compact index retains every skill name. External skill dependency portability
  is not implied. Observed local catalog: 238 skills, 40 roots, 42 name collisions;
  first-root precedence remains the duplicate-name rule.
- Create starts at 15 seconds, 720p and 24fps. Advanced styling and subscription
  options are collapsed. Jobs expose prerequisites, progress, errors and recovery.
  Invalid scene edits preserve the draft; revised aspect ratios and portrait
  previews work. Blender is disabled when unavailable.
- Updated operator guides, plan and [ADR-009](../DECISIONS.md). Existing dirty work
  was preserved. No commit, publication, purchase or persistent governance edit.

## Validation

Observed on this Linux host on September 7, 2026 (America/Chicago):

- Final locked regression suite: **1,896 passed, 35 skipped, 7 warnings in 189.76s**. Command:
  `UV_CACHE_DIR=/tmp/dream-polish-uv uv run --locked --no-sync pytest -q --tb=short`.
  Python 3.12.3; local sockets/browser subprocesses permitted. Log:
  `/tmp/dream-polish-evidence/final-suite.log`. An earlier integration run passed
  1,883 tests with 35 skipped and 7 warnings in 187.39s before the final fixes.
- Latest focused chat/bus run: 21 passed in 6.79s; covers visible streams/results,
  refresh without tool replay, failed sends, Stop, GUI approvals, pending permission
  refresh, Skills draft insertion, terminal App integration, elapsed progress and
  retention of uncopyable payloads. Startup/protocol contributor final run: 45
  passed, including competing-process lock and unsafe-file cases. Backend/skills
  contributor: 121 passed plus 32 guidance tests. Media contributor: 61 passed.
  Final whole-suite result above supersedes these as integration evidence.
- Native GTK/VTE/WebKit smoke: seven checks passed, including actual HTML rendering,
  a real frame button evaluation, reconnect, Browser-to-Chat delivery, refused file
  URLs, narrow tabs and queued clean shutdown. `/usr/bin/python3 tests/desktop_smoke.py
  --output /tmp/dream-polish-evidence/native-smoke`; log and screenshots alongside.
  Native onboarding fixture actions and six earlier boundary checks passed.
- Real CPU media export: 1280×720, 24fps, 360 frames, 15.000s; render took 12.70s.
  HTML export succeeded. Evidence `/tmp/dream-media-polish-5f6x1j64/evidence.json`.
  Desktop and narrow Create screenshots were inspected.
- **Real model load:** actual graphical picker loaded all six GLM-5.3-Flash-UD-Q4_K_XL
  shards on two Arc Pro B70 GPUs, ctx 32768, thinking enabled / max effort. Fresh
  preflight found about 32GiB free VRAM per GPU, measured idle utilization, acceptable
  temperatures and 228.86GiB available host RAM. No serving model or conflicting GPU
  compute process was present. Native Chat connected after 89.09s. Actual allocation:
  123.50GiB pinned host banks, 49.24GiB mmap banks, GPU caches 21.56/22.60GiB.
- **Real chat/tools/skill:** exact short reply succeeded at 118.79s, first text at
  114.80s (reported generation about 10.1 tokens/s). Next task used tool_schema,
  read_file, run_bash and skill_open with no tool errors. The UI approval for the
  bounded arithmetic command was explicitly answered Allow once. Final answer:
  “The project codename is Moonflower, and 17×19 equals 323.” Turn took about 477.5s.
  Browser refresh preserved the completed transcript. Screenshots and JSON event
  evidence are in `/tmp/dream-polish-evidence/live-chat-*`; only fixture content.
- **Failed check:** the first live observer timed out at 240s during that second
  task. It did not cancel or resend the task. A read-only observer reconnected to
  the same turn and captured its successful completion. This is recorded as a
  timeout, not a passing four-minute check. Large-model latency is a real limit.
- **Environment limits:** initial sandbox socket/anyio browser tests stalled;
  permitted host runs completed. Some automatic approval reviews timed out;
  subsequent equivalent safe retries succeeded. No action remained review-blocked.
  Graph discovery intermittently returned Transport closed; focused reads and
  session-baseline diffs were used after these failures, not a claimed full graph audit.
- Compileall and `git diff --check` passed. Tracking check initially caught a stale CURRENT timestamp; corrected before
  the final snapshot check. Final check: passed, all 43 changed source paths covered.
- **Not run:** remote CI, every provider/account, every installed skill, ComfyUI
  inference, subscription generation, broad daily-use or long-context quality tests.
  The suite's skips remain unperformed checks; no embeddings/reranker model was loaded.

## Unfinished work

The requested startup-to-tools flow works in bounded real qualification. Large GLM
response latency is not ChatGPT-like; sustained performance and owner daily-use
acceptance remain open. Full skill portability requires each skill's actual tools
and accounts. ComfyUI/subscription qualification remains as described in the media
guide. Browser transcript retention is bounded and lasts only for the active server.
Unknown GPU telemetry is deliberately not treated as a safe load on unqualified hosts.

The isolated native validation session is left running: parent PID 2652130,
bootstrap PID 2652510, owned model PID 2652745, model port 11435. These identities
were rechecked after the live task; recheck again before acting. Runtime is
`/tmp/dream-polish-live/state`, workspace `/tmp/dream-polish-live/workspace`.
Its private GUI connection token is in the mode-0600 native-state file; do not copy
it into public logs. This test session predates the final load-lock correction,
so the running child does not hold that new lock; no competing loader was started.
New sessions use the verified lock implementation. Lock files remain on disk;
flock ownership, not file existence, determines whether a load is active.

To finish this validation session, use the owning window's normal Finish and close
flow and answer its memory question in Chat; it unloads only its own model. Keep
that owner session open while another Dream instance is attached to its model.
No unrelated process was killed or model restarted. Earlier sandbox-only test
sessions that stalled were not treated as evidence; their host lifetime was not
established by sandbox process inspection.

Recovery baseline `/tmp/dream-polish-20260907-baseline.json` contains 410 original
source hashes, not a backup. `/tmp/dream-polish-before/source.tar` preserves source
content from after initial tracking edits. Compare individual paths before restoring;
do not blanket-restore it or reset the large dirty tree. Runtime evidence remains
local under /tmp and can be lost on reboot; this record retains portable outcomes.

## Next steps

Owner can start `dream desktop`, select a workspace and model (or attach to the
running model), then use Chat, Skills or Create. For DREAM-028 engineering follow-up,
profile prompt prefill and repeated tool rounds from the captured GLM trace using
the existing server first. Changes to model defaults need measured responsiveness
and quality evidence. No new load is implied by this handoff; inspect resources and
applicable authorization before one. Record owner acceptance only after real work.

## Files changed

- `README.md`
- `START_HERE.md`
- `docs/desktop.md`
- `docs/media.md`
- `docs/project/CURRENT.md`
- `docs/project/DECISIONS.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-07-desktop-polish.md`
- `docs/runtime-controls.md`
- `docs/superpowers/plans/2026-09-07-desktop-polish.md`
- `dream/config.py`
- `dream/core/backends/openai_compat.py`
- `dream/desktop/launcher.py`
- `dream/desktop/onboarding.py`
- `dream/desktop/startup.py`
- `dream/desktop/window.py`
- `dream/gui/bus.py`
- `dream/gui/conversation.py`
- `dream/gui/media_routes.py`
- `dream/gui/server.py`
- `dream/gui/static/companion.css`
- `dream/gui/static/companion.js`
- `dream/gui/static/index.html`
- `dream/gui/static/media.css`
- `dream/gui/static/media.js`
- `dream/media/composition.py`
- `dream/media/render.py`
- `dream/media/service.py`
- `dream/skills/catalog.py`
- `dream/skills/loader.py`
- `dream/tools/installed_skill_tools.py`
- `dream/tui/app.py`
- `tests/desktop_smoke.py`
- `tests/desktop_startup_native.py`
- `tests/test_desktop_chat.py`
- `tests/test_desktop_companion.py`
- `tests/test_desktop_startup.py`
- `tests/test_guidance.py`
- `tests/test_media_render.py`
- `tests/test_media_routes.py`
- `tests/test_media_ui.py`
- `tests/test_skill_catalog.py`
- `tests/test_tool_skill_reliability.py`
