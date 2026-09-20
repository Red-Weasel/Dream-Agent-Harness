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
