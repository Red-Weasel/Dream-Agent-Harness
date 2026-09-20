"""Dream fixes from the 2026-09-18/19 DeepSeek-V4.1 live run (DREAM-082): the size
estimate calibrated on the server's count, one-step compaction, reasoning kept on
the reply, a cut-off tool call retried in parts, and the rounds-left note."""
from __future__ import annotations

import json

import pytest

from dream.core.backends import openai_compat
from test_schema_deferral import _FakeClient, _backend, _sse, _text_round, _tool_round

pytestmark = pytest.mark.asyncio


def _usage_round(text="done", prompt_tokens=0, reasoning="", finish="stop", truncated=None):
    choice_end = {"delta": {}, "finish_reason": finish}
    if truncated:
        choice_end["truncated_tool_call"] = {"name": truncated}
    lines = []
    if reasoning:
        lines.append(_sse({"choices": [{"delta": {"reasoning_content": reasoning}, "finish_reason": None}]}))
    if text:
        lines.append(_sse({"choices": [{"delta": {"content": text}, "finish_reason": None}]}))
    lines.append(_sse({"choices": [choice_end]}))
    if prompt_tokens:
        lines.append(_sse({"choices": [], "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 5}}))
    lines.append("data: [DONE]")
    return lines


def _image(n=1):
    return [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}} for _ in range(n)]


async def test_images_no_longer_look_like_4096_tokens_once_the_server_has_counted():
    """Dream fix #23: a flat 4,096 per image made a real ~31K prompt look full and
    trimmed history every request. After one request the estimate is scaled by
    what the server actually counted."""
    b = _backend(n_ctx=40000)
    b.messages.append({"role": "user", "content": [*_image(8), {"type": "text", "text": "look"}]})
    b.messages.append({"role": "assistant", "content": "seen"})
    report = openai_compat.account(b.messages, b._request_tools(), 40000, 1000)
    raw = report.input_tokens
    assert raw > 8 * 4096                                        # the flat estimate
    b._sent_est = (raw, report.tools, openai_compat._est_tokens(b.messages))
    b._last_prompt_tokens = 3000                                 # what V4.1 really counted
    b._record_calibration(3000)
    assert b._calib < 0.2 and b._calib_msgs < 0.2
    counter, method = b._calibrated_counter()
    assert counter(b.messages) < raw * 0.2 and "calibrated" in method
    assert b._ctx_fill() < 5000                                  # not ~33K


async def test_code_heavy_text_is_scaled_up_not_down():
    b = _backend(n_ctx=40000)
    b._client = _FakeClient([_usage_round(prompt_tokens=9000)])
    [ev async for ev in b.ask("x = 1\n" * 400)]
    assert b._calib > 1.0 and b._ctx_fill() >= 9000


async def test_admission_compacts_in_one_big_step():
    """Dream fix #2: over the window, admission used to stub 'just enough' of the
    oldest history every request, changing the prompt's start each round."""
    b = _backend(n_ctx=8192)
    for k in range(30):
        b.messages.append({"role": "user", "content": f"question {k} " + "w" * 900 + f"\n\n[id:m{k + 1:04d}]"})
        b.messages.append({"role": "assistant", "content": "answer " + "v" * 900})
    b._msg_seq = 30
    before = openai_compat._est_tokens(b.messages)
    b._admit_request(b.messages, b._request_tools())
    after = openai_compat._est_tokens(b.messages)
    assert before > 8192 and after <= int(8192 * openai_compat._COMPACT_AT / 2) + 64


async def test_reasoning_is_kept_on_the_reply_and_dropped_for_earlier_tasks():
    """Dream fix #10: V4.1 renders reasoning back when tools are present, so the
    history must carry it or every round re-reads the reply. Earlier tasks'
    reasoning goes at the next compaction."""
    b = _backend(n_ctx=65536)
    b._client = _FakeClient([_usage_round(text="answer one", reasoning="thinking about one")])
    [ev async for ev in b.ask("first")]
    reply = next(m for m in reversed(b.messages) if m.get("role") == "assistant")
    assert reply["reasoning_content"] == "thinking about one"
    b._client = _FakeClient([_usage_round(text="answer two", reasoning="thinking about two")])
    [ev async for ev in b.ask("second")]
    assert b._client.payloads[0]["messages"][-2].get("reasoning_content") == "thinking about one"
    openai_compat._compact_messages(b.messages, 10**6)
    kept = [m.get("reasoning_content") for m in b.messages if m.get("role") == "assistant"]
    assert kept == [None, "thinking about two"]


async def test_reasoning_is_not_sent_to_other_providers():
    b = _backend(n_ctx=65536)
    b.provider.key = "openrouter"
    b._client = _FakeClient([_usage_round(text="answer", reasoning="private chain")])
    [ev async for ev in b.ask("first")]
    assert not any(m.get("reasoning_content") for m in b.messages)


async def test_a_cut_off_tool_call_is_retried_once_in_parts():
    """Dream fix #8/#14: a reply cut off at the output limit inside a write_file
    call ended the turn; the plain Continue attempted the same oversized call."""
    b = _backend(n_ctx=65536)
    b._client = _FakeClient([
        _usage_round(text="Writing the module.", finish="length", truncated="write_file"),
        _text_round("done in parts"),
    ])
    events = [ev async for ev in b.ask("build it")]
    systems = [e.data for e in events if e.kind == "system"]
    assert any("cut off" in s and "(write_file)" in s for s in systems)
    assert any("redo the cut-off write_file call in parts" in s for s in systems)
    retry = b._client.payloads[1]["messages"][-1]
    assert retry["name"] == "dream_recovery_instruction" and "append=true" in retry["content"]
    assert events[-1].data["subtype"] == "success"


async def test_a_second_cut_ends_the_turn_as_incomplete():
    b = _backend(n_ctx=65536)
    cut = _usage_round(text="Writing.", finish="length", truncated="write_file")
    b._client = _FakeClient([cut, cut])
    events = [ev async for ev in b.ask("build it")]
    assert len(b._client.payloads) == 2 and events[-1].data["subtype"] == "length"


async def test_the_model_is_told_when_rounds_run_low(monkeypatch):
    """Dream fix #24."""
    monkeypatch.setattr(openai_compat, "_MAX_TOOL_ROUNDS", 5)

    async def handler(args):
        return {"content": [{"type": "text", "text": "ok"}]}

    from test_compaction import _tool
    b = _backend([_tool("probe", handler)], n_ctx=65536)
    b._client = _FakeClient([_tool_round("probe", "{}")] * 3 + [_text_round()])
    [ev async for ev in b.ask("check")]
    notes = [m["content"] for m in b.messages if m.get("role") == "tool" and "tool rounds left" in m["content"]]
    assert notes and "3 tool rounds left" in notes[0]


async def test_write_file_append_adds_to_the_end(tmp_path, monkeypatch):
    from dream.tools import native
    monkeypatch.setattr(native, "_resolve", lambda raw: tmp_path / raw)
    await native.write_file.handler({"path": "big.js", "content": "part one\n"})
    out = await native.write_file.handler({"path": "big.js", "content": "part two\n", "append": True})
    assert (tmp_path / "big.js").read_text() == "part one\npart two\n"
    assert "Appended" in json.dumps(out)


async def test_a_looping_reply_is_named_and_asked_for_again():
    """The engine now stops a reply that repeats itself (finish_reason
    "repetition", after the live 'adversely' loop). Say what happened and ask
    once for a fresh answer, instead of 'unsupported_finish_reason'."""
    b = _backend(n_ctx=65536)
    b._client = _FakeClient([
        _usage_round(text="adversely " * 50, finish="repetition"),
        _text_round("here is the answer"),
    ])
    events = [ev async for ev in b.ask("summarise it")]
    systems = [e.data for e in events if e.kind == "system"]
    assert any("repeating itself" in s for s in systems)
    retry = b._client.payloads[1]["messages"][-1]
    assert retry["name"] == "dream_recovery_instruction" and "repeating the same words" in retry["content"]
    assert events[-1].data["subtype"] == "success"


async def test_a_second_loop_ends_the_turn_and_says_why():
    b = _backend(n_ctx=65536)
    loop = _usage_round(text="adversely " * 50, finish="repetition")
    b._client = _FakeClient([loop, loop])
    events = [ev async for ev in b.ask("summarise it")]
    errors = [e.data for e in events if e.kind == "error"]
    assert len(b._client.payloads) == 2
    assert any("kept repeating itself" in e for e in errors)
    assert events[-1].data["subtype"] == "repetition"


async def test_a_reply_that_spends_the_whole_budget_without_acting_is_asked_to_act():
    """Live 2026-09-20: 16,384 tokens over 40 minutes, no tool call, cut at the cap,
    ending "Now I'll build. Let me check the HTML controls before editing." Nothing ran,
    and the oversized reply then forced a compaction."""
    b = _backend(n_ctx=65536)
    b._client = _FakeClient([
        _usage_round(text="Analysis. " * 50, finish="length"),
        _text_round("done"),
    ])
    events = [ev async for ev in b.ask("build it")]
    systems = [e.data for e in events if e.kind == "system"]
    assert any("without making a single tool call" in s for s in systems)
    # Deliberately NOT an auto-retry: truncation must not silently repeat work
    # (test_harness_backend_quality::test_truncated_answer_is_failed_...).
    assert len(b._client.payloads) == 1
    assert events[-1].data["subtype"] == "length"


async def test_a_cut_off_tool_call_still_takes_priority_over_the_prose_guard():
    """A length cut inside a write_file must get the in-parts instruction, not the
    'you did not act' one -- it DID act, it just did not fit."""
    b = _backend(n_ctx=65536)
    b._client = _FakeClient([
        _usage_round(text="Writing.", finish="length", truncated="write_file"),
        _text_round("done in parts"),
    ])
    events = [ev async for ev in b.ask("build it")]
    systems = [e.data for e in events if e.kind == "system"]
    assert any("redo the cut-off write_file call in parts" in s for s in systems)
    assert not any("without making a single tool call" in s for s in systems)
