"""Identity that survives leaving the Library, and the states that were unreachable.

Five dead ends, each confirmed in the code before it was fixed:

- D1  a materialized file carried nothing saying where it came from, so an edit could
      only be saved back while the id survived in the model's context
- D2  a soft-deleted file could not be found again, making `undelete` unreachable
- D3  `purge` was named in the module docstring and did not exist
- D4  `folders()` worked and no tool could call it
- D5  nothing turned "the plan" into one committed identity
"""

from __future__ import annotations

import os

import pytest

import json as _json

from dream.library.store import Library, LibraryError, XATTR_ID


def _stamp_of(path):
    """The identity a file carries, as the store writes it."""
    return _json.loads(os.getxattr(path, XATTR_ID).decode())


def _restamp(path, store, file_id, version=1):
    """Forge a stamp, the way a metadata-preserving copy of another file would."""
    os.setxattr(path, XATTR_ID, _json.dumps(
        {"store": store.store_id, "file": file_id, "version": version}).encode())
from dream.tools import library_tools


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    store = Library(tmp_path / "library.db", tmp_path / "blobs")
    monkeypatch.setattr(library_tools, "_LIB", store)
    return store


def _text(res): return "".join(b.get("text", "") for b in res.get("content", []))
def _is_error(res): return bool(res.get("is_error"))


# --- D1: identity survives the round trip ------------------------------------


def test_materializing_stamps_the_file_with_its_identity(lib, tmp_path):
    f = lib.create(b"plan v1", name="plan.md")
    out = lib.materialize(f.id, tmp_path / "work" / "plan.md")
    stamp = _stamp_of(out)
    assert stamp["file"] == f.id
    assert stamp["version"] == f.version
    assert stamp["store"] == lib.store_id   # ids only mean something inside one store


def test_origin_answers_where_a_local_file_came_from(lib, tmp_path):
    f = lib.create(b"plan v1", name="plan.md")
    out = lib.materialize(f.id, tmp_path / "work" / "plan.md")
    found = lib.origin(out)
    assert found is not None
    assert found[0].id == f.id and found[1] == 1 and found[2] == "stamped"


def test_identity_survives_an_editor_that_drops_xattrs(lib, tmp_path):
    """Many editors write a temp file and rename over the target, losing xattrs. The
    checkout row is why that does not lose the document's identity."""
    f = lib.create(b"plan v1", name="plan.md")
    out = lib.materialize(f.id, tmp_path / "work" / "plan.md")
    out.unlink()
    out.write_text("edited by something that knows nothing about us", encoding="utf-8")
    with pytest.raises(OSError):
        os.getxattr(out, XATTR_ID)
    found = lib.origin(out)
    assert found is not None and found[0].id == f.id
    assert found[2] == "path-only"   # the weak signal, and it says so


def test_an_unrelated_file_has_no_origin(lib, tmp_path):
    stray = tmp_path / "stray.md"
    stray.write_text("never been in the library", encoding="utf-8")
    assert lib.origin(stray) is None


def test_origin_of_a_purged_file_is_none_not_a_crash(lib, tmp_path):
    """A stale xattr must not resurrect a file that no longer exists."""
    f = lib.create(b"x", name="a.md")
    out = lib.materialize(f.id, tmp_path / "a.md")
    lib.purge(f.id)
    assert lib.origin(out) is None


def test_materializing_an_old_version_records_that_version(lib, tmp_path):
    f = lib.create(b"v1", name="a.md")
    lib.replace(f.id, b"v2")
    out = lib.materialize(f.id, tmp_path / "old.md", version=1)
    assert lib.origin(out)[1] == 1


@pytest.mark.asyncio
async def test_replace_finds_the_id_from_the_path_alone(lib, tmp_path):
    """The failure this closes: the id falls out of a compacted context, the model
    calls library_create, and the document forks."""
    f = lib.create(b"plan v1", name="plan.md")
    out = lib.materialize(f.id, tmp_path / "work" / "plan.md")
    out.write_text("plan v2", encoding="utf-8")
    res = await library_tools.library_replace.handler({"path": str(out)})
    assert not _is_error(res)
    assert lib.get(f.id).version == 2
    assert lib.read(f.id)[0] == "plan v2"
    assert len(lib.list()) == 1          # no fork


