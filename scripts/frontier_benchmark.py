"""Frontier benchmark (after DREAM-213): three configurations on three read-only tasks, then blind judging.

  (a) Opus alone
  (b) Opus with local Qwen helpers (delegate_local, up to 4 at once)
  (c) a local Qwen lead with local Qwen workers (its task tool, up to 4 at once)

For the lead to run live. Every command prints what it will spend before it spends it; `--dry-run` runs the whole
pipeline on scripted stand-ins (no model, no network) to check the plumbing.

  python scripts/frontier_benchmark.py plan
  python scripts/frontier_benchmark.py run    --out DIR --opus-model M --local-model M [--configs a,b,c] [--tasks ...]
                                              [--timeout-s 900] (--yes | --dry-run)
  python scripts/frontier_benchmark.py judge  --out DIR --judge-provider anthropic|machx --judge-model M
                                              (--yes | --dry-run)
  python scripts/frontier_benchmark.py report --out DIR

Each run is one Dream turn in a fresh workspace holding copies of its task's files, with every Dream path (data,
memory, sessions, logs, settings, plugins, MCP config, the inference leases and the model-launch lock) under
DIR/state: nothing reaches the owner's Dream. A live run needs two things from you:
  - a local engine (DREAM_MACHX_URL) that nothing else uses while it runs: the run's leases are its own
    (DIR/state/leases), so another Dream on that engine would not see them, and the two would share its lanes unaware;
  - a checkout of Dream that is not the live app's -- a worktree or an export: a run creates Dream's custom-tools
    folder (dream/tools/custom) in the checkout it runs from.
Tools: the file readers only (reading, listing and searching the task's workspace); (b) may also call delegate_local,
(c) its task tool, its lead's sub-agents without the verifier. Every other tool is switched off in the run's own
Extensions settings, and a run offered anything more stops before its model is asked. Records:
DIR/runs/<task>__<config>/record.json, output.md, events.jsonl. Every configuration is asked to end with a "## Answer"
section; the judge sees that section alone (the whole answer when it has none), without the lines that are process
artifacts or narration, one answer at a time, its configuration hidden, against a fixed rubric (DIR/judge/). `report`
unblinds it (DIR/report.md, report.json) and says, per answer, whether it had the section and how many lines the
judge did not see.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import re
import shutil
import sys
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]           # the Dream checkout the task files are copied from

TASKS = [
    {"id": "crosscheck",
     "files": ["dream/core/inference_coordination.py", "dream/local/load_lock.py", "dream/agent_activity.py",
               "dream/gui/conversation.py"],
     "prompt": ("Summarise what each of these four modules does, then cross-check them against each other: where one "
                "calls or relies on another, does what it relies on hold? Files: {files}. Report a three-to-five line "
                "summary per module, then every cross-module assumption you checked, marked holds or does not hold, "
                "naming the functions.")},
    {"id": "docs-vs-code",
     "files": ["docs/inference-coordination.md", "dream/core/inference_coordination.py", "docs/nested-dream.md",
               "dream/agent_activity.py"],
     "prompt": ("Find the inconsistencies between the docs and the code in these four files: "
                "docs/inference-coordination.md against dream/core/inference_coordination.py, and "
                "docs/nested-dream.md against dream/agent_activity.py. For each one: the doc's claim (quoted), what "
                "the code does (function), and which of the two is wrong. Say so if you find none.")},
    {"id": "plan-change",
     "files": ["dream/agent_activity.py", "dream/gui/conversation.py", "dream/core/backends/anthropic.py"],
     "prompt": ("Plan, without making it, a small change: each Nested worker row should carry how long its worker has "
                "been running. It touches three modules: dream/agent_activity.py (the row contract), "
                "dream/gui/conversation.py (the reconnect history) and dream/core/backends/anthropic.py (the Claude "
                "sub-agent rows). For each module: what to change, where (function), and the tests to add; then the "
                "risks.")},
]

CONFIGS = {
    "a": {"label": "Opus alone", "provider": "anthropic",
          "instruction": "Work on this yourself, reading the files you need. Do not delegate."},
    "b": {"label": "Opus with 4 local helpers", "provider": "anthropic",
          "instruction": ("Split the independent parts into up to 4 self-contained sub-tasks and run them together in "
                          "one delegate_local call (local helpers), then check and combine their results.")},
    "c": {"label": "Local lead with 4 local workers", "provider": "machx",
          "instruction": ("Split the independent parts into up to 4 self-contained sub-tasks and send them together, "
                          "as task tool calls in one reply, then check and combine their results.")},
}

ANSWER_INSTRUCTION = ('Finish with a section headed exactly "## Answer" that holds the final answer only: nothing '
                      "about how the work was done.")

# The file readers (gate round 1): reading, listing and searching the task's workspace -- the Claude SDK's and Dream's.
FILE_READERS = frozenset({"Read", "Glob", "Grep", "LS", "read_file", "list_dir", "grep"})

RUBRIC = {
    "correctness": "Every claim about the code or docs is true of the files given.",
    "coverage": "Every part the task asks for is answered, for every file it names.",
    "specificity": "Claims name the functions, sections or lines they rest on.",
    "usefulness": "A maintainer could act on it as written, without redoing the work.",
}

# The Opus estimate's assumptions, printed with it.
LEAD_PROMPT_TOKENS = 30_000      # Dream's Claude system prompt and tool definitions, plus the CLI's own
CHARS_PER_TOKEN = 3.5
HELPER_RESULTS_TOKENS = 8_000    # what one delegate_local call returns (it is bounded at 60,000 characters)
JUDGE_CALL_TOKENS = (3_500, 400)  # one judge call: (input, output)

# What the judge sees (gate round 1): the final "## Answer" section, up to the next heading of its level or above --
# the whole answer when there is none -- without the lines that are process artifacts or narration. The tasks' own
# vocabulary stays: workers, sub-agents, Claude, Nested, the local engine, MachX's code names. Nothing marks what went.
ANSWER_HEADING = re.compile(r"^## Answer[ \t]*$", re.MULTILINE)
PROCESS_LINE = re.compile(
    r"^\s*\[Dream\].*\bran \d+ of \d+ tasks\b"          # delegate_local's summary line
    r"|^\s*#+\s*Task \d+ ·"                             # its task headings
    r"|delegate_local|\bhelpers?\b|\btask (?:tools?|calls?)\b|\bworker \d+\b|\bsub-?tasks?\b|\bdelegat"
    r"|\blocal models?\b", re.IGNORECASE)
PRODUCT_NAME = re.compile(r"\bMachX\b")                 # as written: machx.serve and DREAM_MACHX_... stay


def _file_tokens(task, source=SOURCE) -> int:
    return int(sum((source / path).stat().st_size for path in task["files"]) / CHARS_PER_TOKEN)


def estimate(tasks, configs, *, judge_provider=None, source=SOURCE) -> dict:
    """The Opus tokens a run would use, by configuration, from the task files' sizes and the assumptions above."""
    opus_in = opus_out = runs = 0
    for task in tasks:
        files = _file_tokens(task, source)
        if "a" in configs:        # one request per file read, then planning, checking, answering
            requests = len(task["files"]) + 3
            opus_in += requests * LEAD_PROMPT_TOKENS + files * requests // 2
            opus_out += 3_000
            runs += 1
        if "b" in configs:        # the plan and the call, the results, the answer
            opus_in += 3 * LEAD_PROMPT_TOKENS + 2 * HELPER_RESULTS_TOKENS
            opus_out += 2_500
            runs += 1
    judge_in = judge_out = 0
    if judge_provider == "anthropic":
        calls = len(tasks) * len(configs)
        judge_in, judge_out = calls * JUDGE_CALL_TOKENS[0], calls * JUDGE_CALL_TOKENS[1]
    return {"opus_runs": runs, "opus_input_tokens": opus_in, "opus_output_tokens": opus_out,
            "judge_input_tokens": judge_in, "judge_output_tokens": judge_out}


