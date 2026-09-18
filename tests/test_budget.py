"""Tests for the per-prompt tool-call budget (dream/core/budget.py)."""

import dream.config as config
from dream.core.budget import ToolBudget, default_tool_budget


def test_unlimited_never_exhausts():
    b = ToolBudget(None)
    assert b.limit is None
    assert b.remaining() is None
    for _ in range(100):
        b.record()
    assert not b.exhausted()
    assert b.remaining() is None


def test_exhausted_after_limit_records():
    b = ToolBudget(3)
    assert not b.exhausted()
    b.record()
    b.record()
    assert not b.exhausted()
    assert b.remaining() == 1
    b.record()
    assert b.exhausted()
    assert b.count == 3
    assert b.remaining() == 0


def test_remaining_clamps_at_zero_past_limit():
    b = ToolBudget(2)
    b.record()
    b.record()
    b.record()  # over the cap
    assert b.exhausted()
    assert b.remaining() == 0


def test_reset_zeroes_count_keeps_limit():
    b = ToolBudget(3)
    b.record()
    b.record()
    b.record()
    assert b.exhausted()
    b.reset()
    assert b.count == 0
    assert b.limit == 3
    assert not b.exhausted()
    assert b.remaining() == 3


def test_zero_limit_starts_exhausted():
    b = ToolBudget(0)
    assert b.exhausted()
    assert b.remaining() == 0


def test_describe_off_when_unlimited():
    assert ToolBudget(None).describe() == "off"


def test_describe_n_over_limit():
    b = ToolBudget(25)
    assert b.describe() == "0/25"
    b.record()
    assert b.describe() == "1/25"


def test_default_tool_budget_anthropic_is_unlimited():
    assert default_tool_budget("anthropic") is None


def test_default_tool_budget_openai_uses_local_cap():
    assert default_tool_budget("openai") == config.DEFAULT_TOOL_BUDGET_LOCAL


def test_default_tool_budget_unknown_kind_uses_local_cap():
    assert default_tool_budget("machx") == config.DEFAULT_TOOL_BUDGET_LOCAL


def test_default_tool_budget_respects_config(monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_TOOL_BUDGET_LOCAL", 7)
    assert default_tool_budget("openai") == 7
    assert default_tool_budget("anthropic") is None
