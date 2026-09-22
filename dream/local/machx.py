"""Manage the MachX inference engine: discover GGUF models, launch `ie serve`, and
health-check its OpenAI-compatible server on :11435.

MachX needs its oneAPI/SYCL environment, so serving runs through
``bash -lc 'cd <dir> && source scripts/env.sh && ie serve …'``.
"""

from __future__ import annotations

import os
import json
import shlex
import signal
import subprocess
import time
from pathlib import Path

import httpx

from .. import config

# Default: a clone of github.com/Red-Weasel/machx-inference-engine in the home directory.
MACHX_DIR = Path(os.environ.get("DREAM_MACHX_DIR", str(Path.home() / "machx-inference-engine")))
IE_BIN = MACHX_DIR / "build" / "src" / "ie"
ENV_SH = MACHX_DIR / "scripts" / "env.sh"
HOST = os.environ.get("DREAM_MACHX_HOST", "127.0.0.1")
PORT = int(os.environ.get("DREAM_MACHX_PORT", "11435"))
BASE_URL = f"http://{HOST}:{PORT}/v1"

# Default per-user model locations; environment overrides take precedence.
_MODEL_DIRS = [
    Path(os.environ.get("DREAM_MODELS_DIR", str(Path.home() / "models"))),
    MACHX_DIR / "models",
    Path.home() / "models",
    Path.home() / "llama.cpp" / "models",
]


def available() -> bool:
    return IE_BIN.exists()


def list_models() -> list[tuple[str, Path, float]]:
    """(display name, path, size_GB) for every discoverable GGUF, de-duplicated.

    Delegates to ``models.scan_models`` (multi-drive + volume tags) but keeps the
    3-tuple return so existing callers (the launcher) are unchanged."""
    return [(m.name, m.path, m.size_gb) for m in list_models_detailed()]


def list_models_detailed() -> list["LocalModel"]:
    """Like ``list_models`` but with the full ``LocalModel`` (adds ``volume``)."""
    from .models import scan_models  # lazy: models imports machx

    return scan_models()


def is_serving(timeout: float = 2.0) -> bool:
    try:
        return httpx.get(BASE_URL + "/models", timeout=timeout).status_code < 500
    except httpx.HTTPError:
        return False


def served_model_id() -> str | None:
    try:
        data = httpx.get(BASE_URL + "/models", timeout=3).json()
        models = data.get("data") or []
        if models:
            return models[0].get("id")
    except Exception:
        return None
    return None


def residency_summary() -> str | None:
    """Report engine allocation counters; never infer residency from file size."""
    try:
        response = httpx.get(BASE_URL.removesuffix("/v1") + "/props", timeout=3)
        response.raise_for_status()
        props = response.json()
        memory = props.get("memory_residency") if isinstance(props, dict) else None
        if not isinstance(memory, dict):
            return None
        pinned, mapped = memory.get("host_pinned_bytes"), memory.get("host_mmap_bytes")
        caches = memory.get("gpu_expert_cache_bytes")
        if not isinstance(caches, list) or not 1 <= len(caches) <= 2:
            return None
        if any(type(v) is not int or not 0 <= v < 2**64 for v in [pinned, mapped, *caches]):
            return None
        gpu = " / ".join(f"{v / 2**30:.1f}" for v in caches)
        state = (f"partial host residency: {mapped / 2**30:.1f} GiB remains memory-mapped and may read from disk"
                 if mapped else "all active expert banks pinned")
        return f"GLM memory: {pinned / 2**30:.1f} GiB pinned host banks; GPU expert caches {gpu} GiB; {state}."
    except (httpx.HTTPError, ValueError):
        return None


def _pid_file() -> Path:
    return config.VAR_DIR / "machx.pid"


def _log_file() -> Path:
    return config.LOG_DIR / "machx.log"


def _command(args: list[str]) -> list[str]:
    # All caller-controlled values are shell-quoted once. env.sh is the only
    # reason to use bash; model paths and stop strings are never executable text.
    return ["bash", "-lc", "source scripts/env.sh && exec " + shlex.join(args)]


def capabilities(model_path: Path) -> dict:
    """Read architecture capabilities without allocating GPU/model weights."""
    result = subprocess.run(
        _command(["./build/src/ie", "capabilities", str(model_path)]),
        cwd=MACHX_DIR, capture_output=True, text=True, timeout=60,
    )
    if result.returncode:
        raise ValueError("MachX capability check failed. Rebuild the engine, then retry. "
                         + result.stderr.strip()[-600:])
    try:
        # env.sh may print diagnostics; the command's final line is JSON.
        data = json.loads(result.stdout.strip().splitlines()[-1])
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("unsupported capability schema")
        return data
    except (ValueError, IndexError) as exc:
        raise ValueError("MachX returned invalid capabilities. Rebuild the engine.") from exc


