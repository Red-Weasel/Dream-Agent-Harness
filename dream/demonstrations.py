"""User-started Linux demonstrations and portable, reviewable skill drafts.

Recording is a UI operation, never an LLM tool. No keylogging, automatic screen
capture at boot, cloud upload, or coordinate replay is performed here.
"""
from __future__ import annotations

import asyncio
import json
import math
import threading
import os
import re
import shutil
import signal
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from . import config
from .core.run_state import atomic_write

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


def root() -> Path:
    return config.DATA_DIR / "demonstrations"


def directory(identifier: str) -> Path:
    if not _ID.fullmatch(identifier):
        raise ValueError("Invalid demonstration id")
    path = root() / identifier
    if path.is_symlink() or not path.resolve().is_relative_to(root().resolve()):
        raise ValueError("Demonstration path escapes its storage root")
    return path


def read(identifier: str) -> dict:
    path = directory(identifier) / "manifest.json"
    if path.is_symlink() or path.stat().st_size > 2_000_000:
        raise ValueError("Invalid demonstration manifest")
    return json.loads(path.read_text())


_EVIDENCE_LOCK = threading.RLock()
_EVIDENCE_MAX = 512_000


def read_evidence(identifier: str) -> dict:
    """Read human annotations; old recordings need no migration."""
    read(identifier)
    path = directory(identifier) / "annotations.json"
    if path.is_symlink():
        raise ValueError("Invalid annotation path")
    if not path.exists():
        return {"demonstration": identifier, "revision": 0, "source": "manual annotation",
                "application": "", "app_version": "", "events": []}
    if path.stat().st_size > _EVIDENCE_MAX:
        raise ValueError("Annotations exceed the storage limit")
    return json.loads(path.read_text())


def save_evidence(identifier: str, payload: dict, *, expected_revision: int | None = None) -> dict:
    """Explicit human annotations only; never exposed as an agent write tool."""
    with _EVIDENCE_LOCK:
        info = read(identifier)
        if info.get("status") not in {"ready", "draft"}:
            raise ValueError("Extract frames before annotating this recording")
        current = read_evidence(identifier)
        if expected_revision is not None and (type(expected_revision) is not int
                or expected_revision != current["revision"]):
            raise ValueError("Evidence changed; reopen it before saving")
        def bounded(value, label, maximum=2000, required=False):
            if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
                raise ValueError(f"Invalid or oversized {label}")
            return value.strip()
        if not isinstance(payload, dict):
            raise ValueError("Expected an evidence object")
        result = {"demonstration": identifier, "revision": current["revision"] + 1,
                  "source": "manual annotation", "updated": datetime.now(timezone.utc).isoformat(),
                  "application": bounded(payload.get("application", ""), "application", 200),
                  "app_version": bounded(payload.get("app_version", ""), "app version", 100)}
        events = payload.get("events")
        if not isinstance(events, list) or len(events) > 100:
            raise ValueError("Provide at most 100 annotation events")
        frames = {frame["file"]: frame["seconds"] for frame in info.get("frames", [])}
        clean, seen = [], set()
        for event in events:
            if not isinstance(event, dict) or set(event) - {"id", "seconds", "frame", "action", "target", "before", "after", "outcome", "basis", "confirmed"}:
                raise ValueError("Invalid annotation fields; source is always manual annotation")
            event_id = event.get("id") or "event-" + uuid4().hex[:12]
            if not isinstance(event_id, str) or not _ID.fullmatch(event_id) or event_id in seen:
                raise ValueError("Invalid or duplicate event id")
            seen.add(event_id)
            frame, seconds = event.get("frame"), event.get("seconds")
            if not isinstance(frame, str) or frame not in frames:
                raise ValueError("Choose a frame from this demonstration")
            if (type(seconds) not in {int, float} or not math.isfinite(seconds)
                    or seconds < 0 or abs(seconds - frames[frame]) > .5):
                raise ValueError("Event time must be within half a second of the selected frame")
            basis, confirmed = event.get("basis", "inferred"), event.get("confirmed", False)
            if basis not in {"observed", "inferred", "user-confirmed"} or type(confirmed) is not bool:
                raise ValueError("Invalid provenance or outcome confirmation")
            if confirmed and basis != "user-confirmed":
                raise ValueError("Only user-confirmed evidence can confirm an outcome")
            item = {"id": event_id, "frame": frame, "seconds": seconds, "basis": basis, "confirmed": confirmed}
            for field in ("action", "target", "before", "after", "outcome"):
                item[field] = bounded(event.get(field, ""), field, required=field == "action" or (field == "outcome" and confirmed))
            clean.append(item)
        result["events"] = clean
        encoded = json.dumps(result, ensure_ascii=False, indent=2)
        if len(encoded.encode()) > _EVIDENCE_MAX:
            raise ValueError("Annotations exceed the storage limit")
        atomic_write(directory(identifier) / "annotations.json", encoded)
        return result


