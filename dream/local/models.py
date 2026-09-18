"""Multi-drive GGUF and supported directory-checkpoint scanner with volume tags.

Extends ``machx.list_models``'s single-directory scan across every place a model
might live on this machine — the machx dirs, ``~/.seal/models``, and any mounted
volume under ``/media/<user>`` or ``/mnt`` — and tags each hit with the volume it
came from so the launcher/picker can show *where* a model lives (nvme vs. a USB
stick vs. a mounted disk). ``machx.list_models`` delegates here.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import machx

# GGUFs smaller than this are tokenizer/vocab shards, not weights — skip them.
MIN_SIZE_GB = 0.1

# Rented-model cache used by the `seal` tooling; a common off-machx location.
SEAL_MODELS_DIR = Path.home() / ".seal" / "models"


@dataclass(frozen=True)
class LocalModel:
    """A discoverable GGUF file or supported checkpoint directory."""

    name: str      # p.stem
    path: Path
    size_gb: float
    volume: str    # "nvme" | "usb" | "home" | mounted-volume slug


def _slug(label: str) -> str:
    """A tidy lowercase tag from a raw volume/mount label."""
    return "-".join(label.strip().lower().split()) or "disk"


def volume_for(path: Path | str) -> str:
    """Best-effort volume tag for a model path. Total — never raises.

    ``/media/<user>/<LABEL>/…`` → ``usb`` when LABEL mentions USB, else the label
    slug; ``/mnt/<x>/…`` → ``<x>`` slug; anything under ``/home`` → ``home``;
    everything else (root device) → ``nvme``.
    """
    try:
        parts = Path(path).parts
        if len(parts) >= 4 and parts[1] == "media":
            label = parts[3]
            return "usb" if "usb" in label.lower() else _slug(label)
        if len(parts) >= 3 and parts[1] == "mnt":
            return _slug(parts[2])
        if len(parts) >= 2 and parts[1] == "home":
            return "home"
    except Exception:
        return "nvme"
    return "nvme"


def _mounted_volume_roots() -> list[Path]:
    """Every mounted volume under ``/media/<user>`` and ``/mnt``. Never raises."""
    roots: list[Path] = []
    try:
        user = getpass.getuser()
    except Exception:
        user = os.environ.get("USER", "")
    bases = [Path("/mnt")]
    if user:
        bases.insert(0, Path("/media") / user)
    for base in bases:
        try:
            if not base.is_dir():
                continue
            for child in base.iterdir():
                try:
                    if child.is_dir():
                        roots.append(child)
                except OSError:
                    continue
        except OSError:
            continue
    return roots


def _base_roots() -> list[Path]:
    """The default set of directories to scan for GGUFs (before ``extra_roots``)."""
    roots: list[Path] = list(machx._MODEL_DIRS)
    roots.append(SEAL_MODELS_DIR)
    roots.extend(_mounted_volume_roots())
    env = os.environ.get("DREAM_MODELS_DIRS")
    if env:
        roots.extend(Path(p).expanduser() for p in env.split(":") if p.strip())
    return roots


# Bound explicit model-root scans by depth and elapsed time so a large or slow
# directory tree cannot keep the picker waiting indefinitely.
_SCAN_MAX_DEPTH = int(os.environ.get("DREAM_SCAN_MAX_DEPTH", "5"))
_SCAN_BUDGET_S = float(os.environ.get("DREAM_SCAN_BUDGET_S", "4"))
# Mounted volumes are speculative roots. Inspect their top-level names first,
# then search likely model-library directories within a separate bounded scope.
_MODEL_DIR_HINTS = ("model", "gguf", "llm", "weight", "checkpoint", "ollama", "hf")
# Depth INSIDE a directory that already announced itself as a model library.
_VOLUME_MAX_DEPTH = int(os.environ.get("DREAM_VOLUME_SCAN_DEPTH", "6"))
_VOLUME_BUDGET_S = float(os.environ.get("DREAM_VOLUME_SCAN_BUDGET_S", "6"))
# A volume with no obviously-named library still gets a glance, but a shallow one.
_VOLUME_FALLBACK_DEPTH = 2
_VOLUME_FALLBACK_BUDGET_S = 1.0
# Hard ceiling on a whole volume, enforced by abandoning the thread. The
# per-walk budgets cannot bound a syscall that is itself blocked on I/O.
_VOLUME_WALL_CLOCK_S = float(os.environ.get("DREAM_VOLUME_WALL_CLOCK_S", "8"))
_SCAN_SKIP_DIRS = {
    ".git", ".cache", "node_modules", "__pycache__", ".Trash-1000",
    "$RECYCLE.BIN", "System Volume Information", ".snapshots",
}


def _looks_like_models(name: str) -> bool:
    return any(h in name.lower() for h in _MODEL_DIR_HINTS)


def _volume_ggufs(vol: Path) -> list[Path]:
    """GGUFs on a mounted volume, found by NAME rather than by walking it.

    Runs on a daemon thread with a hard join timeout. The in-loop deadline in
    _find_ggufs cannot help when a single readdir blocks: the syscall itself
    stalls and the deadline is only checked between directories.
    A removable disk is never worth hanging the picker for, so if it does not
    answer in time we abandon it — the thread is read-only and dies with the
    process.
    """
    out: list[Path] = []
    worker = threading.Thread(target=lambda: out.extend(_volume_ggufs_blocking(vol)),
                              daemon=True)
    worker.start()
    worker.join(_VOLUME_WALL_CLOCK_S)
    if worker.is_alive():
        return []  # busy or sleeping disk; the internal roots still scanned fine
    return out


def _volume_ggufs_blocking(vol: Path) -> list[Path]:
    try:
        children = [c for c in vol.iterdir() if c.is_dir()]
    except OSError:
        return []
    libraries = [c for c in children if _looks_like_models(c.name)]
    found: list[Path] = []
    for lib in libraries:
        found.extend(_find_ggufs(lib, max_depth=_VOLUME_MAX_DEPTH,
                                 budget_s=_VOLUME_BUDGET_S))
    # Weights sitting loose at the top, or a drive that names its library
    # something unguessable: one shallow look so an odd layout isn't invisible.
    found.extend(_find_ggufs(vol, max_depth=_VOLUME_FALLBACK_DEPTH,
                             budget_s=_VOLUME_FALLBACK_BUDGET_S))
    # The library pass and the shallow fallback overlap near the top of the
    # drive; dedupe here rather than leaning on the caller to do it.
    return list(dict.fromkeys(found))


def _find_ggufs(
    root: Path, *, max_depth: int = _SCAN_MAX_DEPTH, budget_s: float = _SCAN_BUDGET_S
) -> list[Path]:
    """Bounded GGUF and directory-checkpoint candidates; no tensor reads."""
    found: list[Path] = []
    deadline = time.monotonic() + budget_s
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _e: None):
        if time.monotonic() > deadline:
            break
        here = Path(dirpath)
        if len(here.parts) - root_depth >= max_depth:
            dirnames[:] = []  # deep enough; don't descend further
        else:
            dirnames[:] = [d for d in dirnames
                           if d not in _SCAN_SKIP_DIRS and not d.startswith(".")]
        found.extend(here / f for f in filenames if f.endswith(".gguf"))
        if "config.json" in filenames and "model.safetensors.index.json" in filenames:
            found.append(here)
    return found


def read_checkpoint_json(path: Path) -> dict:
    """Bound config/index reads without opening any tensor files."""
    with path.open("rb") as stream:
        raw = stream.read(32 * 1024 * 1024 + 1)
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError("Checkpoint metadata exceeds read budget")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Checkpoint metadata must be an object")
    return value


def directory_model_files(path: Path) -> list[Path]:
    """Validate the directory format supported by MachX and its complete shard set."""
    config_path = path / "config.json"
    if read_checkpoint_json(config_path).get("model_type") != "deepseek_v41":
        raise ValueError("Unsupported directory checkpoint architecture")
    index_path = path / "model.safetensors.index.json"
    weights = read_checkpoint_json(index_path).get("weight_map")
    if not isinstance(weights, dict) or not weights:
        raise ValueError("Checkpoint index has no weight map")
    names = set()
    for name in weights.values():
        if (not isinstance(name, str) or Path(name).name != name
                or not name.endswith(".safetensors")):
            raise ValueError("Invalid checkpoint shard filename")
        names.add(name)
    if len(names) > 4096:
        raise ValueError("Checkpoint shard count exceeds budget")
    shards = [path / name for name in sorted(names)]
    if any(not p.is_file() or p.stat().st_size == 0 for p in shards):
        raise ValueError("Checkpoint shards are missing or empty")
    # Include tokenizer/config and engine-prepared banks in saved-model identity.
    auxiliary = sorted(p for p in path.iterdir()
                       if p.is_file() and p.suffix in {".json", ".ieslot", ".i32"}
                       and p not in (config_path, index_path))
    return [config_path, index_path, *shards, *auxiliary]


# "…-00002-of-00005" — the llama.cpp split-GGUF suffix. A split model is loaded
# by pointing at part 1; the loader reads its index and pulls in the siblings.
_SHARD_RE = re.compile(r"-(\d{5})-of-(\d{5})$")


def _group_shards(sized: list[tuple[Path, float]]) -> list[tuple[Path, float, int]]:
    """Collapse split GGUFs into one entry each: (part-1 path, total size, parts).

    Keyed by directory + base name, so two quantisations of the same model in
    sibling directories stay separate. A file with no shard suffix passes
    through untouched.
    """
    groups: dict[tuple[Path, str], list[tuple[int, Path, float]]] = {}
    singles: list[tuple[Path, float, int]] = []
    for path, size in sized:
        m = _SHARD_RE.search(path.stem)
        if not m:
            singles.append((path, size, 1))
            continue
        base = _SHARD_RE.sub("", path.stem)
        groups.setdefault((path.parent, base), []).append((int(m.group(1)), path, size))

    out = list(singles)
    for parts in groups.values():
        parts.sort()
        first = parts[0][1]  # lowest index present — normally 00001
        out.append((first, sum(p[2] for p in parts), len(parts)))
    return out


def scan_models(
    extra_roots: list[Path] | None = None,
    *,
    min_size_gb: float = MIN_SIZE_GB,
) -> list[LocalModel]:
    """GGUF files and supported directory checkpoints, deduped and volume-tagged.

    Filters match ``machx.list_models``: skip files under ``min_size_gb`` and any
    name containing "vocab", dedup by ``Path.resolve()``, and tolerate broken
    symlinks / unreadable files.
    """
    roots = _base_roots()
    if extra_roots:
        roots.extend(extra_roots)
    speculative = {p.resolve() for p in _mounted_volume_roots()}

    seen: set[Path] = set()
    out: list[LocalModel] = []
    for d in roots:
        try:
            if not d.exists():
                continue
            if d.resolve() in speculative:
                candidates = sorted(_volume_ggufs(d))
            else:
                candidates = sorted(_find_ggufs(d))
        except OSError:
            continue
        sized: list[tuple[Path, float]] = []
        for p in candidates:
            try:
                rp = p.resolve()
                if rp in seen:
                    continue
                if p.is_dir():
                    files = directory_model_files(p)
                    size = sum(f.stat().st_size for f in files
                               if f.suffix == ".safetensors") / 1e9
                    seen.add(rp)
                    if size >= min_size_gb:
                        out.append(LocalModel(name=f"{p.name} · Safetensors", path=p,
                                              size_gb=size, volume=volume_for(p)))
                    continue
                if not p.is_file():
                    continue
                size = p.stat().st_size / 1e9
            except (OSError, ValueError):
                continue  # broken symlink / unreadable
            if "vocab" in p.name.lower():
                continue
            seen.add(rp)
            sized.append((p, size))
        # Group split GGUFs BEFORE the size filter: shard 1 of a split model is
        # tiny (it carries the index, not weights), so filtering per-file dropped
        # exactly the file the loader must be pointed at, and listed the useless
        # remainder as N separate "models".
        for path, size, count in _group_shards(sized):
            if size < min_size_gb:
                continue
            name = _SHARD_RE.sub("", path.stem)
            if count > 1:
                name = f"{name}  ({count} parts)"
            out.append(LocalModel(name=name, path=path, size_gb=size,
                                  volume=volume_for(path)))
    return out