def serve(model_path: Path, gpus: int | None = None, ctx: int | None = None,
          options: dict | None = None) -> subprocess.Popen:
    from .settings import server_args
    from .load_lock import load_lock

    args = ["./build/src/ie", "serve", str(model_path), "--host", HOST, "--port", str(PORT)]
    if gpus is not None:
        if isinstance(gpus, bool) or not isinstance(gpus, int) or gpus < 1:
            raise ValueError("GPUs must be a positive integer")
        args.extend(["--gpus", str(gpus)])
    if ctx is not None:
        if isinstance(ctx, bool) or not isinstance(ctx, int) or not 9 <= ctx <= 2**31 - 1:
            raise ValueError("Context must be an integer from 9 to 2147483647")
        args.extend(["--ctx", str(ctx)])
    args.extend(server_args(options or {}, ctx=ctx or 8192, gpus=gpus))
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    # Fix list #49: the engine records a decode routing profile only when IE_DS41_PROFILE_OUT is set,
    # and the profile is what a Dream-shaped expert placement is built from. Default it (the engine
    # keeps the counts across restarts); an explicit value in the environment wins.
    env = dict(os.environ)
    env.setdefault("IE_DS41_PROFILE_OUT", str(Path.home() / ".cache" / "machx-ie" / "ds41-dream-profile.txt"))
    with load_lock(PORT) as launch_fd, _log_file().open("a", encoding="utf-8") as log:
        proc = subprocess.Popen(
            _command(args), cwd=MACHX_DIR, env=env,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            pass_fds=(launch_fd,),
        )
    # Always reset this value, including a default-context launch after a big one.
    os.environ["DREAM_MACHX_CTX"] = str(ctx or 8192)
    _pid_file().write_text(str(proc.pid), encoding="utf-8")
    return proc


def server_started_at() -> float | None:
    """Epoch start time of the engine process Dream launched (from its pid file and /proc), or
    None when there is no such process. Used by the inference lease to tell a stale record from
    a live one (fix #53)."""
    try:
        pid = int(_pid_file().read_text().strip())
        stat = Path(f"/proc/{pid}/stat").read_text()
        start_ticks = int(stat.rsplit(")", 1)[1].split()[19])      # field 22, after the comm field
        btime = next(int(line.split()[1]) for line in Path("/proc/stat").read_text().splitlines()
                     if line.startswith("btime"))
        return btime + start_ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, StopIteration, IndexError):
        return None


def wait_ready(
    proc: subprocess.Popen,
    timeout: int | None = None,
    *,
    stall_s: float = 300.0,
    max_s: float = 7200.0,
    poll_s: float = 1.5,
    log_path: Path | None = None,
) -> bool:
    """Wait for the engine to start serving.

    The timeout measures SILENCE, not elapsed time. A flat deadline cannot tell
    a slow load from a stuck one, and the difference is entirely about where the
    weights live: DeepSeek-V4 uploads a ~147 GB expert pool, ~4 minutes read from
    NVMe and 20-25 minutes from a USB HDD at ~120 MB/s. The old 600 s deadline
    fired mid-load, and the caller then STOPPED the half-loaded server — twenty
    minutes of loading discarded, with the drive blamed for being slow.

    The loader writes a line per layer as the pool goes up, so progress is
    observable: while the log grows, the load is alive and gets more time.
    ``stall_s`` is how long it may say NOTHING before we give up; ``max_s`` is
    the backstop for an engine that chatters forever without ever serving.

    ``timeout`` is accepted for compatibility with older callers and is treated
    as the stall window.
    """
    if timeout is not None:
        stall_s = float(timeout)
    log = Path(log_path) if log_path is not None else _log_file()

    def log_mark() -> tuple[int, float]:
        """Size and mtime — either moving means the engine is still working."""
        try:
            st = log.stat()
            return st.st_size, st.st_mtime
        except OSError:
            return -1, -1.0

    start = time.monotonic()
    last_change = start
    mark = log_mark()
    while True:
        if proc.poll() is not None:  # died during load
            return False
        if is_serving():
            return True
        now = time.monotonic()
        cur = log_mark()
        if cur != mark:
            mark, last_change = cur, now
        if now - last_change > stall_s:
            return False            # silent for too long: genuinely stuck
        if now - start > max_s:
            return False            # backstop
        time.sleep(poll_s)


def _zombie(pid: int) -> bool:
    """True when the pid is a dead child waiting to be reaped. ``ie`` is spawned as
    our own child, so when it dies its corpse lingers with the pid still signalable
    — without this a dead server reads as a running one."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False  # no procfs to judge by — trust the signal check
    # The comm field can contain spaces and parens; state is the token after the last ')'.
    fields = stat.rpartition(")")[2].split()
    return bool(fields) and fields[0] == "Z"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return not _zombie(pid)


def _signal_group(pid: int, sig: int) -> None:
    try:
        os.killpg(os.getpgid(pid), sig)
    except Exception:
        try:
            os.kill(pid, sig)
        except Exception:
            pass


def stop(timeout: float = 8.0) -> bool:
    """Stop the MachX server and free its GPU memory: SIGTERM the process group,
    escalate to SIGKILL if it lingers past ``timeout``. Idempotent — safe to call
    when nothing is running. Returns True if a live server was actually stopped."""
    pid: int | None = None
    try:
        pid = int(_pid_file().read_text().strip())
    except Exception:
        pass
    if pid is None or not _alive(pid):
        _pid_file().unlink(missing_ok=True)  # stale or absent — nothing to do
        return False
    _signal_group(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.25)
    if _alive(pid):
        _signal_group(pid, signal.SIGKILL)
        time.sleep(0.5)
    _pid_file().unlink(missing_ok=True)
    return True
