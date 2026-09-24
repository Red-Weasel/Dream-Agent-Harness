"""Starter components: the scaffolds a design page begins from.

A local model cannot hand-roll a scaling deck or a phone bezel well, and should not
try. `copy_starter_component` drops one of Dream's own starters into the workspace
and echoes its full content, so the model can slot its design into it at once.
A `.jsx` kind also brings `vendor/` — pinned React, ReactDOM, and Babel — because
neither of Dream's frames has network and a CDN tag cannot load.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from . import mirror
from .context import ctx, err, ok

STARTERS_DIR = Path(__file__).resolve().parent.parent / "gui" / "starters"
KINDS = ("deck_stage.js", "design_canvas.jsx", "ios_frame.jsx", "android_frame.jsx",
         "macos_window.jsx", "browser_window.jsx", "animations.jsx", "three")
VENDOR = ("react.production.min.js", "react-dom.production.min.js", "babel.min.js",
          "LICENSE-react.txt", "LICENSE-babel.txt")

_WHAT = {
    "deck_stage.js": "slide-deck shell web component — scaling, keyboard/tap nav, counter, "
                     "localStorage position, print-to-PDF; put each slide as a <section> child",
    "design_canvas.jsx": "grid of labeled options side by side, for presenting 2+ static variations",
    "ios_frame.jsx": "iPhone bezel with Dynamic Island, status bar, home indicator, optional keyboard",
    "android_frame.jsx": "Android bezel with punch-hole camera, status bar, gesture/button nav, keyboard",
    "macos_window.jsx": "macOS window chrome with traffic lights and a title",
    "browser_window.jsx": "browser chrome with a tab strip and address bar",
    "animations.jsx": "timeline engine: Stage + Sprite + scrubber, useTime/useSprite, Easing, "
                      "interpolate, FadeIn/FadeOut/SlideIn/Scale",
    "three": "three.js r182 in vendor/three: the module build plus OrbitControls, "
             "EffectComposer/RenderPass/UnrealBloomPass/OutputPass/ShaderPass, Sky and RoomEnvironment",
}

THREE_TAGS = """<script type="importmap">
{ "imports": { "three": "./{base}vendor/three/three.module.min.js",
               "three/addons/": "./{base}vendor/three/addons/" } }
</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { Sky } from 'three/addons/objects/Sky.js';
// your scene
</script>"""


def _tags(kind: str, directory: str) -> str:
    base = f"{directory.rstrip('/')}/" if directory else ""
    if kind.endswith(".js"):
        return f'<script src="{base}{kind}"></script>'
    return "\n".join([
        f'<script src="{base}vendor/react.production.min.js"></script>',
        f'<script src="{base}vendor/react-dom.production.min.js"></script>',
        f'<script src="{base}vendor/babel.min.js"></script>',
        f'<script type="text/babel" src="{base}{kind}"></script>',
        '<script type="text/babel">/* your JSX; components are on window */</script>',
    ])


@tool(
    "copy_starter_component",
    "Copy a starter component into the project instead of hand-drawing device bezels, "
    "deck shells, or presentation grids. Kinds: " + ", ".join(KINDS) + ". The kind name "
    "INCLUDES the extension — pass it exactly. .js starters are plain web components "
    "(load with <script src>); .jsx starters are React (load with <script type=\"text/babel\" "
    "src>) and bring a vendor/ folder with React, ReactDOM, and Babel, since the frames "
    "have no network. Echoes the file's full content and the exact script tags to use. "
    "`three` copies three.js with common add-ons (controls, bloom, sky) for 3D scenes.",
    {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(KINDS)},
            "directory": {"type": "string",
                          "description": "Optional subdirectory to copy into (e.g. \"frames/\"). "
                                         "Default: the workspace root."},
        },
        "required": ["kind"],
    },
)
async def copy_starter_component(args: dict[str, Any]) -> dict[str, Any]:
    kind = str(args.get("kind") or "")
    if kind not in KINDS:
        near = [k for k in KINDS if k.split(".")[0] == kind.split(".")[0]]
        hint = f" Did you mean {near[0]!r}? The extension is part of the name." if near else ""
        return err(f"Unknown starter {kind!r}. Kinds: {', '.join(KINDS)}.{hint}")
    directory = str(args.get("directory") or "").strip()
    if directory.startswith(("/", "~")):
        return err("directory must be relative to the workspace, e.g. \"frames/\".")
    directory = directory.strip("/")
    dest_dir = (ctx().workspace / directory).resolve() if directory else ctx().workspace.resolve()
    if not dest_dir.is_relative_to(ctx().workspace.resolve()):
        return err("directory must be inside the workspace.")
    if kind == "three":
        # A library, not a component: the frames have no network, so a CDN import
        # cannot load (Dream fix #3: a live build hand-rolled bloom for lack of it).
        try:
            shutil.copytree(STARTERS_DIR / "vendor" / "three", dest_dir / "vendor" / "three", dirs_exist_ok=True)
        except OSError as e:
            return err(f"Could not copy three.js: {type(e).__name__}: {e}")
        base = f"{directory}/" if directory else ""
        return ok(f"Copied three — {_WHAT[kind]}.\n  {dest_dir / 'vendor' / 'three'}/\n\n"
                  "Load it as an ES module through an import map (file:// works in the preview):\n"
                  + THREE_TAGS.replace("{base}", base))
    src = STARTERS_DIR / kind
    if not src.is_file():
        return err(f"Starter {kind} is missing from Dream's install ({src}).")
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest_dir / kind)
        copied = [str(dest_dir / kind)]
        if kind.endswith(".jsx"):
            (dest_dir / "vendor").mkdir(exist_ok=True)
            for v in VENDOR:
                shutil.copy2(STARTERS_DIR / "vendor" / v, dest_dir / "vendor" / v)
            copied.append(str(dest_dir / "vendor") + "/ (React, ReactDOM, Babel)")
    except OSError as e:
        return err(f"Could not copy {kind}: {type(e).__name__}: {e}")
    mirror.file_written(dest_dir / kind)   # dropped onto the shown page: it reloads there (DREAM-104)
    content = src.read_text(encoding="utf-8")
    return ok(
        f"Copied {kind} — {_WHAT[kind]}.\n" + "\n".join(f"  {c}" for c in copied)
        + "\n\nLoad it with:\n" + _tags(kind, directory)
        + f"\n\n--- {kind} ---\n{content}"
    )


STARTER_TOOLS = [copy_starter_component]
