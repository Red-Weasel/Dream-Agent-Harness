# Reference image planes at real scale (Dream blender-animation skill).
# Run inside Blender: exec(open("blender/reference_planes.py").read()), then e.g.
#   reference_planes({"left": "refs/side.png", "front": "refs/front.png",
#                     "top": {"path": "refs/top.png", "box": (40, 10, 600, 300), "rotate": 90}},
#                    length=4.72, width=1.88, height=1.30)
# Paths are relative to the working directory (live Blender: the workspace).
# Convention: model centred on X=Y=0, standing on Z=0, front toward -Y, length along Y.
# Views: front (seen from -Y), rear, left (seen from +X: the car's left side, front at
# screen left), right, top (seen from +Z, front at screen bottom).
# Per image: "box" = (x0, y0, x1, y1), the model's pixel box in the photo (y down; default
# the whole image); "flip" mirrors it (a side photo facing the other way); "rotate" turns it
# (degrees, multiples of 90). The box's horizontal extent is scaled to the real width
# (front/rear), length (left/right) or the dimension it covers on screen (top).
# Planes are image empties in the "References" collection: never rendered, drawn behind
# the model and only when you look straight along their axis (numpad 1/3/7 views); an
# opposite pair (front + rear) shows in both views, so hide the one you are not using.
import math
import os

import bpy
from mathutils import Matrix, Vector

REF_VIEWS = {  # view: (camera direction from the model, i.e. the side the plane faces)
    "front": Vector((0, -1, 0)), "rear": Vector((0, 1, 0)),
    "left": Vector((1, 0, 0)), "right": Vector((-1, 0, 0)), "top": Vector((0, 0, 1)),
}


def view_basis(toward):
    """(right, up, toward) on screen for a view looking back along `toward`."""
    toward = Vector(toward).normalized()
    if abs(toward.z) > 0.999:
        right = Vector((1, 0, 0))
    else:
        right = Vector((0, 0, 1)).cross(toward).normalized()
    return right, toward.cross(right), toward


def reference_planes(images, length, width, height, gap=0.05, opacity=0.5, collection="References"):
    col = bpy.data.collections.get(collection) or bpy.data.collections.new(collection)
    if col.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(col)
    col.hide_render = True
    made = []
    for view, spec in images.items():
        if view not in REF_VIEWS:
            raise ValueError(f"unknown view {view!r}; use one of {sorted(REF_VIEWS)}")
        spec = {"path": spec} if isinstance(spec, str) else dict(spec)
        path = os.path.abspath(spec["path"])
        img = bpy.data.images.load(path, check_existing=True)
        w, h = img.size
        if not w or not h:
            raise ValueError(f"cannot read image {path}")
        x0, y0, x1, y1 = spec.get("box") or (0, 0, w, h)
        rot = int(spec.get("rotate", 0)) % 360
        if rot % 90:
            raise ValueError("rotate must be a multiple of 90")
        flip = bool(spec.get("flip", False))
        turned = rot in (90, 270)
        real = {"front": width, "rear": width, "left": length, "right": length,
                "top": length if turned else width}[view]
        m = real / ((y1 - y0) if turned else (x1 - x0))  # metres per pixel
        # Box centre relative to the image centre, in metres, image axes (x right, y up).
        cx = ((x0 + x1) / 2 - w / 2) * m * (-1 if flip else 1)
        cy = (h / 2 - (y0 + y1) / 2) * m
        a = math.radians(rot)
        cx, cy = cx * math.cos(a) - cy * math.sin(a), cx * math.sin(a) + cy * math.cos(a)
        right, up, toward = view_basis(REF_VIEWS[view])
        depth = {"front": length, "rear": length, "left": width, "right": width, "top": 0}[view] / 2 + gap
        if view == "top":
            origin = -toward * depth - right * cx - up * cy
            note = ""
        else:  # the box's bottom edge sits on the ground
            bottom = (y1 - y0) * m / 2 if not turned else (x1 - x0) * m / 2
            origin = -toward * depth - right * cx + up * (bottom - cy)
            implied = ((x1 - x0) if turned else (y1 - y0)) * m
            note = f", box height {implied:.2f} m vs height {height:.2f} m"
        name = f"Ref_{view}"
        ob = bpy.data.objects.get(name) or bpy.data.objects.new(name, None)
        for c in ob.users_collection:
            c.objects.unlink(ob)
        col.objects.link(ob)
        ob.empty_display_type = "IMAGE"
        ob.data = img
        ob.empty_display_size = max(w, h) * m
        ob.empty_image_offset = (-0.5, -0.5)
        ob.empty_image_depth = "BACK"
        ob.show_empty_image_only_axis_aligned = True
        ob.use_empty_image_alpha = True
        ob.color[3] = opacity
        ob.matrix_world = (Matrix.Translation(origin) @ Matrix((right, up, toward)).transposed().to_4x4()
                           @ Matrix.Rotation(a, 4, "Z") @ Matrix.Diagonal((-1 if flip else 1, 1, 1, 1)))
        ob.hide_render = True
        ob.hide_select = True
        made.append(ob.name)
        print(f"{name}: {w}x{h} px, {m * 1000:.1f} mm/px{note}")
    return made
