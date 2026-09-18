"""Per-pass stats line: completion timestamp, duration, context fill, pp/decode."""

from datetime import datetime
from types import SimpleNamespace

from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.tui.render import _fmt_dur, _fmt_k, format_turn_stats

NOW = datetime(2026, 7, 4, 13, 42, 7)


def test_local_stats_line_full():
    data = {
        "stats": {
            "pp_tps": 812.4, "gen_tps": 42.31,
            "prompt_tokens": 34200, "completion_tokens": 640,
            "ctx_used": 34840, "n_ctx": 120000, "duration_s": 134.2,
        }
    }
    line = format_turn_stats(data, now=NOW)
    assert line == "⏱ done 13:42:07 · 2m14s · ctx 34.8k/120.0k (29%) · pp 812 t/s · gen 42.3 t/s"


def test_hosted_stats_line_effective_rate():
    data = {
        "usage": {"input_tokens": 1200, "cache_read_input_tokens": 140000,
                  "cache_creation_input_tokens": 2000, "output_tokens": 900},
        "duration_ms": 30000,
        "total_cost_usd": 0.42,
    }
    line = format_turn_stats(data, fallback_window=200000, now=NOW)
    assert "done 13:42:07" in line and "30.0s" in line
    assert "ctx 144.1k/200.0k (72%)" in line
    assert "gen ~30.0 t/s" in line and "$0.42" in line
    assert "pp" not in line  # hosted APIs have no prefill split — never fake one


def test_empty_result_renders_nothing():
    assert format_turn_stats({}, now=NOW) is None


def test_duration_formats():
    assert _fmt_dur(12.44) == "12.4s"
    assert _fmt_dur(134) == "2m14s"
    assert _fmt_dur(3785) == "1h03m"
    assert _fmt_k(999) == "999" and _fmt_k(34840) == "34.8k" and _fmt_k(1_200_000) == "1.2M"


def _backend() -> OpenAICompatBackend:
    provider = SimpleNamespace(
        key="machx", label="MachX", base_url="http://127.0.0.1:11435/v1",
        multimodal=False, api_key=lambda: "none",
    )
    return OpenAICompatBackend(
        provider=provider, model="m", system_prompt="s", tools=[], permission_cb=None
    )


def test_backend_turn_stats_math():
    b = _backend()
    b.n_ctx = 120000
    agg = {"pp_n": 4000, "pp_ms": 2000.0, "gen_n": 300, "gen_ms": 6000.0,
           "prompt_tokens": 4000, "completion_tokens": 300, "ctx_used": 4300}
    s = b._turn_stats(agg, duration_s=9.5)
    assert s["pp_tps"] == 2000.0  # 4000 tok / 2s
    assert s["gen_tps"] == 50.0   # 300 tok / 6s
    assert s["ctx_used"] == 4300 and s["n_ctx"] == 120000 and s["duration_s"] == 9.5


def test_backend_turn_stats_no_division_by_zero():
    b = _backend()
    agg = {"pp_n": 0, "pp_ms": 0.0, "gen_n": 0, "gen_ms": 0.0,
           "prompt_tokens": 0, "completion_tokens": 0, "ctx_used": None}
    s = b._turn_stats(agg, duration_s=0.1)
    assert s["pp_tps"] is None and s["gen_tps"] is None


def test_session_tokens_use_whole_turn_aggregate_not_last_round():
    # A multi-tool-round turn: the final `usage` block is only the last round, but
    # `stats` carries the whole-turn aggregate. session_tokens must use the total.
    from dream.core.engine import Engine
    eng = Engine(provider="machx")
    eng._absorb_result({
        "usage": {"prompt_tokens": 800, "completion_tokens": 20},   # last round only
        "stats": {"prompt_tokens": 5000, "completion_tokens": 900}, # whole turn
    })
    assert eng.session_tokens == 5900          # aggregate, not 820
    assert eng.last_context_tokens == 800      # current ctx = last round's input


def test_session_tokens_fall_back_to_usage_without_stats():
    from dream.core.engine import Engine
    eng = Engine(provider="anthropic")
    eng._absorb_result({"usage": {"input_tokens": 100, "output_tokens": 30}})
    assert eng.session_tokens == 130
