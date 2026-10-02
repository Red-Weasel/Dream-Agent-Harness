"""Anthropic (Claude) backend — wraps the Claude Agent SDK. Tools run in-process via
the SDK's MCP server; this backend just translates SDK messages into Events."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time

from typing import Any, AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TaskNotificationMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from ... import __version__, config
from ...agent_activity import bounded_agent_activity
from .base import Backend, Event, PermissionCallback, content_to_text

SAFE_BUILTINS = ["Read", "Glob", "Grep", "LS", "NotebookRead", "TodoWrite"]
DISALLOWED_BUILTINS = ["WebSearch", "WebFetch", "Bash"]

# DREAM-212: the SDK's sub-agent tool (Task; newer CLIs name it Agent), and why Dream cannot steer what it runs.
SUBAGENT_TOOLS = frozenset({"Task", "Agent"})
WORKER_REFUSAL = "This worker runs inside the Claude SDK; Dream cannot stop, pause or message it."
WORKERS_REFUSAL = "These workers run inside the Claude SDK; Dream cannot stop, pause or message them."
BACKGROUND_TEXT = "Running in the background; the lead was told it started and goes on."
_LOG = logging.getLogger(__name__)


class _SubagentCards:
    """DREAM-212: a Claude-led turn's sub-agents as Nested cards (read-only). The SDK runs them itself and sends their
    messages with parent_tool_use_id = the call that launched them; each becomes agent_activity rows on the contract
    the OpenAI-compatible workers keep (docs/nested-dream.md): run id "claude-<that call's id>", agent its
    subagent_type ("subagent" when none), "Subagent started." at the launch, then per request its request row, any
    streamed text and thinking pieces (coalesced as the workers' are), the response (its text, the SDK's token counts,
    the time from its request to its last block) and the tool rows, and a terminal row when the call's result comes
    back -- or, at the turn's end, for every card still open. The model is the SDK's; no context row: the SDK gives no
    sub-agent's context window.

    DREAM-212 gate, round 1 (the bundled CLI's shapes): the CLI sends a sub-agent's reply one content block at a time,
    with no stream events, so the response row and its tool rows go out when the reply's first tool call comes (tool
    calls end a reply), and the next request row when the tool results go back; a reply's duration ends at its last
    block, not when its tool has run. The CLI runs a sub-agent in the background unless told otherwise: the lead's
    result for the call is then a receipt (tool_use_result status async_launched), the card stays open, and the SDK's
    task notification or update for it ends it -- or the turn's end, as unknown. Display only: AnthropicBackend.ask
    contains its faults, and a broken subscriber cannot abort the turn."""

    def __init__(self, emit):
        from .openai_compat import _DeltaCoalescer        # the workers' coalescing policy (DREAM-190), not a copy
        self.emit, self._coalescer = emit, _DeltaCoalescer
        self.runs: dict[str, dict[str, Any]] = {}          # the launching call's tool_use id -> its card
        self._tasks: dict[str, str] = {}                   # the SDK's task id (a receipt's agentId) -> the call

    def live(self) -> list[str]:
        return [run["activity"]["run_id"] for run in self.runs.values() if not run["ended"]]

    def tool_use(self, block) -> None:
        """A tool call, the lead's or a sub-agent's: a sub-agent launch opens its card, or names the one its frames
        opened before it."""
        data = block.input if isinstance(block.input, dict) else {}
        if block.name in SUBAGENT_TOOLS or "subagent_type" in data:
            agent = data.get("subagent_type")
            run = self._run(block.id, agent)
            if run["activity"]["agent"] == "subagent" and isinstance(agent, str) and agent:
                run["activity"]["agent"] = agent

    def tool_result(self, block, result=None) -> None:
        """A launch's result: its card ends -- completed, or failed with what the lead got -- unless it is the receipt
        of a background launch (`result`, that message's tool_use_result, says so; the CLI's receipt text counts only
        when there is no such result): then the card says so and stays open."""
        run = self.runs.get(block.tool_use_id)
        if run is None or run["ended"]:
            return
        text = content_to_text(block.content)
        self._close_response(run)
        if not block.is_error and (result.get("status") == "async_launched" if isinstance(result, dict)
                                   else text.startswith("Async agent launched successfully")):
            run["background"] = True
            agent_id = result.get("agentId") if isinstance(result, dict) else None
            if isinstance(agent_id, str) and agent_id:
                self._tasks[agent_id] = block.tool_use_id
            self._row(run, "status", status="running", text=BACKGROUND_TEXT)
        elif block.is_error:
            self._end(run, "failed", f"Subagent returned a failed or incomplete result: {text}")
        else:
            self._end(run, "completed", "Subagent finished; its result was returned to the lead agent.")

    def system(self, msg) -> None:
        """The SDK's task messages: which task a launch is (task_started), and a background sub-agent's end (a task
        notification for its call, or a task update for its task)."""
        task_id, call = getattr(msg, "task_id", None), getattr(msg, "tool_use_id", None)
        if isinstance(msg, TaskStartedMessage):
            if call in self.runs and isinstance(task_id, str):
                self._tasks[task_id] = call
            return
        if isinstance(msg, TaskNotificationMessage):
            status, summary = msg.status, msg.summary
        elif isinstance(msg, TaskUpdatedMessage):
            status, summary = msg.status or (msg.patch or {}).get("status"), None
        else:
            return
        run = self.runs.get(call) if call in self.runs else self.runs.get(self._tasks.get(task_id))
        if run is None or run["ended"] or not run["background"]:
            return
        self._close_response(run)
        if status == "completed":
            self._end(run, "completed", "Subagent finished in the background" + (f": {summary}" if summary else "."))
        elif status == "failed":
            self._end(run, "failed", "Subagent returned a failed or incomplete result: "
                      + (summary or "the SDK reports that it failed in the background."))
        elif status in ("stopped", "killed"):
            self._end(run, "interrupted", "Subagent was stopped in the background.")

    def message(self, msg) -> None:
        """A message of a launched sub-agent (parent_tool_use_id): its card's rows."""
        run = self._run(msg.parent_tool_use_id)
        if run["ended"]:
            return
        if isinstance(msg, StreamEvent):
            event = msg.event if isinstance(msg.event, dict) else {}
            if event.get("type") == "message_start":
                message = event.get("message") if isinstance(event.get("message"), dict) else {}
                self._response(run, message.get("id"), message.get("model"), message.get("usage"))
            elif event.get("type") == "content_block_delta":
                delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
                if delta.get("type") in ("text_delta", "thinking_delta"):
                    self._response(run, None, None, None)["streamed"] = True
                    run["deltas"].add(delta["type"], delta.get("text" if delta["type"] == "text_delta" else "thinking"))
            elif event.get("type") == "message_delta" and run["open"] is not None:
                self._response(run, None, None, event.get("usage"))
        elif isinstance(msg, AssistantMessage):
            response = self._response(run, msg.message_id, msg.model, msg.usage)
            response["said"] = True
            for block in msg.content:
                if isinstance(block, TextBlock):
                    response["text"].append(block.text)
                elif isinstance(block, ThinkingBlock):
                    response["thinking"].append(block.thinking)
                elif isinstance(block, ToolUseBlock):
                    self._show_response(run)                 # a tool call ends the reply: it is shown now
                    tool_id = f"{run['activity']['run_id']}:{run['activity']['round']}:{response['calls']}:{block.id[:100]}"
                    response["calls"] += 1
                    run["tools"][block.id] = (tool_id, block.name)
                    self._row(run, "tool_use", status="requested",
                              data={"id": tool_id, "name": block.name, "input": block.input})
                    self.tool_use(block)                     # a launch of its own opens its card, after this row
        elif isinstance(msg, UserMessage):
            self._close_response(run)
            blocks = [block for block in msg.content if isinstance(block, ToolResultBlock)] \
                if isinstance(msg.content, list) else []
            for block in blocks:
                self.tool_result(block, msg.tool_use_result if len(blocks) == 1 else None)
                tool_id, name = run["tools"].pop(block.tool_use_id, (
                    f"{run['activity']['run_id']}:{run['activity']['round']}:-:{block.tool_use_id[:100]}", "tool"))
                self._row(run, "tool_result", status="failed" if block.is_error else "returned", data={
                    "id": tool_id, "name": name, "content": content_to_text(block.content),
                    "is_error": bool(block.is_error)})
            if blocks:
                self._request(run, time.monotonic())         # the results go back with its next request

    def close(self, interrupted: bool) -> None:
        """The turn is over: every card still open ends (gate P4). A background sub-agent's as unknown -- it may still
        run -- any other interrupted when the turn was cancelled or closed, failed when it ended before the sub-agent's
        result came back. A reply that came is shown first; of a cut one, the pieces it sent; a tool call whose result
        never came, as of unknown outcome."""
        for run in self.runs.values():
            if run["ended"]:
                continue
            self._close_response(run)
            if run["background"]:
                self._end(run, "unknown", "Still running in the background when the lead's turn ended; Dream shows no "
                          "more of it.")
                continue
            for tool_id, name in run["tools"].values():
                self._row(run, "tool_result", status="interrupted" if interrupted else "failed", data={
                    "id": tool_id, "name": name, "content": "Tool observation interrupted; outcome is unknown.",
                    "is_error": True})
            run["tools"].clear()
            if interrupted:
                self._end(run, "interrupted", "Subagent observation was interrupted. In-flight effects may need checking.")
            else:
                self._end(run, "failed", "Subagent returned no result: the lead's turn ended before it came back.")

    def _run(self, parent: str, agent=None) -> dict[str, Any]:
        run = self.runs.get(parent)
        if run is None:
            activity = {"run_id": f"claude-{parent}", "agent": agent if isinstance(agent, str) and agent else "subagent",
                        "phase": "subagent", "round": 0}
            run = self.runs[parent] = {"activity": activity, "ended": False, "background": False, "open": None,
                                       "tools": {}, "since": time.monotonic()}
            run["deltas"] = self._coalescer(lambda kind, text: self._row(
                run, kind, text=text, request_index=run["activity"]["round"]))
            self._row(run, "status", status="running", text="Subagent started.")
        return run

    def _request(self, run, sent_at: float | None = None) -> dict[str, Any]:
        """The run's next request and its row: sent at `sent_at` (with the tool results before it) or, the first, at the
        launch."""
        if sent_at is not None:
            run["since"] = sent_at
        run["activity"]["round"] += 1
        response = run["open"] = {"id": None, "text": [], "thinking": [], "counts": {}, "streamed": False,
                                  "said": False, "shown": False, "last": None, "calls": 0}
        self._row(run, "request", status="awaiting_response", request_index=run["activity"]["round"])
        return response

    def _response(self, run, message_id, model, usage) -> dict[str, Any]:
        """The response a sub-agent frame belongs to, stamped with the frame's time: the open one, or -- another message
        id, or none open -- a new request (the one before it closed first). The CLI sends one message per content block,
        all with the message's id."""
        response = run["open"]
        new = response is None or (message_id is not None and response["id"] not in (None, message_id))
        if new:
            self._close_response(run)                        # the one before keeps its own model on its rows
        if isinstance(model, str) and model:
            run["activity"]["model"] = model
        if new:
            response = self._request(run)
        if response["id"] is None:
            response["id"] = message_id
        response["last"] = time.monotonic()
        self._count(response, usage)
        return response

    @staticmethod
    def _count(response, usage) -> None:
        """The response's token counts, from each partial view the SDK gives (they only grow within a response)."""
        if isinstance(usage, dict):
            for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"):
                if type(usage.get(key)) is int and usage[key] >= 0:
                    response["counts"][key] = max(response["counts"].get(key, 0), usage[key])

    def _show_response(self, run) -> None:
        """The open reply's rows, once it has a block: its pieces, the response row (its text, its counts so far, the
        time from its request to its last block), then its reasoning when it was not streamed."""
        response = run["open"]
        if response is None or response["shown"] or not response["said"]:
            return
        response["shown"] = True
        run["deltas"].flush()
        counts = response["counts"]
        usage = {**({"prompt_tokens": counts["input_tokens"] + counts.get("cache_creation_input_tokens", 0)
                     + counts.get("cache_read_input_tokens", 0)} if "input_tokens" in counts else {}),
                 **({"completion_tokens": counts["output_tokens"]} if "output_tokens" in counts else {})}
        text, thinking = "".join(response["text"]), "".join(response["thinking"])
        self._row(run, "response", status="received", request_index=run["activity"]["round"],
                  duration_ms=round(((response["last"] or run["since"]) - run["since"]) * 1000),
                  **({"text": text} if text.strip() else {}), **({"usage": usage} if usage else {}))
        if not response["streamed"] and thinking.strip():
            self._row(run, "thinking_report", text=thinking)

    def _close_response(self, run) -> None:
        """The open response ends: shown if it had a block, its streamed pieces sent either way. A reply Dream cannot
        show (a malformed block) loses its rows only (gate round 2): its card still ends, and so does every other card
        at the turn's end -- this is the one step of a card's close that can fail."""
        if run["open"] is None:
            return
        try:
            self._show_response(run)
        except Exception:
            _LOG.exception("Nested card reply could not be shown; dropped")
        run["deltas"].flush()
        run["open"] = None

    def _end(self, run, status: str, text: str) -> None:
        self._row(run, "status", status=status, text=text)
        run["ended"] = True

    def _row(self, run, kind: str, **fields) -> None:
        if self.emit is None:
            return
        try:
            payload = bounded_agent_activity({**run["activity"], "kind": kind, **fields})
            if payload is not None:
                self.emit(Event("agent_activity", payload))
        except Exception:
            pass


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
            "CLAUDE_AGENT_SDK_CLIENT_APP": f"dream/{__version__}",
            "ENABLE_TOOL_SEARCH": "auto:100",
            # The lead's decision after the DREAM-212 gate: sub-agents run in the foreground, inside the lead's turn,
            # as a local lead's workers do -- Dream reads a turn up to the SDK's first result, and the bundled CLI
            # would otherwise run them in the background. It also stops background Bash, which this path does not use:
            # native Bash is disallowed here and a sub-agent's "Bash" is Dream's run_bash (subagents.py).
            "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
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


