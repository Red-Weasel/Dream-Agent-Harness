# Render the model as a reference photo shows it, next to the photo (Dream blender-animation skill).
# Run inside Blender: exec(open("blender/compare_to_reference.py").read()), then e.g.
#   compare_to_reference("refs/front34.png", "front34")
#   compare_to_reference("refs/side.png", "left", name="side", overlay=True)
# view: front, rear, left, right, top, front34 (front-left), front34r, rear34, rear34r,
# (azimuth_deg, elevation_deg) with azimuth 0 = front, 90 = left side (+X), or the name of
# a camera object. Same convention as reference_planes.py: front toward -Y, ground at Z=0.
# Straight views use an orthographic camera, the others perspective (lens mm). The model is
# every renderable mesh/curve/surface/text outside "References". With `box` (x0, y0, x1, y1:
# the object's pixel box in the photo, y down, as for reference_planes) the model's width,
# left edge and ground line land on the box; its height is left free, so a model with the
# photo's proportions fills the box and a wrong one shows it. Without a box the model
# fills the frame by `fill`. A named camera is used as it is (no box, never modified).
# Writes renders/compare_<name>.png: photo | model [| 50 % overlay], `height` px tall, at the
# photo's aspect ratio, then prints the path to look at with `see`. Leaves a camera
# "Compare_<name>" with the photo as its background (look through it to line things up)
# and restores every scene setting it changed. No numpy: live Blender has none.
import math
import os
import re
from array import array

import bpy
from mathutils import Vector

CMP_VIEWS = {"front": (0, 0), "left": (90, 0), "rear": (180, 0), "right": (270, 0), "top": (0, 90),
             "front34": (45, 12), "rear34": (135, 12), "rear34r": (225, 12), "front34r": (315, 12)}


def _cmp_inside(path):
    root = os.path.realpath(os.getcwd())  # live Blender runs in the workspace
    full = os.path.realpath(os.path.join(root, path))
    if os.path.commonpath([root, full]) != root:
        raise ValueError(f"{path} is outside the workspace")
    return full


def _cmp_pixels(img, w, h):
    if tuple(img.size) != (w, h):
        img.scale(w, h)
    buf = array("f", bytes(4 * w * h * 4))
    img.pixels.foreach_get(buf)
    return buf


def _cmp_camera(name, view, aspect, lens, fill, box_uv):
    az, el = CMP_VIEWS[view] if isinstance(view, str) else view
    az, el = math.radians(az), math.radians(el)
    toward = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el))).normalized()
    top = abs(toward.z) > 0.999
    right = Vector((1, 0, 0)) if top else Vector((0, 0, 1)).cross(toward).normalized()
    up = toward.cross(right)
    pts = [ob.matrix_world @ Vector(c) for ob in bpy.context.scene.objects
           if ob.type in {"MESH", "CURVE", "SURFACE", "FONT", "META"} and not ob.hide_render
           and not any(c.name == "References" for c in ob.users_collection) for c in ob.bound_box]
    if not pts:
        raise ValueError("no renderable model to compare")
    center = sum(pts, Vector()) / len(pts)
    radius = max((p - center).length for p in pts)
    cname = f"Compare_{name}"
    cam = bpy.data.objects.get(cname) or bpy.data.objects.new(cname, bpy.data.cameras.new(cname))
    if cam.name not in bpy.context.scene.collection.objects:
        bpy.context.scene.collection.objects.link(cam)
    d = cam.data
    straight = max(abs(toward.x), abs(toward.y), abs(toward.z)) > 0.999
    d.type, d.sensor_fit, d.shift_x, d.shift_y = ("ORTHO" if straight else "PERSP"), "AUTO", 0.0, 0.0

    def rect(dist):  # the model's extent on screen: plane units (ortho) or tangents (perspective)
        eye = center + toward * dist
        xs, ys = [], []
        for p in pts:
            v = p - eye
            z = 1.0 if straight else max(1e-6, -v.dot(toward))
            xs.append(v.dot(right) / z)
            ys.append(v.dot(up) / z)
        return min(xs), min(ys), max(xs), max(ys)

    if straight:
        dist = radius * 3
        x0, y0, x1, y1 = rect(dist)
        if box_uv:
            u0, v0, u1, v1 = box_uv
            fw = (x1 - x0) / (u1 - u0)  # the model's width spans the box's width
            fh = fw / aspect
            d.ortho_scale = max(fw, fh)
            d.shift_x = ((x0 + x1) / 2 - ((u0 + u1) / 2 - 0.5) * fw) / d.ortho_scale
            d.shift_y = (y0 - (v0 - 0.5) * fh) / d.ortho_scale
        else:
            half_w, half_h = max(-x0, x1), max(-y0, y1)
            d.ortho_scale = 2 * fill * (max(half_w, half_h * aspect) if aspect >= 1 else max(half_h, half_w / aspect))
    else:
        d.lens = lens
        wide = d.sensor_width / 2 / lens  # tan(half fov) across the wider side
        tw, th = (wide, wide / aspect) if aspect >= 1 else (wide * aspect, wide)
        dist = radius * fill / math.sin(math.atan(min(tw, th)))
        if box_uv:
            u0, v0, u1, v1 = box_uv
            for _ in range(30):  # distance so the model's width spans the box's width
                x0, y0, x1, y1 = rect(dist)
                dist *= (x1 - x0) / ((u1 - u0) * 2 * tw)
            x0, y0, x1, y1 = rect(dist)
            d.shift_x = (x0 - (u0 - 0.5) * 2 * tw) / (2 * wide)
            d.shift_y = (y0 - (v0 - 0.5) * 2 * th) / (2 * wide)
    d.clip_end = max(d.clip_end, dist + radius * 2)
    cam.location = center + toward * dist
    cam.rotation_euler = (0, 0, 0) if top else (-toward).to_track_quat("-Z", "Y").to_euler()
    return cam


