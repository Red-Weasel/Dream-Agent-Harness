"""The two commands that make Phase B's checkpoints and code review reachable.

Both exist to be honest about what they did NOT do:

- `/rewind` must never report a clean rollback for files it skipped, and must ask
  before overwriting anything — the files on disk are the user's, not Dream's.
- `/review` has four outcomes and only one of them is a clean bill of health.
  `unavailable` and `unparseable` mean nothing was checked; if the wiring renders
  them like "no findings", the whole module is pointless.

Everything here drives `App._command` directly against a stubbed engine and a
checkpoint store under tmp_path — no model, no network, no touching Dream's own var/.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dream.core import review as review_mod
from dream.core.checkpoints import CheckpointStore
from dream.tui.app import App


class _StubEngine:
    """`prompt in, text out` — the only shape /review needs from a backend."""

    def __init__(self, answer: str = "") -> None:
        self.store = None
        self.answer = answer
        self.prompts: list[str] = []

    async def ask(self, prompt):
        self.prompts.append(prompt)
        yield SimpleNamespace(kind="assistant_done", data=self.answer)

    async def interrupt(self):
        return None


def _app(tmp_path, engine=None) -> App:
    app = App(provider="machx", model="m", workspace=tmp_path)
    app.engine = engine or SimpleNamespace(store=None)
    # Never the live store under var/ — these tests write real checkpoints.
    app.checkpoints = CheckpointStore(tmp_path / "cp", session="sess-1")
    app.renderer.console.width = 200  # so assertions aren't defeated by wrapping
    return app


def _no_questions(app) -> None:
    async def _fail(prompt):
        raise AssertionError(f"asked a question it shouldn't have: {prompt!r}")

    app._read_answer = _fail


def _answers(app, reply: str) -> None:
    async def _reply(prompt):
        return reply

    app._read_answer = _reply


# --- /rewind ------------------------------------------------------------------


async def test_rewind_bare_lists_recent_checkpoints(tmp_path, capsys):
    app = _app(tmp_path)
    f = tmp_path / "a.py"
    f.write_text("original\n")
    cid = app.checkpoints.take("fix the parser", [f])
    app.checkpoints.seal(cid)
    _no_questions(app)  # listing must never prompt

    await app._command("/rewind")

    out = capsys.readouterr().out
    assert cid in out
    assert "fix the parser" in out
    assert "1 file(s)" in out
    assert "just now" in out


async def test_rewind_bare_says_so_when_there_is_nothing(tmp_path, capsys):
    app = _app(tmp_path)
    await app._command("/rewind")
    assert "no checkpoints yet" in capsys.readouterr().out


async def test_rewind_asks_before_restoring_and_does_nothing_on_no(tmp_path, capsys):
    app = _app(tmp_path)
    f = tmp_path / "a.py"
    f.write_text("original\n")
    cid = app.checkpoints.take("turn 1", [f])
    f.write_text("dream's edit\n")
    app.checkpoints.seal(cid)

    asked: list[str] = []

    async def _decline(prompt):
        asked.append(prompt)
        return "n"

    app._read_answer = _decline

    await app._command(f"/rewind {cid}")

    assert asked, "restoring overwrites files on disk — it must ask first"
    assert f.read_text() == "dream's edit\n"  # nothing rolled back
    assert "nothing restored" in capsys.readouterr().out


async def test_rewind_restores_on_yes(tmp_path, capsys):
    app = _app(tmp_path)
    f = tmp_path / "a.py"
    f.write_text("original\n")
    cid = app.checkpoints.take("turn 1", [f])
    f.write_text("dream's edit\n")
    app.checkpoints.seal(cid)
    _answers(app, "y")

    await app._command(f"/rewind {cid}")

    assert f.read_text() == "original\n"
    out = capsys.readouterr().out
    assert "restored" in out
    assert "SKIPPED" not in out


async def test_rewind_never_claims_a_clean_restore_when_it_skipped_files(tmp_path, capsys):
    """The file changed after the snapshot — that change may be the user's, so the
    store leaves it alone. The REPL has to say that, loudly."""
    app = _app(tmp_path)
    f = tmp_path / "a.py"
    f.write_text("original\n")
    cid = app.checkpoints.take("turn 1", [f])
    f.write_text("dream's edit\n")
    app.checkpoints.seal(cid)
    f.write_text("user's later edit\n")  # neither the before nor the after state
    _answers(app, "y")

    await app._command(f"/rewind {cid}")

    assert f.read_text() == "user's later edit\n"  # untouched
    out = capsys.readouterr().out
    assert "SKIPPED" in out
    assert "NOT restored" in out
    assert "✓" not in out  # never a tick over a rollback that didn't happen


async def test_rewind_with_an_unknown_id_prints_usage_and_changes_nothing(tmp_path, capsys):
    app = _app(tmp_path)
    f = tmp_path / "a.py"
    f.write_text("original\n")
    app.checkpoints.seal(app.checkpoints.take("turn 1", [f]))
    _no_questions(app)

    await app._command("/rewind 9999")

    out = capsys.readouterr().out
    assert "no checkpoint '9999'" in out
    assert f.read_text() == "original\n"


async def test_rewind_accepts_an_unpadded_id(tmp_path, capsys):
    app = _app(tmp_path)
    f = tmp_path / "a.py"
    f.write_text("original\n")
    cid = app.checkpoints.take("turn 1", [f])
    assert cid == "0001"
    f.write_text("dream's edit\n")
    app.checkpoints.seal(cid)
    _answers(app, "y")

    await app._command("/rewind 1")  # '1' and '0001' name the same checkpoint

    assert f.read_text() == "original\n"


# --- the automatic snapshot ---------------------------------------------------


async def test_a_turns_writes_fold_into_one_labelled_checkpoint(tmp_path):
    app = _app(tmp_path, engine=_StubEngine())
    app.mode = "auto"  # in-workspace writes are granted without a prompt
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    a.write_text("a before\n")
    b.write_text("b before\n")

    async def stream(prompt):
        for path in (a, b):
            assert await app._permission("write_file", {"path": str(path)})
            path.write_text("dream wrote this\n")
        yield SimpleNamespace(kind="assistant_done", data="done")

    app.engine.ask = stream
    await app._ask("rewrite both files\nand be quick about it")

    rows = app.checkpoints.list()
    assert len(rows) == 1  # a turn is ONE undo step, not one per tool call
    assert rows[0]["label"] == "rewrite both files"  # the prompt's first line
    assert rows[0]["files"] == 2
    assert rows[0]["sealed"] is True  # sealed at the turn boundary

    rep = app.checkpoints.restore(rows[0]["id"])
    assert sorted(rep.restored) == sorted([str(a), str(b)])
    assert a.read_text() == "a before\n"
    assert b.read_text() == "b before\n"


async def test_the_snapshot_survives_a_workspace_with_no_files(tmp_path):
    """Nothing on disk to snapshot yet: the checkpoint records "wasn't there",
    which is what lets a rewind delete the file the turn created."""
    app = _app(tmp_path, engine=_StubEngine())
    app.mode = "auto"
    new = tmp_path / "brand_new.py"

    async def stream(prompt):
        assert await app._permission("write_file", {"path": str(new)})
        new.write_text("created by dream\n")
        yield SimpleNamespace(kind="assistant_done", data="done")

    app.engine.ask = stream
    await app._ask("make a new file")  # must not raise

    rows = app.checkpoints.list()
    assert len(rows) == 1
    rep = app.checkpoints.restore(rows[0]["id"])
    assert rep.deleted == [str(new)]
    assert not new.exists()


async def test_a_turn_that_writes_nothing_leaves_no_checkpoint(tmp_path):
    app = _app(tmp_path, engine=_StubEngine())

    async def stream(prompt):
        assert await app._permission("read_file", {"path": str(tmp_path / "a.py")})
        yield SimpleNamespace(kind="assistant_done", data="done")

    app.engine.ask = stream
    await app._ask("just have a look")

    assert app.checkpoints.list() == []  # nothing to undo, so no undo point
    assert app._turn_checkpoint is None


async def test_a_declined_write_is_still_snapshotted_but_reports_nothing_to_undo(tmp_path):
    """Denial happens in the SDK, after the callback returns — so a snapshot only
    taken on approval is the safe order. A file that never changed comes back as
    'unchanged', not as a restore."""
    app = _app(tmp_path, engine=_StubEngine())
    app.mode = "plan"  # writes are denied outright
    f = tmp_path / "a.py"
    f.write_text("original\n")

    assert await app._permission("write_file", {"path": str(f)}) is False
    assert app.checkpoints.list() == []


# --- /review ------------------------------------------------------------------


def _result(status, findings=(), detail="", files=("a.py",), truncated=False):
    return review_mod.ReviewResult(status, tuple(findings), detail, tuple(files), truncated)


@pytest.fixture
def captured_review(monkeypatch):
    """Swap run_review for a recorder, so these tests exercise the WIRING (arg
    parsing, the ask callable, rendering) and not git or a model."""
    seen: dict = {"calls": 0}

    def install(result):
        async def fake(workspace, ask, *, staged=False, base=None, focus=None):
            seen.update(calls=seen["calls"] + 1, workspace=workspace, ask=ask,
                        staged=staged, base=base)
            return result

        monkeypatch.setattr(review_mod, "run_review", fake)
        return seen

    return install


async def test_review_renders_findings(tmp_path, capsys, captured_review):
    app = _app(tmp_path, engine=_StubEngine())
    seen = captured_review(_result(
        "reviewed",
        [review_mod.Finding("dream/core/engine.py", 412, "critical",
                            "the exception is swallowed", "re-raise after logging"),
         review_mod.Finding("dream/tui/app.py", None, "low", "stale comment", "")],
    ))

    await app._command("/review")

    out = capsys.readouterr().out
    assert seen["staged"] is False and seen["base"] is None
    assert seen["workspace"] == tmp_path.resolve()
    assert "2 finding(s)" in out
    assert "dream/core/engine.py:412" in out
    assert "critical" in out
    assert "the exception is swallowed" in out
    assert "re-raise after logging" in out
    assert "dream/tui/app.py" in out  # a finding with no line still renders


async def test_review_ask_callable_drives_the_current_engine(tmp_path, captured_review):
    engine = _StubEngine(answer="NO FINDINGS")
    app = _app(tmp_path, engine=engine)
    seen = captured_review(_result("reviewed"))

    await app._command("/review")

    answer = await seen["ask"]("please review this diff")
    assert answer == "NO FINDINGS"
    assert engine.prompts == ["please review this diff"]


async def test_review_staged_and_base_ref_reach_run_review(tmp_path, captured_review):
    app = _app(tmp_path, engine=_StubEngine())
    seen = captured_review(_result("no-changes"))

    await app._command("/review --staged")
    assert seen["staged"] is True and seen["base"] is None

    await app._command("/review origin/main")
    assert seen["staged"] is False and seen["base"] == "origin/main"


async def test_review_unavailable_does_not_read_as_clean(tmp_path, capsys, captured_review):
    app = _app(tmp_path, engine=_StubEngine())
    captured_review(_result("unavailable", detail="fatal: not a git repository", files=()))

    await app._command("/review")

    out = capsys.readouterr().out
    assert "DID NOT RUN" in out
    assert "not a git repository" in out
    # None of the vocabulary of a passing review may appear.
    assert "✓" not in out
    assert "found nothing" not in out
    assert "no findings" not in out.lower()


async def test_review_unparseable_does_not_read_as_clean(tmp_path, capsys, captured_review):
    app = _app(tmp_path, engine=_StubEngine())
    captured_review(_result(
        "unparseable", detail="the reviewer answered but not in findings form: I'd be happy to…"))

    await app._command("/review")

    out = capsys.readouterr().out
    assert "not in findings form" in out
    assert "NOT" in out  # ...a clean review
    assert "✓" not in out
    assert "found nothing" not in out


async def test_review_distinguishes_clean_from_nothing_to_review(tmp_path, capsys, captured_review):
    app = _app(tmp_path, engine=_StubEngine())

    captured_review(_result("reviewed"))
    await app._command("/review")
    clean = capsys.readouterr().out
    assert "found nothing" in clean and "✓" in clean

    captured_review(_result("no-changes", detail="No changes to review.", files=()))
    await app._command("/review")
    empty = capsys.readouterr().out
    assert "nothing to review" in empty
    assert "✓" not in empty  # an empty diff is not a passed review


async def test_review_says_when_the_diff_was_truncated(tmp_path, capsys, captured_review):
    app = _app(tmp_path, engine=_StubEngine())
    captured_review(_result("reviewed", truncated=True))

    await app._command("/review")

    assert "TRUNCATED" in capsys.readouterr().out


async def test_review_with_unknown_args_prints_usage_and_calls_nothing(
    tmp_path, capsys, captured_review
):
    app = _app(tmp_path, engine=_StubEngine())
    seen = captured_review(_result("reviewed"))

    await app._command("/review --bogus")

    assert seen["calls"] == 0  # nothing ran
    assert "usage: /review" in capsys.readouterr().out


# --- both are discoverable ----------------------------------------------------


async def test_help_lists_the_new_commands(tmp_path, capsys):
    app = _app(tmp_path)
    await app._command("/help")
    out = capsys.readouterr().out
    assert "/rewind" in out
    assert "/review" in out
