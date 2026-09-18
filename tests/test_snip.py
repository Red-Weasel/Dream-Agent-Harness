"""snip: the model marks exchanges it is done with; they go when pressure builds,
whole rounds at a time, before the blind stubbing."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_schema_deferral import _FakeClient, _backend, _text_round, _tool_round  # noqa: E402
from dream.core.backends import openai_compat  # noqa: E402
from dream.tools.native import NATIVE_TOOLS  # noqa: E402

pytestmark = pytest.mark.asyncio


async def _turns(b, n):
    for i in range(n):
        b._client = _FakeClient([_tool_round("read_file"), _text_round()])
        [ev async for ev in b.ask(f"turn {i + 1}")]


def _user_contents(b):
    return [m["content"] for m in b.messages if m.get("role") == "user"]


async def test_every_user_message_carries_an_id_the_model_can_name():
    b = _backend(NATIVE_TOOLS)
    await _turns(b, 3)
    assert [c.rsplit("[id:", 1)[1].rstrip("]") for c in _user_contents(b)] == ["m0001", "m0002", "m0003"]
    assert _user_contents(b)[0].startswith("turn 1\n\n[id:m0001]")
    sent = b._client.payloads[0]["messages"]
    assert any("[id:m0003]" in str(m.get("content")) for m in sent), "the id reaches the model"


async def test_snip_is_offered_once_there_is_history_to_snip():
    b = _backend(NATIVE_TOOLS)
    names = lambda: [s["function"]["name"] for s in b._client.payloads[0]["tools"]]  # noqa: E731
    await _turns(b, 1)
    assert "snip" not in names()
    await _turns(b, 1)
    assert "snip" not in names()
    await _turns(b, 1)
    assert names()[-1] == "snip", "from the third user message on, and always last"


async def test_register_validates_ids_and_never_the_live_turn():
    b = _backend(NATIVE_TOOLS)
    await _turns(b, 3)
    msg, bad = await b._exec_tool("snip", {"from_id": "m0001", "to_id": "m0002", "reason": "done"})
    assert not bad and "2 exchange(s)" in msg and b._snips == [("m0001", "m0002", "done")]
    msg, bad = await b._exec_tool("snip", {"from_id": "m0009", "to_id": "m0002"})
    assert bad and "unknown id" in msg
    msg, bad = await b._exec_tool("snip", {"from_id": "m0002", "to_id": "m0001"})
    assert bad and "before" in msg
    msg, bad = await b._exec_tool("snip", {"from_id": "m0003", "to_id": "m0003"})
    assert bad and "live turn" in msg
    assert len(b._snips) == 1
    # a subagent cannot snip the lead's history
    msg, bad = await b._exec_tool("snip", {"from_id": "m0001", "to_id": "m0001"}, allowed={"read_file"})
    assert bad and "not available" in msg


async def test_snips_execute_only_under_pressure_whole_rounds_first(monkeypatch):
    b = _backend(NATIVE_TOOLS, n_ctx=1_000_000)
    await _turns(b, 4)
    n_before = len(b.messages)
    await b._exec_tool("snip", {"from_id": "m0002", "to_id": "m0003"})
    assert b._maybe_compact() == [] and len(b.messages) == n_before, "no pressure, nothing removed"
    # pressure: a tiny window
    b.n_ctx = 1024
    b._last_prompt_tokens = 0
    for m in b.messages:
        if m.get("role") == "tool":
            m["content"] = "x" * 3000  # fat results so the estimate crosses the line
    events = b._maybe_compact()
    kinds = " | ".join(str(e.data) for e in events)
    assert "snipped" in kinds
    ids = [i for _, i in b._user_ids()]
    assert ids == ["m0001", "m0004"], ids
    # whole rounds went: every tool_call still has its tool message
    calls = {tc["id"] for m in b.messages for tc in (m.get("tool_calls") or [])}
    results = {m.get("tool_call_id") for m in b.messages if m.get("role") == "tool"}
    assert calls == results
    assert b._snips == [] and b.messages[0]["role"] == "system"


async def test_overlapping_and_stale_ranges_are_handled():
    b = _backend(NATIVE_TOOLS, n_ctx=1_000_000)
    await _turns(b, 5)
    await b._exec_tool("snip", {"from_id": "m0001", "to_id": "m0002"})
    await b._exec_tool("snip", {"from_id": "m0002", "to_id": "m0003"})
    await b._exec_tool("snip", {"from_id": "m0004", "to_id": "m0004"})
    removed = b._execute_snips()
    assert removed > 0
    assert [i for _, i in b._user_ids()] == ["m0005"]
    # ids that no longer exist are skipped, not fatal
    b._snips.append(("m0001", "m0002", ""))
    assert b._execute_snips() == 0