def print_estimate(numbers: dict) -> None:
    print(f"Opus estimate: {numbers['opus_runs']} Opus runs, about {numbers['opus_input_tokens']:,} input tokens "
          f"(mostly prompt-cache reads) and {numbers['opus_output_tokens']:,} output tokens.")
    if numbers["judge_input_tokens"]:
        print(f"  Judge (anthropic): about {numbers['judge_input_tokens']:,} input and "
              f"{numbers['judge_output_tokens']:,} output tokens.")
    print(f"  Assumptions: a {LEAD_PROMPT_TOKENS:,}-token lead prompt, {CHARS_PER_TOKEN} characters per token, "
          f"{HELPER_RESULTS_TOKENS:,} tokens of helper results. The real counts and cost are recorded per run.")


def isolate(out: Path) -> Path:
    """Every Dream path under OUT/state, set before Dream is imported (config derives them from DREAM_ROOT). The
    inference leases and the model-launch lock get private folders there too, whatever the parent set (gate round 1):
    a run never takes its leases in the owner's live folder (/tmp/dream-inference-<uid>). Dream makes and checks them
    as its own: absolute, this user's, mode 0700, no symlink."""
    if "dream.config" in sys.modules:
        raise SystemExit("Dream was imported before its paths were isolated; run this script directly.")
    state = (out / "state").resolve()
    state.mkdir(parents=True, exist_ok=True)
    os.environ["DREAM_ROOT"] = str(state)
    for name in ("DREAM_DB", "DREAM_PLUGINS_DIR", "DREAM_MCP_CONFIG", "DREAM_LIBRARY_DB", "DREAM_MODEL_PRESETS"):
        os.environ.pop(name, None)
    os.environ.update(DREAM_SEMANTIC_MEMORY="0", DREAM_RERANK="0", DREAM_CONSOLIDATE="0",
                      DREAM_EXTENSION_SETTINGS=str(state / "extensions.json"),
                      DREAM_INFERENCE_LEASE_DIR=str(state / "leases"), DREAM_MACHX_LOAD_LOCK_DIR=str(state / "locks"))
    return state


