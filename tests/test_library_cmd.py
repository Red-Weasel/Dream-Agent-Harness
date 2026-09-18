"""`/library` and `/export library` — the human side of the Library.

The store had nine tools and had never been used, because nothing reached it from
where the user sits. These tests drive the exact function the TUI dispatches to, with a
captured printer and a scratch store, so a green here means the command works — not
merely that it exists.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dream.library.store import Library
from dream.tui import library_cmd


@pytest.fixture()
def lib(tmp_path):
    return Library(tmp_path / "library.db", tmp_path / "blobs")


class _Capture:
    """A printer that remembers. `list.append` cannot carry an attribute."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line) -> None:
        self.lines.append(line)          # a str, or a rich Text for file bodies


@pytest.fixture()
def out():
    return _Capture()


def _text(out) -> str:
    return "\n".join(str(x) for x in out.lines)


# --- criterion 1: bare /library lists, or says empty --------------------------


@pytest.mark.asyncio
async def test_bare_library_on_an_empty_store_says_so_and_shows_usage(lib, out):
    await library_cmd.run("", out, lib)
    assert "empty" in _text(out)
    assert "/library search" in _text(out)


@pytest.mark.asyncio
async def test_bare_library_lists_newest_first_with_short_ids(lib, out):
    a = lib.create(b"first", name="a.md")
    b = lib.create(b"second", name="b.md")
    await library_cmd.run("", out, lib)
    body = _text(out)
    assert a.id[:8] in body and b.id[:8] in body
    assert "2 file(s)" in body


# --- criterion 2: search returns hits with ids --------------------------------


@pytest.mark.asyncio
async def test_search_returns_hits_with_ids(lib, out):
    f = lib.create(b"quarterly revenue rose", name="notes.md")
    await library_cmd.run("search revenue", out, lib)
    assert f.id[:8] in _text(out)


@pytest.mark.asyncio
async def test_search_with_no_words_shows_usage_not_a_crash(lib, out):
    await library_cmd.run("search", out, lib)
    assert "/library search <words>" in _text(out)


@pytest.mark.asyncio
async def test_search_miss_is_a_calm_message(lib, out):
    await library_cmd.run("search absent", out, lib)
    assert "nothing matches" in _text(out)


# --- criterion 3: open prints the file; ambiguity lists candidates -----------


@pytest.mark.asyncio
async def test_open_by_id_prints_the_contents(lib, out):
    f = lib.create(b"the actual text", name="a.md")
    await library_cmd.run(f"open {f.id}", out, lib)
    assert "the actual text" in _text(out)


@pytest.mark.asyncio
async def test_open_by_unique_name_prints_the_contents(lib, out):
    lib.create(b"plan body", name="plan.md")
    await library_cmd.run("open plan", out, lib)
    assert "plan body" in _text(out)


@pytest.mark.asyncio
async def test_open_by_ambiguous_name_lists_candidates_and_prints_nothing(lib, out):
    lib.create(b"alpha body", name="plan-alpha.md")
    lib.create(b"beta body", name="plan-beta.md")
    await library_cmd.run("open plan", out, lib)
    body = _text(out)
    assert "ambiguous" in body and "2 candidates" in body
    assert "alpha body" not in body and "beta body" not in body


@pytest.mark.asyncio
async def test_file_contents_are_not_interpreted_as_markup(lib, out):
    """A file containing [red] must not turn the terminal red, and the brackets
    must survive. Asserted by rendering through a real console — the earlier
    version asserted a literal backslash, which pinned the escaping mechanism
    rather than the property, and broke when the mechanism improved."""
    from io import StringIO
    from rich.console import Console

    f = lib.create(b"see [red]this[/red] and [bold]that[/bold]", name="a.md")
    await library_cmd.run(f"open {f.id}", out, lib)
    buf = StringIO()
    con = Console(file=buf, width=200, color_system="truecolor", markup=True, force_terminal=True)
    for line in out.lines:
        con.print(line)
    rendered = buf.getvalue()
    assert "see [red]this[/red] and [bold]that[/bold]" in rendered
    assert "\x1b[31m" not in rendered and "\x1b[1m" not in rendered.split("see [red]")[-1]


# --- criterion 4: trash + undelete --------------------------------------------


@pytest.mark.asyncio
async def test_trash_lists_deleted_files_with_ids(lib, out):
    f = lib.create(b"x", name="gone.md")
    lib.delete(f.id)
    await library_cmd.run("trash", out, lib)
    assert f.id[:8] in _text(out) and "gone.md" in _text(out)


