"""Measured bookkeeping with a fake clock; no backend or device imports."""
import json

import pytest


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def timer():
    from dream.telemetry.turn import TurnTiming
    clock = Clock()
    return TurnTiming(clock=clock), clock


def test_first_activity_and_first_answer_are_independent_and_ignore_empty_events():
    timing, clock = timer()
    assert timing.started == 100
    clock.advance(1)
    timing.observe("text_delta", meaningful=False)
    timing.observe("usage")
    assert timing.summary()["first_activity_s"] is None
    clock.advance(2)
    timing.observe("thinking_delta")
    clock.advance(4)
    timing.observe("tool_use")
    timing.observe("text_delta")
    clock.advance(1)
    timing.observe("text_delta")
    summary = timing.summary()
    assert summary["first_activity_s"] == 3
    assert summary["first_text_s"] == 7
    assert summary["elapsed_s"] == 8


def test_tool_use_counts_as_activity_without_claiming_answer_text():
    timing, clock = timer()
    clock.advance(2)
    timing.observe("tool_use")
    result = timing.finish("cancelled")
    assert result["first_activity_s"] == 2
    assert result["first_text_s"] is None


def test_nested_phases_do_not_double_count_and_errors_close_measurement():
    timing, clock = timer()
    with timing.phase("tool"):
        clock.advance(1)
        with timing.phase("tool"):
            clock.advance(2)
            with timing.phase("approval"):
                clock.advance(3)
        clock.advance(1)
    with pytest.raises(RuntimeError):
        with timing.phase("preparation"):
            clock.advance(2)
            raise RuntimeError("fixture failure")
    summary = timing.summary()
    assert summary["elapsed_s"] == 9
    assert summary["phases"] == {
        "tool": {"count": 2, "seconds": 7},
        "approval": {"count": 1, "seconds": 3},
        "preparation": {"count": 1, "seconds": 2},
    }
    assert summary["phases_may_overlap"] is True


def test_finish_freezes_open_phase_and_is_idempotent_after_later_events():
    timing, clock = timer()
    with timing.phase("preparation"):
        clock.advance(2)
        summary = timing.finish("cancelled")
        clock.advance(10)
    timing.observe("text_delta")
    timing.request(3, usage={"cache_read_input_tokens": 5})
    with timing.phase("post_processing"):
        clock.advance(1)
    assert timing.finish("completed") == summary
    assert summary["outcome"] == "cancelled"
    assert summary["elapsed_s"] == 2
    assert summary["phases"]["preparation"]["seconds"] == 2


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens_details": {}}, {"cache_read_input_tokens": None}])
def test_missing_cache_report_is_unknown(usage):
    timing, _ = timer()
    timing.request(1, usage=usage)
    assert timing.summary()["cache"] == {"reported_requests": 0, "unreported_requests": 1, "cached_tokens": None}


@pytest.mark.parametrize("usage", [
    {"cache_read_input_tokens": 0},
    {"prompt_tokens_details": {"cached_tokens": 0}},
    {"input_tokens_details": {"cached_tokens": 0}},
])
def test_reported_zero_cache_is_preserved(usage):
    timing, _ = timer()
    timing.request(1, usage=usage)
    assert timing.summary()["cache"] == {"reported_requests": 1, "unreported_requests": 0, "cached_tokens": 0}


def test_requests_preserve_client_and_reported_server_measurements_without_raw_content():
    timing, _ = timer()
    timing.request(3, first_activity=1, first_text=2,
                   usage={"prompt_tokens": 100, "completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": 20}, "prompt": "SECRET"},
                   server_timings={"prompt_ms": 80, "predicted_ms": 500, "content": "SECRET", "bad": float("nan")},
                   schema_fingerprint="a" * 64)
    result = timing.summary()
    request = result["requests"][0]
    assert request["client_elapsed_s"] == 3
    assert request["first_activity_s"] == 1
    assert request["first_text_s"] == 2
    assert request["server_timings"] == {"prompt_ms": 80, "predicted_ms": 500}
    assert request["schema_fingerprint"] == "a" * 64
    assert request["usage"]["cached_tokens"] == 20
    assert "SECRET" not in json.dumps(result, allow_nan=False)
    assert result["raw_content_recorded"] is False
    request["server_timings"]["prompt_ms"] = 999
    assert timing.summary()["requests"][0]["server_timings"]["prompt_ms"] == 80


def test_bounded_request_history_retains_complete_cache_aggregate():
    timing, _ = timer()
    for index in range(70):
        timing.request(index, usage={"cache_read_input_tokens": 0} if index < 30 else None)
    result = timing.summary()
    assert result["request_count"] == 70
    assert result["requests_dropped"] == 6
    assert len(result["requests"]) == 64
    assert result["requests"][0]["client_elapsed_s"] == 6
    assert result["cache"] == {"reported_requests": 30, "unreported_requests": 40, "cached_tokens": 0}


def test_invalid_numeric_metadata_does_not_create_cache_evidence_or_nonfinite_json():
    timing, _ = timer()
    timing.request(1, first_activity=float("nan"), first_text=20,
                   usage={"cache_read_input_tokens": True, "prompt_tokens": -1},
                   server_timings={"prompt_ms": float("inf")}, schema_fingerprint="SECRET")
    result = timing.summary()
    row = result["requests"][0]
    assert row["first_activity_s"] is None and row["first_text_s"] is None
    assert row["server_timings"] == {}
    assert row["schema_fingerprint"] is None
    assert result["cache"]["cached_tokens"] is None
    json.dumps(result, allow_nan=False)
