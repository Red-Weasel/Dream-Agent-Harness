"""Context management for the OpenAI-compat (local MachX) backend.

Before this the backend had none. `self.messages` grew without bound: a single
200k-char read_file result went into the history verbatim, the probed `n_ctx`
was used only to *display* a fill number, and every round asked for the full
32k output tokens no matter how little window was left. Once the window filled
the server either 400'd — and since the history never shrinks, every later turn
400'd identically, bricking the session until exit — or silently evicted the
system prompt.

So: clamp what goes INTO the history (the TUI still gets the full result),
compact in place when the fill crosses a fraction of the window, and never ask
for more output tokens than the window can still hold.
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import dream.config as config
from dream.core.backends import openai_compat
from dream.core.backends.openai_compat import OpenAICompatBackend


def _tool(name, handler):
    return SimpleNamespace(
        name=name, description=f"{name} tool",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
    )


def _backend(tools=(), n_ctx=None) -> OpenAICompatBackend:
    p = SimpleNamespace(
        key="machx", label="MachX", base_url="http://x/v1",
        multimodal=False, api_key=lambda: "n",
    )
    b = OpenAICompatBackend(
        provider=p, model="m", system_prompt="SYSTEM PROMPT",
        tools=list(tools), permission_cb=None,
    )
    b.n_ctx = n_ctx
    return b


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
    """Serves one script per request and keeps a SNAPSHOT of each payload — the
    live payload holds the backend's own message list, which keeps mutating."""

    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.payloads = []

    def stream(self, method, url, json=None):
        self.payloads.append(copy.deepcopy(json))
        i = min(len(self.payloads) - 1, len(self._rounds) - 1)
        return _FakeStream(self._rounds[i])


def _sse(obj) -> str:
    return "data: " + json.dumps(obj)


def _text_round(text="done"):
    return [
        _sse({"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]


def _tool_round(name="read_file", args="{}"):
    return [
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": name, "arguments": args}}
        ]}, "finish_reason": None}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]


def _stuff_history(b, rounds=12, size=4000):
    """A long session: `rounds` completed tool rounds of `size`-char results."""
    for i in range(rounds):
        b.messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{i}", "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}},
        ]})
        b.messages.append({"role": "tool", "tool_call_id": f"c{i}",
                           "content": f"RESULT{i} " + ("y" * size)})


def _pairs_balanced(messages) -> bool:
    want = sorted(tc["id"] for m in messages if m.get("role") == "assistant"
                  for tc in (m.get("tool_calls") or []))
    got = sorted(m["tool_call_id"] for m in messages if m.get("role") == "tool")
    return want == got


# --- (b) what goes into the history is clamped; the event stays whole ---------

async def test_huge_tool_result_is_clamped_in_history_but_whole_in_the_event():
    big = "x" * 200_000

    async def handler(args):
        return {"content": [{"type": "text", "text": big}]}

    b = _backend([_tool("read_file", handler)])
    b._client = _FakeClient([_tool_round(), _text_round()])
    events = [ev async for ev in b.ask("read it")]

    res = next(e.data for e in events if e.kind == "tool_result")
    assert res["content"] == big  # the TUI/renderer still sees everything

    hist = next(m for m in b.messages if m.get("role") == "tool")
    assert len(hist["content"]) < len(big) // 4  # the model re-reads a clamp
    assert "chars elided" in hist["content"]
    assert hist["content"].startswith("x" * 1000)  # head kept, tail marked


async def test_small_tool_results_are_untouched():
    async def handler(args):
        return {"content": [{"type": "text", "text": "pong"}]}

    b = _backend([_tool("ping", handler)])
    b._client = _FakeClient([_tool_round("ping"), _text_round()])
    [ev async for ev in b.ask("go")]
    assert next(m for m in b.messages if m.get("role") == "tool")["content"] == "pong"


# --- (c) compaction ----------------------------------------------------------