def inventory() -> list[dict]:
    if not root().exists():
        return []
    items = []
    for path in sorted(root().iterdir(), reverse=True)[:100]:
        try:
            info = read(path.name)
            items.append({k: info.get(k) for k in ("id", "name", "status", "created", "frames", "duration_s", "error")})
        except (OSError, ValueError):
            continue
    return items


def read_draft(identifier: str) -> dict:
    info = read(identifier)
    if info.get("status") != "draft":
        raise ValueError("This demonstration does not have a skill draft yet")
    folder = directory(identifier) / "draft"
    path = folder / "SKILL.md"
    if folder.is_symlink() or path.is_symlink() or path.stat().st_size > 512_000:
        raise ValueError("Invalid or oversized skill draft")
    return {"id": identifier, "body": path.read_text()}


def import_video(source: Path, name: str) -> dict:
    """Explicit user-selected file import for Wayland and existing recordings."""
    source = Path(source).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() not in {".mkv", ".mp4", ".mov", ".webm"}:
        raise ValueError("Choose a local MKV, MP4, MOV or WebM recording")
    if source.stat().st_size > 500_000_000:
        raise ValueError("Choose a recording under 500 MB")
    identifier = "demo-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:6]
    folder = directory(identifier)
    folder.mkdir(parents=True, mode=0o700)
    shutil.copyfile(source, folder / "recording.mkv")
    info = {"id": identifier, "name": (name or source.stem)[:100], "status": "recorded",
            "created": datetime.now(timezone.utc).isoformat(), "frames": [], "sample_fps": 2,
            "captures": "imported video", "analysis": "not started; nothing uploaded"}
    atomic_write(folder / "manifest.json", json.dumps(info, indent=2))
    return info


class Recorder:
    def __init__(self):
        self.process = None
        self.info = None
        self._watcher = None
        self._lock = asyncio.Lock()
        self._log = None

    async def start(self, name: str, *, region: tuple[int, int, int, int], seconds: int = 300) -> dict:
        """Called only by an explicit user Start action with a selected region."""
        async with self._lock:
            if self.process is not None:
                raise ValueError("A demonstration is already recording")
            if not name.strip() or len(name) > 100:
                raise ValueError("Give the demonstration a short name")
            if len(region) != 4 or any(type(n) is not int for n in region):
                raise ValueError("Region requires integer x, y, width, height")
            x, y, width, height = region
            if min(x, y) < 0 or not 64 <= width <= 7680 or not 64 <= height <= 4320:
                raise ValueError("Choose a valid screen region between 64px and 7680×4320")
            if type(seconds) is not int or not 1 <= seconds <= 900:
                raise ValueError("Recording duration must be between 1 and 900 seconds")
            ffmpeg = shutil.which("ffmpeg")
            display = os.environ.get("DISPLAY", "")
            if not ffmpeg:
                raise ValueError("ffmpeg is required for demonstration recording")
            if os.environ.get("XDG_SESSION_TYPE") == "wayland" or not re.fullmatch(r":\d+(?:\.\d+)?", display):
                raise ValueError("Direct recording requires an X11 session. On Wayland, use the desktop recorder and import its video.")
            identifier = "demo-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:6]
            folder = directory(identifier)
            folder.mkdir(parents=True, mode=0o700)
            self.info = {"id": identifier, "name": name.strip(), "status": "recording",
                         "created": datetime.now(timezone.utc).isoformat(), "region": list(region),
                         "sample_fps": 2, "maximum_seconds": seconds, "frames": [],
                         "captures": "selected screen region and visible pointer; no keyboard event log",
                         "analysis": "not started; nothing uploaded"}
            atomic_write(folder / "manifest.json", json.dumps(self.info, indent=2))
            self._log = (folder / "capture.log").open("wb")
            spawning = asyncio.create_task(asyncio.create_subprocess_exec(
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "x11grab", "-framerate", "2",
                    "-draw_mouse", "1", "-video_size", f"{width}x{height}", "-i", f"{display}+{x},{y}",
                    "-t", str(seconds), "-vf", "scale='min(1600,iw)':-2", "-c:v", "libx264",
                    "-preset", "ultrafast", "-crf", "20", str(folder / "recording.mkv"),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=self._log,
                    start_new_session=True))
            try:
                try:
                    self.process = await asyncio.shield(spawning)
                except asyncio.CancelledError:
                    process = await spawning
                    if process.returncode is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    await asyncio.shield(process.wait())
                    raise
            except BaseException:
                self._log.close()
                self._log = None
                self.info["status"] = "failed"
                atomic_write(folder / "manifest.json", json.dumps(self.info, indent=2))
                raise
            self._started = time.monotonic()
            self._watcher = asyncio.create_task(self._watch())
            return dict(self.info)

    async def _watch(self):
        process = self.process
        await process.wait()
        await self.stop()

    async def stop(self) -> dict:
        async with self._lock:
            if self.process is None:
                return dict(self.info or {"status": "idle"})
            process = self.process
            try:
                if process.returncode is None:
                    try:
                        process.stdin.write(b"q\n")
                        await process.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    try:
                        await asyncio.wait_for(process.wait(), 5)
                    except TimeoutError:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:
                            await asyncio.wait_for(process.wait(), 2)
                        except TimeoutError:
                            os.killpg(process.pid, signal.SIGKILL)
                            await process.wait()
                self.info["duration_s"] = round(time.monotonic() - self._started, 2)
                self.info["status"] = "recorded" if process.returncode == 0 else "failed"
                if process.returncode:
                    self.info["error"] = f"Screen recorder exited {process.returncode}; see capture.log"
                atomic_write(directory(self.info["id"]) / "manifest.json", json.dumps(self.info, indent=2))
                return dict(self.info)
            except asyncio.CancelledError:
                # Stop is also called during app shutdown. Cancellation must
                # not detach a recorder that is still capturing the screen.
                if process.returncode is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await asyncio.shield(process.wait())
                self.info["status"] = "failed"
                self.info["error"] = "Recording stopped during cancellation; inspect the local video before importing"
                atomic_write(directory(self.info["id"]) / "manifest.json", json.dumps(self.info, indent=2))
                raise
            finally:
                self.process = None
                if self._log:
                    self._log.close()
                    self._log = None


