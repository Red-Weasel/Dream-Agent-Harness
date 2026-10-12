"""Host restart preserves outcome accounting; a display report cannot restore execution."""
import copy
import json

import pytest

from dream.core.worker_evidence import Evidence, KEEP

RUN = "a" * 32


def restored(evidence):
    data = json.loads(json.dumps(evidence.snapshot()))
    return Evidence.from_snapshot(data, run_id=RUN, agent="coder")


def test_roundtrip_preserves_coalesced_counts_and_evicted_outcomes():
    evidence = Evidence(RUN, "coder")
    evidence.requests = 7
    for index in range(KEEP + 12):
        seq = evidence.tool_started(f"call_{index}", "read_file")
        evidence.tool_ended(seq, "completed", f"result {index}")
    for index in range(4):
        seq = evidence.tool_started(f"last_{index}", "read_file")
        evidence.tool_ended(seq, "failed", "missing file")
    evidence.report_written = True
    evidence.reported = True
    result = restored(evidence)
    assert result.counts == evidence.counts
    assert list(result.observations) == list(evidence.observations)
    assert result.dropped == evidence.dropped > 0
    assert result.requests == 7 and result.report_written and result.reported
    assert result.handle is None and not result._written
    result.admit_resume()


def test_running_open_intent_survives_and_refuses_execution():
    evidence = Evidence(RUN, "coder")
    seq = evidence.tool_started("pending", "write_file")
    result = restored(evidence)
    assert result.in_flight == evidence.in_flight and seq in result.in_flight
    with pytest.raises(ValueError, match="unsettled"):
        result.admit_resume()


@pytest.mark.parametrize("terminal", [False, True])
def test_uncertain_effect_never_becomes_safe_to_repeat(terminal):
    evidence = Evidence(RUN, "coder")
    seq = evidence.tool_started("pending", "write_file")
    evidence.tool_ended(seq, "uncertain", "connection lost")
    if terminal:
        evidence.close("timeout", "not_sent")
    result = restored(evidence)
    assert result.counts["uncertain"] == 1
    assert result.ended == terminal
    with pytest.raises(ValueError):
        result.admit_resume()


def test_terminal_report_is_restorable_for_delivery_but_not_generation():
    evidence = Evidence(RUN, "coder")
    evidence.report_written = True
    evidence.close("context_full", "incomplete_reply")
    result = restored(evidence)
    assert result.reason == "context_full" and result.recovery == "incomplete_reply"
    assert result.report_written and result.ended
    with pytest.raises(ValueError, match="terminal"):
        result.admit_resume()


def test_snapshot_detaches_both_directions():
    evidence = Evidence(RUN, "coder")
    seq = evidence.tool_started("first", "read_file")
    evidence.tool_ended(seq, "completed", "checked")
    data = evidence.snapshot()
    result = Evidence.from_snapshot(data, run_id=RUN, agent="coder")
    data["counts"]["started"] = 999
    data["observations"][0]["text"] = "changed"
    assert evidence.counts["started"] == result.counts["started"] == 1
    assert evidence.observations[0]["text"] == result.observations[0]["text"] == "checked"


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 1.0), ("requests", True), ("requests", -1),
    ("requests", float("nan")), ("dropped", -1), ("ended", 1),
    ("report_written", "yes"), ("reported", 0), ("reason", "invented"),
    ("recovery", "invented"), ("run_id", "b" * 32), ("agent", "researcher"),
    ("kind", "dream.worker_evidence"),
])
def test_invalid_identity_counter_or_state_refused(field, value):
    data = Evidence(RUN, "coder").snapshot()
    data[field] = value
    with pytest.raises(ValueError):
        Evidence.from_snapshot(data, run_id=RUN, agent="coder")


@pytest.mark.parametrize("change", [
    lambda x: x["counts"].update(started=2),
    lambda x: x["counts"].update(completed=True),
    lambda x: x["counts"].update(extra=0),
    lambda x: x["observations"].append(copy.deepcopy(x["observations"][0])),
    lambda x: x["observations"][0].update(seq=2),
    lambda x: x["observations"][0].update(state="invented"),
    lambda x: x["observations"][0].update(repeats=0),
    lambda x: x["observations"][0].update(text="x" * 601),
    lambda x: x["observations"][0].update(extra="data"),
    lambda x: x.update(extra="data"),
])
def test_invalid_outcome_accounting_refused(change):
    evidence = Evidence(RUN, "coder")
    seq = evidence.tool_started("first", "read_file")
    evidence.tool_ended(seq, "completed", "checked")
    data = evidence.snapshot()
    change(data)
    with pytest.raises(ValueError):
        Evidence.from_snapshot(data, run_id=RUN, agent="coder")


def test_full_in_flight_list_not_the_truncated_display_record():
    evidence = Evidence(RUN, "coder")
    for index in range(KEEP + 5):
        evidence.tool_started(f"pending_{index}", "read_file")
    assert len(json.loads(evidence._record())["in_flight"]) == KEEP
    result = restored(evidence)
    assert len(result.in_flight) == KEEP + 5
    with pytest.raises(ValueError):
        result.admit_resume()


def test_in_flight_bound_refuses_instead_of_truncating():
    evidence = Evidence(RUN, "coder")
    for index in range(257):
        evidence.tool_started(f"pending_{index}", "read_file")
    with pytest.raises(ValueError):
        evidence.snapshot()


def test_out_of_order_outcomes_keep_observation_order():
    evidence = Evidence(RUN, "coder")
    first = evidence.tool_started("first", "read_file")
    second = evidence.tool_started("second", "write_file")
    evidence.tool_ended(second, "completed", "written")
    evidence.tool_ended(first, "failed", "missing")
    result = restored(evidence)
    assert [item["seq"] for item in result.observations] == [second, first]
    result.admit_resume()


def test_live_recovery_reason_is_preserved_but_does_not_admit_more_work():
    evidence = Evidence(RUN, "coder")
    evidence.reason = "context_full"  # The backend sets this before requesting its final report.
    result = restored(evidence)
    assert not result.ended and result.reason == "context_full"
    with pytest.raises(ValueError, match="recovery"):
        result.admit_resume()
