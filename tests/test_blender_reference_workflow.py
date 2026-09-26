"""DREAM-122: the Blender skill's reference workflow and its two bundled scripts.

The functional tests run the scripts in a real headless Blender (skipped without /usr/bin/blender)
with HOME and the user site hidden, as in live Blender's sandbox, where numpy is not importable."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "blender-animation"
SCRIPTS = SKILL / "scripts"
BLENDER = Path("/usr/bin/blender")


def _text(rel: str) -> str:
    path = SKILL / rel
    if not path.is_file():
        pytest.fail(f"missing {path}")
    return path.read_text(encoding="utf-8")


def test_skill_links_the_reference_workflow():
    assert "references/reference-modeling.md" in _text("SKILL.md")
    live = _text("references/live.md")
    assert "reference-modeling.md" in live


def test_live_says_names_do_not_persist_and_how_to_reload():
    live = _text("references/live.md")
    assert "NameError" in live and "do not persist" in live
    assert "exec(open(" in live
    assert "compare_to_reference" in live


def test_reference_modeling_prescribes_the_workflow():
    text = _text("references/reference-modeling.md")
    for needle in ("reference_planes", "compare_to_reference", "Subdivision Surface", "web_search", "Cycles",
                   "Workbench", "one script per part", "save", "skill_file", "scripts/reference_planes.py",
                   "scripts/compare_to_reference.py"):
        assert needle in text, needle
    assert len(text) < 4000  # a local model reads it: keep it short


def test_scripts_are_bundled_and_readable_by_skill_file():
    from dream.skills import loader

    for name in ("reference_planes.py", "compare_to_reference.py"):
        source = (SCRIPTS / name).read_text(encoding="utf-8")
        compile(source, name, "exec")
        assert len(source) < loader.FILE_MAX_CHARS
        assert "import numpy" not in source and "PIL" not in source


def _blender(tmp_path: Path, body: str) -> dict:
    if not BLENDER.is_file():
        pytest.skip("no /usr/bin/blender")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    script = tmp_path / "run.py"
    script.write_text(
        "import bpy, json, sys, os\n"
        "def make_png(path, w, h, rgba):\n"
        "    im = bpy.data.images.new(os.path.basename(path), w, h)\n"
        "    im.pixels.foreach_set(list(rgba) * (w * h))\n"
        "    im.filepath_raw, im.file_format = path, 'PNG'\n"
        "    im.save()\n"
        "    bpy.data.images.remove(im)\n"
        + SNAPSHOT +
        f"exec(open({str(SCRIPTS / 'reference_planes.py')!r}).read())\n"
        f"exec(open({str(SCRIPTS / 'compare_to_reference.py')!r}).read())\n"
        "OUT = {}\n" + body +
        "\nOUT['numpy'] = 'numpy' in sys.modules\n"
        "print('RESULT ' + json.dumps(OUT))\n", encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "PYTHONNOUSERSITE": "1", "LANG": "C.UTF-8"}
    proc = subprocess.run(["nice", "-n", "15", str(BLENDER), "-b", "--factory-startup", "--python-exit-code", "1",
                           "--python", str(script)], cwd=tmp_path, env=env, capture_output=True, text=True,
                          timeout=240)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert proc.returncode == 0 and lines, proc.stdout[-3000:] + proc.stderr[-3000:]
    out = json.loads(lines[-1][7:])
    assert out.pop("numpy") is False  # the scripts work without numpy, as live Blender must
    out["_stdout"] = proc.stdout
    return out


# Every writable plain setting of the scene's render, output, engine, colour-management and display
# structs, the scene camera and every camera's background images: what a compare must leave as it found.
SNAPSHOT = """
def snapshot():
    sc = bpy.context.scene
    snap = {'camera': sc.camera.name if sc.camera else None}
    structs = {'render': sc.render, 'image_settings': sc.render.image_settings, 'eevee': sc.eevee,
               'display': sc.display, 'shading': sc.display.shading, 'view_settings': sc.view_settings,
               'display_settings': sc.display_settings, 'cycles': getattr(sc, 'cycles', None)}
    for key, st in structs.items():
        if st is None:
            continue
        for prop in st.bl_rna.properties:
            if prop.is_readonly or prop.type not in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM'}:
                continue
            v = getattr(st, prop.identifier)
            snap[key + '.' + prop.identifier] = list(v) if hasattr(v, '__len__') and not isinstance(v, str) else v
    for ob in bpy.data.objects:
        if ob.type == 'CAMERA' and not ob.name.startswith('Compare_'):
            d = ob.data
            snap['cam.' + ob.name] = [list(ob.matrix_world.col[3]), d.type, d.lens, d.shift_x, d.shift_y,
                                     d.show_background_images, len(d.background_images)]
    return snap


def changed(before, after):
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


