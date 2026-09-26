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


def sdk_options(*, system_prompt, mcp_servers, preapproved_tool_ids, agents, can_use_tool, model, effort, cwd,
                stderr=None, disallowed_tools=()) -> ClaudeAgentOptions:
    """The main Claude path's SDK options. Council and review consultations build theirs
    here too (DREAM-137), so their posture cannot drift from the main path's."""
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers=mcp_servers,
        strict_mcp_config=True,
        # Only the exempt subset is pre-approved; everything else (Write/Edit/
        # Bash, mutating Dream tools, custom tools) routes through can_use_tool.
        allowed_tools=list(preapproved_tool_ids) + SAFE_BUILTINS,
        disallowed_tools=DISALLOWED_BUILTINS + list(disallowed_tools),
        # "default" routes the rest through can_use_tool, making the TUI's mode
        # cycle + workspace boundary the single permission authority.
        # (acceptEdits would silently bypass the callback.)
        permission_mode="default",
        can_use_tool=can_use_tool,
        agents=agents,
        cwd=cwd,
        # Dream assembles project guidance and runs its reviewed hooks.
        # Importing a second settings tree would reintroduce independent
        # hooks and automatic permission grants behind those controls.
        setting_sources=[],
        settings='{"disableAllHooks":true}',
        include_partial_messages=True,
        model=model,
        effort=effort,
        env={
            "CLAUDE_AGENT_SDK_CLIENT_APP": "dream/0.1.0",
            "ENABLE_TOOL_SEARCH": "auto:100",
        },
        stderr=stderr,
    )


# DREAM-137: a consultation may not convene the Council again from inside itself.
CONSULT_DISALLOWED = [config.tool_id("consult"), config.tool_id("council")]


# DREAM-137: Dream tools that reach the owner -- a form or question whose answer becomes the main
# session's next prompt, a page, card or widget in the owner's Studio, JS in the owner's open
# view, the Studio's shared hidden frame. Read-only on the main path, refused in every consult.
CONSULT_OWNER_FACING = frozenset({
    "questions_v2", "ask_user_input", "suggest_research", "visual_check", "image_search",
    "eval_js_user_view", "screenshot_user_view", "done", "show_html", "show_to_user", "eval_js",
    "get_webview_logs", "multi_screenshot", "present_fs_item_for_download", "visualize_show_widget",
    "save_screenshot", "end_conversation",
    # offer cards: they also spend the main session's once-per-session offer (plugin_tools._OFFERED)
    "suggest_plugin_install", "suggest_skills",
    # see mirrors what it looks at into the owner's pane; a consult views images with Read
    "see",
    "chart_display_v0", "comparison_card_display_v0", "featured_card_display_v0", "itinerary_display_v0",
    "link_preview_display_v0", "options_card_display_v0", "places_list_display_v0",
    "product_carousel_display_v0", "quiz_display_v0", "step_card_display_v0", "translation_display_v0",
})


def _session_tools() -> dict | None:
    """The main path's Dream tool bundle of the bound session, as the engine sets it."""
    from ...tools.context import ctx
    try:
        return getattr(ctx(), "claude_tools", None)
    except RuntimeError:
        return None


def consult_permission(mode: str, workspace: str, preapproved=(), execution=None):
    """The permission callback of a Claude consultation, which has no owner to ask.

    Dream's policy decides as on the main path, in the consult's mode: what the main
    path runs without a prompt runs; what it would ask the owner about, or denies, is
    refused with the reason. So plan and ask are read-only, and accept-edits/auto may
    write inside the workspace.

    One departure from the main path: Dream's own-mind tools (the session's plan, todos,
    skills, tasks, title, notes and memories), free in every mode there, belong to the
    main session. A consult may add a memory with ``remember`` in accept-edits/auto and
    is refused every other one in every mode, so the prompt optimizer (plan) gets none.
    Tools that reach the owner (CONSULT_OWNER_FACING) are refused in every mode."""
    from pathlib import Path

    from .. import policy

    async def decide(tool_name, tool_input, context):
        if policy._short(tool_name) in CONSULT_OWNER_FACING:
            return PermissionResultDeny(message="Not run: a consultation cannot show things to or ask the owner.")
        if tool_name in preapproved:
            return PermissionResultAllow()
        if policy.capability(tool_name) == policy.MEMORY:
            if policy._short(tool_name) == "remember" and mode in ("accept-edits", "auto"):
                return PermissionResultAllow()
            return PermissionResultDeny(message="Not run: a consultation may not change the main session's "
                                                "plan, todos, skills, tasks or memories.")
        scope, capability = execution() if execution is not None else (None, None)
        native_shell = tool_name in {"run_bash", config.tool_id("run_bash")}
        decision, reason = policy.decide(tool_name, tool_input, mode, Path(workspace),
                                         execution_scope=scope,
                                         execution_capability=capability if native_shell else None)
        if native_shell and capability is not None and not capability.available and decision != "deny":
            decision, reason = "ask", "outside workspace protection unavailable"
        if decision == "allow":
            return PermissionResultAllow()
        why = reason or decision
        if decision == "ask":
            return PermissionResultDeny(message=f"Not run: a consultation cannot ask the owner ({why}).")
        return PermissionResultDeny(message=f"Not run: blocked by Dream's policy ({why}).")

    return decide


