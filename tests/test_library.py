"""The Library store: identity that survives editing, and history that survives undo.

The point of the Library is that putting something in it is safe. These tests are
mostly about the ways that promise could quietly break — an id that changes under a
rename, a restore that destroys what it restored over, a delete that loses bytes, a
concurrent edit that wins by being second.
"""

from __future__ import annotations

import pytest

from dream.library.store import Library, LibraryError, VersionConflict


@pytest.fixture()
def lib(tmp_path):
    return Library(tmp_path / "library.db", tmp_path / "blobs")


# --- identity ----------------------------------------------------------------


def test_a_new_file_gets_an_id_and_version_one(lib):
    f = lib.create(b"hello", name="a.md")
    assert f.id and f.version == 1 and f.size_bytes == 5


def test_replacing_contents_keeps_the_id_and_bumps_the_version(lib):
    """The whole contract: an edit produces a new version of the same item."""
    f = lib.create(b"first", name="a.md")
    g = lib.replace(f.id, b"second")
    assert g.id == f.id
    assert g.version == 2
    assert lib.read(f.id)[0] == "second"


def test_renaming_does_not_change_identity(lib):
    f = lib.create(b"x", name="old.md")
    g = lib.rename(f.id, "new.md")
    assert g.id == f.id and g.name == "new.md"


def test_moving_does_not_change_identity(lib):
    f = lib.create(b"x", name="a.md", folder="/one")
    g = lib.move(f.id, "/two/three")
    assert g.id == f.id and g.folder == "/two/three"
    assert g.path == "/two/three/a.md"


def test_a_missing_id_is_a_clear_refusal(lib):
    with pytest.raises(LibraryError, match="no Library file"):
        lib.get("nope")


# --- versions ----------------------------------------------------------------


def test_every_version_stays_readable(lib):
    f = lib.create(b"v1", name="a.md")
    lib.replace(f.id, b"v2")
    lib.replace(f.id, b"v3")
    assert lib.read(f.id, version=1)[0] == "v1"
    assert lib.read(f.id, version=2)[0] == "v2"
    assert lib.read(f.id)[0] == "v3"


def test_restoring_an_old_version_appends_rather_than_rewrites(lib):
    """Undo must itself be undoable: restoring v1 over v3 makes a v4, so v3 is still
    there if the restore was the mistake."""
    f = lib.create(b"v1", name="a.md")
    lib.replace(f.id, b"v2")
    lib.replace(f.id, b"v3")
    r = lib.restore_version(f.id, 1)
    assert r.version == 4
    assert lib.read(f.id)[0] == "v1"
    assert lib.read(f.id, version=3)[0] == "v3"


def test_version_history_is_newest_first_and_notes_a_restore(lib):
    f = lib.create(b"v1", name="a.md")
    lib.replace(f.id, b"v2")
    lib.restore_version(f.id, 1)
    hist = lib.versions(f.id)
    assert [h["version"] for h in hist] == [3, 2, 1]
    assert "restored from v1" in hist[0]["note"]


def test_restoring_a_version_that_never_existed_is_refused(lib):
    f = lib.create(b"v1", name="a.md")
    with pytest.raises(LibraryError, match="no version"):
        lib.restore_version(f.id, 9)


# --- concurrency -------------------------------------------------------------


def test_a_stale_writer_is_refused_rather_than_silently_winning(lib):
    f = lib.create(b"v1", name="a.md")
    lib.replace(f.id, b"v2")                       # someone else got there first
    with pytest.raises(VersionConflict):
        lib.replace(f.id, b"mine", expected_current_version=1)
    assert lib.read(f.id)[0] == "v2"               # their edit survives


def test_the_guard_passes_when_the_version_is_current(lib):
    f = lib.create(b"v1", name="a.md")
    g = lib.replace(f.id, b"v2", expected_current_version=1)
    assert g.version == 2


def test_no_guard_means_last_write_wins(lib):
    """Omitting the guard is allowed — it is how a single writer avoids ceremony —
    so it must still work, and must not pretend to be safe."""
    f = lib.create(b"v1", name="a.md")
    lib.replace(f.id, b"v2")
    assert lib.replace(f.id, b"v3").version == 3


