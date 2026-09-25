"""DREAM-130 (fix list #107): a reviewer's compaction reaches the owning run's meter.

review_backend gives an OpenAI-compatible reviewer an AttributedMeter as `runtime_meter`, and the backend's
compaction paths call `runtime_meter.record(kind, **metadata)`. AttributedMeter had no `record`, so any compaction
in a reviewer raised AttributeError (an independent gate reproduced it with an admission at 195k)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from dream.core.evaluator import review_backend, ReviewSettings
from dream.core.profiles import resolve_profile
from dream.core.providers import get_provider
from dream.telemetry.runtime import RunMeter
from test_compaction import _FakeClient, _text_round
from test_compaction_admission import _fill_to


def _reviewer(tmp_path, window):
    parent = RunMeter("owner", 1, SimpleNamespace(max_run_tools=100, max_run_tokens=None, max_run_seconds=None),
                      path=tmp_path / "run.jsonl")
    provider = get_provider("machx")
    engine = SimpleNamespace(provider=provider, model="fixture-local", profile=resolve_profile(provider),
                             runtime_meter=parent)
    backend = review_backend(ReviewSettings.resolve(engine), [], "review", tmp_path)
    backend.n_ctx = window
    return backend, tmp_path / "run.jsonl"


def _lines(path, event):
    return [e for e in map(json.loads, path.read_text().splitlines()) if e["event"] == event]


def test_a_reviewer_admission_compaction_is_recorded_on_the_owning_run(tmp_path):
    b, log = _reviewer(tmp_path, 200_000)
    b._local_options = {"max_tokens": 60_000}
    _fill_to(b, 195_000)
    b._admit_request(b.messages, b._request_tools())
    (rec,) = _lines(log, "compaction")
    assert rec["source"] == "admission" and rec["before"] > rec["after"] > 0 and rec["elided"] > 0
    assert rec["session"] == "owner" and rec["scope"] == "evaluator:machx"


@pytest.mark.asyncio
async def test_a_reviewer_turn_at_90_percent_compacts_without_raising(tmp_path):
    b, log = _reviewer(tmp_path, 100_000)
    _fill_to(b, 90_000)
    b.messages.pop()                                  # ask() adds the prompt itself
    b._client = _FakeClient([_text_round("VERDICT: PASS")])
    events = [ev async for ev in b.ask("review")]
    assert not [ev for ev in events if ev.kind == "error"]
    (rec,) = _lines(log, "compaction")
    assert rec["source"] == "window" and rec["scope"] == "evaluator:machx"
