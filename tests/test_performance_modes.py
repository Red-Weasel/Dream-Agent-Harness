"""Session mode selection is CPU-only and does not invoke a provider."""
from types import SimpleNamespace

import pytest


def modes(*args, **kwargs):
    from dream.core.performance import PerformanceModes
    return PerformanceModes(*args, **kwargs)


def test_custom_restores_original_values_after_every_explicit_mode():
    choice = modes(12000, "medium", effort_levels=("low", "medium", "high"))
    assert choice.status()["current"] == "custom"
    assert choice.select("quick") == {"output_tokens": 2048, "reasoning_effort": "low"}
    assert choice.select("balanced") == {"output_tokens": 8192, "reasoning_effort": "medium"}
    assert choice.select("thorough") == {"output_tokens": 12000, "reasoning_effort": "high"}
    assert choice.select("custom") == {"output_tokens": 12000, "reasoning_effort": "medium"}


@pytest.mark.parametrize("limit", [1, 512, 4096])
def test_modes_never_raise_original_output_bound_or_invent_effort(limit):
    choice = modes(limit, None)
    for name in ("quick", "balanced", "thorough", "custom"):
        selected = choice.select(name)
        assert 0 < selected["output_tokens"] <= limit
        assert selected["reasoning_effort"] is None


def test_reported_effort_order_and_explicit_custom_effort_are_preserved():
    choice = modes(9000, "my-existing-value", effort_levels=("lite", "deep"))
    assert choice.select("quick")["reasoning_effort"] == "lite"
    assert choice.select("thorough")["reasoning_effort"] == "deep"
    assert choice.select("custom")["reasoning_effort"] == "my-existing-value"
    status = choice.status()
    status["modes"][0]["output_tokens"] = 3
    status["effective"]["reasoning_effort"] = "overwritten"
    assert choice.select("custom") == {"output_tokens": 9000, "reasoning_effort": "my-existing-value"}


@pytest.mark.parametrize("bad", ["", "fast", None, [], True])
def test_invalid_mode_does_not_mutate_current_selection(bad):
    choice = modes(4000, None)
    choice.select("quick")
    with pytest.raises(ValueError, match="mode"):
        choice.select(bad)
    assert choice.status()["current"] == "quick"


def test_unavailable_adapter_cannot_claim_mode_applied():
    choice = modes(4000, None, supported=False, unavailable_reason="Native CLI owns generation settings")
    assert choice.status()["supported"] is False
    assert "Native CLI" in choice.status()["reason"]
    with pytest.raises(ValueError, match="unavailable"):
        choice.select("quick")


async def test_runtime_action_requires_mode_and_supported_backend():
    from dream.tui.app import App
    app = SimpleNamespace(engine=SimpleNamespace(backend=SimpleNamespace()))
    with pytest.raises(ValueError, match="unavailable"):
        await App._runtime_control(app, {"action": "performance", "mode": "quick"})


async def test_runtime_action_applies_only_explicit_session_selection():
    from dream.tui.app import App
    choice = modes(16000, "high", effort_levels=("low", "medium", "high"))

    def select(name):
        choice.select(name)
        return choice.status()

    app = SimpleNamespace(engine=SimpleNamespace(backend=SimpleNamespace(set_performance_mode=select)))
    result = await App._runtime_control(app, {"action": "performance", "mode": "quick"})
    assert result["current"] == "quick"
    assert result["effective"] == {"output_tokens": 2048, "reasoning_effort": "low"}
    with pytest.raises(ValueError, match="mode"):
        await App._runtime_control(app, {"action": "performance", "mode": 12})
