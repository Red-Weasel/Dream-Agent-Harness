"""Offline tests for the MoE tools' plumbing.

These never spawn a real advisor: a fake ``dream.core.moe`` module is injected so the
tools' delegation, formatting, and error paths are exercised in isolation. The real
Module A implementation is validated by ``tests/test_moe.py``.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from dream.tools import context as tool_context
from dream.tools import moe_tools
from dream.tools.context import ToolContext, set_context


@dataclass(frozen=True)
class _MoeConfig:
    """Stand-in for core.moe.MoeConfig — the tools only read ``.advisors``."""

    orchestrator: str
    advisors: list[str]


@pytest.fixture
def fake_moe(monkeypatch):
    """Inject a fake ``dream.core.moe`` whose invokers record their calls."""
    calls = {"consult": [], "council": []}
    mod = types.ModuleType("dream.core.moe")

    async def consult_advisor(provider_key, question, context="", *, cwd=None):
        calls["consult"].append((provider_key, question, context, cwd))
        return f"answer-from-{provider_key}"

    async def council(advisors, question, context="", *, cwd=None):
        calls["council"].append((list(advisors), question, context, cwd))
        return [
            {"advisor": a, "label": a.upper(), "answer": f"answer-from-{a}"}
            for a in advisors
        ]

    mod.MoeConfig = _MoeConfig
    mod.consult_advisor = consult_advisor
    mod.council = council

    import dream.core as core_pkg

    monkeypatch.setitem(sys.modules, "dream.core.moe", mod)
    monkeypatch.setattr(core_pkg, "moe", mod, raising=False)
    mod.calls = calls
    return mod


def _install_context(moe_config, workspace="/tmp/moe-ws"):
    ctx = ToolContext(
        store=SimpleNamespace(),
        working=SimpleNamespace(),
        browser=SimpleNamespace(),
        session_id="test-session",
        workspace=Path(workspace),
        moe_config=moe_config,
    )
    set_context(ctx)
    return ctx


@pytest.fixture(autouse=True)
def _reset_context():
    yield
    # Leave the module-level context unset so tests don't leak into one another.
    tool_context._CTX = None


async def test_consult_returns_formatted_answer(fake_moe):
    cfg = fake_moe.MoeConfig(orchestrator="codex", advisors=["grok", "codex"])
    _install_context(cfg, workspace="/tmp/ws-consult")

    result = await moe_tools.consult.handler(
        {"advisor": "grok", "question": "is this wise?", "context": "some background"}
    )

    assert not result.get("is_error")
    text = result["content"][0]["text"]
    assert "answer-from-grok" in text
    assert "Grok" in text  # provider label, not the bare key
    assert text.endswith("answer-from-grok")
    # Delegated with the right args and the session workspace as cwd.
    assert fake_moe.calls["consult"] == [
        ("grok", "is this wise?", "some background", "/tmp/ws-consult")
    ]


async def test_consult_context_optional(fake_moe):
    cfg = fake_moe.MoeConfig(orchestrator="codex", advisors=["grok"])
    _install_context(cfg)

    result = await moe_tools.consult.handler({"advisor": "grok", "question": "q"})

    assert not result.get("is_error")
    assert fake_moe.calls["consult"][0][2] == ""  # context defaulted to empty


async def test_consult_unknown_advisor_errs_cleanly(fake_moe):
    cfg = fake_moe.MoeConfig(orchestrator="codex", advisors=["grok", "codex"])
    _install_context(cfg)

    result = await moe_tools.consult.handler({"advisor": "gemini", "question": "q"})

    assert result.get("is_error") is True
    text = result["content"][0]["text"]
    assert "gemini" in text
    assert "grok" in text and "codex" in text  # lists the valid line-up
    assert not fake_moe.calls["consult"]  # never delegated


async def test_consult_without_moe_config_errs():
    _install_context(None)

    result = await moe_tools.consult.handler({"advisor": "grok", "question": "q"})

    assert result.get("is_error") is True
    assert "MoE" in result["content"][0]["text"]


async def test_council_formats_titled_blocks(fake_moe):
    cfg = fake_moe.MoeConfig(orchestrator="codex", advisors=["grok", "codex"])
    _install_context(cfg, workspace="/tmp/ws-council")

    result = await moe_tools.council.handler({"question": "big call?", "context": "bg"})

    assert not result.get("is_error")
    text = result["content"][0]["text"]
    assert text == (
        "### GROK\nanswer-from-grok\n\n### CODEX\nanswer-from-codex"
    )
    # Order preserved and delegated with the full advisor list + workspace cwd.
    assert fake_moe.calls["council"] == [
        (["grok", "codex"], "big call?", "bg", "/tmp/ws-council")
    ]


async def test_council_without_moe_config_errs():
    _install_context(None)

    result = await moe_tools.council.handler({"question": "q"})

    assert result.get("is_error") is True
    assert "MoE" in result["content"][0]["text"]


def test_moe_tools_exports_both_tools():
    tools = moe_tools.moe_tools()
    assert [t.name for t in tools] == ["consult", "council"]
    assert tools == [moe_tools.consult, moe_tools.council]
