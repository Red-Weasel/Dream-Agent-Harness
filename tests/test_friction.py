"""FrictionMeter — behavioral uncertainty from the event stream. Each signal
raises the score, turn boundaries decay it and feed the sparkline, reason() only
speaks when friction is high, and the labels band the score."""

from dream.core.friction import FrictionMeter, _BARS


def _tool_result(name="recall", content="", is_error=False):
    return {"name": name, "content": content, "is_error": is_error}


def _tool_use(name="recall", inp=None):
    return {"name": name, "input": inp or {}, "id": "t1"}


# -- signals raise the score -------------------------------------------------


def test_fresh_meter_is_zero_and_low():
    m = FrictionMeter()
    assert m.score() == 0.0
    assert m.label() == "low"
    assert m.sparkline() == ""
    assert m.reason() is None


def test_tool_error_raises_score():
    m = FrictionMeter()
    m.record("tool_result", _tool_result(is_error=True))
    assert m.score() == FrictionMeter.W_ERROR
    assert m.score() > 0


def test_empty_search_raises_score():
    m = FrictionMeter()
    m.record("tool_result", _tool_result(name="recall", content="No memories matched."))
    assert m.score() == FrictionMeter.W_EMPTY


def test_zero_results_marker_on_web_search():
    m = FrictionMeter()
    m.record("tool_result", _tool_result(name="web_search", content="Found 0 results"))
    assert m.score() == FrictionMeter.W_EMPTY


def test_non_empty_search_result_does_not_raise():
    m = FrictionMeter()
    m.record("tool_result", _tool_result(name="recall", content="3 memories: ..."))
    assert m.score() == 0.0


def test_repeated_identical_tool_call_raises_score():
    m = FrictionMeter()
    m.record("tool_use", _tool_use(name="shell", inp={"command": "ls"}))
    assert m.score() == 0.0  # first call is not a repeat
    m.record("tool_use", _tool_use(name="shell", inp={"command": "ls"}))
    assert m.score() == FrictionMeter.W_REPEAT


def test_different_tool_calls_are_not_repeats():
    m = FrictionMeter()
    m.record("tool_use", _tool_use(name="shell", inp={"command": "ls"}))
    m.record("tool_use", _tool_use(name="shell", inp={"command": "pwd"}))
    assert m.score() == 0.0


def test_three_identical_calls_count_two_repeats():
    m = FrictionMeter()
    for _ in range(3):
        m.record("tool_use", _tool_use(name="shell", inp={"command": "ls"}))
    assert abs(m.score() - 2 * FrictionMeter.W_REPEAT) < 1e-9


def test_self_correction_phrase_raises_score():
    m = FrictionMeter()
    m.record("assistant_done", "Actually, that's wrong — let me reconsider.")
    # three distinct phrases present: "actually,", "that's wrong", "let me reconsider"
    assert abs(m.score() - min(1.0, 3 * FrictionMeter.W_CORRECTION)) < 1e-9


def test_self_correction_accepts_string_payload():
    m = FrictionMeter()
    m.record("assistant_done", "Wait, I mis-read the file.")
    assert m.score() == FrictionMeter.W_CORRECTION


def test_clean_text_does_not_raise():
    m = FrictionMeter()
    m.record("assistant_done", "Here is the summary you asked for.")
    assert m.score() == 0.0


def test_score_is_clamped_to_one():
    m = FrictionMeter()
    for _ in range(20):
        m.record("tool_result", _tool_result(is_error=True))
    assert m.score() == 1.0


def test_malformed_payloads_are_ignored():
    m = FrictionMeter()
    m.record("tool_result", None)
    m.record("tool_use", "not a dict")
    m.record("bogus_kind", {"whatever": 1})
    assert m.score() == 0.0


# -- decay + sparkline -------------------------------------------------------


def test_turn_boundary_decays_score():
    m = FrictionMeter()
    m.record("tool_result", _tool_result(is_error=True))
    before = m.score()
    m.turn_boundary()
    assert abs(m.score() - before * FrictionMeter.DECAY) < 1e-9


def test_turn_boundary_archives_peak_into_window():
    m = FrictionMeter()
    m.record("tool_result", _tool_result(is_error=True))
    m.turn_boundary()
    assert len(m.sparkline()) == 1  # one closed turn → one bar


def test_turn_boundary_resets_per_turn_counts_and_last_tool():
    m = FrictionMeter()
    m.record("tool_use", _tool_use(name="shell", inp={"command": "ls"}))
    m.turn_boundary()
    # same call again in the new turn is not a back-to-back repeat
    m.record("tool_use", _tool_use(name="shell", inp={"command": "ls"}))
    # only the decayed residual remains, no repeat bump was added
    assert m.score() < FrictionMeter.W_REPEAT


def test_window_is_capped():
    m = FrictionMeter()
    for _ in range(FrictionMeter.WINDOW + 5):
        m.turn_boundary()
    assert len(m.sparkline()) == FrictionMeter.WINDOW


def test_sparkline_maps_low_and_high_turns():
    m = FrictionMeter()
    # a quiet turn → lowest bar
    m.turn_boundary()
    # a max-friction turn → highest bar
    for _ in range(20):
        m.record("tool_result", _tool_result(is_error=True))
    m.turn_boundary()
    spark = m.sparkline()
    assert len(spark) == 2
    assert spark[0] == _BARS[0]
    assert spark[1] == _BARS[-1]


def test_sparkline_glyphs_all_from_the_ramp():
    m = FrictionMeter()
    for n in range(4):
        for _ in range(n):
            m.record("tool_result", _tool_result(is_error=True))
        m.turn_boundary()
    assert all(ch in _BARS for ch in m.sparkline())


# -- labels + reason ---------------------------------------------------------


def test_label_thresholds():
    m = FrictionMeter()
    assert m.label() == "low"
    m.record("tool_result", _tool_result(is_error=True))  # 0.34 → warm
    assert m.label() == "warm"
    m.record("tool_result", _tool_result(is_error=True))  # 0.68 → high
    assert m.label() == "high"


def test_reason_is_none_until_high():
    m = FrictionMeter()
    m.record("tool_result", _tool_result(is_error=True))  # warm
    assert m.label() == "warm"
    assert m.reason() is None


def test_reason_names_the_signals_when_high():
    m = FrictionMeter()
    m.record("tool_result", _tool_result(is_error=True))
    m.record("tool_result", _tool_result(is_error=True))
    m.record("tool_result", _tool_result(is_error=True))
    m.record("tool_result", _tool_result(name="recall", content="no memories"))
    m.record("tool_result", _tool_result(name="web", content="0 results"))
    assert m.label() == "high"
    assert m.reason() == "3 tool errors, 2 empty searches this turn"


def test_reason_singular_pluralization():
    m = FrictionMeter()
    # one error + two corrections → high, singular "error", singular "self-correction"? no, two.
    m.record("tool_result", _tool_result(is_error=True))
    m.record("assistant_done", "Actually, wait, that's wrong.")
    assert m.label() == "high"
    r = m.reason()
    assert r is not None
    assert "1 tool error" in r and "self-correction" in r
    assert "1 tool errors" not in r  # singular


def test_reason_clears_after_turn_boundary():
    m = FrictionMeter()
    for _ in range(3):
        m.record("tool_result", _tool_result(is_error=True))
    assert m.reason() is not None
    m.turn_boundary()
    # residual 0.5 is below HIGH, so no reason and counts are cleared
    assert m.label() != "high"
    assert m.reason() is None
