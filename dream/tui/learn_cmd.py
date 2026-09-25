"""Explicit human controls for demonstration capture and skill review."""
from __future__ import annotations
import asyncio
import json
import shlex
from pathlib import Path

from .. import demonstrations as demos
from ..core import turn_origin

USAGE = """/learn start <name> <x> <y> <width> <height> [seconds]
/learn stop | list
/learn import <video-path> [name]
/learn extract <id> | analyze <id> | draft <id> | install <id>
Recording stays local. Analyze sends selected evidence to this session's model.
Installed drafts remain disabled until you enable their skill in Extensions."""


async def command(app, argument: str):
    try:
        parts = shlex.split(argument)
        action, *rest = parts or ["help"]
        if action == "start":
            if len(rest) not in {5, 6}:
                raise ValueError(USAGE)
            if getattr(app, "recorder", None) is None:
                app.recorder = demos.Recorder()
            info = await app.recorder.start(rest[0], region=tuple(int(n) for n in rest[1:5]),
                                            seconds=int(rest[5]) if len(rest) == 6 else 300)
            app.renderer.system(f"● RECORDING {info['name']} — region {info['region']}. /learn stop ends capture. Auto-stop in {info['maximum_seconds']} seconds.")
        elif action == "stop":
            if getattr(app, "recorder", None) is None:
                raise ValueError("No active demonstration")
            info = await app.recorder.stop()
            app.renderer.system(f"Capture {info['status']}: {info.get('id', '')}. /learn extract <id> prepares local frames.")
        elif action == "list":
            app.renderer.info(json.dumps(demos.inventory(), indent=2))
        elif action == "import" and rest:
            info = await asyncio.to_thread(demos.import_video, Path(rest[0]), " ".join(rest[1:]))
            app.renderer.info(f"Imported: {info['id']}. /learn extract {info['id']}")
        elif action == "extract" and len(rest) == 1:
            info = await demos.extract(rest[0])
            app.renderer.info(f"Prepared {len(info['frames'])} frames locally. /learn analyze {rest[0]} uses this session's model.")
        elif action == "analyze" and len(rest) == 1:
            info = demos.read(rest[0])
            if info['status'] not in {"ready", "draft"}:
                raise ValueError("Extract frames first")
            if not app.engine.provider.multimodal and app.engine.provider.kind != "cli":
                raise ValueError("This provider has vision disabled. Choose a vision-capable provider before analyzing screen evidence.")
            with turn_origin.generated(turn_origin.LEARN):   # Dream wrote this prompt; the transcript marks it
                await app._ask(
                    f"Analyze my demonstration {rest[0]} ({info['name']}). Use demonstration_read to select representative "
                    "frames, then see to inspect them. Work in small batches, inspect transitions and uncertainty. "
                    "Create a reusable skill draft with demonstration_draft, concrete outcome checks and frame evidence. "
                    "Label inferred actions; screenshots do not record keystrokes. Do not install, enable, or replay the skill.")
        elif action == "draft" and len(rest) == 1:
            app.renderer.info((demos.directory(rest[0]) / "draft" / "SKILL.md").read_text())
        elif action == "install" and len(rest) == 1:
            path = await asyncio.to_thread(demos.install, rest[0])
            app.renderer.info(f"Installed disabled: {path}. After review: /extensions enable skill:{path.name}")
        else:
            app.renderer.info(USAGE)
    except (OSError, ValueError, KeyError) as exc:
        app.renderer.error(str(exc))
