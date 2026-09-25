"""Tie a spawned engine to the Dream process that owns it (fix #51, DREAM-110).

After a desktop crash, `ie serve` -- ~200 GiB of pinned host RAM and both GPUs -- ran on for 33
minutes with no owner: the process that launched it died before its `finally` could stop it, and
the engine runs in its own session, where no terminal hangup reaches it.

The owner is the process that spawns the engine and is responsible for stopping it: the desktop's
`python -m dream.desktop.startup run` (the session process; it holds the launch lock and stops the
engine in its `finally`) or the terminal `dream local` process. When it dies by any means -- exit,
crash, SIGKILL, or SIGHUP when the desktop window that hosts its terminal dies -- the engine goes too:

* PR_SET_PDEATHSIG: the kernel sends the engine SIGTERM, its orderly stop, when the owner dies. The
  signal follows the spawning THREAD, so it is armed only for a spawn from the main thread, which
  lives exactly as long as the process.
* A watchdog process (this file, run as a script) waits on pidfds of the owner and the engine. When
  the owner goes first it sends SIGTERM itself if the parent-death signal was not armed, waits
  DREAM_ENGINE_KILL_GRACE_S (default 10 s, the grace stop_owned gives) and SIGKILLs an engine that is
  still running. It leaves as soon as the engine exits.

`keep_hot` spawns untied: that engine is meant to outlive Dream. Standard library only: the watchdog
runs as `python -I <this file>`.
"""
from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

PR_SET_PDEATHSIG = 1
GRACE_ENV = "DREAM_ENGINE_KILL_GRACE_S"
DEFAULT_GRACE_S = 10.0

_prctl = None


def _parent_death_signal():
    """A preexec_fn that arms SIGTERM for the owner's death, or None off the main thread (there the
    signal would fire when the spawning thread ends, not when the owner does)."""
    global _prctl
    if threading.current_thread() is not threading.main_thread():
        return None
    if _prctl is None:
        import ctypes
        _prctl = ctypes.CDLL(None, use_errno=True).prctl      # resolved here, never in the child
    owner, prctl = os.getpid(), _prctl

    def arm():
        if prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0) != 0:
            raise OSError("prctl(PR_SET_PDEATHSIG) failed")
        if os.getppid() != owner:                             # the owner died before it was armed
            os._exit(1)
    return arm


def _note(log_path, text: str) -> None:
    if log_path is None:
        return
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"[dream engine guard {time.strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")


def _grace_s(log_path) -> float:
    raw = os.environ.get(GRACE_ENV)
    if raw is None:
        return DEFAULT_GRACE_S
    try:
        value = float(raw)
        if value >= 0 and value != float("inf"):
            return value
    except ValueError:
        pass
    _note(log_path, f"{GRACE_ENV}={raw!r} is not a number of seconds; using {DEFAULT_GRACE_S:g}")
    return DEFAULT_GRACE_S


def spawn(command, *, keep_hot: bool = False, log_path=None, **popen_kwargs):
    """`subprocess.Popen` for an engine this process owns. Unless `keep_hot`, the engine gets the
    parent-death signal and a watchdog; `proc.dream_watchdog` is the watchdog's Popen (None when
    untied). The watchdog notes what it does in `log_path` (the engine log)."""
    arm = None if keep_hot else _parent_death_signal()
    proc = subprocess.Popen(command, preexec_fn=arm, **popen_kwargs)
    proc.dream_watchdog = None if keep_hot else _watch(proc, term=arm is None, log_path=log_path)
    return proc


def _watch(proc, *, term: bool, log_path):
    """Start the watchdog for our own child `proc`. A pid that is not our child is never watched,
    let alone signalled: the guard is noted as not armed in the log instead."""
    try:
        os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)   # raises unless our child
        engine = os.pidfd_open(proc.pid)
    except (OSError, TypeError) as exc:
        _note(log_path, f"engine pid {getattr(proc, 'pid', '?')} is not tied to this process: {exc}")
        return None
    owner = os.pidfd_open(os.getpid())
    log = None
    try:
        log = open(log_path, "ab") if log_path is not None else None
        watchdog = subprocess.Popen(
            [sys.executable, "-I", str(Path(__file__).resolve()), str(owner), str(engine),
             str(_grace_s(log_path)), "1" if term else "0", str(os.getpid()), str(proc.pid)],
            pass_fds=(owner, engine), stdin=subprocess.DEVNULL,
            stdout=log if log is not None else subprocess.DEVNULL, stderr=subprocess.STDOUT,
            start_new_session=True)               # no terminal signal of the owner's reaches it
    except OSError as exc:
        # The engine is already running: never strand it by raising here. Say what is missing.
        _note(log_path, f"engine pid {proc.pid}: the watchdog did not start ({exc}); "
                        + ("only the parent-death signal ties it to this process" if not term
                           else "it is NOT tied to this process"))
        return None
    finally:
        os.close(owner)
        os.close(engine)
        if log is not None:
            log.close()
    # Reap it when it leaves (with the engine) so no zombie waits for the owner's exit.
    threading.Thread(target=watchdog.wait, name="dream-engine-watchdog", daemon=True).start()
    return watchdog


def _exits_within(fd: int, seconds: float) -> bool:
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    return bool(poller.poll(int(seconds * 1000)))


def _send(fd: int, sig: int) -> None:
    try:
        signal.pidfd_send_signal(fd, sig)
    except ProcessLookupError:
        pass


def main(argv: list[str]) -> int:
    """The watchdog: argv = owner_pidfd engine_pidfd grace_s send_term owner_pid engine_pid."""
    owner, engine = int(argv[0]), int(argv[1])
    grace, send_term, owner_pid, engine_pid = float(argv[2]), argv[3] == "1", argv[4], argv[5]
    poller = select.poll()
    poller.register(owner, select.POLLIN)
    poller.register(engine, select.POLLIN)
    while True:
        ready = {fd for fd, _ in poller.poll()}
        if engine in ready:
            return 0                          # the engine is gone: nothing left to guard
        if owner in ready:
            break

    def say(text):
        print(f"[dream engine watchdog {time.strftime('%Y-%m-%d %H:%M:%S')}] {text}", flush=True)

    if send_term:
        _send(engine, signal.SIGTERM)
        say(f"owner pid {owner_pid} exited; sent SIGTERM to engine pid {engine_pid}")
    else:
        say(f"owner pid {owner_pid} exited; engine pid {engine_pid} got SIGTERM from the kernel")
    if _exits_within(engine, grace):
        say(f"engine pid {engine_pid} stopped")
        return 0
    _send(engine, signal.SIGKILL)
    say(f"engine pid {engine_pid} still running {grace:g} s after SIGTERM; sent SIGKILL")
    _exits_within(engine, 5.0)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