@pytest.mark.asyncio
async def test_empty_trash_says_so(lib, out):
    await library_cmd.run("trash", out, lib)
    assert "trash is empty" in _text(out)


@pytest.mark.asyncio
async def test_undelete_by_full_id_restores(lib, out):
    f = lib.create(b"x", name="gone.md")
    lib.delete(f.id)
    await library_cmd.run(f"undelete {f.id}", out, lib)
    assert "restored" in _text(out)
    assert [x.id for x in lib.list()] == [f.id]


@pytest.mark.asyncio
async def test_undelete_accepts_the_short_id_the_listing_shows(lib, out):
    """Nobody retypes 32 hex characters."""
    f = lib.create(b"x", name="gone.md")
    lib.delete(f.id)
    await library_cmd.run(f"undelete {f.id[:8]}", out, lib)
    assert lib.list() and lib.list()[0].id == f.id


@pytest.mark.asyncio
async def test_a_short_id_matching_two_files_is_refused_not_guessed(lib, out, monkeypatch):
    """An undelete aimed at the wrong file is exactly what a short id must not enable."""
    # Two ids sharing a prefix, minted the way the store mints them — rewriting a
    # primary key after the fact trips the foreign key from library_versions,
    # which is the store being right, not the test.
    import uuid
    from itertools import count

    n = count()
    fake = lambda: uuid.UUID(hex=f"deadbeef{next(n):024x}")
    monkeypatch.setattr("dream.library.store.uuid.uuid4", fake)
    lib.create(b"a", name="a.md")
    lib.create(b"b", name="b.md")
    await library_cmd.run("undelete deadbeef", out, lib)
    assert "matches 2 files" in _text(out)


# --- criterion 5: folders --------------------------------------------------------


@pytest.mark.asyncio
async def test_folders_lists_with_counts(lib, out):
    lib.create(b"x", name="a.md", folder="/plans")
    lib.create(b"y", name="b.md", folder="/plans")
    await library_cmd.run("folders", out, lib)
    assert "/plans" in _text(out) and "2 files" in _text(out)


# --- criterion 6: /export library ---------------------------------------------


def _session_log(tmp_path: Path, sid: str) -> Path:
    p = tmp_path / f"{sid}.jsonl"
    events = [
        {"role": "user", "content": "hello", "ts": "2026-09-02T10:00:00"},
        {"role": "assistant", "content": "hi there", "ts": "2026-09-02T10:00:01"},
    ]
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return p


@pytest.mark.asyncio
async def test_export_library_files_the_session_and_prints_the_id(lib, out, tmp_path):
    log = _session_log(tmp_path, "20260902-100000-abcd")
    await library_cmd.file_session(log, "20260902-100000-abcd", lib, out)
    body = _text(out)
    assert "filed 2 events" in body
    files = lib.list()
    assert len(files) == 1
    assert files[0].folder == "/sessions"
    assert files[0].id in body
    assert "hi there" in lib.read(files[0].id)[0]


@pytest.mark.asyncio
async def test_export_library_with_a_missing_log_is_an_error_not_a_crash(lib, out, tmp_path):
    await library_cmd.file_session(tmp_path / "nope.jsonl", "x", lib, out)
    assert "no session log" in _text(out)
    assert lib.list() == []


# --- criterion 7: wired and documented ---------------------------------------


def test_the_command_is_wired_and_documented():
    app = (Path(__file__).parent.parent / "dream" / "tui" / "app.py").read_text()
    assert 'cmd == "library"' in app
    assert "library_cmd.run(" in app          # delegates to the tested path
    assert "library_cmd.file_session(" in app
    assert "/library [verb]" in app            # help banner
    assert "/export library" in app


# --- unknown verb ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unknown_verb_shows_usage(lib, out):
    await library_cmd.run("frobnicate", out, lib)
    assert "unknown" in _text(out) and "/library search" in _text(out)


# =============================================================================
# Gate 1 findings — each one below was a FAIL from a fresh evaluator, verified by
# execution before it was fixed. The test names say what the gate caught.
# =============================================================================