async def test_compaction_fires_and_keeps_system_last_user_and_pairing():
    b = _backend(n_ctx=8192)
    _stuff_history(b)  # ~48k chars ≈ 12k tokens, well over 0.75 * 8192
    b._client = _FakeClient([_text_round("ok")])
    events = [ev async for ev in b.ask("WHAT DID YOU FIND?")]

    notes = [e.data for e in events if e.kind == "system"]
    assert any("compacted context:" in n for n in notes)  # visible, never silent

    assert b.messages[0] == {"role": "system", "content": "SYSTEM PROMPT"}
    users = [m for m in b.messages if m.get("role") == "user"]
    assert users[-1]["content"].startswith("WHAT DID YOU FIND?")  # the live question survives
    assert users[-1]["content"].endswith("]")  # …with its [id:mNNNN] tag (snip)
    assert _pairs_balanced(b.messages)  # eliding must never orphan a tool_call

    stubbed = [m for m in b.messages
               if m.get("role") == "tool" and m["content"].startswith("[elided:")]
    assert stubbed and "read_file" in stubbed[0]["content"]
    # the newest rounds stay verbatim — that's the model's working state
    newest = next(m for m in b.messages if m.get("tool_call_id") == "c11")
    assert newest["content"].startswith("RESULT11 ")
    # ...and the oldest are the ones that got stubbed
    assert next(m for m in b.messages
                if m.get("tool_call_id") == "c0")["content"].startswith("[elided:")

    sent = b._client.payloads[0]["messages"]
    assert sum(len(m.get("content") or "") for m in sent) < 12 * 4000


async def test_no_compaction_while_the_window_is_roomy():
    b = _backend(n_ctx=8192)
    _stuff_history(b, rounds=2, size=1000)
    b._client = _FakeClient([_text_round("ok")])
    events = [ev async for ev in b.ask("hi")]
    assert not [e for e in events if e.kind == "system"]
    assert all(not (m.get("content") or "").startswith("[elided:") for m in b.messages)


async def test_compaction_threshold_is_a_knob(monkeypatch):
    async def run():
        b = _backend(n_ctx=8192)
        _stuff_history(b, rounds=7, size=4000)  # ~7k tokens: over 0.75, under 0.99
        b._client = _FakeClient([_text_round("ok")])
        return [ev async for ev in b.ask("hi")]

    assert any(e.kind == "system" for e in await run())  # default 0.75 → compacts
    monkeypatch.setattr(openai_compat, "_COMPACT_AT", 0.99)
    assert not [e for e in await run() if e.kind == "system"]


async def test_unknown_n_ctx_still_compacts_against_the_assumed_window(monkeypatch):
    # OpenAI/xAI never report n_ctx, and a /props probe can fail — the net must
    # still exist, sized by the assumed window.
    monkeypatch.setattr(openai_compat, "_ASSUMED_CTX", 8192)
    b = _backend()
    assert b.n_ctx is None
    _stuff_history(b)
    b._client = _FakeClient([_text_round("ok")])
    events = [ev async for ev in b.ask("go")]
    assert any("compacted context:" in e.data for e in events if e.kind == "system")


async def test_second_pass_elides_old_turn_bodies_when_stubs_are_not_enough():
    b = _backend(n_ctx=4096)
    # No tool results at all — only user/assistant prose, so pass 1 has nothing
    # to stub and pass 2 has to do the work.
    for i in range(10):
        b.messages.append({"role": "user", "content": f"Q{i} " + "q" * 3000})
        b.messages.append({"role": "assistant", "content": f"A{i} " + "a" * 3000})
    b._client = _FakeClient([_text_round("ok")])
    events = [ev async for ev in b.ask("LATEST QUESTION")]

    assert any("compacted context:" in e.data for e in events if e.kind == "system")
    assert b.messages[0]["content"] == "SYSTEM PROMPT"
    assert [m for m in b.messages if m.get("role") == "user"][-1]["content"].startswith("LATEST QUESTION")
    assert any((m.get("content") or "").startswith("[elided:") for m in b.messages)


async def test_compaction_is_idempotent_and_does_not_re_elide():
    b = _backend(n_ctx=8192)
    _stuff_history(b)
    b._client = _FakeClient([_text_round("ok"), _text_round("ok2")])
    [ev async for ev in b.ask("first")]
    stubs = [m["content"] for m in b.messages if (m.get("content") or "").startswith("[elided:")]
    events = [ev async for ev in b.ask("second")]
    again = [m["content"] for m in b.messages if (m.get("content") or "").startswith("[elided:")]
    assert again[:len(stubs)] == stubs  # already-stubbed content is left alone
    assert all("[elided: [elided:" not in c for c in again)
    assert _pairs_balanced(b.messages)
    assert events


# --- (d) max_tokens can never overrun the window -----------------------------

async def test_max_tokens_is_clamped_to_the_remaining_window():
    b = _backend(n_ctx=8192)
    _stuff_history(b, rounds=1, size=4000)  # ~1k tokens: under the compact line
    b._client = _FakeClient([_text_round("ok")])
    [ev async for ev in b.ask("hi")]
    asked = b._client.payloads[0]["max_tokens"]
    assert asked < config.MAX_OUTPUT_TOKENS
    assert 1024 <= asked <= 8192