@pytest.mark.asyncio
async def test_a_path_with_no_history_is_refused_and_points_at_create(lib, tmp_path):
    stray = tmp_path / "stray.md"
    stray.write_text("new work", encoding="utf-8")
    res = await library_tools.library_replace.handler({"path": str(stray)})
    assert _is_error(res)
    assert "library_create" in _text(res)


@pytest.mark.asyncio
async def test_the_checked_out_version_becomes_the_guard(lib, tmp_path):
    """Resolving by path should not silently weaken the conflict guard: the version
    checked out is the honest 'what I last saw'."""
    f = lib.create(b"v1", name="a.md")
    out = lib.materialize(f.id, tmp_path / "a.md")
    lib.replace(f.id, b"someone else's v2")      # changed under us
    out.write_text("my edit", encoding="utf-8")
    res = await library_tools.library_replace.handler({"path": str(out)})
    assert _is_error(res)
    assert "conflict" in _text(res).lower()
    assert lib.read(f.id)[0] == "someone else's v2"


@pytest.mark.asyncio
async def test_an_explicit_id_contradicting_the_stamp_is_refused(lib, tmp_path):
    """This test used to assert the opposite — that an explicit file_id simply wins.
    That codified a hole: a caller working from a stale belief could redirect an edit
    onto a document the file was never a checkout of, and nothing checked. When two
    signals name different documents, writing either one is a guess."""
    a = lib.create(b"a", name="a.md")
    b = lib.create(b"b", name="b.md")
    out = lib.materialize(a.id, tmp_path / "a.md")
    out.write_text("redirected", encoding="utf-8")
    res = await library_tools.library_replace.handler(
        {"file_id": b.id, "path": str(out)})
    assert _is_error(res)
    assert lib.read(a.id)[0] == "a"      # neither document was touched
    assert lib.read(b.id)[0] == "b"


# --- D2: the trash is reachable ----------------------------------------------


def test_deleted_files_can_be_listed(lib):
    f = lib.create(b"x", name="gone.md")
    lib.delete(f.id)
    assert [d.name for d in lib.deleted()] == ["gone.md"]


def test_live_files_are_not_in_the_trash(lib):
    lib.create(b"x", name="here.md")
    assert lib.deleted() == []


@pytest.mark.asyncio
async def test_trash_action_shows_the_id_needed_to_undelete(lib):
    f = lib.create(b"x", name="gone.md")
    lib.delete(f.id)
    res = await library_tools.library_manage.handler({"action": "trash"})
    assert f.id in _text(res)


@pytest.mark.asyncio
async def test_an_empty_trash_says_so(lib):
    res = await library_tools.library_manage.handler({"action": "trash"})
    assert not _is_error(res) and "empty" in _text(res)


# --- D3: purge exists and refcounts blobs ------------------------------------


def test_purge_removes_the_file_for_good(lib):
    f = lib.create(b"x", name="a.md")
    lib.purge(f.id)
    with pytest.raises(LibraryError):
        lib.get(f.id, include_deleted=True)


def test_purge_frees_bytes_nothing_else_needs(lib):
    f = lib.create(b"unique content", name="a.md")
    rep = lib.purge(f.id)
    assert rep["bytes_freed"] > 0
    assert not [p for p in lib.blob_dir.rglob("*") if p.is_file()]


def test_purge_never_takes_another_file_s_bytes(lib):
    """Blobs are content-addressed and therefore shared. Purging without a reference
    count would delete a blob a second file still points at."""
    a = lib.create(b"shared bytes", name="a.md")
    b = lib.create(b"shared bytes", name="b.md")
    rep = lib.purge(a.id)
    assert rep["bytes_freed"] == 0
    assert lib.read(b.id)[0] == "shared bytes"


