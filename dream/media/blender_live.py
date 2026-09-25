"""Live Blender (DREAM-109): a real Blender window the model drives while the owner watches.

The model works in Blender through the Blender MCP bridge (scene, code, screenshots) and,
for what only the window shows, through computer use on that window. The owner steers in
chat. Blender, the Blender MCP add-on's server and the stdio bridge run in ONE bubblewrap
sandbox (dream.core.execution.blender_live_argv). Blender's Python can read and write the
workspace and nothing else of the owner's.

Blender never sees the owner's display. It draws into its own nested X server (Xephyr),
whose window on the owner's screen is the live view. Dream starts that server outside the
sandbox, in its own bubblewrap (nested_display_argv), when the model first calls a Blender
tool, and stops it with Blender. The two sandboxes share only a private folder: the nested
server's socket and the session's cookie.

- ``managed_servers(workspace)`` is the session's server entry, named "blender" (a name
  dream.mcp_client reserves for it), or the reason there is none. The Engine adds it before
  the extension toggles apply, so ``mcp:blender`` can switch it off.
- ``python -I -m dream.media.blender_live --workspace W --display :0`` is that entry's
  command: the launcher. It stays outside the sandbox for the whole session, runs the
  sandbox as its child, and answers the bridge's three requests on a private control
  socket: open (start the nested display), close (stop it) and window (its window id on
  the owner's display). When the sandbox ends, it stops the nested display and removes the
  private folder. There is no host fallback: without the sandbox nothing runs.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import secrets
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from ..core.execution import (_SUPERVISOR_PYTHON, ExecutionRefused, ExecutionScope, _bubblewrap_executable,
                              _mount_descriptors, _socket_filter, _trusted_system_file, blender_live_argv,
                              nested_display_argv)

SERVER_NAME = "blender"
SOURCE = "(DREAM_BLENDER or the blender.binary runtime setting)"


def _configured_blender() -> Path:
    """DREAM-124: the Blender binary Dream is told to use -- DREAM_BLENDER, else the runtime setting
    ``blender.binary`` (data/runtime-settings.json), else the system package. An official blender.org build
    unpacked by root (/opt/blender-4.5.14-linux-x64/blender) has OpenImageDenoise. The path is resolved and
    checked once (``_checked_blender``) and the resolved path is what is mounted and run. The sandbox still has
    no /dev/dri: Cycles and the denoiser run on the CPU."""
    if os.environ.get("DREAM_BLENDER"):
        return Path(os.environ["DREAM_BLENDER"])
    from ..core.profiles import read_settings
    try:
        section = read_settings().get("blender")
    except ValueError:  # an unreadable settings file is reported where the profile loads, as follow_default does
        section = None
    if isinstance(section, dict) and "binary" in section:
        return Path(str(section["binary"]))  # a value that is no absolute path is refused, not dropped
    return Path("/usr/bin/blender")


BLENDER = _configured_blender()
XEPHYR = Path("/usr/bin/Xephyr")
XWININFO = Path("/usr/bin/xwininfo")
RUNTIME = Path(__file__).resolve().parent / "blender_mcp"
SCREEN = (1600, 1000)  # the nested display, and Blender's window filling it
DISPLAY_BASE = 90  # nested display numbers start here, clear of :0, :1 and gdm's :1024/:1025
_SYSTEM = (Path("/usr"), Path("/bin"), Path("/sbin"), Path("/lib"), Path("/lib64"))


def _unix_names(net_unix: Path = Path("/proc/net/unix")) -> set[str]:
    """The addresses of every Unix socket in this network namespace ("@..." = abstract)."""
    try:
        lines = net_unix.read_text().splitlines()[1:]
    except OSError:
        return set()
    return {parts[-1] for parts in (line.split() for line in lines) if len(parts) >= 8}


def x11_display(value: str | None = None, *, net_unix: Path = Path("/proc/net/unix")) -> tuple[str, int] | None:
    """The local X11 display (":0" or "unix:0.0") and its number, or None.

    The nested server reaches it only through its abstract socket (its /tmp/.X11-unix is
    the private folder), so the abstract socket must exist; the path socket does not matter.
    """
    value = os.environ.get("DISPLAY", "") if value is None else value
    match = re.fullmatch(r"(?:unix)?:([0-9]{1,4})(\.[0-9]{1,2})?", value)
    if not match or f"@/tmp/.X11-unix/X{match.group(1)}" not in _unix_names(net_unix):
        return None
    return f":{match.group(1)}{match.group(2) or ''}", int(match.group(1))


def free_display(start: int = DISPLAY_BASE, *, x11_dir: Path = Path("/tmp/.X11-unix"), tmp: Path = Path("/tmp"),
                 net_unix: Path = Path("/proc/net/unix")) -> int:
    """The first display number from ``start`` with no socket file, no lock file and no socket name
    (abstract, or another nested server's path) anywhere on this host. Never auto-picked by X."""
    names = _unix_names(net_unix)
    for number in range(start, start + 500):
        if ((x11_dir / f"X{number}").exists() or (x11_dir / f"X{number}").is_symlink()
                or (tmp / f".X{number}-lock").exists() or (tmp / f".X{number}-lock").is_symlink()
                or f"@/tmp/.X11-unix/X{number}" in names or f"/tmp/.X11-unix/X{number}" in names):
            continue
        return number
    raise ExecutionRefused(f"no free display number from :{start}")


def write_xauth(path: Path) -> bytes:
    """The session's cookie: one MIT-MAGIC-COOKIE-1 entry for any address and display, mode 0600."""
    cookie = secrets.token_bytes(16)

    def field(value: bytes) -> bytes:
        return struct.pack(">H", len(value)) + value

    entry = struct.pack(">H", 0xFFFF) + field(b"") + field(b"") + field(b"MIT-MAGIC-COOKIE-1") + field(cookie)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as file:
        file.write(entry)
    return cookie


def window_in_tree(tree: str, title: str) -> str:
    """The id of the window titled exactly ``title`` in ``xwininfo -root -tree`` output."""
    found = [m.group(1) for m in re.finditer(r'^\s*(0x[0-9a-fA-F]+) "(.*)":', tree, re.M) if m.group(2) == title]
    if not found:
        raise RuntimeError("the live Blender window was not found on the display")
    return found[0]


def _site_packages() -> Path | None:
    """The folder holding the mcp package the bridge imports (Dream's own dependency)."""
    spec = importlib.util.find_spec("mcp")
    if spec is None or not spec.origin:
        return None
    return Path(spec.origin).resolve().parent.parent


def _blender_folder(blender: Path) -> Path | None:
    """The folder of a Blender build outside the system trees, which the sandbox must mount, or None."""
    folder = blender.resolve().parent
    return None if any(folder == s or folder.is_relative_to(s) for s in _SYSTEM) else folder


def _untrusted_in(folder: Path) -> Path | None:
    """The first entry of ``folder`` (itself included) not owned by root or writable by group/others."""
    for path in (folder, *folder.rglob("*")):
        info = path.lstat()
        if info.st_uid != 0 or (not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o022):
            return path
    return None


def _checked_blender() -> tuple[Path | None, str | None]:
    """BLENDER resolved ONCE, then checked: (the resolved path to mount and run, None) or (None, why not).

    Every component of a path that passes is root-owned and not writable by others, so it cannot be swapped
    after the check; the unresolved name (a symlink in the workspace, say) never travels further."""
    if not BLENDER.is_absolute():
        return None, f"the Blender path {BLENDER} is not absolute; {SOURCE[1:-1]} must name one"
    try:
        real = BLENDER.resolve(strict=True)
    except (OSError, RuntimeError):
        return None, f"no Blender at {BLENDER} (root-owned, as the system package installs it)"
    if not _trusted_system_file(real):
        return None, (f"the Blender at {BLENDER} is not root-owned or can be changed by a user; "
                      f"live Blender runs only a binary installed by root {SOURCE}")
    folder = _blender_folder(real)
    if folder is not None and len(folder.parts) < 3:
        return None, (f"the Blender at {BLENDER} must sit in its own folder, "
                      f"such as /opt/blender-4.5.14-linux-x64/blender {SOURCE}")
    if folder is not None and (bad := _untrusted_in(folder)) is not None:
        return None, (f"the Blender folder {folder} holds {bad}, which is not root-owned "
                      f"or can be changed by a user; install the build as root {SOURCE}")
    return real, None


def _scope(workspace: Path) -> ExecutionScope:
    site = _site_packages()
    if site is None:
        raise ExecutionRefused("the mcp package Dream uses was not found")
    # Read-only: the bridge and add-on copy, and the Python packages the bridge imports.
    # A folder under /usr is already mounted; system trees cannot be read roots.
    roots = tuple(r for r in (RUNTIME, site) if not any(r == s or r.is_relative_to(s) for s in _SYSTEM))
    if (folder := _blender_folder(BLENDER)) is not None:
        roots += (folder,)  # a build under /opt: its binary, libraries and scripts, read-only
    scope = ExecutionScope(workspace, read_roots=roots)
    scope.validate()
    return scope


def unavailable(workspace: str | Path, display: str | None = None) -> str | None:
    """Why live Blender cannot run for this workspace, or None when it can."""
    if sys.platform != "linux":
        return "it needs Linux"
    if (problem := _checked_blender()[1]) is not None:
        return problem
    if not _trusted_system_file(XEPHYR):
        return f"no Xephyr at {XEPHYR} (the xserver-xephyr package), which live Blender draws into"
    if _bubblewrap_executable() is None:
        return "the bubblewrap sandbox is not installed"
    if x11_display(display) is None:
        return "no local X11 display with an abstract socket (a Wayland-only or remote session cannot show it)"
    if not (RUNTIME / "live.py").is_file():
        return f"its bridge is missing from {RUNTIME}"
    try:
        _scope(Path(workspace))
    except (ExecutionRefused, ValueError, OSError) as exc:
        return f"workspace refused: {exc}"
    return None


def managed_servers(workspace: str | Path) -> tuple[list[dict], list[str]]:
    """The session's live-Blender MCP server entry, or no entry and the reason."""
    reason = unavailable(workspace)
    blender, problem = _checked_blender()  # the resolved path the launcher receives, checked again
    if reason or problem:
        return [], [f"live Blender is off: {reason or problem}"]
    display, _ = x11_display()
    return [{
        "name": SERVER_NAME,
        "command": sys.executable,
        "args": ["-I", "-m", "dream.media.blender_live",
                 "--workspace", str(Path(workspace).resolve()), "--display", display,
                 "--blender", str(blender)],
        "_dream_source": "Dream live Blender (DREAM-109)",
    }], []


def sandbox_command(control_fd: int, background: bool = False) -> list[str]:
    """What runs inside the sandbox: the bridge, with Blender as its child."""
    site = _site_packages()
    if site is None:
        raise ExecutionRefused("the mcp package Dream uses was not found")
    return [_SUPERVISOR_PYTHON, "-I", "-S", "-B", str(RUNTIME / "live.py"),
            "--site-packages", str(site), "--blender", str(BLENDER), "--control-fd", str(control_fd),
            "--screen", f"{SCREEN[0]}x{SCREEN[1]}", *(["--background"] if background else [])]


class NestedDisplay:
    """The session's nested X server: started on request, one at a time, never on the host's sockets."""

    def __init__(self, bwrap: str, private: Path, host_display: str, title: str) -> None:
        self.bwrap, self.host_display, self.title = bwrap, host_display, title
        self.x11_dir, self.xauth, self.log = private / "x11", private / "xauth", private / "nested.log"
        self.process: subprocess.Popen | None = None
        self.number: int | None = None
        self._lock = threading.Lock()

    def start(self) -> str:
        with self._lock:
            if self.process is not None and self.process.poll() is None:
                return f":{self.number}"
            self._stop()
            number = free_display()
            argv = nested_display_argv(self.bwrap, str(XEPHYR), x11_dir=self.x11_dir, xauth=self.xauth,
                                       host_display=self.host_display, number=number, screen=SCREEN,
                                       title=self.title)
            with open(self.log, "ab") as log:
                self.process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log,
                                                stderr=subprocess.STDOUT, start_new_session=True)
            self.number = number
            socket_path = self.x11_dir / f"X{number}"
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and self.process.poll() is None:
                if socket_path.is_socket():
                    return f":{number}"
                time.sleep(0.02)
            tail = self.log.read_bytes()[-1500:].decode("utf-8", "replace") if self.log.exists() else ""
            self._stop()
            raise RuntimeError(f"the nested display did not start. Its log ends:\n{tail}")

    def stop(self) -> None:
        with self._lock:
            self._stop()

    def _stop(self) -> None:
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            process.terminate()  # bubblewrap; --die-with-parent takes the server with it
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if self.number is not None:
            (self.x11_dir / f"X{self.number}").unlink(missing_ok=True)
        self.number = None

    def window(self) -> str:
        """The nested server's window on the owner's display, for computer use."""
        with self._lock:
            if self.process is None or self.process.poll() is not None:
                raise RuntimeError("the nested display is not running")
        tree = subprocess.run([str(XWININFO), "-root", "-tree", "-display", self.host_display],
                              capture_output=True, text=True, timeout=10,
                              env={"PATH": "/usr/bin:/bin"}).stdout
        return window_in_tree(tree, self.title)


