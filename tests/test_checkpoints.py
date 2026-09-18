"""Checkpoints: Dream's undo for its own edits. Content-addressed snapshots in
Dream's own var/ — never a commit, a stash, or an index entry in the user's repo, and
identical in a directory that isn't a repo at all."""

from pathlib import Path

import pytest

from dream.core.checkpoints import CheckpointStore


def _store(tmp_path: Path, **kw) -> CheckpointStore:
    return CheckpointStore(tmp_path / "store", session="sess-1", **kw)


def _blobs(st: CheckpointStore) -> int:
    return len([p for p in st.objects.rglob("*") if p.is_file()])


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


# --- round trip --------------------------------------------------------------


def test_take_and_restore_round_trips_content(tmp_path):
    ws = _ws(tmp_path)
    f = ws / "a.py"
    f.write_text("original\n")
    st = _store(tmp_path)

    cid = st.take("turn 1", [f])
    f.write_text("dream's edit\n")
    st.seal(cid)

    rep = st.restore(cid)
    assert f.read_text() == "original\n"
    assert rep.restored == [str(f)]
    assert rep.skipped == []


def test_restore_reports_a_file_it_did_not_need_to_touch(tmp_path):
    ws = _ws(tmp_path)
    f = ws / "a.py"
    f.write_text("original\n")
    st = _store(tmp_path)

    cid = st.take("turn 1", [f])
    st.seal(cid)  # the write never landed — the file is still the snapshot

    rep = st.restore(cid)
    assert rep.unchanged == [str(f)]
    assert rep.restored == []


def test_take_records_the_state_before_the_write_not_after(tmp_path):
    ws = _ws(tmp_path)
    f = ws / "a.py"
    f.write_text("v1\n")
    st = _store(tmp_path)

    cid = st.take("turn 1", [f])
    f.write_text("v2\n")
    st.take("turn 1", [f], into=cid)  # same file touched twice in one turn
    f.write_text("v3\n")
    st.seal(cid)

    st.restore(cid)
    assert f.read_text() == "v1\n"  # the FIRST observation is the pre-turn state


# --- content addressing ------------------------------------------------------


def test_unchanged_file_is_deduplicated(tmp_path):
    ws = _ws(tmp_path)
    f = ws / "a.py"
    f.write_text("same\n")
    st = _store(tmp_path)

    st.take("turn 1", [f])
    after_first = _blobs(st)
    st.take("turn 2", [f])
    assert _blobs(st) == after_first  # nothing changed → nothing stored

    f.write_text("different\n")
    st.take("turn 3", [f])
    assert _blobs(st) == after_first + 1


# --- creation and deletion ---------------------------------------------------


def test_a_file_the_turn_created_is_removed_on_restore(tmp_path):
    ws = _ws(tmp_path)
    f = ws / "new.py"
    st = _store(tmp_path)

    cid = st.take("turn 1", [f])  # nothing there yet
    f.write_text("brand new\n")
    st.seal(cid)

    rep = st.restore(cid)
    assert not f.exists()
    assert rep.deleted == [str(f)]


def test_a_file_the_turn_deleted_is_recreated_on_restore(tmp_path):
    ws = _ws(tmp_path)
    f = ws / "doomed.sh"
    f.write_text("#!/bin/sh\necho hi\n")
    f.chmod(0o755)
    st = _store(tmp_path)

    cid = st.take("turn 1", [f])
    f.unlink()
    st.seal(cid)

    rep = st.restore(cid)
    assert f.read_text() == "#!/bin/sh\necho hi\n"
    assert f.stat().st_mode & 0o111  # the executable bit came back too
    assert rep.restored == [str(f)]


# --- never clobber a concurrent edit -----------------------------------------


def test_restore_refuses_a_file_changed_after_the_snapshot(tmp_path):
    ws = _ws(tmp_path)
    f = ws / "a.py"
    f.write_text("original\n")
    st = _store(tmp_path)

    cid = st.take("turn 1", [f])
    f.write_text("dream's edit\n")
    st.seal(cid)
    f.write_text("THE USER'S OWN EDIT\n")  # in their editor, after the turn

    rep = st.restore(cid)
    assert f.read_text() == "THE USER'S OWN EDIT\n"  # untouched
    assert rep.restored == []
    assert len(rep.skipped) == 1
    path, why = rep.skipped[0]
    assert path == str(f)
    assert "changed" in why.lower()
    assert "changed" in rep.summary().lower()

    forced = st.restore(cid, force=True)
    assert f.read_text() == "original\n"
    assert forced.restored == [str(f)]


