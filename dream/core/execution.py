"""Enforced local execution and ownership of subprocesses.

Policy advice is not a sandbox. Native run_bash uses this module to enforce the
boundary; provider-owned Bash/CLI tools must opt into the same executor or report
their own boundary. Context and approvals are supplied by the harness, never by
model tool arguments. No environment variable can enable red-team or host access.

Bubblewrap options follow https://github.com/containers/bubblewrap/blob/v0.9.0/bwrap.xml.
Only explicit filesystem mounts are exposed. The host network, host processes,
devices, agent sockets, and the host home directory are absent -- except the installed
skills' script folders and node's runtime, which run_bash mounts read-only (dream.core.skill_runtime). A scope with
network=True reaches the public internet only, through sandbox_net's proxy.
Each invocation has a private /tmp (including HOME=/tmp/home). Those files vanish
when its process tree exits; use a workspace path for persistent output.
"""

from __future__ import annotations

import asyncio
import ctypes
import errno
import math
import os
import re
import signal
import socket
import stat
import sys
import tempfile
import time
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterator, Mapping, Sequence


# Resolve once while loading trusted harness code. A writable venv launcher must
# not replace the unsandboxed supervisor interpreter between tool calls.
_SUPERVISOR_PYTHON = str(Path(getattr(sys, "_base_executable", sys.executable)).resolve())


def _trusted_system_file(path: Path) -> bool:
    try:
        path = path.resolve(strict=True)
        return path.is_file() and all(
            p.stat().st_uid == 0 and not p.stat().st_mode & 0o022 for p in (path, *path.parents))
    except OSError:
        return False


def _trusted_runtime_directory(path: Path) -> bool:
    try:
        return path.resolve(strict=True) == path and path.is_dir() and all(
            p.stat().st_uid == 0 and not p.stat().st_mode & 0o022 for p in (path, *path.parents))
    except OSError:
        return False


def _bubblewrap_executable() -> str | None:
    # PATH commonly starts with a writable workspace/venv. A replacement called
    # bwrap could make a no-op probe pass and then execute the command on the host.
    for candidate in (Path("/usr/bin/bwrap"), Path("/bin/bwrap"), Path("/usr/local/bin/bwrap")):
        if _trusted_system_file(candidate):
            return str(candidate.resolve())
    return None


class ExecutionRefused(RuntimeError):
    pass


class ExecutionUnavailable(ExecutionRefused):
    """An execution prerequisite failed before the command or worker could run."""


ENV_BASE = frozenset({"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL",
                      "LC_CTYPE", "TERM", "TMPDIR", "PWD", "SYSTEMROOT", "WINDIR"})


def minimal_environment(overrides: Mapping[str, str] | None = None, *,
                        inherit: Sequence[str] = ()) -> dict[str, str]:
    """A fixed base plus explicitly configured values/names; no secret heuristics."""
    env = {k: v for k in ENV_BASE.union(inherit)
           if (v := os.environ.get(k)) is not None and not v.startswith("()")}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    for key, value in (overrides or {}).items():
        if not isinstance(key, str) or not key or "=" in key or "\0" in key:
            raise ValueError("invalid environment variable name")
        if not isinstance(value, str) or "\0" in value:
            raise ValueError(f"invalid environment value for {key}")
        env[key] = value
    return env