def consult_options(*, system_prompt: str, cwd: str, mode: str | None, model, effort,
                    extra_servers: dict | None = None, extra_allowed=()) -> ClaudeAgentOptions:
    """A Claude consultation runs like the main-model Claude path (DREAM-137): the same
    options, the session's Dream tool server, and Dream's policy in the consult's mode
    (no mode runs as ask) in place of the owner's approval prompts, except that the main
    session's own-mind tools are not the consult's (see consult_permission)."""
    from ..subagents import subagents

    from .. import policy

    tools = _session_tools()
    servers = dict(extra_servers or {})
    preapproved = list(extra_allowed)
    execution = None
    if tools:
        servers[config.MCP_SERVER_NAME] = tools["server"]
        # Only the read-only exempt tools are pre-approved; the session's own-mind tools
        # go to consult_permission, which refuses them (see there).
        preapproved = [tool for tool in tools["exempt_tool_ids"]
                       if policy.capability(tool) == policy.READONLY
                       and policy._short(tool) not in CONSULT_OWNER_FACING] + preapproved
        execution = tools.get("execution")
    return sdk_options(
        system_prompt=system_prompt, mcp_servers=servers, preapproved_tool_ids=preapproved,
        agents=subagents(), can_use_tool=consult_permission(mode or "ask", cwd, preapproved, execution),
        model=model, effort=effort, cwd=cwd, disallowed_tools=CONSULT_DISALLOWED,
    )


def consult_provenance(cwd: str, mode: str | None, *, dream_tools: bool) -> dict:
    """How a Claude consultation ran, stored under the key DREAM-136's CLI rows use."""
    scope = ("Claude built-ins Read/Glob/Grep/LS/NotebookRead/TodoWrite (images through Read), Write/Edit by mode, "
             "and the session's Dream tool server (web_search, browse, media_read, run_bash, ...) as on the main path, "
             "except consult, council and the tools that show things to or ask the owner" if dream_tools else
             "Claude built-ins only: no Dream session tools were bound (no web or shell); consult and council "
             "are unavailable")
    return {
        "isolation": "none: runs like the main-model Claude path",
        "configuration": "the main Claude path's SDK options: Dream's own guidance and tools, no settings "
                         "sources, hooks off, strict MCP",
        "tool_scope": scope,
        "cwd": cwd,
        "permission_mode": mode or "ask",
        "sdk_permission_mode": "default",
        "without_asking": "what Dream's policy allows in this mode without a prompt, except the main session's "
                          "plan, todos, skills, tasks and memories (only remember, in accept-edits/auto) and "
                          "every tool that shows things to or asks the owner; "
                          "anything it would ask the owner about is refused",
        "credentials": "owner's own sign-in, used in place",
    }


# DREAM-140, the owner's decision: the Claude advisor and SDK reviewer run like Claude Code in
# VS Code (the owner's settings, Claude Code's prompt and native tools), LOOSER than Dream's own
# main Claude path. The prompt optimizer keeps consult_options above.
CLAUDE_CODE_PERMISSION_MODES = {"plan": "plan", "ask": "plan", "accept-edits": "acceptEdits",
                                "auto": "bypassPermissions"}
_EDIT_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})


def claude_code_refusal(tool_name: str, mode: str) -> str | None:
    """Why a consultation may not run this tool whatever Claude Code's own rules say, or None.

    Dream's tools are matched by name on ANY MCP server, so a Dream bridge registered in the
    owner's Claude config under another name is caught too."""
    from .. import policy

    parts = tool_name.split("__", 2) if tool_name.startswith("mcp__") else ()
    if len(parts) == 3:
        short = parts[2]
        if short in ("consult", "council"):
            return "a consultation may not convene the Council again"
        if short in CONSULT_OWNER_FACING:
            return "a consultation cannot show things to or ask the owner"
        if policy.capability(config.tool_id(short)) == policy.MEMORY and not (
                short == "remember" and mode in ("accept-edits", "auto")):
            return "a consultation may not change the main session's plan, todos, skills, tasks or memories"
    elif tool_name in _EDIT_TOOLS and CLAUDE_CODE_PERMISSION_MODES.get(mode, "plan") == "plan":
        return "plan mode is read-only"
    return None


