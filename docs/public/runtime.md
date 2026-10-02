# Runtime, Council, and recovery

Select the intended workspace before starting. Navigation between Chat, Studio,
Terminal, and Browser does not change that workspace or create a new model turn.

Projects can explicitly open a different saved workspace while idle. Skills and
Projects are full pages with editors; see [Projects and Skills](projects-and-skills.md).

## Correct an active turn

During ordinary compatible-HTTP chat, **Steer** saves your correction for the next
lead request, after the current response and tool batch settle. **Queue** keeps its
separate after-turn behavior. Native CLI/SDK agents, guided workflows and autonomous
runs do not support this live route; the interface explicitly reports queued fallback.
That older queue remains in process and is not durable across restart.

Live receipts distinguish **pending** (saved), **included** (added to context),
**submitted** (the HTTP response opened) and **retained** (no confirmed submission).
Submission does not prove the model followed the correction. A request may have
reached the server even when no response opened. Failed acknowledgments preserve
the draft and its receipt identity for retry; stale turn targets are rejected.

Stop or failure retains unfinished live corrections in private session-adjacent
`.steering.json` receipts and session history where capture succeeded. Dream never
automatically replays these after a restart. Inspect the saved text and actual work
before resubmitting. Reloading the browser is not a guarantee against duplicate
deliberate sends with new receipt IDs. The original task and ordered corrections
remain available to the active HTTP request and its scheduled review.

**Stop** interrupts the whole active turn, including a terminal approval prompt
that began before Studio connected. Terminal **Ctrl-C** at that prompt still
declines the individual request and lets the turn handle the denial. Stopping a
turn does not close Dream or automatically continue its unfinished work.

## Running source version

Controls includes Dream version details with the process ID, package import time
and a comparison of selected source files at import versus now. **Changed** means
those files need a new process to load; **Unknown** means the comparison could not
be completed. This covers selected core files, not every plugin or loaded module.
An older server can show **Not reported**. No automatic restart is performed.

## Model and effort

The workspace header shows the selected model and reported effort. Its effort
control opens the adapter's supported performance or main-agent settings. Changes
affect subsequent requests; not every backend supports the same ladder. Thinking
on/off can be a model-loading setting rather than a live effort switch.

Council provides friendly model selections and per-member effort. Apply the roster,
then choose read-only review, an active assignment to one member, or a sequential
team relay. Editing uses ordinary tools and permissions and restores the main agent.
Model selection alone does not invoke a member. Concurrent resident editing is not
implemented.

## Permissions and time limits

Shift+Tab in the Chat composer cycles Ask, Accept edits, Auto, and Plan. The badge
shows the last confirmed mode. A failed acknowledgment remains unconfirmed; a mode
change does not answer an already-open permission request.

Eligible native Bash commands can receive separate session-scoped exact-command
sandbox or host grants. Changing the command, workspace, or execution scope can
require a new decision. These are not universal shell permissions. Provider-owned
executors may enforce additional approval rules.

Active and wall-clock limits default to unlimited and can be explicitly configured.
Approval waiting is separate from active work. Local HTTP generation also has no
read cutoff by default; set `DREAM_LLM_READ_TIMEOUT_S` to request one. Hosted HTTP
adapters keep their profile defaults unless overridden. Context reports the actual
HTTP client's timeout where available. Output/token limits remain separate.

## Settings, sub-agents and context

`dream settings show`, `/settings` in a session and **Controls → Settings** list
every setting with its value and source. `dream settings set <key> <value>` and
`unset` change the saved ones; `dream settings check` reports problems. A refused
value names its key and writes nothing.

| Setting | Values | Default | What it does |
|---|---|---|---|
| `nested.max_workers` | 3, 7, 11, 15 | 7 | Sub-agents one reply may start (4, 8, 12 or 16 agents with the main agent) |
| `engine.parallel` | 1–16 | unset | Lanes the local engine serves; applies the next time Dream starts it |
| `engine.slot_ctx` | 0, or 9 and up | unset | Context of each extra lane; 0 or unset keeps the engine's own size |
| `behaviour.context_overflow` | `compact`, `handoff`, `stop` | `compact` | The main agent at the trigger |
| `behaviour.subagent_overflow` | `compact`, `handoff`, `return` | `return` | A sub-agent at the trigger |
| `behaviour.context_trigger` | 10–95 (percent) | 80 | How full a context gets before the choice acts |
| `behaviour.context_warning` | 0–50 (points) | 5 | How far before the trigger the model is warned; 0 is never |

The worker and engine settings are saved for this computer only; a project file
(`.dream/settings.json`) may hold roles, output and behaviour settings.

**Nested Dream** in the sidebar shows the main agent and its sub-agents as they
work. Sub-agents exist only inside a turn: the main agent starts them with its
`task` tool, and a call past the worker limit is answered with a refusal instead of
run. On a local engine serving lanes they run at once up to the lane count and the
rest queue. Each lane holds its own context, 32,768 tokens by default, so a
sub-agent's window is smaller than the main agent's. **Pause** holds a worker at
its next round boundary. **Stop** ends that worker's call alone; on a local engine
it takes effect at the next round boundary. A worker whose reply budget ran out
while it was thinking returns no output and ends as **Outcome unknown**.

Sub-agents that a Claude session runs appear as read-only cards: Dream cannot
stop, pause or message them. The coding CLIs report no worker activity.

## When a request fails

Inspect the reported error and partial work. Transport diagnostics record exception
category, stage, and effective read timeout without raw response bodies. A client
failure can leave a local server still generating. Check the server, then explicitly
confirm idle in Controls only when that is established. Dream does not automatically
replay an uncertain request.

Resume from saved files and inspect the latest script/output before rerunning a
render or another expensive action. A browser refresh does not load changed Python
backend code; backend upgrades need a new Dream process.

## Studio and visual checks

Studio preparation is not proof of successful rendering or playback. Preview status
separates media events from visual review. HTML media must resolve inside its folder
tree; visible previews inline supported local media within size limits (8 MiB per
file and 32 MiB total before base64). Use smaller previews and downloads for large
outputs. The preview retains an isolated origin and blocks network access.

Vision requires actual image bytes plus a compatible model/adapter. `see` availability
and endpoint image acceptance are separate facts. Screenshot metadata and pixel
statistics are not substitutes for visual review.

[Installation](getting-started.md) · [Privacy](privacy.md)