@dataclass(frozen=True)
class ExecutionScope:
    workspace: Path
    read_roots: tuple[Path, ...] = ()
    red_team: bool = False
    target_roots: tuple[Path, ...] = ()
    expires_at: float | None = None  # time.monotonic(); mandatory for red-team
    network: bool = False  # run_bash reaches public internet hosts via sandbox_net

    def __post_init__(self) -> None:
        ws = Path(self.workspace).expanduser().resolve()
        object.__setattr__(self, "workspace", ws)
        object.__setattr__(self, "read_roots", tuple(Path(p).expanduser().resolve() for p in self.read_roots))
        object.__setattr__(self, "target_roots", tuple(Path(p).expanduser().resolve() for p in self.target_roots))
        if self.expires_at is not None and not math.isfinite(self.expires_at):
            raise ValueError("execution expiry must be finite monotonic time")
        if self.red_team:
            if not self.target_roots or self.expires_at is None:
                raise ValueError("red-team requires explicit target roots and an expiry")
            if any(p == ws or not p.is_relative_to(ws) for p in self.target_roots):
                raise ValueError("red-team targets must be strict subdirectories of the workspace")
        elif self.target_roots:
            raise ValueError("target roots require explicit red-team scope")

    def validate(self) -> None:
        if self.expires_at is not None and time.monotonic() >= self.expires_at:
            raise ExecutionRefused("execution scope expired")
        if not self.workspace.is_dir():
            raise ExecutionRefused("execution workspace does not exist")
        # Mounting a host root or credential/runtime socket tree defeats isolation. /tmp/.X11-unix
        # also holds the host display's sockets, and the live-Blender sandbox mounts its private
        # display there (DREAM-109).
        forbidden = tuple(Path(p) for p in ("/proc", "/sys", "/dev", "/run",
                                            "/usr", "/bin", "/sbin", "/lib", "/lib64", "/tmp/.X11-unix"))
        roots = (self.workspace, *self.read_roots, *self.target_roots)
        for root in roots:
            if root in {Path("/"), Path("/home"), Path("/tmp"), Path("/var"), Path("/etc")} or root == Path.home().resolve():
                raise ExecutionRefused("a host root or home directory is not an execution scope")
            if root.resolve() != root:
                raise ExecutionRefused("execution root changed through a symlink")
            if any(root == p or root.is_relative_to(p) for p in forbidden):
                raise ExecutionRefused("host process, device, and system trees cannot be execution roots")
        if any(not p.is_dir() for p in (*self.read_roots, *self.target_roots)):
            raise ExecutionRefused("execution roots must name existing directories")

    def allows_deletion(self, paths: Sequence[Path]) -> bool:
        return bool(self.red_team and self.expires_at is not None
                    and time.monotonic() < self.expires_at and paths
                    and all(any(p.resolve().is_relative_to(r) for r in self.target_roots) for p in paths))


@dataclass(frozen=True)
class SandboxCapability:
    """Probe evidence for an executor, not a grant to run arbitrary provider tools."""
    available: bool
    reason: str
    scope: ExecutionScope
    executable: str | None = None

    def enforces(self, scope: ExecutionScope) -> bool:
        return self.available and self.scope == scope and self.executable is not None


@dataclass
class CommandApproval:
    """One exact command, in one scope, consumed once after a human decision.

    allow_uncontained must be a separate explicit decision. It is never inferred
    from a missing sandbox, Auto mode, red-team mode, or ordinary tool approval.
    """
    command: str
    scope: ExecutionScope
    expires_at: float
    allow_uncontained: bool = False
    _used: bool = False

    def consume(self, command: str, scope: ExecutionScope) -> bool:
        if (self._used or self.command != command or self.scope != scope
                or not math.isfinite(self.expires_at) or time.monotonic() >= self.expires_at):
            return False
        self._used = True
        return True


@dataclass(frozen=True)
class ExecutionContext:
    scope: ExecutionScope
    mode: str = "auto"
    approval: CommandApproval | None = None


_CONTEXT: ContextVar[ExecutionContext | None] = ContextVar("dream_execution", default=None)


@contextmanager
def execution_context(scope: ExecutionScope, *, mode: str = "auto",
                      approval: CommandApproval | None = None) -> Iterator[ExecutionContext]:
    context = ExecutionContext(scope, mode, approval)
    token = _CONTEXT.set(context)
    try:
        yield context
    finally:
        _CONTEXT.reset(token)


def current_execution(workspace: Path) -> ExecutionContext:
    context = _CONTEXT.get() or ExecutionContext(ExecutionScope(workspace))
    if context.scope.workspace != Path(workspace).resolve():
        raise ExecutionRefused("execution context belongs to another workspace")
    return context