def test_purge_removes_every_version(lib):
    f = lib.create(b"v1", name="a.md")
    lib.replace(f.id, b"v2")
    assert lib.purge(f.id)["versions"] == 2


def test_purge_clears_the_checkout_rows(lib, tmp_path):
    f = lib.create(b"x", name="a.md")
    out = lib.materialize(f.id, tmp_path / "a.md")
    lib.purge(f.id)
    assert lib.checkouts(f.id) == []
    assert lib.origin(out) is None


@pytest.mark.asyncio
async def test_the_purge_tool_says_it_cannot_be_undone(lib):
    f = lib.create(b"x", name="a.md")
    res = await library_tools.library_manage.handler({"action": "purge", "file_id": f.id})
    assert "cannot be undone" in _text(res)


# --- D4: folders are discoverable --------------------------------------------


def test_folder_counts_report_live_files_only(lib):
    lib.create(b"x", name="a.md", folder="/plans")
    lib.create(b"y", name="b.md", folder="/plans")
    c = lib.create(b"z", name="c.md", folder="/notes")
    lib.delete(c.id)
    assert lib.folder_counts() == [("/plans", 2)]


@pytest.mark.asyncio
async def test_the_folders_action_lists_them(lib):
    lib.create(b"x", name="a.md", folder="/plans")
    res = await library_tools.library_manage.handler({"action": "folders"})
    assert "/plans" in _text(res) and "1 file" in _text(res)


@pytest.mark.asyncio
async def test_folders_and_trash_need_no_file_id(lib):
    for action in ("folders", "trash"):
        assert not _is_error(await library_tools.library_manage.handler({"action": action}))


@pytest.mark.asyncio
async def test_actions_that_do_need_an_id_still_say_so(lib):
    res = await library_tools.library_manage.handler({"action": "delete"})
    assert _is_error(res) and "file_id" in _text(res)


# --- D5: resolving one identity ----------------------------------------------


@pytest.mark.asyncio
async def test_resolve_accepts_an_id(lib):
    f = lib.create(b"x", name="a.md")
    res = await library_tools.library_resolve.handler({"reference": f.id})
    assert f.id in _text(res)


@pytest.mark.asyncio
async def test_resolve_commits_when_there_is_one_match(lib):
    f = lib.create(b"quarterly revenue", name="revenue.md")
    res = await library_tools.library_resolve.handler({"reference": "revenue"})
    assert f.id in _text(res) and "ambiguous" not in _text(res)


@pytest.mark.asyncio
async def test_resolve_refuses_to_pick_between_candidates(lib):
    lib.create(b"x", name="plan-alpha.md")
    lib.create(b"y", name="plan-beta.md")
    res = await library_tools.library_resolve.handler({"reference": "plan"})
    body = _text(res)
    assert "ambiguous" in body
    assert "Ask the user" in body


@pytest.mark.asyncio
async def test_resolve_maps_a_local_path_back_to_its_library_file(lib, tmp_path):
    f = lib.create(b"x", name="a.md")
    out = lib.materialize(f.id, tmp_path / "work" / "a.md")
    res = await library_tools.library_resolve.handler({"reference": str(out)})
    assert f.id in _text(res)


@pytest.mark.asyncio
async def test_resolve_warns_when_the_checkout_is_behind(lib, tmp_path):
    f = lib.create(b"v1", name="a.md")
    out = lib.materialize(f.id, tmp_path / "a.md")
    lib.replace(f.id, b"v2")
    res = await library_tools.library_resolve.handler({"reference": str(out)})
    assert "re-read before replacing" in _text(res)


@pytest.mark.asyncio
async def test_resolve_refuses_to_guess_for_an_unknown_path(lib, tmp_path):
    stray = tmp_path / "stray.md"
    stray.write_text("x", encoding="utf-8")
    res = await library_tools.library_resolve.handler({"reference": str(stray)})
    assert _is_error(res) and "Do not guess" in _text(res)