def _workspace(task, run_dir: Path, source=SOURCE) -> Path:
    workspace = run_dir / "workspace"
    for path in task["files"]:
        (workspace / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / path, workspace / path)
    return workspace


def _prompt(task, config_id) -> str:
    return "\n\n".join([task["prompt"].format(files=", ".join(task["files"])), CONFIGS[config_id]["instruction"],
                        ANSWER_INSTRUCTION])


def _tools_for(config_id) -> None:
    """This run's tools (gate round 1): the file readers, plus (b) its delegate_local; (c)'s task tool is its backend's
    own. Every other tool the Engine offers is switched off in the run's Extensions settings (DREAM_EXTENSION_SETTINGS,
    under DIR/state): not offered, and refused if called -- the read-only ones included (web_search, browse, the GitHub
    reads, the UI tools), which Dream pre-approves without asking the permission callback. (b)'s helpers and (c)'s
    workers get the session's tools. A custom tool in the checkout needs a trust this state never gives, so it never
    loads; run_one checks what the Engine offered."""
    from dream import extensions
    from dream.tools import registry
    from dream.tools.capability_tools import CAPABILITY_TOOLS
    from dream.tools.demonstration_tools import DEMONSTRATION_TOOLS
    from dream.tools.moe_tools import moe_tools
    from dream.tools.native import NATIVE_TOOLS
    wanted = {tool.name: tool.name in FILE_READERS
              for tool in [*registry._BASE_TOOLS, *NATIVE_TOOLS, *DEMONSTRATION_TOOLS, *CAPABILITY_TOOLS, *moe_tools()]}
    wanted["delegate_local"] = config_id == "b"
    for name, on in wanted.items():
        if extensions.is_enabled(f"tool:{name}") != on:
            extensions.set_enabled(f"tool:{name}", on)


