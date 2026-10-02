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
import sys
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
# How long a supervisor (`ie supervise`, DREAM-144) may take to stop: every child gets its own /admin/shutdown and
# the slowest child's load plus its stop bounds it. stop() waits this long; the engine guard gives it the same
# time after SIGTERM before SIGKILL (a plain `ie serve` keeps the guard's 120 s).
SUPERVISOR_STOP_S = 600.0

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
    """Something answers /v1/models with 200 (a 4xx from another process on the port is not an engine)."""
    try:
        return httpx.get(BASE_URL + "/models", timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def model_loaded(timeout: float = 2.0) -> bool:
    """A model is loaded and answering (Sleepwalk, DREAM-159). `ie serve` only listens once its model is loaded; a
    supervised layout's front answers /v1/models at once and gives each model a status, so one must be "ready"."""
    try:
        response = httpx.get(BASE_URL + "/models", timeout=timeout)
        models = response.json().get("data") if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError, AttributeError):
        return False
    if not isinstance(models, list):
        return False
    states = [m["status"] for m in models if isinstance(m, dict) and "status" in m]
    return not states or "ready" in states


def _root_url() -> str:
    return BASE_URL.removesuffix("/v1")


def choose_main(models: list[dict], default: str | None = None, preferred: str | None = None) -> str | None:
    """Which listed model Dream treats as main (DREAM-144). One model: that one. Several (a supervised layout's
    /v1/models lists every server by name, with `root` = the model file's id): `preferred` (roles.main.model)
    when it is a listed id, or the one entry whose `root` it names; else the supervisor's `default` when listed;
    else the first listed, as before."""
    ids = [m["id"] for m in models]
    if not ids:
        return None
    if len(ids) == 1:
        return ids[0]
    chosen = _by_preference(models, preferred)
    if chosen is not None:
        return chosen
    if default in ids:
        return default
    return ids[0]


def _by_preference(models: list[dict], preferred: str | None) -> str | None:
    """The listed id `preferred` names, directly or as the one entry with that `root`; None when it names none."""
    if preferred is None:
        return None
    if any(m["id"] == preferred for m in models):
        return preferred
    by_root = [m["id"] for m in models if m.get("root") == preferred]
    return by_root[0] if len(by_root) == 1 else None


def main_role() -> str | None:
    """roles.main.model from the settings file; an unusable file is the session's to report, never the attach's."""
    try:
        from ..core.settings import main_model
        return main_model()
    except Exception:
        return None


def served_model_id() -> str | None:
    """The model id Dream attaches to: the one served, or among several (a supervised layout) the choice of
    `choose_main` -- roles.main.model, else the supervisor's default from /health, else the first listed."""
    try:
        data = httpx.get(BASE_URL + "/models", timeout=3).json()
        models = [m for m in data.get("data") or [] if isinstance(m, dict) and isinstance(m.get("id"), str)]
    except Exception:
        return None
    if len(models) <= 1:
        return choose_main(models)
    preferred = main_role()
    default = None
    if _by_preference(models, preferred) is None:      # the role did not decide: the supervisor's default may
        try:
            health = httpx.get(_root_url() + "/health", timeout=3).json()
            default = health.get("default") if isinstance(health, dict) else None
        except Exception:
            default = None
    return choose_main(models, default, preferred)


def served_lanes(timeout: float = 3.0) -> int | None:
    """How many requests the running server serves at once (DREAM-151): its /props `total_slots` (`ie serve
    --parallel N`), or None when it does not say or does not answer."""
    try:
        response = httpx.get(_root_url() + "/props", timeout=timeout)
        props = response.json() if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    slots = props.get("total_slots") if isinstance(props, dict) else None
    return slots if type(slots) is int and slots >= 1 else None


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


def _cmdline(pid: int) -> list[str] | None:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part] or None