def _serve(control: socket.socket, nested: NestedDisplay) -> None:
    """Answer the bridge until the sandbox closes its end. Three requests, nothing else."""
    with control, control.makefile("rwb", buffering=0) as stream:
        for line in stream:
            request = line.strip().decode("ascii", "replace")
            try:
                if request == "open":
                    reply = "ok " + nested.start()
                elif request == "close":
                    nested.stop()
                    reply = "ok"
                elif request == "window":
                    reply = "ok " + nested.window()
                else:
                    reply = "error unknown request"
            except Exception as exc:  # noqa: BLE001 -- the bridge reports it to the model
                reply = "error " + " ".join(str(exc).split())
            stream.write(reply.encode("utf-8", "replace") + b"\n")


def run(workspace: Path, display: str, *, background: bool = False) -> int:
    """The launcher: the sandbox as a child, the nested display on request, cleanup at the end."""
    reason = unavailable(workspace, display)
    if reason:
        raise ExecutionRefused(reason)
    if _checked_blender()[0] != BLENDER:  # Dream passes the resolved path; the launcher runs no other
        raise ExecutionRefused(f"the launcher runs only a resolved Blender path, not {BLENDER}")
    display, _ = x11_display(display)
    scope = _scope(workspace)
    bwrap = _bubblewrap_executable()
    private = Path(tempfile.mkdtemp(prefix="dream-blender-"))  # 0700: the socket folder and the cookie
    try:
        (private / "x11").mkdir(mode=0o700)
        write_xauth(private / "xauth")
        name = re.sub(r"[^\w .-]", "", scope.workspace.name)[:60].strip() or "workspace"
        nested = NestedDisplay(bwrap, private, display, f"Dream live Blender: {name} [{secrets.token_hex(2)}]")
        ours, theirs = socket.socketpair()
        with ours:
            try:
                with _socket_filter(inet=True, unix=True) as seccomp, _mount_descriptors(scope) as mounts:
                    argv = blender_live_argv(scope, bwrap, sandbox_command(theirs.fileno(), background),
                                             seccomp_fd=seccomp, mount_fds=mounts, x11_dir=private / "x11",
                                             xauth=private / "xauth")
                    sandbox = subprocess.Popen(argv, pass_fds=(seccomp, *mounts.values(), theirs.fileno()))
            finally:
                theirs.close()
            threading.Thread(target=_serve, args=(ours, nested), daemon=True).start()
            try:
                return sandbox.wait()
            finally:
                if sandbox.poll() is None:
                    sandbox.terminate()
                    try:
                        sandbox.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        sandbox.kill()
                nested.stop()
    finally:
        shutil.rmtree(private, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    global BLENDER
    parser = argparse.ArgumentParser(prog="python -I -m dream.media.blender_live",
                                     description="Run Dream's live Blender MCP server in its sandbox.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--display", required=True, help="the owner's display, where the nested window appears")
    parser.add_argument("--background", action="store_true", help="Blender without a window (tests)")
    parser.add_argument("--blender", default=str(BLENDER), help="the resolved Blender binary, checked again here")
    args = parser.parse_args(argv)
    BLENDER = Path(args.blender)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # clean up the display and folder on stop
    try:
        return run(Path(args.workspace), args.display, background=args.background)
    except (ExecutionRefused, OSError, ValueError) as exc:
        print(f"live Blender did not start: {exc}. Nothing ran outside the sandbox.", file=sys.stderr, flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
