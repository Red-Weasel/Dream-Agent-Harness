"""DREAM-213: `delegate_local`, a Claude lead's local helpers (dream.core.local_helpers.LocalHelpers).

Offered only to a Claude-led session on a computer with the local engine (Engine._local_helper_tool); the Engine puts
the session's LocalHelpers on the tool context, and the call runs through them with the turn's runtime meter.
"""
from __future__ import annotations

from typing import Any

from claude_agent_sdk import SdkMcpTool

from .context import bound_runtime_meter, ctx, err, ok

SCHEMA = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "minItems": 1,
            "description": "The sub-tasks, run at once; each is complete in itself (its worker sees nothing else).",
            "items": {
                "type": "object",
                "properties": {
                    "subagent_type": {"type": "string", "description": "Which local sub-agent does it."},
                    "prompt": {"type": "string", "description": "Everything the worker needs: the task, the files "
                               "or facts it starts from, and what to report back."},
                },
                "required": ["subagent_type", "prompt"],
            },
        },
    },
    "required": ["tasks"],
}


def delegate_local_tool(*, model: str | None, limit: int | None, names: list[str]) -> SdkMcpTool:
    """The tool, its description naming the served model (when the engine answered at session start), what runs at
    once (the engine's lanes; the owner's max agents when set) and the local sub-agents."""
    served = model or "model (named in each result)"
    at_once = (f"as many at once as the engine has lanes, at most {limit} (the owner's max agents)" if limit is not None
               else "as many at once as the engine has lanes")
    description = (
        f"Run several scoped, self-contained sub-tasks on the local engine's {served}: each task is "
        f"{{subagent_type, prompt}} for one of the local sub-agents ({', '.join(names) or 'none'}). They run as workers "
        f"the owner watches and steers in Nested Dream (stop, pause, message), {at_once}; the rest wait their turn, "
        "so send as many tasks as the job needs. The call returns every result together. Give each task everything "
        "it needs in its prompt. Needs a running local engine.")

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        helpers = getattr(ctx(), "local_helpers", None)
        if helpers is None:
            return err("delegate_local runs only in a Claude-led session on a computer with the local engine; a local "
                       "lead delegates with its task tool.")
        text, failed = await helpers.delegate(args.get("tasks"), meter=bound_runtime_meter())
        return err(text) if failed else ok(text)

    # No maxItems: every task runs; past what runs at once a task waits its turn (LocalHelpers.delegate).
    return SdkMcpTool(name="delegate_local", description=description, input_schema=SCHEMA, handler=handler)