def _bwrap_argv(scope: ExecutionScope, executable: str, command: Sequence[str], *,
                seccomp_fd: int, mount_fds: Mapping[Path, int], net_fd: int | None = None) -> list[str]:
    scope.validate()
    argv = [executable, "--unshare-user", "--unshare-pid", "--unshare-net", "--unshare-ipc",
            "--unshare-uts", "--disable-userns", "--die-with-parent", "--new-session",
            "--cap-drop", "ALL", "--seccomp", str(seccomp_fd)]
    for raw in ("/usr", "/bin", "/sbin", "/lib", "/lib64"):
        if Path(raw).exists():
            argv += ["--ro-bind", raw, raw]
    for raw in ("/etc/ld.so.cache", "/etc/localtime", "/etc/passwd", "/etc/group"):
        if Path(raw).is_file():
            argv += ["--ro-bind", raw, raw]
    # Debian runtime libraries and executables can link through alternatives.
    # Expose only this trusted link directory; targets resolve inside the sandbox.
    alternatives = Path("/etc/alternatives")
    if alternatives.exists() or alternatives.is_symlink():
        if not _trusted_runtime_directory(alternatives):
            raise ExecutionRefused("untrusted runtime alternatives directory")
        argv += ["--ro-bind", str(alternatives), str(alternatives)]
    if net_fd is not None:
        # TLS clients need the CA store; the private key directory stays out.
        if _trusted_runtime_directory(Path("/etc/ssl/certs")):
            argv += ["--ro-bind", "/etc/ssl/certs", "/etc/ssl/certs"]
        if _trusted_system_file(Path("/etc/ssl/openssl.cnf")):
            argv += ["--ro-bind", "/etc/ssl/openssl.cnf", "/etc/ssl/openssl.cnf"]
        from .sandbox_net import FORWARDER_SOURCE
        command = [_SUPERVISOR_PYTHON, "-I", "-c", FORWARDER_SOURCE, str(net_fd), "--", *command]
    argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/tmp/home"]
    for root in scope.read_roots:
        if scope.workspace.is_relative_to(root):
            argv += ["--ro-bind-fd", str(mount_fds[root]), str(root)]
    argv += ["--ro-bind-fd" if scope.red_team else "--bind-fd",
             str(mount_fds[scope.workspace]), str(scope.workspace)]
    for root in scope.read_roots:
        if not scope.workspace.is_relative_to(root):
            argv += ["--ro-bind-fd", str(mount_fds[root]), str(root)]
    for root in scope.target_roots:
        argv += ["--bind-fd", str(mount_fds[root]), str(root)]
    # Builds may inspect git history, but should not rewrite it through scripts.
    git = scope.workspace / ".git"
    if git in mount_fds:
        argv += ["--ro-bind-fd", str(mount_fds[git]), str(git)]
    argv += ["--remount-ro", "/", "--setenv", "HOME", "/tmp/home", "--setenv", "TMPDIR", "/tmp",
             "--setenv", "PWD", str(scope.workspace), "--chdir", str(scope.workspace), "--", *command]
    return argv


_HOST_X11_DIR = Path("/tmp/.X11-unix")


def _system_binds() -> list[str]:
    """The read-only system trees and /etc files run_bash's profile mounts."""
    argv: list[str] = []
    for raw in ("/usr", "/bin", "/sbin", "/lib", "/lib64"):
        if Path(raw).exists():
            argv += ["--ro-bind", raw, raw]
    for raw in ("/etc/ld.so.cache", "/etc/localtime", "/etc/passwd", "/etc/group"):
        if Path(raw).is_file():
            argv += ["--ro-bind", raw, raw]
    alternatives = Path("/etc/alternatives")
    if alternatives.exists() or alternatives.is_symlink():
        if not _trusted_runtime_directory(alternatives):
            raise ExecutionRefused("untrusted runtime alternatives directory")
        argv += ["--ro-bind", str(alternatives), str(alternatives)]
    return argv


