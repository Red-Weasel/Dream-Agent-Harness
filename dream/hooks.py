"""Explicitly trusted, bounded process middleware; never skill auto-execution.

Hooks get a JSON event on stdin. Observation hooks fail open. A before_tool
gate can veto, but a successful gate only continues to the normal permission
policy. No hook result can grant permissions or replace tool arguments.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import signal
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import extensions
from .core.execution import ExecutionRefused, supervised_command

EVENTS = frozenset({"before_tool", "after_tool", "session_start", "session_end"})
MAX_HOOKS = 16
MAX_TIMEOUT_S = 10.0
MAX_EVENT_BUDGET_S = 30.0
MAX_INPUT_BYTES = 32768
MAX_OUTPUT_BYTES = 32768


def validate_spec(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("hook registration must be an object")
    unknown = set(raw) - {"name", "command", "cwd", "events", "mode", "timeout_s"}
    if unknown:
        raise ValueError("unsupported hook fields: " + ", ".join(sorted(unknown)))
    name = raw.get("name")
    if not isinstance(name, str) or not name or len(name) > 80 or not all(c.isascii() and (c.isalnum() or c in "-_") for c in name):
        raise ValueError("hook name must be 1–80 ASCII letters, digits, - or _")
    command = raw.get("command")
    if not isinstance(command, list) or not 1 <= len(command) <= 64 or not all(isinstance(s, str) and len(s) <= 4096 and "\x00" not in s for s in command):
        raise ValueError("command must be a bounded argv list; shell strings are not supported")
    if not command[0] or not Path(command[0]).is_absolute():
        raise ValueError("hook executable must be an absolute path")
    events = raw.get("events")
    if not isinstance(events, list) or not events or any(not isinstance(e, str) or e not in EVENTS for e in events):
        raise ValueError("events must name before_tool, after_tool, session_start, or session_end")
    mode = raw.get("mode", "observe")
    if mode not in ("observe", "gate") or (mode == "gate" and set(events) != {"before_tool"}):
        raise ValueError("gate mode is only supported for before_tool")
    timeout = raw.get("timeout_s", 2.0)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT_S:
        raise ValueError(f"timeout_s must be positive and at most {MAX_TIMEOUT_S}")
    cwd = raw.get("cwd")
    if not isinstance(cwd, str) or "\x00" in cwd or not Path(cwd).is_absolute():
        raise ValueError("cwd must be an explicit absolute directory")
    return {"name": name.casefold(), "command": list(command), "events": sorted(set(events)),
            "mode": mode, "timeout_s": float(timeout), "cwd": str(Path(cwd).resolve())}


def fingerprint(spec: dict[str, Any]) -> str:
    """Bind trust to argv, cwd, event, mode, limits, and directly named files.

    This is consent to trusted local code, not a sandbox or a transitive package
    signature. A script's imported dependencies remain the user's responsibility.
    """
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode())
    for i, arg in enumerate(spec["command"]):
        path = Path(arg) if Path(arg).is_absolute() else Path(spec["cwd"]) / arg
        if i != 0 and (arg.startswith("-") or not path.is_file()):
            continue
        if not path.is_file():
            raise ValueError(f"hook executable does not exist: {path}")
        # Hash scripts; executable binaries can be large, so stat the binary
        # identity as well. Any normal replacement/update requires new trust.
        stat = path.stat()
        digest.update(f"{path.resolve()}:{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}".encode())
        if stat.st_size <= 2_000_000:
            with path.open("rb") as f:
                digest.update(f.read(2_000_001))
    return digest.hexdigest()


def register_hook(spec: dict[str, Any]) -> dict[str, Any]:
    """User-facing registration only; always disabled until explicitly trusted."""
    spec = validate_spec(spec)
    identifier = extensions.extension_id("hook", spec["name"])
    with extensions._transaction() as data:
        if spec["name"] not in data["hooks"] and len(data["hooks"]) >= MAX_HOOKS:
            raise ValueError(f"at most {MAX_HOOKS} hooks may be registered")
        data["hooks"][spec["name"]] = spec
        data["overrides"][identifier] = False
        data["trusted_hooks"].pop(identifier, None)
    return {"id": identifier, "enabled": False, "spec": spec}


@dataclass
class HookOutcome:
    id: str
    event: str
    status: str
    allowed: bool
    reason: str = ""
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    duration_s: float = 0.0


@dataclass
class HookReport:
    allowed: bool = True
    outcomes: list[HookOutcome] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _OutputLimit(Exception):
    pass


async def _execute(spec: dict[str, Any], event: str, payload: bytes, timeout: float) -> HookOutcome:
    identifier = extensions.extension_id("hook", spec["name"])
    outcome = HookOutcome(identifier, event, "error", spec["mode"] != "gate")
    started = time.monotonic()
    proc = None
    tasks = []
    buffers = [bytearray(), bytearray()]
    consumed = 0

    async def drain(reader, target):
        nonlocal consumed
        while chunk := await reader.read(4096):
            remaining = MAX_OUTPUT_BYTES - consumed
            target.extend(chunk[:max(0, remaining)])
            consumed += len(chunk)
            if consumed > MAX_OUTPUT_BYTES:
                raise _OutputLimit()

    async def write():
        try:
            proc.stdin.write(payload)
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            proc.stdin.close()

    try:
        spawning = asyncio.create_task(asyncio.create_subprocess_exec(
            *supervised_command(spec["command"]), cwd=spec["cwd"],
            env={"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True, limit=8192,
        ))
        try:
            proc = await asyncio.shield(spawning)
        except asyncio.CancelledError:
            proc = await spawning  # retain ownership if cancellation races exec
            raise
        tasks = [asyncio.create_task(write()), asyncio.create_task(drain(proc.stdout, buffers[0])),
                 asyncio.create_task(drain(proc.stderr, buffers[1])), asyncio.create_task(proc.wait())]
        await asyncio.wait_for(asyncio.gather(*tasks), timeout)
        outcome.returncode = proc.returncode
        outcome.status = "completed" if proc.returncode == 0 else "error"
        if proc.returncode:
            outcome.reason = f"hook exited with status {proc.returncode}"
        elif spec["mode"] == "observe":
            outcome.allowed = True
        else:
            try:
                reply = json.loads(buffers[0])
                if (not isinstance(reply, dict) or set(reply) - {"decision", "reason"}
                        or reply.get("decision") not in ("continue", "deny")
                        or not isinstance(reply.get("reason", ""), str)):
                    raise ValueError("expected decision continue or deny, and optional reason")
                outcome.allowed = reply["decision"] == "continue"
                outcome.status = "completed" if outcome.allowed else "denied"
                outcome.reason = reply.get("reason", "")[:4096]
            except (ValueError, UnicodeError):
                outcome.status = "error"
                outcome.reason = "gate returned invalid JSON; expected decision continue or deny"
    except asyncio.TimeoutError:
        outcome.status, outcome.reason = "timeout", f"hook exceeded {timeout:.3g}s"
    except _OutputLimit:
        outcome.status, outcome.reason = "output_limit", "hook exceeded output limit"
    except (OSError, ExecutionRefused) as e:
        outcome.reason = f"hook could not start: {type(e).__name__}: {e}"[:4096]
    finally:
        if proc is not None:
            # TERM lets the command-local supervisor kill and reap descendants,
            # including setsid/double-fork children. SIGKILL here would kill the
            # subreaper before it could perform that cleanup.
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

            async def discard(reader):
                while await reader.read(4096):
                    pass

            # A reader cancelled at the output cap can leave a pipe transport
            # paused with a full buffer. Process.wait also waits for those
            # transports to close: drain bounded chunks after killing, not an
            # unbounded read/communicate or a wait with nobody reading.
            try:
                await asyncio.wait_for(asyncio.gather(discard(proc.stdout), discard(proc.stderr), proc.wait()), 1.0)
            except asyncio.TimeoutError:
                # No public asyncio Process.close exists. Close its owned pipe
                # transports if the supervisor/OS cannot finish cleanup within
                # the allowance. This is an execution failure, not a sandbox.
                proc._transport.close()
                await asyncio.wait_for(proc.wait(), 1.0)
            outcome.returncode = proc.returncode
        outcome.stdout = buffers[0].decode("utf-8", "replace")
        outcome.stderr = buffers[1].decode("utf-8", "replace")
        outcome.duration_s = round(time.monotonic() - started, 6)
    return outcome


async def run_hooks(event: str, payload: dict[str, Any] | None = None, *, budget_s: float = 10.0) -> HookReport:
    """Run registered enabled hooks in name order, with a total event deadline.

    The engine must honor report.allowed before a tool call and still apply its
    ordinary policy. Cancellation is propagated after owned processes are killed.
    Session/after-tool observation failures remain visible but never veto.
    """
    if event not in EVENTS:
        raise ValueError("unsupported hook event")
    if type(budget_s) not in (float, int) or not math.isfinite(budget_s) or not 0 < budget_s <= MAX_EVENT_BUDGET_S:
        raise ValueError("invalid hook event budget")
    report = HookReport()
    try:
        data = extensions.settings()
    except extensions.SettingsError as e:
        report.allowed = event != "before_tool"
        report.outcomes.append(HookOutcome("hooks:settings", event, "settings_error", report.allowed, str(e)))
        return report
    matching = []
    for index, (name, raw) in enumerate(sorted(data["hooks"].items())):
        identifier = extensions.extension_id("hook", name)
        if not data["overrides"].get(identifier, False):
            continue
        try:
            spec = validate_spec(raw)
            if spec["name"] != name:
                raise ValueError("registration name differs from its key")
        except ValueError as e:
            outcome = HookOutcome(identifier, event, "invalid", event != "before_tool", str(e))
            report.outcomes.append(outcome)
            report.allowed = report.allowed and outcome.allowed
            if not report.allowed:
                return report
        else:
            if event in spec["events"]:
                matching.append((index, identifier, spec))
    # A large write_file call is not a hook error when no enabled hook consumes
    # it. Also do not serialize payloads for unrelated session/after events.
    if not matching:
        return report
    try:
        encoded = json.dumps({"event": event, "payload": payload or {}}, allow_nan=False).encode() + b"\n"
        if len(encoded) > MAX_INPUT_BYTES:
            raise ValueError("input exceeds limit")
    except (ValueError, TypeError):
        report.allowed = not any(spec["mode"] == "gate" for _, _, spec in matching)
        report.outcomes.append(HookOutcome("hooks:input", event, "input_error", report.allowed, "hook input is not bounded JSON"))
        return report
    deadline = time.monotonic() + budget_s
    for index, identifier, spec in matching:
        gate = spec["mode"] == "gate"
        if not extensions.is_enabled(identifier, False):
            outcome = HookOutcome(identifier, event, "untrusted", not gate, "hook changed or has not been explicitly trusted")
        elif index >= MAX_HOOKS or deadline <= time.monotonic():
            outcome = HookOutcome(identifier, event, "timeout", not gate, "event hook budget exhausted")
        else:
            extensions.record_usage(identifier, "attempted")
            try:
                outcome = await _execute(spec, event, encoded, min(spec["timeout_s"], deadline - time.monotonic()))
            except asyncio.CancelledError:
                extensions.record_usage(identifier, "cancelled")
                raise
            extensions.record_usage(identifier, "completed" if outcome.status == "completed" else "failed")
        report.outcomes.append(outcome)
        report.allowed = report.allowed and outcome.allowed
        if not report.allowed:
            break
    return report