@pytest.mark.asyncio
async def test_gate1_open_accepts_the_eight_char_id_the_listing_shows(lib, out):
    """Every listing prints id[:8]; open only took the full id. So 'open <id>' was
    unreachable from the TUI and the ambiguity hint was a dead end."""
    f = lib.create(b"body", name="notes.md")
    await library_cmd.run(f"open {f.id[:8]}", out, lib)
    assert "body" in _text(out)


@pytest.mark.asyncio
async def test_gate1_an_exact_name_wins_over_fts_ambiguity(lib, out):
    """FTS is an OR of prefix tokens, so plan.md was 'ambiguous' beside plan-alpha.md
    and plan-beta.md — none of the three could be opened by name."""
    lib.create(b"the plan", name="plan.md")
    lib.create(b"alpha", name="plan-alpha.md")
    lib.create(b"beta", name="plan-beta.md")
    await library_cmd.run("open plan.md", out, lib)
    assert "the plan" in _text(out) and "ambiguous" not in _text(out)
    out.lines.clear()
    await library_cmd.run("open plan-alpha.md", out, lib)
    assert "alpha" in _text(out) and "ambiguous" not in _text(out)


@pytest.mark.asyncio
async def test_gate1_a_backslash_before_a_bracket_is_not_mangled(lib, out):
    """The hand-rolled escape turned \\[red]x into an escaped backslash plus a LIVE
    tag: text dropped, what followed painted red. Any regex or LaTeX file hit it."""
    from rich.console import Console
    from io import StringIO

    body = 're.compile(r"\\[red\\]") and literal \\[red]x'
    f = lib.create(body.encode(), name="rx.py")
    await library_cmd.run(f"open {f.id}", out, lib)
    buf = StringIO()
    con = Console(file=buf, width=200, color_system=None, markup=True)
    for line in out.lines:
        con.print(line)
    rendered = buf.getvalue()
    assert 're.compile(r"\\[red\\]")' in rendered
    assert "literal \\[red]x" in rendered


@pytest.mark.asyncio
async def test_gate1_same_second_creates_list_newest_first(lib, out):
    """Second-resolution timestamps tie, and ties fell to rowid ASC — three files
    filed in one round listed OLDEST first under a 'newest first' label."""
    a = lib.create(b"1", name="a.md")
    b = lib.create(b"2", name="b.md")
    c = lib.create(b"3", name="c.md")
    await library_cmd.run("", out, lib)
    body = _text(out)
    assert body.index(c.id[:8]) < body.index(b.id[:8]) < body.index(a.id[:8])


# --- observations the gate listed as non-blocking; fixed anyway --------------


@pytest.mark.asyncio
async def test_gate1_undelete_on_a_live_file_is_refused_not_faked(lib, out):
    f = lib.create(b"x", name="alive.md")
    await library_cmd.run(f"undelete {f.id}", out, lib)
    assert "restored" not in _text(out)
    assert "not deleted" in _text(out)


@pytest.mark.asyncio
async def test_gate1_like_wildcards_in_a_short_id_are_literal(lib, out):
    """`undelete %` matched every file. A prefix is text, not a pattern."""
    f = lib.create(b"x", name="gone.md")
    lib.delete(f.id)
    await library_cmd.run("undelete %", out, lib)
    assert "restored" not in _text(out)
    assert lib.list() == []


def test_gate1_the_command_no_longer_reaches_into_the_private_connection():
    import inspect

    assert "_conn" not in inspect.getsource(library_cmd)


@pytest.mark.asyncio
async def test_gate1_opening_a_deleted_file_says_deleted_not_nothing_matches(lib, out):
    f = lib.create(b"x", name="gone.md")
    lib.delete(f.id)
    await library_cmd.run(f"open {f.id}", out, lib)
    assert "is deleted" in _text(out) and "undelete" in _text(out)
    assert "nothing matches" not in _text(out)


@pytest.mark.asyncio
async def test_gate1_reexporting_a_session_is_a_new_version_not_a_duplicate(lib, out, tmp_path):
    """The Library's own rule — an edit is a replace — applied to its own writes."""
    log = _session_log(tmp_path, "20260902-100000-abcd")
    await library_cmd.file_session(log, "20260902-100000-abcd", lib, out)
    await library_cmd.file_session(log, "20260902-100000-abcd", lib, out)
    files = lib.list(folder="/sessions")
    assert len(files) == 1 and files[0].version == 2
    assert "updated to v2" in _text(out)


# --- criterion 8, strengthened: drive the REAL App._command glue -------------