def _private_display_parts(x11_dir: Path, xauth: Path) -> None:
    """The session's own socket folder and cookie file: never the host's /tmp/.X11-unix."""
    x11_dir, xauth = Path(x11_dir), Path(xauth)
    if (not x11_dir.is_absolute() or x11_dir.is_symlink() or not x11_dir.is_dir()
            or x11_dir.resolve() != x11_dir):
        raise ExecutionRefused("the nested display's socket folder must be a real, private directory")
    host = _HOST_X11_DIR.resolve()
    if x11_dir == host or x11_dir.is_relative_to(host) or host.is_relative_to(x11_dir):
        raise ExecutionRefused("the host's /tmp/.X11-unix is never mounted for live Blender")
    if not xauth.is_absolute() or xauth.is_symlink() or not xauth.is_file() or xauth.resolve() != xauth:
        raise ExecutionRefused("the nested display's cookie must be a regular file")


def blender_live_argv(scope: ExecutionScope, executable: str, command: Sequence[str], *,
                      seccomp_fd: int, mount_fds: Mapping[Path, int], x11_dir: Path, xauth: Path) -> list[str]:
    """DREAM-109's live-Blender profile: run_bash's boundary plus Blender's own nested display.

    Pair it with ``_socket_filter(inet=True, unix=True)``. Blender draws into a nested X
    server (Xephyr, ``nested_display_argv``) that Dream starts outside this sandbox; the
    host's display is never mounted. The openings beyond run_bash's profile:
    - the session's private socket folder at /tmp/.X11-unix, read-only: it holds only the
      nested server's socket, never the host's display (not X0, not gdm's);
    - the session's cookie for that server at /tmp/xauth, read-only;
    - AF_UNIX sockets, for X11: they reach only socket files mounted here (the nested one,
      plus any socket file inside the workspace) and abstract sockets of this sandbox's
      own network namespace. The host display's abstract socket is in another namespace;
    - AF_INET/AF_INET6 on the private loopback only (no --share-net): the Blender MCP
      bridge and the add-on talk over 127.0.0.1 inside this sandbox;
    - one inherited descriptor: the control socket the bridge uses to ask the launcher for
      the nested display (open, close, window id) and nothing else.
    Not opened: the host display and its X authority, the network, the home folder, /run
    (D-Bus), /etc beyond run_bash's files, the host's /tmp, SysV IPC and every device
    (the nested server offers no DRI3, so Blender renders GL with llvmpipe on the CPU).
    """
    scope.validate()
    if scope.red_team or scope.target_roots or scope.network:
        raise ExecutionRefused("live Blender runs only in an ordinary workspace scope without network")
    _private_display_parts(x11_dir, xauth)
    argv = [executable, "--unshare-user", "--unshare-pid", "--unshare-net", "--unshare-ipc",
            "--unshare-uts", "--disable-userns", "--die-with-parent", "--new-session",
            "--cap-drop", "ALL", "--seccomp", str(seccomp_fd), "--clearenv", *_system_binds(),
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/tmp/home",
            "--ro-bind", str(x11_dir), "/tmp/.X11-unix", "--ro-bind", str(xauth), "/tmp/xauth"]
    # The workspace, read roots and .git exactly as run_bash mounts them.
    for root in scope.read_roots:
        if scope.workspace.is_relative_to(root):
            argv += ["--ro-bind-fd", str(mount_fds[root]), str(root)]
    argv += ["--bind-fd", str(mount_fds[scope.workspace]), str(scope.workspace)]
    for root in scope.read_roots:
        if not scope.workspace.is_relative_to(root):
            argv += ["--ro-bind-fd", str(mount_fds[root]), str(root)]
    git = scope.workspace / ".git"
    if git in mount_fds:
        argv += ["--ro-bind-fd", str(mount_fds[git]), str(git)]
    argv += ["--remount-ro", "/"]
    for key, value in (("PATH", "/usr/bin:/bin"), ("HOME", "/tmp/home"), ("TMPDIR", "/tmp"),
                       ("LANG", "C.UTF-8"), ("XAUTHORITY", "/tmp/xauth"), ("PWD", str(scope.workspace))):
        argv += ["--setenv", key, value]
    return argv + ["--chdir", str(scope.workspace), "--", *command]


