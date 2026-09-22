"""A turn that keeps looking without changing anything gets told so.

The loop guard catches a call REPEATED with the same arguments and the same
result. It cannot see a session whose every call differs and yet changes nothing:
live 2026-09-20, one ran 114 tool calls over four hours on a single visual detail
and edited two files."""
import pytest

from test_schema_deferral import _FakeClient, _backend, _text_round, _tool_round

pytestmark = pytest.mark.asyncio


def _looking_backend(tool_name, rounds):
    """A backend whose tool always succeeds, driven for `rounds` tool rounds."""
    ran = []

    async def handler(args):
        ran.append(args)
        return {"content": "looked"}

    from test_schema_deferral import _tool
    tool = _tool(tool_name)
    tool.handler = handler
    b = _backend([tool])
    # Every call DIFFERS -- that is the case the loop guard cannot see. Repeating one
    # identical call is caught by the existing guard after two executions.
    b._client = _FakeClient([_tool_round(tool_name, '{"path": "f%d.txt"}' % i)
                             for i in range(rounds)] + [_text_round("done")])
    return b, ran


def _notes(b):
    return [m["content"] for m in b.messages
            if m.get("role") == "tool" and isinstance(m.get("content"), str)
            and "since anything changed" in m["content"]]


async def test_thirty_looks_without_a_change_says_so():
    b, ran = _looking_backend("read_file", 31)
    [ev async for ev in b.ask("find the problem")]
    assert len(ran) >= 30
    notes = _notes(b)
    assert notes, "expected a note after 30 non-changing calls"
    assert "30 tool calls since anything changed" in notes[0]
    assert "If you know the fix, make it now" in notes[0]


async def test_a_change_resets_the_count():
    """write_file changes something, so the count starts again and no note lands."""
    ran = []

    async def handler(args):
        ran.append(args)
        return {"content": "ok"}

    from test_schema_deferral import _tool
    look, change = _tool("read_file"), _tool("write_file")
    look.handler = change.handler = handler
    b = _backend([look, change])
    rounds = []
    for i in range(40):                      # a change every 10th round
        rounds.append(_tool_round("write_file" if i % 10 == 9 else "read_file",
                                  '{"path": "f%d.txt"}' % i))
    b._client = _FakeClient(rounds + [_text_round("done")])
    [ev async for ev in b.ask("fix it")]
    assert _notes(b) == [], "a change every 10 rounds should never reach 30"


async def test_a_shell_command_counts_as_changing():
    """run_bash is how a model edits when it prefers the shell; nagging a session
    doing real work through it would be wrong."""
    b, ran = _looking_backend("run_bash", 35)
    [ev async for ev in b.ask("build it")]
    assert _notes(b) == []
