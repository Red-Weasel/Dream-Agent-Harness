"""DREAM-132: a viewport screenshot shows the scene as it is, and says when nothing changed.

Live, the model changed the scene between screenshots and got the same pixels back each time,
so it gave up on them and checked every step with minutes-long Cycles renders. The capture
(GPUOffScreen.draw_view3d) runs only Blender's own draw engines: with the viewport in Rendered
shading and Cycles as the engine it returns the empty background and the overlays, the same
whatever the scene holds, while the window shows the render.

The add-on's handler runs here in a real Blender window on a private Xvfb display (never the
owner's), one step per timer tick as the live add-on runs commands; skipped without Xvfb. The same
handler also runs against fake bpy and gpu modules (no Blender, no display), which is where the
switch to Material Preview and back is checked when the offscreen draw fails. The bridge's line
about the picture is checked against a fake Blender connection.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import textwrap
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from dream.media import blender_live

RUNTIME = blender_live.RUNTIME
BLENDER = Path("/usr/bin/blender")

SCENARIO = r'''
import json, os, sys, bpy
sys.path.insert(0, RUNTIME)
import addon
server = addon.BlenderMCPServer()
out, n = [], [0]

def shot():
    path = os.path.join(OUT, f"shot{len(out)}.png")
    result = server.get_viewport_screenshot(max_size=400, filepath=path)
    area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
    result["after"] = area.spaces.active.shading.type
    out.append(result)

def cube_color(rgba):
    bpy.data.objects["Cube"].active_material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = rgba

def rendered():
    bpy.context.scene.render.engine = "CYCLES"
    next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D").spaces.active.shading.type = "RENDERED"

STEPS = [
    lambda: None, lambda: None,                                   # the window draws once
    shot,                                                          # 0: solid
    lambda: setattr(bpy.data.objects["Cube"].location, "x", 2.0),
    shot,                                                          # 1: the cube moved
    shot,                                                          # 2: nothing changed
    rendered, lambda: cube_color((0.8, 0.8, 0.8, 1.0)),
    shot,                                                          # 3: Rendered, Cycles
    lambda: cube_color((1.0, 0.0, 0.0, 1.0)),
    shot,                                                          # 4: the cube turned red
]

def tick():
    try:
        STEPS[n[0]]()
    except Exception as exc:
        out.append({"exception": repr(exc)})
    n[0] += 1
    if n[0] < len(STEPS):
        return 0.3
    with open(os.path.join(OUT, "result.json"), "w") as f:
        json.dump(out, f)
    bpy.ops.wm.quit_blender()

bpy.app.timers.register(tick, first_interval=1.0)
'''


def _xvfb() -> str:
    xvfb = shutil.which("Xvfb")
    if xvfb is None or not BLENDER.exists():
        pytest.skip("needs Xvfb and /usr/bin/blender: the capture needs a real window")
    return xvfb


@pytest.fixture
def display():
    """A private Xvfb display, numbered far above the owner's and live Blender's; stopped after the test."""
    xvfb, number = _xvfb(), blender_live.free_display(400)
    read, write = os.pipe()
    server = subprocess.Popen([xvfb, f":{number}", "-displayfd", str(write), "-screen", "0", "1280x800x24",
                               "-nolisten", "tcp"], pass_fds=(write,),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(write)
    try:
        with os.fdopen(read) as pipe:
            ready = pipe.readline().strip()  # written once the server accepts connections
        assert ready == str(number), f"Xvfb did not start on :{number}"
        yield f":{number}"
    finally:
        server.terminate()
        server.wait(timeout=30)


def _run(display: str, tmp_path: Path) -> list[dict]:
    script = tmp_path / "scenario.py"
    script.write_text(f"RUNTIME = {str(RUNTIME)!r}\nOUT = {str(tmp_path)!r}\n" + textwrap.dedent(SCENARIO))
    env = {**os.environ, "DISPLAY": display}
    done = subprocess.run([str(BLENDER), "--factory-startup", "--python", str(script)], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=600)
    result = tmp_path / "result.json"
    assert result.exists(), done.stdout[-2000:] + done.stderr[-2000:]
    return json.loads(result.read_text())


def test_a_screenshot_shows_each_change_and_says_when_nothing_changed(display, tmp_path):
    shots = _run(display, tmp_path)
    assert len(shots) == 5 and all(s.get("success") for s in shots), shots
    assert all(s["method"] == "offscreen" for s in shots), shots
    digest = [(tmp_path / f"shot{i}.png").read_bytes() for i in range(5)]
    assert digest[0] != digest[1] and shots[1]["same_as_previous"] is False       # the move shows
    assert digest[2] == digest[1] and shots[2]["same_as_previous"] is True        # an honest "unchanged"
    assert shots[0]["shading"] == shots[0]["drawn_as"] == "SOLID" and shots[0]["view"] == "PERSP"
    # Rendered with Cycles: before DREAM-132 both pictures were the empty background, identical.
    assert digest[4] != digest[3] and shots[4]["same_as_previous"] is False
    for s in shots[3:]:
        assert (s["shading"], s["engine"], s["drawn_as"]) == ("RENDERED", "CYCLES", "MATERIAL"), s
        assert s["scene_lights"] is True and s["scene_world"] is True
        assert s["after"] == "RENDERED"  # the owner's viewport is put back as it was


# --- the add-on's handler against fake bpy and gpu modules ------------------------------------


SHADING_FIELDS = ("type", "use_scene_lights", "use_scene_world", "use_scene_lights_render",
                  "use_scene_world_render", "color_type")


class _FakeBlenderModules:
    """Just enough of bpy and gpu for get_viewport_screenshot. The picture's bytes come from `colour`."""

    def __init__(self, fail: str | None = None):
        self.fail, self.colour, self.drawn, self.freed = fail, 1, [], 0
        self.shading = SimpleNamespace(type="RENDERED", use_scene_lights=False, use_scene_world=True,
                                       use_scene_lights_render=True, use_scene_world_render=False,
                                       color_type="MATERIAL")
        region = SimpleNamespace(type="WINDOW", width=8, height=4)
        space = SimpleNamespace(shading=self.shading, region_3d=SimpleNamespace(
            view_matrix=None, window_matrix=None, view_perspective="PERSP"))
        area = SimpleNamespace(type="VIEW_3D", spaces=SimpleNamespace(active=space), regions=[region])

        @contextmanager
        def temp_override(**_):
            yield

        fake = self
        bpy = ModuleType("bpy")
        bpy.context = SimpleNamespace(screen=SimpleNamespace(areas=[area]), view_layer=object(),
                                      scene=SimpleNamespace(render=SimpleNamespace(engine="CYCLES")),
                                      temp_override=temp_override)

        class Image:
            def __init__(self, width, height, data=b""):
                self.size, self.data, self.filepath_raw, self.file_format = (width, height), data, "", "PNG"
                self.pixels = SimpleNamespace(foreach_set=lambda values: setattr(self, "data", values.tobytes()))

            def save(self):
                Path(self.filepath_raw).write_bytes(self.data)

            def scale(self, width, height):
                self.size = (width, height)

        bpy.data = SimpleNamespace(images=SimpleNamespace(
            new=lambda name, width, height, alpha=True: Image(width, height),
            load=lambda path: Image(8, 4, Path(path).read_bytes()),
            remove=lambda image: None))
        bpy.ops = SimpleNamespace(screen=SimpleNamespace(
            screenshot_area=lambda filepath: Path(filepath).write_bytes(b"window %d" % fake.colour)))

        class Buffer(list):
            dimensions = None

        class OffScreen:
            def __init__(self, width, height):
                if fake.fail == "constructor":
                    raise RuntimeError("no GPU context")
                self.size = width * height * 4

            def draw_view3d(self, scene, view_layer, view3d, region, view, window, do_color_management=False):
                fake.drawn.append({f: getattr(view3d.shading, f) for f in SHADING_FIELDS})
                if fake.fail == "draw":
                    raise RuntimeError("draw failed")

            @property
            def texture_color(self):
                return SimpleNamespace(read=lambda: Buffer([fake.colour] * self.size))

            def free(self):
                fake.freed += 1

        gpu = ModuleType("gpu")
        gpu.types = SimpleNamespace(GPUOffScreen=OffScreen)
        self.modules = {"bpy": bpy, "gpu": gpu, "mathutils": ModuleType("mathutils")}

    def state(self) -> dict:
        return {f: getattr(self.shading, f) for f in SHADING_FIELDS}


