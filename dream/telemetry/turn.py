"""CPU-only per-turn timings. Client latency is not server prefill time.

Phase intervals may overlap. Their sum is deliberately not used as elapsed time.
Requests retain allowlisted numeric fields, bounded configuration labels and hashes;
request content is omitted.
"""
from __future__ import annotations

import math
import hashlib
import re
import time
from collections import deque
from contextlib import contextmanager
from copy import copy, deepcopy
from typing import Callable
from .failures import failure_signal


_REQUEST_LIMIT = 64
_USAGE_FIELDS = ("prompt_tokens", "input_tokens", "completion_tokens", "output_tokens",
                 "cache_read_input_tokens", "cache_creation_input_tokens")
_SERVER_FIELDS = ("prompt_n", "prompt_ms", "prompt_per_second", "predicted_n",
                  "predicted_ms", "predicted_per_second", "cache_n")
_TOOL_CATEGORIES = {
    "read": frozenset({"read_file", "list_dir", "grep", "glob", "search_files"}),
    "write": frozenset({"write_file", "edit_file", "apply_patch"}),
    "shell": frozenset({"run_bash", "bash", "Bash", "shell", "exec_command", "run_python"}),
    "vision": frozenset({"see", "view_image", "save_screenshot", "screenshot"}),
    "browser": frozenset({"browse", "web_search", "fetch", "ask_frame", "done"}),
}
_OUTCOME_COUNTERS = ("use_events", "result_events", "reported_errors",
                     "reported_successes", "unreported_outcomes")


def _tool_counters():
    return dict.fromkeys(_OUTCOME_COUNTERS, 0)


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _tokens(value):
    return value if type(value) is int and value >= 0 else None


