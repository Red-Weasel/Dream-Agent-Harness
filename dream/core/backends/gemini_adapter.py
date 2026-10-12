"""Gemini (Google) CLI adapter — ``gemini -p … --output-format stream-json``.

Mirrors :class:`~dream.core.backends.cli_agent.CodexAdapter`: ``argv`` builds the
headless invocation, ``translate`` is a pure function of one decoded stream object,
and ``capture_session`` pulls the resumable id out of the stream. Both are exercised
over recorded fixtures with no subprocess (see ``tests/test_gemini_adapter.py``).

Schema provenance (from official source and fixtures, not live inference).
MCP command syntax also checked against the installed Gemini CLI 0.52.0 bundle:

* Event shapes come straight from the gemini-cli source, not just prose docs:
  ``packages/core/src/output/types.ts`` (the ``JsonStreamEvent`` union) and the emit
  order in ``packages/cli/src/nonInteractiveCli.ts``. The six ``stream-json`` event
  types are ``init | message | tool_use | tool_result | error | result``.
  - ``init``   → ``{type, timestamp, session_id, model}`` (once, at turn start).
  - ``message``→ ``{type, timestamp, role:"user"|"assistant", content, delta?}``.
                 Assistant text arrives as many ``delta:true`` chunks; the user
                 ``message`` is just the echo of the prompt we sent.
  - ``tool_use``   → ``{type, timestamp, tool_name, tool_id, parameters}``.
  - ``tool_result``→ ``{type, timestamp, tool_id, status:"success"|"error",
                       output?, error?:{type,message}}`` — note it carries *only*
                       ``tool_id`` (no name), so we correlate back to the ``tool_use``.
  - ``error``  → ``{type, timestamp, severity:"warning"|"error", message}`` —
                 non-fatal in-stream notes (loop detected, max turns, blocked …);
                 the turn still ends with a ``result``.
  - ``result`` → ``{type, timestamp, status:"success"|"error", error?, stats?}`` with
                 ``stats = {total_tokens, input_tokens, output_tokens, cached, input,
                 duration_ms, tool_calls, models}``.

Streaming → Dream mapping. Dream logs ``assistant_done`` to memory and treats
``text_delta`` as render-only (see ``openai_compat``/``anthropic`` backends + the
engine). Gemini only streams assistant *deltas*, so we forward each delta as a
``text_delta`` for live paint AND accumulate it, flushing one ``assistant_done`` with
the full text at the next boundary (a ``tool_use``/``error``/``result``). That keeps
the memory log intact without double-counting.

Known gaps to verify live (see ``open_questions`` in the returned schema):
  * There is no per-turn "resume-by-session-id" flag documented: ``--resume`` officially
    takes ``latest`` or an index, ``--session-id`` *starts* a session with a chosen
    UUID, and ``--session-file`` loads a JSON file. We pass the captured id through
    ``--resume`` (its ``coerce`` accepts an arbitrary string), which is the natural
    analogue of codex's ``resume <thread_id>`` but is UNVERIFIED — ``--resume latest``
    or full re-priming may be needed instead.
  * MCP is registered by the orchestrator under a unique user-scoped server name,
    then removed in the same scope on cleanup. Each invocation allows only that
    name; it cannot discover other sessions' configured bridges. This does not
    prove authentication or live tool use.
"""

from __future__ import annotations

import re
from typing import Any

from .base import Event
from .cli_agent import CliAdapter


