"""Where the browser runs (DREAM-187): on Linux, inside bubblewrap with a network namespace of its own.

The browser's network rule is enforced by the per-fetch boundary web/browser.py gives each context as an
HTTP proxy; Firefox is only configured to use it. This module is the structural backstop under that: Camoufox
runs in a bubblewrap sandbox whose network namespace holds a loopback interface and nothing else, so a
Firefox feature that ignores the context's proxy (WebRTC, DNS prefetch, QUIC, speculative connects, captive
portal and connectivity checks, add-on and list updates) reaches nothing, and nothing on this machine or its
network reaches the browser. The one way out is a listener Dream binds *inside* that namespace for each
fetch: the sandbox's init (INIT_SOURCE), asked over a control channel, binds 127.0.0.1:0 there and passes
the socket out; browser.py serves its boundary on it exactly as it would on a host listener, so the
context's proxy names a port that exists only inside the sandbox and whose accept loop runs here.

The profile is run_bash's (core/execution.py), shaped for Firefox: every namespace unshared, nested user
namespaces disabled, all capabilities dropped, the seccomp socket filter (socket() for AF_INET/AF_INET6
only -- no AF_UNIX or netlink socket can be created that way; socketpair() stays, Firefox's processes talk
over it), a cleared environment with only the launcher's own variables; read-only
the system trees and /etc files run_bash mounts, the Camoufox root (Camoufox's own INSTALL_DIR: the
install, the add-ons, the fontconfig file) and, when outside those trees, the prefix of the interpreter
Dream runs on -- the init runs on that interpreter, the only one sure to exist wherever Dream does, and
needs nothing beyond its standard library; a private /tmp holding HOME and the profile directory
Playwright made (the one writable mount); no host HOME, /run, /sys or display, so the sandboxed browser is
headless only.

Playwright launches an executable: it gets the wrapper this module writes into a private folder (_WRAPPER)
instead of camoufox-bin. The wrapper connects the control channel, opens the seccomp program and execs
bubblewrap with Firefox's own arguments, environment and descriptors (the Juggler pipe on 3 and 4 crosses
untouched). Without bubblewrap, or when the profile fails its probe, the browser does not run: Unavailable,
which browser.py reports as the fetch's error -- there is no unsandboxed fallback. Another platform keeps
the proxy-only boundary (supported() is False), which the tool's text says.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import stat
import struct
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..core import execution


class Unavailable(RuntimeError):
    """The sandbox cannot run here, so neither does the browser."""


def supported() -> bool:
    return sys.platform == "linux"


@dataclass(frozen=True)
class Capability:
    available: bool
    reason: str


_ATTACH_S = 5.0  # the wrapper connects before Firefox starts: pending by the time the launch returns
_PROBE_S = 5.0
_REAP_S = 5.0  # a killed probe process is reaped within this, through any number of cancellations
_ASK_S = 5.0  # one deadline per request for a listener: the send and every reply, discarded ones included
_LAST_SERIAL = 0xFFFFFFFF  # the tags of one channel never wrap: at the last one the channel is retired
_KEEP = ("FONTCONFIG_FILE",)  # of the launcher's environment, what gets in: this and the prefixes below
_KEEP_PREFIXES = ("CAMOU_CONFIG_", "MOZ_")
_SETENV = {"HOME": "/tmp/home", "TMPDIR": "/tmp", "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
_FLAGS = ["--unshare-user", "--unshare-pid", "--unshare-net", "--unshare-ipc", "--unshare-uts",
          "--disable-userns", "--die-with-parent", "--new-session", "--cap-drop", "ALL"]
_SYSTEM = ("/usr", "/bin", "/sbin", "/lib", "/lib64")

# PID 2 of the sandbox (bubblewrap's own init is 1): runs the browser with the descriptors the wrapper got
# from Playwright (0-2, the Juggler pipe on 3 and 4) and serves the control channel, one message per
# request (SOCK_SEQPACKET) -- b"L" + a tag: a fresh loopback listener of this network namespace, passed out
# with the request echoed as the reply, our copy dropped. Control EOF (Dream let go), or a reply that cannot
# be sent (Dream gone mid-request), ends the browser: whatever ends the serving, the browser is killed in
# the outer finally and the listener copy closed in the inner one; the exit status is the browser's, so
# Playwright sees the browser exit as usual.
INIT_SOURCE = r'''
import os, socket, subprocess, sys, threading
control = socket.socket(fileno=int(sys.argv[1]))
control.set_inheritable(False)
browser = subprocess.Popen(sys.argv[3:], pass_fds=(3, 4))

def serve():
    try:
        while True:
            try:
                request = control.recv(64)
            except OSError:
                request = b""
            if not request:
                break
            if request[:1] == b"L":
                listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    listener.bind(("127.0.0.1", 0))
                    listener.listen(64)
                    socket.send_fds(control, [request], [listener.fileno()])
                finally:
                    listener.close()
    finally:
        try:
            browser.kill()
        except OSError:
            pass

threading.Thread(target=serve, daemon=True).start()
code = browser.wait()
os._exit(code if code >= 0 else 128 - code)
'''

# The executable Playwright launches, on the interpreter Dream runs on. It assembles the same argv as
# _argv/_command from the plan baked into it (test_browse_netns holds the two to each other).
_WRAPPER = r'''#!%(python)s -I
import json, os, socket, sys
plan = json.loads(%(plan)s)
args = sys.argv[1:]
profile = args[args.index("-profile") + 1]
control = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
control.connect(plan["control"])
os.set_inheritable(control.fileno(), True)
seccomp = os.open(plan["seccomp"], os.O_RDONLY)
os.set_inheritable(seccomp, True)
env = {k: v for k, v in os.environ.items() if k in plan["keep"] or k.startswith(tuple(plan["keep_prefixes"]))}
env.update(plan["setenv"])
argv = [plan["bwrap"], *plan["flags"], "--seccomp", str(seccomp), "--clearenv"]
for path in plan["ro_binds"]:
    argv += ["--ro-bind", path, path]
argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/tmp/home",
         "--bind", profile, profile, "--remount-ro", "/"]
for key in sorted(env):
    argv += ["--setenv", key, env[key]]
argv += ["--chdir", "/tmp/home", "--", plan["python"], "-I", "-c", plan["init"], str(control.fileno()), "--",
         plan["camoufox"], *args]
os.execv(argv[0], argv)
'''


def _bubblewrap() -> str:
    path = execution._bubblewrap_executable()
    if not path:
        raise Unavailable("trusted system bubblewrap (/usr/bin/bwrap) is not installed")
    return path


def _tmp() -> Path:
    return Path(tempfile.gettempdir())


def _seccomp() -> bytes:
    with execution._socket_filter(inet=True) as fd:
        os.lseek(fd, 0, os.SEEK_SET)
        return os.read(fd, 1 << 20)


def _install() -> tuple[Path, Path, Path]:
    """(the Camoufox root, the executable, the active install), by Camoufox's own functions: INSTALL_DIR
    respects XDG_CACHE_HOME; get_active_path() and launch_path() read what is there and never download,
    clean or update -- camoufox_path() would, and is never called."""
    try:
        from camoufox import pkgman
        from camoufox.multiversion import get_active_path
    except Exception as exc:
        raise Unavailable(f"Camoufox is not importable: {exc}") from exc
    active = get_active_path()
    if active is None:
        raise Unavailable(f"Camoufox is not installed under {pkgman.INSTALL_DIR}")
    try:
        executable = Path(pkgman.launch_path(active))
    except Exception as exc:
        raise Unavailable(str(exc)) from exc
    return Path(pkgman.INSTALL_DIR), executable, Path(active)


def _under(path: str, roots) -> bool:
    return any(path == root or path.startswith(root.rstrip("/") + "/") for root in roots)


def _interpreter() -> tuple[str, list[str]]:
    """The interpreter the init runs on -- the one Dream runs on, resolved once at import by execution --
    and what to mount read-only for it: its base prefix and its own directory, unless under a system tree."""
    python = execution._SUPERVISOR_PYTHON
    binds: list[str] = []
    for path in (str(Path(sys.base_prefix).resolve()), str(Path(python).resolve().parent)):
        if not _under(path, _SYSTEM) and not _under(path, binds):
            binds.append(path)
    return python, binds


def _plan(*, control: Path, seccomp: Path) -> dict:
    """Everything the wrapper needs to exec bubblewrap, in one JSON-able dict."""
    cache, executable, active = _install()
    python, python_binds = _interpreter()
    system = execution._system_binds()  # ["--ro-bind", path, path, ...]
    return {"bwrap": _bubblewrap(), "flags": list(_FLAGS),
            "ro_binds": [*system[1::3], *python_binds, str(cache)],
            "setenv": dict(_SETENV), "keep": list(_KEEP), "keep_prefixes": list(_KEEP_PREFIXES),
            "python": python, "camoufox": str(executable), "cache": str(cache),
            "properties": str(active / "properties.json"),
            "control": str(control), "seccomp": str(seccomp), "init": INIT_SOURCE}


def _argv(plan: dict, *, seccomp_fd: int, profile: str, env: dict[str, str], command: list[str]) -> list[str]:
    """bubblewrap's argv for the plan: what the wrapper assembles, for the probe and the tests."""
    argv = [plan["bwrap"], *plan["flags"], "--seccomp", str(seccomp_fd), "--clearenv"]
    for path in plan["ro_binds"]:
        argv += ["--ro-bind", path, path]
    argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/tmp/home",
             "--bind", profile, profile, "--remount-ro", "/"]
    for key, value in sorted({**env, **plan["setenv"]}.items()):
        argv += ["--setenv", key, value]
    return argv + ["--chdir", "/tmp/home", "--", *command]


