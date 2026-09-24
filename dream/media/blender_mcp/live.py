"""Dream's live Blender, inside its sandbox (DREAM-109).

Run by dream.media.blender_live inside ONE bubblewrap sandbox: this process is the Blender
MCP bridge on stdio (bridge.py), and the Blender it drives is its child. Blender starts on
the first tool call, not with the session, so a session that never uses Blender opens no
window. When Dream closes the MCP session, stdin ends, this process returns, and the
sandbox ends with it: bubblewrap kills every process left in the sandbox's PID namespace.

Blender draws into its own nested display, never the owner's. The launcher outside the
sandbox starts that display when this bridge asks on the control socket ("open"), stops it
("close") and names its window on the owner's screen ("window"). Its socket appears in
/tmp/.X11-unix here, and /tmp/xauth holds the session's cookie for it.

Only Python's standard library and the MCP package (Dream's own, mounted read-only) run
here. Nothing in this folder is imported by Dream itself.
"""
from __future__ import annotations

import argparse
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOST, PORT = "127.0.0.1", 9876
START_TIMEOUT_S = 90.0
LOG = Path("/tmp/blender-live.log")  # the sandbox's private /tmp
XAUTHORITY = "/tmp/xauth"


class Control:
    """The bridge's side of the launcher's control socket: one request, one reply line."""

    def __init__(self, descriptor: int) -> None:
        self._stream = socket.socket(fileno=descriptor).makefile("rwb", buffering=0)
        self._lock = threading.Lock()

    def request(self, command: str) -> str:
        with self._lock:
            self._stream.write(command.encode("ascii") + b"\n")
            reply = self._stream.readline().decode("utf-8", "replace").strip()
        if reply == "ok" or reply.startswith("ok "):
            return reply[3:]
        raise RuntimeError(reply[6:] if reply.startswith("error ") else "the launcher did not answer")


class LiveBlender:
    """One Blender for this sandbox: started on demand, started again if it was closed."""

    def __init__(self, blender: str, *, background: bool, control: Control, screen: tuple[int, int]) -> None:
        self.blender = blender
        self.background = background
        self.control = control
        self.screen = screen
        self.process: subprocess.Popen | None = None
        self.generation = 0
        self._lock = threading.Lock()

    def ensure(self) -> tuple[str, int, int]:
        with self._lock:
            if self.process is not None and self.process.poll() is None:
                return HOST, PORT, self.generation
            self._start()
            return HOST, PORT, self.generation

    def _start(self) -> None:
        display = self.control.request("open")  # the nested display, started outside this sandbox
        argv = [self.blender, "--factory-startup"]
        if self.background:
            argv.append("--background")
        else:  # fill the nested screen; there is no window manager there to place it
            argv += ["--window-geometry", "0", "0", str(self.screen[0]), str(self.screen[1])]
        argv += ["--python", str(HERE / "startup.py")]
        env = {**os.environ, "DISPLAY": display, "XAUTHORITY": XAUTHORITY}
        with open(LOG, "ab") as log:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env)
        self.process = process
        self.generation += 1
        threading.Thread(target=self._watch, args=(process,), daemon=True).start()
        deadline = time.monotonic() + START_TIMEOUT_S
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Blender exited with status {process.returncode} before it was "
                                   f"ready. Its log ends:\n{_tail(LOG)}")
            try:
                with socket.create_connection((HOST, PORT), timeout=0.5):
                    return
            except OSError:
                time.sleep(0.1)
        self._stop_blender()
        raise RuntimeError(f"Blender did not answer within {START_TIMEOUT_S:.0f} s. Its log ends:\n{_tail(LOG)}")

    def _watch(self, process: subprocess.Popen) -> None:
        """When this Blender ends (the owner quit it, or it crashed), its nested display closes too."""
        process.wait()
        with self._lock:
            if self.process is process:
                self.process = None
                try:
                    self.control.request("close")
                except (OSError, RuntimeError):
                    pass

    def opened_note(self, first: bool) -> str:
        where = ("without a window (background mode)" if self.background
                 else "in its own window (a nested display) the owner can watch")
        if first:
            return f"Opened live Blender {where}. It is confined to the workspace."
        return f"Blender had closed, so live Blender opened again {where}, with a new unsaved scene."

    def find_window(self) -> str:
        """The nested display's window on the owner's screen (hex X11 id), for Dream's computer use."""
        self.ensure()
        if self.background:
            raise RuntimeError("Blender is running without a window (background mode), so there is no "
                               "window to attach to.")
        return self.control.request("window")

    def _stop_blender(self) -> None:
        process, self.process = self.process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def close(self) -> None:
        with self._lock:
            self._stop_blender()
        try:
            self.control.request("close")
        except (OSError, RuntimeError):
            pass


def _tail(path: Path, limit: int = 2000) -> str:
    try:
        return path.read_bytes()[-limit:].decode("utf-8", "replace")
    except OSError:
        return "(no log)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-packages", required=True, help="the read-only folder holding the mcp package")
    parser.add_argument("--blender", default="/usr/bin/blender")
    parser.add_argument("--control-fd", type=int, required=True, help="the launcher's control socket")
    parser.add_argument("--screen", default="1600x1000", help="the nested display's size, WxH")
    parser.add_argument("--background", action="store_true", help="no window (tests)")
    args = parser.parse_args(argv)
    sys.path[:0] = [str(HERE), args.site_packages]
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    import bridge

    width, height = (int(v) for v in args.screen.split("x"))
    live = LiveBlender(args.blender, background=args.background, control=Control(args.control_fd),
                       screen=(width, height))
    bridge.ensure_blender = live.ensure
    bridge.opened_note = live.opened_note

    @bridge.mcp.tool()
    def get_window() -> str:
        """
        The live Blender window's X11 id on the owner's screen (the nested display Blender fills),
        for computer use: computer_open(kind="desktop", window_id=<id>) attaches to it, then
        computer_observe looks and computer_action clicks or types. Starts Blender if it is not running.
        """
        try:
            return f"Blender window: {live.find_window()}"
        except Exception as e:
            return f"Error finding the Blender window: {e}"

    try:
        bridge.mcp.run()
    finally:
        live.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