async def extract(identifier: str) -> dict:
    """Extract bounded frames locally. Frame sampling is evidence, not an action log."""
    info = read(identifier)
    if info["status"] not in {"recorded", "ready", "draft"}:
        raise ValueError("Stop the recording successfully before extracting frames")
    folder = directory(identifier)
    video = folder / "recording.mkv"
    if video.is_symlink() or not video.is_file():
        raise ValueError("Recording file is unavailable")
    frames = folder / "frames"
    frames.mkdir(exist_ok=True, mode=0o700)
    from .core.execution import run_owned, minimal_environment
    executable = shutil.which("ffmpeg")
    if not executable:
        raise ValueError("ffmpeg is required to extract frames")
    result = await run_owned([executable, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                              "-protocol_whitelist", "file,pipe", "-i", str(video), "-vf",
                              "fps=2,scale='min(1280,iw)':-2", "-frames:v", "1800", str(frames / "%05d.jpg")],
                             cwd=folder, env=minimal_environment(), timeout=120, max_output=3000)
    if result.returncode or result.timed_out:
        raise ValueError("Frame extraction failed: " + result.output.decode(errors="replace"))
    info["frames"] = [{"file": f"frames/{p.name}", "seconds": round((int(p.stem) - 1) / 2, 1)}
                      for p in sorted(frames.glob("*.jpg"))]
    if not info["frames"]:
        raise ValueError("Recording contained no extractable frames")
    info["status"] = "ready"
    atomic_write(folder / "manifest.json", json.dumps(info, indent=2))
    return info


