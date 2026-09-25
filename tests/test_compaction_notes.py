"""Phase 10: compaction that remembers. What compaction elides is saved as a
working note first; the stub names the note; read_notes brings it back; snips
run before the blind stubbing; a subagent's scratch history saves nothing."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from dream import config
from dream.memory.store import MemoryStore
from dream.memory.working import WorkingMemory
from dream.tools import context as tool_context
from dream.tools.context import ToolContext, set_context
from dream.tools.notes import read_notes

sys.path.insert(0, str(Path(__file__).parent))
from test_compaction import _FakeClient, _backend, _pairs_balanced, _text_round  # noqa: E402
from dream.core.backends import openai_compat  # noqa: E402

pytestmark = pytest.mark.asyncio


@pytest.fixture
def working(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    store = MemoryStore(tmp_path / "w.db")
    store.start_session("s")
    w = WorkingMemory(store, "s")
    set_context(ToolContext(store=store, working=w, browser=None,  # type: ignore[arg-type]
                            session_id="s", workspace=tmp_path, emit=None))
    yield w
    tool_context._CTX = None
    store.close()


def _stuff(b, rounds=20, size=4000):
    """`rounds` tool rounds, each result carrying its own fact."""
    words = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
             "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
             "seventeen", "eighteen", "nineteen"]
    for i in range(rounds):
        b.messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]})
        b.messages.append({"role": "tool", "tool_call_id": f"c{i}",
                           "content": f"RESULT{i}: the round-{words[i]} fact is PINEAPPLE-{i:02d}. " + ("y" * size)})


def _t(res):
    return res["content"][0]["text"]


async def test_what_compaction_removes_is_one_read_notes_away(working):
    b = _backend(n_ctx=8192)
    _stuff(b)  # 20 rounds ≈ 20k tokens, far over 0.75 * 8192
    b._client = _FakeClient([_text_round("ok")])
    t0 = time.monotonic()
    events = [ev async for ev in b.ask("what was the round-two fact?")]
    assert time.monotonic() - t0 < 2.0, "compaction stays bounded"
    assert any("compacted context:" in str(e.data) for e in events if e.kind == "system")
    assert _pairs_balanced(b.messages)

    # the round-2 result is gone from history, and its stub says where it went
    stub = next(m for m in b.messages if m.get("tool_call_id") == "c1")["content"]
    assert stub.startswith("[elided: read_file result") and "PINEAPPLE-01" not in stub
    assert "— note #" in stub and len(stub) < 60, stub  # the stub is permanent history: keep it small
    note_id = int(stub.split("note #")[1].rstrip("]"))

    # ... and the note has it, tagged with the turn and what it was
    hit = _t(await read_notes.handler({"query": "#%d" % note_id}))
    assert "PINEAPPLE-01" in hit and "[elided read_file result · turn m0001]" in hit
    # the tag names the turn the CONTENT came from, not the turn compaction ran in
    assert "turn m0001]" in next(n for n in working.notes(False) if n["id"] == note_id)["note"]
    hit = _t(await read_notes.handler({"query": "round-one fact"}))
    assert "PINEAPPLE-01" in hit
    # bounded: a 4,000-char body becomes a head, an ellipsis, and a tail
    note = next(n for n in working.notes(False) if n["id"] == note_id)["note"]
    assert 800 < len(note) <= 1000 and "\n…\n" in note

    # the plain listing does not drown consolidation in elided bodies
    plain = _t(await read_notes.handler({}))
    assert "PINEAPPLE" not in plain and "elided item(s) from compaction are saved too" in plain
    assert "recovery, not facts to promote" in plain
    # the model's own note still lists
    working.note("my own note")
    plain = _t(await read_notes.handler({}))
    assert "• my own note" in plain


async def test_gate10_observations_turn_tag_note_bound_reasons_and_search(working, monkeypatch):
    """Gate 10 observations 1, 3, 4, 5, 7, 8: the turn tag, the whole-note bound,
    an honest failure reason, LIKE wildcards, a bad limit, an empty body."""
    b = _backend(n_ctx=8192)
    save = b._note_elided()
    # 3: a pathological tool name cannot push the note past its ceiling
    nid, why = save("x" * 128 + " result", "y" * 4000, "m0007")
    note = next(n for n in working.notes(False) if n["id"] == nid)["note"]
    assert len(note) <= b._NOTE_CHARS and why == "" and "turn m0007" in note
    # 8: a body with nothing in it spends no slot and says so
    nid, why = save("grep result", "   \n  ", "m0007")
    assert nid is None and why == "nothing to save"
    # 4: a store that cannot be written says that, not "cap"
    class Broken:
        def note(self, text):
            raise RuntimeError("db is closed")
    b2 = _backend(n_ctx=8192)
    import dream.tools.context as tc
    real = tc._CTX
    tc._CTX = type(real)(store=real.store, working=Broken(), browser=None,
                         session_id="s", workspace=real.workspace, emit=None)
    nid, why = b2._note_elided()("grep result", "z" * 900, "m0001")
    tc._CTX = real
    assert nid is None and "could not be written" in why and "RuntimeError" in why
    # 5: a query's % and _ are characters, not wildcards
    working.note("[elided grep result · turn m0002] a 50% discount")
    working.note("[elided grep result · turn m0002] plain text")
    assert len(working.search("%")) == 1 and "50%" in working.search("%")[0]["note"]
    assert working.search("_") == []
    # 7: a bad limit is a tool error, not a traceback
    res = await read_notes.handler({"query": "a", "limit": "abc"})
    assert res.get("is_error") and "limit must be a number" in _t(res)


@pytest.mark.asyncio
async def test_the_note_cap_bounds_a_compaction_and_the_stub_says_so(working, monkeypatch):
    monkeypatch.setattr(openai_compat.OpenAICompatBackend, "_NOTE_CAP", 5)
    b = _backend(n_ctx=8192)
    _stuff(b)
    b._client = _FakeClient([_text_round("ok")])
    [ev async for ev in b.ask("go")]
    stubs = [m["content"] for m in b.messages if str(m.get("content") or "").startswith("[elided:")]
    saved = [s for s in stubs if "— note #" in s]
    unsaved = [s for s in stubs if "not saved (note cap" in s]
    assert len(saved) == 5 and unsaved, (len(saved), len(unsaved))
    assert all("not saved (note cap for this compaction)" in s for s in unsaved)
    assert len([n for n in working.notes(False) if n["note"].startswith("[elided ")]) == 5


async def test_snips_run_before_the_blind_stubbing_and_a_bare_backend_saves_nothing(working, monkeypatch):
    order: list[str] = []
    b = _backend(n_ctx=8192)
    real_snips, real_compact = b._execute_snips, openai_compat._compact_messages

    def snips():
        order.append("snips")
        return real_snips()

    def compact(messages, target, on_elide=None, **measure):
        order.append("compact")
        return real_compact(messages, target, on_elide, **measure)

    monkeypatch.setattr(b, "_execute_snips", snips)
    monkeypatch.setattr(openai_compat, "_compact_messages", compact)
    _stuff(b)
    b._client = _FakeClient([_text_round("ok")])
    [ev async for ev in b.ask("go")]
    assert order[:2] == ["snips", "compact"]

    # with no working memory in the context, the stub is the old plain one
    tool_context._CTX = None
    b2 = _backend(n_ctx=8192)
    _stuff(b2)
    b2._client = _FakeClient([_text_round("ok")])
    [ev async for ev in b2.ask("go")]
    stub = next(m for m in b2.messages if m.get("tool_call_id") == "c1")["content"]
    assert stub.startswith("[elided: read_file result") and "note" not in stub and stub.endswith("]")


async def test_a_subagent_loop_compacts_without_saving_notes(working, monkeypatch):
    from types import SimpleNamespace

    calls: list[dict] = []
    real = openai_compat._compact_messages

    def compact(messages, target, on_elide=None):
        calls.append({"on_elide": on_elide, "n": len(messages)})
        return real(messages, target, on_elide)

    monkeypatch.setattr(openai_compat, "_compact_messages", compact)
    from test_local_subagents import _FakeClient as _SubClient, _sub_final  # noqa: E402

    b = _backend(n_ctx=8192)
    b._client = _SubClient(post_scripts=[_sub_final("done")])  # a subagent posts, non-streaming
    spec = SimpleNamespace(name="x", description="d", prompt="p", tool_names=())
    text, failed = await b._subagent_loop(spec, "x", "q " * 20000)  # ~10k tokens: over the line
    assert not failed and calls and all(c["on_elide"] is None for c in calls)
    assert not [n for n in working.notes(False) if n["note"].startswith("[elided ")]
