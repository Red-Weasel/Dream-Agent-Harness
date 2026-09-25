"""DREAM-126 (fix list #98, #99): compaction fires only when it must, and every compaction says what it did.

#98: with the output ceiling raised to 60,000, admission reserved the whole ceiling, so a 138,781-token prompt
(69 % of a 200k window) was compacted -- a 2.5-6 minute re-read on MiMo. Admission now reserves at most
_ADMISSION_RESERVE for the reply; a reply that runs longer is cut at what the window holds and continued (DREAM-117).
#99: a plan phase end cut the history to 25 % of the window even at 65k fill and wrote no runtime event."""
from __future__ import annotations

import pytest

from dream.core.backends import openai_compat
from dream.core.context_budget import ContextOverflow
from test_compaction import _backend
from test_compaction_event import Meter


def _fill_to(b, tokens: int) -> None:
    """User/assistant pairs until the history's fill reaches `tokens`."""
    k = 0
    while b._ctx_fill() < tokens:
        k += 1
        b.messages.append({"role": "user", "content": f"step {k} " + "w" * 3400 + f"\n\n[id:m{k:04d}]"})
        b.messages.append({"role": "assistant", "content": "did it " + "v" * 3400})
    b._msg_seq = k
    b.messages.append({"role": "user", "content": f"continue\n\n[id:m{k + 1:04d}]"})


def _big(window=200_000, ceiling=60_000):
    b = _backend(n_ctx=window)
    b._local_options = {"max_tokens": ceiling}
    b.runtime_meter = Meter()
    return b


def _compactions(b, source=None):
    return [f for e, f in b.runtime_meter.records if e == "compaction" and (source is None or f.get("source") == source)]


def test_a_60k_ceiling_no_longer_compacts_a_prompt_at_69_percent():
    b = _big()
    _fill_to(b, 138_781)
    count = len(b.messages)
    report = b._admit_request(b.messages, b._request_tools())
    assert len(b.messages) == count and b.context_report["elided_messages"] == 0
    assert not _compactions(b)
    # the reply may still use what the window holds: more than the reserve, up to the ceiling
    assert report.output > openai_compat._ADMISSION_RESERVE
    assert report.input_tokens + report.output + report.margin <= 200_000


def test_the_reserve_is_the_documented_16k():
    assert openai_compat._ADMISSION_RESERVE == 16_384


@pytest.mark.parametrize("fill", [150_000, 175_000, 186_000, 195_000])
def test_an_admitted_prompt_never_exceeds_the_window(fill):
    b = _big()
    _fill_to(b, fill)
    report = b._admit_request(b.messages, b._request_tools())
    assert report.input_tokens + report.output + report.margin <= 200_000
    assert report.output >= 256


def test_near_the_window_admission_still_compacts_and_says_so():
    b = _big()
    _fill_to(b, 195_000)
    report = b._admit_request(b.messages, b._request_tools())
    assert b.context_report["elided_messages"] > 0
    assert report.output >= openai_compat._ADMISSION_RESERVE   # room for a typical reply after the cut
    (rec,) = _compactions(b, "admission")
    assert rec["before"] > rec["after"] > 0 and rec["elided"] > 0
    assert rec["window"] == 200_000 and rec["phase"] == "lead"


def test_an_irreducible_prompt_is_refused_not_sent():
    b = _big(window=16_384, ceiling=8_000)
    b.messages.append({"role": "user", "content": "x" * 70_000 + "\n\n[id:m0001]"})
    with pytest.raises(ContextOverflow):
        b._admit_request(b.messages, b._request_tools())


def _phase_end(b):
    b._phase_reset_pending = True
    return b._phase_reset()


def test_a_phase_end_at_30_percent_does_not_compact():
    b = _big(window=100_000)
    _fill_to(b, 30_000)
    count = len(b.messages)
    events = _phase_end(b)
    assert len(b.messages) == count and not events
    assert not _compactions(b)
    (rec,) = [f for e, f in b.runtime_meter.records if e == "phase_boundary"]
    assert rec["compacted"] is False and 0.29 < rec["fill"] / rec["window"] < 0.5
    assert rec["threshold"] == openai_compat._PHASE_RESET_AT


def test_a_phase_end_at_70_percent_compacts_and_records_it():
    b = _big(window=100_000)
    _fill_to(b, 70_000)
    count = len(b.messages)
    events = _phase_end(b)
    assert len(b.messages) <= count and any("Phase complete" in str(e.data) for e in events)
    (rec,) = _compactions(b, "phase")
    assert rec["before"] >= 70_000 > rec["after"] > 0 and rec["elided"] > 0
    assert rec["window"] == 100_000
    (boundary,) = [f for e, f in b.runtime_meter.records if e == "phase_boundary"]
    assert boundary["compacted"] is True


def test_the_window_compaction_event_names_its_source():
    b = _big(window=100_000)
    _fill_to(b, 90_000)
    b._maybe_compact()
    (rec,) = _compactions(b, "window")
    assert rec["before"] > rec["after"] > 0 and rec["elided"] > 0


# Gate round 1 (DREAM-126): the real RunMeter.record(kind, **metadata) takes the event name as `kind`, so a field
# of that name raised TypeError on every compaction; the fake Meter above hid it. These drive the real meter.
import json
from types import SimpleNamespace

from dream.telemetry.runtime import RunMeter
from test_compaction import _FakeClient, _text_round


def _real(b, tmp_path):
    b.runtime_meter = RunMeter("s", 1, SimpleNamespace(max_run_tools=100, max_run_tokens=None, max_run_seconds=None),
                               path=tmp_path / "run.jsonl")
    return tmp_path / "run.jsonl"


def _lines(path, event):
    return [e for e in map(json.loads, path.read_text().splitlines()) if e["event"] == event]


@pytest.mark.asyncio
async def test_a_turn_at_90_percent_compacts_through_the_real_run_meter(tmp_path):
    b = _backend(n_ctx=100_000)
    _fill_to(b, 90_000)
    b.messages.pop()                                  # ask() adds the user's message itself
    log = _real(b, tmp_path)
    b._client = _FakeClient([_text_round("ok")])
    [ev async for ev in b.ask("continue")]
    (rec,) = _lines(log, "compaction")
    assert rec["source"] == "window" and rec["before"] > rec["after"] > 0 and rec["elided"] > 0


def test_admission_and_phase_compactions_write_their_jsonl_lines(tmp_path):
    b = _big()
    log = _real(b, tmp_path)
    _fill_to(b, 195_000)
    b._admit_request(b.messages, b._request_tools())
    (adm,) = _lines(log, "compaction")
    assert adm["source"] == "admission" and adm["before"] > adm["after"] > 0 and adm["elided"] > 0
    p = _big(window=100_000)
    plog = _real(p, tmp_path / "p")
    _fill_to(p, 70_000)
    _phase_end(p)
    (ph,) = _lines(plog, "compaction")
    assert ph["source"] == "phase" and ph["before"] > ph["after"] > 0 and ph["elided"] > 0
    (boundary,) = _lines(plog, "phase_boundary")
    assert boundary["compacted"] is True
    q = _big(window=100_000)
    qlog = _real(q, tmp_path / "q")
    _fill_to(q, 30_000)
    _phase_end(q)
    assert _lines(qlog, "phase_boundary")[0]["compacted"] is False and not _lines(qlog, "compaction")
