"""The Library tools as the model meets them.

The store is tested separately; this is about the layer on top — that a refusal
arrives as an error rather than as prose the model might mistake for content, that a
version conflict is labelled clearly enough that the model does not "fix" it by
dropping the guard, and that the ids a tool hands back are the ones the next call
needs.
"""

from __future__ import annotations

import pytest

from dream.library.store import Library
from dream.tools import library_tools


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    store = Library(tmp_path / "library.db", tmp_path / "blobs")
    monkeypatch.setattr(library_tools, "_LIB", store)
    return store


def _text(res: dict) -> str:
    return "".join(b.get("text", "") for b in res.get("content", []))


def _is_error(res: dict) -> bool:
    return bool(res.get("is_error"))


# --- reading -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_listing_an_empty_library_says_so_without_erroring(lib):
    res = await library_tools.library_list.handler({})
    assert not _is_error(res) and "empty" in _text(res).lower()


@pytest.mark.asyncio
async def test_list_shows_the_id_the_next_call_needs(lib):
    f = lib.create(b"x", name="a.md")
    res = await library_tools.library_list.handler({})
    assert f.id in _text(res)


@pytest.mark.asyncio
async def test_list_can_be_scoped_to_a_folder(lib):
    lib.create(b"x", name="a.md", folder="/one")
    lib.create(b"y", name="b.md", folder="/two")
    res = await library_tools.library_list.handler({"folder": "/one"})
    assert "a.md" in _text(res) and "b.md" not in _text(res)


@pytest.mark.asyncio
async def test_an_absurd_limit_is_refused_as_an_error(lib):
    res = await library_tools.library_list.handler({"limit": 99999})
    assert _is_error(res) and "at most" in _text(res)


@pytest.mark.asyncio
async def test_search_returns_hits_with_snippets(lib):
    lib.create(b"quarterly revenue climbed", name="notes.md")
    res = await library_tools.library_search.handler({"query": "revenue"})
    assert "notes.md" in _text(res)


@pytest.mark.asyncio
async def test_a_search_with_no_hits_is_not_an_error(lib):
    """Nothing found is an answer, not a failure — an error would push the model to
    retry a search that will keep succeeding at finding nothing."""
    res = await library_tools.library_search.handler({"query": "absent"})
    assert not _is_error(res) and "No Library file" in _text(res)


@pytest.mark.asyncio
async def test_reading_an_unknown_id_is_an_error(lib):
    res = await library_tools.library_read.handler({"file_id": "nope"})
    assert _is_error(res) and "no Library file" in _text(res)


@pytest.mark.asyncio
async def test_read_returns_the_content(lib):
    f = lib.create(b"the actual text", name="a.md")
    res = await library_tools.library_read.handler({"file_id": f.id})
    assert "the actual text" in _text(res)


@pytest.mark.asyncio
async def test_read_can_reach_an_older_version(lib):
    f = lib.create(b"v1", name="a.md")
    lib.replace(f.id, b"v2")
    res = await library_tools.library_read.handler({"file_id": f.id, "version": 1})
    assert "v1" in _text(res)


@pytest.mark.asyncio
async def test_reading_a_binary_file_is_an_error_pointing_at_materialize(lib):
    f = lib.create(b"\x00\x01\x02binary", name="a.bin")
    res = await library_tools.library_read.handler({"file_id": f.id})
    assert _is_error(res) and "materialize" in _text(res)


@pytest.mark.asyncio
async def test_find_reports_file_and_line(lib):
    f = lib.create(b"alpha\nbeta\n", name="a.md")
    res = await library_tools.library_find.handler(
        {"file_ids": [f.id], "pattern": "beta"})
    assert "a.md:2" in _text(res)


@pytest.mark.asyncio
async def test_find_accepts_a_single_id_as_a_bare_string(lib):
    """Local models routinely send a scalar where an array is declared; losing the
    call over that is worse than accepting it."""
    f = lib.create(b"alpha\nbeta\n", name="a.md")
    res = await library_tools.library_find.handler({"file_ids": f.id, "pattern": "beta"})
    assert not _is_error(res) and "a.md:2" in _text(res)


# --- writing -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_from_content_needs_a_name(lib):
    res = await library_tools.library_create.handler({"content": "hello"})
    assert _is_error(res) and "name" in _text(res)


@pytest.mark.asyncio
async def test_create_with_neither_path_nor_content_is_refused(lib):
    res = await library_tools.library_create.handler({"name": "a.md"})
    assert _is_error(res) and "path" in _text(res)


@pytest.mark.asyncio
async def test_create_from_content_works_and_returns_an_id(lib):
    res = await library_tools.library_create.handler(
        {"content": "hello", "name": "a.md", "folder": "/notes"})
    assert not _is_error(res)
    assert "/notes/a.md" in _text(res)
    assert lib.list()[0].name == "a.md"


@pytest.mark.asyncio
async def test_create_from_a_local_path(lib, tmp_path):
    src = tmp_path / "src.md"
    src.write_text("from disk", encoding="utf-8")
    res = await library_tools.library_create.handler({"path": str(src)})
    assert not _is_error(res) and "src.md" in _text(res)