def _fake_addon(monkeypatch, fake: _FakeBlenderModules):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)  # no __pycache__ in the mounted runtime
    for name, module in fake.modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location("dream132_addon", RUNTIME / "addon.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.BlenderMCPServer()


def test_the_cycles_substitute_is_drawn_and_the_owners_shading_comes_back_exactly(monkeypatch, tmp_path):
    fake = _FakeBlenderModules()
    before = fake.state()
    server = _fake_addon(monkeypatch, fake)
    first = server.get_viewport_screenshot(filepath=str(tmp_path / "a.png"))
    assert fake.state() == before and fake.freed == 1
    assert fake.drawn == [{**before, "type": "MATERIAL", "use_scene_lights": True, "use_scene_world": False}]
    assert (first["method"], first["shading"], first["drawn_as"]) == ("offscreen", "RENDERED", "MATERIAL")
    assert (first["scene_lights"], first["scene_world"], first["same_as_previous"]) == (True, False, False)
    again = server.get_viewport_screenshot(filepath=str(tmp_path / "b.png"))
    assert again["same_as_previous"] is True and fake.state() == before
    fake.colour = 2  # the scene changed
    assert server.get_viewport_screenshot(filepath=str(tmp_path / "c.png"))["same_as_previous"] is False


@pytest.mark.parametrize("fail", ["constructor", "draw"])
def test_a_failed_offscreen_draw_puts_the_shading_back_and_reports_the_owners_shading(monkeypatch, tmp_path, fail):
    fake = _FakeBlenderModules(fail=fail)
    before = fake.state()
    server = _fake_addon(monkeypatch, fake)
    result = server.get_viewport_screenshot(filepath=str(tmp_path / "a.png"))
    assert fake.state() == before
    assert fake.freed == (1 if fail == "draw" else 0)
    assert (result["method"], result["shading"], result["drawn_as"]) == ("window_grab", "RENDERED", "RENDERED")
    assert (result["scene_lights"], result["scene_world"]) == (True, False)  # the Rendered view's own flags
    assert server.get_viewport_screenshot(filepath=str(tmp_path / "b.png"))["same_as_previous"] is True
    assert fake.state() == before


def test_a_view_the_offscreen_draw_can_run_is_not_switched(monkeypatch, tmp_path):
    fake = _FakeBlenderModules()
    fake.shading.type = "SOLID"
    fake.modules["bpy"].context.scene.render.engine = "BLENDER_EEVEE"
    before = fake.state()
    result = _fake_addon(monkeypatch, fake).get_viewport_screenshot(filepath=str(tmp_path / "a.png"))
    assert fake.drawn == [before] and fake.state() == before
    assert (result["drawn_as"], result["scene_lights"], result["color_type"]) == ("SOLID", None, "MATERIAL")


# --- the bridge's line about the picture -------------------------------------------------------


class _FakeBlender:
    def __init__(self, result: dict):
        self.result = result

    def send_command(self, kind, params=None):
        Path(params["filepath"]).write_bytes(b"\x89PNG fake")
        return dict(self.result)


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)  # no __pycache__ in the mounted runtime
    monkeypatch.syspath_prepend(str(RUNTIME))
    monkeypatch.delitem(sys.modules, "scene_snapshots", raising=False)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    spec = importlib.util.spec_from_file_location("dream132_bridge", RUNTIME / "bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = {"success": True, "method": "offscreen", "engine": "CYCLES", "view": "PERSP",
        "scene_lights": False, "scene_world": False, "same_as_previous": False}