def _permission(config_id):
    """The run's permission callback, for the lead and, through the same callback, (b)'s helpers and (c)'s workers:
    the file readers, (b) its delegate_local, (c) its task tool. Everything else is refused: writes, shell, the Claude
    SDK's own sub-agents (Agent/Task), the web, GitHub, the UI tools."""
    async def decide(tool_name, tool_input):
        short = tool_name.rsplit("__", 1)[-1]
        return short in FILE_READERS or (config_id == "b" and short == "delegate_local") or (
            config_id == "c" and short == "task")
    return decide


@contextmanager
def _without_verifier():
    """(Gate round 2) While (c)'s Engine starts, its local lead's sub-agents come without the verifier. With it the
    lead is offered fork_verifier_agent, a page checker: inert with the UI tools off, but it still opens a card."""
    from dream.core import subagents
    every = subagents.local_subagents
    subagents.local_subagents = lambda: {name: spec for name, spec in every().items() if name != "verifier"}
    try:
        yield
    finally:
        subagents.local_subagents = every


class _FirstRequestGuard:
    """(Gate round 2) An httpx request hook on the local lead's client: the tools its first chat request offers -- the
    payload, which holds tools the registry never lists, such as fork_verifier_agent -- kept for the record. If they go
    past `allowed`, that request is stopped before it is sent, and so is every chat request after it."""

    def __init__(self, allowed):
        self.allowed, self.offered, self.extra = allowed, None, []

    async def __call__(self, request) -> None:
        if not request.url.path.endswith("/chat/completions"):
            return
        if self.offered is None:
            self.offered = sorted(tool["function"]["name"] for tool in json.loads(request.content).get("tools") or [])
            self.extra = sorted(set(self.offered) - self.allowed)
        if self.extra:
            raise RuntimeError(f"the first request offered tools past the file readers ({', '.join(self.extra)})")


def _local_tokens(events, runtime_rows, config_id, result) -> dict:
    """(b): the delegate_local entries the turn's runtime record holds (DREAM-213), else the workers' rows; (c): the
    local lead's own count, which already holds its workers' (delegated) tokens."""
    if config_id == "c":
        stats = (result or {}).get("stats") or {}
        return {"prompt_tokens": stats.get("prompt_tokens"), "completion_tokens": stats.get("completion_tokens"),
                "source": "local lead (workers included)"}
    entries = [row for row in runtime_rows if row.get("event") == "delegate_local"]
    if entries:
        return {"prompt_tokens": sum(row.get("prompt_tokens") or 0 for row in entries),
                "completion_tokens": sum(row.get("completion_tokens") or 0 for row in entries),
                "source": "runtime record (delegate_local)"}
    rows = [event["data"] for event in events if event["kind"] == "agent_activity"
            and event["data"].get("kind") == "response" and not str(event["data"].get("run_id")).startswith("claude-")]
    return {"prompt_tokens": sum((row.get("usage") or {}).get("prompt_tokens", 0) for row in rows),
            "completion_tokens": sum((row.get("usage") or {}).get("completion_tokens", 0) for row in rows),
            "source": "worker rows"}


# What each configuration's stand-in answers: its narration, then (a, b) the "## Answer" section -- (b)'s with one of
# delegate_local's summary lines in it -- or (c) no section at all. The judge's packets must come out the same.
DRY_ANSWERS = {"a": "[dry run] Read the four files one after another.\n\n## Answer\n[dry run] Two issues found.",
               "b": ("[dry run] Split the work for four local helpers.\n\n## Answer\n[Dream] delegate_local ran 4 of 4 "
                     "tasks on the local engine's dry (4 lanes) in 0 s.\n[dry run] Two issues found."),
               "c": "[dry run] Sent four task calls in one reply.\n[dry run] Two issues found."}