def served_model_path() -> Path | None:
    """The weights the running server loaded, so an attached session can read their capabilities (DREAM-093).

    First the server Dream launched (pid file -> its command line -> the argument after `serve`), then the one local
    model whose name is the served id. Two candidates, or none, is None: never a guess.
    """
    served = served_model_id()
    if not served:
        return None
    try:
        args = _cmdline(int(_pid_file().read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        args = None
    if args and "serve" in args[:-1]:
        path = Path(args[args.index("serve") + 1]).expanduser()
        path = (path if path.is_absolute() else MACHX_DIR / path).resolve()
        if served in (path.name, path.stem):
            return path
    matches = {m.path for m in list_models_detailed() if served in (m.name, m.path.name, m.path.stem)}
    return matches.pop() if len(matches) == 1 else None


def _log_file() -> Path:
    return config.LOG_DIR / "machx.log"


def log_mark() -> int:
    """Where machx.log ends now: a launch reads its own lines from here (launch_error)."""
    try:
        return _log_file().stat().st_size
    except OSError:
        return 0


# What `ie serve` prints when it refuses to start (src/cli/main.cpp): a bad option, or a load the engine refused -- for
# lanes, the numbers of the VRAM they need (P4 B1).
_LAUNCH_ERRORS = ("load failed:", "invalid options:")


def launch_error(since: int) -> str | None:
    """The engine's own reason a launch did not come up (DREAM-151: "say so plainly"): the last `load failed:` or
    `invalid options:` line written to machx.log after `since` (log_mark() before the launch), without its time stamp;
    None when there is none. At most the log's last 64 KiB after `since` are read, and the line is capped."""
    try:
        with _log_file().open("rb") as log:
            end = log.seek(0, os.SEEK_END)
            log.seek(max(since, end - 65536))
            text = log.read().decode("utf-8", "replace")
    except OSError:
        return None
    for line in reversed(text.splitlines()):
        for marker in _LAUNCH_ERRORS:
            if marker in line:
                return line[line.index(marker):].strip()[:600]
    return None


def _command(args: list[str]) -> list[str]:
    # All caller-controlled values are shell-quoted once. env.sh is the only
    # reason to use bash; model paths and stop strings are never executable text.
    return ["bash", "-lc", "source scripts/env.sh && exec " + shlex.join(args)]


def _stamped(command: list[str]) -> list[str]:
    """The serve command with the engine's stdout and stderr through log_stamp.py (DREAM-127, fix list #102):
    each machx.log line gets a wall-clock prefix. The shell redirects its own output first, so env.sh's lines are
    stamped too, and still `exec`s the engine: the pid Dream records, signals and reads the args of stays the
    engine's. The stamper writes to the shell's stdout at that point, the log file `serve` opened."""
    stamper = f"exec {shlex.quote(sys.executable)} -I {shlex.quote(str(Path(__file__).with_name('log_stamp.py')))}"
    return [*command[:-1], f"exec > >({stamper}) 2>&1; " + command[-1]]


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


def lanes_queue(options: dict | None) -> int | None:
    """DREAM-209: `--max-queue` for a launch with these options, or None at one lane, where nothing is read and the
    command is today's. Past --max-queue waiting requests the engine answers 429 at once (its default is 8). With
    lanes -- from engine.parallel or the model's own `parallel` -- there is room for every worker one reply may start
    and the lead: max(8, nested.max_workers + 1). It is read at each launch (an engine already running keeps its
    queue). An unusable nested section raises, naming the file and the fix; the launchers ask before they announce
    the load, as they do for their other refusals."""
    if (options or {}).get("parallel", 1) <= 1:
        return None
    from ..core.settings import _MAX_WORKERS_TEXT, _NESTED_SHAPE_TEXT, nested_max_workers
    try:
        cap = nested_max_workers()
    except ValueError as exc:
        fix = ("set nested.max_workers to 3, 7, 11 or 15, or remove it" if _MAX_WORKERS_TEXT in str(exc) else
               "make nested an object holding only max_workers (3, 7, 11 or 15), or remove the nested section"
               if _NESTED_SHAPE_TEXT in str(exc) else "repair the file")
        raise ValueError(f"{exc}. A launch with lanes sizes the engine's queue from it: {fix}, then load the model "
                         "again.") from exc
    return max(8, cap + 1)


def serve(model_path: Path, gpus: int | None = None, ctx: int | None = None,
          options: dict | None = None, *, keep_hot: bool = False) -> subprocess.Popen:
    """Launch `ie serve`. The engine is tied to this process (fix #51): when it dies by any means the
    engine gets SIGTERM, then SIGKILL after a grace period -- unless `keep_hot` asks it to outlive us."""
    from .settings import server_args
    from .load_lock import load_lock
    from . import engine_guard

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
    queue = lanes_queue(options)            # DREAM-209: raises before anything loads when the cap cannot be read
    if queue is not None:
        args.extend(["--max-queue", str(queue)])
    # DREAM-154: Dream's routing-profile default goes on a one-lane launch only: the V4.1 engine refuses
    # IE_DS41_PROFILE_OUT at --parallel > 1 (P4 B6b), and no other engine reads it.
    proc = _launch(args, keep_hot=keep_hot, profile=(options or {}).get("parallel", 1) == 1)
    # Always reset this value, including a default-context launch after a big one.
    os.environ["DREAM_MACHX_CTX"] = str(ctx or 8192)
    return proc


def supervise(layout_path: Path, *, keep_hot: bool = False) -> subprocess.Popen:
    """Launch `ie supervise --config <layout>` (DREAM-144; the engine's docs/serve_config.md) instead of one
    `ie serve`: every server of the layout on its own cards, behind one front on Dream's host and port (the
    `--host/--port` here override the layout's front so Dream finds it at BASE_URL). Same guard, log and pid file
    as serve(). Readiness is `wait_ready(proc, ready=supervisor_ready)`; stop() knows a supervisor."""
    layout_path = Path(layout_path).expanduser()
    if not layout_path.is_file():
        raise ValueError(f"Layout file not found: {layout_path}")
    args = ["./build/src/ie", "supervise", "--config", str(layout_path.resolve()), "--host", HOST, "--port", str(PORT)]
    return _launch(args, keep_hot=keep_hot, grace_s=SUPERVISOR_STOP_S, profile=not _layout_has_lanes(layout_path))


def _layout_has_lanes(layout_path: Path) -> bool:
    """DREAM-155 (DREAM-154 gate finding 2): whether a server of the layout runs lanes (`parallel` above 1). The
    supervisor passes its environment to every child, and a V4.1 child with lanes refuses IE_DS41_PROFILE_OUT, so
    Dream's routing-profile default stays out of such a layout (no other engine reads it). A file Dream cannot read
    keeps today's launch: the supervisor refuses it itself."""
    from ..core.engine_layout import read_layout
    try:
        servers = read_layout(layout_path).values()
    except ValueError:
        return False
    return any(type(server.get("parallel")) is int and server["parallel"] > 1 for server in servers)


def _launch(args: list[str], *, keep_hot: bool, grace_s: float | None = None, profile: bool = True) -> subprocess.Popen:
    from .load_lock import load_lock
    from . import engine_guard

    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    # Fix list #49: the engine records a decode routing profile only when IE_DS41_PROFILE_OUT is set,
    # and the profile is what a Dream-shaped expert placement is built from. Default it (the engine
    # keeps the counts across restarts); an explicit value in the environment wins. Not with lanes
    # (`profile` False, DREAM-154): the engine refuses it there.
    env = dict(os.environ)
    if profile:
        env.setdefault("IE_DS41_PROFILE_OUT", str(Path.home() / ".cache" / "machx-ie" / "ds41-dream-profile.txt"))
    with load_lock(PORT) as launch_fd, _log_file().open("a", encoding="utf-8") as log:
        proc = engine_guard.spawn(
            _stamped(_command(args)), keep_hot=keep_hot, log_path=_log_file(), grace_s=grace_s,
            cwd=MACHX_DIR, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            pass_fds=(launch_fd,),
        )
    _pid_file().write_text(str(proc.pid), encoding="utf-8")
    return proc


def supervisor_ready(timeout: float = 2.0) -> bool:
    """A supervised layout has finished loading: the front's /health lists its servers, at least one is `ready`
    and none is still `loading` (one that exited is reported by the launcher; the others serve). False for a
    plain `ie serve` (no `servers`) and for nothing listening."""
    try:
        body = httpx.get(_root_url() + "/health", timeout=timeout).json()
    except Exception:
        return False
    servers = body.get("servers") if isinstance(body, dict) else None
    if not isinstance(servers, dict):
        return False
    states = [s.get("state") for s in servers.values() if isinstance(s, dict)]
    return "ready" in states and "loading" not in states


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
    ready=None,
) -> bool:
    """Wait for the engine to start serving (`ready`: the test, default `is_serving`; a supervised layout
    passes `supervisor_ready`, because its front answers before its children have loaded).

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
    ready = ready or is_serving
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
        if ready():
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


def left_running() -> int | None:
    """The pid of an engine Dream launched that is still alive (a supervisor still stopping after stop()'s wait,
    DREAM-144), or None: nothing Dream started is running."""
    try:
        pid = int(_pid_file().read_text().strip())
    except Exception:
        return None
    return pid if _alive(pid) else None


def _is_supervisor(pid: int) -> bool:
    """The recorded process is `ie supervise` (its command line, or the shell about to exec it)."""
    return any(part == "supervise" or " supervise " in part for part in _cmdline(pid) or [])


def _stop_supervisor(pid: int, timeout: float) -> bool:
    """Stop a supervisor through its front's POST /admin/shutdown (the same as SIGTERM to it: it tells every
    child to stop and waits for them, up to the slowest child's load plus its stop), then wait -- and never
    SIGKILL it: a killed supervisor leaves its children running on their cards (the engine's serve_config.md).
    One still stopping after `timeout` is left running with its pid file, so a later stop can look again."""
    try:
        httpx.post(_root_url() + "/admin/shutdown", timeout=5)
    except httpx.HTTPError:
        _signal_group(pid, signal.SIGTERM)          # the front is not answering; SIGTERM is its orderly stop too
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.25)
    if _alive(pid):
        return False
    _pid_file().unlink(missing_ok=True)
    return True


def stop(timeout: float = 120.0, *, supervisor_timeout: float = SUPERVISOR_STOP_S) -> bool:
    """Stop the MachX server and free its GPU memory: SIGTERM the process group,
    escalate to SIGKILL if it lingers past ``timeout``. Idempotent — safe to call
    when nothing is running. Returns True if a live server was actually stopped.
    A supervisor (`ie supervise`, DREAM-144) is asked to shut down through its front and waited for up to
    ``supervisor_timeout``, never killed."""
    pid: int | None = None
    try:
        pid = int(_pid_file().read_text().strip())
    except Exception:
        pass
    if pid is None or not _alive(pid):
        _pid_file().unlink(missing_ok=True)  # stale or absent — nothing to do
        return False
    if _is_supervisor(pid):
        return _stop_supervisor(pid, supervisor_timeout)
    _signal_group(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.25)
    if _alive(pid):
        _signal_group(pid, signal.SIGKILL)
        time.sleep(0.5)
    _pid_file().unlink(missing_ok=True)
    return True