def compare_to_reference(photo, view, name=None, height=480, overlay=False, engine="BLENDER_EEVEE",
                         lens=50, fill=1.05, samples=8, box=None):
    named = bpy.data.objects.get(view) if isinstance(view, str) else None
    if isinstance(view, str) and view not in CMP_VIEWS and not (named and named.type == "CAMERA"):
        raise ValueError(f"unknown view {view!r}: use one of {', '.join(CMP_VIEWS)}, "
                         "(azimuth, elevation) or a camera's name")
    if named and box:
        raise ValueError(f"camera {view!r} is used as it is: drop box, or use a view preset")
    name = name or (view if isinstance(view, str) else "view")
    if not re.fullmatch(r"[\w-]+", name):
        raise ValueError(f"name {name!r}: use letters, digits, _ or -")
    out = _cmp_inside(os.path.join("renders", f"compare_{name}.png"))
    model_png = _cmp_inside(os.path.join("renders", f"compare_{name}_model.png"))
    source = _cmp_inside(photo)
    if not os.path.isfile(source):
        raise ValueError(f"no photo at {photo}")
    ref = bpy.data.images.load(source, check_existing=True)
    pw, ph = ref.size
    if not pw or not ph:
        raise ValueError(f"cannot read image {photo}")
    box_uv = None
    if box:
        x0, y0, x1, y1 = box
        if not (0 <= x0 < x1 <= pw and 0 <= y0 < y1 <= ph):
            raise ValueError(f"box {tuple(box)} is not inside the {pw}x{ph} photo")
        box_uv = (x0 / pw, 1 - y1 / ph, x1 / pw, 1 - y0 / ph)  # v up from the bottom
    os.makedirs(os.path.dirname(out), exist_ok=True)
    h = int(height)
    w = max(1, round(h * pw / ph))
    scene, r = bpy.context.scene, bpy.context.scene.render
    fields = [(scene, "camera"), (r, "engine"), (r, "resolution_x"), (r, "resolution_y"),
              (r, "resolution_percentage"), (r, "filepath"), (r.image_settings, "file_format"),
              (r.image_settings, "color_mode"), (scene.eevee, "taa_render_samples")]
    saved = [getattr(o, a) for o, a in fields]
    try:
        if named:
            cam = named
        else:
            cam = _cmp_camera(name, view, w / h, lens, fill, box_uv)
            cam.data.show_background_images = True
            if not any(b.image == ref for b in cam.data.background_images):
                bg = cam.data.background_images.new()
                bg.image, bg.alpha, bg.display_depth = ref, 0.5, "FRONT"
        scene.camera = cam
        try:
            r.engine = engine
            fell = ""
        except TypeError:
            r.engine = "BLENDER_WORKBENCH"
            fell = f", fell back from {engine}"
        used = r.engine
        if used == "BLENDER_EEVEE":
            scene.eevee.taa_render_samples = samples
        r.resolution_x, r.resolution_y, r.resolution_percentage = w, h, 100
        r.image_settings.file_format, r.image_settings.color_mode = "PNG", "RGBA"
        r.filepath = model_png
        bpy.ops.render.render(write_still=True)
    finally:
        for (o, a), v in zip(fields, saved):
            setattr(o, a, v)

    shot = bpy.data.images.load(model_png, check_existing=False)
    small = ref.copy()  # the camera keeps the full-size photo
    a = _cmp_pixels(small, w, h)
    b = _cmp_pixels(shot, w, h)
    b[3::4] = array("f", [1.0]) * (w * h)  # a transparent render shows opaque
    panels = [a, b] + ([array("f", [(x + y) * 0.5 for x, y in zip(a, b)])] if overlay else [])
    row = 4 * w
    total = array("f")
    for y in range(h):
        for p in panels:
            total.extend(p[y * row:(y + 1) * row])
    img = bpy.data.images.new(f"compare_{name}", len(panels) * w, h, alpha=True)
    img.pixels.foreach_set(total)
    img.filepath_raw, img.file_format = out, "PNG"
    img.save()
    for im in (img, shot, small):
        bpy.data.images.remove(im)
    framing = ("named camera" if named else "model framed on the photo box" if box
               else "no box: the model fills the frame")
    print(f"compare: {out} ({len(panels) * w}x{h}: photo | model{' | overlay' if overlay else ''}; "
          f"rendered with {used}{fell}; {framing})")
    return out
