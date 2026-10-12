"""Stream identity evidence cannot forget an earlier conflicting model ID."""

import json

import pytest

from dream.local_harness_eval import _observe_sse


def observe(row, value):
    _observe_sse(row, b"data: " + json.dumps(value).encode())


@pytest.mark.parametrize("models", [
    ["different-model", "requested-model"],
    ["requested-model", "different-model", "requested-model"],
])
def test_conflict_retains_first_model_and_remains_sticky(models):
    row = {"usage": None, "reported_model": None, "stream_complete": False}
    for model in models:
        observe(row, {"model": model})
    assert row["reported_model"] == models[0], "Later chunks must not erase the first model identity"
    assert row.get("reported_model_conflict") is True, "Conflicting identities must remain observable"
    _observe_sse(row, b"data: [DONE]")
    assert row["reported_model_conflict"] is True


@pytest.mark.parametrize("event", [
    {"model": None}, {"model": 17}, {"model": ["requested-model"]},
    {"model": "x" * 257}, {"choices": []},
])
def test_invalid_or_nonmodel_event_cannot_clear_existing_conflict(event):
    row = {"usage": None, "reported_model": None, "stream_complete": False}
    observe(row, {"model": "different-model"})
    observe(row, {"model": "requested-model"})
    observe(row, event)
    assert row["reported_model"] == "different-model"
    assert row.get("reported_model_conflict") is True


def test_repeated_consistent_identity_does_not_invent_conflict():
    row = {"usage": None, "reported_model": None, "stream_complete": False}
    observe(row, {"model": "requested-model"})
    observe(row, {"model": "requested-model"})
    assert row["reported_model"] == "requested-model"
    assert not row.get("reported_model_conflict", False)