# --- content-addressed storage ----------------------------------------------


def test_identical_content_is_stored_once(lib):
    a = lib.create(b"same bytes", name="a.md")
    b = lib.create(b"same bytes", name="b.md")
    blobs = list((lib.blob_dir).rglob("*"))
    assert a.id != b.id
    assert len([p for p in blobs if p.is_file()]) == 1


def test_a_restore_copies_no_bytes(lib):
    f = lib.create(b"v1", name="a.md")
    lib.replace(f.id, b"v2")
    before = len([p for p in lib.blob_dir.rglob("*") if p.is_file()])
    lib.restore_version(f.id, 1)
    assert len([p for p in lib.blob_dir.rglob("*") if p.is_file()]) == before


# --- delete is safe ----------------------------------------------------------


def test_delete_hides_the_file_but_keeps_it(lib):
    f = lib.create(b"precious", name="a.md")
    lib.delete(f.id)
    assert lib.list() == []
    with pytest.raises(LibraryError, match="deleted"):
        lib.get(f.id)
    assert lib.get(f.id, include_deleted=True).deleted is True


def test_undelete_brings_it_back_with_its_history(lib):
    f = lib.create(b"v1", name="a.md")
    lib.replace(f.id, b"v2")
    lib.delete(f.id)
    g = lib.undelete(f.id)
    assert g.version == 2
    assert lib.read(f.id)[0] == "v2"
    assert len(lib.versions(f.id)) == 2


def test_a_deleted_file_leaves_search(lib):
    f = lib.create(b"findable text", name="a.md")
    assert lib.search("findable")
    lib.delete(f.id)
    assert lib.search("findable") == []


def test_undelete_puts_it_back_in_search(lib):
    f = lib.create(b"findable text", name="a.md")
    lib.delete(f.id)
    lib.undelete(f.id)
    assert [h.file.id for h in lib.search("findable")] == [f.id]


# --- search and find ---------------------------------------------------------


def test_search_matches_content(lib):
    lib.create(b"quarterly revenue was up", name="notes.md")
    lib.create(b"unrelated", name="other.md")
    assert [h.file.name for h in lib.search("revenue")] == ["notes.md"]


def test_search_matches_a_stemmed_title(lib):
    """Porter stemming is why this goes through FTS rather than LIKE."""
    lib.create(b"body", name="Quarterly Revenues.md")
    assert lib.search("revenue", title_only=True)


def test_title_only_search_ignores_the_body(lib):
    lib.create(b"a body mentioning bananas", name="notes.md")
    assert lib.search("bananas") != []
    assert lib.search("bananas", title_only=True) == []


def test_search_of_a_pure_punctuation_query_returns_nothing(lib):
    """An FTS MATCH built from no tokens must not become a syntax error."""
    lib.create(b"x", name="a.md")
    assert lib.search("???") == []


def test_find_reports_line_numbers(lib):
    f = lib.create(b"alpha\nbeta\ngamma\n", name="a.md")
    hits = lib.find([f.id], "beta")
    assert [(h.line_no, h.line) for h in hits] == [(2, "beta")]


def test_find_can_use_a_regex(lib):
    f = lib.create(b"v1.2.3\nnope\n", name="a.md")
    assert lib.find([f.id], r"v\d+\.\d+\.\d+", regex=True)[0].line_no == 1


def test_a_bad_regex_is_a_refusal_not_a_crash(lib):
    f = lib.create(b"x", name="a.md")
    with pytest.raises(LibraryError, match="bad pattern"):
        lib.find([f.id], "(unclosed", regex=True)


def test_a_literal_search_does_not_treat_input_as_a_pattern(lib):
    f = lib.create(b"cost is $5 (approx)\n", name="a.md")
    assert lib.find([f.id], "$5 (approx)")[0].line_no == 1


# --- listing and folders -----------------------------------------------------


