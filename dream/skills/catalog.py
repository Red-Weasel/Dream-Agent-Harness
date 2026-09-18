"""Read installed Codex plugin metadata to locate skills, without importing code."""

from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path


def _version_key(path: Path) -> tuple:
    # Prefer a local checkout, then the newest numbered release. Hash revisions
    # have no sortable version, so use the most recently installed directory.
    if path.name == "local":
        return (2, (), 0, path.name)
    if re.fullmatch(r"\d+(?:\.\d+)*", path.name):
        return (1, tuple(int(p) for p in path.name.split(".")), 0, path.name)
    return (0, (), path.stat().st_mtime_ns, path.name)


def plugin_skill_dirs() -> list[Path]:
    """One installed revision per enabled plugin; stale cache copies stay out.

    Codex config identifies enabled local plugins, while remote installs carry a
    marker. An explicit disabled config entry wins over a remote install marker.
    Version choice is the newest installed release, not proof of a running
    client's active revision. Only skill directories inside a package are read.
    """
    codex = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    cache = codex / "plugins/cache"
    try:
        with (codex / "config.toml").open("rb") as stream:
            settings = tomllib.load(stream).get("plugins", {})
    except (OSError, ValueError):
        settings = {}
    if not isinstance(settings, dict):
        settings = {}
    installed = {key for key, value in settings.items()
                 if isinstance(value, dict) and value.get("enabled") is True}
    for marker in cache.glob("*/*/.codex-remote-plugin-install.json"):
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not data.get("remote_plugin_id"):
                continue
        except (OSError, ValueError):
            continue
        installed.add(f"{marker.parent.name}@{marker.parent.parent.name}")
    roots = []
    # Current official packages precede the historical openai-curated mirror.
    for identifier in sorted(installed, key=lambda key: tuple(reversed(key.split("@")))):
        state = settings.get(identifier, {})
        if isinstance(state, dict) and state.get("enabled") is False:
            continue
        parts = identifier.split("@")
        if len(parts) != 2 or any(not re.fullmatch(r"[\w.-]+", p) or p in {".", ".."} for p in parts):
            continue
        name, marketplace = parts
        try:
            versions = [p for p in (cache / marketplace / name).iterdir()
                        if p.is_dir() and not p.name.startswith(".")]
            if not versions:
                continue
            package = max(versions, key=_version_key).resolve()
            manifest = next((p for p in (package / ".codex-plugin/plugin.json",
                                         package / ".claude-plugin/plugin.json") if p.is_file()), None)
            data = json.loads(manifest.read_text(encoding="utf-8")) if manifest else {}
            if not isinstance(data, dict):
                continue
            declared = data.get("skills", "./skills")
            paths = [declared] if isinstance(declared, str) else declared
            if not isinstance(paths, list):
                continue
            for raw in paths:
                if not isinstance(raw, str):
                    continue
                root = (package / raw).resolve()
                if root.is_relative_to(package) and root.is_dir():
                    roots.append(root)
        except (OSError, ValueError):
            continue
    return roots
