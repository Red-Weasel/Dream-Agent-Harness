# Modeling from reference photos

For a real object (a car, a building, a product), match the photos by looking, not by
typing coordinates: lofting hand-typed cross-sections gets proportions wrong.

1. Dimensions: take the real length, width and height from the owner or look them up
   with `web_search`. Put the photos in the workspace (`refs/`).
2. Helpers: read `scripts/reference_planes.py` and `scripts/compare_to_reference.py` with
   `skill_file(name="blender-animation", path=...)` and save each once into the workspace
   (`blender/`) with `write_file`. Each script's header says how to call it.
3. Reference planes: `reference_planes({"front": ..., "left": ..., "top": ...}, length=,
   width=, height=)` puts each photo behind the model at real scale in a "References"
   collection that never renders. Use the straightest front, side and top photos; pass
   `box` (the object's pixel box) when it does not fill the photo, `flip` for a side photo
   facing the other way. Check the printed box height against the real height. 3/4 photos
   are for comparing (step 6), not planes.
4. Block out: a low-poly mesh with a Subdivision Surface modifier, shaped by moving
   vertices and adding edge loops (loop cut) over the planes, mirrored across X. Not
   lofted coordinate lists, not booleans for the main body.
5. After each change, look from front, side and top (orthographic) and check the
   silhouette against the plane. Fix proportions (length, wheelbase, hood, cabin, roof)
   before any detail.
6. After each part: `compare_to_reference("refs/<photo>.png", "front34", name=...)` against
   the closest photo, `see` the printed PNG (photo | model), fix, then move on. Views:
   front, rear, left, right, top, front34, rear34 (and `...r` for the right side), or
   `(azimuth, elevation)`, or a camera's name. Pass the photo's `box` so the model's width
   and ground line land on the car in the photo; a wrong height or shape then shows. If the
   angle differs, try another `(azimuth, elevation)`, or look through the `Compare_<name>`
   camera (the photo is its background), move it to line up, and compare with its name.
7. Previews with Workbench or Eevee (few samples, small size) while iterating; Cycles only
   for finals.
8. Work in one script per part, each starting by loading the helpers (live.md); save the
   `.blend` after each part.