def nested_display_argv(executable: str, program: str, *, x11_dir: Path, xauth: Path, host_display: str,
                        number: int, screen: tuple[int, int], title: str) -> list[str]:
    """The nested X server (Xephyr) that live Blender draws into, in its own bubblewrap.

    Its /tmp/.X11-unix is the session's private socket folder and its /tmp is private, so it
    can neither create nor remove anything in the host's /tmp/.X11-unix or its lock files,
    whatever display number it runs as (DREAM-109 incident, 2026-09-24 13:18: an unconfined
    Xephyr replaced and then deleted the host's X0 socket). It shares the host's network
    namespace only to reach the host display through its abstract socket, the only way in
    from here: the host's socket folder is not mounted. ``-nolisten tcp -nolisten local``
    leave it no socket but the one in the private folder, and ``-auth`` makes every client
    present the session's cookie. Its clients get no MIT-SHM (it runs in the host's SysV IPC
    namespace, so a client's segment id could name one of the owner's segments there), no
    XVideo (which Xephyr forwards to the host display) and no indirect GLX.
    """
    _private_display_parts(x11_dir, xauth)
    if not re.fullmatch(r":[0-9]{1,4}(\.[0-9]{1,2})?", host_display):
        raise ExecutionRefused("the host display must be a local X11 display such as :0")
    width, height = screen
    if not (0 < number < 10000 and 0 < width <= 8192 and 0 < height <= 8192):
        raise ExecutionRefused("invalid nested display number or size")
    return [executable, "--unshare-user", "--unshare-pid", "--unshare-uts", "--disable-userns",
            "--die-with-parent", "--new-session", "--cap-drop", "ALL", "--clearenv", *_system_binds(),
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--bind", str(x11_dir), "/tmp/.X11-unix", "--ro-bind", str(xauth), "/tmp/xauth",
            "--remount-ro", "/", "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/tmp",
            "--setenv", "DISPLAY", host_display, "--",
            program, f":{number}", "-auth", "/tmp/xauth", "-nolisten", "tcp", "-nolisten", "local",
            "-extension", "MIT-SHM", "-noxv", "-iglx",
            "-screen", f"{width}x{height}", "-no-host-grab", "-title", title]


@contextmanager
def _mount_descriptors(scope: ExecutionScope) -> Iterator[dict[Path, int]]:
    """Pin sources before exec so another tool cannot substitute a mount symlink."""
    scope.validate()
    roots = [scope.workspace, *scope.read_roots, *scope.target_roots]
    git = scope.workspace / ".git"
    if git.is_symlink():
        raise ExecutionRefused("a symlinked .git cannot be auto-mounted; use an explicit read scope")
    if git.exists():
        roots.append(git)
    descriptors: dict[Path, int] = {}
    try:
        for root in dict.fromkeys(roots):
            flags = os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC
            if root != git:
                flags |= os.O_DIRECTORY
            fd = os.open(root, flags)
            descriptors[root] = fd
            # O_NOFOLLOW rejects leaf symlinks; this catches replaced ancestors.
            # The descriptor, not a subsequently-resolved pathname, is mounted.
            actual = Path(os.readlink(f"/proc/self/fd/{fd}"))
            if actual != root or stat.S_ISLNK(os.fstat(fd).st_mode):
                raise ExecutionRefused("execution mount source changed during preparation")
        yield descriptors
    finally:
        for fd in descriptors.values():
            os.close(fd)


