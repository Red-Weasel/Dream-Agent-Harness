"""Vital signs, Dream half (DREAM-135): the local MachX engine is asked for `ie_vitals` and, when it answers,
its per-window and per-request readings become `vitals` events and one runtime-log line per request.
Passive only: nothing here changes the reply, the history, the other providers' requests or any decision.

No engine, no GPU: a scripted stream stands in for `ie serve`."""
import json
import math

import pytest

from dream.core.profiles import PROFILES
from dream.telemetry.runtime import RunMeter
from test_schema_deferral import _FakeClient, _backend, _sse

pytestmark = pytest.mark.asyncio

# The engine's own shapes (vitals_window_json / vitals_summary_json, openai_proto.cpp at 40f6598 on the engine's
# vitals-engine branch): H_mean, H_max and margin_min are null in a window with no sampled row (greedy decoding);
# the summary carries restore_ms, and cache_source is "none", "live", "prompt end" or "slot <id>".
WINDOW = {"n": 16, "n_hi": 2, "draft": {"offered": 14, "accepted": 9}, "H_mean": 0.41, "H_max": 2.9,
          "margin_min": 0.03}
WINDOW2 = {"n": 8, "n_hi": 0, "draft": {"offered": 6, "accepted": 6}, "H_mean": None, "H_max": None,
           "margin_min": None}
SUMMARY = {"tokens": 24, "n_hi": 2, "draft_offered": 20, "draft_accepted": 15, "cached_tokens": 38000,
           "prefill_ms": 1900.5, "restore_ms": 38.0, "H_mean": 0.3, "cache_source": "slot 3", "decode_tps": 16.2}
USAGE = {"prompt_tokens": 41000, "completion_tokens": 24, "prompt_tokens_details": {"cached_tokens": 38000}}


def _round(window=WINDOW, window2=WINDOW2, summary=SUMMARY, text="All good."):
    first = {"choices": [{"delta": {"content": text[:4]}, "finish_reason": None}]}
    second = {"choices": [{"delta": {"content": text[4:]}, "finish_reason": None}]}
    if window is not None:
        first["ie_vitals"] = window
    if window2 is not None:
        second["ie_vitals"] = window2
    last = {"choices": [], "usage": USAGE}
    if summary is not None:
        last["ie_vitals_summary"] = summary
    return [_sse(first), _sse(second), _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            _sse(last), "data: [DONE]"]


def _machx(rounds):
    b = _backend(n_ctx=65536)
    b.provider.key = "machx"
    b._client = _FakeClient(rounds)
    return b


# --- the opt-in: MachX only --------------------------------------------------------------------------


async def test_the_local_engine_is_asked_for_vitals():
    b = _machx([_round()])
    [ev async for ev in b.ask("hello")]
    assert b._client.payloads[0]["ie_vitals"] is True


@pytest.mark.parametrize("key,base_url", [("openrouter", "https://openrouter.ai/api/v1"),
                                          ("openai", "https://api.openai.com/v1"),
                                          ("lmstudio", "http://127.0.0.1:1234/v1")])
async def test_other_providers_are_never_sent_the_flag(key, base_url):
    """An unknown field could break a strict server, so no other provider sees it -- not even another
    server on a loopback address."""
    b = _backend(n_ctx=65536)
    b.provider.key, b.provider.base_url = key, base_url
    b._client = _FakeClient([_round()])
    [ev async for ev in b.ask("hello")]
    assert "ie_vitals" not in b._client.payloads[0]
    assert not any("vital" in k for k in b._client.payloads[0])


# --- parsing -----------------------------------------------------------------------------------------


async def test_windows_and_the_summary_become_vitals_events():
    b = _machx([_round()])
    events = [ev async for ev in b.ask("hello")]
    vitals = [e.data for e in events if e.kind == "vitals"]
    assert vitals == [
        {"window": {"n": 16, "H_mean": 0.41, "H_max": 2.9, "margin_min": 0.03, "n_hi": 2,
                    "draft_offered": 14, "draft_accepted": 9}},
        {"window": {"n": 8, "n_hi": 0, "draft_offered": 6, "draft_accepted": 6}},   # H null: left out, never 0
        {"summary": SUMMARY},
    ]
    # The window comes out as its chunk arrives, before the text that chunk carries.
    kinds = [e.kind for e in events]
    assert kinds.index("vitals") < kinds.index("text_delta")
    # Nothing of it reaches the reply or the history.
    assert "".join(e.data for e in events if e.kind == "text_delta") == "All good."
    assert all("vital" not in json.dumps(m) and "H_mean" not in json.dumps(m) for m in b.messages)


