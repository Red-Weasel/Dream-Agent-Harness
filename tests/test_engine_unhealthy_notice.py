"""When a generation fails because the engine lost its device, say so and say what
fixes it (restart), instead of a bare server_error (2026-09-22 02:28)."""
from __future__ import annotations
import json
import pytest
from test_compaction import _backend, _sse

pytestmark = pytest.mark.asyncio


class _Resp:
    def __init__(self, lines):
        self.status_code = 200
        self._lines = lines
    async def aiter_lines(self):
        for ln in self._lines:
            yield ln
    async def aread(self):
        return b""


class _Stream:
    def __init__(self, lines):
        self._lines = lines
    async def __aenter__(self):
        return _Resp(self._lines)
    async def __aexit__(self, *exc):
        return False


class _HealthJSON:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
    def json(self):
        return self._body


class _FaultingClient:
    def __init__(self, health):
        self.health = health
        self.payloads = []
    def stream(self, method, url, json=None):
        self.payloads.append(json)
        return _Stream([_sse({"error": {"message": "level_zero backend failed with error: 20 (UR_RESULT_ERROR_DEVICE_LOST)"}}), "data: [DONE]"])
    async def get(self, url, timeout=None):
        return _HealthJSON(503, self.health) if "/health" in url else _HealthJSON(404, {})


class Meter:
    def __init__(self):
        self.records = []
    def record(self, event, **fields):
        self.records.append((event, fields))
    def check(self):
        return None
    def usage(self, usage, phase="lead"):
        return None
    def before_tool(self, *a, **k):
        return None


async def test_a_lost_device_is_named_with_the_fix():
    b = _backend(n_ctx=8192)
    b.runtime_meter = Meter()
    b._client = _FaultingClient({"status": "unhealthy", "reason": "level_zero backend failed with error: 20 (UR_RESULT_ERROR_DEVICE_LOST)"})
    events = [ev async for ev in b.ask("hi")]
    notes = [e.data for e in events if e.kind == "system"]
    assert any("restart" in n.lower() and "DEVICE_LOST" in n for n in notes), notes
    assert any(e == "engine_unhealthy" for e, _ in b.runtime_meter.records)