def test_listing_is_scoped_to_a_folder(lib):
    lib.create(b"x", name="a.md", folder="/one")
    lib.create(b"y", name="b.md", folder="/two")
    assert [f.name for f in lib.list(folder="/one")] == ["a.md"]


def test_folders_are_normalised_to_one_spelling(lib):
    f = lib.create(b"x", name="a.md", folder="work//notes/")
    assert f.folder == "/work/notes"


def test_a_folder_cannot_climb_out_of_the_library(lib):
    """Folders are labels in a database, not filesystem paths."""
    f = lib.create(b"x", name="a.md", folder="/../../etc")
    assert f.folder == "/etc"          # '..' stripped, not resolved
    assert ".." not in f.folder


def test_a_name_cannot_smuggle_a_path(lib):
    f = lib.create(b"x", name="../../escape.md")
    assert f.name == "escape.md"


def test_an_empty_name_is_refused(lib):
    with pytest.raises(LibraryError, match="real name"):
        lib.create(b"x", name="   ")


def test_creating_from_bytes_requires_a_name(lib):
    with pytest.raises(LibraryError, match="explicit name"):
        lib.create(b"x")


def test_listing_refuses_an_absurd_limit(lib):
    with pytest.raises(LibraryError, match="at most"):
        lib.list(limit=10_000)


# --- local files in and out --------------------------------------------------


def test_a_local_file_can_be_taken_in(lib, tmp_path):
    src = tmp_path / "src.md"
    src.write_text("from disk", encoding="utf-8")
    f = lib.create(src)
    assert f.name == "src.md"
    assert lib.read(f.id)[0] == "from disk"


def test_a_missing_local_file_is_a_clear_refusal(lib, tmp_path):
    with pytest.raises(LibraryError, match="no such local file"):
        lib.create(tmp_path / "absent.md")


def test_materialize_writes_the_bytes_back_out(lib, tmp_path):
    f = lib.create(b"content", name="a.md")
    out = lib.materialize(f.id, tmp_path / "out")
    assert out.read_bytes() == b"content"


def test_materialize_into_a_directory_uses_the_library_name(lib, tmp_path):
    f = lib.create(b"content", name="a.md")
    d = tmp_path / "dir"
    d.mkdir()
    assert lib.materialize(f.id, d).name == "a.md"


def test_an_old_version_can_be_materialized(lib, tmp_path):
    f = lib.create(b"v1", name="a.md")
    lib.replace(f.id, b"v2")
    out = lib.materialize(f.id, tmp_path / "old.md", version=1)
    assert out.read_bytes() == b"v1"


# --- binary ------------------------------------------------------------------


def test_binary_content_is_refused_by_read_not_mangled(lib):
    f = lib.create(b"\x89PNG\x00\x01\x02", name="img.png")
    with pytest.raises(LibraryError, match="binary"):
        lib.read(f.id)


def test_binary_content_still_round_trips_through_materialize(lib, tmp_path):
    raw = b"\x89PNG\x00\x01\x02"
    f = lib.create(raw, name="img.png")
    assert lib.materialize(f.id, tmp_path / "o.png").read_bytes() == raw


def test_a_long_read_is_truncated_and_says_so(lib, monkeypatch):
    monkeypatch.setattr("dream.library.store.READ_MAX_CHARS", 50)
    f = lib.create(b"z" * 5000, name="a.md")
    text, truncated = lib.read(f.id)
    assert truncated and "truncated" in text and len(text) < 200


# --- durability --------------------------------------------------------------


def test_the_library_survives_being_reopened(lib, tmp_path):
    f = lib.create(b"durable", name="a.md")
    lib.replace(f.id, b"still here")
    lib.close()
    again = Library(tmp_path / "library.db", tmp_path / "blobs")
    assert again.read(f.id)[0] == "still here"
    assert again.get(f.id).version == 2


def test_a_missing_blob_is_reported_rather_than_returning_nothing(lib):
    f = lib.create(b"content", name="a.md")
    for p in lib.blob_dir.rglob("*"):
        if p.is_file():
            p.unlink()
    with pytest.raises(LibraryError, match="missing from the Library store"):
        lib.read(f.id)
