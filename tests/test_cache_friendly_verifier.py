"""DREAM-112, fix list #71: on a local engine that keeps ONE conversation cached, the end-of-turn verifier
must not evict the lead's. Live 2026-09-24 (MiMo-V2.6, 200k window): ten verifier requests of 3-8k tokens
replaced a 135k-token chat and the owner's next message waited ~8 minutes for the re-read.

How many conversations the engine caches comes from its /props `prompt_cache_slots` (1 = the live one
only), else a per-model default (MiMo 1, DeepSeek-V4.1 more than one). With one slot the check is skipped
with a visible note by default; DREAM_SINGLE_SLOT_VERIFIER=continue runs it inside the lead conversation
instead, so the engine only ever extends its cached prefix. With more slots it runs as it always has.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from dream.core.backends import openai_compat
from test_cache_friendly_head import FakeEngine, backend, tool, turn

VERIFIER = SimpleNamespace(name="verifier", description="Page verifier. Checks the delivered page.",
                           prompt="VERIFIER PROMPT",
                           tool_names=("show_html", "get_webview_logs", "save_screenshot", "see", "eval_js"))
DONE = {"calls": [("done", {"path": "page.html"})]}
SHIPPED = {"text": "Shipped the page."}
FINDING = "The header overlaps the canvas at 1280px."
CHECKS = [{"calls": [("get_webview_logs", {})]}, {"text": FINDING}]


def props(slots=None, n_ctx=200_000):
    out = {"default_generation_settings": {"n_ctx": n_ctx}, "total_slots": 1}
    if slots is not None:
        out["prompt_cache_slots"] = slots
    return out


async def page_backend(engine, *, model="m"):
    tools = [tool("done", "clean"), tool("show_html", "shown"), tool("get_webview_logs", "no console errors"),
             tool("save_screenshot", "saved shot.png"), tool("see", "one image", images=1), tool("eval_js", "42")]
    b = backend(engine, tools=tools, subagents={"verifier": VERIFIER}, model=model, multimodal=True)
    b.n_ctx = await b._probe_n_ctx()          # what connect() does: /props into the backend
    return b


def side(engine):
    return [r for r in engine.requests if r["kind"] == "side"]


@pytest.fixture(autouse=True)
def _default_mode(monkeypatch):
    monkeypatch.delenv("DREAM_SINGLE_SLOT_VERIFIER", raising=False)


async def test_a_single_slot_engine_skips_the_check_says_so_and_keeps_the_cache():
    engine = FakeEngine(lead=[DONE, SHIPPED, {"text": "ok"}], side=list(CHECKS), props=props(slots=1))
    b = await page_backend(engine)
    events = await turn(b, "build the page")
    assert side(engine) == []                                  # no verifier conversation was sent
    said = [str(e.data) for e in events if e.kind == "system"]
    assert any(s.startswith("verifier: skipped on page.html") and "one conversation cached" in s
               and "DREAM_SINGLE_SLOT_VERIFIER=continue" in s for s in said), said
    result = events[-1].data
    assert result["subtype"] == "success" and not result["is_error"]    # a deliberate skip is not a failure
    assert result["delivery_review"]["status"] == "not_requested"
    assert result["delivery_review"]["reason"] == "single_slot_engine"
    await turn(b, "next")
    last, before = engine.requests[-1], engine.requests[-2]
    assert last["cached"] >= before["prompt_tokens"]           # the owner's conversation stayed cached


@pytest.mark.parametrize("model,architecture", [("MiMo-V2.6-Flash-RL-UNCENSORED", None), ("m", "mimo_v2")])
async def test_without_the_props_field_mimo_counts_as_single_slot(model, architecture):
    engine = FakeEngine(lead=[DONE, SHIPPED], side=list(CHECKS), props=props())
    b = await page_backend(engine, model=model)
    if architecture:
        b._local_capabilities = {"architecture": architecture}
    assert b._prompt_cache_slots() == (1, "Dream's default for mimo_v2")
    await turn(b, "build the page")
    assert side(engine) == []


@pytest.mark.parametrize("model,slots", [
    ("m", 4),                                    # the engine reports host slots
    ("MiMo-V2.6-Flash-RL-UNCENSORED", 3),        # the field wins over the table (#70 deployed)
    ("DeepSeek-V4.1-Flash", None),               # the table: host slots and disk entries
    ("m", None),                                 # unknown: as it always was
])
async def test_an_engine_with_more_slots_runs_the_verifier_as_before(model, slots):
    engine = FakeEngine(lead=[DONE, SHIPPED], side=list(CHECKS), props=props(slots=slots))
    b = await page_backend(engine, model=model)
    events = await turn(b, "build the page")
    checks = side(engine)
    assert len(checks) == 2
    assert checks[0]["messages"][0] == {"role": "system", "content": "VERIFIER PROMPT"}   # its own conversation
    assert any("verifier: findings on page.html" in str(e.data) for e in events if e.kind == "system")
    assert b._pending_findings and FINDING in b._pending_findings


@pytest.mark.parametrize("bad", [0, -1, "1", True, 1.0, None])
async def test_a_malformed_slot_count_is_not_believed(bad):
    engine = FakeEngine(props={**props(), "prompt_cache_slots": bad})
    b = await page_backend(engine, model="MiMo-V2.6-Flash-RL-UNCENSORED")
    assert b._prompt_cache_slots() == (1, "Dream's default for mimo_v2")
    engine = FakeEngine(props={**props(), "prompt_cache_slots": bad})
    assert (await page_backend(engine))._prompt_cache_slots() == (None, "unknown")


async def test_the_default_table_is_for_machx_only():
    """DREAM-112 gate finding 6: MiMo behind another loopback provider, whose /props does not say, is
    unknown; its verifier runs as a separate conversation, as every verifier did before."""
    engine = FakeEngine(lead=[DONE, SHIPPED], side=list(CHECKS), props=props())
    tools = [tool("done", "clean"), tool("show_html", "shown"), tool("get_webview_logs", "no console errors"),
             tool("save_screenshot", "saved shot.png"), tool("see", "one image", images=1), tool("eval_js", "42")]
    b = backend(engine, tools=tools, subagents={"verifier": VERIFIER}, model="MiMo-V2.6-Flash-RL-UNCENSORED",
                key="openai", base_url="http://127.0.0.1:8080/v1", multimodal=True)
    b._coordination_override = _NoLease()
    assert b._cache_sensitive() and b._prompt_cache_slots() == (None, "unknown")
    await turn(b, "build the page")
    assert len(side(engine)) == 2
    assert side(engine)[0]["messages"][0] == {"role": "system", "content": "VERIFIER PROMPT"}


class _NoLease:
    """A loopback provider takes the local lease; this test's fake engine has none to take."""
    def request(self, **_):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def status(self):
        return {"enabled": False}


