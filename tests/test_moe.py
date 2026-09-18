"""Module A — Dream MoE council orchestration.

The three per-kind invokers (`_consult_cli` / `_consult_anthropic` / `_consult_openai`)
are monkeypatched throughout, so no real CLI is spawned and no network is touched. What
we actually exercise: config save/load round-trip + tolerance of a missing/corrupt file;
`consult_advisor` dispatching to the right invoker by provider kind and building the
prompt from context+question; that it never raises (unknown key / raising invoker →
error string); and `council`'s parallel, order-preserving, failure-tolerant fan-out.
"""

from __future__ import annotations

import asyncio

from dream.core import moe


# --- config save / load ------------------------------------------------------


def test_config_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "sub" / "moe.json"  # parent doesn't exist yet — save must mkdir
    monkeypatch.setattr(moe, "CONFIG_PATH", path)

    cfg = moe.MoeConfig(orchestrator="codex", advisors=["grok", "anthropic"])
    moe.save_config(cfg)

    assert path.exists()
    loaded = moe.load_config()
    assert loaded == cfg
    assert loaded.orchestrator == "codex"
    assert loaded.advisors == ["grok", "anthropic"]


def test_load_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(moe, "CONFIG_PATH", tmp_path / "absent.json")
    assert moe.load_config() is None


def test_load_corrupt_returns_none(tmp_path, monkeypatch):
    path = tmp_path / "moe.json"
    path.write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setattr(moe, "CONFIG_PATH", path)
    assert moe.load_config() is None


# --- consult_advisor dispatch ------------------------------------------------


def _patch_invokers(monkeypatch, cli=None, anthropic=None, openai=None):
    async def _reject(provider, prompt, *, cwd=None):
        raise AssertionError(f"wrong invoker called for {provider.key}")

    monkeypatch.setattr(moe, "_consult_cli", cli or _reject)
    monkeypatch.setattr(moe, "_consult_anthropic", anthropic or _reject)
    monkeypatch.setattr(moe, "_consult_openai", openai or _reject)


async def test_dispatch_cli(monkeypatch):
    seen = {}

    async def fake(provider, prompt, *, cwd=None):
        seen.update(key=provider.key, prompt=prompt, cwd=cwd)
        return "cli-answer"

    _patch_invokers(monkeypatch, cli=fake)
    out = await moe.consult_advisor("codex", "how?", "some context", cwd="/tmp/ws")

    assert out == "cli-answer"
    assert seen["key"] == "codex"                 # codex is a cli-kind provider
    assert seen["prompt"] == "some context\n\nhow?"  # context joined before question
    assert seen["cwd"] == "/tmp/ws"


async def test_dispatch_anthropic(monkeypatch):
    seen = {}

    async def fake(provider, prompt, *, cwd=None):
        seen.update(key=provider.key, prompt=prompt)
        return "anthropic-answer"

    _patch_invokers(monkeypatch, anthropic=fake)
    out = await moe.consult_advisor("anthropic", "why?")

    assert out == "anthropic-answer"
    assert seen["key"] == "anthropic"
    assert seen["prompt"] == "why?"               # no context → bare question


async def test_dispatch_openai(monkeypatch):
    seen = {}

    async def fake(provider, prompt, *, cwd=None):
        seen["key"] = provider.key
        return "openai-answer"

    _patch_invokers(monkeypatch, openai=fake)
    out = await moe.consult_advisor("machx", "q")

    assert out == "openai-answer"
    assert seen["key"] == "machx"                 # machx is an openai-kind provider


# --- consult_advisor never raises --------------------------------------------


async def test_unknown_provider_key_returns_error(monkeypatch):
    _patch_invokers(monkeypatch)  # every invoker would blow up if reached
    out = await moe.consult_advisor("nope-not-a-provider", "q")

    assert out.startswith("[") and out.endswith("]")
    assert "unavailable" in out
    assert "nope-not-a-provider" in out           # label falls back to the raw key


async def test_invoker_failure_is_wrapped(monkeypatch):
    async def boom(provider, prompt, *, cwd=None):
        raise RuntimeError("model-on-fire")

    _patch_invokers(monkeypatch, cli=boom)
    out = await moe.consult_advisor("codex", "q")

    assert out.startswith("[") and out.endswith("]")
    assert "unavailable" in out
    assert "model-on-fire" in out
    assert "Codex" in out                          # provider label, not the raw key


# --- council fan-out ---------------------------------------------------------


async def test_council_preserves_order_and_labels(monkeypatch):
    async def echo(provider, prompt, *, cwd=None):
        return f"ok:{provider.key}"

    # machx→openai, codex→cli, anthropic→anthropic: one of each kind.
    _patch_invokers(monkeypatch, cli=echo, anthropic=echo, openai=echo)
    results = await moe.council(["machx", "codex", "anthropic"], "q")

    assert [r["advisor"] for r in results] == ["machx", "codex", "anthropic"]
    assert [r["answer"] for r in results] == ["ok:machx", "ok:codex", "ok:anthropic"]
    # labels come from the provider table, not the key
    assert results[2]["label"] == "Claude · Anthropic"


async def test_council_is_failure_tolerant(monkeypatch):
    async def ok(provider, prompt, *, cwd=None):
        return f"ok:{provider.key}"

    async def boom(provider, prompt, *, cwd=None):
        raise RuntimeError("boom")

    _patch_invokers(monkeypatch, openai=ok, cli=boom, anthropic=ok)
    results = await moe.council(["machx", "codex", "anthropic"], "q")

    assert [r["advisor"] for r in results] == ["machx", "codex", "anthropic"]
    assert results[0]["answer"] == "ok:machx"
    assert "unavailable" in results[1]["answer"]   # codex raised, entry carries the error
    assert "boom" in results[1]["answer"]
    assert results[2]["answer"] == "ok:anthropic"  # others unaffected


async def test_council_runs_in_parallel(monkeypatch):
    # If the fan-out were sequential the first advisor would block forever waiting for
    # the others to start; a per-call timeout would fire and every answer would be an
    # error string. Parallel start → the gate opens and every answer is the real one.
    n = 3
    started = 0
    all_started = asyncio.Event()

    async def gated(provider, prompt, *, cwd=None):
        nonlocal started
        started += 1
        if started == n:
            all_started.set()
        await asyncio.wait_for(all_started.wait(), timeout=2.0)
        return f"ok:{provider.key}"

    _patch_invokers(monkeypatch, cli=gated, anthropic=gated, openai=gated)
    results = await moe.council(["codex", "machx", "anthropic"], "q")

    assert [r["advisor"] for r in results] == ["codex", "machx", "anthropic"]
    assert all(r["answer"].startswith("ok:") for r in results)
