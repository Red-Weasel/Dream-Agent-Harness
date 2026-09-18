"""Anthropic (Claude) backend — wraps the Claude Agent SDK. Tools run in-process via
the SDK's MCP server; this backend just translates SDK messages into Events."""

from __future__ import annotations

import asyncio
import json
import sys

from typing import Any, AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from ... import config
from .base import Backend, Event, PermissionCallback, content_to_text

SAFE_BUILTINS = ["Read", "Glob", "Grep", "LS", "NotebookRead", "TodoWrite"]
DISALLOWED_BUILTINS = ["WebSearch", "WebFetch", "Bash"]


class AnthropicBackend(Backend):
    provider_label = "Claude · Anthropic"

    def __init__(
        self,
        *,
        system_prompt: str,
        mcp_server: Any,
        preapproved_tool_ids: list[str],
        agents: dict[str, Any] | None,
        permission_cb: PermissionCallback | None,
        model: str | None,
        stderr_cb=None,
        cwd: str | None = None,
    ) -> None:
        self.system_prompt = system_prompt
        self.mcp_server = mcp_server
        # ONLY the exempt Dream tools (read-only + own-mind memory) are pre-approved
        # here. Mutating Dream tools and every self-built custom tool are omitted, so
        # the SDK routes them to can_use_tool → the mode policy. Pre-approving all of
        # them (the old behavior) let plan mode and workspace isolation be bypassed.
        self.preapproved_tool_ids = preapproved_tool_ids
        self.agents = agents
        self.permission_cb = permission_cb
        self.model = model
        self.stderr_cb = stderr_cb
        self.cwd = cwd
        self.client: ClaudeSDKClient | None = None
        self._effort = None
        self._budget_failures: list[str] | None = None

    async def _permission_handler(self, tool_name, tool_input, context):
        from ...telemetry.runtime import RunLimit
        if self.permission_cb is None:
            return PermissionResultDeny(message="No permission handler is attached.")
        budget_failures = getattr(self, "_budget_failures", None)
        try:
            ok = await self.permission_cb(tool_name, tool_input)
        except RunLimit as exc:
            if budget_failures is not None:
                budget_failures.append(str(exc))
            return PermissionResultDeny(message=str(exc))
        except Exception:
            ok = False
        return PermissionResultAllow() if ok else PermissionResultDeny(message="Declined by the user.")

    def _build_options(self) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            system_prompt=self.system_prompt,
            mcp_servers={config.MCP_SERVER_NAME: self.mcp_server},
            strict_mcp_config=True,
            # Only the exempt subset is pre-approved; everything else (Write/Edit/
            # Bash, mutating Dream tools, custom tools) routes through can_use_tool.
            allowed_tools=self.preapproved_tool_ids + SAFE_BUILTINS,
            disallowed_tools=DISALLOWED_BUILTINS,
            # "default" routes the rest through can_use_tool, making the TUI's mode
            # cycle + workspace boundary the single permission authority.
            # (acceptEdits would silently bypass the callback.)
            permission_mode="default",
            can_use_tool=self._permission_handler,
            agents=self.agents,
            cwd=str(self.cwd or config.ROOT),
            # Dream assembles project guidance and runs its reviewed hooks.
            # Importing a second settings tree would reintroduce independent
            # hooks and automatic permission grants behind those controls.
            setting_sources=[],
            settings='{"disableAllHooks":true}',
            include_partial_messages=True,
            model=self.model,
            effort=self._effort,
            env={
                "CLAUDE_AGENT_SDK_CLIENT_APP": "dream/0.1.0",
                "ENABLE_TOOL_SEARCH": "auto:100",
            },
            stderr=self.stderr_cb,
        )

    async def connect(self) -> None:
        self.client = ClaudeSDKClient(self._build_options())
        await self.client.connect()

    async def ask(self, prompt: str) -> AsyncIterator[Event]:
        import types
        if self.client is None:
            raise RuntimeError("Backend not connected.")
        inherited_exception = sys.exception()
        budget_failures: list[str] = []
        self._budget_failures = budget_failures
        try:
            await self.client.query(prompt)
        except BaseException:
            if self._budget_failures is budget_failures:
                self._budget_failures = None
            raise
        tool_by_id: dict[str, str] = {}
        response = self.client.receive_response()
        result = None
        failure = None
        failures = []
        closing = False
        read_cancels = []

        @types.coroutine
        def observe(awaitable, cancellations):
            iterator = awaitable.__await__()
            try:
                value = next(iterator)
                while True:
                    pending = None
                    try:
                        sent = yield value
                    except BaseException as exc:
                        pending = exc
                    # Forward outside the handler to preserve provider contexts.
                    if isinstance(pending, GeneratorExit):
                        close = getattr(iterator, 'close', None)
                        if close is not None:
                            close()
                        raise pending
                    if pending is not None:
                        if isinstance(pending, asyncio.CancelledError):
                            cancellations[:] = [pending]
                        throw = getattr(iterator, 'throw', None)
                        if throw is None:
                            raise pending
                        value = throw(pending)
                    else:
                        value = next(iterator) if sent is None else iterator.send(sent)
            except StopIteration as finished:
                return finished.value

        try:
            iterator = aiter(response)
            while True:
                read_cancels = []
                try:
                    msg = await observe(anext(iterator), read_cancels)
                except StopAsyncIteration:
                    break
                read_cancels = []
                if getattr(msg, "parent_tool_use_id", None):
                    continue  # suppress nested sub-agent output
                if isinstance(msg, StreamEvent):
                    ev = msg.event or {}
                    if ev.get("type") == "content_block_delta":
                        d = ev.get("delta", {})
                        if d.get("type") == "text_delta":
                            yield Event("text_delta", d.get("text", ""))
                        elif d.get("type") == "thinking_delta":
                            yield Event("thinking_delta", d.get("thinking", ""))
                elif isinstance(msg, AssistantMessage):
                    text_parts: list[str] = []
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)
                        elif isinstance(block, ToolUseBlock):
                            tool_by_id[block.id] = block.name
                            yield Event("tool_use", {"name": block.name, "input": block.input, "id": block.id})
                        elif isinstance(block, ThinkingBlock):
                            pass
                    full = "".join(text_parts).strip()
                    if full:
                        yield Event("assistant_done", full)
                elif isinstance(msg, UserMessage):
                    if isinstance(msg.content, list):
                        for block in msg.content:
                            if isinstance(block, ToolResultBlock):
                                name = tool_by_id.get(block.tool_use_id, "tool")
                                yield Event("tool_result", {
                                    "name": name,
                                    "content": content_to_text(block.content),
                                    "is_error": bool(block.is_error),
                                    "id": block.tool_use_id,  # correlate to its tool_use
                                })
                elif isinstance(msg, ResultMessage):
                    result = {
                        "is_error": msg.is_error,
                        "num_turns": msg.num_turns,
                        "duration_ms": msg.duration_ms,
                        "total_cost_usd": msg.total_cost_usd,
                        "subtype": msg.subtype,
                        "usage": msg.usage,
                        "api_error_status": getattr(msg, "api_error_status", None),
                        "terminal_reason": getattr(msg, "terminal_reason", None),
                    }
        except BaseException as exc:
            failure = exc
            failures.append((exc, read_cancels[0] if read_cancels else None))
            closing = isinstance(exc, GeneratorExit)
        finally:
            # Own just this response, in the task that entered it. The connected
            # SDK client survives turns; receive_response stops at its first result.
            close_cancels = []
            try:
                await observe(response.aclose(), close_cancels)
            except BaseException as exc:
                failures.append((exc, close_cancels[0] if close_cancels else None))
                if failure is None or isinstance(failure, GeneratorExit):
                    failure = exc
            finally:
                if self._budget_failures is budget_failures:
                    self._budget_failures = None

        if failure is not None:
            # Keep both boundary exceptions: explicit close can replace a new
            # cancellation even after an earlier read fault. Context inherited
            # from a caller's recovery handler belongs to the previous request.
            seen = set()
            # Reserve room for both boundary failures before their contexts,
            # so a long read error cannot hide a later cleanup failure.
            details = [f"{type(exc).__name__}: {str(exc)[:120]}" for exc, _ in failures]
            cancellation = None
            for boundary_error, injected in failures:
                if cancellation is None and isinstance(boundary_error, asyncio.CancelledError):
                    cancellation = boundary_error
                cause = boundary_error
                while (cause is not None and cause is not inherited_exception
                       and id(cause) not in seen):
                    seen.add(id(cause))
                    if cancellation is None and cause is injected:
                        cancellation = cause
                    if cause is not boundary_error:
                        details.append(f"{type(cause).__name__}: {str(cause)[:120]}")
                    details.extend(str(note)[:120] for note in getattr(cause, "__notes__", ()))
                    cause = cause.__context__
            diagnostic = json.dumps("; ".join(details)[:500])
            if cancellation is not None:
                if any(exc is not cancellation for exc, _ in failures):
                    cancellation.add_note("SDK response cleanup failed: " + diagnostic)
                raise cancellation
            if closing or not isinstance(failure, Exception):
                raise failure
            error = "SDK response failed: " + diagnostic
        elif result is None:
            error = "SDK response ended without a terminal result. Review the partial results before continuing."
        elif (result["is_error"] or result["subtype"] != "success"
              or result["terminal_reason"] not in (None, "completed")):
            subtype = json.dumps(str(result["subtype"])[:120])
            reason = json.dumps(str(result["terminal_reason"])[:120])
            error = (f"SDK turn incomplete. Reported subtype: {subtype}; terminal reason: {reason}. "
                     "Review the partial results before continuing.")
        else:
            error = None
        if budget_failures:
            budget_error = budget_failures[0]
            error = f"{error} {budget_error}" if error else budget_error
        if result is not None:
            if error:
                # Keep raw provider fields and accounting, but make contradictory
                # or unsettled completion rejectable by existing consumers.
                result["is_error"] = True
                result["completion_error"] = error
            yield Event("result", result)
        if error:
            yield Event("error", error)

    async def interrupt(self) -> None:
        if self.client is not None:
            await self.client.interrupt()

    async def set_model(self, model: str | None) -> None:
        if self.client is not None:
            await self.client.set_model(model)
        self.model = model

    def set_effort(self, level: str | None) -> None:
        # SDK effort belongs to initial options. Engine recreates this backend
        # between turns when Council changes it on a connected session.
        self._effort = level

    async def context_usage(self) -> dict[str, Any] | None:
        if self.client is None:
            return None
        try:
            usage = await self.client.get_context_usage()
            return usage if isinstance(usage, dict) else getattr(usage, "__dict__", None)
        except Exception:
            return None

    async def disconnect(self) -> None:
        if self.client is not None:
            await self.client.disconnect()
            self.client = None
