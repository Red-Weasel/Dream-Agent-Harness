"""DREAM-213: a Claude lead's local helpers.

`delegate_local` (dream.tools.delegate_tools) hands a Claude-led session's sub-tasks to the local engine in one tool
call. A helper OpenAICompatBackend for the MachX provider runs each task as a full worker, exactly as a local lead's
`task` calls run (_bounded_task: a lane, a Nested card with the owner's Stop, Pause and Message through the Claude
backend's worker_control, the engine's leases), all at once up to the engine's lanes. The call returns every result
together, bounded. One call, because the SDK may not run several MCP calls in parallel.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from anyio import CancelScope

# What one call returns, all of it: the CLI cuts an MCP tool's output at 25,000 tokens by default
# (MAX_MCP_OUTPUT_TOKENS), and a cut there would not say which task lost what. The header takes its part, and the
# tasks that ran share the rest equally, each its heading, its text and any cut note (DREAM-213 gate, round 1). With
# at most 15 tasks run per call (nested.max_workers), each gets about 3,990 characters or more.
RESULT_LIMIT = 60_000


class LocalHelpers:
    """A Claude-led session's local helpers (the Engine sets one on the tool context). `running` holds the helper
    backends with a call in progress, so the owner's worker controls reach their workers (control)."""

    def __init__(self, *, tools, permission_cb, emit, provider=None, subagents=None, lead=None):
        from .providers import get_provider
        self.tools, self.permission_cb, self.emit = list(tools), permission_cb, emit
        self.provider = provider if provider is not None else get_provider("machx")
        self._subagents = subagents                    # None: the local sub-agents as each call finds them
        # () -> the session's lead provider now (the Engine's); the tool was built for a Claude lead, and a session
        # switched to another keeps its tools. None: not checked.
        self._lead = lead
        self.running: list[Any] = []

    def control(self, action: str, run_id: Any = None, text: Any = None) -> dict[str, Any] | None:
        """The owner's Stop, Pause, Resume or Message for a helper's worker -> its answer, or None when no running
        helper has that run (the caller answers then). Pause all pauses every helper worker that is not stopping."""
        if action == "agents_pause_all":
            runs = []
            for helper in self.running:
                if any(worker.state != "stopping" for worker in helper._workers.values()):
                    runs.extend(helper.worker_control("agents_pause_all")["runs"])
            return {"runs": runs} if runs else None
        for helper in self.running:
            if run_id in helper._workers:
                return helper.worker_control(action, run_id, text)
        return None

    async def delegate(self, tasks: Any, *, meter: Any = None) -> tuple[str, bool]:
        """Run `tasks` ([{subagent_type, prompt}, ...]) on the local engine's workers at once -> (what the lead gets,
        whether nothing ran). The model is the one the engine serves; of several, the one roles.main.model names.
        Tasks past the owner's worker limit are not run; one line says so. Cancelled (the owner's interrupt), it stops
        every worker, so every card ends, and the cancellation goes on. The local tokens are recorded on `meter` as
        their own entry (source delegate_local)."""
        lead = self._lead() if self._lead is not None else None
        if lead is not None and lead.kind != "anthropic":
            return (f"[Dream] delegate_local serves a Claude lead, and this session's lead is now {lead.label}: "
                    "delegate with that lead's own tools. Nothing was run."), True
        if not (isinstance(tasks, list) and tasks and all(
                isinstance(task, dict) and isinstance(task.get("subagent_type"), str)
                and isinstance(task.get("prompt"), str) and task["prompt"].strip() for task in tasks)):
            return ("[Dream] delegate_local needs tasks: a list of {subagent_type, prompt}, each prompt a non-empty "
                    "string. Nothing was run."), True
        url = self.provider.base_url
        models = await self._served_models()
        if not models:
            return (f"[Dream] delegate_local needs a running local engine, and none answers at {url}: start one "
                    "(Local model, or `dream local`), then call it again. Nothing was run."), True
        model = self._model(models)
        if model is None:
            return (f"[Dream] The local engine at {url} serves several models "
                    f"({', '.join(entry['id'] for entry in models)}), and roles.main.model names none of them: set it "
                    "to the one the helpers should use, then call again. Nothing was run."), True
        helper = self._helper(model)
        try:
            await helper.connect()
        except Exception as exc:
            await helper.disconnect()
            return (f"[Dream] delegate_local could not connect to the local engine at {url}: {type(exc).__name__}: "
                    f"{exc}. Nothing was run."), True
        cap = helper._worker_cap
        runnable = tasks[:cap]
        started = time.monotonic()
        self.running.append(helper)
        scopes = [CancelScope() for _ in runnable]
        # Each task its own loop-guard history: two tasks alike are two workers, not one worker repeating itself.
        futures = [asyncio.ensure_future(helper._bounded_task(
            {"subagent_type": task["subagent_type"], "prompt": task["prompt"]}, {}, scope))
            for task, scope in zip(runnable, scopes)]
        try:
            outcomes = await asyncio.gather(*(asyncio.shield(future) for future in futures), return_exceptions=True)
        except asyncio.CancelledError:
            for scope in scopes:
                scope.cancel()
            await asyncio.gather(*futures, return_exceptions=True)          # every card ends before this call does
            raise
        finally:
            self.running.remove(helper)
            await helper.disconnect()
        wall = time.monotonic() - started
        if meter is not None:
            try:
                meter.record("delegate_local", source="delegate_local", model=model, tasks=len(tasks),
                             ran=len(runnable), prompt_tokens=helper._delegated_usage["prompt_tokens"],
                             completion_tokens=helper._delegated_usage["completion_tokens"], wall_s=round(wall, 3))
            except Exception:
                pass
        lanes, ran = helper._task_lanes(), len(runnable)
        header = (f"[Dream] delegate_local ran {ran} of {len(tasks)} tasks on the local engine's {model} "
                  f"({lanes} lane{'s' if lanes != 1 else ''}) in {wall:.0f} s.")
        if ran < len(tasks):                                  # one line for them all (DREAM-213 gate, round 1)
            which = f"Task {len(tasks)} was" if ran + 1 == len(tasks) else f"Tasks {ran + 1}-{len(tasks)} were"
            header += f"\n{which} not run: the worker limit is {cap} per call."
        share = (RESULT_LIMIT - len(header)) // ran - 2       # a task's section, and the blank line before it
        sections = [header]
        for number, (task, outcome) in enumerate(zip(runnable, outcomes), 1):
            if isinstance(outcome, BaseException):
                body, failed = f"Error: {type(outcome).__name__}: {outcome}", True
            else:
                body, failed = str(outcome[0]), bool(outcome[1])
            status = "not completed" if failed else "completed"
            sections.append(_bounded(f"### Task {number} · {task['subagent_type']} · {status}\n{body}", share))
        return "\n\n".join(sections), False

    async def _served_models(self) -> list[dict]:
        """The engine's /v1/models entries (their id and root); empty when nothing answers."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
                response = await client.get(f"{self.provider.base_url}/models")
            data = response.json().get("data") if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError, AttributeError):
            return []
        return [entry for entry in data if isinstance(entry, dict) and isinstance(entry.get("id"), str)] \
            if isinstance(data, list) else []

    @staticmethod
    def _model(models: list[dict]) -> str | None:
        """The one model listed, or of several the one roles.main.model names (its id, or the one entry whose root it
        names); None: several, and the setting names none of them."""
        from ..local import machx
        if len(models) == 1:
            return models[0]["id"]
        return machx._by_preference(models, machx.main_role())

    def _helper(self, model: str):
        """The helper backend: the local provider and model, the profile a local lead would get, this session's tools
        (without delegate_local) and permission callback, and the session's emitter for its rows and lanes."""
        from .backends.openai_compat import OpenAICompatBackend
        from .profiles import resolve_profile
        from .subagents import local_subagents
        helper = OpenAICompatBackend(
            provider=self.provider, model=model, system_prompt="", tools=self.tools, permission_cb=self.permission_cb,
            subagents=self._subagents if self._subagents is not None else local_subagents(),
            profile=resolve_profile(self.provider, model=model), worker_streaming=self.emit is not None)
        if self.emit is not None:
            helper.enable_background_filing(self.emit)
        return helper


def _bounded(text: str, limit: int) -> str:
    """`text`, or as much of it as fits in `limit` characters with the note saying how much more was cut."""
    if len(text) <= limit:
        return text
    note = "\n[… {:,} more characters cut; see this worker's card in Nested Dream]"
    keep = limit - len(note.format(len(text)))                # the note, with at most that many digits, fits too
    return text[:keep] + note.format(len(text) - keep)
