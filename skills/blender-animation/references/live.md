# Live Blender

When `blender__*` tools are listed, Dream runs a real Blender, confined to the workspace:
its Python reads and writes only the workspace, and the Blender process itself is
offline. Only Blender is offline: `web_search`, `browse` and `run_bash` reach the
internet as usual. It draws into its own window on the owner's screen (a nested display
it fills). The first Blender call opens it and says so; it closes when the session ends.
The viewport renders on the CPU, so keep scenes light while working live.

- Look first: `blender__get_scene_info` and `blender__get_object_info`.
- Change with `blender__execute_blender_code`, one small step at a time. Look names up
  with `blender__bpy_api_lookup` and node sockets with `blender__describe_node_type`
  instead of guessing.
- Look at the result with `blender__get_viewport_screenshot`. For what only the window
  shows (menus, panels, the render view), call `blender__get_window`, then
  `computer_open(kind="desktop", window_id=...)`, `computer_observe` and `computer_action`
  on that window. Blender fills it, so window coordinates are Blender's.
- The owner watches and steers in chat ("make the water a little rougher"). Apply the
  steer as the next step, then show it.
- Save the `.blend` in the workspace after each accepted step: unsaved work is lost when
  the session ends, and files written outside the workspace vanish. Renders default to
  `renders/`.
- References and assets: when the owner gives none, find them. Look up reference photos
  and dimensions with `web_search` and `browse`; download textures, HDRIs or models
  (Poly Haven, Sketchfab and the like) with `run_bash` into the workspace, then import
  them with `blender__execute_blender_code`. Blender's own add-ons cannot download
  (the process is offline), so fetch first, import second. Build from primitives and
  procedural materials when nothing suitable is found.
- Without these tools (no display, no Blender, or switched off), use headless Blender
  scripts through `run_bash`.