def claude_code_options(*, system_prompt: str, cwd: str, mode: str | None, model, effort,
                        extra_servers: dict | None = None, extra_allowed=()) -> ClaudeAgentOptions:
    """Claude Code's own posture for a consultation: the owner's user/project/local settings
    (CLAUDE.md, hooks, MCP servers, plugins, permission rules), Claude Code's preset prompt with
    ``system_prompt`` appended, its native tools, and a permission mode mapped from Dream's (no
    mode runs as ask). Dream's tool server is not attached. A consult has no UI, so whatever
    Claude Code would prompt the owner for is refused; a PreToolUse hook, which runs even under
    bypassPermissions, keeps claude_code_refusal's guards in every mode."""
    from claude_agent_sdk import HookMatcher

    mode = mode or "ask"
    preapproved = list(extra_allowed)

    async def decide(tool_name, tool_input, context):
        reason = claude_code_refusal(tool_name, mode)
        if reason is None and tool_name in preapproved:
            return PermissionResultAllow()
        return PermissionResultDeny(message=f"Not run: {reason or 'a consultation cannot ask the owner (Claude Code would prompt)'}.")

    async def guard(input_data, tool_use_id, context):
        reason = claude_code_refusal(str(input_data.get("tool_name", "")), mode)
        if reason is None:
            return {}  # no decision: Claude Code's own rules apply
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": f"Not run: {reason}."}}

    return ClaudeAgentOptions(
        system_prompt={"type": "preset", "preset": "claude_code", "append": system_prompt},
        tools={"type": "preset", "preset": "claude_code"},
        setting_sources=["user", "project", "local"],
        mcp_servers=dict(extra_servers or {}),
        allowed_tools=preapproved,
        disallowed_tools=list(CONSULT_DISALLOWED),
        permission_mode=CLAUDE_CODE_PERMISSION_MODES.get(mode, "plan"),
        can_use_tool=decide,
        hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[guard])]},
        cwd=cwd,
        model=model,
        effort=effort,
        env={"CLAUDE_AGENT_SDK_CLIENT_APP": "dream/0.1.0"},
    )


def claude_code_provenance(cwd: str, mode: str | None) -> dict:
    """How a Claude consultation ran under claude_code_options."""
    sdk_mode = CLAUDE_CODE_PERMISSION_MODES.get(mode or "ask", "plan")
    return {
        "isolation": "none: runs like Claude Code (owner's settings)",
        "configuration": "Claude Code's preset system prompt with Dream's text appended; the owner's user, "
                         "project and local settings (CLAUDE.md, hooks, MCP servers, plugins, permission rules)",
        "tool_scope": "Claude Code's native tools (Bash, WebSearch, WebFetch, Read incl. images, Write, Edit, "
                      "Glob, Grep, ...) and the owner's MCP servers; Dream's tool server is not attached; "
                      "consult, council, Dream's session-state tools and the tools that show things to or ask "
                      "the owner are refused on any MCP server",
        "cwd": cwd,
        "permission_mode": mode or "ask",
        "sdk_permission_mode": sdk_mode,
        "without_asking": f"what Claude Code runs without a prompt in {sdk_mode} under the owner's permission "
                          "rules; anything it would prompt the owner for is refused",
        "credentials": "owner's own sign-in, used in place",
    }


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
        except Exception as exc:   # Dream's own refusal or a failing check -- not the owner's No (DREAM-085)
            from ..permission_refusal import refusal_text
            return PermissionResultDeny(message=refusal_text(exc))
        return PermissionResultAllow() if ok else PermissionResultDeny(message="Declined by the user.")

    def _build_options(self) -> ClaudeAgentOptions:
        return sdk_options(
            system_prompt=self.system_prompt,
            mcp_servers={config.MCP_SERVER_NAME: self.mcp_server},
            preapproved_tool_ids=self.preapproved_tool_ids,
            agents=self.agents,
            can_use_tool=self._permission_handler,
            model=self.model,
            effort=self._effort,
            cwd=str(self.cwd or config.ROOT),
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