@pytest.mark.asyncio
async def test_replace_keeps_the_id_and_bumps_the_version(lib):
    f = lib.create(b"v1", name="a.md")
    res = await library_tools.library_replace.handler(
        {"file_id": f.id, "content": "v2"})
    assert not _is_error(res) and "v2" in _text(res)
    assert lib.get(f.id).version == 2
    assert lib.read(f.id)[0] == "v2"


@pytest.mark.asyncio
async def test_a_version_conflict_is_labelled_so_the_model_does_not_drop_the_guard(lib):
    """The failure mode this guards against: a model reads 'error', assumes the guard
    is the problem, and retries without it — overwriting the other writer."""
    f = lib.create(b"v1", name="a.md")
    lib.replace(f.id, b"theirs")
    res = await library_tools.library_replace.handler(
        {"file_id": f.id, "content": "mine", "expected_current_version": 1})
    assert _is_error(res)
    body = _text(res)
    assert "conflict" in body.lower()
    assert "do NOT retry without the version guard" in body
    assert lib.read(f.id)[0] == "theirs"


@pytest.mark.asyncio
async def test_replace_with_nothing_to_write_is_refused(lib):
    f = lib.create(b"v1", name="a.md")
    res = await library_tools.library_replace.handler({"file_id": f.id})
    assert _is_error(res)


@pytest.mark.asyncio
async def test_materialize_writes_the_file_and_says_to_replace_the_same_id(lib, tmp_path):
    f = lib.create(b"content", name="a.md")
    dest = tmp_path / "work" / "a.md"
    res = await library_tools.library_materialize.handler(
        {"file_id": f.id, "dest": str(dest)})
    assert not _is_error(res)
    assert dest.read_bytes() == b"content"
    assert "replace the same library_file_id" in _text(res)


# --- managing ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unknown_action_lists_the_real_ones(lib):
    f = lib.create(b"x", name="a.md")
    res = await library_tools.library_manage.handler(
        {"action": "obliterate", "file_id": f.id})
    assert _is_error(res) and "rename" in _text(res)


@pytest.mark.asyncio
async def test_rename_without_a_name_is_refused(lib):
    f = lib.create(b"x", name="a.md")
    res = await library_tools.library_manage.handler({"action": "rename", "file_id": f.id})
    assert _is_error(res)


@pytest.mark.asyncio
async def test_rename_and_move_preserve_identity(lib):
    f = lib.create(b"x", name="a.md")
    await library_tools.library_manage.handler(
        {"action": "rename", "file_id": f.id, "name": "b.md"})
    await library_tools.library_manage.handler(
        {"action": "move", "file_id": f.id, "folder": "/elsewhere"})
    got = lib.get(f.id)
    assert got.id == f.id and got.name == "b.md" and got.folder == "/elsewhere"


@pytest.mark.asyncio
async def test_delete_tells_the_model_it_is_recoverable(lib):
    f = lib.create(b"x", name="a.md")
    res = await library_tools.library_manage.handler({"action": "delete", "file_id": f.id})
    assert not _is_error(res)
    assert "recoverable" in _text(res) and "nothing was erased" in _text(res)


@pytest.mark.asyncio
async def test_undelete_brings_it_back(lib):
    f = lib.create(b"x", name="a.md")
    await library_tools.library_manage.handler({"action": "delete", "file_id": f.id})
    res = await library_tools.library_manage.handler({"action": "undelete", "file_id": f.id})
    assert not _is_error(res) and lib.list()[0].id == f.id


@pytest.mark.asyncio
async def test_versions_are_listed_newest_first(lib):
    f = lib.create(b"v1", name="a.md")
    lib.replace(f.id, b"v2")
    res = await library_tools.library_manage.handler({"action": "versions", "file_id": f.id})
    body = _text(res)
    assert body.index("v2") < body.index("v1")


@pytest.mark.asyncio
async def test_restore_needs_a_version(lib):
    f = lib.create(b"x", name="a.md")
    res = await library_tools.library_manage.handler(
        {"action": "restore_version", "file_id": f.id})
    assert _is_error(res) and "version" in _text(res)


@pytest.mark.asyncio
async def test_restore_appends_rather_than_rewriting(lib):
    f = lib.create(b"v1", name="a.md")
    lib.replace(f.id, b"v2")
    res = await library_tools.library_manage.handler(
        {"action": "restore_version", "file_id": f.id, "version": 1})
    assert not _is_error(res)
    assert lib.get(f.id).version == 3
    assert lib.read(f.id)[0] == "v1"
    assert lib.read(f.id, version=2)[0] == "v2"   # what we undid is still there


# --- the tools are wired in --------------------------------------------------


def test_every_library_tool_is_registered_and_classified():
    from dream.core import policy
    from dream.tools import registry

    names = set(registry.build()["names"])
    expected = {t.name for t in library_tools.LIBRARY_TOOLS}
    assert expected <= names
    # Reads are free; writes are gated. materialize writes a caller-chosen local path,
    # so it is a filesystem write, not merely a Library mutation.
    assert policy.capability("library_read") == policy.READONLY
    assert policy.capability("library_search") == policy.READONLY
    assert policy.capability("library_materialize") == policy.WRITE
    assert policy.capability("library_replace") not in policy.AUTO_CAPS
    assert policy.capability("library_manage") not in policy.AUTO_CAPS