@contextmanager
def _socket_filter(inet: bool = False, unix: bool = False) -> Iterator[int]:
    """Block socket creation, including host Unix sockets in a bound workspace.

    Network namespaces alone do not stop filesystem Unix sockets. io_uring can
    create sockets without the socket syscall, so that API is disabled too.
    Private socketpair IPC remains available. libseccomp rejects alternate ABIs.
    inet admits AF_INET/AF_INET6 only: inside the sandbox's own network
    namespace they reach its loopback and nothing else. unix (the live-Blender
    profile only, for X11) also admits AF_UNIX; see blender_live_argv.
    """
    try:
        # Avoid find_library's fallback to executing PATH-selected compiler tools.
        lib = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    except OSError as exc:
        raise ExecutionRefused("libseccomp is required to isolate mounted Unix sockets") from exc
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                          ctypes.c_int, ctypes.c_uint, ctypes.c_void_p]
    lib.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    context = lib.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise ExecutionRefused("could not create the required syscall filter")
    try:
        for syscall in (b"socket", b"io_uring_setup"):
            number = lib.seccomp_syscall_resolve_name(syscall)
            if number < 0:
                raise ExecutionRefused("could not enforce the required socket filter")
            if syscall == b"socket" and inet and not unix:
                # Refuse every domain but 2 and 10 (the whole 64-bit register is
                # compared, so high bits cannot smuggle AF_UNIX past GT 10).
                rules = [(_SCMP_CMP_LT, 2), (_SCMP_CMP_GT, 10), *((_SCMP_CMP_EQ, d) for d in range(3, 10))]
            elif syscall == b"socket" and unix:
                # AF_UNIX (1), plus AF_INET (2) and AF_INET6 (10) when inet; the same
                # whole-register comparisons refuse everything else, AF_NETLINK included.
                allowed = (1, 2, 10) if inet else (1,)
                rules = [(_SCMP_CMP_LT, allowed[0]), (_SCMP_CMP_GT, allowed[-1]),
                         *((_SCMP_CMP_EQ, d) for d in range(allowed[0] + 1, allowed[-1]) if d not in allowed)]
            else:
                rules = [None]
            for rule in rules:
                arg = None if rule is None else ctypes.byref(_ScmpArgCmp(0, rule[0], rule[1], 0))
                if lib.seccomp_rule_add_array(context, 0x00050000 | errno.EPERM,
                                              number, 0 if rule is None else 1, arg) != 0:
                    raise ExecutionRefused("could not enforce the required socket filter")
        with tempfile.TemporaryFile() as file:
            if lib.seccomp_export_bpf(context, file.fileno()) != 0:
                raise ExecutionRefused("could not export the required syscall filter")
            file.seek(0)
            yield file.fileno()
    finally:
        lib.seccomp_release(context)


_SCMP_CMP_LT, _SCMP_CMP_EQ, _SCMP_CMP_GT = 2, 4, 6  # enum scmp_compare


class _ScmpArgCmp(ctypes.Structure):
    _fields_ = [("arg", ctypes.c_uint), ("op", ctypes.c_int),
                ("datum_a", ctypes.c_uint64), ("datum_b", ctypes.c_uint64)]


@asynccontextmanager
async def _network_proxy(enabled: bool):
    """The sandbox end of sandbox_net's control channel, served while in use."""
    if not enabled:
        yield None
        return
    from . import sandbox_net
    ours, theirs = socket.socketpair()
    ours.setblocking(False)
    task = asyncio.create_task(sandbox_net.serve(ours))
    try:
        yield theirs.fileno()
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        ours.close()
        theirs.close()


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    output: bytes
    timed_out: bool = False
    truncated: bool = False


def supervised_command(argv: Sequence[str], *, pass_fds: Sequence[int] = ()) -> list[str]:
    if sys.platform != "linux":
        raise ExecutionRefused("owned descendant cleanup currently requires Linux")
    if not _trusted_system_file(Path(_SUPERVISOR_PYTHON)):
        raise ExecutionRefused("owned execution requires a trusted system Python interpreter")
    return [_SUPERVISOR_PYTHON, "-I", "-c", _SUPERVISOR_SOURCE,
            ",".join(str(fd) for fd in pass_fds), *argv]


