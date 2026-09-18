"""InferenceMeter: TTFT, live tokens/s, exact-stat absorption, session rollups."""

from __future__ import annotations

import pytest

from dream.telemetry.meter import InferenceMeter


class Clock:
    def __init__(self) -> None:
        self.t = 50.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture()
def m():
    clock = Clock()
    meter = InferenceMeter(clock=clock)
    meter.clock = clock  # test handle
    return meter


def test_ttft_first_output_of_turn(m):
    m.turn_start()
    assert m.state == "waiting" and m.ttft_s is None
    m.clock.t += 1.5
    m.feed("text_delta", "hello")
    assert m.ttft_s == pytest.approx(1.5)
    m.clock.t += 1.0
    m.feed("text_delta", "world")
    assert m.ttft_s == pytest.approx(1.5)  # first output only


def test_thinking_counts_as_first_output(m):
    m.turn_start()
    m.clock.t += 0.7
    m.feed("thinking_delta", "hmm")
    assert m.ttft_s == pytest.approx(0.7)
    assert m.state == "thinking"


def test_live_tps_sliding_window(m):
    m.turn_start()
    for _ in range(3):
        m.clock.t += 0.5
        m.feed("text_delta", "x" * 40)  # 10 est tokens per delta
    # samples: (0.5,10) (1.0,20) (1.5,30) → 20 tok over 1.0 s
    assert m.live_tps() == pytest.approx(20.0)
    assert m.out_tokens_est() == 30
    assert m.state == "decoding"


def test_live_tps_prunes_old_samples(m):
    m.turn_start()
    m.clock.t += 0.1
    m.feed("text_delta", "x" * 400)  # early burst: 100 tokens
    for _ in range(4):
        m.clock.t += 1.0
        m.feed("text_delta", "x" * 40)
    # burst is >3 s old → only the 10-tok/s tail is in the window
    assert m.live_tps() == pytest.approx(10.0, rel=0.2)


def test_live_tps_none_when_idle_or_sparse(m):
    assert m.live_tps() is None
    m.turn_start()
    m.feed("text_delta", "one")
    assert m.live_tps() is None  # a single sample is not a rate
    m.feed("result", {})
    assert m.live_tps() is None  # idle again


def test_round_ttft_resets_after_tool_result(m):
    m.turn_start()
    m.clock.t += 2.0
    m.feed("text_delta", "planning")
    m.feed("tool_use", {"name": "read"})
    assert m.state == "tools"
    m.clock.t += 3.0
    m.feed("tool_result", {"name": "read"})
    m.clock.t += 1.25
    m.feed("text_delta", "answer")
    assert m.round_ttft_s == pytest.approx(1.25)  # prefill proxy for this round
    assert m.ttft_s == pytest.approx(2.0)  # turn TTFT untouched


def test_exact_round_stats_absorbed(m):
    m.turn_start()
    m.feed("stats", {"pp_n": 1000, "pp_ms": 500.0, "gen_n": 100, "gen_ms": 2000.0})
    assert m.pp_time_s == pytest.approx(0.5)
    assert m.pp_tps == pytest.approx(2000.0)
    assert m.gen_tps == pytest.approx(50.0)
    assert m.prompt_tokens == 1000


def test_result_local_shape_rolls_up(m):
    m.turn_start()
    m.feed("result", {
        "stats": {"pp_tps": 1800.0, "gen_tps": 42.0, "prompt_tokens": 5000,
                  "completion_tokens": 900, "ctx_used": 5900, "n_ctx": 32768,
                  "duration_s": 30.0},
    })
    lt = m.last_turn
    assert lt["gen_tps"] == 42.0 and lt["pp_tps"] == 1800.0
    assert lt["in_tokens"] == 5000 and lt["out_tokens"] == 900
    assert lt["ctx_used"] == 5900 and lt["n_ctx"] == 32768
    assert m.state == "idle"
    s = m.snapshot()["session"]
    assert s["turns"] == 1 and s["in_tokens"] == 5000 and s["out_tokens"] == 900


def test_result_hosted_shape_effective_rate(m):
    m.turn_start()
    m.clock.t += 0.8
    m.feed("text_delta", "hi")
    m.feed("result", {
        "usage": {"input_tokens": 1200, "output_tokens": 300},
        "duration_ms": 10000,
    })
    lt = m.last_turn
    assert lt["duration_s"] == pytest.approx(10.0)
    assert lt["gen_tps"] == pytest.approx(30.0)  # effective: 300 tok / 10 s
    assert lt["ttft_s"] == pytest.approx(0.8)


def test_turn_start_resets_turn_but_keeps_session(m):
    m.turn_start()
    m.clock.t += 1.0
    m.feed("text_delta", "x" * 400)
    m.feed("result", {"usage": {"input_tokens": 10, "output_tokens": 100},
                      "duration_ms": 2000})
    m.turn_start()
    assert m.ttft_s is None and m.out_tokens_est() == 0 and m.state == "waiting"
    assert m.snapshot()["session"]["turns"] == 1


def test_two_turns_accumulate(m):
    for _ in range(2):
        m.turn_start()
        m.feed("result", {"usage": {"input_tokens": 100, "output_tokens": 50},
                          "duration_ms": 5000})
    s = m.snapshot()["session"]
    assert s["turns"] == 2 and s["out_tokens"] == 100
    assert s["avg_tps"] == pytest.approx(10.0)


def test_garbage_data_is_ignored(m):
    m.turn_start()
    m.feed("text_delta", None)
    m.feed("stats", "not-a-dict")
    m.feed("stats", {"pp_ms": "junk", "gen_n": object()})  # garbage values too
    m.feed("result", None)
    m.feed("unknown_kind", {"x": 1})
    assert m.snapshot()["state"] == "waiting"
    assert m.pp_tps is None and m.gen_tps is None