def _shot(bridge, monkeypatch, result: dict):
    monkeypatch.setattr(bridge, "get_blender_connection", lambda: _FakeBlender(result))
    return bridge.get_viewport_screenshot()


def _text(bridge, monkeypatch, **result) -> str:
    text, image = _shot(bridge, monkeypatch, {**BASE, **result})
    assert image.data == b"\x89PNG fake"
    return text


def test_the_result_says_what_the_picture_shows(bridge, monkeypatch):
    text = _text(bridge, monkeypatch, shading="MATERIAL", drawn_as="MATERIAL")
    assert text == ("Viewport: Material Preview shading, seen from the viewport's own view, not the scene camera.\n"
                    "In this picture the scene's lights and world do not (a studio HDRI stands in for the world).")
    text = _text(bridge, monkeypatch, shading="RENDERED", drawn_as="MATERIAL", scene_lights=True, scene_world=True,
                 view="CAMERA", same_as_previous=True)
    assert text.startswith("Viewport: Material Preview shading, seen from the scene camera.")
    assert ("Rendered shading with CYCLES, which this capture cannot draw; the picture is Material Preview "
            "instead: the scene's own lights and world show.") in text and "blender__get_window" in text
    assert text.endswith("The pixels are identical to the previous screenshot: nothing changed that this view shows.")
    text = _text(bridge, monkeypatch, shading="SOLID", drawn_as="SOLID", scene_lights=None, scene_world=None)
    assert text.endswith("This shading shows no scene lights, world or shader-node colors.")


def test_the_lighting_words_follow_the_flags(bridge, monkeypatch):
    # (gate) a Cycles substitute whose render view has the scene world off
    text = _text(bridge, monkeypatch, shading="RENDERED", drawn_as="MATERIAL", scene_lights=True, scene_world=False)
    assert ("the picture is Material Preview instead: the scene's own lights show; its world does not "
            "(a studio HDRI stands in for the world).") in text and "lights and world show" not in text
    # (gate) Material Preview with the scene lights on and the world off: the lights do show
    text = _text(bridge, monkeypatch, shading="MATERIAL", drawn_as="MATERIAL", scene_lights=True, scene_world=False)
    assert text.endswith("In this picture the scene's own lights show; its world does not "
                         "(a studio HDRI stands in for the world).")
    text = _text(bridge, monkeypatch, shading="MATERIAL", drawn_as="MATERIAL", scene_lights=False, scene_world=True)
    assert text.endswith("In this picture the scene's own world shows; its lights do not.")
    text = _text(bridge, monkeypatch, shading="MATERIAL", drawn_as="MATERIAL", scene_lights=True, scene_world=True)
    assert text == "Viewport: Material Preview shading, seen from the viewport's own view, not the scene camera."
    text = _text(bridge, monkeypatch, shading="SOLID", drawn_as="SOLID", color_type="TEXTURE")
    assert text.endswith("Solid shading with image textures: no scene lights, world or other shader-node colors show.")


def test_an_older_add_ons_result_gets_no_made_up_line(bridge, monkeypatch):
    assert not isinstance(_shot(bridge, monkeypatch, {"success": True}), list)  # the picture alone, as before
    text, _ = _shot(bridge, monkeypatch, {"success": True, "shading": "SOLID"})
    assert text == "Viewport: Solid shading, seen from the viewport.\nThis shading shows no scene lights, world or shader-node colors."