async def run_owned(argv: Sequence[str], *, cwd: Path, env: Mapping[str, str],
                    timeout: float = 120, max_output: int = 200_000,
                    pass_fds: Sequence[int] = (), input_data: bytes | None = None) -> ProcessResult:
    """Own/reap the whole command tree, including detached and double-forked children.

    A separate subreaper owns just this command, never unrelated engine children.
    Output is drained with a bounded retained prefix even when the command floods.
    """
    spawning = asyncio.create_task(asyncio.create_subprocess_exec(
        *supervised_command(argv, pass_fds=pass_fds), cwd=str(cwd), env=dict(env),
        stdin=asyncio.subprocess.PIPE if input_data is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, start_new_session=True, pass_fds=tuple(pass_fds),
    ))
    cancelled = False
    try:
        proc = await asyncio.shield(spawning)
    except asyncio.CancelledError:
        # Cancellation during exec must not lose the newly-created process.
        proc = await spawning
        cancelled = True
    output = bytearray()
    truncated = False

    async def drain() -> None:
        nonlocal truncated
        while chunk := await proc.stdout.read(65536):
            remaining = max(0, max_output - len(output))
            output.extend(chunk[:remaining])
            truncated |= len(chunk) > remaining

    async def finish() -> None:
        async def feed() -> None:
            if proc.stdin is not None:
                try:
                    proc.stdin.write(input_data)
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    proc.stdin.close()
        await asyncio.gather(drain(), feed(), proc.wait())

    completed = asyncio.create_task(finish())

    async def terminate() -> None:
        if proc.returncode is None:
            try:
                proc.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass
        await asyncio.wait_for(asyncio.shield(completed), 5)

    timed_out = False
    try:
        if cancelled:
            raise asyncio.CancelledError
        await asyncio.wait_for(asyncio.shield(completed), timeout)
    except asyncio.TimeoutError:
        timed_out = True
        await terminate()
    except BaseException:
        cleanup = asyncio.create_task(terminate())
        await asyncio.shield(cleanup)
        raise
    return ProcessResult(proc.returncode, bytes(output), timed_out, truncated)


async def probe_sandbox(scope: ExecutionScope) -> SandboxCapability:
    """Exercise the required namespaces/mounts, rather than merely finding bwrap."""
    executable = _bubblewrap_executable() if sys.platform == "linux" else None
    if not executable:
        return SandboxCapability(False, "trusted system Linux bubblewrap is not installed", scope)
    try:
        with _socket_filter() as fd, _mount_descriptors(scope) as mounts:
            argv = _bwrap_argv(scope, executable, ["/bin/true"], seccomp_fd=fd, mount_fds=mounts)
            result = await run_owned(argv, cwd=scope.workspace, env=minimal_environment(),
                                     timeout=3, max_output=2000, pass_fds=(fd, *mounts.values()))
        if result.returncode == 0 and not result.timed_out:
            return SandboxCapability(True, "bubblewrap filesystem, PID, network and socket isolation verified", scope, executable)
        reason = result.output.decode("utf-8", "replace").strip() or "namespace probe failed"
    except (OSError, ValueError, ExecutionRefused) as exc:
        reason = str(exc)
    return SandboxCapability(False, reason, scope, executable)


async def run_contained(argv: Sequence[str], scope: ExecutionScope, *, timeout: float = 30,
                        max_output: int = 200_000, input_data: bytes | None = None) -> ProcessResult:
    """Run a tool's worker within the real boundary; no host-approval fallback.

    Used for built-ins whose contract always requires containment. The caller
    supplies its fixed runtime and data; model text is never a host shell command.
    """
    scope.validate()
    capability = await probe_sandbox(scope)
    if not capability.available:
        raise ExecutionUnavailable(f"sandbox unavailable: {capability.reason}. Worker was not run.")
    scope.validate()
    if scope.expires_at is not None:
        timeout = min(timeout, max(0, scope.expires_at - time.monotonic()))
    with _socket_filter() as fd, _mount_descriptors(scope) as mounts:
        command = _bwrap_argv(scope, capability.executable, argv, seccomp_fd=fd, mount_fds=mounts)
        return await run_owned(command, cwd=scope.workspace, env=minimal_environment(),
                               timeout=timeout, max_output=max_output, input_data=input_data,
                               pass_fds=(fd, *mounts.values()))