def _label(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("Timing labels must be short names without content")
    return value


def _setting_label(value, *, model=False):
    if not isinstance(value, str) or not 1 <= len(value) <= 4096:
        return None
    # Local model directories and URLs must not enter exported timing metadata.
    if model:
        value = value.replace("\\", '/').rsplit('/', 1)[-1]
    pattern = r"[A-Za-z0-9][A-Za-z0-9._/-]{0,255}" if model else r"[A-Za-z][A-Za-z0-9_-]{0,63}"
    return value if re.fullmatch(pattern, value) else None


def _configuration(values, *, request=False):
    values = values if isinstance(values, dict) else {}
    model = values.get('model')
    result = {'model': _setting_label(model, model=True),
              'model_sha256': hashlib.sha256(model.encode('utf-8', errors='replace')).hexdigest()
                  if isinstance(model, str) and 1 <= len(model) <= 4096 else None}
    for key in (('phase', 'reasoning_effort') if request else
                ('provider', 'profile', 'configured_effort', 'reasoning_effort')):
        result[key] = _setting_label(values.get(key))
    for key in (('max_tokens',) if request else ('turn', 'output_ceiling', 'context_window', 'reported_context')):
        value = values.get(key)
        result[key] = value if type(value) is int and 0 < value <= 2**53 - 1 else None
    if request:
        context = values.get('context')
        result['context'] = None
        if isinstance(context, dict):
            result['context'] = {key: value if type(value := context.get(key)) is int
                                and 0 <= value <= 2**53 - 1 else None
                                for key in ('window', 'instructions', 'history', 'tools', 'output',
                                            'margin', 'input_tokens', 'remaining')}
            result['context']['method'] = 'estimated_request'
    else:
        source = values.get('source')
        result['source'] = source if source in {'prepared_http', 'native_configuration_only', 'unprepared_http'} else 'unknown'
    return result


class TurnTiming:
    """Bookkeeping owned by one event loop, with an injectable monotonic clock."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self.started = clock()
        self._ended = None
        self._outcome = None
        self._first_activity = None
        self._first_text = None
        self._phases = {}
        self._requests = deque(maxlen=_REQUEST_LIMIT)
        self._request_count = 0
        self._cache_reports = 0
        self._cached_tokens = 0
        self._configuration = None
        self._revision = 0
        self._boundary = "in_progress"
        self._tool_outcomes = {**_tool_counters(), "categories": {},
                               "scope": "observed_tool_events", "task_success": None,
                               "raw_content_recorded": False}
        self._delivery_review = {"status": "unreported"}
        self._failure_counts = {}

    def configure(self, values: dict) -> None:
        if self._ended is None:
            self._configuration = _configuration(values)

    def identify(self, turn: int | None) -> None:
        if self._ended is None and self._configuration is not None:
            self._configuration['turn'] = turn if type(turn) is int and 0 < turn <= 2**53 - 1 else None

    def _now(self):
        return self._ended if self._ended is not None else self._clock()

    @contextmanager
    def phase(self, name: str):
        """Record calls and the union of intervals for each phase name.

        Different names can overlap, such as an approval inside tool execution.
        Re-entrant calls with the same name do not count their shared time twice.
        """
        if self._ended is not None:
            yield
            return
        name = _label(name)
        if name not in self._phases and len(self._phases) >= 31:
            name = "other"
        phase = self._phases.setdefault(name, {"count": 0, "seconds": 0.0, "depth": 0, "started": None})
        phase["count"] += 1
        if phase["depth"] == 0:
            phase["started"] = self._now()
        phase["depth"] += 1
        try:
            yield
        finally:
            phase["depth"] -= 1
            if phase["depth"] == 0:
                phase["seconds"] += max(0.0, self._now() - phase["started"])

    def observe(self, kind: str, meaningful: bool = True) -> None:
        """Call with meaningful=False for empty deltas; no event content is stored."""
        if self._ended is not None or not meaningful or kind not in {"text_delta", "thinking_delta", "tool_use"}:
            return
        elapsed = max(0.0, self._now() - self.started)
        if self._first_activity is None:
            self._first_activity = elapsed
        if kind == "text_delta" and self._first_text is None:
            self._first_text = elapsed

    def observe_tool(self, kind: str, data) -> None:
        """Count observed events, not unique calls or inferred task outcomes.

        Missing/non-boolean error flags remain unknown. No names, IDs, arguments
        or results are retained; unfamiliar names share one bounded category.
        """
        if self._ended is not None:
            return
        signal = failure_signal(kind, data)
        if signal is not None:
            self._failure_counts[signal] = min(2**53 - 1, self._failure_counts.get(signal, 0) + 1)
        if kind not in {"tool_use", "tool_result"}:
            return
        data = data if isinstance(data, dict) else {}
        name = data.get("name")
        category = next((key for key, names in _TOOL_CATEGORIES.items()
                         if isinstance(name, str) and name in names), "other")
        counters = self._tool_outcomes["categories"].setdefault(category, _tool_counters())
        keys = ["use_events"] if kind == "tool_use" else ["result_events"]
        if kind == "tool_result":
            flag = data.get("is_error")
            keys.append("reported_errors" if flag is True else
                        "reported_successes" if flag is False else "unreported_outcomes")
        for key in keys:
            self._tool_outcomes[key] += 1
            counters[key] += 1

    def observe_delivery(self, data) -> None:
        """Keep only the protocol's explicit delivery review status."""
        if self._ended is not None or not isinstance(data, dict):
            return
        review = data.get("delivery_review")
        status = review.get("status") if isinstance(review, dict) else None
        if isinstance(status, str) and status in {"pass", "needs_attention", "unverified", "not_requested"}:
            self._delivery_review = {"status": status, "source": "protocol_result"}

    def request(self, elapsed, first_activity=None, first_text=None, usage=None,
                server_timings=None, schema_fingerprint=None, configuration=None) -> None:
        """Record one finished request. First-event delays are request-relative.

        Cache totals cover reports received, not unreported requests. Unknown
        fields are excluded so raw prompt, response and tool data cannot leak.
        """
        if self._ended is not None:
            return
        elapsed = _number(elapsed)

        def delay(value):
            value = _number(value)
            return value if value is not None and elapsed is not None and value <= elapsed else None

        usage = usage if isinstance(usage, dict) else {}
        safe_usage = {key: usage[key] for key in _USAGE_FIELDS if _tokens(usage.get(key)) is not None}
        cached = _tokens(usage.get("cache_read_input_tokens"))
        for details in ("prompt_tokens_details", "input_tokens_details"):
            row = usage.get(details)
            if cached is None and isinstance(row, dict):
                cached = _tokens(row.get("cached_tokens"))
        if cached is None:
            cached = _tokens(usage.get("cached_tokens"))
        if cached is not None:
            safe_usage["cached_tokens"] = cached
            self._cache_reports += 1
            self._cached_tokens += cached
        server_timings = server_timings if isinstance(server_timings, dict) else {}
        safe_timings = {key: server_timings[key] for key in _SERVER_FIELDS if _number(server_timings.get(key)) is not None}
        fingerprint = schema_fingerprint if isinstance(schema_fingerprint, str) and re.fullmatch(r"[0-9a-fA-F]{8,128}", schema_fingerprint) else None
        self._request_count += 1
        self._requests.append({"index": self._request_count, "client_elapsed_s": elapsed,
                               "first_activity_s": delay(first_activity), "first_text_s": delay(first_text),
                               "usage": safe_usage, "server_timings": safe_timings,
                               "schema_fingerprint": fingerprint,
                               "configuration": _configuration(configuration, request=True)})

    def finish(self, outcome: str, *, boundary: str = "measurement") -> dict:
        if self._ended is None:
            self._outcome = _label(outcome)
            self._boundary = _label(boundary)
            self._ended = self._clock()
        return self.summary()

    def corrected(self, outcome: str, *, boundary: str) -> TurnTiming:
        """Return a separate failure snapshot; previously frozen snapshots stay fixed."""
        correction = copy(self)
        correction._outcome = _label(outcome)
        correction._boundary = _label(boundary)
        correction._ended = self._clock()
        correction._revision = self._revision + 1
        return correction

    def summary(self) -> dict:
        now = self._now()
        phases = {name: {"count": row["count"], "seconds": row["seconds"] +
                        (max(0.0, now - row["started"]) if row["depth"] else 0.0)}
                  for name, row in self._phases.items()}
        return {"elapsed_s": max(0.0, now - self.started), "first_activity_s": self._first_activity,
                "first_text_s": self._first_text, "outcome": self._outcome, "phases": phases,
                "outcome_scope": "protocol", "final": self._ended is not None,
                "revision": self._revision, "boundary": self._boundary,
                "configuration": deepcopy(self._configuration),
                "tool_outcomes": deepcopy(self._tool_outcomes),
                "delivery_review": deepcopy(self._delivery_review),
                "failure_diagnostics": {"schema": 1, "counts": dict(self._failure_counts),
                    "scope": "observed_failure_signals", "root_cause_verified": False,
                    "raw_content_recorded": False},
                "phases_may_overlap": True, "requests": deepcopy(list(self._requests)),
                "request_count": self._request_count,
                "requests_dropped": self._request_count - len(self._requests),
                "cache": {"reported_requests": self._cache_reports,
                          "unreported_requests": self._request_count - self._cache_reports,
                          "cached_tokens": self._cached_tokens if self._cache_reports else None},
                "raw_content_recorded": False}
