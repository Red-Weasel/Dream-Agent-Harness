"""DREAM-187 (browse), the structural layer: on Linux the browser runs inside bubblewrap with its own
network namespace, and each fetch's boundary is served on a listener Dream binds inside that namespace.
Host tests need no browser: they check the sandbox profile (what is unshared, what is mounted, what
environment gets in), the launch wrapper Playwright executes, the in-sandbox init that hands listeners
out, the probe, and that a failed launch leaves nothing behind. The tests marked live drive the real
browser (DREAM_TEST_LIVE_BROWSER=1)."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from dream.core import execution
from dream.web import netns

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="the network sandbox is Linux-only")


def _paths(argv: list[str], option: str) -> list[tuple[str, str]]:
    return [(argv[i + 1], argv[i + 2]) for i, a in enumerate(argv) if a == option]


def _setenv(argv: list[str]) -> dict[str, str]:
    return {argv[i + 1]: argv[i + 2] for i, a in enumerate(argv) if a == "--setenv"}


@pytest.fixture
def fake_install(monkeypatch, tmp_path):
    """A Camoufox root and active install under tmp_path (no real Camoufox needed), and the private
    folders under tmp_path too -> (root, executable, active)."""
    root = tmp_path / "cache" / "camoufox"
    active = root / "browsers" / "official" / "1.0"
    active.mkdir(parents=True)
    (active / "camoufox-bin").write_text("")
    (active / "properties.json").write_text("[]")
    monkeypatch.setattr(netns, "_install", lambda: (root, active / "camoufox-bin", active))
    monkeypatch.setattr(netns, "_tmp", lambda: tmp_path)
    return root, active / "camoufox-bin", active


@pytest.fixture
def plan(fake_install, monkeypatch, tmp_path):
    """A plan for fixed paths: the fake install, an interpreter under /usr, bubblewrap at its usual place."""
    monkeypatch.setattr(netns, "_interpreter", lambda: ("/usr/bin/python3", []))
    monkeypatch.setattr(netns, "_bubblewrap", lambda: "/usr/bin/bwrap")
    return netns._plan(control=tmp_path / "control", seccomp=tmp_path / "seccomp.bpf")


def test_the_profile_unshares_every_namespace_and_mounts_only_what_firefox_needs(plan, tmp_path):
    """run_bash's profile (core/execution.py), Firefox-shaped: every namespace unshared, nested user
    namespaces disabled, all capabilities dropped, the socket filter, a clean environment; read-only the
    system trees and /etc files run_bash mounts plus the Camoufox root; a private /tmp with HOME and
    Playwright's profile directory (the one writable mount); nothing of the host's HOME, /run, /sys or
    display."""
    profile = "/tmp/playwright_firefoxdev_profile-abc"
    argv = netns._argv(plan, seccomp_fd=7, profile=profile, env={"CAMOU_CONFIG_1": "{}"},
                       command=["/bin/true"])
    assert argv[0] == plan["bwrap"]
    for flag in ("--unshare-user", "--unshare-pid", "--unshare-net", "--unshare-ipc", "--unshare-uts",
                 "--disable-userns", "--die-with-parent", "--new-session", "--clearenv"):
        assert flag in argv, flag
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--seccomp") + 1] == "7"
    ro = _paths(argv, "--ro-bind")
    assert all(src == dst for src, dst in ro)
    expected = {p for p in ("/usr", "/bin", "/sbin", "/lib", "/lib64") if Path(p).exists()}
    expected |= {p for p in ("/etc/ld.so.cache", "/etc/localtime", "/etc/passwd", "/etc/group") if Path(p).is_file()}
    expected |= {"/etc/alternatives"} if Path("/etc/alternatives").exists() else set()
    expected.add(plan["cache"])
    assert {src for src, _ in ro} == expected
    assert _paths(argv, "--bind") == [(profile, profile)]
    assert ("--proc", "/proc") in zip(argv, argv[1:]) and ("--dev", "/dev") in zip(argv, argv[1:])
    assert ("--tmpfs", "/tmp") in zip(argv, argv[1:]) and ("--dir", "/tmp/home") in zip(argv, argv[1:])
    assert ("--remount-ro", "/") in zip(argv, argv[1:]) and ("--chdir", "/tmp/home") in zip(argv, argv[1:])
    assert _setenv(argv) == {"HOME": "/tmp/home", "TMPDIR": "/tmp", "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                             "CAMOU_CONFIG_1": "{}"}
    assert argv[argv.index("--") + 1:] == ["/bin/true"]
    home = str(Path.home())
    assert not any(home in a and plan["cache"] not in a for a in argv)  # no host HOME
    assert not any(a.startswith(("/run", "/sys", "/tmp/.X11-unix")) for a in argv)


def test_the_interpreter_dream_runs_on_is_bound_read_only_when_outside_the_system_trees(monkeypatch, tmp_path):
    """Dream may run on a uv-managed interpreter under ~/.local: the in-sandbox init runs on the same
    interpreter Dream does (the only one sure to exist), with its prefix mounted read-only."""
    prefix = tmp_path / "uv" / "cpython-3.12"
    (prefix / "bin").mkdir(parents=True)
    python = prefix / "bin" / "python3.12"
    python.write_text("")
    monkeypatch.setattr(execution, "_SUPERVISOR_PYTHON", str(python))
    monkeypatch.setattr(sys, "base_prefix", str(prefix))
    interpreter, binds = netns._interpreter()
    assert interpreter == str(python) and binds == [str(prefix)]
    monkeypatch.setattr(execution, "_SUPERVISOR_PYTHON", "/usr/bin/python3")
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    assert netns._interpreter() == ("/usr/bin/python3", [])  # already under /usr


def test_the_camoufox_root_is_resolved_through_camoufox_itself_without_installing(monkeypatch, tmp_path):
    """The root is Camoufox's own INSTALL_DIR (XDG_CACHE_HOME respected) and the executable the active
    install's, found by Camoufox's functions that never download or clean anything; no install -> the
    sandbox is unavailable with a reason, and camoufox_path() is never called."""
    pkgman = pytest.importorskip("camoufox.pkgman")
    multiversion = pytest.importorskip("camoufox.multiversion")
    root = tmp_path / "xdg" / "camoufox"
    active = root / "browsers" / "official" / "2.0"
    active.mkdir(parents=True)
    (active / "camoufox-bin").write_text("")
    monkeypatch.setattr(pkgman, "INSTALL_DIR", root)
    monkeypatch.setattr(pkgman, "camoufox_path", lambda *a, **k: pytest.fail("camoufox_path() must never be called"))
    monkeypatch.setattr(multiversion, "get_active_path", lambda: active)
    assert netns._install() == (root, active / "camoufox-bin", active)
    monkeypatch.setattr(multiversion, "get_active_path", lambda: None)
    with pytest.raises(netns.Unavailable, match="not installed"):
        netns._install()


async def _accept(control: socket.socket) -> socket.socket:
    loop = asyncio.get_running_loop()
    peer, _ = await asyncio.wait_for(loop.sock_accept(control), 5)
    return peer


async def test_the_wrapper_binds_the_profile_and_passes_only_the_browser_environment(fake_install, monkeypatch, tmp_path):
    """Playwright executes the wrapper with Firefox's arguments and the launcher's environment. It connects
    the control channel, opens the seccomp program and execs bubblewrap with the profile's exact argv:
    the `-profile` directory Playwright chose is the writable mount, and of the environment only the
    launcher's own variables get in (the fingerprint, the fontconfig file, Mozilla's) -- nothing else of
    Dream's."""
    _, _, active = fake_install
    fake = tmp_path / "bwrap"
    fake.write_text("#!/bin/sh\nprintf '%s\\0' \"$@\"\nprintf '===\\n'\nenv | sort\n")  # NUL-separated: the init source spans lines
    fake.chmod(stat.S_IRWXU)
    monkeypatch.setattr(netns, "_bubblewrap", lambda: str(fake))
    sandbox = netns.Sandbox()
    try:
        assert sandbox.launch_kwargs() == {"executable_path": str(sandbox.wrapper), "env": {}}
        assert (sandbox.dir / "properties.json").resolve() == active / "properties.json"
        assert stat.S_IMODE(sandbox.dir.stat().st_mode) == 0o700
        profile = str(tmp_path / "playwright_firefoxdev_profile-x")
        args = ["-no-remote", "-headless", "-profile", profile, "-juggler-pipe", "-silent"]
        env = {"CAMOU_CONFIG_1": '{"a":1}', "CAMOU_CONFIG_2": "x", "FONTCONFIG_FILE": "/f.conf",
               "MOZ_CRASHREPORTER": "1", "DREAM_SECRET": "no", "PATH": "/usr/bin", "HOME": str(tmp_path)}
        proc = await asyncio.create_subprocess_exec(str(sandbox.wrapper), *args, env=env,
                                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        peer = await _accept(sandbox._listener)
        out, err = await asyncio.wait_for(proc.communicate(), 10)
        peer.close()
    finally:
        await sandbox.close()
    assert proc.returncode == 0, err.decode()
    argv_bytes, _, env_bytes = out.partition(b"===\n")
    argv, env_text = [str(fake), *argv_bytes.decode().split("\0")[:-1]], env_bytes.decode()
    seccomp_fd = argv[argv.index("--seccomp") + 1]
    control_fd = argv[argv.index("-c") + 2]
    assert seccomp_fd.isdigit() and control_fd.isdigit()
    keep = {k: v for k, v in env.items() if k.startswith(("CAMOU_CONFIG_", "MOZ_")) or k == "FONTCONFIG_FILE"}
    expected = netns._argv(sandbox.plan, seccomp_fd=int(seccomp_fd), profile=profile, env=keep,
                           command=netns._command(sandbox.plan, int(control_fd), args))
    assert argv == expected
    assert "DREAM_SECRET" not in _setenv(argv) and "PATH" in _setenv(argv) and _setenv(argv)["HOME"] == "/tmp/home"
    assert "DREAM_SECRET=no" in env_text  # the wrapper itself ran with Dream's environment: --clearenv takes it away


@pytest.fixture
async def available():
    capability = await netns.probe()
    if not capability.available:
        pytest.skip(f"the browser sandbox is unavailable here: {capability.reason}")
    return capability


async def test_the_init_hands_out_a_listener_bound_in_its_own_network_namespace(available, tmp_path):
    """Inside the real sandbox, the init runs its command and, asked over the control channel, binds a
    loopback listener in the sandbox's network namespace and passes the socket out: the host serves it,
    nothing on the host can connect to that port, and the command's namespace is not the host's. The host
    letting go of the control channel ends the command."""
    root, executable, _ = netns._install()
    plan = netns._plan(control=tmp_path / "control", seccomp=tmp_path / "seccomp.bpf")
    (tmp_path / "seccomp.bpf").write_bytes(netns._seccomp())
    profile = tmp_path / "profile"
    profile.mkdir()
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    fd = os.open(tmp_path / "seccomp.bpf", os.O_RDONLY)
    command = netns._command(plan, theirs.fileno(), ["-c", "readlink /proc/self/ns/net; exec sleep 30"])
    command[command.index(str(executable))] = "/bin/sh"  # the browser's place, taken by a shell
    argv = netns._argv(plan, seccomp_fd=fd, profile=str(profile), env={}, command=command)
    proc = await asyncio.create_subprocess_exec(*argv, pass_fds=(fd, theirs.fileno()),
                                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    theirs.close()
    os.close(fd)
    sandbox = netns.Sandbox.__new__(netns.Sandbox)
    sandbox._control, sandbox._lock, sandbox._serial = ours, asyncio.Lock(), 0
    ours.setblocking(False)
    try:
        listener = await sandbox.listener()
        assert listener.family == socket.AF_INET and listener.getsockname()[0] == "127.0.0.1"
        port = listener.getsockname()[1]
        server = await asyncio.start_server(lambda r, w: w.close(), sock=listener)
        with pytest.raises(OSError):
            await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 2)  # not a host port
        server.close()
        inside = await asyncio.wait_for(proc.stdout.readline(), 5)
        assert inside.strip() and inside.strip() != os.readlink("/proc/self/ns/net").encode()
    finally:
        ours.close()  # the host lets go: the init ends the command
        try:
            await asyncio.wait_for(proc.wait(), 5)
        finally:
            if proc.returncode is None:
                proc.kill()
    assert proc.returncode is not None


# The init run on this interpreter outside the sandbox: the control descriptor moved to 10, /dev/null at 3 and 4
# (the Juggler pipe's places, which the init hands the browser), then the real INIT_SOURCE with the command given.
_HOST_INIT = r'''
import os, sys
control, source = int(sys.argv[1]), sys.argv[2]
os.dup2(control, 10)                  # in this fresh process only 0-2 and the control descriptor are open
if control != 10:
    os.close(control)
for n in (3, 4):
    if os.open("/dev/null", os.O_RDWR) != n:
        raise SystemExit(f"descriptor {n} was taken")
    os.set_inheritable(n, True)
os.execv(sys.executable, [sys.executable, "-I", "-c", source, "10", "--", *sys.argv[3:]])
'''


async def test_the_init_ends_the_browser_when_its_reply_cannot_be_sent():
    """Codex round 3, item 7 (DREAM-204): the init's reply failing (Dream gone while a listener was being made:
    EPIPE) must end the browser as control EOF does -- not leave the serving thread dead and the browser running
    until bubblewrap goes. The real init runs here with `sleep 30` in the browser's place; the reading side of the
    control channel is shut before the request goes, so the init receives it and its reply fails."""
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    proc = await asyncio.create_subprocess_exec(sys.executable, "-c", _HOST_INIT, str(theirs.fileno()), netns.INIT_SOURCE,
                                                "sleep", "30", pass_fds=(theirs.fileno(),), start_new_session=True,
                                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    theirs.close()
    try:
        ours.shutdown(socket.SHUT_RD)  # the init's sends fail from here on; ours still reach it
        ours.send(b"L\x00\x00\x00\x01")
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            pytest.fail("the init kept the browser running after its reply failed")
        _, err = await proc.communicate()
        assert proc.returncode == 137, (proc.returncode, err.decode())  # the browser's exit: killed, 128 - (-9)
    finally:
        if proc.returncode is None:  # the init and its sleep, which holds the pipes open otherwise
            os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
        ours.close()


async def test_the_probe_names_a_missing_or_failing_bubblewrap(fake_install, monkeypatch, tmp_path):
    monkeypatch.setattr(execution, "_bubblewrap_executable", lambda: None)
    capability = await netns.probe()
    assert not capability.available and "bubblewrap" in capability.reason
    fake = tmp_path / "bwrap"
    fake.write_text("#!/bin/sh\necho 'bwrap: no permission for namespaces' >&2\nexit 1\n")
    fake.chmod(stat.S_IRWXU)
    monkeypatch.setattr(netns, "_bubblewrap", lambda: str(fake))
    capability = await netns.probe()
    assert not capability.available and "no permission for namespaces" in capability.reason
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("dream-browser-")]  # nothing left behind


async def test_a_sandbox_that_never_launched_leaves_no_private_folder(fake_install, tmp_path):
    sandbox = netns.Sandbox()
    assert sandbox.dir.is_dir() and sandbox.wrapper.is_file() and (sandbox.dir / "seccomp.bpf").is_file()
    await sandbox.close()
    assert not sandbox.dir.exists()


async def test_create_closes_the_sandbox_when_the_probe_raises(fake_install, monkeypatch, tmp_path):
    """The probe raising -- an OSError, or the fetch's cancellation landing in it -- must not leave the
    folder or the control socket behind: create() owns the sandbox for its whole run."""
    for raised in (OSError("boom"), asyncio.CancelledError()):
        async def failing(self, _exc=raised):
            raise _exc

        monkeypatch.setattr(netns.Sandbox, "probe", failing)
        with pytest.raises(type(raised)):
            await netns.Sandbox.create()
        assert not [p for p in tmp_path.iterdir() if p.name.startswith("dream-browser-")], type(raised)


async def test_a_cancelled_probe_ends_and_reaps_its_subprocess(fake_install, monkeypatch, tmp_path):
    """Cancelled while the probe's process runs, the probe kills and reaps it before the cancellation
    goes on: no process of the probe outlives it, and its folder is gone."""
    pidfile = tmp_path / "pid"
    monkeypatch.setattr(netns, "_argv", lambda plan, **kw: ["/bin/sh", "-c", f"echo $$ > {pidfile}; exec sleep 30"])
    sandbox = netns.Sandbox()
    try:
        probing = asyncio.create_task(sandbox.probe())
        for _ in range(100):
            if pidfile.exists() and pidfile.read_text().strip():
                break
            await asyncio.sleep(0.05)
        pid = int(pidfile.read_text())
        probing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await probing
        for _ in range(40):  # killed and reaped: no such process, or a zombie already collected
            try:
                state = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[0]
            except OSError:
                state = "gone"
            if state in ("gone", "Z"):
                break
            await asyncio.sleep(0.05)
        assert state in ("gone", "Z"), state
        assert not [p for p in sandbox.dir.iterdir() if p.name.startswith("probe-")]
    finally:
        await sandbox.close()


async def test_a_probe_that_cannot_open_its_program_reports_and_cleans_up(fake_install):
    """The seccomp program missing when the probe opens it: a capability with the reason, not an exception,
    and no probe folder left in the sandbox's."""
    sandbox = netns.Sandbox()
    try:
        (sandbox.dir / "seccomp.bpf").unlink()
        capability = await sandbox.probe()
        assert not capability.available and "seccomp.bpf" in capability.reason
        assert not [p for p in sandbox.dir.iterdir() if p.name.startswith("probe-")]
    finally:
        await sandbox.close()


async def test_a_late_reply_to_an_interrupted_request_never_answers_the_next(fake_install, monkeypatch):
    """Request A times out (the init slow to answer); its reply comes later, while B asks. B must get its
    own listener, never A's, and A's late listener is closed unused: each request carries a tag the init
    echoes, and a reply with another tag is discarded."""
    monkeypatch.setattr(netns, "_ASK_S", 0.2)
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    ours.setblocking(False)
    theirs.setblocking(False)
    sandbox = netns.Sandbox.__new__(netns.Sandbox)
    sandbox._control, sandbox._lock, sandbox._serial, sandbox._listener, sandbox.dir = ours, asyncio.Lock(), 0, None, None
    first, second = socket.socket(), socket.socket()
    first.bind(("127.0.0.1", 0))
    second.bind(("127.0.0.1", 0))
    loop = asyncio.get_running_loop()

    async def init():
        """The sandbox's init, slow on the first request: it answers A after B was sent."""
        a = await loop.sock_recv(theirs, 64)
        b = await loop.sock_recv(theirs, 64)
        assert a[:1] == b[:1] == b"L" and a != b, (a, b)
        socket.send_fds(theirs, [a], [first.fileno()])
        socket.send_fds(theirs, [b], [second.fileno()])

    serving = asyncio.create_task(init())
    try:
        with pytest.raises(netns.Unavailable, match="did not answer"):
            await sandbox.listener()  # A: no answer within the wait
        fds_before = len(os.listdir("/proc/self/fd"))
        got = await sandbox.listener()  # B: the init answers A late, then B
        await serving
        assert got.getsockname() == second.getsockname(), (got.getsockname(), first.getsockname(), second.getsockname())
        assert len(os.listdir("/proc/self/fd")) == fds_before + 1  # B's listener; A's late one closed unused
        got.close()
    finally:
        serving.cancel()
        for sock in (ours, theirs, first, second):
            sock.close()


class _StuckProcess:
    """A probe process that never finishes on its own: communicate() waits forever, wait() waits until
    the test lets go -- so a cancellation can land in the reap as well as in the run."""

    def __init__(self):
        self.returncode, self.killed = None, False
        self.communicating, self.waiting, self.release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def communicate(self):
        self.communicating.set()
        await asyncio.Event().wait()

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waiting.set()
        await self.release.wait()
        self.returncode = -9
        return -9


async def test_a_second_cancellation_during_the_probes_cleanup_releases_everything(fake_install, monkeypatch):
    """Cancelled while the probe's process runs, then cancelled again while the cleanup reaps it: the
    process is still killed and reaped, the program's descriptor closed and the probe folder removed --
    nothing that must happen sits behind an await a second cancellation can skip."""
    proc = _StuckProcess()

    async def spawn(*args, **kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    sandbox = netns.Sandbox()
    try:
        fds_before = len(os.listdir("/proc/self/fd"))
        probing = asyncio.create_task(sandbox.probe())
        await asyncio.wait_for(proc.communicating.wait(), 5)
        probing.cancel()
        await asyncio.wait_for(proc.waiting.wait(), 5)  # the cleanup is reaping
        probing.cancel()  # and is cancelled again there
        await asyncio.sleep(0.05)
        proc.release.set()
        with pytest.raises(asyncio.CancelledError):
            await probing
        assert proc.killed and proc.returncode == -9  # killed, and reaped before the cancellation went on
        assert len(os.listdir("/proc/self/fd")) == fds_before  # the program's descriptor closed
        assert not [p for p in sandbox.dir.iterdir() if p.name.startswith("probe-")]  # the folder gone
    finally:
        await sandbox.close()


async def test_discarded_replies_do_not_extend_the_request_deadline(fake_install, monkeypatch):
    """A stream of replies to other requests must not keep a request alive: one deadline, taken before
    the send, covers the send and every reply, discarded ones included; each discarded descriptor is
    closed."""
    import time
    monkeypatch.setattr(netns, "_ASK_S", 0.25)
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    ours.setblocking(False)
    sandbox = netns.Sandbox.__new__(netns.Sandbox)
    sandbox._control, sandbox._lock, sandbox._serial = ours, asyncio.Lock(), 0
    dummy = socket.socket()

    async def spam():
        for _ in range(16):
            await asyncio.sleep(0.05)
            socket.send_fds(theirs, [b"L\x00\x00\x00\x00"], [dummy.fileno()])  # never a tag of ours

    spamming = asyncio.create_task(spam())
    try:
        fds_before = len(os.listdir("/proc/self/fd"))
        started = time.monotonic()
        with pytest.raises(netns.Unavailable, match="did not answer"):
            await sandbox.listener()
        elapsed = time.monotonic() - started
        assert 0.2 <= elapsed < 0.5, elapsed  # the one deadline, not one per discarded reply
        assert len(os.listdir("/proc/self/fd")) == fds_before  # every discarded descriptor closed
    finally:
        spamming.cancel()
        for sock in (ours, theirs, dummy):
            sock.close()


async def test_a_spent_serial_retires_the_channel_before_it_could_wrap(fake_install):
    """The tags never wrap on one channel: at the last serial the channel is retired -- closed, so the
    init ends the browser -- and the request reads as the browser gone, which relaunches it through the
    sandbox."""
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    ours.setblocking(False)
    theirs.setblocking(False)
    sandbox = netns.Sandbox.__new__(netns.Sandbox)
    sandbox._control, sandbox._lock, sandbox._serial, sandbox._listener, sandbox.dir = ours, asyncio.Lock(), 2 ** 32 - 2, None, None
    loop = asyncio.get_running_loop()
    handed = socket.socket()
    handed.bind(("127.0.0.1", 0))

    async def init():
        request = await loop.sock_recv(theirs, 64)
        socket.send_fds(theirs, [request], [handed.fileno()])
        return await loop.sock_recv(theirs, 64)  # what comes next: EOF once the channel is retired

    serving = asyncio.create_task(init())
    try:
        got = await sandbox.listener()  # the last tag there is
        assert got.getsockname() == handed.getsockname()
        got.close()
        with pytest.raises(ConnectionError, match="closed"):
            await sandbox.listener()
        assert sandbox._control is None
        assert await asyncio.wait_for(serving, 5) == b""  # the init saw the channel close
    finally:
        serving.cancel()
        for sock in (ours, theirs, handed):
            sock.close()


async def test_callers_queued_behind_the_channels_retirement_all_read_it_as_the_browser_gone(fake_install):
    """Codex round 3, item 5 (DREAM-204): at the last serial the caller that retires the channel gets the reset
    error -- and so must every caller queued behind the lock at that moment (they passed the attached check before
    the retirement): the channel is None by the time they hold the lock, which reads as the browser gone too, never
    an AttributeError."""
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    ours.setblocking(False)
    theirs.setblocking(False)
    sandbox = netns.Sandbox.__new__(netns.Sandbox)
    sandbox._control, sandbox._lock, sandbox._serial, sandbox._listener, sandbox.dir = ours, asyncio.Lock(), 2 ** 32 - 2, None, None
    loop = asyncio.get_running_loop()
    handed = socket.socket()
    handed.bind(("127.0.0.1", 0))
    queued = asyncio.Event()

    async def init():
        request = await loop.sock_recv(theirs, 64)  # the last tag there is
        await queued.wait()  # answered once two more callers wait behind the lock
        socket.send_fds(theirs, [request], [handed.fileno()])
        return await loop.sock_recv(theirs, 64)  # EOF once the channel is retired

    serving = asyncio.create_task(init())
    first = asyncio.create_task(sandbox.listener())
    await asyncio.sleep(0)  # first holds the lock: its request sent, the reply awaited
    second, third = asyncio.create_task(sandbox.listener()), asyncio.create_task(sandbox.listener())
    await asyncio.sleep(0)  # both past the attached check, waiting for the lock
    assert sandbox._lock.locked() and not second.done() and not third.done()
    queued.set()
    try:
        got = await first
        got.close()
        results = await asyncio.gather(second, third, return_exceptions=True)
        assert all(isinstance(r, ConnectionResetError) and "closed" in str(r) for r in results), results
        assert sandbox._control is None
        assert await asyncio.wait_for(serving, 5) == b""  # the channel closed once: the init saw EOF
    finally:
        serving.cancel()
        for sock in (ours, theirs, handed):
            sock.close()


async def test_a_closed_control_channel_reads_as_the_browser_gone(fake_install):
    """The init gone (control EOF) is the browser gone: the request fails as a closed connection, which
    the fetch's self-heal takes as a dead browser (reset, relaunch once) rather than a sandbox policy."""
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    ours.setblocking(False)
    theirs.close()
    sandbox = netns.Sandbox.__new__(netns.Sandbox)
    sandbox._control, sandbox._lock, sandbox._serial = ours, asyncio.Lock(), 0
    try:
        with pytest.raises(ConnectionError, match="closed"):
            await sandbox.listener()
    finally:
        ours.close()


async def test_attach_refuses_a_control_connection_from_nobody(fake_install, monkeypatch):
    """The control channel is accepted only once the browser launched through the wrapper; nothing
    connecting within the wait means the browser did not go through it, and the sandbox fails closed."""
    monkeypatch.setattr(netns, "_ATTACH_S", 0.2)
    sandbox = netns.Sandbox()
    try:
        with pytest.raises(netns.Unavailable, match="did not connect"):
            await sandbox.attach()
    finally:
        await sandbox.close()


# --- browser.py through the sandbox ---------------------------------------------------------------------

from dream.core import sandbox_net  # noqa: E402
from dream.web import browser as browser_mod  # noqa: E402
from dream.web.browser import Browser  # noqa: E402


class _FakeSandbox:
    """A sandbox that hands out host loopback listeners (a namespace cannot be faked): what browser.py does
    with it is the question."""

    def __init__(self):
        self.attached = self.closed = False
        self.listeners: list[tuple[str, int]] = []  # each listener's address (the fetch closes the socket itself)

    def launch_kwargs(self):
        return {"executable_path": "/private/launch", "env": {}}

    async def attach(self):
        self.attached = True

    async def listener(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.listeners.append(sock.getsockname()[:2])
        return sock

    async def close(self):
        self.closed = True


class _Response:
    def __init__(self, url, boundary):
        self.url, self.status, self._boundary = url, 200, boundary
        self.request = type("R", (), {"is_navigation_request": staticmethod(lambda: True)})()

    async def server_addr(self):
        return {"ipAddress": self._boundary[0], "port": self._boundary[1]}


class _Page:
    def __init__(self, boundary):
        self._boundary, self._listeners, self.url = boundary, [], "https://example.com/"

    def on(self, event, listener):
        self._listeners.append(listener)

    async def goto(self, url, **_):
        response = _Response(url, self._boundary)
        for listener in self._listeners:
            listener(response)
        return response

    async def title(self):
        return "Fake"

    async def content(self):
        return "<html><body><p>Fake body text</p></body></html>"


class _Context:
    def __init__(self, boundary):
        self.boundary = boundary

    async def new_page(self):
        return _Page(self.boundary)

    async def close(self):
        pass


class _Launched:
    """What a fake launcher's browser records: the proxy each context was given."""

    def __init__(self):
        self.contexts: list[_Context] = []

    async def new_context(self, proxy, **_):
        from urllib.parse import urlsplit
        parts = urlsplit(proxy["server"])
        self.contexts.append(_Context((parts.hostname, parts.port)))
        return self.contexts[-1]


@pytest.fixture
def launcher(monkeypatch):
    """A fake Camoufox launcher recording its kwargs; the fetch's name resolution answers a public address."""
    launches: list[dict] = []
    launched = _Launched()

    class _Camoufox:
        def __init__(self, **kwargs):
            launches.append(kwargs)

        async def __aenter__(self):
            return launched

        async def __aexit__(self, *exc):
            return None

    async def public(host):
        return ["93.184.216.34"]

    monkeypatch.setattr(browser_mod, "_load_camoufox", lambda: _Camoufox)
    monkeypatch.setattr(browser_mod.config, "BROWSER_IDLE_SHUTDOWN_S", 0)
    monkeypatch.setattr(browser_mod.config, "BROWSER_HEADLESS", True)
    monkeypatch.setattr(sandbox_net, "_resolve", public)
    return launches, launched


@pytest.fixture
def sandboxed(monkeypatch):
    """The sandbox browser.py gets on Linux: a fake one, created in place of netns.Sandbox.create()."""
    fake = _FakeSandbox()

    async def create(**_):
        return fake

    monkeypatch.setattr(netns, "supported", lambda: True)
    monkeypatch.setattr(netns.Sandbox, "create", create)
    return fake


async def test_the_browser_launches_through_the_sandbox_and_serves_each_boundary_inside_it(launcher, sandboxed):
    """On Linux the launcher is given the sandbox's wrapper as the executable and an empty environment to
    build on, the control channel is attached once the browser is up, and every fetch's boundary listens
    on a socket the sandbox handed out -- the context's proxy names that socket's port, Firefox reports it
    as the address it connected to, and the page comes back. Closing the browser closes the sandbox."""
    launches, launched = launcher
    browser = Browser()
    res = await browser.fetch("https://example.com/")
    assert launches[0]["executable_path"] == "/private/launch" and launches[0]["env"] == {}
    assert launches[0]["firefox_user_prefs"] is browser_mod._PROXY_PREFS and launches[0]["headless"] is True
    assert sandboxed.attached and browser._sandbox is sandboxed
    assert res.get("status") == 200 and "Fake body text" in res["text"], res
    assert len(sandboxed.listeners) == 1
    assert launched.contexts[0].boundary == sandboxed.listeners[0]
    res = await browser.fetch("https://example.com/again")
    assert res.get("status") == 200 and launched.contexts[1].boundary == sandboxed.listeners[1]
    await asyncio.sleep(0)
    await browser.aclose()
    assert sandboxed.closed and browser._sandbox is None and not browser.running


async def test_where_the_sandbox_is_not_supported_the_browser_launches_as_today(launcher, monkeypatch):
    """Another platform keeps the proxy-only boundary: no wrapper, the launcher's own executable and
    environment, the boundary on this host's loopback."""
    launches, launched = launcher
    monkeypatch.setattr(netns, "supported", lambda: False)
    browser = Browser()
    res = await browser.fetch("https://example.com/")
    assert "executable_path" not in launches[0] and "env" not in launches[0]
    assert browser._sandbox is None and res.get("status") == 200
    assert launched.contexts[0].boundary[0] == "127.0.0.1" and launched.contexts[0].boundary[1] > 0
    await asyncio.sleep(0)
    await browser.aclose()


async def test_an_unavailable_sandbox_fails_the_fetch_closed(launcher, monkeypatch):
    """No bubblewrap (or a failing one): the browser is not launched at all, and the fetch's error says why."""
    launches, _ = launcher

    async def unavailable(**_):
        raise netns.Unavailable("the browser sandbox is unavailable: trusted system bubblewrap (/usr/bin/bwrap) is not installed")

    monkeypatch.setattr(netns, "supported", lambda: True)
    monkeypatch.setattr(netns.Sandbox, "create", unavailable)
    browser = Browser()
    res = await browser.fetch("https://example.com/")
    assert res["error"].startswith("the browser sandbox is unavailable: trusted system bubblewrap"), res
    assert not launches and not browser.running and browser._sandbox is None


async def test_a_launch_that_fails_closes_its_sandbox(launcher, sandboxed, monkeypatch):
    """The launcher failing (or the browser never connecting the control channel) leaves no sandbox
    behind: its folder and channel are closed, and the fetch reports the failure."""
    class _Failing:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            raise RuntimeError("Failed to launch the browser process")

        async def __aexit__(self, *exc):
            return None

    monkeypatch.setattr(browser_mod, "_load_camoufox", lambda: _Failing)
    browser = Browser()
    res = await browser.fetch("https://example.com/")
    assert "Failed to launch the browser process" in res["error"] and not browser.running
    assert sandboxed.closed and browser._sandbox is None


async def test_the_browser_never_connecting_the_control_channel_is_a_failed_launch(launcher, sandboxed, monkeypatch):
    launches, _ = launcher
    closed = []

    async def never():
        raise netns.Unavailable("the browser did not connect the sandbox's control channel")

    monkeypatch.setattr(sandboxed, "attach", never)
    browser = Browser()
    res = await browser.fetch("https://example.com/")
    assert "did not connect" in res["error"] and not browser.running and sandboxed.closed


async def test_a_closed_control_channel_relaunches_the_browser_once_through_the_sandbox(launcher, sandboxed, monkeypatch):
    """A retired or closed channel (the init gone, or the tags spent) is the browser gone: the fetch's
    self-heal resets and relaunches -- through the sandbox again, once -- and the page comes back."""
    launches, _ = launcher
    real = sandboxed.listener
    calls = []

    async def gone_then_fine():
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionResetError("the sandbox's control channel closed: the browser is gone")
        return await real()

    monkeypatch.setattr(sandboxed, "listener", gone_then_fine)
    browser = Browser()
    res = await browser.fetch("https://example.com/")
    assert res.get("status") == 200, res
    assert len(launches) == 2 and all(l["executable_path"] == "/private/launch" for l in launches)
    assert browser.running and browser._sandbox is sandboxed
    await asyncio.sleep(0)
    await browser.aclose()


# --- policy: fail closed on Linux, proxy-only elsewhere, headless only inside, and the tool says so --------


async def test_headed_mode_is_refused_inside_the_sandbox(launcher, monkeypatch):
    """The sandbox has no display: DREAM_BROWSER_HEADLESS=0 on Linux is refused before anything is
    built or launched, with the setting named -- never a browser outside the sandbox."""
    launches, _ = launcher
    monkeypatch.setattr(netns, "supported", lambda: True)
    monkeypatch.setattr(browser_mod.config, "BROWSER_HEADLESS", False)
    browser = Browser()
    res = await browser.fetch("https://example.com/")
    assert "headless" in res["error"] and "DREAM_BROWSER_HEADLESS" in res["error"], res
    assert not launches and not browser.running and browser._sandbox is None


async def test_where_the_sandbox_is_not_supported_headed_mode_stays_allowed(launcher, monkeypatch):
    launches, _ = launcher
    monkeypatch.setattr(netns, "supported", lambda: False)
    monkeypatch.setattr(browser_mod.config, "BROWSER_HEADLESS", False)
    browser = Browser()
    res = await browser.fetch("https://example.com/")
    assert res.get("status") == 200 and launches[0]["headless"] is False
    await asyncio.sleep(0)
    await browser.aclose()


async def test_an_unavailable_sandbox_says_why_and_that_nothing_was_fetched(fake_install, monkeypatch, tmp_path):
    """The message the fetch carries: the probe's reason, that the browser runs only inside bubblewrap on
    Linux, that nothing was fetched; and the private folder is gone."""
    async def failing(self):
        return netns.Capability(False, "bwrap: setting up uid map: Permission denied")

    monkeypatch.setattr(netns.Sandbox, "probe", failing)
    with pytest.raises(netns.Unavailable) as caught:
        await netns.Sandbox.create()
    message = str(caught.value)
    assert message.startswith("the browser sandbox is unavailable: bwrap: setting up uid map: Permission denied")
    assert "bubblewrap" in message and "nothing was fetched" in message
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("dream-browser-")]


def test_the_tool_text_says_where_the_browser_runs():
    from dream.tools import web
    text = web.browse.description
    assert "bubblewrap" in text and "network namespace" in text and "Linux" in text
    assert "network traffic" in text  # what the sandbox cuts: Playwright's control pipes cross it by design
    assert "proxy" in text  # elsewhere: the boundary is the proxy alone


# --- the real browser, inside the real sandbox (DREAM_TEST_LIVE_BROWSER=1, XDG_CACHE_HOME at the cache) ----


@pytest.fixture
async def live(monkeypatch):
    if os.environ.get("DREAM_TEST_LIVE_BROWSER") != "1":
        pytest.skip("set DREAM_TEST_LIVE_BROWSER=1 (with Camoufox installed) to drive the real browser")
    try:
        netns._install()
    except netns.Unavailable as exc:
        pytest.skip(str(exc))
    monkeypatch.setattr(browser_mod.config, "BROWSER_HEADLESS", True)
    monkeypatch.setattr(browser_mod.config, "BROWSER_IDLE_SHUTDOWN_S", 0)
    browser = Browser()
    try:
        yield browser
    finally:
        await browser.aclose()


def _descendants() -> dict[int, int]:
    """pid -> parent pid, for every process."""
    parents = {}
    for p in Path("/proc").iterdir():
        if p.name.isdigit():
            try:
                parents[int(p.name)] = int((p / "stat").read_text().rsplit(")", 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                pass
    return parents


def _our_firefox_pids() -> list[int]:
    """The browser's processes under this test process, by the executable the sandbox launches."""
    executable = str(netns._install()[1]).encode()
    parents, found = _descendants(), []
    for pid in parents:
        try:
            if not (Path("/proc") / str(pid) / "cmdline").read_bytes().startswith(executable):
                continue
        except OSError:
            continue
        p = pid
        while p in parents and p != os.getpid() and p > 1:
            p = parents[p]
        if p == os.getpid():
            found.append(pid)
    return found


class _Counter:
    """An HTTP service on every interface that counts connections and answers one small page."""

    def __init__(self):
        self.connections, self.port, self._server = 0, 0, None

    async def start(self):
        async def serve(reader, writer):
            self.connections += 1
            body = b"<html><head><title>counted</title></head><body><p>hello from the host</p></body></html>"
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: %d\r\n"
                         b"Connection: close\r\n\r\n" % len(body) + body)
            try:
                await writer.drain()
            except OSError:
                pass
            writer.close()
        self._server = await asyncio.start_server(serve, "0.0.0.0", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def close(self):
        self._server.close()


class _Datagrams(asyncio.DatagramProtocol):
    def __init__(self):
        self.count = 0

    def datagram_received(self, data, addr):
        self.count += 1


def _lan_address() -> str | None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return None


_WEBRTC = """async ([targets]) => {
  const cands = [];
  const pc = new RTCPeerConnection({iceServers: [{urls: targets}]});
  pc.onicecandidate = e => { if (e.candidate) cands.push(e.candidate.candidate); };
  pc.createDataChannel('x');
  await pc.setLocalDescription(await pc.createOffer());
  await new Promise(r => setTimeout(r, 4000));
  return {gathering: pc.iceGatheringState, candidates: cands.length};
}"""


async def _reach(browser, counter, datagrams, targets, stun):
    """From a context WITHOUT a proxy -- what a Firefox feature ignoring the proxy would have -- navigate
    to the counting service by each address and gather WebRTC candidates against the STUN targets ->
    (connections counted, datagrams counted, the WebRTC state)."""
    context = await browser.new_context(no_viewport=True)
    page = await context.new_page()
    before, ubefore = counter.connections, datagrams.count
    for target in targets:
        try:
            await page.goto(target, wait_until="domcontentloaded", timeout=10000)
        except Exception:
            pass  # refused, unreachable or reset: the count is the proof
    await asyncio.sleep(0.5)
    await page.set_content("<html><body><p>webrtc</p></body></html>")
    state = await page.evaluate(_WEBRTC, [stun])
    await context.close()
    return counter.connections - before, datagrams.count - ubefore, state


async def test_the_real_browser_runs_in_its_own_network_namespace(live):
    """Every Firefox process of the live browser sits in a network namespace that is not this process's,
    and a fetch through it comes back with Firefox reporting the in-sandbox boundary as the address it
    connected to (the fetch's own check)."""
    await live._ensure()
    assert live._sandbox is not None
    pids = _our_firefox_pids()
    assert pids, "no Firefox process found under this test"
    ours = os.readlink("/proc/self/ns/net")
    assert all(os.readlink(f"/proc/{pid}/ns/net") != ours for pid in pids), pids
    counter = _Counter()
    await counter.start()
    try:
        res = await live.fetch(f"http://127.0.0.1:{counter.port}/", allowed=frozenset({("http", "127.0.0.1", counter.port)}))
    finally:
        await counter.close()
    assert res.get("status") == 200 and "hello from the host" in res["text"], res  # the one way through: the boundary dialled it
    assert counter.connections >= 1


async def test_a_context_without_a_proxy_reaches_nothing_from_inside_the_sandbox(live, monkeypatch):
    """The structural proof: with no proxy at all, and WebRTC's pref turned back on, a page inside the
    sandbox reaches neither a service on this host's loopback nor one on its LAN address (0 connections),
    and its ICE gathering sends no datagram to a host UDP socket (0 datagrams, no candidates). The same
    page in a browser launched outside the sandbox is the positive control: connections and datagrams
    counted."""
    prefs = dict(browser_mod._PROXY_PREFS, **{"media.peerconnection.enabled": True})
    monkeypatch.setattr(browser_mod, "_PROXY_PREFS", prefs)
    counter, loop = _Counter(), asyncio.get_running_loop()
    await counter.start()
    transport, datagrams = await loop.create_datagram_endpoint(_Datagrams, local_addr=("0.0.0.0", 0))
    udp_port = transport.get_extra_info("sockname")[1]
    lan = _lan_address()
    targets = [f"http://127.0.0.1:{counter.port}/"] + ([f"http://{lan}:{counter.port}/"] if lan else [])
    stun = [f"stun:127.0.0.1:{udp_port}"] + ([f"stun:{lan}:{udp_port}"] if lan else [])
    try:
        await live._ensure()
        inside = await _reach(live._browser, counter, datagrams, targets, stun)
        assert inside[0] == 0 and inside[1] == 0 and inside[2]["candidates"] == 0, inside
        from camoufox.async_api import AsyncCamoufox
        control = AsyncCamoufox(headless=True, humanize=True, firefox_user_prefs=prefs)
        outside_browser = await control.__aenter__()
        try:
            outside = await _reach(outside_browser, counter, datagrams, targets, stun)
        finally:
            await control.__aexit__(None, None, None)
        assert outside[0] > 0, outside  # the positive control reached the service
        assert outside[1] > 0 or outside[2]["candidates"] > 0, outside  # and gathered candidates / sent STUN
    finally:
        transport.close()
        await counter.close()


async def test_soak_fresh_launches_each_followed_by_a_fetch(live):
    """Opt-in soak (DREAM_TEST_BROWSER_SOAK=<cycles>, with the live browser enabled): that many fresh
    sandboxed launches, each followed at once by a granted loopback fetch and then a warm one; every one
    must come back 200 through the in-sandbox boundary, and no profile folder may be left behind."""
    import glob
    import time
    cycles = int(os.environ.get("DREAM_TEST_BROWSER_SOAK", "0"))
    if not cycles:
        pytest.skip("set DREAM_TEST_BROWSER_SOAK=<cycles> for the launch-and-fetch soak")
    import gc
    profiles = "/tmp/playwright_firefoxdev_profile-*"
    folders = lambda: [p for p in netns._tmp().iterdir() if p.name.startswith("dream-browser-")]  # noqa: E731
    stale_before, folders_before = len(glob.glob(profiles)), len(folders())
    counter = _Counter()
    await counter.start()
    url, typed = f"http://127.0.0.1:{counter.port}/", frozenset({("http", "127.0.0.1", counter.port)})
    cold, warm, failures, fds = [], [], [], []
    try:
        for i in range(cycles):
            browser = Browser()
            t = time.monotonic()
            res = await browser.fetch(url, allowed=typed)  # the launch and the first fetch
            cold.append(time.monotonic() - t)
            if res.get("status") != 200:
                failures.append((i, "cold", res.get("error")))
            t = time.monotonic()
            res = await browser.fetch(url, allowed=typed)
            warm.append(time.monotonic() - t)
            if res.get("status") != 200:
                failures.append((i, "warm", res.get("error")))
            await browser.aclose()
            gc.collect()
            fds.append(len(os.listdir("/proc/self/fd")))
    finally:
        await counter.close()
    await asyncio.sleep(0.5)
    stale_after = len(glob.glob(profiles))
    summary = (f"soak {cycles} cycles: launch+fetch median {sorted(cold)[len(cold) // 2]:.2f}s max {max(cold):.2f}s; "
               f"warm fetch median {sorted(warm)[len(warm) // 2]:.3f}s max {max(warm):.3f}s; "
               f"failures {len(failures)}; profile folders under /tmp {stale_before} -> {stale_after}; "
               f"descriptors after cycle 1 {fds[0]}, after the last {fds[-1]}; "
               f"sandbox folders {folders_before} -> {len(folders())}; Firefox processes of this test left {len(_our_firefox_pids())}")
    print(summary)
    assert not failures, (summary, failures)
    assert stale_after <= stale_before, summary
    assert len(folders()) <= folders_before and not _our_firefox_pids(), summary  # nothing of the sandboxes left
    assert fds[-1] <= fds[0], summary  # a steady descriptor count: each cycle gives everything back