@pytest.mark.asyncio
async def test_resolve_says_nothing_matched_rather_than_inviting_a_duplicate(lib):
    res = await library_tools.library_resolve.handler({"reference": "nonexistent thing"})
    assert "duplicate" in _text(res)


@pytest.mark.asyncio
async def test_resolve_is_registered_and_free_to_call():
    from dream.core import policy
    from dream.tools import registry

    assert "library_resolve" in registry.build()["names"]
    assert policy.capability("library_resolve") == policy.READONLY


# --- the wrong-document overwrite ---------------------------------------------
#
# Found by asking "can path->id be tricked into replacing the wrong file?" and then
# actually trying it. It could: a stale checkout row made an unrelated file resolve to
# a real document, and replacing by path overwrote it. This is the exact failure the
# whole subsystem exists to prevent, arriving through the feature meant to prevent it.


def test_an_unrelated_file_at_a_checked_out_path_is_only_a_weak_match(lib, tmp_path):
    f = lib.create(b"IMPORTANT plan", name="plan.md")
    out = lib.materialize(f.id, tmp_path / "doc.md")
    out.unlink()
    out.write_text("a totally unrelated shopping list", encoding="utf-8")
    found = lib.origin(out)
    assert found is not None            # the row still points somewhere
    assert found[2] == "path-only"      # but it must NOT claim to be sure


@pytest.mark.asyncio
async def test_replacing_by_path_refuses_when_the_stamp_is_gone(lib, tmp_path):
    f = lib.create(b"IMPORTANT plan", name="plan.md")
    out = lib.materialize(f.id, tmp_path / "doc.md")
    out.unlink()
    out.write_text("a totally unrelated shopping list", encoding="utf-8")
    res = await library_tools.library_replace.handler({"path": str(out)})
    assert _is_error(res)
    assert "no longer carries its Library stamp" in _text(res)
    assert lib.read(f.id)[0] == "IMPORTANT plan"     # untouched


@pytest.mark.asyncio
async def test_the_refusal_offers_the_id_so_the_caller_can_commit(lib, tmp_path):
    """Refusing is only useful if the way forward is in the message."""
    f = lib.create(b"x", name="plan.md")
    out = lib.materialize(f.id, tmp_path / "doc.md")
    out.unlink(); out.write_text("y", encoding="utf-8")
    res = await library_tools.library_replace.handler({"path": str(out)})
    assert f"file_id={f.id}" in _text(res)


@pytest.mark.asyncio
async def test_an_explicit_id_overrides_a_weak_match(lib, tmp_path):
    """The escape hatch: the caller who genuinely knows may still proceed."""
    f = lib.create(b"x", name="plan.md")
    out = lib.materialize(f.id, tmp_path / "doc.md")
    out.unlink(); out.write_text("deliberate", encoding="utf-8")
    res = await library_tools.library_replace.handler({"file_id": f.id, "path": str(out)})
    assert not _is_error(res)
    assert lib.read(f.id)[0] == "deliberate"


@pytest.mark.asyncio
async def test_an_in_place_edit_keeps_the_stamp_and_is_allowed(lib, tmp_path):
    """Dream's own write_file truncates in place, so the common path stays frictionless.
    If this ever fails, the path route has become useless and the refusal above is
    blocking real work rather than a real mistake."""
    f = lib.create(b"v1", name="plan.md")
    out = lib.materialize(f.id, tmp_path / "doc.md")
    out.write_text("v2 edited in place", encoding="utf-8")
    assert lib.origin(out)[2] == "stamped"
    res = await library_tools.library_replace.handler({"path": str(out)})
    assert not _is_error(res)
    assert lib.get(f.id).version == 2


@pytest.mark.asyncio
async def test_resolve_flags_a_weak_match_rather_than_asserting_it(lib, tmp_path):
    f = lib.create(b"x", name="plan.md")
    out = lib.materialize(f.id, tmp_path / "doc.md")
    out.unlink(); out.write_text("y", encoding="utf-8")
    res = await library_tools.library_resolve.handler({"reference": str(out)})
    assert "WEAK MATCH" in _text(res)
    assert "replace by file_id, not by path" in _text(res)