def _mcp_tool_names(tool_name: str) -> set[str]:
    """Every tool name `mcp__<server>__<tool>` can stand for. A server name may itself hold "__", so each "__"
    after the prefix -- overlapping ones too, for a server name ending in "_" -- may be the one that ends it
    (DREAM-148, the DREAM-140 gate's note): `mcp__my__bridge__consult` is `consult` on `my__bridge` as well as
    `bridge__consult` on `my`."""
    rest = tool_name.removeprefix("mcp__")
    return {rest[i + 2:] for i in range(len(rest) - 1) if rest.startswith("__", i)}


def claude_code_refusal(tool_name: str, mode: str) -> str | None:
    """Why a consultation may not run this tool whatever Claude Code's own rules say, or None.

    Dream's tools are matched by name on ANY MCP server, so a Dream bridge registered in the
    owner's Claude config under another name is caught too -- a server name holding "__" included:
    every reading of the name is checked, and one guarded reading refuses the call."""
    from .. import policy

    if tool_name.startswith("mcp__"):
        shorts = _mcp_tool_names(tool_name)
        if shorts & {"consult", "council"}:
            return "a consultation may not convene the Council again"
        if shorts & CONSULT_OWNER_FACING:
            return "a consultation cannot show things to or ask the owner"
        if any(policy.capability(config.tool_id(short)) == policy.MEMORY
               and not (short == "remember" and mode in ("accept-edits", "auto")) for short in shorts):
            return "a consultation may not change the main session's plan, todos, skills, tasks or memories"
    elif tool_name in _EDIT_TOOLS and CLAUDE_CODE_PERMISSION_MODES.get(mode, "plan") == "plan":
        return "plan mode is read-only"
    return None