def silhouette(path, panel, panel_w):
    im = bpy.data.images.load(path, check_existing=False)
    w, h = im.size
    px = list(im.pixels)
    bg = px[4 * (panel * panel_w + 1):4 * (panel * panel_w + 1) + 3]
    xs, ys = [], []
    for y in range(h):
        for x in range(panel * panel_w, (panel + 1) * panel_w):
            i = 4 * (y * w + x)
            if max(abs(px[i + c] - bg[c]) for c in range(3)) > 0.08:
                xs.append(x - panel * panel_w)
                ys.append(h - 1 - y)  # rows from the top, as in the photo
    return [min(xs), min(ys), max(xs) + 1, max(ys) + 1] if xs else None
"""


def test_reference_planes_are_to_scale_aligned_and_never_rendered(tmp_path):
    out = _blender(tmp_path, """
os.makedirs('refs')
make_png('refs/side.png', 64, 48, (0.2, 0.4, 0.6, 1))
make_png('refs/front.png', 48, 64, (0.6, 0.4, 0.2, 1))
make_png('refs/top.png', 64, 48, (0.3, 0.3, 0.3, 1))
names = reference_planes({
    'left': {'path': 'refs/side.png', 'box': (8, 8, 56, 40)},   # 48 px long -> 0.1 m/px
    'front': 'refs/front.png',                                  # whole image, 48 px wide
    'top': {'path': 'refs/top.png', 'box': (0, 0, 64, 24), 'rotate': 90},
}, length=4.8, width=1.92, height=1.3, gap=0.05)
col = bpy.data.collections['References']
OUT['names'] = sorted(names)
OUT['collection_hide_render'] = col.hide_render
for n in names:
    ob = bpy.data.objects[n]
    m = ob.matrix_world
    OUT[n] = {'size': ob.empty_display_size, 'type': ob.empty_display_type, 'hide_render': ob.hide_render,
              'loc': list(m.translation), 'x': list(m.col[0][:3]), 'y': list(m.col[1][:3]), 'z': list(m.col[2][:3]),
              'coll': [c.name for c in ob.users_collection], 'alpha': ob.color[3], 'image': ob.data.name}
""")
    approx = pytest.approx
    assert out["names"] == ["Ref_front", "Ref_left", "Ref_top"]
    assert out["collection_hide_render"] is True
    left, front, top = out["Ref_left"], out["Ref_front"], out["Ref_top"]
    for ob in (left, front, top):
        assert ob["type"] == "IMAGE" and ob["hide_render"] is True and ob["coll"] == ["References"]
        assert ob["alpha"] == approx(0.5)
    # Side: 0.1 m/px, the larger image side (64 px) is the display size; the plane stands beside
    # the model (x = -width/2 - gap) facing +X, its image x along +Y (front at screen left), up = +Z.
    assert left["size"] == approx(6.4)
    assert left["z"] == approx([1, 0, 0], abs=1e-6) and left["x"] == approx([0, 1, 0], abs=1e-6)
    # The box (32 px = 3.2 m tall, centred in the image) stands on the ground: centre 1.6 m up.
    assert left["loc"] == approx([-0.96 - 0.05, 0, 1.6], abs=1e-6)
    # Front: 48 px -> 1.92 m = 0.04 m/px; portrait, so the display size is the 64 px height.
    assert front["size"] == approx(64 * 0.04)
    assert front["z"] == approx([0, -1, 0], abs=1e-6) and front["x"] == approx([1, 0, 0], abs=1e-6)
    assert front["loc"] == approx([0, 2.4 + 0.05, 64 * 0.04 / 2], abs=1e-6)
    # Top, rotated 90: the box's 24 px vertical extent spans the length (0.2 m/px), centred.
    assert top["size"] == approx(64 * 0.2)
    assert top["z"] == approx([0, 0, 1], abs=1e-6) and top["x"] == approx([0, 1, 0], abs=1e-6)
    # The box sits 12 px above the image centre; turned 90, image-up is world -X: the plane moves +X.
    assert top["loc"] == approx([12 * 0.2, 0, -0.05], abs=1e-6)


@pytest.mark.parametrize("size,height,panel,engine", [((64, 48), 48, 64, "BLENDER_EEVEE"),
                                                       ((48, 64), 64, 48, "BLENDER_WORKBENCH")])
def test_compare_to_reference_writes_photo_beside_model(tmp_path, size, height, panel, engine):
    out = _blender(tmp_path, f"""
os.makedirs('refs')
make_png('refs/photo.png', {size[0]}, {size[1]}, (1, 0, 0, 1))
bpy.context.scene.eevee.taa_render_samples = 64
bpy.context.scene.render.engine = 'CYCLES'
before = snapshot()
path = compare_to_reference('refs/photo.png', 'front34', name='t', height={height}, overlay=True,
                            engine={engine!r})
OUT['path'] = path
OUT['changed'] = changed(before, snapshot())
OUT['samples'] = bpy.context.scene.eevee.taa_render_samples
im = bpy.data.images.load(path)
OUT['size'] = list(im.size)
px = list(im.pixels)
w = im.size[0]
def at(x, y):
    i = 4 * (y * w + x)
    return px[i:i + 3]