def _command(plan: dict, control_fd: int, args: list[str]) -> list[str]:
    """The sandbox's command: the init on Dream's interpreter, then the browser with Firefox's arguments."""
    return [plan["python"], "-I", "-c", plan["init"], str(control_fd), "--", plan["camoufox"], *args]


async def _readable(sock: socket.socket) -> None:
    loop = asyncio.get_running_loop()
    ready = loop.create_future()
    loop.add_reader(sock.fileno(), lambda: ready.done() or ready.set_result(None))
    try:
        await ready
    finally:
        loop.remove_reader(sock.fileno())


async def _reaped(proc) -> None:
    """proc.wait() for a process already killed, through any number of cancellations landing here: the
    wait goes on until the process is reaped (or _REAP_S has passed), and a cancellation taken meanwhile
    is raised once it is."""
    loop = asyncio.get_running_loop()
    waiting = asyncio.ensure_future(proc.wait())
    deadline = loop.time() + _REAP_S
    cancelled: BaseException | None = None
    while not waiting.done():
        remaining = deadline - loop.time()
        if remaining <= 0:
            waiting.cancel()  # given up on: the loop's child watcher still reaps it
            break
        try:
            await asyncio.wait_for(asyncio.shield(waiting), remaining)
        except asyncio.CancelledError as exc:
            cancelled = exc
        except asyncio.TimeoutError:
            pass
    if cancelled is not None:
        raise cancelled