async def test_continue_runs_the_check_inside_the_conversation_and_only_extends_the_cache(monkeypatch):
    monkeypatch.setenv("DREAM_SINGLE_SLOT_VERIFIER", "continue")
    engine = FakeEngine(lead=[DONE, SHIPPED, {"text": "ok"}], side=list(CHECKS), props=props(slots=1))
    b = await page_backend(engine)
    events = await turn(b, "build the page")
    lead = engine.requests[1]
    checks = side(engine)
    assert len(checks) == 2
    for check in checks:
        assert check["head"] == lead["head"]                   # the lead's system text and tools
        assert check["messages"][:len(lead["messages"])] == lead["messages"]   # the lead history, extended
    instruction = checks[0]["messages"][-1]
    assert instruction["role"] == "user" and instruction["name"] == "dream_verifier_instruction"
    assert "not from the user" in instruction["content"] and "VERIFIER PROMPT" in instruction["content"]
    assert checks[0]["cached"] >= lead["prompt_tokens"] and checks[1]["cached"] >= checks[0]["prompt_tokens"]
    assert any("verifier: findings on page.html" in str(e.data) for e in events if e.kind == "system")
    assert events[-1].data["delivery_review"]["status"] == "needs_attention"
    assert b.messages[-1] == {"role": "assistant", "content": FINDING}   # its rounds stay in the history
    await turn(b, "next")
    nxt = engine.requests[-1]
    assert nxt["kind"] == "lead" and nxt["cached"] >= checks[-1]["prompt_tokens"]   # no re-read
    assert not [m for m in b.messages if m.get("name") == "dream_verifier_report"]  # the report is not repeated


async def test_continue_still_runs_only_the_verifiers_tools(monkeypatch):
    monkeypatch.setenv("DREAM_SINGLE_SLOT_VERIFIER", "continue")
    ran = []
    engine = FakeEngine(lead=[DONE, SHIPPED], props=props(slots=1),
                        side=[{"calls": [("done", {"path": "other.html"})]}, {"text": "PASS"}])
    b = await page_backend(engine)
    b.tools_by_name["done"] = tool("done", "clean", calls=ran)
    await turn(b, "build the page")
    assert ran == [("done", {"path": "page.html"})]            # the lead's call ran; the check's did not
    assert any(m.get("role") == "tool" and "not available to this subagent" in m["content"] for m in b.messages)


async def test_the_owner_sees_the_check_progress(monkeypatch):
    for mode in ("continue", None):
        if mode:
            monkeypatch.setenv("DREAM_SINGLE_SLOT_VERIFIER", mode)
        engine = FakeEngine(lead=[DONE, SHIPPED], side=list(CHECKS), props=props(slots=1 if mode else 4))
        b = await page_backend(engine)
        seen = []
        b._background_emit = seen.append
        await turn(b, "build the page")
        texts = [e.data.get("text") for e in seen if e.kind == "agent_activity" and e.data.get("kind") == "status"]
        cap = openai_compat._SUB_MAX_ROUNDS
        assert f"Checking the work (1/{cap})" in texts and f"Checking the work (2/{cap})" in texts, texts
        monkeypatch.delenv("DREAM_SINGLE_SLOT_VERIFIER", raising=False)


async def test_a_directed_check_on_a_single_slot_engine_is_left_to_the_model(monkeypatch):
    for mode in (None, "continue"):
        if mode:
            monkeypatch.setenv("DREAM_SINGLE_SLOT_VERIFIER", mode)
        engine = FakeEngine(side=list(CHECKS), props=props(slots=1))
        b = await page_backend(engine)
        await b._exec_tool("done", {"path": "page.html"})
        text, bad = await b._exec_tool("fork_verifier_agent", {"task": "check the spacing"})
        assert bad and text.startswith("Not run: this engine keeps one conversation cached")
        assert "Check it yourself now" in text and side(engine) == []


async def test_scheduling_the_sweep_on_a_single_slot_engine(monkeypatch):
    engine = FakeEngine(props=props(slots=1))
    b = await page_backend(engine)
    await b._exec_tool("done", {"path": "page.html"})
    b._verify_at_turn_end = None
    text, bad = await b._exec_tool("fork_verifier_agent", {})
    assert not bad and "skipped on this engine unless DREAM_SINGLE_SLOT_VERIFIER=continue" in text
    assert b._verify_at_turn_end is None
    monkeypatch.setenv("DREAM_SINGLE_SLOT_VERIFIER", "continue")
    text, bad = await b._exec_tool("fork_verifier_agent", {})
    assert not bad and "Verifier will sweep page.html" in text and b._verify_at_turn_end == "page.html"