OUT['photo'] = at(2, 2)
OUT['model_panel_differs'] = any(at(x, y) != at(2, 2) for x in range({panel}, 2 * {panel}) for y in range({height}))
cam = bpy.data.objects['Compare_t']
OUT['cam_bg'] = [b.image.name for b in cam.data.background_images]
OUT['refused'] = []
for photo, nm in (('refs/photo.png', '../escape'), ('../outside.png', 'x')):
    try:
        compare_to_reference(photo, 'front', name=nm, engine='BLENDER_WORKBENCH')
    except ValueError as e:
        OUT['refused'].append(str(e))
""")
    assert Path(out["path"]) == (tmp_path / "renders" / "compare_t.png").resolve()
    assert out["size"] == [3 * panel, height]  # photo | model | overlay at the photo's aspect
    assert out["photo"] == pytest.approx([1, 0, 0], abs=0.02)
    assert out["model_panel_differs"] is True
    assert out["changed"] == [] and out["samples"] == 64  # every setting as it was, Eevee samples included
    assert f"rendered with {engine}" in out["_stdout"]  # BLENDER_EEVEE: BLENDER_EEVEE_NEXT on Blender 4.2-4.5
    assert "fell back" not in out["_stdout"]  # DREAM-141: Eevee on 4.5 is Eevee, not Workbench
    assert out["cam_bg"] == ["photo.png"]
    assert len(out["refused"]) == 2 and "outside the workspace" in out["refused"][1]
    assert sorted(p.name for p in (tmp_path / "renders").iterdir()) == ["compare_t.png", "compare_t_model.png"]


def test_compare_frames_the_model_on_the_photo_box(tmp_path):
    """With a box, the model's width, left edge and ground line land on the box and its height stays free:
    a cube seen straight on fills a square box exactly (orthographic); in perspective the width and ground
    line match."""
    out = _blender(tmp_path, """
os.makedirs('refs')
make_png('refs/photo.png', 64, 48, (1, 0, 0, 1))
path = compare_to_reference('refs/photo.png', 'front', name='o', height=96, box=(10, 8, 34, 32),
                            engine='BLENDER_WORKBENCH')
OUT['ortho'] = silhouette(path, 1, 128)
path = compare_to_reference('refs/photo.png', 'front34', name='p', height=96, box=(6, 10, 50, 40),
                            engine='BLENDER_WORKBENCH')
OUT['persp'] = silhouette(path, 1, 128)
path = compare_to_reference('refs/photo.png', 'front', name='n', height=96, engine='BLENDER_WORKBENCH')
""")
    # Panel = photo x2 (128x96). Ortho: the 2 m cube's square fills the 24x24 px box -> (20,16)-(68,64).
    assert out["ortho"] == pytest.approx([20, 16, 68, 64], abs=2)
    x0, y0, x1, y1 = out["persp"]
    assert [x0, x1, y1] == pytest.approx([12, 100, 80], abs=3)
    assert "no box: the model fills the frame" in out["_stdout"]
    assert "model framed on the photo box" in out["_stdout"]


def test_compare_refusals_create_nothing_and_leave_named_cameras_alone(tmp_path):
    out = _blender(tmp_path, """
os.makedirs('refs')
make_png('refs/photo.png', 64, 48, (1, 0, 0, 1))
OUT['errors'] = []
for kw in ({'view': 'sideways'}, {'view': 'front', 'name': 'a/b'}, {'view': 'front', 'photo': '../x.png'},
           {'view': 'front', 'photo': 'refs/missing.png'}, {'view': 'Camera', 'box': (0, 0, 10, 10)},
           {'view': 'front', 'box': (0, 0, 99, 10)}):
    kw.setdefault('photo', 'refs/photo.png')
    try:
        compare_to_reference(kw.pop('photo'), kw.pop('view'), **kw)
        OUT['errors'].append(None)
    except ValueError as e:
        OUT['errors'].append(str(e))
OUT['renders_after_refusals'] = os.path.exists('renders')
before = snapshot()
compare_to_reference('refs/photo.png', 'Camera', name='cam', height=24, engine='NO_SUCH_ENGINE')
OUT['changed'] = changed(before, snapshot())
""")
    errors = out["errors"]
    assert all(errors), errors
    assert "front34" in errors[0] and "rear34r" in errors[0]  # the valid views are listed
    assert "outside the workspace" in errors[2] and "missing.png" in errors[3]
    assert "Camera" in errors[4] and "box" in errors[5]
    assert out["renders_after_refusals"] is False
    assert out["changed"] == []  # the owner's camera gets no background image, nothing else moves
    assert "fell back from NO_SUCH_ENGINE" in out["_stdout"] and "rendered with BLENDER_WORKBENCH" in out["_stdout"]
