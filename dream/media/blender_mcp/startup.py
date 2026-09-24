"""Dream's live Blender startup (DREAM-109): runs inside Blender, inside the sandbox.

`blender --factory-startup --python startup.py` loads the Blender MCP add-on's server from
this folder and starts it on 127.0.0.1:9876 in the sandbox's own network namespace, so no
add-on is installed into anyone's Blender preferences. Blender's working directory is the
workspace; renders default to <workspace>/renders/render (render.png for a still,
render0001.png... for frames) instead of Blender's /tmp/, which here is the sandbox's
private folder and vanishes with it.

With a window, the add-on's timer runs the commands on Blender's main thread. In
background mode (Dream's tests: no display) there is no event loop to run that timer, so
this script runs the same queue on the main thread until the sandbox ends.
"""
import os
import sys
import time

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import addon  # noqa: E402 -- the add-on copy beside this script

renders = os.path.join(os.getcwd(), "renders") + os.sep
bpy.context.preferences.filepaths.render_output_directory = renders  # for new scenes
bpy.context.scene.render.filepath = renders + "render"  # a still is render.png, not ".png"
bpy.context.preferences.view.show_splash = False

server = addon.BlenderMCPServer(port=9876)
server.start()
bpy.types.blendermcp_server = server  # where the upstream add-on keeps it

if bpy.app.background:
    while server.running:
        server._drain_command_queue()
        time.sleep(0.02)