# --- findings from the Codex review pass --------------------------------------
#
# Three holes it named, each verified against the code before being fixed:
#   1. identity signals could disagree and the answer still claimed to be certain
#   2. the version guard was read-compare-write, so a concurrent writer could slip in
#   3. purge counted references, committed, then unlinked — a window where a new
#      version could claim a blob that was already condemned


def test_disagreeing_identity_signals_are_not_blended(lib, tmp_path):
    """xattr naming one file and the checkout row naming another used to yield the
    first file's identity with the second file's version number — a guard value that
    belonged to neither, so the conflict check compared against nonsense."""
    a = lib.create(b"file A", name="A.md")
    b = lib.create(b"file B", name="B.md")
    lib.replace(b.id, b"B v2")
    lib.replace(b.id, b"B v3")
    out = lib.materialize(b.id, tmp_path / "w.md")     # row: B at v3
    _restamp(out, lib, a.id, version=1)                # stamp: A at v1

    f, version, confidence = lib.origin(out)
    assert f.id == a.id                 # the file's own stamp wins the identity
    assert version == a.version         # ...and the version comes from A, not B
    assert confidence == "conflicting"  # ...and it stops claiming to be sure


@pytest.mark.asyncio
async def test_a_conflicting_stamp_blocks_a_replace_by_path(lib, tmp_path):
    a = lib.create(b"file A", name="A.md")
    b = lib.create(b"file B", name="B.md")
    out = lib.materialize(b.id, tmp_path / "w.md")
    _restamp(out, lib, a.id)
    out.write_text("edited", encoding="utf-8")
    res = await library_tools.library_replace.handler({"path": str(out)})
    assert _is_error(res)
    assert lib.read(a.id)[0] == "file A"
    assert lib.read(b.id)[0] == "file B"


def test_the_version_guard_is_enforced_by_the_write_not_by_a_prior_read(lib):
    """The guard must live in the UPDATE's WHERE clause. A read-then-write guard is
    only as good as the gap between the two statements."""
    import inspect

    from dream.library import store as store_mod

    src = inspect.getsource(store_mod.Library.replace)
    assert "BEGIN IMMEDIATE" in src
    assert "WHERE id=? AND current_version=?" in src
    assert "rowcount" in src


def test_purge_holds_the_blob_lock_across_the_whole_decision(lib):
    """This test first asserted the opposite ordering — unlink before commit — to pin
    a fix for "a concurrent writer claims a condemned blob". That fix was half right.
    Unlinking first means a crash before the commit rolls the rows back and leaves a
    live file whose bytes are gone: silent data loss from an operation that did not
    finish. Committing first only ever leaks disk.

    The ordering was never what closed the concurrent-writer race — the interprocess
    lock is, held across both halves here and across write-plus-insert in the writers.
    So the invariant to pin is the lock span, and the safe crash direction."""
    import inspect

    from dream.library import store as store_mod

    src = inspect.getsource(store_mod.Library.purge)
    assert "BEGIN IMMEDIATE" in src
    assert "_blob_lock" in src
    commit_at = src.index("self._conn.commit()")
    unlink_at = src.index("blob.unlink()")
    assert commit_at < unlink_at, "a crash must leak a blob, never orphan a live row"


def test_writers_hold_the_blob_lock_while_the_row_is_inserted(lib):
    """Observing that a blob exists and then committing a reference to it is only safe
    if no purge can decide it is unreferenced in between."""
    import inspect

    from dream.library import store as store_mod

    for name in ("create", "replace", "restore_version"):
        src = inspect.getsource(getattr(store_mod.Library, name))
        assert "_blob_lock" in src, f"{name} inserts a blob reference without the lock"


