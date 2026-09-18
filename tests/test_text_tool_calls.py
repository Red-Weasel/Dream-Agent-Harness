"""DeepSeek-V4 on MachX writes its tool calls as Claude-style XML in the message
body — MachX has no tool-call parser, so the structured `tool_calls` field is
never populated and the turn ended after the first call (openai_compat treats an
empty `tool_calls` as "the model is done talking").

Verified against the live server 2026-08-03: a /v1/chat/completions request with
a `tools` array came back finish_reason="stop" and content

    <tool_calls>
    <invoke name="list_dir">
    <parameter name="path" string="true">/tmp</parameter>
    </invoke>
    </tool_calls>

So the backend has to recover calls from the text when the server gives it none.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from dream.core.backends.openai_compat import (
    OpenAICompatBackend,
    _parse_text_tool_calls,
)


def _tool(name: str, fn) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description=f"{name} tool",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        handler=fn,
    )


def _backend(tools=()) -> OpenAICompatBackend:
    p = SimpleNamespace(
        key="machx", label="MachX", base_url="http://x/v1",
        multimodal=False, api_key=lambda: "n",
    )
    return OpenAICompatBackend(
        provider=p, model="m", system_prompt="s", tools=list(tools),
        permission_cb=None,
    )


class _FakeResponse:
    def __init__(self, lines):
        self.status_code = 200
        self._lines = lines

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln

    async def aread(self):
        return b""


class _FakeStream:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return _FakeResponse(self._lines)

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    """Serves one scripted SSE stream per round, so a recovered tool call can be
    followed by a real answer."""

    def __init__(self, rounds):
        self._rounds = list(rounds)

    def stream(self, method, url, json=None):
        return _FakeStream(self._rounds.pop(0) if self._rounds else ["data: [DONE]"])


def _sse(obj) -> str:
    return "data: " + json.dumps(obj)


def _text_round(text: str) -> list[str]:
    return [
        _sse({"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]


DEEPSEEK_BLOCK = (
    '<tool_calls>\n<invoke name="list_dir">\n'
    '<parameter name="path" string="true">/tmp</parameter>\n'
    "</invoke>\n</tool_calls>"
)


# --- the parser itself ------------------------------------------------------

def test_parses_a_deepseek_style_call_out_of_the_body():
    text, calls = _parse_text_tool_calls(DEEPSEEK_BLOCK)
    assert text == ""
    assert len(calls) == 1
    assert calls[0]["name"] == "list_dir"
    assert json.loads(calls[0]["args"]) == {"path": "/tmp"}


def test_keeps_the_prose_and_drops_only_the_call_block():
    body = f"Let me look at the workspace first.\n\n{DEEPSEEK_BLOCK}"
    text, calls = _parse_text_tool_calls(body)
    assert text == "Let me look at the workspace first."
    assert len(calls) == 1


def test_parses_several_invokes_in_one_block():
    body = (
        '<tool_calls>\n'
        '<invoke name="a"><parameter name="path">/x</parameter></invoke>\n'
        '<invoke name="b"><parameter name="path">/y</parameter></invoke>\n'
        "</tool_calls>"
    )
    _, calls = _parse_text_tool_calls(body)
    assert [c["name"] for c in calls] == ["a", "b"]
    assert json.loads(calls[1]["args"]) == {"path": "/y"}


def test_plain_prose_yields_no_calls():
    text, calls = _parse_text_tool_calls("No tools needed, the answer is 4.")
    assert calls == []
    assert text == "No tools needed, the answer is 4."


def test_a_lone_invoke_without_the_wrapper_still_parses():
    # Observed shape varies; the wrapper tag is not always emitted.
    _, calls = _parse_text_tool_calls(
        '<invoke name="list_dir"><parameter name="path">/tmp</parameter></invoke>'
    )
    assert len(calls) == 1
    assert calls[0]["name"] == "list_dir"


def test_a_truncated_block_is_left_as_prose():
    body = '<tool_calls>\n<invoke name="list_dir">\n<parameter name="path">/tm'
    text, calls = _parse_text_tool_calls(body)
    assert calls == []
    assert text == body


# --- the loop actually runs the recovered call ------------------------------

async def test_a_text_only_tool_call_still_runs_the_tool():
    seen: list[dict] = []

    async def handler(args):
        seen.append(args)
        return {"content": [{"type": "text", "text": "a.txt\nb.txt"}]}

    b = _backend([_tool("list_dir", handler)])
    b._client = _FakeClient([
        _text_round(f"Looking now.\n\n{DEEPSEEK_BLOCK}"),
        _text_round("There are two files."),
    ])

    kinds = []
    async for ev in b.ask("what's in /tmp?"):
        kinds.append((ev.kind, ev.data))

    assert seen == [{"path": "/tmp"}], "the tool must actually have run"
    tool_uses = [d for k, d in kinds if k == "tool_use"]
    assert len(tool_uses) == 1
    assert tool_uses[0]["name"] == "list_dir"
    # ...and the turn continued into a second round instead of stopping.
    assert any(k == "assistant_done" and d == "There are two files." for k, d in kinds)


async def test_an_unknown_recovered_tool_errors_instead_of_ending_the_turn():
    """An unregistered recovered tool returns an error and permits recovery."""
    async def handler(args):
        return {"content": [{"type": "text", "text": "ok"}]}

    b = _backend([_tool("list_dir", handler)])
    b._client = _FakeClient([
        _text_round('<invoke name="unregistered_fixture_tool">'
                    '<parameter name="path" string="true">/x.png</parameter></invoke>'),
        _text_round("That tool is unavailable; reporting the failure."),
    ])

    kinds = []
    async for ev in b.ask("look at it"):
        kinds.append((ev.kind, ev.data))

    results = [d for k, d in kinds if k == "tool_result"]
    assert len(results) == 1
    assert results[0]["is_error"] is True
    assert "unknown tool" in results[0]["content"]
    # ...and the model got a second round to react, rather than the turn ending.
    assert any(k == "assistant_done"
               and d == "That tool is unavailable; reporting the failure."
               for k, d in kinds)


class _FakePost:
    """Non-streaming responses for the subagent loop, one per round."""

    def __init__(self, rounds):
        self._rounds = list(rounds)

    async def __call__(self, payload):
        body = self._rounds.pop(0) if self._rounds else {
            "choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]
        }
        return SimpleNamespace(json=lambda: body)


def _msg(content, finish="stop") -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content},
                         "finish_reason": finish}]}


async def test_a_subagent_recovers_a_text_tool_call_too():
    """The subagent loop is non-streaming and read `tool_calls` the same way, so
    a dispatched researcher on DeepSeek handed the lead its raw XML as if it were
    the answer."""
    seen = []

    async def handler(args):
        seen.append(args)
        return {"content": [{"type": "text", "text": "a.txt"}]}

    b = _backend([_tool("list_dir", handler)])
    b._subagents = {
        "explorer": SimpleNamespace(prompt="explore", tool_names=("list_dir",))
    }
    b._post_with_retry = _FakePost([
        _msg(DEEPSEEK_BLOCK),
        _msg("There is one file, a.txt."),
    ])

    text, is_error = await b._run_subagent("explorer", "what's in /tmp?")

    assert seen == [{"path": "/tmp"}], "the subagent's tool must actually have run"
    assert is_error is False
    assert text == "There is one file, a.txt."
    assert "<invoke" not in text, "raw XML must never come back as the answer"


async def test_a_call_cut_off_mid_write_says_so_instead_of_stopping_silently():
    """A big write_file can hit the token ceiling before `</invoke>` arrives. The
    parser is right to refuse a half-call — but the turn then ended with no
    explanation, which is indistinguishable from the original bug."""
    b = _backend([_tool("write_file", lambda a: None)])
    cut = ('<tool_calls>\n<invoke name="write_file">\n'
           '<parameter name="path" string="true">/tmp/site/index.html</parameter>\n'
           '<parameter name="content" string="true"><!DOCTYPE html><html><body>')
    b._client = _FakeClient([[
        _sse({"choices": [{"delta": {"content": cut}, "finish_reason": "length"}]}),
        "data: [DONE]",
    ]])

    kinds = []
    async for ev in b.ask("build the site"):
        kinds.append((ev.kind, ev.data))

    systems = [d for k, d in kinds if k == "system"]
    assert systems, "a truncated tool call must be reported, not swallowed"
    assert "truncat" in systems[0].lower() or "cut off" in systems[0].lower()
    result = [d for k, d in kinds if k == "result"][0]
    assert result["subtype"] == "length"


async def test_the_recovered_call_is_recorded_as_a_real_tool_call_pair():
    async def handler(args):
        return {"content": [{"type": "text", "text": "ok"}]}

    b = _backend([_tool("list_dir", handler)])
    b._client = _FakeClient([_text_round(DEEPSEEK_BLOCK), _text_round("done")])
    async for _ in b.ask("go"):
        pass

    assistant = [m for m in b.messages if m.get("role") == "assistant" and m.get("tool_calls")]
    assert len(assistant) == 1
    tc = assistant[0]["tool_calls"][0]
    assert tc["function"]["name"] == "list_dir"
    # The XML must not survive into history as content — it would teach the model
    # the format is fine and double-count the call.
    assert "<invoke" not in (assistant[0].get("content") or "")
    ids = {tc["id"]} & {m.get("tool_call_id") for m in b.messages if m.get("role") == "tool"}
    assert ids, "every recovered call needs its matching tool result"