class _DryEngine:
    """A scripted stand-in for Engine (the dry run): the same calls, events shaped like each configuration's."""

    def __init__(self, *, provider, model, workspace, emit, can_use_tool, config_id, task_id):
        self.emit, self.config_id, self.task_id, self.session_id = emit, config_id, task_id, f"dry-{task_id}-{config_id}"

    async def start(self):
        pass

    async def ask(self, prompt):
        from dream.core.backends.base import Event
        if self.config_id in "bc":
            for n in range(4):
                self.emit(Event("agent_activity", {"run_id": f"dry{n}", "agent": "worker", "phase": "subagent",
                                                   "kind": "response", "usage": {"prompt_tokens": 900,
                                                                                 "completion_tokens": 300}}))
        yield Event("assistant_done", DRY_ANSWERS[self.config_id])
        yield Event("result", {"is_error": False, "subtype": "success", "total_cost_usd": 0.0 if self.config_id != "c" else None,
                               "usage": {"input_tokens": 1200, "output_tokens": 300} if self.config_id != "c" else None,
                               "stats": {"prompt_tokens": 4800, "completion_tokens": 1500} if self.config_id == "c" else None})

    async def stop(self, consolidate=True, label=None):
        return None


async def run_one(task, config_id, args, run_dir: Path) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    workspace = _workspace(task, run_dir)
    config = CONFIGS[config_id]
    model = args.local_model if config["provider"] == "machx" else args.opus_model
    events = []

    def emit(event):
        events.append({"kind": event.kind, "data": event.data, "t": round(time.monotonic() - started, 3)})
    started = time.monotonic()
    _tools_for(config_id)
    if args.dry_run:
        engine = _DryEngine(provider=config["provider"], model=model, workspace=workspace, emit=emit,
                            can_use_tool=_permission(config_id), config_id=config_id, task_id=task["id"])
    else:
        from dream.core.engine import Engine
        engine = Engine(provider=config["provider"], model=model, workspace=workspace, emit=emit,
                        can_use_tool=_permission(config_id))
    outcome, result, answers, tools, guard = "completed", None, [], None, None

    async def turn():
        nonlocal result
        async for event in engine.ask(_prompt(task, config_id)):
            emit(event)
            if event.kind == "assistant_done":
                answers.append(event.data)
            elif event.kind == "result":
                result = event.data
    with _without_verifier() if config_id == "c" and not args.dry_run else nullcontext():
        await engine.start()
    try:
        if not args.dry_run:
            tools = sorted(engine.tool_names)
            if config["provider"] == "anthropic":     # the CLI builds its requests: what Dream's tool server offers it
                extra = sorted(set(tools) - FILE_READERS - ({"delegate_local"} if config_id == "b" else set()))
                if extra:
                    raise RuntimeError(f"the session was offered tools past the file readers ({', '.join(extra)}); "
                                       "nothing was asked")
            else:                                     # the local lead's own first request (gate round 2)
                guard = _FirstRequestGuard(FILE_READERS | {"task"})
                engine.backend._client.event_hooks["request"].append(guard)
        await asyncio.wait_for(turn(), timeout=args.timeout_s)
    except asyncio.TimeoutError:
        outcome = "timeout"
    except Exception as exc:
        outcome = f"error: {type(exc).__name__}: {exc}"
    finally:
        await engine.stop(consolidate=False)
    wall = round(time.monotonic() - started, 3)
    if result is not None and result.get("is_error"):
        outcome = "error: " + str(result.get("completion_error") or result.get("subtype"))
    if guard is not None and guard.extra:
        outcome = (f"error: the first request offered tools past the file readers ({', '.join(guard.extra)}); "
                   "nothing was asked")
    runtime_rows = []
    runtime = Path(os.environ.get("DREAM_ROOT", "")) / "var" / "logs" / "runtime" / f"{engine.session_id}.jsonl"
    if runtime.is_file():
        runtime_rows = [json.loads(line) for line in runtime.read_text().splitlines() if line.strip()]
    record = {
        "task": task["id"], "config": config_id, "label": config["label"], "provider": config["provider"],
        "model": model, "local_model": args.local_model if config_id in "bc" else None, "tools": tools,
        "first_request_tools": guard.offered if guard is not None else None, "outcome": outcome, "wall_s": wall,
        "opus": ({"usage": (result or {}).get("usage"), "total_cost_usd": (result or {}).get("total_cost_usd")}
                 if config["provider"] == "anthropic" else None),
        "local": _local_tokens(events, runtime_rows, config_id, result) if config_id in "bc" else None,
        "workers": len({event["data"].get("run_id") for event in events if event["kind"] == "agent_activity"}),
        "dry_run": bool(args.dry_run),
    }
    (run_dir / "record.json").write_text(json.dumps(record, indent=2))
    (run_dir / "output.md").write_text(answers[-1] if answers else "")
    with (run_dir / "events.jsonl").open("w") as out:
        for event in events:
            out.write(json.dumps(event, default=str)[:20_000] + "\n")
    return record