def test_an_unsealed_checkpoint_is_not_restored_silently(tmp_path):
    # Without the post-turn observation there is no way to tell Dream's write from
    # an edit of the user's, so restore says so instead of guessing.
    ws = _ws(tmp_path)
    f = ws / "a.py"
    f.write_text("original\n")
    st = _store(tmp_path)

    cid = st.take("turn 1", [f])
    f.write_text("someone changed it\n")

    rep = st.restore(cid)
    assert f.read_text() == "someone changed it\n"
    assert rep.restored == []
    assert rep.skipped and "post-turn" in rep.skipped[0][1]

    assert st.restore(cid, force=True).restored == [str(f)]
    assert f.read_text() == "original\n"


# --- bounded -----------------------------------------------------------------


def test_prune_keeps_the_newest_n(tmp_path):
    ws = _ws(tmp_path)
    st = _store(tmp_path, limit=3)
    ids = []
    for i in range(6):
        f = ws / f"f{i}.txt"
        f.write_text(f"body {i}\n")
        ids.append(st.take(f"turn {i}", [f]))

    kept = [cp["id"] for cp in st.list()]
    assert kept == ids[-3:][::-1]  # newest first, oldest dropped
    assert _blobs(st) == 3  # blobs nothing references any more are gone too

    with pytest.raises(KeyError):
        st.restore(ids[0])


def test_prune_is_a_no_op_below_the_limit(tmp_path):
    ws = _ws(tmp_path)
    f = ws / "a.py"
    f.write_text("x\n")
    st = _store(tmp_path, limit=10)
    st.take("turn 1", [f])
    assert st.prune() == 0
    assert len(st.list()) == 1


# --- git is the user's, not Dream's ------------------------------------------


def test_git_internals_are_never_snapshotted_or_written(tmp_path):
    ws = _ws(tmp_path)
    git_cfg = ws / ".git" / "config"
    git_cfg.parent.mkdir()
    git_cfg.write_text("[core]\n\trepositoryformatversion = 0\n")
    src = ws / "a.py"
    src.write_text("original\n")
    st = _store(tmp_path)

    cid = st.take("turn 1", [git_cfg, src])
    git_cfg.write_text("TAMPERED\n")
    src.write_text("dream's edit\n")
    st.seal(cid)

    rep = st.restore(cid)
    assert git_cfg.read_text() == "TAMPERED\n"  # restore left .git alone
    assert src.read_text() == "original\n"
    assert [p for p, _ in rep.skipped] == [str(git_cfg)]
    assert ".git" in rep.skipped[0][1]


def test_a_plain_directory_behaves_identically(tmp_path):
    # No repo anywhere: no .git in the workspace, and Dream's store never makes one.
    ws = _ws(tmp_path)
    f = ws / "notes.md"
    f.write_text("draft\n")
    st = _store(tmp_path)

    cid = st.take("turn 1", [f])
    f.write_text("mangled\n")
    st.seal(cid)
    assert st.restore(cid).restored == [str(f)]
    assert f.read_text() == "draft\n"
    assert not list(tmp_path.rglob(".git"))


# --- listing and diffing -----------------------------------------------------


def test_list_is_newest_first_with_labels(tmp_path):
    ws = _ws(tmp_path)
    a, b = ws / "a.py", ws / "b.py"
    a.write_text("a\n")
    b.write_text("b\n")
    st = _store(tmp_path)

    first = st.take("fix the parser", [a])
    st.seal(first)
    second = st.take("rename the module", [a, b])

    rows = st.list()
    assert [r["id"] for r in rows] == [second, first]
    assert rows[0]["label"] == "rename the module"
    assert rows[0]["files"] == 2
    assert rows[0]["sealed"] is False and rows[1]["sealed"] is True
    assert rows[1]["session"] == "sess-1"


def test_diff_shows_what_changed_since_the_snapshot(tmp_path):
    ws = _ws(tmp_path)
    f = ws / "a.py"
    f.write_text("keep\ndrop\n")
    st = _store(tmp_path)

    cid = st.take("turn 1", [f])
    f.write_text("keep\nadded\n")

    out = st.diff(cid)
    assert "-drop" in out and "+added" in out and str(f) in out


def test_short_ids_are_accepted(tmp_path):
    ws = _ws(tmp_path)
    f = ws / "a.py"
    f.write_text("x\n")
    st = _store(tmp_path)
    cid = st.take("turn 1", [f])
    assert st.list()[0]["id"] == cid
    assert st.diff(cid.lstrip("0"))  # "0001" typed back as "1"


