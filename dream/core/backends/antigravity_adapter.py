"""Antigravity CLI NDJSON transport, distinct from the retired consumer Gemini CLI.

Contract: https://antigravity.google/docs/cli/headless/; flags checked against
the official Linux CLI 1.3.0 payload. Native permission rules remain authoritative;
``plan`` is a planning instruction, not an enforced read-only filesystem.
"""
from __future__ import annotations

import json
import re

from .base import Event
from .cli_agent import CliAdapter


def denied_diagnostic(output: bytes) -> bool:
    """Classify native soft-denial diagnostics without exposing stderr contents.

    Documented headless denials may still have a SUCCESS envelope and exit0.
    Retain this conservative fallback alongside the newer denied_actions field.
    """
    return re.search(rb"(?:permission[^\n]{0,160}(?:denied|refused|blocked)|(?:denied|refused)[^\n]{0,160}(?:permission|tool|action)|soft[- ]den(?:y|ied)|approval required|operation not permitted)",
                     output.lower()) is not None


class AntigravityAdapter(CliAdapter):
    key = "antigravity"
    cmd = "agy"
    label = "Gemini · Antigravity CLI"

    def __init__(self):
        self._usage_conversation = None
        self._usage_totals = {}

    def _usage_session(self, identifier):
        if self._identifier(identifier) and identifier != self._usage_conversation:
            self._usage_conversation = identifier
            self._usage_totals = {}

    def argv(self, prompt: str, *, cwd: str, resume_id: str | None, sandbox: str,
             mcp_config: dict | None = None) -> list[str]:
        argv = [self.cmd, "--input-format", "stream-json", "--output-format", "stream-json",
                "--print-timeout", "15m", "--mode",
                "plan" if sandbox == "read-only" else "accept-edits", "--sandbox"]
        if resume_id:
            if not self._identifier(resume_id):
                raise ValueError("Invalid Antigravity conversation identifier")
            argv += ["--conversation", resume_id]
        return argv

    def stdin_payload(self, prompt: str) -> bytes:
        return (json.dumps({"event": "user", "message": {"content": prompt}},
                           ensure_ascii=False) + "\n").encode("utf-8")

    @staticmethod
    def _identifier(value) -> bool:
        return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}", value) is not None

    def capture_session(self, obj: dict) -> str | None:
        if obj.get("event") != "init":
            return None
        payload = obj.get("init")
        sid = obj.get("conversation_id") or (payload.get("conversation_id") if isinstance(payload, dict) else None)
        return sid if self._identifier(sid) else None

    def mcp_register_argv(self, name: str, env: dict[str, str], server_cmd: list[str]) -> list[str]:
        if not self._identifier(name) or len(name) > 128:
            raise ValueError("Invalid Antigravity MCP server name")
        if (not isinstance(server_cmd, list) or not 1 <= len(server_cmd) <= 64
                or any(not isinstance(x, str) or not x or "\x00" in x or len(x) > 8192 for x in server_cmd)):
            raise ValueError("Invalid Antigravity MCP server command")
        if not isinstance(env, dict):
            raise ValueError("Invalid Antigravity MCP environment")
        # Globally discoverable registration MUST NOT contain a session capability.
        # The agy child receives its own bridge discovery/session in process env;
        # its stdio children inherit that. Other sessions fail closed, never fall
        # back to the legacy independent memory server.
        argv = [self.cmd, "mcp", "add", "--type", "stdio",
                "--env", "DREAM_REQUIRE_PARENT_BRIDGE=1"]
        path = env.get("PYTHONPATH")
        if path is not None:
            if not isinstance(path, str) or not path or "\x00" in path or len(path) > 8192:
                raise ValueError("Invalid Antigravity MCP Python path")
            argv += ["--env", "PYTHONPATH=" + path]
        return [*argv, name, "--", *server_cmd]

    def mcp_deregister_argv(self, name: str) -> list[str]:
        if not self._identifier(name) or len(name) > 128:
            raise ValueError("Invalid Antigravity MCP server name")
        return [self.cmd, "mcp", "remove", name]

    @staticmethod
    def _flush(state: dict) -> list[Event]:
        text = "".join(state.pop("assistant_buf", [])).strip()
        return [Event("assistant_done", text)] if text else []

    def _usage(self, raw) -> tuple[dict, dict, bool]:
        if not isinstance(raw, dict):
            return {}, {}, False
        fields = ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens")
        values = {k: v for k, v in raw.items() if k in fields and type(v) is int and v >= 0}
        # Headless result counters cover the entire conversation, including on
        # resume. Its input count excludes cache reads (see the documented
        # two-turn stream example); expose each turn's full input exactly once.
        reset = any(value < self._usage_totals[key] for key, value in values.items()
                    if key in self._usage_totals)
        previous = {} if reset else self._usage_totals
        delta = {key: value - previous.get(key, 0) for key, value in values.items()}
        return ({**delta,
                 "prompt_tokens": (delta["input_tokens"] + delta.get("cache_read_tokens", 0)
                                   if "input_tokens" in delta else None),
                 "completion_tokens": delta.get("output_tokens"),
                 "prompt_tokens_details": {"cached_tokens": delta.get("cache_read_tokens")}},
                values, reset)

    def commit_result(self, event: Event) -> None:
        """Advance only once the backend publishes its settled result.

        A child interruption or cleanup failure can drop a translated result;
        the next resume must still account for those unpublished tokens.
        """
        data = event.data
        totals = data.get("provider_usage") if isinstance(data, dict) else None
        if isinstance(totals, dict):
            if data.get("usage_counter_reset"):
                self._usage_totals = {}
            self._usage_totals.update({k: v for k, v in totals.items()
                                       if type(v) is int and v >= 0})

    def translate(self, obj: dict, state: dict) -> list[Event]:
        if obj.get("event") == "init":
            self._usage_session(self.capture_session(obj))
            return []
        if obj.get("event") == "result":
            payload = obj.get("result")
            if not isinstance(payload, dict):
                payload = {}
            events = self._flush(state)
            response = payload.get("response")
            if not state.get("had_text") and isinstance(response, str) and response.strip():
                events += [Event("text_delta", response), Event("assistant_done", response.strip())]
                state["had_text"] = True
            failed = (payload.get("status") != "SUCCESS" or bool(payload.get("error"))
                      or bool(payload.get("denied_actions")))
            self._usage_session(payload.get("conversation_id"))
            usage, totals, reset = self._usage(payload.get("usage"))
            data = {"is_error": failed, "subtype": "error" if failed else "success",
                    "usage": usage, "total_cost_usd": None,
                    "usage_scope": "turn", "context_tokens": None,
                    "provider_usage": totals, "provider_usage_scope": "session",
                    "usage_counter_reset": reset}
            if failed:
                data["error"] = ("Antigravity denied required actions; review native permissions in agy."
                                 if payload.get("denied_actions") else
                                 "Antigravity returned an incomplete or failed terminal result; check sign-in and model access.")
            events.append(Event("result", data))
            return events
        if obj.get("event") != "step_update" or not isinstance(obj.get("step_update"), dict):
            return []
        step = obj["step_update"]
        index = step.get("step_index")
        sid = step.get("conversation_id")
        if type(index) is not int or index < 0 or not self._identifier(sid) or step.get("state") not in ("ACTIVE", "DONE"):
            return []
        identity = f"{sid}:{index}"
        steps = state.setdefault("steps", {})
        record = steps.setdefault(identity, {})
        if record.get("done"):
            return []
        kind = step.get("step_type")
        events = []
        if kind == "agent_response":
            delta = step.get("text_delta")
            if isinstance(delta, str) and delta:
                state.setdefault("assistant_buf", []).append(delta)
                state["had_text"] = True
                events.append(Event("text_delta", delta))
            if step["state"] == "DONE":
                events += self._flush(state)
        elif kind == "tool":
            events += self._flush(state)
            info = step.get("tool_info")
            if isinstance(info, dict):
                name = info.get("name") or step.get("tool_name")
                if isinstance(name, str) and name:
                    if not record.get("called"):
                        parameters = info.get("parameters")
                        events.append(Event("tool_use", {"name": name, "input": parameters if isinstance(parameters, dict) else {},
                                                         "id": identity}))
                        record["called"] = True
                    if step["state"] == "DONE":
                        output = info.get("output", "")
                        events.append(Event("tool_result", {"name": name, "id": identity,
                            "content": output if isinstance(output, str) else json.dumps(output, ensure_ascii=False),
                            "is_error": bool(info.get("error"))}))
        if step["state"] == "DONE":
            record["done"] = True
        return events