def _check_local_engine(args) -> str | None:
    """None when the local engine answers with the model to use, else why not."""
    import httpx
    url = os.environ.get("DREAM_MACHX_URL", "http://localhost:11435/v1")
    try:
        models = [entry["id"] for entry in httpx.get(f"{url}/models", timeout=5).json().get("data", [])]
    except Exception as exc:
        return f"no local engine answers at {url} ({type(exc).__name__})"
    if args.local_model not in models:
        return f"the local engine at {url} serves {models}, not {args.local_model}"
    if len(models) > 1:
        return f"the local engine at {url} serves several models ({models}); serve {args.local_model} alone"
    return None


def cmd_plan(args) -> int:
    configs = args.configs.split(",")
    tasks = [task for task in TASKS if task["id"] in args.tasks.split(",")]
    for task in tasks:
        print(f"task {task['id']}: {len(task['files'])} files, about {_file_tokens(task):,} tokens")
    print_estimate(estimate(tasks, configs, judge_provider=args.judge_provider))
    print("A live run needs a local engine nothing else uses while it runs (DREAM_MACHX_URL): the run's leases are its "
          "own (DIR/state/leases), so another Dream on that engine would not see them.")
    print("Run it from a worktree or an export of Dream, never the live app's checkout: a run creates Dream's "
          "custom-tools folder (dream/tools/custom) in the checkout it runs from.")
    return 0


def cmd_run(args) -> int:
    out = Path(args.out)
    isolate(out)
    configs = args.configs.split(",")
    tasks = [task for task in TASKS if task["id"] in args.tasks.split(",")]
    print_estimate(estimate(tasks, configs))
    if not (args.yes or args.dry_run):
        print("Nothing was run: pass --yes to spend it, or --dry-run to check the pipeline.")
        return 2
    if not args.dry_run and set(configs) & {"b", "c"}:
        problem = _check_local_engine(args)
        if problem:
            print(f"Nothing was run: configurations b and c need the local engine, and {problem}.")
            return 2
    for task in tasks:
        for config_id in configs:
            record = asyncio.run(run_one(task, config_id, args, out / "runs" / f"{task['id']}__{config_id}"))
            print(f"{task['id']:>13} {config_id}: {record['outcome']}, {record['wall_s']} s")
    return 0


JUDGE_SYSTEM = ("You grade one answer to a code-review task against a fixed rubric. Grade only what is written. "
                "Reply with JSON only.")


def _answer_section(answer: str) -> tuple[str, bool]:
    """The last "## Answer" section, up to the next heading of its level or above -> (it, True); without one, (the
    whole answer, False)."""
    heads = list(ANSWER_HEADING.finditer(answer))
    if not heads:
        return answer, False
    rest = answer[heads[-1].end():]
    end = re.search(r"^#{1,2} ", rest, re.MULTILINE)
    return (rest[:end.start()] if end else rest), True


def _judged_text(answer: str, model_ids) -> tuple[str, dict]:
    """What the judge sees of an answer -> (the text, what was done to it: for the report, never the judge): its
    section (or the whole answer) without the process lines, the product name MachX, or a configured model's id."""
    text, sectioned = _answer_section(answer)
    ids = [re.escape(model_id) for model_id in model_ids if model_id]
    named = re.compile("|".join(ids), re.IGNORECASE) if ids else None
    lines = text.split("\n")
    kept = [line for line in lines
            if not (PROCESS_LINE.search(line) or PRODUCT_NAME.search(line) or (named and named.search(line)))]
    return (re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip("\n"),
            {"answer_section": sectioned, "lines_removed": len(lines) - len(kept)})