async def execute_bash(command: str, context: ExecutionContext, *, timeout: float = 120,
                       max_output: int = 200_000) -> tuple[ProcessResult, bool]:
    from dream.core import policy, skill_runtime

    # DREAM-105: installed skills' script folders (and node's runtime) are visible READ-ONLY, so a skill runs its
    # scripts through the shell like any command; writes still reach only the workspace.
    scope = replace(context.scope, read_roots=tuple(dict.fromkeys(
        (*context.scope.read_roots, *skill_runtime.script_roots()))))
    env = minimal_environment(skill_runtime.script_env())
    scope.validate()
    if not isinstance(command, str) or not command.strip() or "\0" in command:
        raise ExecutionRefused("a non-empty command string is required")
    capability = await probe_sandbox(scope)
    decision, reason = policy.decide("run_bash", {"command": command}, context.mode,
                                     scope.workspace, execution_scope=scope,
                                     execution_capability=capability)
    if decision == "deny":
        raise ExecutionRefused(reason)
    approval = context.approval
    approved = approval is not None and approval.consume(command, context.scope)   # approved in the session's scope
    uncontained = bool(approved and approval.allow_uncontained)
    if not capability.available and not uncontained:
        raise ExecutionUnavailable(f"sandbox unavailable: {capability.reason}. Command was not run; "
                                   "explicit approval for this exact uncontained command is required.")
    if decision == "ask" and not approved:
        raise ExecutionRefused(f"approval required: {reason}")
    if uncontained and scope.red_team:
        raise ExecutionRefused("red-team execution never permits an uncontained fallback")
    argv = ["/bin/bash", "--noprofile", "--norc", "-c", command]
    scope.validate()  # probe time counts against an expiring scope
    if scope.expires_at is not None:
        timeout = min(timeout, max(0, scope.expires_at - time.monotonic()))
    if uncontained:
        result = await run_owned(argv, cwd=scope.workspace, env=env,
                                 timeout=timeout, max_output=max_output)
    else:
        async with _network_proxy(scope.network) as net_fd:
            with _socket_filter(inet=net_fd is not None) as fd, _mount_descriptors(scope) as mounts:
                argv = _bwrap_argv(scope, capability.executable, argv, seccomp_fd=fd,
                                   mount_fds=mounts, net_fd=net_fd)
                extra = () if net_fd is None else (net_fd,)
                result = await run_owned(argv, cwd=scope.workspace, env=env,
                                         timeout=timeout, max_output=max_output,
                                         pass_fds=(fd, *mounts.values(), *extra))
    return result, not uncontained


# The child executes this in-memory snapshot, never re-importing editable project
# source outside containment. Stdout belongs entirely to its command (e.g. MCP).
_SUPERVISOR_SOURCE = r'''
import ctypes
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

def _supervise(argv: list[str], pass_fds: tuple[int, ...] = ()) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    parent = os.getppid()
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "cannot establish subprocess ownership")
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise OSError(ctypes.get_errno(), "cannot monitor process owner")
    if os.getppid() != parent:
        return 143

    def children(pid: int) -> list[int]:
        try:
            return [int(p) for p in Path(f"/proc/{pid}/task/{pid}/children").read_text().split()]
        except (OSError, ValueError):
            return []

    def descendants(pid: int) -> list[int]:
        found = children(pid)
        for child in list(found):
            found.extend(descendants(child))
        return found

    def send(pid: int, sig: int) -> None:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass

    proc = subprocess.Popen(argv, start_new_session=True, pass_fds=pass_fds)
    for fd in pass_fds:
        os.close(fd)
    try:
        while not stopping and proc.poll() is None:
            time.sleep(0.01)
        status = proc.returncode if proc.returncode is not None else 143
    finally:
        # A successful shell can leave background children holding pipes open;
        # success, failure, timeout, cancellation and EOF all take this path.
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        for pid in descendants(os.getpid()):
            send(pid, signal.SIGTERM)
        deadline = time.monotonic() + 0.2
        while children(os.getpid()):
            if time.monotonic() >= deadline:
                for pid in reversed(descendants(os.getpid())):
                    send(pid, signal.SIGKILL)
            try:
                while os.waitpid(-1, os.WNOHANG)[0]:
                    pass
            except ChildProcessError:
                break
            time.sleep(0.01)
    return status if status >= 0 else 128 - status


if __name__ == "__main__":
    try:
        raise SystemExit(_supervise(sys.argv[2:], tuple(int(fd) for fd in sys.argv[1].split(",") if fd)))
    except OSError as exc:
        print(f"execution supervisor: {exc}", file=sys.stderr)
        raise SystemExit(125)
'''
