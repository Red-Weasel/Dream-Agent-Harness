"""Local execution metadata and run-wide limits; no raw prompt/tool arguments.

Verification is recorded as an outcome by its verifier. These traces measure
execution; recording a successful tool call is not evidence the task is complete.
"""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Wall-clock time on every event (DREAM-127, fix list #102): the engine log's lines carry the same clock, so a
# request here and its [gen] line there can be joined. Bound at import: tests replace this module's `time`.
_wall_clock = time.time


class RunLimit(RuntimeError):
    pass


@dataclass
class RunMeter:
    session_id: str
    turn: int
    profile: Any
    path: Path | None = None
    started: float = field(default_factory=time.monotonic)
    tools: int = 0
    failures: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    phases: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    _lock: Any = field(default_factory=threading.RLock, repr=False)
    _approval_depth: int = field(default=0, init=False, repr=False)
    _approval_started: float | None = field(default=None, init=False, repr=False)
    _approval_seconds: float = field(default=0, init=False, repr=False)
    _finished: float | None = field(default=None, init=False, repr=False)
    _finished_approval: float = field(default=0, init=False, repr=False)

    def _times(self, *, for_enforcement: bool = False) -> tuple[float, float, float]:
        """Caller holds the lock; concurrent approval waits count only once."""
        frozen = self._finished is not None and not for_enforcement
        now = self._finished if frozen else time.monotonic()
        wall = max(0.0, now - self.started)
        approval = self._finished_approval if frozen else self._approval_seconds
        if not frozen and self._approval_started is not None:
            approval += max(0.0, now - self._approval_started)
        return wall, approval, max(0.0, wall - approval)

    @contextmanager
    def approval_wait(self):
        """Exclude the union of actual permission callback intervals."""
        with self._lock:
            if self._approval_depth == 0:
                self._approval_started = time.monotonic()
            self._approval_depth += 1
        try:
            yield
        finally:
            with self._lock:
                self._approval_depth -= 1
                if self._approval_depth == 0:
                    now = time.monotonic()
                    self._approval_seconds += max(0.0, now - self._approval_started)
                    self._approval_started = None

    def finish(self) -> None:
        """Freeze completed run status, including outstanding approval intervals."""
        with self._lock:
            if self._finished is None:
                self._finished_approval = self._times()[1]
                self._finished = time.monotonic()

    def check_time(self) -> None:
        with self._lock:
            # Optional jobs retain this meter after the foreground finishes.
            # Frozen display status must never freeze their enforcement clock.
            wall, approval, active = self._times(for_enforcement=True)
            wall_limit = getattr(self.profile, "max_wall_seconds", None)
            recovery = "Completed work is retained; continue explicitly to start a new turn."
            if wall_limit is not None and wall >= wall_limit:
                raise RunLimit(f"Run wall time budget reached ({wall:.1f}s elapsed / {wall_limit:g}s limit, "
                               f"including {approval:.1f}s awaiting approval). {recovery}")
            if self.profile.max_run_seconds is not None and active >= self.profile.max_run_seconds:
                raise RunLimit(f"Run active work time budget reached ({active:.1f}s active / "
                               f"{self.profile.max_run_seconds:g}s limit; {wall:.1f}s wall elapsed, "
                               f"{approval:.1f}s awaiting approval excluded). {recovery}")

    def check(self) -> None:
        if self.tools >= self.profile.max_run_tools:
            raise RunLimit(f"Run tool budget reached ({self.tools}); continue explicitly to start a new turn.")
        if self.profile.max_run_tokens and self.prompt_tokens + self.output_tokens >= self.profile.max_run_tokens:
            raise RunLimit("Run token budget reached; completed work is retained.")
        self.check_time()

    def before_tool(self, name: str) -> None:
        with self._lock:
            self.check()
            self.tools += 1
            self.record("tool_started", tool=name)

    def usage(self, usage: dict, phase: str = "lead") -> None:
        """A backend may add ``head_hash`` to the usage it reports (Dream fix #38): 12 hex of that
        request's head (system text + tools JSON). A prefix-cached engine reuses nothing past a byte
        that differs there, so a head_hash that changes beside ``cached_tokens`` 0 names the cause."""
        incoming = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
        outgoing = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
        cache = int(usage.get("cache_read_input_tokens") or (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        if "input_tokens" in usage and "prompt_tokens" not in usage:
            incoming += cache + int(usage.get("cache_creation_input_tokens") or 0)
        with self._lock:
            self.prompt_tokens += incoming
            self.output_tokens += outgoing
            self.cached_tokens += cache
            p = self.phases.setdefault(phase, {"requests": 0, "prompt_tokens": 0, "output_tokens": 0})
            p["requests"] += 1
            p["prompt_tokens"] += incoming
            p["output_tokens"] += outgoing
            head = {"head_hash": usage["head_hash"]} if isinstance(usage.get("head_hash"), str) else {}
            if usage.get("thinking_capped") is True:     # DREAM-125: sent with thinking off by the build-turn cap
                head["thinking_capped"] = True
            if isinstance(usage.get("response_id"), str):   # the engine's id for the reply (DREAM-127)
                head["response_id"] = usage["response_id"]
            self.record("usage", phase=phase, input_tokens=incoming, output_tokens=outgoing, cached_tokens=cache,
                        **head)

    def record(self, kind: str, **metadata) -> None:
        if self.path is None:
            return
        with self._lock:
            wall, approval, active = self._times()
        event = {"event": kind, "session": self.session_id, "turn": self.turn, "ts": round(_wall_clock(), 3),
                 "elapsed_s": round(wall, 3), "active_elapsed_s": round(active, 3),
                 "approval_wait_s": round(approval, 3), **metadata}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = (json.dumps(event, ensure_ascii=False, default=str) + "\n").encode()
            with self._lock:
                fd = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                try:
                    os.write(fd, data)
                finally:
                    os.close(fd)
        except OSError as exc:
            warning = f"Runtime trace unavailable: {type(exc).__name__}"
            if warning not in self.warnings:
                self.warnings.append(warning)

    def summary(self) -> dict:
        with self._lock:
            wall, approval, active = self._times()
            wall_limit = getattr(self.profile, "max_wall_seconds", None)
            return {"tools": self.tools, "failures": self.failures, "prompt_tokens": self.prompt_tokens,
                    "output_tokens": self.output_tokens, "cached_tokens": self.cached_tokens,
                    "elapsed_s": round(wall, 3), "active_elapsed_s": round(active, 3),
                    "approval_wait_s": round(approval, 3),
                    "remaining_active_s": (round(max(0.0, self.profile.max_run_seconds - active), 3)
                                           if self.profile.max_run_seconds is not None else None),
                    "max_active_s": self.profile.max_run_seconds,
                    "remaining_wall_s": round(max(0.0, wall_limit - wall), 3) if wall_limit is not None else None,
                    "max_wall_s": wall_limit,
                    "approval_pending": self._approval_depth > 0 and self._finished is None,
                    "finished": self._finished is not None,
                    "phases": self.phases, "warnings": list(self.warnings), "raw_content_recorded": False}
