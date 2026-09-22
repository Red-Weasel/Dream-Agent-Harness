"""A local server matches on the prompt's head (system message + tool schemas).
When that head changes mid-session the whole conversation is re-read — live
2026-09-21, 33,077 tokens in 143 s — and nothing said what had changed."""
import pytest

from test_schema_deferral import _FakeClient, _backend, _text_round

pytestmark = pytest.mark.asyncio


async def test_a_steady_head_says_nothing():
    b = _backend(n_ctx=65536)
    for _ in range(3):
        b._client = _FakeClient([_text_round("ok")])
        events = [ev async for ev in b.ask("hello")]
        assert not [e for e in events if e.kind == "system" and "head changed" in str(e.data)]


async def test_a_changed_system_message_is_named():
    b = _backend(n_ctx=65536)
    b._client = _FakeClient([_text_round("ok")])
    [ev async for ev in b.ask("first")]
    b.messages[0]["content"] = "SYSTEM PROMPT with an extra open task"   # what live state does
    b._client = _FakeClient([_text_round("ok")])
    events = [ev async for ev in b.ask("second")]
    said = [str(e.data) for e in events if e.kind == "system" and "head changed" in str(e.data)]
    assert said and "the system message" in said[0], said


async def test_other_providers_are_not_fingerprinted():
    b = _backend(n_ctx=65536)
    b.provider.key = "openrouter"
    b._client = _FakeClient([_text_round("ok")])
    [ev async for ev in b.ask("first")]
    b.messages[0]["content"] = "changed"
    b._client = _FakeClient([_text_round("ok")])
    events = [ev async for ev in b.ask("second")]
    assert not [e for e in events if e.kind == "system" and "head changed" in str(e.data)]
