"""Reasoning-effort ladder: normalization, the ultra fan-out flag, and the
per-backend payload fragments. Pure mapping — every LEVEL must round-trip."""

import pytest

from dream.core import effort


# --- normalize ---------------------------------------------------------------


def test_normalize_canonical_levels():
    for lvl in effort.LEVELS:
        assert effort.normalize(lvl) == lvl


def test_normalize_alias_and_case_and_whitespace():
    assert effort.normalize("medium") == "med"
    assert effort.normalize("MEDIUM") == "med"
    assert effort.normalize("  High ") == "high"
    assert effort.normalize("ULTRA") == "ultra"


def test_normalize_rejects_unknown():
    for bad in ("low", "", "  ", "gigacharge", None):
        assert effort.normalize(bad) is None


# --- is_ultra ----------------------------------------------------------------


def test_is_ultra_only_for_ultra():
    assert effort.is_ultra("ultra") is True
    assert effort.is_ultra("ULTRA") is True
    for lvl in ("med", "high", "xhigh", "max"):
        assert effort.is_ultra(lvl) is False


def test_is_ultra_total_on_garbage():
    assert effort.is_ultra("nonsense") is False
    assert effort.is_ultra("") is False


# --- for_openai --------------------------------------------------------------


def test_for_openai_map():
    assert effort.for_openai("med") == {"reasoning_effort": "medium"}
    assert effort.for_openai("high") == {"reasoning_effort": "high"}
    assert effort.for_openai("xhigh") == {"reasoning_effort": "xhigh"}
    # OpenAI's ladder tops at xhigh — max and ultra clamp there.
    assert effort.for_openai("max") == {"reasoning_effort": "xhigh"}
    assert effort.for_openai("ultra") == {"reasoning_effort": "xhigh"}


def test_for_openai_accepts_alias():
    assert effort.for_openai("medium") == {"reasoning_effort": "medium"}


def test_for_openai_values_are_valid_effort():
    accepted = {"low", "medium", "high", "xhigh"}
    for lvl in effort.LEVELS:
        assert effort.for_openai(lvl)["reasoning_effort"] in accepted


def test_for_openai_rejects_unknown():
    with pytest.raises(ValueError):
        effort.for_openai("turbo")


# --- for_anthropic -----------------------------------------------------------


def test_for_anthropic_tiers_ascend():
    tokens = [effort.for_anthropic(l)["thinking_budget_tokens"] for l in ("med", "high", "xhigh", "max")]
    assert tokens == sorted(tokens)
    assert tokens[0] < tokens[-1]
    assert all(isinstance(t, int) and t > 0 for t in tokens)


def test_for_anthropic_ultra_matches_max():
    assert effort.for_anthropic("ultra") == effort.for_anthropic("max")


def test_for_anthropic_rejects_unknown():
    with pytest.raises(ValueError):
        effort.for_anthropic("bogus")


# --- describe ----------------------------------------------------------------


def test_describe_is_a_nonempty_label_for_each_level():
    for lvl in effort.LEVELS:
        label = effort.describe(lvl)
        assert isinstance(label, str) and label


def test_describe_marks_ultra_distinctly():
    assert effort.describe("ultra") != effort.describe("max")
    assert "fan" in effort.describe("ultra").lower()


def test_describe_rejects_unknown():
    with pytest.raises(ValueError):
        effort.describe("nope")


# --- round-trip --------------------------------------------------------------


def test_levels_is_the_expected_ladder():
    assert effort.LEVELS == ("med", "high", "xhigh", "max", "ultra")


def test_full_round_trip_over_levels():
    for lvl in effort.LEVELS:
        assert effort.normalize(lvl) == lvl
        assert "reasoning_effort" in effort.for_openai(lvl)
        assert effort.for_anthropic(lvl)["thinking_budget_tokens"] > 0
        assert effort.describe(lvl)
