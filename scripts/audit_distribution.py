"""Check a built wheel's layout; this does not certify repository history clean."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    wheels = sorted(args.directory.glob("dream-*.whl"))
    if not wheels:
        parser.error("No Dream wheel found")
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            metadata_root = "-".join(wheel.name.split("-")[:2]) + ".dist-info"
            seen = set()
            files = set()
            parents = set()
            for entry in archive.infolist():
                name = entry.filename
                path = PurePosixPath(name)
                canonical = path.as_posix() + ("/" if entry.is_dir() else "")
                if (name != canonical or name != entry.orig_filename
                        or path.is_absolute() or ".." in path.parts
                        or "\\" in name or ":" in name
                        or any(ord(char) < 32 or ord(char) == 127 for char in name)
                        or len(path.parts) < 2
                        or path.parts[0] not in {"dream", metadata_root}
                        or any(part.endswith(".dist-info") for part in path.parts[1:])):
                    raise ValueError(f"Unexpected distribution path: {name}")
                if path.as_posix() in seen:
                    raise ValueError(f"Duplicate distribution path: {name}")
                ancestors = {parent.as_posix() for parent in path.parents}
                if ancestors & files or (not entry.is_dir() and path.as_posix() in parents):
                    raise ValueError(f"File/directory collision in distribution: {name}")
                seen.add(path.as_posix())
                parents.update(ancestors)
                if not entry.is_dir():
                    files.add(path.as_posix())
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError(f"Symlink in distribution: {name}")
                if any(part in {"data", "var", ".remember", ".dream", ".git", ".codex",
                                ".agents", ".claude", ".codebase-memory", "__pycache__",
                                "artifacts"} for part in path.parts):
                    raise ValueError(f"Runtime state in distribution: {name}")
                if path.suffix in {".db", ".sqlite", ".sqlite3", ".gguf", ".safetensors", ".onnx", ".log", ".pyc"} or path.name.startswith(".env"):
                    raise ValueError(f"Private/runtime file in distribution: {name}")
            required = {"dream/gui/static/controls.js", "dream/gui/static/companion.js",
                        "dream/gui/static/media.js", "dream/gui/static/media.css",
                        "dream/gui/static/prompt_optimizer.js", "dream/gui/static/prompt_optimizer.css",
                        "dream/gui/static/projects.js", "dream/gui/static/projects.css",
                        "dream/gui/static/turn-timing.js", "dream/gui/static/turn-timing.css",
                        "dream/media/player.html", "dream/media/cli.py",
                        "dream/desktop/window.py", "dream/resources/skills/illuminati-handshake/SKILL.md"}
            if required - set(names):
                raise ValueError(f"Distribution is missing runtime assets: {sorted(required - set(names))}")
        print(json.dumps({"wheel": wheel.name, "files": len(names), "bytes": wheel.stat().st_size,
                          "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(), "layout_audit": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