def test_a_vanished_blob_surfaces_as_a_library_error(lib):
    """The API contract is LibraryError. A raw FileNotFoundError naming a blob path
    leaks storage internals into the harness and is not catchable by callers."""
    f = lib.create(b"x", name="a.md")
    for b in lib.blob_dir.rglob("*"):
        if b.is_file():
            b.unlink()
    with pytest.raises(LibraryError, match="missing from the Library store"):
        lib.read(f.id)


def test_the_guard_still_refuses_a_stale_writer(lib):
    """The rewrite must not have weakened what it replaced."""
    from dream.library.store import VersionConflict

    f = lib.create(b"v1", name="a.md")
    lib.replace(f.id, b"theirs")
    with pytest.raises(VersionConflict):
        lib.replace(f.id, b"mine", expected_current_version=1)
    assert lib.read(f.id)[0] == "theirs"


def test_replacing_a_missing_file_is_still_a_clean_refusal(lib):
    with pytest.raises(LibraryError):
        lib.replace("nope", b"x")


# --- the rest of the Codex findings -------------------------------------------


def test_a_stamp_from_another_library_is_not_resolved_here(lib, tmp_path):
    """ids are only meaningful inside one store. A file carried over from another
    machine's Library must not resolve against whatever local id happens to collide —
    that is the worst kind of answer: confident and wrong."""
    f = lib.create(b"x", name="a.md")
    out = lib.materialize(f.id, tmp_path / "a.md")
    os.setxattr(out, XATTR_ID, _json.dumps(
        {"store": "some-other-library", "file": f.id, "version": 1}).encode())
    assert lib.origin(out) is None


def test_a_moved_checkout_keeps_the_version_it_was_taken_at(lib, tmp_path):
    """The path row does not travel with a move; the stamp does. Without the version
    on the stamp, origin() fell back to the CURRENT version — silently upgrading the
    guard and letting a stale edit overwrite a newer one without conflict."""
    f = lib.create(b"v1", name="a.md")
    out = lib.materialize(f.id, tmp_path / "a.md")
    lib.replace(f.id, b"v2")                    # someone else moved it on
    moved = tmp_path / "elsewhere.md"
    out.rename(moved)
    got = lib.origin(moved)
    assert got is not None
    assert got[1] == 1, "the checked-out version must survive the move"


@pytest.mark.asyncio
async def test_a_stale_moved_checkout_is_refused_by_the_guard(lib, tmp_path):
    f = lib.create(b"v1", name="a.md")
    out = lib.materialize(f.id, tmp_path / "a.md")
    lib.replace(f.id, b"theirs v2")
    moved = tmp_path / "elsewhere.md"
    out.rename(moved)
    moved.write_text("my stale edit", encoding="utf-8")
    res = await library_tools.library_replace.handler({"path": str(moved)})
    assert _is_error(res)
    assert lib.read(f.id)[0] == "theirs v2"


def test_a_legacy_stamp_still_resolves(lib, tmp_path):
    """Files stamped by the first version of this code carry a bare id. Failing to
    read them would orphan every checkout taken before the format changed."""
    from dream.library.store import XATTR_ID_LEGACY

    f = lib.create(b"x", name="a.md")
    out = lib.materialize(f.id, tmp_path / "a.md")
    os.removexattr(out, XATTR_ID)
    os.setxattr(out, XATTR_ID_LEGACY, f.id.encode())
    got = lib.origin(out)
    assert got is not None and got[0].id == f.id


def test_trash_paging_reaches_past_the_first_page(lib):
    for i in range(5):
        lib.delete(lib.create(b"x" + str(i).encode(), name=f"{i}.md").id)
    first = {x.id for x in lib.deleted(limit=2)}
    second = {x.id for x in lib.deleted(limit=2, offset=2)}
    assert first and second and not (first & second)


def test_asking_for_no_trash_returns_none_of_it(lib):
    lib.delete(lib.create(b"x", name="a.md").id)
    assert lib.deleted(limit=0) == []


