"""DREAM-129: numbered snapshots of the live Blender scene, and the way back.

A model's scripts end with bpy.ops.wm.save_mainfile(); Blender keeps one .blend1, so three bad
steps overwrote the owner's good scene. Before every live-Blender call that can change the scene
(execute_blender_code, export_scene) the bridge now saves a compressed copy into
.dream/blender-snapshots/ (the session's own file untouched), keeps the last KEEP, and
restore_scene_snapshot reopens one.

Here: the bookkeeping (scene_snapshots.py), the bridge's order of commands against a fake
Blender connection, and the add-on's two handlers in real headless Blender. The whole path in
the real sandbox (fake nested display, background Blender) is in tests/test_blender_live.py.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from dream.core import policy
from dream.media import blender_live

RUNTIME = blender_live.RUNTIME
BLENDER = Path("/usr/bin/blender")


def _load(monkeypatch, name: str):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)  # no __pycache__ in the mounted runtime
    monkeypatch.syspath_prepend(str(RUNTIME))
    for cached in ("scene_snapshots",):
        monkeypatch.delitem(sys.modules, cached, raising=False)
    spec = importlib.util.spec_from_file_location(f"dream129_{name}", RUNTIME / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the bookkeeping -----------------------------------------------------------------------


def test_snapshots_are_numbered_indexed_and_only_the_last_keep_stay(monkeypatch, tmp_path):
    snaps = _load(monkeypatch, "scene_snapshots")
    assert snaps.FOLDER == Path(".dream") / "blender-snapshots" and snaps.KEEP == 20
    where = snaps.folder(tmp_path)
    assert where == tmp_path / ".dream" / "blender-snapshots"
    for step in range(1, snaps.KEEP + 4):
        number, path = snaps.allocate(where, now=0)
        assert number == step and path.parent == where and path.name.startswith(f"{step:04d}-")
        path.write_bytes(b"blend")  # Blender writes it; here a stand-in
        entries = snaps.record(where, number, path, f"\n  import bpy  # step {step}\nbpy.ops.wm.save_mainfile()")
    assert [e["number"] for e in entries] == list(range(4, snaps.KEEP + 4))
    assert sorted(p.name for p in where.glob("*.blend")) == [e["file"] for e in entries]
    index = json.loads((where / "index.json").read_text())
    assert index == entries and index[-1]["call"] == f"import bpy  # step {snaps.KEEP + 3}"
    assert index[-1]["time"] and set(index[-1]) == {"number", "file", "time", "call"}
    assert snaps.find(where, 3) is None and snaps.find(where, 4) == where / entries[0]["file"]
    text = snaps.listing(where)
    assert "restore_scene_snapshot(number)" in text and f"  23  " in text and "before: import bpy  # step 23" in text


def test_the_snapshot_being_restored_is_never_pruned(monkeypatch, tmp_path):
    snaps = _load(monkeypatch, "scene_snapshots")
    where = snaps.folder(tmp_path)
    for _ in range(snaps.KEEP):
        number, path = snaps.allocate(where)
        path.write_bytes(b"blend")
        snaps.record(where, number, path, "step")
    number, path = snaps.allocate(where)  # the snapshot taken before restoring snapshot 1
    path.write_bytes(b"blend")
    entries = snaps.record(where, number, path, "restore_scene_snapshot(1)", protect=1)
    assert snaps.find(where, 1) is not None and [e["number"] for e in entries] == list(range(1, snaps.KEEP + 2))
    number, path = snaps.allocate(where)  # the next snapshot prunes it as usual
    path.write_bytes(b"blend")
    assert [e["number"] for e in snaps.record(where, number, path, "next")] == list(range(3, snaps.KEEP + 3))


def test_a_damaged_index_or_a_stray_file_does_not_lose_the_snapshots(monkeypatch, tmp_path):
    snaps = _load(monkeypatch, "scene_snapshots")
    where = snaps.folder(tmp_path)
    number, path = snaps.allocate(where)
    path.write_bytes(b"blend")
    snaps.record(where, number, path, "first")
    (where / "index.json").write_text("{not json")
    (where / "notes.txt").write_text("x")
    number, path = snaps.allocate(where)
    assert number == 2
    path.write_bytes(b"blend")
    entries = snaps.record(where, number, path, "second")
    assert [e["number"] for e in entries] == [1, 2] and entries[1]["call"] == "second"
    assert (where / "notes.txt").exists()


def test_an_empty_folder_lists_no_snapshots(monkeypatch, tmp_path):
    snaps = _load(monkeypatch, "scene_snapshots")
    assert snaps.listing(snaps.folder(tmp_path)).startswith("No scene snapshots yet")


# --- the bridge, against a fake Blender connection -------------------------------------------


class _FakeBlender:
    def __init__(self, fail: set[str] = frozenset()):
        self.sent, self.fail = [], fail
        self.opened = {"saved_as": "/w/car.blend", "filepath": "/w/car.blend", "objects": 165, "problems": []}

    def send_command(self, kind, params=None):
        self.sent.append((kind, params or {}))
        if kind in self.fail:
            raise Exception(f"{kind} broke")
        if kind == "save_snapshot":
            Path(params["filepath"]).write_bytes(b"blend")
            return {"filepath": params["filepath"], "seconds": 0.01, "bytes": 5}
        if kind == "open_snapshot":
            return {"opened": params["filepath"], **self.opened}
        return {"result": "ran\n"}


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    module = _load(monkeypatch, "bridge")
    fake = _FakeBlender()
    monkeypatch.setattr(module, "get_blender_connection", lambda: fake)
    monkeypatch.chdir(tmp_path)  # the bridge runs in the workspace
    module.fake = fake
    return module


async def test_every_code_call_saves_a_snapshot_first_and_says_so(bridge, tmp_path):
    text = await bridge.execute_blender_code("import bpy\nbpy.ops.wm.save_mainfile()")
    assert [kind for kind, _ in bridge.fake.sent] == ["save_snapshot", "execute_code"]
    saved = Path(bridge.fake.sent[0][1]["filepath"])
    assert saved.parent == tmp_path / ".dream" / "blender-snapshots" and saved.name.startswith("0001-")
    assert text.splitlines()[0] == ("[Scene snapshot 1 saved before this call; "
                                    "restore_scene_snapshot(1) brings the scene back to it.]")
    assert text.splitlines()[1] == "Code executed successfully: ran"
    await bridge.export_scene("out/car.glb")
    assert [kind for kind, _ in bridge.fake.sent][2:] == ["save_snapshot", "export_scene"]
    listing = await bridge.list_scene_snapshots()
    assert "before: import bpy" in listing and "before: export_scene out/car.glb" in listing


async def test_a_failed_call_still_names_its_snapshot(bridge):
    bridge.fake.fail = {"execute_code"}
    text = await bridge.execute_blender_code("bpy.data.objects.remove(everything)")
    assert text.startswith("[Scene snapshot 1 saved before this call;") and "Error executing code" in text


async def test_a_failed_snapshot_is_reported_and_the_call_still_runs(bridge):
    bridge.fake.fail = {"save_snapshot"}
    text = await bridge.execute_blender_code("print(1)")
    assert [kind for kind, _ in bridge.fake.sent] == ["save_snapshot", "execute_code"]
    assert text.startswith("[WARNING: the scene snapshot before this call failed (save_snapshot broke); "
                           "this step cannot be undone")


async def test_restore_snapshots_the_current_scene_then_reopens_the_chosen_one(bridge, tmp_path):
    await bridge.execute_blender_code("step one")
    await bridge.execute_blender_code("step two")
    bridge.fake.sent.clear()
    text = await bridge.restore_scene_snapshot(1)
    kinds = [kind for kind, _ in bridge.fake.sent]
    assert kinds == ["save_snapshot", "open_snapshot"]
    opened = bridge.fake.sent[1][1]
    assert Path(opened["filepath"]).name.startswith("0001-")
    assert opened["fallback"] == str(tmp_path / "restored-1.blend")
    assert text.startswith("[Scene snapshot 3 saved before this call;")
    assert "Restored scene snapshot 1 (165 objects); the scene is now saved as /w/car.blend." in text
    assert "before: restore_scene_snapshot(1)" in await bridge.list_scene_snapshots()


async def test_restoring_a_missing_snapshot_changes_nothing(bridge):
    text = await bridge.restore_scene_snapshot(7)
    assert text.startswith("Error: there is no scene snapshot 7.") and bridge.fake.sent == []


async def test_reading_tools_take_no_snapshot(bridge):
    await bridge.get_scene_info()
    await bridge.bpy_api_lookup("Object")
    await bridge.list_scene_snapshots()
    assert "save_snapshot" not in [kind for kind, _ in bridge.fake.sent]


def test_the_restore_tool_is_contained_and_the_listing_is_free(tmp_path):
    for mode in policy.MODES:
        assert policy.decide("blender__list_scene_snapshots", {}, mode, tmp_path)[0] == "allow", mode
    assert policy.decide("blender__restore_scene_snapshot", {}, "auto", tmp_path)[0] == "allow"
    assert policy.decide("blender__restore_scene_snapshot", {}, "accept-edits", tmp_path)[0] == "allow"
    assert policy.decide("blender__restore_scene_snapshot", {}, "ask", tmp_path)[0] == "ask"
    assert policy.decide("blender__restore_scene_snapshot", {}, "plan", tmp_path)[0] == "deny"


async def test_restore_names_the_snapshot_folder_and_where_the_scene_went(bridge, tmp_path):
    await bridge.execute_blender_code("step one")
    await bridge.restore_scene_snapshot(1)
    assert bridge.fake.sent[-1][1]["snapshots"] == str(tmp_path / ".dream" / "blender-snapshots")
    bridge.fake.opened = {"saved_as": str(tmp_path / "restored-1.blend"), "filepath": str(tmp_path / "restored-1.blend"),
                          "objects": 3, "problems": ["not saved as /w/ro/car.blend: Cannot open file for writing"]}
    text = await bridge.restore_scene_snapshot(1)
    assert "[Scene snapshot 3 saved before this call;" in text
    assert f"the scene is now saved as {tmp_path / 'restored-1.blend'}" in text
    assert "not saved as /w/ro/car.blend: Cannot open file for writing" in text


async def test_a_restore_that_cannot_save_says_the_scene_is_open_from_the_snapshot_folder(bridge, tmp_path):
    await bridge.execute_blender_code("step one")
    inside = str(tmp_path / ".dream" / "blender-snapshots" / "0001-000000.blend")
    bridge.fake.opened = {"saved_as": None, "filepath": inside, "objects": 3,
                          "problems": ["not saved as /w/restored-1.blend: disk full"]}
    text = await bridge.restore_scene_snapshot(1)
    assert text.startswith("[Scene snapshot 2 saved before this call;")
    assert f"WARNING: the restored scene is open from the snapshot folder ({inside})" in text
    assert "save_as_mainfile(filepath=" in text and "disk full" in text


async def test_a_restore_that_fails_keeps_the_pre_restore_snapshot_line(bridge):
    await bridge.execute_blender_code("step one")
    bridge.fake.fail = {"open_snapshot"}
    text = await bridge.restore_scene_snapshot(1)
    assert text.startswith("[Scene snapshot 2 saved before this call;")
    assert "Error restoring scene snapshot 1: open_snapshot broke" in text


async def test_numbers_continue_when_a_script_deletes_the_snapshot_folder(bridge, tmp_path):
    import shutil
    await bridge.execute_blender_code("one")
    await bridge.execute_blender_code("two")
    shutil.rmtree(tmp_path / ".dream" / "blender-snapshots")
    text = await bridge.execute_blender_code("three")
    assert text.startswith("[Scene snapshot 3 saved before this call;")


def test_the_blender_skill_mentions_snapshots():
    live = (Path(__file__).resolve().parents[1] / "skills" / "blender-animation" / "references" / "live.md").read_text()
    assert "restore_scene_snapshot" in live and ".dream/blender-snapshots" in live


# --- the add-on's handlers in real headless Blender ------------------------------------------


HEADLESS = r'''
import json, os, sys, bpy
sys.path.insert(0, RUNTIME)
import addon
server = addon.BlenderMCPServer()
out = {}
ws = os.getcwd()
bpy.ops.mesh.primitive_monkey_add()
image = bpy.data.images.new("ref", 4, 4)
image.filepath_raw = os.path.join(ws, "photos", "ref.png")
image.file_format = "PNG"
image.use_fake_user = True
image.save()
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(ws, "car.blend"), relative_remap=True)
bpy.data.images["ref"].filepath = "//photos/ref.png"
bpy.ops.wm.save_mainfile()
os.makedirs(os.path.join(ws, ".dream", "blender-snapshots"))
snap = os.path.join(ws, ".dream", "blender-snapshots", "0001-120000.blend")
out["save"] = server.execute_command({"type": "save_snapshot", "params": {"filepath": snap}})
out["path_after_save"] = bpy.data.filepath
for o in list(bpy.data.objects):  # a bad step: everything gone, and saved over the file
    bpy.data.objects.remove(o)
bpy.ops.wm.save_mainfile()
out["objects_after_bad_step"] = len(bpy.data.objects)
out["open"] = server.execute_command({"type": "open_snapshot",
                                      "params": {"filepath": snap, "fallback": os.path.join(ws, "x.blend"),
                                                 "snapshots": os.path.dirname(snap)}})
out["objects"] = sorted(o.name for o in bpy.data.objects)
out["path"] = bpy.data.filepath
out["image"] = bpy.path.abspath(bpy.data.images["ref"].filepath)
print("OUT" + json.dumps(out))
'''


def _headless(tmp_path: Path, code: str) -> dict:
    if not BLENDER.exists():
        pytest.skip("no /usr/bin/blender")
    script = tmp_path / "probe.py"
    script.write_text(f"RUNTIME = {str(RUNTIME)!r}\n" + code)
    run = subprocess.run([str(BLENDER), "-b", "--factory-startup", "--python-exit-code", "1", "--python", str(script)],
                         cwd=tmp_path, capture_output=True, text=True, timeout=120,
                         env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-3000:]
    line = next(l for l in run.stdout.splitlines() if l.startswith("OUT"))
    return json.loads(line[3:])


def test_the_addon_saves_a_copy_and_restores_it_under_the_session_file(tmp_path):
    out = _headless(tmp_path, HEADLESS)
    ws = tmp_path.resolve()
    save = out["save"]
    assert save["status"] == "success" and save["result"]["bytes"] > 0, save
    assert out["path_after_save"] == str(ws / "car.blend")  # the session's own file stays the open file
    assert out["objects_after_bad_step"] == 0
    assert out["open"]["status"] == "success", out["open"]
    assert out["objects"] == ["Camera", "Cube", "Light", "Suzanne"]  # the scene from before the bad step
    assert out["path"] == str(ws / "car.blend")  # later saves go to the session's file, not the snapshot
    assert out["image"] == str(ws / "photos" / "ref.png")  # relative paths survive the round trip
    assert (ws / ".dream" / "blender-snapshots" / "0001-120000.blend").is_file()


SELF_OPEN = r"""
import hashlib, json, os, sys, bpy
sys.path.insert(0, RUNTIME)
import addon
server = addon.BlenderMCPServer()
ws = os.getcwd()
snaps = os.path.join(ws, ".dream", "blender-snapshots")
os.makedirs(snaps)
def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(ws, "car.blend"))
one, two = os.path.join(snaps, "0001-000000.blend"), os.path.join(snaps, "0002-000000.blend")
server.execute_command({"type": "save_snapshot", "params": {"filepath": one}})
bpy.ops.mesh.primitive_monkey_add()
server.execute_command({"type": "save_snapshot", "params": {"filepath": two}})
before = md5(one)
bpy.ops.wm.open_mainfile(filepath=one)  # the model opened snapshot 1 itself
out = {"self": server.execute_command({"type": "open_snapshot", "params": {
    "filepath": two, "fallback": os.path.join(ws, "restored-2.blend"), "snapshots": snaps}})}