class Sandbox:
    """One browser's sandbox: the private folder (0700) with the wrapper, the seccomp program, the control
    socket and a link to the install's properties.json (the launcher validates its config beside the
    executable it is given); the control channel once attached."""

    def __init__(self) -> None:
        if not supported():
            raise Unavailable("the browser sandbox runs on Linux only")
        self._control: socket.socket | None = None
        self._listener: socket.socket | None = None
        self._lock = asyncio.Lock()
        self._serial = 0  # the last request's tag
        self.dir = Path(tempfile.mkdtemp(prefix="dream-browser-", dir=_tmp()))
        try:
            control, seccomp = self.dir / "control", self.dir / "seccomp.bpf"
            if len(str(control).encode()) > 107:
                raise Unavailable(f"the temporary directory's path is too long for a socket: {control}")
            try:
                seccomp.write_bytes(_seccomp())
                self.plan = _plan(control=control, seccomp=seccomp)
            except execution.ExecutionRefused as exc:
                raise Unavailable(str(exc)) from exc
            os.symlink(self.plan["properties"], self.dir / "properties.json")
            self.wrapper = self.dir / "launch"
            self.wrapper.write_text(_WRAPPER % {"python": self.plan["python"], "plan": repr(json.dumps(self.plan))})
            self.wrapper.chmod(stat.S_IRWXU)
            self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)  # one message per request and reply
            self._listener.bind(str(control))
            self._listener.listen(1)
            self._listener.setblocking(False)
        except BaseException:
            if self._listener is not None:
                self._listener.close()
            shutil.rmtree(self.dir, ignore_errors=True)
            raise

    @classmethod
    async def create(cls, *, headless: bool = True) -> Sandbox:
        """A probed sandbox to launch through; Unavailable, nothing left behind, when it cannot run here --
        or when a visible browser is asked for: the sandbox has no display, and the browser never runs
        outside it on Linux."""
        if not headless:
            raise Unavailable("the sandboxed browser runs headless only: DREAM_BROWSER_HEADLESS=0 is not "
                              "supported on Linux, where the browser runs inside bubblewrap without a display; "
                              "nothing was fetched")
        sandbox = cls()
        try:
            capability = await sandbox.probe()
        except BaseException:  # the probe failing, or a cancellation landing in it: nothing is kept
            await sandbox.close()
            raise
        if not capability.available:
            await sandbox.close()
            raise Unavailable(f"the browser sandbox is unavailable: {capability.reason}. On Linux Dream's browser "
                              f"runs only inside bubblewrap (the bubblewrap package, /usr/bin/bwrap); nothing was fetched")
        return sandbox

    def launch_kwargs(self) -> dict:
        """What the Camoufox launcher takes to go through the wrapper: it as the executable, and an empty
        environment to build on -- the launcher adds its own variables, and nothing else of this process
        reaches the browser."""
        return {"executable_path": str(self.wrapper), "env": {}}

    async def probe(self) -> Capability:
        """The profile run once around /bin/true, as run_bash probes its sandbox, before the browser.
        Everything the probe takes -- its folder, the program's descriptor, its process -- is given back on
        every way out: an error is a capability with the reason, and a cancellation landing anywhere in it
        (the spawn included) still ends and reaps the process before going on -- through a second
        cancellation landing in the reap too; the descriptor and the folder are released with no await
        in the way."""
        profile = fd = proc = None
        try:
            profile = Path(tempfile.mkdtemp(prefix="probe-", dir=self.dir))
            fd = os.open(self.dir / "seccomp.bpf", os.O_RDONLY)
            argv = _argv(self.plan, seccomp_fd=fd, profile=str(profile), env={}, command=["/bin/true"])
            spawning = asyncio.ensure_future(asyncio.create_subprocess_exec(
                *argv, pass_fds=(fd,), stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT))
            try:
                proc = await asyncio.shield(spawning)
            except asyncio.CancelledError:
                proc = await spawning  # cancelled mid-spawn: the process is ours all the same, ended below
                raise
            try:
                output, _ = await asyncio.wait_for(proc.communicate(), _PROBE_S)
            except asyncio.TimeoutError:
                return Capability(False, "the sandbox probe did not finish in time")
        except OSError as exc:
            return Capability(False, str(exc))
        finally:
            try:
                if proc is not None and proc.returncode is None:
                    proc.kill()
                    await _reaped(proc)
            finally:
                if fd is not None:
                    os.close(fd)
                if profile is not None:
                    shutil.rmtree(profile, ignore_errors=True)
        if proc.returncode == 0:
            return Capability(True, "bubblewrap network, filesystem, PID and socket isolation verified")
        return Capability(False, output.decode("utf-8", "replace").strip() or f"the sandbox probe failed (exit {proc.returncode})")

    async def attach(self) -> None:
        """Take the control connection the wrapper made before Firefox started. Nothing connecting within
        the wait means the browser did not go through the wrapper: fail closed."""
        loop = asyncio.get_running_loop()
        try:
            control, _ = await asyncio.wait_for(loop.sock_accept(self._listener), _ATTACH_S)
        except asyncio.TimeoutError:
            raise Unavailable("the browser did not connect the sandbox's control channel") from None
        _, uid, _ = struct.unpack("3i", control.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != os.getuid():
            control.close()
            raise Unavailable(f"the sandbox's control channel was connected by uid {uid}, not {os.getuid()}")
        control.setblocking(False)
        self._control = control
        self._listener.close()  # one connection, ever

    async def listener(self) -> socket.socket:
        """A TCP listener bound on 127.0.0.1:0 inside the sandbox's network namespace, the caller's own.
        Each request carries a tag of its own that the init echoes with the reply: a reply to a request
        interrupted earlier -- one that timed out, or whose fetch was cancelled while it waited -- comes
        with another tag and is closed unused, never taken for this one's. One deadline, taken before the
        send, covers the send and every reply, discarded ones included. The init gone (control EOF) is the
        browser gone: a closed connection, which the fetch's self-heal takes as a dead browser; so is the
        channel retired at its last tag (they never wrap), the browser relaunched through the sandbox -- for
        the caller that retires it and for any queued behind the lock at that moment alike."""
        if self._control is None:
            raise Unavailable("the sandbox's control channel is not attached")
        loop = asyncio.get_running_loop()
        async with self._lock:
            if self._control is None:  # retired, or closed, by a caller ahead in this queue: gone for this one too
                raise ConnectionResetError("the sandbox's control channel is closed: the browser is relaunched")
            if self._serial >= _LAST_SERIAL:
                control, self._control = self._control, None
                control.close()  # the init ends the browser
                raise ConnectionResetError(f"the sandbox's control channel closed after {self._serial} requests: "
                                           "the browser is relaunched")
            self._serial += 1
            tag = b"L" + self._serial.to_bytes(4, "big")
            deadline = loop.time() + _ASK_S
            try:
                await asyncio.wait_for(loop.sock_sendall(self._control, tag), _ASK_S)
            except asyncio.TimeoutError:
                raise Unavailable(f"the sandbox did not take a request for a listener within {_ASK_S:g}s") from None
            except OSError as exc:  # the peer gone before the request could be sent
                raise ConnectionResetError(f"the sandbox's control channel closed: the browser is gone ({exc})") from None
            while True:
                try:
                    await asyncio.wait_for(_readable(self._control), max(0.0, deadline - loop.time()))
                    message, fds, _, _ = socket.recv_fds(self._control, 64, 1)
                except asyncio.TimeoutError:
                    raise Unavailable(f"the sandbox did not answer a request for a listener within {_ASK_S:g}s") from None
                except BlockingIOError:
                    continue
                if message == tag and fds:
                    return socket.socket(fileno=fds[0])
                for fd in fds:
                    os.close(fd)  # a late reply to an interrupted request: its listener is not this one's
                if not message:
                    raise ConnectionResetError("the sandbox's control channel closed: the browser is gone")

    async def close(self) -> None:
        """Let go: the control channel closed (the init ends the browser if it still runs), the folder gone."""
        for sock in (self._control, getattr(self, "_listener", None)):
            if sock is not None:
                sock.close()
        self._control = None
        shutil.rmtree(self.dir, ignore_errors=True)


async def probe() -> Capability:
    """Whether the sandbox works here, as run_bash's probe_sandbox: bubblewrap trusted, libseccomp present,
    Camoufox installed, the profile run once."""
    if not supported():
        return Capability(False, "the browser sandbox runs on Linux only")
    try:
        sandbox = Sandbox()
    except Unavailable as exc:
        return Capability(False, str(exc))
    try:
        return await sandbox.probe()
    finally:
        await sandbox.close()