class GeminiAdapter(CliAdapter):
    key = "gemini"
    cmd = "gemini"
    label = "Gemini · CLI"

    @staticmethod
    def _mcp_name(name: str) -> str:
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name) is None:
            raise ValueError("Invalid Gemini MCP server name")
        return name

    def mcp_register_argv(self, name: str, env: dict[str, str], server_cmd: list[str]) -> list[str]:
        self._mcp_name(name)
        if (not isinstance(server_cmd, list) or not 1 <= len(server_cmd) <= 64
                or any(not isinstance(part, str) or not part or '\x00' in part or len(part) > 8192 for part in server_cmd)
                or server_cmd[0].startswith('-')):
            raise ValueError("Invalid Gemini MCP server command")
        if not isinstance(env, dict) or len(env) > 64:
            raise ValueError("Invalid Gemini MCP server environment")
        argv = [self.cmd, "mcp", "add", "--scope", "user", "--transport", "stdio"]
        for key, value in env.items():
            # Gemini 0.52.0's CLI splits KEY=value at '=' and drops empty values.
            # Refuse lossy serialization rather than registering a broken bridge.
            if (not isinstance(key, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", key) is None
                    or not isinstance(value, str) or not value or '\x00' in value or '=' in value or len(value) > 8192):
                raise ValueError("Invalid or unsupported Gemini MCP environment value")
            argv += ["--env", f"{key}={value}"]
        # Gemini requires both positional fields before '--'; server flags must
        # follow it so they cannot override Gemini's registration options.
        return [*argv, name, server_cmd[0], "--", *server_cmd[1:]]

    def mcp_deregister_argv(self, name: str) -> list[str]:
        return [self.cmd, "mcp", "remove", "--scope", "user", self._mcp_name(name)]

    # --- invocation -----------------------------------------------------------

    def argv(
        self, prompt: str, *, cwd: str, resume_id: str | None, sandbox: str,
        mcp_config: dict | None = None,
    ) -> list[str]:
        # -p/--prompt forces headless (non-interactive) mode; stream-json gives the
        # JSONL event stream the backend reads. Gemini reads shared user settings;
        # constrain discovery to this session's bridge instead of every entry.
        argv = [self.cmd, "--output-format", "stream-json"]
        argv += ["--approval-mode", self._approval_mode(sandbox)]
        if mcp_config is not None:
            argv += ["--allowed-mcp-server-names", self._mcp_name(mcp_config.get("name"))]
        if cwd:
            # The backend sets process cwd to this workspace. Retain the explicit
            # inclusion as well; it does not replace Gemini's primary cwd.
            argv += ["--include-directories", cwd]
        if resume_id:
            argv += ["--resume", resume_id]
        argv += ["-p", prompt]
        return argv

    @staticmethod
    def _approval_mode(sandbox: str) -> str:
        """Dream's mode → gemini's ``--approval-mode``. Read-only Dream modes
        (plan/ask, surfaced as the ``read-only`` sandbox) map to gemini's read-only
        ``plan``; edit-capable modes map to ``yolo`` — headless has no human to answer
        an approval prompt, so writes must auto-approve or the turn would hang."""
        return "plan" if sandbox == "read-only" else "yolo"

    def capture_session(self, obj: dict) -> str | None:
        if obj.get("type") == "init":
            sid = obj.get("session_id")
            return sid if isinstance(sid, str) and sid else None
        return None

    # --- translation ----------------------------------------------------------

    def translate(self, obj: dict, state: dict) -> list[Event]:
        t = obj.get("type")
        if t == "message":
            return self._message(obj, state)
        if t == "tool_use":
            # flush any assistant text the model produced before deciding to call a tool
            return self._flush(state) + self._tool_use(obj, state)
        if t == "tool_result":
            return self._flush(state) + self._tool_result(obj, state)
        if t == "error":
            return self._flush(state) + self._error(obj)
        if t == "result":
            return self._flush(state) + self._result(obj)
        # init (captured via capture_session) and anything unrecognised render nothing.
        return []

    def _message(self, obj: dict, state: dict) -> list[Event]:
        # The user message is just the echo of the prompt we sent — Dream already has it.
        if obj.get("role") != "assistant":
            return []
        content = obj.get("content") or ""
        if not content:
            return []
        state.setdefault("assistant_buf", []).append(content)
        return [Event("text_delta", content)]

    @staticmethod
    def _flush(state: dict) -> list[Event]:
        """Emit the accumulated assistant text as one ``assistant_done`` (what the
        engine logs to memory) and clear the buffer. No-op when nothing is buffered."""
        buf = state.get("assistant_buf")
        if not buf:
            return []
        state["assistant_buf"] = []
        full = "".join(buf).strip()
        return [Event("assistant_done", full)] if full else []

    def _tool_use(self, obj: dict, state: dict) -> list[Event]:
        tool_id = obj.get("tool_id")
        name = obj.get("tool_name") or "tool"
        if tool_id:
            # remember the name so the id-only tool_result can be labelled
            state.setdefault("tools", {})[tool_id] = name
        return [Event("tool_use", {
            "name": name,
            "input": obj.get("parameters") or {},
            "id": tool_id,
        })]

    def _tool_result(self, obj: dict, state: dict) -> list[Event]:
        tool_id = obj.get("tool_id")
        name = state.get("tools", {}).get(tool_id, "tool")
        return [Event("tool_result", {
            "name": name,
            "id": tool_id,
            "content": self._result_text(obj),
            "is_error": obj.get("status") == "error",
        })]

    @staticmethod
    def _result_text(obj: dict) -> str:
        """Prefer the tool's string ``output``; fall back to the error message."""
        out = obj.get("output")
        if isinstance(out, str) and out:
            return out
        err = obj.get("error")
        if isinstance(err, dict):
            return err.get("message") or err.get("type") or ""
        return out if isinstance(out, str) else ""

    @staticmethod
    def _error(obj: dict) -> list[Event]:
        # In-stream errors (loop detected, max-turns, blocked, invalid-stream) are
        # non-fatal notes — the turn still yields a result. Forward as a system note,
        # matching how CodexAdapter treats its in-stream error items. The terminal
        # failure, if any, is carried by the result event's is_error.
        msg = obj.get("message") or ""
        return [Event("system", msg)] if msg else []

    def _result(self, obj: dict) -> list[Event]:
        # A clean process exit cannot supply missing completion evidence. Only
        # the protocol's explicit success status completes the turn.
        status = obj.get("status")
        is_error = status != "success"
        data: dict[str, Any] = {
            "is_error": is_error,
            "subtype": "error" if is_error else "success",
            "usage": self._map_usage(obj.get("stats") or {}),
            "total_cost_usd": None,
        }
        err = obj.get("error")
        if isinstance(err, dict):
            data["error"] = err.get("message") or err.get("type")
        if status not in ("success", "error") and not data.get("error"):
            data["error"] = "Gemini returned a missing or unrecognized terminal status."
        return [Event("result", data)]

    @staticmethod
    def _map_usage(stats: dict) -> dict[str, Any]:
        """gemini reports input_tokens/output_tokens; expose the OpenAI-style names
        Dream's stats read while keeping the raw fields (cached/total/duration/models)
        alongside."""
        return {
            **stats,
            "prompt_tokens": stats.get("input_tokens"),
            "completion_tokens": stats.get("output_tokens"),
        }