async def test_an_engine_without_the_field_gives_the_same_events_as_before():
    plain = _machx([_round(window=None, window2=None, summary=None)])
    events = [ev async for ev in plain.ask("hello")]
    assert not [e for e in events if e.kind == "vitals"]
    with_vitals = _machx([_round()])
    other = [ev async for ev in with_vitals.ask("hello")]
    # stats and result carry wall-clock timings, which differ run to run.
    strip = lambda evs: [(e.kind, e.data) for e in evs if e.kind not in ("vitals", "result", "stats")]
    assert strip(events) == strip(other)
    assert plain.messages == with_vitals.messages


@pytest.mark.parametrize("window,summary", [
    ("garbage", ["not", "a", "dict"]),
    ({"n": -3, "H_mean": "x", "H_max": float("nan"), "margin_min": float("inf"), "n_hi": True,
      "draft": "x"}, {"tokens": "many", "cache_source": 7, "decode_tps": -1}),
    ({"n": 16.5, "draft": {"offered": "a", "accepted": None}}, {"cache_source": "slot\n3"}),
    ({"draft": {}}, {"cache_source": "x" * 33}),
    ({}, {}),
    (None, None),
])
async def test_malformed_readings_are_dropped_never_raised(window, summary):
    b = _machx([_round(window=window, window2=None, summary=summary)])
    events = [ev async for ev in b.ask("hello")]
    assert not [e for e in events if e.kind == "vitals"]
    assert events[-1].kind == "result" and events[-1].data["subtype"] == "success"
    assert "".join(e.data for e in events if e.kind == "text_delta") == "All good."


async def test_a_partly_malformed_window_keeps_the_good_fields():
    b = _machx([_round(window={"n": 16, "H_mean": "x", "H_max": 3, "n_hi": 1.0, "margin_min": -0.1,
                               "draft": {"offered": 4, "accepted": "x"}}, window2=None,
                       summary={"tokens": 16, "cache_source": "none", "prefill_ms": None})])
    events = [ev async for ev in b.ask("hello")]
    assert [e.data for e in events if e.kind == "vitals"] == [
        {"window": {"n": 16, "H_max": 3.0, "n_hi": 1, "draft_offered": 4}},
        {"summary": {"tokens": 16, "cache_source": "none"}},
    ]
    assert all(not isinstance(v, float) or math.isfinite(v)
               for e in events if e.kind == "vitals" for part in e.data.values() for v in part.values())


# --- the runtime log ---------------------------------------------------------------------------------


def _meter(tmp_path):
    return RunMeter("fixture", 1, PROFILES["lean"], tmp_path / "runtime.jsonl")


async def test_one_runtime_line_per_request_with_the_windows_folded(tmp_path):
    b = _machx([_round()])
    b.runtime_meter = _meter(tmp_path)
    [ev async for ev in b.ask("hello")]
    rows = [json.loads(line) for line in (tmp_path / "runtime.jsonl").read_text().splitlines()]
    vitals = [r for r in rows if r["event"] == "vitals"]
    assert len(vitals) == 1
    row = vitals[0]
    assert row["session"] == "fixture" and row["turn"] == 1         # the meter's own fields are intact
    assert row["windows"] == 2 and row["tokens"] == 24
    assert row["H_mean"] == pytest.approx(0.41)                     # only the window that had an H
    assert row["H_max"] == 2.9 and row["margin_min"] == 0.03 and row["n_hi"] == 2
    assert row["draft_offered"] == 20 and row["draft_accepted"] == 15
    assert row["summary"] == SUMMARY