def create_draft(identifier: str, *, goal: str, steps: list[dict], uncertainty: str = "") -> Path:
    """Write evidence-linked instructions; never treat inferred clicks as observed."""
    info = read(identifier)
    if info["status"] not in {"ready", "draft"}:
        raise ValueError("Extract and inspect demonstration frames first")
    if not goal.strip() or len(goal) > 2000 or not 1 <= len(steps) <= 100:
        raise ValueError("Provide a goal and 1–100 evidence-linked steps")
    frames = {frame["file"] for frame in info["frames"]}
    annotations = read_evidence(identifier)
    annotation_map = {event["id"]: event for event in annotations["events"]}
    clean = []
    for step in steps:
        action = step.get("action", "")
        evidence = step.get("frames", [])
        if not isinstance(action, str) or not action.strip() or len(action) > 2000:
            raise ValueError("Each step needs a bounded action description")
        if not isinstance(evidence, list) or not evidence or any(f not in frames for f in evidence):
            raise ValueError("Each step must cite frames from this demonstration")
        basis = step.get("basis", "inferred")
        if basis not in {"visible", "inferred", "user-confirmed"}:
            raise ValueError("Step basis must be visible, inferred or user-confirmed")
        evidence_ids = step.get("evidence_ids", [])
        if (not isinstance(evidence_ids, list) or len(evidence_ids) > 100
                or any(not isinstance(i, str) or i not in annotation_map for i in evidence_ids)):
            raise ValueError("Cite annotation ids from this demonstration")
        linked = [annotation_map[i] for i in evidence_ids]
        if any(event["frame"] not in evidence for event in linked):
            raise ValueError("Cited annotations must match the step frames")
        if basis == "user-confirmed" and not any(event["basis"] == "user-confirmed" and event["action"] == action.strip() for event in linked):
            raise ValueError("User-confirmed steps require a saved human annotation")
        clean.append({"action": action.strip(), "frames": evidence, "basis": basis, "evidence_ids": evidence_ids,
                      "verify": str(step.get("verify", ""))[:2000]})
    folder = directory(identifier) / "draft"
    folder.mkdir(exist_ok=True)
    name = "learned-" + identifier
    body = ("---\nname: " + name + "\ndescription: " + json.dumps(goal[:300]) + "\n---\n\n"
            + goal.strip() + "\n\nThis procedure was inferred from a user demonstration. "
            "Confirm the current app state and adapt selectors; recorded coordinates are not a reliable replay strategy.\n\n")
    if annotations["events"]:
        body += f"Application: {annotations['application'] or 'unspecified'}; version: {annotations['app_version'] or 'unknown'}.\n\n"
    for i, step in enumerate(clean, 1):
        body += f"{i}. {step['action']}\n   Evidence: {', '.join(step['frames'])} ({step['basis']}).\n"
        for event_id in step["evidence_ids"]:
            event = annotation_map[event_id]
            body += (f"   Manual annotation {event_id}, {event['seconds']}s ({event['basis']}): "
                     f"target={event['target']}; before={event['before']}; action={event['action']}; "
                     f"after={event['after']}; outcome={event['outcome']}; "
                     f"user confirmed outcome={event['confirmed']}.\n")
        if step["verify"]:
            body += f"   Check: {step['verify']}\n"
    body += "\nUncertainty to resolve before relying on this skill: " + (uncertainty[:4000] or "Review all inferred actions against the original demonstration.") + "\n"
    atomic_write(folder / "SKILL.md", body)
    atomic_write(folder / "evidence.json", json.dumps({"demonstration": identifier, "steps": clean, "annotations": {**annotations, "events": [annotation_map[i] for i in dict.fromkeys(i for step in clean for i in step["evidence_ids"])]}}, indent=2))
    info["status"] = "draft"
    info["draft"] = str(folder / "SKILL.md")
    atomic_write(directory(identifier) / "manifest.json", json.dumps(info, indent=2))
    return folder / "SKILL.md"


def install(identifier: str) -> Path:
    """An explicit user review/install action. Installed packages remain disabled."""
    from . import extensions
    info = read(identifier)
    if info["status"] != "draft":
        raise ValueError("Create and review a draft before installing")
    name = "learned-" + identifier
    target = config.DATA_DIR / "skills" / name
    if target.exists() or target.is_symlink():
        raise ValueError("This skill is already installed; review edits in place")
    extensions.set_enabled("skill:" + name, False)
    # Keep incomplete bundles outside skill discovery, including on validation failure.
    with tempfile.TemporaryDirectory(prefix="skill-install-", dir=config.DATA_DIR) as temporary:
        stage = Path(temporary) / name
        shutil.copytree(directory(identifier) / "draft", stage)
        evidence = json.loads((stage / "evidence.json").read_text())
        (stage / "frames").mkdir()
        recorded_frames = {frame["file"] for frame in info["frames"]}
        for file in {f for step in evidence["steps"] for f in step["frames"]}:
            source = directory(identifier) / file
            if (file not in recorded_frames or source.is_symlink()
                    or not source.resolve().is_relative_to(directory(identifier).resolve())):
                raise ValueError("Invalid frame reference")
            shutil.copy2(source, stage / file)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        stage.rename(target)
    return target