out["one_unchanged"] = md5(one) == before
out["folder"] = sorted(os.listdir(snaps))
out["path1"] = bpy.data.filepath
out["head1"] = open(bpy.data.filepath, "rb").read(4).hex()
# the session's file cannot be written: the fallback takes it
os.makedirs(os.path.join(ws, "ro"))
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(ws, "ro", "scene.blend"))
os.chmod(os.path.join(ws, "ro"), 0o555)
out["ro"] = server.execute_command({"type": "open_snapshot", "params": {
    "filepath": two, "fallback": os.path.join(ws, "restored-2b.blend"), "snapshots": snaps}})
out["path2"] = bpy.data.filepath
# neither can be written
bpy.ops.wm.open_mainfile(filepath=os.path.join(ws, "ro", "scene.blend"))
out["none"] = server.execute_command({"type": "open_snapshot", "params": {
    "filepath": two, "fallback": os.path.join(ws, "ro", "restored-2c.blend"), "snapshots": snaps}})
out["path3"] = bpy.data.filepath
os.chmod(os.path.join(ws, "ro"), 0o755)
print("OUT" + json.dumps(out))
"""


def test_a_restore_never_leaves_the_session_file_in_the_snapshot_folder(tmp_path):
    out = _headless(tmp_path, SELF_OPEN)
    ws = tmp_path.resolve()
    snaps = ws / ".dream" / "blender-snapshots"
    first = out["self"]["result"]
    assert out["self"]["status"] == "success" and first["saved_as"] == str(ws / "restored-2.blend"), out["self"]
    assert out["path1"] == str(ws / "restored-2.blend")
    assert out["one_unchanged"] and out["folder"] == ["0001-000000.blend", "0002-000000.blend"]  # no .blend1
    assert any("is a snapshot" in p for p in first["problems"]), first
    assert out["head1"] == "424c454e"  # "BLEN": the restored file is saved uncompressed, not zstd
    second = out["ro"]["result"]
    assert second["saved_as"] == str(ws / "restored-2b.blend") and out["path2"] == second["saved_as"], second
    assert any(p.startswith(f"not saved as {ws / 'ro' / 'scene.blend'}") for p in second["problems"]), second
    third = out["none"]["result"]
    assert third["saved_as"] is None and len(third["problems"]) == 2, third
    assert out["path3"] == third["filepath"] and Path(third["filepath"]).parent == snaps


def test_numbers_in_the_index_are_not_reused_when_their_files_are_gone(monkeypatch, tmp_path):
    snaps = _load(monkeypatch, "scene_snapshots")
    where = snaps.folder(tmp_path)
    for _ in range(3):
        number, path = snaps.allocate(where)
        path.write_bytes(b"blend")
        snaps.record(where, number, path, "step")
    for path in where.glob("*.blend"):
        path.unlink()
    assert snaps.allocate(where)[0] == 4