async def test_max_tokens_never_drops_below_a_usable_floor():
    b = _backend(n_ctx=2048)
    _stuff_history(b, rounds=40, size=4000)
    b._client = _FakeClient([_text_round("ok")])
    [ev async for ev in b.ask("hi")]
    # Compaction gets the history back under a tiny window, so the arithmetic
    # need not bottom out — what must hold is that we never ask for less than a
    # usable reply, nor for more than the window can still hold.
    assert b._client.payloads[0]["max_tokens"] >= 1024


async def test_max_tokens_floor_holds_when_the_window_cannot_fit_the_turn():
    # A system prompt alone bigger than the window: the remaining-window
    # arithmetic goes negative and the 1024 floor is the only thing left.
    b = _backend(n_ctx=2048)
    b.messages[0]["content"] = "x" * 40_000
    b._client = _FakeClient([_text_round("ok")])
    [ev async for ev in b.ask("hi")]
    assert b._client.payloads[0]["max_tokens"] == 1024


async def test_unknown_n_ctx_keeps_the_configured_ceiling():
    b = _backend()
    b._client = _FakeClient([_text_round("ok")])
    [ev async for ev in b.ask("hi")]
    assert b._client.payloads[0]["max_tokens"] == config.MAX_OUTPUT_TOKENS


# --- (g) the keep-recent window must not become a floor ----------------------


async def test_recent_messages_are_not_a_floor_compaction_cannot_get_under():
    """The last few rounds are kept verbatim only while they FIT. A fixed keep
    count made a small window unreachable: four capped tool results alone are
    ~24k tokens, more than a 16k window, so compaction would 'succeed' and still
    leave the request over the wall."""
    b = _backend(n_ctx=16384)
    # Four recent rounds at the tool-result cap: 4 x 24k chars = ~24k tokens.
    _stuff_history(b, rounds=4, size=openai_compat._TOOL_RESULT_CAP)
    b._client = _FakeClient([_text_round("ok")])
    events = [ev async for ev in b.ask("hi")]
    assert openai_compat._est_tokens(b.messages) <= 16384
    assert _pairs_balanced(b.messages)
    assert any(e.kind == "system" and "compacted context" in str(e.data)
               for e in events)


async def test_irreducible_history_over_the_window_warns_instead_of_going_silent():
    """When nothing is left to elide — the system prompt alone is bigger than
    the window — the request still goes out, but it must SAY so. A turn that
    dies at the context wall must never die mysteriously."""
    b = _backend(n_ctx=2048)
    b.messages[0]["content"] = "x" * 40_000  # ~10k tokens of irreducible prompt
    b._client = _FakeClient([_text_round("ok")])
    events = [ev async for ev in b.ask("hi")]
    warned = [e for e in events if e.kind == "system" and "still exceeds" in str(e.data)]
    assert warned, "an over-window send must be announced, not silent"
    assert "2048" in str(warned[0].data)


async def test_no_warning_when_compaction_brings_it_back_under():
    b = _backend(n_ctx=32768)
    _stuff_history(b, rounds=30, size=4000)
    b._client = _FakeClient([_text_round("ok")])
    events = [ev async for ev in b.ask("hi")]
    assert not [e for e in events if e.kind == "system" and "still exceeds" in str(e.data)]
    assert openai_compat._est_tokens(b.messages) <= 32768


# --- (h) tool_call ARGUMENTS are capped and elidable too ---------------------


async def test_huge_tool_call_arguments_are_capped_on_the_way_in():
    """A write_file call carries the whole file body in its ARGUMENTS. Results
    were capped from the start; arguments were not, so a session of big writes
    grew monotonically and compaction could not touch it.

    The window is deliberately roomy so compaction never runs: this pins the
    ENTRY cap alone. The kept head of the long value is the fingerprint that
    separates them — only the entry cap keeps a head, elision drops it — so
    asserting it is what makes this test fail if the cap is removed and elision
    picks up the slack. Short fields (the path) survive both."""
    big = "z" * 200_000

    async def handler(args):
        assert args["content"] == big  # the CALL still runs with the full body
        return {"content": [{"type": "text", "text": "written"}]}

    b = _backend([_tool("write_file", handler)], n_ctx=65536)
    b._client = _FakeClient([
        _tool_round("write_file", json.dumps({"path": "x.txt", "content": big})),
        _text_round(),
    ])
    events = [ev async for ev in b.ask("write it")]
    assert not [e for e in events if e.kind == "system"]  # no compaction ran

    hist = next(m for m in b.messages if m.get("tool_calls"))
    stored = hist["tool_calls"][0]["function"]["arguments"]
    assert len(stored) < len(big) // 10
    cut = json.loads(stored)  # still valid JSON — a strict server may parse it
    assert cut["_elided_chars"] > 200_000 - 1
    assert cut["path"] == "x.txt"  # which file, even after the cut
    assert cut["content"].startswith("z" * 400) and cut["content"].endswith("chars elided]")  # entry cap, not elision


