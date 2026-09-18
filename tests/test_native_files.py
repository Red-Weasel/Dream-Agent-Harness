"""Phase 3 — files the model can trust: the six file tools and their policy classes."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from dream.core import policy
from dream.tools import context as tool_context
from dream.tools.context import ToolContext, set_context
from dream.tools.files import (
    FILE_TOOLS, _GREP_MAX_HITS, copy_files, delete_file, grep, image_metadata,
    sleep, str_replace_edit,
)
from dream.tools.native import NATIVE_TOOLS

@pytest.fixture
def ws(tmp_path):
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=tmp_path))
    yield tmp_path
    tool_context._CTX = None


def _text(res) -> str:
    return res["content"][0]["text"]


def _failed(res) -> bool:
    return bool(res.get("is_error"))


# --- str_replace_edit: exact-once, atomic batches ------------------------------------


@pytest.mark.asyncio
async def test_str_replace_edits_a_unique_match(ws):
    f = ws / "a.py"
    f.write_text("x = 1\ny = 2\n")
    res = await str_replace_edit.handler({"path": "a.py", "old_string": "y = 2", "new_string": "y = 3"})
    assert not _failed(res) and "1 replacement" in _text(res)
    assert f.read_text() == "x = 1\ny = 3\n"


@pytest.mark.asyncio
async def test_str_replace_refuses_zero_and_many_matches_naming_the_count(ws):
    f = ws / "a.txt"
    f.write_text("dup\ndup\n")
    zero = await str_replace_edit.handler({"path": "a.txt", "old_string": "nope", "new_string": "x"})
    assert _failed(zero) and "matches 0 time(s)" in _text(zero) and "Nothing was changed" in _text(zero)
    assert "Read the current file" in _text(zero)
    assert "Widen" not in _text(zero)
    many = await str_replace_edit.handler({"path": "a.txt", "old_string": "dup", "new_string": "x"})
    assert _failed(many) and "matches 2 time(s)" in _text(many)
    assert "Widen" in _text(many)
    assert f.read_text() == "dup\ndup\n"


@pytest.mark.asyncio
async def test_str_replace_batch_is_atomic(ws):
    f = ws / "a.txt"
    f.write_text("one\ntwo\n")
    res = await str_replace_edit.handler({"path": "a.txt", "edits": [
        {"old_string": "one", "new_string": "1"},
        {"old_string": "missing", "new_string": "2"},
    ]})
    assert _failed(res) and "edits[1]" in _text(res)
    assert f.read_text() == "one\ntwo\n", "a failed later edit must not leave the earlier one applied"


@pytest.mark.asyncio
async def test_str_replace_batch_edits_apply_in_order_and_may_build_on_each_other(ws):
    f = ws / "a.txt"
    f.write_text("alpha\n")
    res = await str_replace_edit.handler({"path": "a.txt", "edits": [
        {"old_string": "alpha", "new_string": "beta"},
        {"old_string": "beta", "new_string": "gamma"},
    ]})
    assert not _failed(res) and "2 replacement" in _text(res)
    assert f.read_text() == "gamma\n"


@pytest.mark.asyncio
async def test_str_replace_refuses_both_forms_and_empty_or_identical_strings(ws):
    (ws / "a.txt").write_text("a\n")
    both = await str_replace_edit.handler({"path": "a.txt", "old_string": "a", "new_string": "b",
                                           "edits": [{"old_string": "a", "new_string": "b"}]})
    assert _failed(both) and "not both" in _text(both)
    empty = await str_replace_edit.handler({"path": "a.txt", "old_string": "", "new_string": "b"})
    assert _failed(empty) and "empty" in _text(empty)
    same = await str_replace_edit.handler({"path": "a.txt", "old_string": "a", "new_string": "a"})
    assert _failed(same) and "identical" in _text(same)
    assert (ws / "a.txt").read_text() == "a\n"


@pytest.mark.asyncio
async def test_str_replace_refuses_a_missing_file_and_binary_content(ws):
    res = await str_replace_edit.handler({"path": "nope.txt", "old_string": "a", "new_string": "b"})
    assert _failed(res) and "No file" in _text(res)
    (ws / "bin").write_bytes(b"\xff\xfe\x00\x01")
    res = await str_replace_edit.handler({"path": "bin", "old_string": "a", "new_string": "b"})
    assert _failed(res) and "not UTF-8" in _text(res)


# --- grep: path, line, context; caps; scope; skips -----------------------------------


@pytest.mark.asyncio
async def test_grep_returns_path_line_and_two_lines_of_context_case_insensitive(ws):
    (ws / "src").mkdir()
    (ws / "src" / "m.py").write_text("l1\nl2\nNeedle here\nl4\nl5\nl6\n")
    res = await grep.handler({"pattern": "needle"})
    out = _text(res)
    assert not _failed(res)
    assert "src/m.py:3" in out
    assert "> 3: Needle here" in out
    assert "1: l1" in out and "5: l5" in out and "6: l6" not in out


@pytest.mark.asyncio
async def test_grep_reports_no_matches_plainly(ws):
    (ws / "a.txt").write_text("nothing\n")
    res = await grep.handler({"pattern": "zzz"})
    assert not _failed(res) and _text(res).startswith("No matches")


@pytest.mark.asyncio
async def test_grep_caps_at_100_and_says_to_narrow(ws):
    (ws / "big.txt").write_text("hit\n" * 250)
    out = _text(await grep.handler({"pattern": "hit"}))
    assert out.count("big.txt:") == _GREP_MAX_HITS
    assert "capped at 100" in out and "narrow" in out


@pytest.mark.asyncio
async def test_grep_scopes_to_a_path_and_skips_vendor_and_hidden_dirs(ws):
    (ws / "a").mkdir(); (ws / "b").mkdir()
    (ws / "a" / "x.txt").write_text("token\n")
    (ws / "b" / "y.txt").write_text("token\n")
    for d in (".git", ".venv", "node_modules", "__pycache__"):
        (ws / d).mkdir()
        (ws / d / "z.txt").write_text("token\n")
    out = _text(await grep.handler({"pattern": "token", "path": "a"}))
    assert "a/x.txt" in out and "b/y.txt" not in out
    out = _text(await grep.handler({"pattern": "token"}))
    assert "a/x.txt" in out and "b/y.txt" in out
    assert ".git" not in out and ".venv" not in out and "node_modules" not in out
    # a file path searches just that file
    out = _text(await grep.handler({"pattern": "token", "path": "b/y.txt"}))
    assert "b/y.txt:1" in out and "a/x.txt" not in out


@pytest.mark.asyncio
async def test_grep_skips_binaries_and_rejects_a_bad_regex(ws):
    (ws / "img.bin").write_bytes(b"token\x00binary")
    (ws / "t.txt").write_text("token\n")
    out = _text(await grep.handler({"pattern": "token"}))
    assert "t.txt" in out and "img.bin" not in out
    bad = await grep.handler({"pattern": "("})
    assert _failed(bad) and "Bad regex" in _text(bad)


# --- copy_files / delete_file ----------------------------------------------------------


@pytest.mark.asyncio
async def test_copy_files_copies_files_and_folders_and_moves(ws):
    (ws / "f.txt").write_text("hi")
    (ws / "d" / "sub").mkdir(parents=True)
    (ws / "d" / "sub" / "n.txt").write_text("nested")
    (ws / "m.txt").write_text("moving")
    res = await copy_files.handler({"files": [
        {"src": "f.txt", "dest": "out/f2.txt"},
        {"src": "d", "dest": "out/d2"},
        {"src": "m.txt", "dest": "out/m2.txt", "move": True},
    ]})
    assert not _failed(res)
    assert (ws / "out" / "f2.txt").read_text() == "hi" and (ws / "f.txt").exists()
    assert (ws / "out" / "d2" / "sub" / "n.txt").read_text() == "nested"
    assert (ws / "out" / "m2.txt").read_text() == "moving" and not (ws / "m.txt").exists()
    assert "moved" in _text(res) and "copied" in _text(res)


@pytest.mark.asyncio
async def test_copy_files_refuses_a_missing_source_before_touching_anything(ws):
    (ws / "f.txt").write_text("hi")
    res = await copy_files.handler({"files": [
        {"src": "f.txt", "dest": "out/f2.txt"},
        {"src": "ghost", "dest": "out/g"},
    ]})
    assert _failed(res) and "files[1]" in _text(res) and "Nothing was copied" in _text(res)
    assert not (ws / "out").exists()


@pytest.mark.asyncio
async def test_copy_files_refuses_a_folder_into_itself(ws):
    (ws / "d").mkdir()
    res = await copy_files.handler({"files": [{"src": "d", "dest": "d/inner"}]})
    assert _failed(res) and "into itself" in _text(res)


@pytest.mark.asyncio
async def test_delete_file_removes_files_and_folders_recursively_and_lists_them(ws):
    (ws / "f.txt").write_text("x")
    (ws / "d" / "sub").mkdir(parents=True)
    (ws / "d" / "sub" / "n.txt").write_text("y")
    res = await delete_file.handler({"paths": ["f.txt", "d"]})
    assert not _failed(res)
    assert not (ws / "f.txt").exists() and not (ws / "d").exists()
    out = _text(res)
    assert out.startswith("Deleted:") and "f.txt" in out and "d/ (folder, recursive)" in out


@pytest.mark.asyncio
async def test_delete_file_refuses_a_missing_path_and_removes_nothing(ws):
    (ws / "keep.txt").write_text("x")
    res = await delete_file.handler({"paths": ["keep.txt", "ghost"]})
    assert _failed(res) and "ghost" in _text(res) and "Nothing was deleted" in _text(res)
    assert (ws / "keep.txt").exists()


@pytest.mark.asyncio
async def test_delete_file_unlinks_a_symlink_and_never_follows_it(ws):
    """Gate 3 finding 1: the link goes, the target stays — for files, folders, and
    a dangling link."""
    real_dir = ws / "real_dir"; real_dir.mkdir(); (real_dir / "precious.txt").write_text("keep")
    real_f = ws / "real_f.txt"; real_f.write_text("keep")
    (ws / "sl_dir").symlink_to(real_dir, target_is_directory=True)
    (ws / "sl_f").symlink_to(real_f)
    (ws / "dangling").symlink_to(ws / "nowhere")
    res = await delete_file.handler({"paths": ["sl_dir", "sl_f", "dangling"]})
    assert not _failed(res), _text(res)
    assert (real_dir / "precious.txt").read_text() == "keep" and real_f.read_text() == "keep"
    for name in ("sl_dir", "sl_f", "dangling"):
        assert not (ws / name).exists() and not (ws / name).is_symlink(), name
    assert _text(res).count("symlink; target left in place") == 3


@pytest.mark.asyncio
async def test_delete_file_refuses_the_workspace_itself(ws):
    (ws / "a.txt").write_text("x")
    for raw in (".", str(ws), "sub/.."):
        (ws / "sub").mkdir(exist_ok=True)
        res = await delete_file.handler({"paths": [raw]})
        assert _failed(res) and "workspace itself" in _text(res), raw
    assert (ws / "a.txt").exists()


@pytest.mark.asyncio
async def test_str_replace_keeps_crlf_line_endings(ws):
    f = ws / "w.txt"
    f.write_bytes(b"x = 1\r\ny = 2\r\n")
    res = await str_replace_edit.handler({"path": "w.txt", "old_string": "y = 2", "new_string": "y = 3"})
    assert not _failed(res)
    assert f.read_bytes() == b"x = 1\r\ny = 3\r\n"
    res = await str_replace_edit.handler({"path": "w.txt", "old_string": "x = 1\r\n", "new_string": "x = 0\r\n"})
    assert not _failed(res) and f.read_bytes() == b"x = 0\r\ny = 3\r\n"


@pytest.mark.asyncio
async def test_str_replace_hints_at_crlf_when_a_multiline_old_string_misses(ws):
    f = ws / "w.txt"
    f.write_bytes(b"x = 1\r\ny = 2\r\n")
    res = await str_replace_edit.handler({"path": "w.txt", "old_string": "x = 1\ny = 2", "new_string": "z"})
    assert _failed(res) and "matches 0 time(s)" in _text(res) and "CRLF" in _text(res)
    assert f.read_bytes() == b"x = 1\r\ny = 2\r\n"


@pytest.mark.asyncio
async def test_delete_file_never_deletes_outside_the_workspace(ws, tmp_path_factory):
    """Defense in depth beyond the policy: `..`, an absolute outside path, and a
    link that LIVES outside are all refused; a link inside that POINTS outside is
    unlinked (the link is inside, the target is untouched)."""
    outside = tmp_path_factory.mktemp("outside")
    (outside / "victim.txt").write_text("keep")
    (ws / "a.txt").write_text("x")
    for raw in ("..", str(outside), str(outside / "victim.txt")):
        res = await delete_file.handler({"paths": [raw]})
        assert _failed(res) and "outside the workspace" in _text(res), raw
    assert (ws / "a.txt").exists() and (outside / "victim.txt").read_text() == "keep"
    (ws / "to_outside").symlink_to(outside, target_is_directory=True)
    res = await delete_file.handler({"paths": ["to_outside"]})
    assert not _failed(res) and (outside / "victim.txt").read_text() == "keep"
    assert not (ws / "to_outside").is_symlink()


@pytest.mark.asyncio
async def test_str_replace_counts_overlapping_matches_as_ambiguous(ws):
    f = ws / "o.txt"
    f.write_text("aaa")
    res = await str_replace_edit.handler({"path": "o.txt", "old_string": "aa", "new_string": "B"})
    assert _failed(res) and "matches 2 time(s)" in _text(res)
    assert f.read_text() == "aaa"


# --- image_metadata / sleep -------------------------------------------------------------


@pytest.mark.asyncio
async def test_image_metadata_scans_alpha_for_real_transparency(ws):
    opaque = ws / "o.png"
    Image.new("RGBA", (4, 3), (255, 0, 0, 255)).save(opaque)
    out = _text(await image_metadata.handler({"path": "o.png"}))
    assert "format: PNG" in out and "size: 4×3" in out
    assert "transparency supported: yes" in out and "has transparent pixels: no" in out
    assert "animated: no" in out
    im = Image.new("RGBA", (4, 3), (255, 0, 0, 255)); im.putpixel((0, 0), (0, 0, 0, 0))
    im.save(ws / "t.png")
    assert "has transparent pixels: yes" in _text(await image_metadata.handler({"path": "t.png"}))
    Image.new("RGB", (2, 2)).save(ws / "j.jpg")
    out = _text(await image_metadata.handler({"path": "j.jpg"}))
    assert "format: JPEG" in out and "transparency supported: no" in out


@pytest.mark.asyncio
async def test_image_metadata_reports_animation_frame_count(ws):
    frames = [Image.new("RGB", (2, 2), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    frames[0].save(ws / "a.gif", save_all=True, append_images=frames[1:], duration=50, loop=0)
    out = _text(await image_metadata.handler({"path": "a.gif"}))
    assert "format: GIF" in out and "animated: yes (3 frames)" in out


@pytest.mark.asyncio
async def test_image_metadata_reads_svg_dimensions_from_attributes_or_viewbox(ws):
    (ws / "a.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80"></svg>')
    out = _text(await image_metadata.handler({"path": "a.svg"}))
    assert "format: SVG" in out and "120×80" in out and "width/height" in out
    (ws / "v.svg").write_text('<?xml version="1.0"?>\n<svg viewBox="0 0 300 150"></svg>')
    out = _text(await image_metadata.handler({"path": "v.svg"}))
    assert "300×150" in out and "viewBox" in out


@pytest.mark.asyncio
async def test_image_metadata_refuses_a_non_image(ws):
    (ws / "t.txt").write_text("hello")
    res = await image_metadata.handler({"path": "t.txt"})
    assert _failed(res) and "Could not read" in _text(res)
    (ws / "fake.svg").write_text("just text, no svg here")
    res = await image_metadata.handler({"path": "fake.svg"})
    assert _failed(res) and "no <svg> root" in _text(res)


@pytest.mark.asyncio
async def test_sleep_is_bounded(ws):
    assert _failed(await sleep.handler({"seconds": 0}))
    assert _failed(await sleep.handler({"seconds": 61}))
    assert _failed(await sleep.handler({"seconds": "soon"}))
    assert _failed(await sleep.handler({"seconds": True}))
    res = await sleep.handler({"seconds": 0.01})
    assert not _failed(res) and "Slept" in _text(res)


# --- criterion 5: policy classes -----------------------------------------------------------


WS = Path("/tmp/ws-files")


def test_each_tool_lands_in_its_class():
    for name in ("grep", "image_metadata", "sleep"):
        assert policy.capability(name) == policy.READONLY, name
    for name in ("str_replace_edit", "copy_files"):
        assert policy.capability(name) == policy.WRITE, name
    assert policy.capability("delete_file") == policy.DESTRUCTIVE


def test_auto_mode_never_prompts_for_reads_and_always_gates_delete():
    for name, inp in (("grep", {"pattern": "x"}), ("image_metadata", {"path": "/etc/x.png"}),
                      ("sleep", {"seconds": 1})):
        for mode in policy.MODES:
            assert policy.decide(name, inp, mode, WS)[0] == "allow", (name, mode)
    inp = {"paths": ["inside.txt"]}
    for mode in ("ask", "accept-edits", "auto"):
        d, why = policy.decide("delete_file", inp, mode, WS)
        assert d == "ask" and why == "deletes: inside.txt", (mode, why)
    assert policy.decide("delete_file", inp, "plan", WS)[0] == "deny"


def test_edits_and_copies_obey_the_workspace_boundary_in_every_mode():
    inside = {"path": "a.py", "old_string": "x", "new_string": "y"}
    assert policy.decide("str_replace_edit", inside, "auto", WS)[0] == "allow"
    assert policy.decide("str_replace_edit", inside, "accept-edits", WS)[0] == "allow"
    assert policy.decide("str_replace_edit", inside, "ask", WS)[0] == "ask"
    assert policy.decide("str_replace_edit", inside, "plan", WS)[0] == "deny"
    outside = {"path": "/etc/hosts", "old_string": "x", "new_string": "y"}
    d, why = policy.decide("str_replace_edit", outside, "auto", WS)
    assert d == "ask" and why.startswith("outside workspace")
    ok_copy = {"files": [{"src": "a", "dest": "b"}]}
    assert policy.decide("copy_files", ok_copy, "auto", WS)[0] == "allow"
    for bad in ({"files": [{"src": "a", "dest": "/tmp/elsewhere/b"}]},
                {"files": [{"src": "/etc/hosts", "dest": "b", "move": True}]}):
        d, why = policy.decide("copy_files", bad, "auto", WS)
        assert d == "ask" and why.startswith("outside workspace"), bad
    d, why = policy.decide("delete_file", {"paths": ["/etc/hosts"]}, "auto", WS)
    assert d == "ask" and "outside workspace" in why


def test_the_six_are_native_tools_and_reserved_builtin_names():
    names = {t.name for t in FILE_TOOLS}
    assert names == {"str_replace_edit", "grep", "copy_files", "delete_file", "image_metadata", "sleep"}
    assert names <= {t.name for t in NATIVE_TOOLS}
    assert names <= policy.builtin_names(), "a custom tool must not be able to claim these names"


def test_the_tui_snapshots_before_a_delete_like_it_does_before_a_write():
    app = (Path(__file__).parent.parent / "dream" / "tui" / "app.py").read_text()
    assert "not in (policy.WRITE, policy.DESTRUCTIVE)" in app


def test_file_activity_accounting_for_the_new_tools(tmp_path):
    (tmp_path / "a.py").write_text("x")
    assert policy.classify_file_activity(
        "str_replace_edit", {"path": "a.py", "old_string": "x", "new_string": "y"}, tmp_path
    ) == ("edited", "a.py")
    assert policy.classify_file_activity(
        "copy_files", {"files": [{"src": "a.py", "dest": "b.py"}]}, tmp_path
    ) == ("created", "b.py")
    assert policy.classify_file_activity(
        "delete_file", {"paths": ["a.py"]}, tmp_path
    ) == ("deleted", "a.py")
