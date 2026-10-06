"""Live code streaming (owner request, DREAM-084): MachX streams a tool call's text
while the model writes it, and the chat shows it forming until the call's card
replaces it. Display only — it never becomes reply text, history or a replayed event."""
import pytest
from playwright.async_api import expect

from dream.core.backends.base import Event
from dream.gui.conversation import Conversation
from test_desktop_chat import chat  # noqa: F401
from test_schema_deferral import _FakeClient, _backend, _sse

pytestmark = pytest.mark.asyncio


def _preview_round(preview_parts, text="Here is the file."):
    lines = [_sse({"choices": [{"delta": {"content": text}, "finish_reason": None}]})]
    for part in preview_parts:
        lines.append(_sse({"choices": [{"delta": {"tool_call_preview": part}, "finish_reason": None}]}))
    lines.append(_sse({"choices": [{"index": 0, "delta": {"tool_calls": [
        {"index": 0, "id": "call_1", "type": "function",
         "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}}]}, "finish_reason": None}]}))
    lines.append(_sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}))
    lines.append("data: [DONE]")
    return lines


async def test_the_preview_is_shown_but_never_becomes_the_reply():
    b = _backend(n_ctx=65536)
    b.provider.key = "machx"
    b._client = _FakeClient([_preview_round(["<｜DSML｜ calls>read_file", '{"path": "a.txt"}'])])
    events = [ev async for ev in b.ask("read it")]
    previews = [e.data for e in events if e.kind == "tool_preview"]
    assert "".join(previews).startswith("<｜DSML｜ calls>read_file")
    assert b._client.payloads[0]["stream_tool_preview"] is True
    # The preview is display only: not in the reply text, not in the history.
    assert all("DSML" not in str(m.get("content") or "") for m in b.messages)
    reply = next(m for m in reversed(b.messages) if m.get("role") == "assistant")
    assert reply["content"].strip() == "Here is the file."


async def test_other_providers_are_never_sent_the_flag():
    b = _backend(n_ctx=65536)
    b.provider.key = "openrouter"
    b._client = _FakeClient([_preview_round([])])
    [ev async for ev in b.ask("read it")]
    assert "stream_tool_preview" not in b._client.payloads[0]


async def test_a_preview_is_not_kept_for_replay():
    """A half-written call must not come back on reload — the finished card does."""
    c = Conversation()
    c.append(Event("tool_preview", "write_file {\"path\""))
    c.append(Event("tool_use", {"id": "1", "name": "write_file", "input": {}}))
    assert [e["kind"] for e in c.snapshot()["events"]] == ["tool_use"]


async def test_the_chat_shows_the_call_forming_then_replaces_it_with_its_card(chat):
    server, page, prompts, _ = chat
    server.bus.publish(Event("text_delta", "Writing the module."))
    server.bus.publish(Event("tool_preview", 'write_file {"path": "main.js",'))
    server.bus.publish(Event("tool_preview", ' "content": "export const stages = 2;"}'))
    await expect(page.locator("#stream .tool-preview")).to_contain_text("export const stages = 2;")
    server.bus.publish(Event("tool_use", {"id": "w1", "name": "write_file",
                                          "input": {"path": "main.js", "content": "export const stages = 2;"}}))
    await expect(page.locator("#stream .tool-preview")).to_have_count(0)
    await expect(page.locator("#stream .tool")).to_have_count(1)
    # A turn that fails mid-call must not leave the half-written text on screen.
    server.bus.publish(Event("tool_preview", 'read_file {"path"'))
    await expect(page.locator("#stream .tool-preview")).to_have_count(1)
    server.bus.publish(Event("error", "The model stopped responding."))
    await expect(page.locator("#stream .tool-preview")).to_have_count(0)
    assert prompts == []


async def test_a_reply_that_is_one_long_tool_call_reports_its_real_speeds(monkeypatch):
    """The owner's footer, 2026-10-04: "prefill 13 tok/s · decode 20721.0 tok/s" while the engine's own log said 115
    and 11.7. The first token was taken at the first content, reasoning or structured-call delta; a reply that is one
    tool call streams only tool_call_preview until the finished call arrives at the very end, so its 26.9 s of writing
    were counted as prompt reading and its decode as an instant. The preview's first piece is the first token."""
    import time as real_time

    from dream.core.backends import openai_compat

    class Clock:
        t = 100.0

        def monotonic(self):
            return self.t

        def __getattr__(self, name):
            return getattr(real_time, name)

    clock = Clock()
    monkeypatch.setattr(openai_compat, "time", clock)

    def lines():
        clock.t = 103.4                                   # the prompt is read: the call's first token arrives
        yield _sse({"choices": [{"delta": {"tool_call_preview": "<tool_call><function=read_file>"}, "finish_reason": None}]})
        clock.t = 130.3                                   # 26.9 s of the call being written
        yield _sse({"choices": [{"delta": {"tool_call_preview": "</function></tool_call>"}, "finish_reason": None}]})
        yield _sse({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "type": "function",
             "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}}]}, "finish_reason": None}]})
        yield _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}],
                    "usage": {"prompt_tokens": 66234, "completion_tokens": 315, "prompt_tokens_details": {"cached_tokens": 65839}}})
        yield "data: [DONE]"

    b = _backend(n_ctx=250000)
    b.provider.key = "machx"
    b._client = _FakeClient([lines()])
    stats = None
    agen = b.ask("measure it")
    async for ev in agen:
        if ev.kind == "stats":
            stats = ev.data
            break
    await agen.aclose()
    assert stats is not None and stats["exact"] is False, stats
    assert abs(stats["pp_ms"] - 3400) < 1 and abs(stats["gen_ms"] - 26900) < 1, stats      # 3.4 s reading, 26.9 s writing
    assert abs(stats["ttft_ms"] - 3400) < 1 and stats["gen_n"] == 315, stats
    fresh = stats["prompt_tokens"] - stats["cached"]
    assert round(fresh * 1000 / stats["pp_ms"]) == 116 and round(stats["gen_n"] * 1000 / stats["gen_ms"], 1) == 11.7   # what the footer shows