@pytest.mark.asyncio
async def test_gate1_library_dispatches_through_the_real_app(tmp_path, capsys, monkeypatch):
    """Source-text inspection proves the branch exists; this proves it runs. The
    app.py glue (SESSIONS_DIR path, --no-tools parse, return False) had no executed
    test — the gate was right to want one."""
    from types import SimpleNamespace

    from dream.tools import library_tools
    from dream.tui.app import App
    import dream.config as config

    store = Library(tmp_path / "library.db", tmp_path / "blobs")
    monkeypatch.setattr(library_tools, "_LIB", store)
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setattr(config, "SESSIONS_DIR", sessions)
    sid = "20260902-100000-abcd"
    _session_log(sessions, sid)

    app = App(provider="machx", model="m", workspace=tmp_path)
    app.engine = SimpleNamespace(store=None, session_id=sid)
    app.renderer.console.width = 200

    f = store.create(b"real dispatch body", name="notes.md")
    quit_ = await app._command(f"/library open {f.id[:8]}")
    assert quit_ is False
    assert "real dispatch body" in capsys.readouterr().out

    quit_ = await app._command("/export library --no-tools")
    assert quit_ is False
    printed = capsys.readouterr().out
    assert "filed 2 events" in printed
    filed = store.list(folder="/sessions")
    assert len(filed) == 1 and filed[0].name == f"session-{sid}.md"



# --- Gate 1 PASS observations, fixed anyway -----------------------------------


@pytest.mark.asyncio
async def test_gate1_obs_a_backslash_before_a_non_tag_bracket_survives(lib, out):
    """rich.markup.escape only escapes tag-shaped brackets; the plain-text pass then
    rewrote `\\[` to `[` anyway. The body is now a Text renderable — never parsed."""
    from io import StringIO
    from rich.console import Console

    body = 're.compile(r"\\[(\\d+)\\]")  LaTeX: \\[ x^2 \\]  C:\\[dir]  x\\\\[0] y'
    f = lib.create(body.encode(), name="rx.py")
    await library_cmd.run(f"open {f.id}", out, lib)
    buf = StringIO()
    con = Console(file=buf, width=300, color_system=None, markup=True)
    for line in out.lines:
        con.print(line)
    assert body in buf.getvalue()


@pytest.mark.asyncio
async def test_gate1_obs_open_by_name_has_no_200_file_cliff(lib, out):
    for i in range(205):
        lib.create(b"x", name=f"f{i:03d}.md")
    oldest = lib.create(b"the oldest body", name="zz-oldest.md")  # newest by time...
    # ...so make it the OLDEST by pushing 205 more after it
    for i in range(205):
        lib.create(b"y", name=f"g{i:03d}.md")
    await library_cmd.run("open zz-oldest.md", out, lib)
    assert "the oldest body" in _text(out) and "ambiguous" not in _text(out)


@pytest.mark.asyncio
async def test_gate1_obs_a_short_hex_name_is_a_name_not_an_id(lib, out):
    """`cafe` is a filename; only 8+ hex chars are tried as an id prefix."""
    lib.create(b"the cafe file", name="cafe")
    await library_cmd.run("open cafe", out, lib)
    assert "the cafe file" in _text(out)


@pytest.mark.asyncio
async def test_gate1_obs_open_by_name_of_a_deleted_file_says_deleted(lib, out):
    f = lib.create(b"x", name="gone.md")
    lib.delete(f.id)
    await library_cmd.run("open gone.md", out, lib)
    assert "is deleted" in _text(out) and "nothing matches" not in _text(out)


@pytest.mark.asyncio
async def test_gate1_obs_exact_name_is_case_insensitive(lib, out):
    lib.create(b"plan body", name="plan.md")
    await library_cmd.run("open Plan.MD", out, lib)
    assert "plan body" in _text(out)


@pytest.mark.asyncio
async def test_gate1_obs_reexport_lookup_has_no_cliff(lib, out, tmp_path):
    for i in range(205):
        lib.create(b"s", name=f"session-old{i:03d}.md", folder="/sessions")
    log = _session_log(tmp_path, "20260902-100000-abcd")
    await library_cmd.file_session(log, "20260902-100000-abcd", lib, out)
    await library_cmd.file_session(log, "20260902-100000-abcd", lib, out)
    mine = lib.by_name("session-20260902-100000-abcd.md", folder="/sessions")
    assert len(mine) == 1 and mine[0].version == 2
