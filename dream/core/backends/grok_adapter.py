"""Grok (xAI) CLI adapter — ``grok -p … --output-format streaming-json``.

Grok's CLI advertises Claude-Code *flag* compatibility (``--allowedTools`` etc.),
but its headless **output** is its own reduced format, not Claude Code's
``stream-json`` transcript. Verified from the CLI's own bundled docs
(``~/.grok/docs/user-guide/14-headless-mode.md``) and from strings in the grok
binary's ``src/headless.rs`` emitter, the ``streaming-json`` stream is:

    {"type":"text","data":"Here's"}          # a chunk of assistant response text
    {"type":"text","data":" a summary"}
    {"type":"thought","data":"Analyzing…"}    # a chunk of reasoning (thinking)
    {"type":"end","stopReason":"EndTurn","sessionId":"abc123","requestId":"xyz789"}
    {"type":"error","message":"…"}            # emitted instead on failure

The list is non-exhaustive: grok may also emit ``max_turns_reached`` and
``auto_compact_*`` housekeeping events, so we switch on ``type`` and ignore the
unknown. Crucially, **tool calls are NOT surfaced in the headless stream** — the
per-tool ``tool_call`` events live only on grok's internal ACP pager bus
(``acp_handler.rs``), which the headless emitter does not forward. So unlike the
codex adapter, this one renders assistant text + reasoning + a terminal result,
but no ``tool_use``/``tool_result`` events (see the module's open questions).

Because the stream is delta-based (many ``text`` chunks rather than codex's one
whole ``agent_message``), ``translate`` accumulates the chunks in ``state`` and
flushes a single ``assistant_done`` at ``end`` — mirroring the Anthropic backend,
which also emits ``text_delta`` chunks then a final ``assistant_done``. That
terminal ``assistant_done`` is what the engine's memory logging and dream
consolidation key on, so it must be emitted.

The ``sessionId`` for ``-r`` resume arrives in the ``end`` event (there is no
init/system event in this stream), captured by :meth:`capture_session`.
"""

from __future__ import annotations

from .base import Event
from .cli_agent import CliAdapter


class GrokAdapter(CliAdapter):
    key = "grok"
    cmd = "grok"
    label = "xAI · Grok"

    # Dream's two-bucket sandbox posture (from ``sandbox_for_mode``) → grok's
    # ``--permission-mode``. Dream's plan/ask collapse to "read-only" → grok's
    # ``plan`` (its strictest, read/plan-only posture); accept-edits/auto/default
    # collapse to "workspace-write" → ``bypassPermissions``, the one mode grok's
    # docs say actually takes effect via this flag and the only way a headless run
    # (which cannot prompt a human) can act on writes without hanging/denying.
    # NOTE: per grok's permissions doc, ``--permission-mode`` is otherwise largely
    # unwired — real deny-by-default read-only wants ``.claude/settings.json``
    # ``defaultMode: dontAsk`` or grok's kernel-enforced ``--sandbox read-only``
    # profile (see open questions). Both mapped values are valid per ``grok --help``
    # (default, acceptEdits, auto, dontAsk, bypassPermissions, plan).
    _MODE_FOR_SANDBOX = {"read-only": "plan", "workspace-write": "bypassPermissions"}

    # stopReason values that mean a clean completion. Anything else (MaxTurns,
    # Refusal, …) is a non-error but *incomplete* turn: result.subtype carries the
    # raw reason so consolidation treats it as unfinished (is_error stays False;
    # genuine failures arrive as a separate {"type":"error"} event).
    _SUCCESS_STOP = {"endturn", "stop", "end_turn", "stopsequence", "complete", "completed"}

    def argv(
        self, prompt: str, *, cwd: str, resume_id: str | None, sandbox: str,
        mcp_config: dict | None = None,
    ) -> list[str]:
        argv = [self.cmd, "-p", prompt, "--output-format", "streaming-json", "--cwd", cwd]
        if resume_id:
            argv += ["-r", resume_id]
        mode = self._MODE_FOR_SANDBOX.get(sandbox, "bypassPermissions")
        argv += ["--permission-mode", mode]
        # mcp_config is intentionally unused here: grok registers MCP servers
        # *persistently* via `grok mcp add` (see mcp_register_argv), not per
        # invocation the way codex does with `-c mcp_servers.*` overrides. The
        # orchestrator owns that lifecycle; argv accepts the kwarg for interface
        # parity with the CliAdapter base / codex.
        return argv

    def capture_session(self, obj: dict) -> str | None:
        # The resume id rides on the terminal `end` event (grok's streaming-json
        # has no init/system event). Read any `sessionId` field defensively so a
        # future init event or the non-streaming `json` object also works.
        sid = obj.get("sessionId")
        return sid if isinstance(sid, str) and sid else None

    def translate(self, obj: dict, state: dict) -> list[Event]:
        t = obj.get("type")
        if t == "text":
            data = obj.get("data") or ""
            if not data:
                return []
            state["text"] = state.get("text", "") + data
            return [Event("text_delta", data)]
        if t == "thought":
            data = obj.get("data") or ""
            return [Event("thinking_delta", data)] if data else []
        if t == "end":
            return self._end(obj, state)
        if t == "error":
            return [Event("error", self._err_msg(obj))]
        if t == "max_turns_reached":
            return [Event("system", "grok: maximum turns reached")]
        if isinstance(t, str) and t.startswith("auto_compact"):
            # Routine context compaction — surface only a failure, drop the rest
            # (mirrors codex dropping its skills-budget housekeeping notice).
            if t in ("auto_compact_failed", "auto_compact_cancelled"):
                return [Event("system", f"grok: {t.replace('_', ' ')}")]
            return []
        # Unknown / non-rendered types (the schema is explicitly non-exhaustive).
        return []

    def _end(self, obj: dict, state: dict) -> list[Event]:
        events: list[Event] = []
        text = state.pop("text", "")
        # Flush the accumulated assistant text as the terminal done event the
        # engine keys on. Skip it only when the turn produced no text at all.
        if text.strip():
            events.append(Event("assistant_done", text))
        stop = obj.get("stopReason")
        is_success = stop is None or str(stop).lower() in self._SUCCESS_STOP
        events.append(Event("result", {
            "is_error": False,
            "subtype": "success" if is_success else str(stop),
            # streaming-json's end event carries no usage/cost; keep the shape
            # {} so the engine's stats reader (usage or {}) stays happy and any
            # future usage field flows through untouched.
            "usage": obj.get("usage") or {},
            "total_cost_usd": obj.get("total_cost_usd"),
        }))
        return events

    @staticmethod
    def _err_msg(obj: dict) -> str:
        msg = obj.get("message") or obj.get("data")
        return str(msg) if msg else "grok error"

    # --- MCP registration (persistent, NOT session-scoped) --------------------
    # grok has no per-invocation MCP override flag; `grok mcp add` writes the
    # server into config.toml (default `--scope user` → ~/.grok/config.toml) and
    # it persists across sessions until removed. The orchestrator should register
    # before a session and deregister after — see integration notes.

    def mcp_register_argv(self, name: str, env: dict[str, str], server_cmd: list[str]) -> list[str] | None:
        argv = [self.cmd, "mcp", "add", name]
        for k, v in env.items():
            argv += ["--env", f"{k}={v}"]
        # `--` so the server's own flags (e.g. `-m`) go to the server, not grok.
        # First token after `--` is the launch command; the rest are its args.
        argv += ["--", *server_cmd]
        return argv

    def mcp_deregister_argv(self, name: str) -> list[str] | None:
        return [self.cmd, "mcp", "remove", name]