def test_restore_uses_the_same_conditional_bump_as_replace(lib):
    import inspect

    from dream.library import store as store_mod

    src = inspect.getsource(store_mod.Library.restore_version)
    assert "BEGIN IMMEDIATE" in src and "current_version=?" in src


def test_blob_writes_and_purges_share_an_interprocess_lock(lib):
    """Threads and SQLite writers are serialised; the filesystem is not. Without a
    lock both processes can agree on, one can unlink a blob the other is committing a
    reference to."""
    import inspect

    from dream.library import store as store_mod

    assert "flock" in inspect.getsource(store_mod.Library._blob_lock)
    assert "_blob_lock" in inspect.getsource(store_mod.Library._put_blob)
    assert "_blob_lock" in inspect.getsource(store_mod.Library.purge)


def test_purge_reports_bytes_it_could_not_remove(lib):
    """'Gone for good' must not be said when the bytes are still on disk."""
    f = lib.create(b"unique", name="a.md")
    rep = lib.purge(f.id)
    assert "unremovable_blobs" in rep and rep["unremovable_blobs"] == []


def test_the_replace_schema_permits_the_path_route(lib):
    """The description said file_id was optional while the schema marked it required —
    a strict validator would have rejected every path-route call before the handler
    ever ran, making the headline feature unreachable."""
    schema = library_tools.library_replace.input_schema
    assert "file_id" not in (schema.get("required") or [])


def test_materialize_is_atomic(lib, tmp_path):
    """Bytes and identity must land together. Copy-then-stamp on a shared name lets
    two processes interleave and leave one file's bytes wearing the other's stamp."""
    import inspect

    from dream.library import store as store_mod

    src = inspect.getsource(store_mod.Library.materialize)
    assert "os.replace(" in src, "must move a fully-built file into place"
    stamp_at, move_at = src.index("_stamp_file_only"), src.index("os.replace(")
    assert stamp_at < move_at, "the stamp must be applied before the rename"


def test_materialize_leaves_no_fragment_when_it_fails(lib, tmp_path, monkeypatch):
    f = lib.create(b"x", name="a.md")
    dest = tmp_path / "out" / "a.md"

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("dream.library.store.shutil.copyfile", boom)
    with pytest.raises(OSError):
        lib.materialize(f.id, dest)
    leftovers = list((tmp_path / "out").glob("*")) if (tmp_path / "out").exists() else []
    assert leftovers == [], f"left a fragment behind: {leftovers}"


def test_materialize_over_an_existing_file_replaces_it_wholly(lib, tmp_path):
    a = lib.create(b"aaaa", name="a.md")
    b = lib.create(b"bbbb", name="b.md")
    dest = tmp_path / "shared.md"
    lib.materialize(a.id, dest)
    lib.materialize(b.id, dest)
    assert dest.read_bytes() == b"bbbb"
    got = lib.origin(dest)
    assert got[0].id == b.id, "bytes and stamp must agree after an overwrite"


# --- findings from the Grok review pass ---------------------------------------


@pytest.mark.asyncio
async def test_the_path_route_survives_more_than_one_save(lib, tmp_path):
    """The dead end: replace() moved the Library forward without re-stamping the file
    it saved from, so the next save supplied a version that no longer existed and was
    refused — and the refusal told the caller to re-read and retry, which could never
    succeed. An edit loop has to actually loop."""
    f = lib.create(b"v1", name="plan.md")
    out = lib.materialize(f.id, tmp_path / "plan.md")
    for i in range(2, 6):
        out.write_text(f"v{i}", encoding="utf-8")
        res = await library_tools.library_replace.handler({"path": str(out)})
        assert not _is_error(res), f"save {i} was refused: {_text(res)[:120]}"
        assert lib.get(f.id).version == i
        assert lib.origin(out)[1] == i, "the stamp must track the version it just wrote"
    assert lib.read(f.id)[0] == "v5"


def test_taking_a_local_file_in_stamps_it(lib, tmp_path):
    """Identity used to be attached only on the way out, so a file created FROM disk
    resolved to nothing once its id left context — and the next create forked it."""
    src = tmp_path / "plan.md"
    src.write_text("v1", encoding="utf-8")
    f = lib.create(src)
    got = lib.origin(src)
    assert got is not None and got[0].id == f.id