def _judge_prompt(task, answer: str) -> str:
    criteria = "\n".join(f"- {name}: {text} (1 = not at all, 5 = fully)" for name, text in RUBRIC.items())
    return (f"The task:\n{task['prompt'].format(files=', '.join(task['files']))}\n\nThe rubric:\n{criteria}\n\n"
            f"The answer:\n<answer>\n{answer}\n</answer>\n\nReply with exactly: "
            '{"scores": {' + ", ".join(f'"{name}": n' for name in RUBRIC) + '}, "notes": "one sentence"}')


def _parse_scores(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    try:
        value = json.loads(match.group(0)) if match else None
    except ValueError:
        return None
    scores = value.get("scores") if isinstance(value, dict) else None
    if not isinstance(scores, dict) or set(scores) != set(RUBRIC) or not all(
            type(score) is int and 1 <= score <= 5 for score in scores.values()):
        return None
    return {"scores": scores, "total": sum(scores.values()), "notes": str(value.get("notes", ""))[:500]}


async def _judge_call(provider, model, prompt, dry_run) -> tuple[str, dict]:
    if dry_run:
        digest = hashlib.sha256(prompt.encode()).digest()
        scores = {name: 1 + digest[i] % 5 for i, name in enumerate(RUBRIC)}
        return json.dumps({"scores": scores, "notes": "dry run"}), {}
    if provider == "machx":
        import httpx
        url = os.environ.get("DREAM_MACHX_URL", "http://localhost:11435/v1")
        async with httpx.AsyncClient(timeout=600) as client:
            reply = (await client.post(f"{url}/chat/completions", json={
                "model": model, "temperature": 0, "max_tokens": 600,
                "messages": [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": prompt}]})).json()
        return reply["choices"][0]["message"]["content"] or "", reply.get("usage") or {}
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query
    parts, usage = [], {}
    options = ClaudeAgentOptions(model=model, system_prompt=JUDGE_SYSTEM, max_turns=1, tools=[], setting_sources=[])
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            parts.extend(block.text for block in message.content if isinstance(block, TextBlock))
        elif isinstance(message, ResultMessage):
            usage = {"usage": message.usage, "total_cost_usd": message.total_cost_usd}
    return "".join(parts), usage


def cmd_judge(args) -> int:
    out = Path(args.out)
    isolate(out)
    records = [json.loads(path.read_text()) for path in sorted((out / "runs").glob("*/record.json"))]
    configs = sorted({record["config"] for record in records})
    tasks = [task for task in TASKS if task["id"] in {record["task"] for record in records}]
    print_estimate({"opus_runs": 0, "opus_input_tokens": 0, "opus_output_tokens": 0,
                    **{k: v for k, v in estimate(tasks, configs, judge_provider=args.judge_provider).items()
                       if k.startswith("judge")}})
    if not (args.yes or args.dry_run):
        print("Nothing was judged: pass --yes to spend it, or --dry-run to check the pipeline.")
        return 2
    judge_dir = out / "judge"
    ids = sorted({model for record in records for model in (record.get("model"), record.get("local_model")) if model})
    key = {}
    for task in tasks:
        present = [record["config"] for record in records if record["task"] == task["id"]]
        random.Random(f"{args.seed}:{task['id']}").shuffle(present)
        key[task["id"]] = {chr(ord("A") + n): config_id for n, config_id in enumerate(present)}
        for label, config_id in key[task["id"]].items():
            answer = (out / "runs" / f"{task['id']}__{config_id}" / "output.md").read_text()
            judged, extraction = _judged_text(answer, ids)
            prompt = _judge_prompt(task, judged)
            packet = judge_dir / task["id"] / f"{label}.md"
            packet.parent.mkdir(parents=True, exist_ok=True)
            packet.write_text(prompt)
            text, usage = asyncio.run(_judge_call(args.judge_provider, args.judge_model, prompt, args.dry_run))
            verdict = _parse_scores(text)
            (judge_dir / task["id"] / f"{label}.json").write_text(json.dumps(
                {"verdict": verdict, "raw": text[:4000], "usage": usage,
                 "judge": [args.judge_provider, args.judge_model], **extraction}, indent=2, default=str))
            print(f"{task['id']:>13} {label}: {verdict['total'] if verdict else 'unparsed'}")
    (judge_dir / "key.json").write_text(json.dumps(key, indent=2))
    return 0


def cmd_report(args) -> int:
    out = Path(args.out)
    key = json.loads((out / "judge" / "key.json").read_text()) if (out / "judge" / "key.json").is_file() else {}
    rows = []
    for path in sorted((out / "runs").glob("*/record.json")):
        record = json.loads(path.read_text())
        label = next((lab for lab, cfg in key.get(record["task"], {}).items() if cfg == record["config"]), None)
        verdict_file = out / "judge" / record["task"] / f"{label}.json"
        judged = json.loads(verdict_file.read_text()) if label and verdict_file.is_file() else {}
        verdict = judged.get("verdict")
        opus_usage = (record.get("opus") or {}).get("usage") or {}
        rows.append({**record, "blind_label": label, "score": verdict["total"] if verdict else None,
                     "scores": verdict["scores"] if verdict else None,
                     "answer_section": judged.get("answer_section"), "lines_removed": judged.get("lines_removed"),
                     "opus_input": sum(v for k, v in opus_usage.items() if k.endswith("input_tokens") and type(v) is int),
                     "opus_output": opus_usage.get("output_tokens"),
                     "opus_cost": (record.get("opus") or {}).get("total_cost_usd")})
    lines = ["| task | config | score | wall s | Opus in | Opus out | Opus $ | local prompt | local completion "
             "| workers | outcome | answer section | lines not judged |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for row in rows:
        local = row.get("local") or {}
        section = {True: "yes", False: "missing"}.get(row["answer_section"], "-")
        lines.append(f"| {row['task']} | {row['config']} ({row['label']}) | {row['score']} | {row['wall_s']} | "
                     f"{row['opus_input'] or '-'} | {row['opus_output'] or '-'} | {row['opus_cost'] if row['opus_cost'] is not None else '-'} | "
                     f"{local.get('prompt_tokens', '-')} | {local.get('completion_tokens', '-')} | {row['workers']} | "
                     f"{row['outcome']} | {section} | "
                     f"{row['lines_removed'] if row['lines_removed'] is not None else '-'} |")
    (out / "report.md").write_text("\n".join(lines) + "\n")
    (out / "report.json").write_text(json.dumps(rows, indent=2, default=str))
    print("\n".join(lines))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run", "judge", "report"):
        sub = commands.add_parser(name)
        sub.add_argument("--out", required=name != "plan")
        sub.add_argument("--configs", default="a,b,c")
        sub.add_argument("--tasks", default=",".join(task["id"] for task in TASKS))
        sub.add_argument("--dry-run", action="store_true")
        sub.add_argument("--yes", action="store_true")
        sub.add_argument("--opus-model", default=None)
        sub.add_argument("--local-model", default=None)
        sub.add_argument("--timeout-s", type=float, default=900.0)
        sub.add_argument("--judge-provider", choices=["anthropic", "machx"], default=None)
        sub.add_argument("--judge-model", default=None)
        sub.add_argument("--seed", default="213")
    args = parser.parse_args(argv)
    if args.command == "run" and not args.dry_run and set(args.configs.split(",")) & {"c"} and not args.local_model:
        parser.error("configuration c needs --local-model")
    if args.command == "judge" and not args.dry_run and not (args.judge_provider and args.judge_model):
        parser.error("judging needs --judge-provider and --judge-model")
    return {"plan": cmd_plan, "run": cmd_run, "judge": cmd_judge, "report": cmd_report}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