def test_unknown_checkpoint_raises(tmp_path):
    st = _store(tmp_path)
    with pytest.raises(KeyError):
        st.restore("9999")


# --- oddities are reported, never guessed at ---------------------------------


def test_a_symlink_is_skipped_rather_than_followed(tmp_path):
    ws = _ws(tmp_path)
    target = ws / "real.txt"
    target.write_text("target\n")
    link = ws / "link.txt"
    link.symlink_to(target)
    st = _store(tmp_path)

    cid = st.take("turn 1", [link])
    st.seal(cid)
    rep = st.restore(cid)
    assert target.read_text() == "target\n"
    assert rep.skipped and "symlink" in rep.skipped[0][1]


def test_a_file_too_big_to_snapshot_is_reported_not_dropped(tmp_path):
    ws = _ws(tmp_path)
    big = ws / "big.bin"
    big.write_bytes(b"x" * 4096)
    st = _store(tmp_path, max_bytes=1024)

    cid = st.take("turn 1", [big])
    big.write_bytes(b"y" * 4096)
    st.seal(cid)

    rep = st.restore(cid)
    assert big.read_bytes() == b"y" * 4096
    assert rep.skipped and "limit" in rep.skipped[0][1]


def test_an_unreadable_file_does_not_cost_the_others_their_snapshot(tmp_path):
    """take() over several paths must not be all-or-nothing: one PermissionError
    used to throw the WHOLE checkpoint away, so a multi-file turn proceeded with
    no undo record for any of its files."""
    ws = _ws(tmp_path)
    good = ws / "good.py"
    good.write_text("original\n")
    locked = ws / "locked.py"
    locked.write_text("secret\n")
    locked.chmod(0o000)
    st = _store(tmp_path)
    try:
        cid = st.take("turn 1", [good, locked])
        good.write_text("dream's edit\n")
        st.seal(cid)

        rep = st.restore(cid)
        assert good.read_text() == "original\n"  # the readable file kept its undo
        assert rep.restored == [str(good)]
        assert rep.skipped and str(locked) in rep.skipped[0][0]
    finally:
        locked.chmod(0o644)


def test_one_failing_restore_does_not_abort_the_rest(tmp_path):
    """A file whose put-back fails (parent made read-only after the turn) is
    reported; the other files still restore."""
    ws = _ws(tmp_path)
    sub = ws / "sub"
    sub.mkdir()
    inner = sub / "inner.py"
    inner.write_text("inner v1\n")
    outer = ws / "outer.py"
    outer.write_text("outer v1\n")
    st = _store(tmp_path)

    cid = st.take("turn 1", [inner, outer])
    inner.write_text("inner v2\n")
    outer.write_text("outer v2\n")
    st.seal(cid)

    sub.chmod(0o555)  # restore can't write the tmp file next to inner
    try:
        rep = st.restore(cid)
        assert outer.read_text() == "outer v1\n"
        assert str(outer) in rep.restored
        assert rep.skipped and str(inner) in rep.skipped[0][0]
        assert "restore failed" in rep.skipped[0][1]
    finally:
        sub.chmod(0o755)


# --- the tools the model reaches for -----------------------------------------


async def test_checkpoint_tools_list_and_restore(tmp_path, monkeypatch):
    from dream import config
    from dream.tools import checkpoint_tools

    monkeypatch.setattr(config, "VAR_DIR", tmp_path / "var")
    ws = _ws(tmp_path)
    f = ws / "a.py"
    f.write_text("original\n")

    st = checkpoint_tools._store()
    cid = st.take("fix the parser", [f])
    f.write_text("dream's edit\n")
    st.seal(cid)

    listed = await checkpoint_tools.checkpoint_list.handler({})
    text = listed["content"][0]["text"]
    assert cid in text and "fix the parser" in text

    out = await checkpoint_tools.checkpoint_restore.handler({"checkpoint_id": cid})
    assert not out.get("is_error")
    assert f.read_text() == "original\n"
    assert "a.py" in out["content"][0]["text"]

    missing = await checkpoint_tools.checkpoint_restore.handler({"checkpoint_id": "4242"})
    assert missing.get("is_error")


async def test_checkpoint_list_says_when_there_is_nothing(tmp_path, monkeypatch):
    from dream import config
    from dream.tools import checkpoint_tools

    monkeypatch.setattr(config, "VAR_DIR", tmp_path / "var")
    out = await checkpoint_tools.checkpoint_list.handler({})
    assert "No checkpoints" in out["content"][0]["text"]