def claude_code_options(*, system_prompt: str, cwd: str, mode: str | None, model, effort,
                        extra_servers: dict | None = None, extra_allowed=(), owner_settings: bool = True) -> ClaudeAgentOptions:
    """Claude Code's own posture for a consultation: the owner's user/project/local settings
    (CLAUDE.md, hooks, MCP servers, plugins, permission rules), Claude Code's preset prompt with
    ``system_prompt`` appended, its native tools, and a permission mode mapped from Dream's (no
    mode runs as ask). Dream's tool server is not attached. A consult has no UI, so whatever
    Claude Code would prompt the owner for is refused; a PreToolUse hook, which runs even under
    bypassPermissions, keeps claude_code_refusal's guards in every mode. ``owner_settings=False`` (Sleepwalk,
    DREAM-157) loads no user, project or local settings: no CLAUDE.md, hooks, MCP servers or plugins."""
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

    from ..cli_review import CLAUDE_SIGN_IN, runner_env
    return ClaudeAgentOptions(
        system_prompt={"type": "preset", "preset": "claude_code", "append": system_prompt},
        tools={"type": "preset", "preset": "claude_code"},
        setting_sources=["user", "project", "local"] if owner_settings else [],
        mcp_servers=dict(extra_servers or {}),
        allowed_tools=preapproved,
        disallowed_tools=list(CONSULT_DISALLOWED),
        permission_mode=CLAUDE_CODE_PERMISSION_MODES.get(mode, "plan"),
        can_use_tool=decide,
        hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[guard])]},
        cwd=cwd,
        model=model,
        effort=effort,
        # The SDK adds this to the parent's environment; an isolated run blanks everything outside the allowlist.
        env={**({} if owner_settings else {k: "" for k in os.environ if k not in runner_env(CLAUDE_SIGN_IN) and k != "CLAUDE_CODE_ENTRYPOINT"}),
             "CLAUDE_AGENT_SDK_CLIENT_APP": f"dream/{__version__}"},
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
        activity_emit=None,
        local_helpers=None,
    ) -> None:
        self.system_prompt = system_prompt
        # DREAM-212: where the sub-agents' Nested rows go (the Engine's background emitter; None: no view, none sent).
        self.activity_emit = activity_emit
        self._cards: _SubagentCards | None = None
        # DREAM-213: the session's local helpers (delegate_local), whose workers the owner's controls reach.
        self.local_helpers = local_helpers
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
        # DREAM-212: this turn's sub-agents as Nested cards. Display only (gate round 1, finding 3): a fault in them is
        # logged and that update dropped, never raised into the lead's turn; a cancellation, not an Exception, still
        # goes through.
        try:
            cards = self._cards = _SubagentCards(self.activity_emit)
        except Exception:
            _LOG.exception("Nested cards unavailable for this turn")
            cards = self._cards = None

        def mapped(update, *args):
            if cards is None:
                return
            try:
                getattr(cards, update)(*args)
            except Exception:
                _LOG.exception("Nested card update %s failed; dropped", update)
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
                    mapped("message", msg)   # a sub-agent's output: its Nested card, never the lead's events
                    continue
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
                            mapped("tool_use", block)    # a launch's card opens before this event can close the turn
                            yield Event("tool_use", {"name": block.name, "input": block.input, "id": block.id})
                        elif isinstance(block, ThinkingBlock):
                            pass
                    full = "".join(text_parts).strip()
                    if full:
                        yield Event("assistant_done", full)
                elif isinstance(msg, UserMessage):
                    if isinstance(msg.content, list):
                        answers = sum(isinstance(block, ToolResultBlock) for block in msg.content)
                        for block in msg.content:
                            if isinstance(block, ToolResultBlock):
                                # A sub-agent's card ends before the lead reads its result -- or, for the receipt of a
                                # background launch (the message's tool_use_result), stays open (gate round 1).
                                mapped("tool_result", block, msg.tool_use_result if answers == 1 else None)
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
                elif isinstance(msg, SystemMessage):
                    mapped("system", msg)   # the SDK's task messages end background sub-agents' cards (gate round 1)
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
                # DREAM-212: every sub-agent card ends with the turn -- interrupted when the turn was cancelled or
                # closed, failed when it ended before that sub-agent's result came back.
                mapped("close", failure is not None and not isinstance(failure, Exception))

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

    def worker_control(self, action: str, run_id: Any = None, text: Any = None) -> dict[str, Any]:
        """DREAM-212: the SDK runs a Claude-led session's sub-agents itself, so Dream shows them (read-only cards) but
        cannot stop, pause, resume or message them. Checked as the OpenAI-compatible backends check first (an unknown
        action, Pause all with no worker, a malformed run id, a run id that is not a live worker here); then refused
        with that reason, as a ValueError, which /api/control answers as a 400 with the text. DREAM-213: a worker of
        the local helpers (delegate_local) is Dream's own, and its controls go to its helper; Pause all pauses them."""
        if action not in ("agent_stop", "agent_pause", "agent_resume", "agents_pause_all", "agent_message"):
            raise ValueError(f"Unknown worker action: {action!r}.")
        helpers = self.local_helpers
        live = self._cards.live() if self._cards is not None else []
        if action == "agents_pause_all":
            answered = helpers.control(action) if helpers is not None else None
            if answered is not None:
                return answered
            if not live:
                raise ValueError("No worker is running in this session; there is nothing to pause.")
            raise ValueError(WORKERS_REFUSAL)
        if not isinstance(run_id, str) or not 1 <= len(run_id) <= 100:
            raise ValueError("Choose a worker: run_id must be the run id its activity rows carry.")
        answered = helpers.control(action, run_id, text) if helpers is not None else None
        if answered is not None:
            return answered
        if run_id not in live:
            raise ValueError(f"No running worker has run id {run_id!r} in this session: it has finished, or it never "
                             "ran here.")
        raise ValueError(WORKER_REFUSAL)

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