async def test_no_runtime_line_without_readings(tmp_path):
    b = _machx([_round(window=None, window2=None, summary=None)])
    b.runtime_meter = _meter(tmp_path)
    [ev async for ev in b.ask("hello")]
    rows = [json.loads(line) for line in (tmp_path / "runtime.jsonl").read_text().splitlines()]
    assert not [r for r in rows if r["event"] == "vitals"]
    assert [r for r in rows if r["event"] == "usage"]                # the usage line is still written


# --- hostile values (gate round 1): nothing a server sends may end the turn --------------------------------------

HUGE = 10 ** 400                    # a valid JSON integer too large for a float: math.isfinite raises OverflowError


def _raw_round(first_vitals: str, summary_vitals: str) -> list[str]:
    """Raw SSE text, so the hostile value reaches json.loads exactly as a server would send it."""
    return [
        'data: {"choices": [{"delta": {"content": "All "}, "finish_reason": null}], "ie_vitals": ' + first_vitals + '}',
        _sse({"choices": [{"delta": {"content": "good."}, "finish_reason": None}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        'data: {"choices": [], "usage": ' + json.dumps(USAGE) + ', "ie_vitals_summary": ' + summary_vitals + '}',
        "data: [DONE]",
    ]


@pytest.mark.parametrize("window,summary,kept_window,kept_summary", [
    ('{"n": %d, "n_hi": 1}' % HUGE, '{"tokens": 4}', {"n_hi": 1}, {"tokens": 4}),
    ('{"n": -%d, "n_hi": 1}' % HUGE, '{"tokens": 4}', {"n_hi": 1}, {"tokens": 4}),
    ('{"n": 4, "H_mean": %d}' % HUGE, '{"tokens": 4}', {"n": 4}, {"tokens": 4}),
    ('{"n": 4, "draft": {"offered": %d, "accepted": 1}}' % HUGE, '{"tokens": 4}',
     {"n": 4, "draft_accepted": 1}, {"tokens": 4}),
    ('{"n": 4}', '{"tokens": %d, "decode_tps": %d, "restore_ms": 2}' % (HUGE, HUGE), {"n": 4}, {"restore_ms": 2.0}),
    ('{"n": 4}', '{"tokens": 4, "cache_source": ["slot", 3]}', {"n": 4}, {"tokens": 4}),
    ('{"n": 4}', '{"tokens": 4, "cache_source": {"slot": 3}}', {"n": 4}, {"tokens": 4}),
    ('{"n": 4, "cache_source": ["x"]}', '{"tokens": 4, "cache_source": null, "restore_ms": "x"}',
     {"n": 4}, {"tokens": 4}),
])
async def test_a_hostile_value_drops_only_itself(window, summary, kept_window, kept_summary):
    b = _machx([_raw_round(window, summary)])
    events = [ev async for ev in b.ask("hello")]
    assert events[-1].kind == "result" and events[-1].data["subtype"] == "success"
    assert not [e for e in events if e.kind == "error"]
    assert "".join(e.data for e in events if e.kind == "text_delta") == "All good."
    assert [e.data for e in events if e.kind == "vitals"] == [{"window": kept_window}, {"summary": kept_summary}]


async def test_a_parser_fault_drops_the_reading_not_the_turn(monkeypatch, tmp_path):
    from dream.core.backends import openai_compat

    def broken(raw, fields):
        raise RuntimeError("parser fault")
    monkeypatch.setattr(openai_compat, "_vitals", broken)
    b = _machx([_round()])
    b.runtime_meter = _meter(tmp_path)
    events = [ev async for ev in b.ask("hello")]
    assert events[-1].kind == "result" and events[-1].data["subtype"] == "success"
    assert not [e for e in events if e.kind in ("vitals", "error")]
    assert "".join(e.data for e in events if e.kind == "text_delta") == "All good."


@pytest.mark.parametrize("source", ["slot 3", "slot 12", "prompt end", "live", "none", "host slot", "checkpoint"])
async def test_every_cache_source_the_engine_names_is_kept(source):
    b = _machx([_round(summary=dict(SUMMARY, cache_source=source))])
    events = [ev async for ev in b.ask("hello")]
    assert [e.data for e in events if e.kind == "vitals"][-1]["summary"]["cache_source"] == source