@pytest.mark.asyncio
async def test_a_filesystem_without_xattrs_can_still_use_the_path_route(lib, tmp_path,
                                                                       monkeypatch):
    """A stamp that was never written is not a stamp that went missing. Treating them
    the same voided the guarantee on every mount that refuses xattrs."""
    monkeypatch.setattr("dream.library.store.os.setxattr",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("unsupported")))
    f = lib.create(b"v1", name="plan.md")
    out = lib.materialize(f.id, tmp_path / "plan.md")
    assert lib.origin(out)[2] == "unstampable"
    out.write_text("v2", encoding="utf-8")
    res = await library_tools.library_replace.handler({"path": str(out)})
    assert not _is_error(res), _text(res)[:160]
    assert lib.read(f.id)[0] == "v2"


def test_search_finds_non_ascii_names_and_bodies(lib):
    """The index is unicode61; the query builder was [A-Za-z0-9_]. Every non-English
    document was invisible to the one resolution route that needs no id."""
    f = lib.create("季度収入は上昇".encode(), name="季度.md")
    assert [h.file.id for h in lib.search("季度")] == [f.id]


def test_every_paginator_treats_zero_as_zero(lib):
    lib.create(b"x", name="a.md")
    assert lib.list(limit=0) == []
    assert lib.search("a", top_k=0) == []
    assert lib.find([lib.list()[0].id], "x", max_matches=0) == []


def test_a_binary_tail_past_the_sniff_window_is_still_binary(lib):
    """A plausible text header followed by binary passed an 8 KiB sniff and was
    decoded with errors='replace' straight into a context window."""
    f = lib.create(b"# Notes\n" + b"x" * 9000 + b"\x00\xff\xfe", name="sneaky.md")
    with pytest.raises(LibraryError, match="binary"):
        lib.read(f.id)


def test_materialize_refuses_to_write_over_the_store(lib, tmp_path):
    """`dest` comes from a model. 'Materialize onto the database' is store suicide
    reported as a successful copy."""
    f = lib.create(b"x", name="a.md")
    with pytest.raises(LibraryError, match="own storage"):
        lib.materialize(f.id, lib.db_path)
    with pytest.raises(LibraryError, match="own storage"):
        lib.materialize(f.id, lib.blob_dir / "aa" / "victim")


def test_one_file_never_gets_two_search_rows(lib):
    """Committing the version and indexing afterwards let two writers interleave their
    delete/insert pairs, leaving two fts rows for one file — which made resolve call a
    single document ambiguous and refuse to write to it."""
    f = lib.create(b"quarterly revenue", name="rev.md")
    for i in range(5):
        lib.replace(f.id, f"quarterly revenue v{i}".encode())
    assert len(lib.search("quarterly")) == 1


def test_a_failed_restore_does_not_publish_an_unreadable_version(lib):
    """It committed the version row, then fetched the blob to index it. A missing blob
    raised after current_version had already moved, so the call reported failure while
    leaving the file pointing at something unreadable."""
    f = lib.create(b"v1", name="a.md")
    lib.replace(f.id, b"v2")
    before = lib.get(f.id).version
    for b in lib.blob_dir.rglob("*"):
        if b.is_file() and b.read_bytes() == b"v1":
            b.unlink()
    with pytest.raises(LibraryError):
        lib.restore_version(f.id, 1)
    assert lib.get(f.id).version == before, "a failed restore must not move the version"
    assert lib.read(f.id)[0] == "v2"


@pytest.mark.asyncio
async def test_purge_reports_bytes_it_could_not_actually_delete(lib):
    """The store reports unremovable blobs; the tool used to throw that away and tell
    the user the bytes were gone."""
    import inspect

    src = inspect.getsource(library_tools.library_manage.handler)
    assert "unremovable_blobs" in src