async def test_a_session_of_big_writes_stays_inside_the_window():
    """The gate's scenario: 8 write_file turns with 24k-char content arguments
    at the repo's real default window. This used to grow to ~48k tokens (5.9x
    the window) with ZERO compaction events, because nothing could shrink an
    arguments blob."""
    b = _backend(n_ctx=8192)
    peak = 0
    for t in range(8):
        b.messages.append({"role": "user", "content": f"turn {t}"})
        b._maybe_compact()
        peak = max(peak, openai_compat._est_tokens(b.messages))
        b.messages.append({"role": "assistant", "content": None, "tool_calls": [{
            "id": f"w{t}", "type": "function",
            "function": {"name": "write_file",
                         "arguments": json.dumps({"content": "z" * 24_000})},
        }]})
        b.messages.append({"role": "tool", "tool_call_id": f"w{t}",
                           "content": "written"})
    assert peak <= 8192, f"history reached {peak} tokens against an 8192 window"
    assert _pairs_balanced(b.messages)


async def test_argument_elision_keeps_the_newest_call_whole():
    # A window with room to spare: the newest call is the model's working state
    # and survives. (At a window too small to hold even one, nothing is spared —
    # see test_a_session_of_big_writes_stays_inside_the_window.)
    b = _backend(n_ctx=32768)
    for t in range(6):
        b.messages.append({"role": "assistant", "content": None, "tool_calls": [{
            "id": f"w{t}", "type": "function",
            "function": {"name": "write_file",
                         "arguments": json.dumps({"content": "z" * 20_000})},
        }]})
        b.messages.append({"role": "tool", "tool_call_id": f"w{t}", "content": "ok"})
    b.messages.append({"role": "user", "content": "and now?"})
    b._maybe_compact()
    newest = [m for m in b.messages if m.get("tool_calls")][-1]
    oldest = [m for m in b.messages if m.get("tool_calls")][0]
    assert "_elided_chars" in oldest["tool_calls"][0]["function"]["arguments"]
    assert len(newest["tool_calls"][0]["function"]["arguments"]) > 19_000


async def test_a_garbled_tool_name_cannot_grow_the_history():
    """The last field neither the result cap nor elision could shrink. A local
    model that emits a multi-KB name would otherwise grow the history forever."""
    b = _backend(n_ctx=8192)
    b._client = _FakeClient([_tool_round("w" * 4000, "{}"), _text_round()])
    [ev async for ev in b.ask("go")]
    stored = next(m for m in b.messages if m.get("tool_calls"))
    assert len(stored["tool_calls"][0]["function"]["name"]) == 128


async def test_an_elided_user_message_keeps_its_id_so_it_can_still_be_snipped():
    """Gate 7 observation: stubbing a user message dropped its [id:mNNNN] tag, so
    a stubbed exchange could never be named to `snip` afterwards."""
    b = _backend(n_ctx=4096)
    for i in range(6):
        b._client = _FakeClient([_text_round(f"A{i} " + "a" * 3000)])
        [ev async for ev in b.ask(f"Q{i} " + "q" * 3000)]
    users = [m for m in b.messages if m.get("role") == "user"]
    stubbed = [m for m in users if m["content"].startswith("[elided:")]
    assert stubbed, "the setup must actually elide a user message"
    assert len(b._user_ids()) == len(users)
    ids = [i for _, i in b._user_ids()]
    assert ids == [f"m{n:04d}" for n in range(1, 7)]
    text, bad = b._register_snip({"from_id": "m0001", "to_id": "m0002"})
    assert not bad, text



def test_elided_call_arguments_keep_the_path():
    """Dream fix #13: compaction used to replace a write_file call's arguments
    with {"_elided_chars": N}, so the model no longer knew which files it had
    written and wrote them again. Short fields survive; long values are cut."""
    msg = {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
        "name": "write_file", "arguments": json.dumps({"path": "falcon9/textures.js", "content": "x" * 20000,
                                                       "append": False})}}]}
    assert openai_compat._elide_args(msg) == 1
    cut = json.loads(msg["tool_calls"][0]["function"]["arguments"])
    assert cut["path"] == "falcon9/textures.js" and cut["append"] is False
    assert cut["content"] == "[20,000 chars elided]" and cut["_elided_chars"] > 20000
