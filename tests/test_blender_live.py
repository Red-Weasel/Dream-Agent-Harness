"""DREAM-109: live Blender -- Blender, its MCP add-on server and the stdio bridge in ONE sandbox,
drawing into its own nested X server, never the owner's display.

No test opens a window anywhere. Every launch goes through the real launcher, the real
sandbox and the real nested-server bubblewrap; only the nested server's program is swapped
for tests/live_blender_fake_x.py, which listens where Xephyr would and draws nothing, and
Blender runs in background mode. An autouse guard checks that no test changes the host's
X11 sockets or lock files, or starts Xephyr. What only a live run can show is listed in
docs/media.md.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

import dream
from dream import config, extensions, plugins
from dream.core import execution as ex
from dream.core import policy
from dream.mcp_client import McpClients, load_config
from dream.media import blender_live

READ_TOOLS = {"get_scene_info", "get_object_info", "get_viewport_screenshot", "describe_node_type",
              "bpy_api_lookup", "get_window"}
CODE_TOOLS = {"execute_blender_code", "export_scene"}
FAKE_LAUNCHER = Path(__file__).with_name("live_blender_fake_launcher.py")


def _host_x11() -> tuple[list, list, set]:
    """What a launch must never change: the host's X socket folder, its lock files, and the
    socket names in this network namespace."""
    folder = sorted((e.name, e.inode(), e.stat(follow_symlinks=False).st_mtime_ns)
                    for e in os.scandir("/tmp/.X11-unix"))
    locks = sorted((p.name, p.stat().st_ino) for p in Path("/tmp").glob(".X*-lock"))
    names = {n for n in blender_live._unix_names() if "X11-unix" in n}
    return folder, locks, names


def _xephyr_pids() -> set[int]:
    found = set()
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                if os.readlink(entry / "exe") == str(blender_live.XEPHYR):
                    found.add(int(entry.name))
            except OSError:
                continue
    return found


@pytest.fixture(autouse=True)
def _never_touch_the_host_display():
    before, xephyr = _host_x11(), _xephyr_pids()
    yield
    for _ in range(50):  # a closing session finishes its cleanup
        if _host_x11() == before:
            break
        time.sleep(0.1)
    assert _host_x11() == before, "a launch changed the host's X11 sockets or lock files"
    assert _xephyr_pids() <= xephyr, "a test started a real Xephyr"


def _live_config(workspace: Path, *, env: dict | None = None) -> dict:
    entries, warnings = blender_live.managed_servers(workspace)
    assert entries and not warnings, warnings
    cfg = dict(entries[0])
    assert cfg["args"][:3] == ["-I", "-m", "dream.media.blender_live"]
    # tests: the real launcher with the fake nested server, and Blender in background mode
    cfg["args"] = ["-I", str(FAKE_LAUNCHER), *cfg["args"][3:], "--background"]
    if env:
        cfg["env"] = env
    return cfg


def _needs_live(workspace: Path) -> None:
    reason = blender_live.unavailable(workspace)
    if reason:
        pytest.skip(f"live Blender unavailable here: {reason}")


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "project"
    ws.mkdir()
    return ws


async def _text(tool, args=None) -> tuple[str, bool]:
    result = await tool.handler(args or {})
    return "".join(b.get("text", "") for b in result["content"]), bool(result["is_error"])


def _processes(marker: str) -> list[int]:
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if marker in (entry / "cmdline").read_bytes().decode("utf-8", "replace"):
                found.append(int(entry.name))
        except OSError:
            continue
    return found


def _first_arg_is(pid: int, program: bytes) -> bool:
    try:
        return (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")[0] == program
    except OSError:
        return False


def _bridges() -> list[int]:
    return [p for p in _processes(str(blender_live.RUNTIME / "live.py"))
            if _first_arg_is(p, ex._SUPERVISOR_PYTHON.encode())]


def _blenders() -> list[int]:
    return [p for p in _processes(str(blender_live.RUNTIME / "startup.py")) if _first_arg_is(p, b"/usr/bin/blender")]


def _nested_servers() -> list[int]:
    return [p for p in _processes("/tmp/fake_x.py") if _first_arg_is(p, b"/usr/bin/python3")]


def _private_folders() -> set[str]:
    return {p.name for p in Path(os.environ.get("TMPDIR", "/tmp")).glob("dream-blender-*")}


# --- the sandbox profiles ----------------------------------------------------------------


def _private_display(tmp_path: Path) -> tuple[Path, Path]:
    private = tmp_path / "private"
    (private / "x11").mkdir(parents=True)
    blender_live.write_xauth(private / "xauth")
    return private / "x11", private / "xauth"


def _profile(workspace: Path, **overrides) -> list[str]:
    x11_dir, xauth = _private_display(workspace.parent)
    read_root = workspace.parent / "runtime"
    read_root.mkdir(exist_ok=True)
    scope = ex.ExecutionScope(workspace, read_roots=(read_root,))
    kwargs = dict(seccomp_fd=90, mount_fds={workspace: 91, read_root: 92}, x11_dir=x11_dir, xauth=xauth)
    kwargs.update(overrides)
    return ex.blender_live_argv(scope, "/usr/bin/bwrap", ["inner"], **kwargs)


def _binds(argv: list[str]) -> set[tuple[str, str, str]]:
    kinds = {"--bind", "--ro-bind", "--dev-bind", "--bind-fd", "--ro-bind-fd"}
    return {(argv[i], argv[i + 1], argv[i + 2]) for i, a in enumerate(argv) if a in kinds}


def test_the_profile_is_run_bash_boundary_plus_the_private_display_folder(workspace):
    argv = _profile(workspace)
    x11_dir, xauth = workspace.parent / "private" / "x11", workspace.parent / "private" / "xauth"
    for flag in ("--unshare-user", "--unshare-pid", "--unshare-net", "--unshare-ipc", "--unshare-uts",
                 "--disable-userns", "--die-with-parent", "--new-session", "--clearenv"):
        assert flag in argv
    assert argv[argv.index("--cap-drop") + 1] == "ALL" and argv[argv.index("--seccomp") + 1] == "90"
    assert "--share-net" not in argv
    # compared with run_bash's own profile for the same scope, the ONLY new mounts are the
    # session's private socket folder and its cookie, both read-only
    scope = ex.ExecutionScope(workspace, read_roots=(workspace.parent / "runtime",))
    run_bash = ex._bwrap_argv(scope, "/usr/bin/bwrap", ["inner"], seccomp_fd=90,
                              mount_fds={workspace: 91, workspace.parent / "runtime": 92})
    assert _binds(argv) - _binds(run_bash) == {("--ro-bind", str(x11_dir), "/tmp/.X11-unix"),
                                               ("--ro-bind", str(xauth), "/tmp/xauth")}
    assert _binds(run_bash) <= _binds(argv)
    # nothing of the host's display: not its socket folder, not /run (X authority, D-Bus), no GPU
    sources = [source for _, source, _ in _binds(argv)]
    assert not any(s == "/tmp/.X11-unix" or s.startswith(("/tmp/.X11-unix/", "/run", "/dev", "/sys"))
                   for s in sources)
    assert "--dev-bind" not in argv and not any(a.startswith(("/dev/dri", "/sys")) for a in argv)
    assert [a for a in argv if a in {"--bind", "--bind-fd"}] == ["--bind-fd"]  # only the workspace writes
    env = {argv[i + 1]: argv[i + 2] for i, a in enumerate(argv) if a == "--setenv"}
    assert env == {"PATH": "/usr/bin:/bin", "HOME": "/tmp/home", "TMPDIR": "/tmp", "LANG": "C.UTF-8",
                   "XAUTHORITY": "/tmp/xauth", "PWD": str(workspace)}  # DISPLAY: the nested one, set per start
    assert argv.index("--remount-ro") < argv.index("--chdir") < argv.index("--")
    assert argv[argv.index("--") + 1:] == ["inner"]


@pytest.mark.parametrize("change", ["host_folder", "inside_host_folder", "symlinked_folder", "cookie_folder",
                                    "symlinked_cookie"])
def test_the_profile_refuses_the_host_display_folder_and_odd_cookies(workspace, tmp_path, change):
    x11_dir, xauth = _private_display(tmp_path / "other")
    overrides = {}
    if change == "host_folder":
        overrides["x11_dir"] = Path("/tmp/.X11-unix")
    elif change == "inside_host_folder":
        overrides["x11_dir"] = Path("/tmp/.X11-unix/../.X11-unix")
    elif change == "symlinked_folder":
        (tmp_path / "link").symlink_to(x11_dir)
        overrides["x11_dir"] = tmp_path / "link"
    elif change == "cookie_folder":
        overrides["xauth"] = x11_dir
    else:
        (tmp_path / "cookie-link").symlink_to(xauth)
        overrides["xauth"] = tmp_path / "cookie-link"
    with pytest.raises(ex.ExecutionRefused):
        _profile(workspace, **overrides)


@pytest.mark.parametrize("kind", ["network", "red_team", "home"])
def test_the_profile_refuses_scopes_it_does_not_confine(tmp_path, kind):
    x11_dir, xauth = _private_display(tmp_path)
    ws = tmp_path / "w"
    (ws / "target").mkdir(parents=True)
    if kind == "network":
        scope = ex.ExecutionScope(ws, network=True)
    elif kind == "red_team":
        scope = ex.ExecutionScope(ws, red_team=True, target_roots=(ws / "target",), expires_at=time.monotonic() + 60)
    else:
        scope = ex.ExecutionScope(Path.home())
    with pytest.raises(ex.ExecutionRefused):
        ex.blender_live_argv(scope, "/usr/bin/bwrap", ["x"], seccomp_fd=1, mount_fds={}, x11_dir=x11_dir,
                             xauth=xauth)


@pytest.mark.skipif(not Path("/tmp/.X11-unix").is_dir(), reason="no /tmp/.X11-unix on this host")
def test_the_display_folder_is_never_an_execution_root():
    """Gate note (DREAM-109 PASS): a workspace at /tmp/.X11-unix itself would be bound read-write after, and
    over, the private display mount. validate() refuses it, like the other host trees."""
    with pytest.raises(ex.ExecutionRefused):
        ex.ExecutionScope(Path("/tmp/.X11-unix")).validate()


def test_run_bash_keeps_its_own_profile(workspace):
    argv = ex._bwrap_argv(ex.ExecutionScope(workspace), "/usr/bin/bwrap", ["x"], seccomp_fd=3,
                          mount_fds={workspace: 4})
    assert "--clearenv" not in argv and "DISPLAY" not in argv and "XAUTHORITY" not in argv
    assert not any("X11-unix" in a or a.startswith("/dev/dri") or a.startswith("/sys") for a in argv)


def _nested(tmp_path: Path, **overrides) -> list[str]:
    x11_dir, xauth = _private_display(tmp_path)
    kwargs = dict(x11_dir=x11_dir, xauth=xauth, host_display=":0", number=93, screen=(1600, 1000),
                  title="Dream live Blender: project [abcd]")
    kwargs.update(overrides)
    return ex.nested_display_argv("/usr/bin/bwrap", "/usr/bin/Xephyr", **kwargs)


def test_the_nested_server_runs_in_its_own_sandbox_on_the_private_folder(tmp_path):
    argv = _nested(tmp_path)
    x11_dir, xauth = tmp_path / "private" / "x11", tmp_path / "private" / "xauth"
    cut = argv.index("--")
    options, command = argv[:cut], argv[cut + 1:]
    # its /tmp is private and its /tmp/.X11-unix IS the session's folder: it cannot create or
    # remove anything in the host's /tmp/.X11-unix or its lock files, whatever its number
    tmpfs = options.index("--tmpfs")
    assert options[tmpfs + 1] == "/tmp"
    folder = options.index("--bind")
    assert options[folder:folder + 3] == ["--bind", str(x11_dir), "/tmp/.X11-unix"] and folder > tmpfs
    assert ["--ro-bind", str(xauth), "/tmp/xauth"] == options[folder + 3:folder + 6]
    assert [a for a in options if a in {"--bind", "--bind-fd", "--dev-bind"}] == ["--bind"]
    host_paths = [options[i + 1] for i, a in enumerate(options) if a in {"--bind", "--ro-bind", "--dev-bind"}]
    assert not any(p == "/tmp" or p.startswith("/tmp/.X") or p.startswith("/run") or p.startswith("/home")
                   for p in host_paths)
    for flag in ("--unshare-user", "--unshare-pid", "--die-with-parent", "--new-session", "--clearenv"):
        assert flag in options
    # it keeps the host's network namespace only to reach the host display's abstract socket
    assert "--unshare-net" not in options
    env = {options[i + 1]: options[i + 2] for i, a in enumerate(options) if a == "--setenv"}
    assert env == {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "DISPLAY": ":0"}
    assert command == ["/usr/bin/Xephyr", ":93", "-auth", "/tmp/xauth", "-nolisten", "tcp", "-nolisten", "local",
                       "-extension", "MIT-SHM", "-noxv", "-iglx",
                       "-screen", "1600x1000", "-no-host-grab", "-title", "Dream live Blender: project [abcd]"]


@pytest.mark.parametrize("override", [{"x11_dir": Path("/tmp/.X11-unix")}, {"host_display": "remote:0"},
                                      {"number": 0}, {"screen": (0, 1000)}])
def test_the_nested_server_profile_refuses_the_host_folder_and_bad_values(tmp_path, override):
    with pytest.raises(ex.ExecutionRefused):
        _nested(tmp_path, **override)


def _fake_host(tmp_path: Path, *, sockets=(), locks=(), names=()) -> dict:
    x11, tmp = tmp_path / "x11-unix", tmp_path / "tmp"
    x11.mkdir()
    tmp.mkdir()
    for n in sockets:
        (x11 / f"X{n}").touch()
    for n in locks:
        (tmp / f".X{n}-lock").touch()
    table = tmp_path / "unix"
    rows = [f"0000: 00000002 00000000 00010000 0001 01 {i} {name}" for i, name in enumerate(names)]
    table.write_text("Num RefCount Protocol Flags Type St Inode Path\n" + "\n".join(rows) + "\n")
    return {"x11_dir": x11, "tmp": tmp, "net_unix": table}


def test_display_numbers_are_explicit_and_skip_anything_in_use(tmp_path):
    host = _fake_host(tmp_path, sockets=[90], locks=[91], names=["@/tmp/.X11-unix/X92", "/tmp/.X11-unix/X93",
                                                                  "@/tmp/.X11-unix/X0"])
    (host["x11_dir"] / "X94").symlink_to("/nonexistent")
    assert blender_live.free_display(**host) == 95
    assert blender_live.free_display(start=96, **host) == 96
    assert blender_live.DISPLAY_BASE == 90 and blender_live.free_display() >= 90


def test_only_a_local_display_with_an_abstract_socket_counts(tmp_path):
    table = _fake_host(tmp_path, names=["@/tmp/.X11-unix/X0", "/tmp/.X11-unix/X1"])["net_unix"]
    assert blender_live.x11_display(":0", net_unix=table) == (":0", 0)
    assert blender_live.x11_display("unix:0.0", net_unix=table) == (":0.0", 0)
    assert blender_live.x11_display(":1", net_unix=table) is None  # a path socket only: unreachable from the nested server
    for value in ("localhost:10.0", "", ":x", ":0 ; rm"):
        assert blender_live.x11_display(value, net_unix=table) is None


def test_the_session_cookie_file(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    cookie = blender_live.write_xauth(first)
    blender_live.write_xauth(second)
    data = first.read_bytes()
    assert data == (b"\xff\xff" + b"\x00\x00" + b"\x00\x00" + b"\x00\x12MIT-MAGIC-COOKIE-1" + b"\x00\x10" + cookie)
    assert len(cookie) == 16 and data != second.read_bytes()
    assert stat.S_IMODE(first.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        blender_live.write_xauth(first)


def test_the_window_is_found_by_its_exact_title():
    tree = ('  0x503 (the root window) (has no name)\n'
            '     0x6000002 "Dream live Blender: project [abcd]": ("Xephyr" "Xephyr")  1600x1000+0+0  +0+0\n'
            '     0x6100002 "Dream live Blender: project [abcd] copy": ()  10x10+0+0  +0+0\n')
    assert blender_live.window_in_tree(tree, "Dream live Blender: project [abcd]") == "0x6000002"
    with pytest.raises(RuntimeError, match="not found"):
        blender_live.window_in_tree(tree, "Dream live Blender: project [ffff]")


def test_the_launcher_asks_the_owner_display_only_for_its_own_window(tmp_path, monkeypatch):
    private = tmp_path / "private"
    (private / "x11").mkdir(parents=True)
    nested = blender_live.NestedDisplay("/usr/bin/bwrap", private, ":0", "Dream live Blender: p [0001]")
    with pytest.raises(RuntimeError, match="not running"):
        nested.window()
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout='  0x42 "Dream live Blender: p [0001]": ()  1600x1000+0+0  +0+0\n')

    nested.process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(blender_live.subprocess, "run", fake_run)
    assert nested.window() == "0x42"
    assert calls == [["/usr/bin/xwininfo", "-root", "-tree", "-display", ":0"]]  # a read-only query


# --- availability, the managed entry, the launcher -------------------------------------


def test_the_managed_entry_is_the_sandboxed_launcher_for_this_workspace(workspace):
    _needs_live(workspace)
    entries, warnings = blender_live.managed_servers(workspace)
    assert not warnings and len(entries) == 1
    entry = entries[0]
    assert entry["name"] == "blender" == blender_live.SERVER_NAME
    assert entry["command"] == sys.executable
    assert entry["args"][:3] == ["-I", "-m", "dream.media.blender_live"]
    assert entry["args"][entry["args"].index("--workspace") + 1] == str(workspace.resolve())
    assert "env" not in entry and "inherit_env" not in entry
    path = workspace.parent / "mcp.json"
    path.write_text(json.dumps({"servers": [{k: v for k, v in entry.items() if not k.startswith("_")}]}))
    loaded, load_warnings = load_config(path)
    assert not loaded and "reserved" in load_warnings[0]  # the name is Dream's own


@pytest.mark.parametrize("broken,needle", [
    ("blender", "no Blender at"), ("xephyr", "no Xephyr at"), ("bwrap", "bubblewrap"),
    ("display", "X11 display"), ("home", "workspace refused"),
])
def test_an_unusable_setup_gives_a_reason_and_no_server(workspace, monkeypatch, broken, needle):
    if broken not in ("blender", "xephyr"):
        _needs_live(workspace)
    if broken == "blender":
        monkeypatch.setattr(blender_live, "BLENDER", workspace / "no-blender")
    elif broken == "xephyr":
        monkeypatch.setattr(blender_live, "XEPHYR", workspace / "no-xephyr")
    elif broken == "bwrap":
        monkeypatch.setattr(blender_live, "_bubblewrap_executable", lambda: None)
    elif broken == "display":
        monkeypatch.setenv("DISPLAY", "localhost:10.0")
    else:
        workspace = Path.home()
    entries, warnings = blender_live.managed_servers(workspace)
    assert entries == [] and len(warnings) == 1
    assert warnings[0].startswith("live Blender is off: ") and needle in warnings[0]


def test_the_launcher_refuses_clearly_and_never_falls_back_to_the_host(workspace, monkeypatch, capsys):
    monkeypatch.setattr(blender_live, "BLENDER", workspace / "no-blender")
    started = []
    monkeypatch.setattr(blender_live.subprocess, "Popen", lambda *a, **k: started.append(a))
    assert blender_live.main(["--workspace", str(workspace), "--display", ":0"]) == 1
    err = capsys.readouterr().err
    assert "live Blender did not start: no Blender at" in err and "Nothing ran outside the sandbox" in err
    assert started == []


# --- the real sandbox: the bridge, the nested display (fake server), Blender (background) --


async def test_the_bridge_lists_the_blender_tools_inside_the_sandbox(workspace):
    _needs_live(workspace)
    clients = McpClients()
    try:
        tools, warnings = await clients.start([_live_config(workspace)])
        assert not warnings
        names = {t.name for t in tools}
        assert names == {f"blender__{t}" for t in READ_TOOLS | CODE_TOOLS}
        for tool in tools:
            assert "user_prompt" not in json.dumps(tool.input_schema)
            assert "external MCP" in tool.description
        text, error = await _text({t.name: t for t in tools}["blender__get_scene_info"])
        assert not error and json.loads(text.split("\n", 1)[1])["name"] == "Scene"  # after the opened note
    finally:
        await clients.stop()


PROBE = r'''
import glob, json, os, socket, subprocess, urllib.request
out = {}
def attempt(name, fn):
    try:
        out[name] = ["ok", repr(fn())[:200]]
    except BaseException as e:
        out[name] = ["refused", type(e).__name__ + ": " + str(e)[:200]]
def connect(address):
    s = socket.socket(socket.AF_UNIX)
    try:
        s.connect(address)
    finally:
        s.close()
def x_client(argv):
    r = subprocess.run(argv, capture_output=True, text=True, timeout=20, env={**os.environ, "DISPLAY": ":0"})
    if r.returncode:
        raise RuntimeError(f"exit {r.returncode}: {(r.stdout + r.stderr).strip()[:120]}")
    return r.stdout[:80]
home = __HOME__
attempt("read_ssh", lambda: os.listdir(os.path.join(home, ".ssh")))
attempt("read_bashrc", lambda: open(os.path.join(home, ".bashrc")).read())
attempt("read_dream_data", lambda: os.listdir(__DREAM_DATA__))
attempt("read_outside_file", lambda: open(__SECRET__).read())
attempt("read_through_host_symlink", lambda: open("host-link").read())
attempt("write_through_host_symlink", lambda: open("host-link", "w").write("x"))
def model_symlink():
    os.symlink(__SECRET__, "model-link")
    return open("model-link").read()
attempt("read_through_model_symlink", model_symlink)
attempt("write_home", lambda: open(os.path.join(home, "dream-probe.txt"), "w").write("x"))
attempt("write_tmp", lambda: open("/tmp/" + __MARKER__, "w").write("x"))
attempt("write_runtime", lambda: open(__RUNTIME__ + "/probe.txt", "w").write("x"))
attempt("write_git", lambda: open(".git/probe", "w").write("x"))
attempt("write_display_folder", lambda: open("/tmp/.X11-unix/probe", "w").write("x"))
attempt("connect_internet", lambda: socket.create_connection(("1.1.1.1", 443), timeout=3))
attempt("resolve_name", lambda: socket.getaddrinfo("example.com", 443))
attempt("fetch_asset_api", lambda: urllib.request.urlopen("https://api.polyhaven.com/assets", timeout=5))
attempt("netlink_socket", lambda: socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 0))
attempt("packet_socket", lambda: socket.socket(socket.AF_PACKET, socket.SOCK_RAW, 0))
attempt("host_dbus", lambda: connect("/run/user/" + __UID__ + "/bus"))
attempt("host_display_path", lambda: connect("/tmp/.X11-unix/X0"))
attempt("host_display_abstract", lambda: connect("\0/tmp/.X11-unix/X0"))
attempt("host_display_xdpyinfo", lambda: x_client(["xdpyinfo"]))
attempt("host_display_xdotool", lambda: x_client(["xdotool", "search", "--name", "."]))
attempt("host_display_xwininfo", lambda: x_client(["xwininfo", "-root", "-tree"]))
attempt("host_x_authority", lambda: open("/run/user/" + __UID__ + "/gdm/Xauthority", "rb").read())
attempt("nested_display", lambda: connect("/tmp/.X11-unix/X" + os.environ["DISPLAY"][1:]))
attempt("write_workspace", lambda: open("inside.txt", "w").write("written in Blender"))
out["display"] = os.environ.get("DISPLAY")
out["xauthority"] = os.environ.get("XAUTHORITY")
out["cookie"] = open("/tmp/xauth", "rb").read().hex()
out["display_folder"] = sorted(os.listdir("/tmp/.X11-unix"))
out["nested_record"] = [json.load(open(p)) for p in glob.glob("/tmp/.X11-unix/fake-X*.json")]
out["cwd"] = os.getcwd()
out["pids"] = sorted(int(p) for p in os.listdir("/proc") if p.isdigit())
out["env"] = sorted(os.environ)
out["dri"] = os.path.exists("/dev/dri")
print("PROBE" + json.dumps(out))
'''


async def test_blender_python_reaches_the_workspace_and_nothing_else(workspace, tmp_path):
    _needs_live(workspace)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("owner secret")
    (workspace / "host-link").symlink_to(secret)
    (workspace / ".git").mkdir()
    marker = f"dream-probe-{uuid.uuid4().hex}"
    code = PROBE
    for token, value in (("__HOME__", str(Path.home())), ("__SECRET__", str(secret)), ("__MARKER__", marker),
                         ("__DREAM_DATA__", str(Path(dream.__file__).resolve().parents[1] / "data")),
                         ("__RUNTIME__", str(blender_live.RUNTIME)), ("__UID__", str(os.getuid()))):
        code = code.replace(token, repr(value))
    clients = McpClients()
    try:
        tools, _ = await clients.start([_live_config(workspace, env={"DREAM_TEST_CANARY": "must-not-reach"})])
        text, error = await _text({t.name: t for t in tools}["blender__execute_blender_code"], {"code": code})
    finally:
        await clients.stop()
    assert not error, text
    found = json.loads(text.split("PROBE", 1)[1].strip())
    for name in ("read_ssh", "read_bashrc", "read_dream_data", "read_outside_file", "read_through_host_symlink",
                 "write_through_host_symlink", "read_through_model_symlink", "write_home", "write_runtime",
                 "write_git", "write_display_folder", "connect_internet", "resolve_name", "fetch_asset_api",
                 "netlink_socket", "packet_socket", "host_dbus", "host_display_path", "host_display_abstract",
                 "host_display_xdpyinfo", "host_display_xdotool", "host_display_xwininfo", "host_x_authority"):
        assert found[name][0] == "refused", (name, found[name])
    assert found["write_tmp"][0] == "ok"            # the sandbox's own /tmp ...
    assert not (Path("/tmp") / marker).exists()      # ... is not the host's
    assert found["write_workspace"][0] == "ok"
    assert (workspace / "inside.txt").read_text() == "written in Blender"
    assert secret.read_text() == "owner secret"
    assert not (Path.home() / "dream-probe.txt").exists()
    assert found["cwd"] == str(workspace.resolve())
    assert os.getpid() not in found["pids"] and max(found["pids"]) < 1000
    assert "DREAM_TEST_CANARY" not in found["env"] and "DBUS_SESSION_BUS_ADDRESS" not in found["env"]
    # the display Blender has is the nested one, with the session's cookie, and nothing else
    number = int(found["display"][1:])
    assert number >= blender_live.DISPLAY_BASE and found["xauthority"] == "/tmp/xauth"
    assert found["nested_display"][0] == "ok"
    assert found["display_folder"] == [f"X{number}", f"fake-X{number}.json"]
    assert found["dri"] is False
    (record,) = found["nested_record"]
    assert record["socket_folder"] == [f"X{number}"]      # the nested server saw only the private folder
    assert record["cookie"] == found["cookie"]            # and the same session cookie
    assert record["host_abstract"] is True and record["host_path_visible"] is False
    assert record["display"] == ":0" and record["argv"][:11] == [
        f":{number}", "-auth", "/tmp/xauth", "-nolisten", "tcp", "-nolisten", "local", "-extension", "MIT-SHM",
        "-noxv", "-iglx"]


async def test_saves_and_renders_land_in_the_workspace(workspace):
    _needs_live(workspace)
    code = (
        "import bpy\n"
        "scene = bpy.context.scene\n"
        "print('RENDER', scene.render.filepath)\n"
        "bpy.ops.wm.save_as_mainfile(filepath='scene.blend')\n"
        "scene.render.engine = 'CYCLES'\n"
        "scene.cycles.device = 'CPU'\n"
        "scene.cycles.samples = 1\n"
        "scene.cycles.use_denoising = False  # Debian's Blender is built without OpenImageDenoise\n"
        "scene.render.resolution_x = scene.render.resolution_y = 8\n"
        "scene.render.resolution_percentage = 100\n"
        "bpy.ops.render.render(write_still=True)\n"
    )
    clients = McpClients()
    try:
        tools, _ = await clients.start([_live_config(workspace)])
        text, error = await _text({t.name: t for t in tools}["blender__execute_blender_code"], {"code": code})
    finally:
        await clients.stop()
    assert not error, text
    assert f"RENDER {workspace.resolve()}/renders/render" in text
    assert (workspace / "scene.blend").is_file()
    assert [p.name for p in (workspace / "renders").iterdir()] == ["render.png"]


async def test_a_session_starts_only_the_bridge_and_the_first_call_opens_blender_and_its_display(workspace):
    _needs_live(workspace)
    folders = _private_folders()
    clients = McpClients()
    try:
        tools, _ = await clients.start([_live_config(workspace)])  # what a session start does
        await asyncio.sleep(1.0)
        assert len(_bridges()) == 1 and _blenders() == [] and _nested_servers() == []  # no display, no Blender
        (private,) = _private_folders() - folders
        assert os.listdir(Path(os.environ.get("TMPDIR", "/tmp")) / private / "x11") == []
        by = {t.name: t for t in tools}
        first, _ = await _text(by["blender__get_scene_info"])  # waits until Blender answers
        assert first.startswith("[Opened live Blender without a window (background mode).")
        assert len(_blenders()) == 1 and len(_nested_servers()) == 1
        second, _ = await _text(by["blender__get_scene_info"])
        assert not second.startswith("[") and len(_blenders()) == 1 and len(_nested_servers()) == 1
    finally:
        await clients.stop()


async def test_a_launch_leaves_the_host_x11_sockets_alone(workspace):
    _needs_live(workspace)
    before = _host_x11()
    clients = McpClients()
    try:
        tools, _ = await clients.start([_live_config(workspace)])
        text, _ = await _text({t.name: t for t in tools}["blender__execute_blender_code"],
                              {"code": "import os; print('DISPLAY', os.environ['DISPLAY'])"})
        number = int(text.split("DISPLAY :", 1)[1].split()[0])
        folder, locks, names = _host_x11()
        assert (folder, locks) == before[:2]   # nothing created or removed in /tmp/.X11-unix or lock files
        # this network namespace lists the running nested server's own socket, by the path it
        # bound inside its private mount namespace, and nothing else new
        assert names - before[2] == {f"/tmp/.X11-unix/X{number}"}
        assert not Path(f"/tmp/.X11-unix/X{number}").exists()
    finally:
        await clients.stop()
    # the autouse guard checks that everything is back as it was


async def test_one_blender_per_session_reused_then_started_again_after_it_closes(workspace):
    _needs_live(workspace)
    clients = McpClients()
    try:
        tools, _ = await clients.start([_live_config(workspace)])
        run = {t.name: t for t in tools}["blender__execute_blender_code"]
        pid = "import os; print('PID', os.getpid())"
        first, _ = await _text(run, {"code": "import bpy; bpy.ops.mesh.primitive_monkey_add(); " + pid})
        again, _ = await _text(run, {"code": pid})
        assert first.split("PID")[1] == again.split("PID")[1]   # the same Blender, reused
        text, _ = await _text(run, {"code": "import os; os._exit(0)"})  # Blender closes (the owner quits)
        assert text.startswith("Error executing code")
        for _ in range(50):  # ... and its nested display closes with it
            if not _nested_servers():
                break
            await asyncio.sleep(0.1)
        assert _nested_servers() == [] and _blenders() == []
        text, error = await _text(run, {"code": "import bpy; print('OBJECTS', sorted(o.name for o in bpy.data.objects))"})
        assert not error and "Suzanne" not in text and "OBJECTS" in text  # a new Blender, started on demand
        assert text.startswith("[Blender had closed, so live Blender opened again")
        assert len(_nested_servers()) == 1
    finally:
        await clients.stop()


async def test_closing_the_session_closes_blender_the_display_and_the_folder(workspace):
    _needs_live(workspace)
    marker = str(workspace.resolve())
    folders = _private_folders()
    clients = McpClients()
    tools, _ = await clients.start([_live_config(workspace)])
    await _text({t.name: t for t in tools}["blender__get_scene_info"])
    assert _processes(marker) and _blenders() and _nested_servers() and _private_folders() - folders
    await clients.stop()
    for _ in range(50):
        if not (_processes(marker) or _blenders() or _nested_servers() or _private_folders() - folders):
            break
        await asyncio.sleep(0.1)
    assert _processes(marker) == [] and _blenders() == [] and _nested_servers() == []
    assert _private_folders() - folders == set()


async def test_when_the_bridge_dies_blender_and_the_display_die_with_it(workspace):
    import signal
    _needs_live(workspace)
    folders = _private_folders()
    clients = McpClients()
    try:
        tools, _ = await clients.start([_live_config(workspace)])
        await _text({t.name: t for t in tools}["blender__get_scene_info"])
        bridge = _bridges()
        assert len(bridge) == 1 and _blenders() and _nested_servers()
        os.kill(bridge[0], signal.SIGKILL)
        for _ in range(50):
            if not (_blenders() or _nested_servers() or _private_folders() - folders):
                break
            await asyncio.sleep(0.1)
        assert _blenders() == [] and _nested_servers() == [] and _private_folders() - folders == set()
        text, error = await _text({t.name: t for t in tools}["blender__get_scene_info"])
        assert error and "failed" in text  # a clear error for the model, no silent host fallback
    finally:
        await clients.stop()


async def test_two_sessions_get_two_blenders_and_two_displays(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    _needs_live(first)
    a, b = McpClients(), McpClients()
    try:
        tools_a, _ = await a.start([_live_config(first)])
        tools_b, _ = await b.start([_live_config(second)])
        code = {"code": "import os; open('who.txt', 'w').write(os.getcwd()); "
                        "print('SEEN', os.environ['DISPLAY'], open('/tmp/xauth', 'rb').read().hex())"}
        results = await asyncio.gather(
            _text({t.name: t for t in tools_a}["blender__execute_blender_code"], code),
            _text({t.name: t for t in tools_b}["blender__execute_blender_code"], code))
        assert not any(error for _, error in results)
        assert (first / "who.txt").read_text() == str(first.resolve())
        assert (second / "who.txt").read_text() == str(second.resolve())
        seen = [text.split("SEEN ", 1)[1].split()[:2] for text, _ in results]
        assert seen[0][1] != seen[1][1]  # each session has its own cookie ...
        assert len(_nested_servers()) == 2 and len(_blenders()) == 2  # ... its own nested server and Blender
    finally:
        await a.stop()
        await b.stop()


async def test_window_and_viewport_tools_fail_cleanly_without_a_display(workspace):
    _needs_live(workspace)
    clients = McpClients()
    try:
        tools, _ = await clients.start([_live_config(workspace)])
        by = {t.name: t for t in tools}
        text, _ = await _text(by["blender__get_window"])
        assert text.startswith("Error finding the Blender window") and "background" in text
        text, error = await _text(by["blender__get_viewport_screenshot"])
        assert error and "Screenshot failed" in text
    finally:
        await clients.stop()


async def test_a_failed_launch_is_a_warning_and_nothing_runs_on_the_host(workspace):
    _needs_live(workspace)
    cfg = _live_config(workspace)
    cfg["args"][cfg["args"].index("--display") + 1] = "localhost:10.0"  # not a local display
    folders = _private_folders()
    clients = McpClients()
    try:
        tools, warnings = await clients.start([cfg])
    finally:
        await clients.stop()
    assert tools == [] and warnings and "mcp server 'blender' failed to start" in warnings[0]
    assert _processes(str(workspace.resolve())) == [] and _private_folders() - folders == set()


# --- policy -------------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", ["", f"mcp__{config.MCP_SERVER_NAME}__"])
@pytest.mark.parametrize("mode", policy.MODES)
def test_blender_read_tools_are_free_in_every_mode(tmp_path, prefix, mode):
    for tool in READ_TOOLS:
        assert policy.decide(f"{prefix}blender__{tool}", {}, mode, tmp_path)[0] == "allow"


@pytest.mark.parametrize("prefix", ["", f"mcp__{config.MCP_SERVER_NAME}__"])
def test_blender_code_runs_unasked_in_accept_edits_and_auto_asks_in_ask_denied_in_plan(tmp_path, prefix):
    for tool in CODE_TOOLS:
        name = f"{prefix}blender__{tool}"
        args = {"code": "import os; os.system('rm -rf /')", "filepath": "/etc/x.glb"}
        assert policy.decide(name, args, "auto", tmp_path)[0] == "allow"
        assert policy.decide(name, args, "accept-edits", tmp_path)[0] == "allow"
        assert policy.decide(name, args, "ask", tmp_path)[0] == "ask"
        decision, reason = policy.decide(name, args, "plan", tmp_path)
        assert decision == "deny" and "plan" in reason


def test_only_dreams_blender_server_gets_the_pass(tmp_path):
    for name in ("blender2__execute_blender_code", "Blender__execute_blender_code",
                 "mcp__blender__execute_blender_code", "blender__install_addon", "evil__blender__get_scene_info"):
        assert policy.capability(name) == policy.MUTATING, name
        assert policy.decide(name, {}, "auto", tmp_path)[0] == "ask"
    (tmp_path / "t").mkdir()
    red = ex.ExecutionScope(tmp_path, red_team=True, target_roots=(tmp_path / "t",), expires_at=time.monotonic() + 60)
    decision, _ = policy.decide("blender__execute_blender_code", {}, "auto", tmp_path, execution_scope=red)
    assert decision == "deny"


def test_the_blender_server_name_is_reserved_in_every_mcp_config(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"servers": [
        {"name": "blender", "command": "uvx", "args": ["mcp-for-blender"]},
        {"name": "Blender", "command": "uvx"},
        {"name": "blender-tools", "command": "uvx"},
    ]}))
    servers, warnings = load_config(path)
    assert [s["name"] for s in servers] == ["blender-tools"]
    assert len(warnings) == 2 and all("reserved for Dream's sandboxed live Blender" in w for w in warnings)


# --- the Engine hook ------------------------------------------------------------------------


class _Stop(Exception):
    pass


async def _start_engine_until_backend(tmp_path, monkeypatch, *, disabled=False, live=None, real_mcp=False):
    from dream.core import engine as engines
    from dream.core.engine import Engine

    ws = tmp_path / "session-ws"
    ws.mkdir()
    captured = {}

    async def no_hooks(*args, **kwargs):
        return SimpleNamespace(allowed=True, outcomes=[])

    async def fake_start(self, configs):
        captured["configs"] = [dict(c) for c in configs]
        return [], []

    async def stop_here(self):
        captured["bridges"] = _bridges()
        captured["blenders"] = _blenders()
        captured["nested"] = _nested_servers()
        raise _Stop()

    from dream import hooks
    from dream.tools import context as tool_context
    monkeypatch.setattr(tool_context, "_CTX", tool_context._CTX)  # Engine.start sets it; put it back after
    monkeypatch.setattr(hooks, "run_hooks", no_hooks)
    monkeypatch.setattr(config, "SEMANTIC_MEMORY", False)
    monkeypatch.setattr(config, "MCP_CONFIG_PATH", tmp_path / "no-mcp.json")
    monkeypatch.setattr(engines, "get_browser", lambda: None)
    monkeypatch.setattr(plugins, "load", lambda: ([], []))
    monkeypatch.setattr(plugins, "mcp_servers", lambda: [])
    if not real_mcp:
        monkeypatch.setattr(McpClients, "start", fake_start)
    monkeypatch.setattr(engines.registry, "build", lambda **kw: {
        "tools": [], "names": [], "warnings": [], "server": {}, "custom_names": []})
    monkeypatch.setattr(Engine, "_create_backend", stop_here)
    if live is not None:
        monkeypatch.setattr(blender_live, "managed_servers", live)
    if disabled:
        extensions.set_enabled(extensions.extension_id("mcp", "blender"), False)
    instance = Engine(provider="openai", workspace=ws)

    def no_memory(self, embedder=None, reranker=None):
        self.store, self.working, self.tasks = None, None, None

    monkeypatch.setattr(Engine, "_open_memory", no_memory)
    with pytest.raises(_Stop):
        await instance.start()
    return instance, ws, captured


async def test_the_engine_adds_live_blender_for_its_own_workspace(tmp_path, monkeypatch):
    seen = []

    def live(workspace):
        seen.append(Path(workspace))
        return [{"name": "blender", "command": "launcher", "args": ["--workspace", str(workspace)]}], []

    instance, ws, captured = await _start_engine_until_backend(tmp_path, monkeypatch, live=live)
    assert seen == [ws.resolve()]
    assert [c["name"] for c in captured["configs"]] == ["blender"]
    assert captured["configs"][0]["args"] == ["--workspace", str(ws.resolve())]


async def test_the_owner_toggle_still_switches_live_blender_off(tmp_path, monkeypatch):
    def live(workspace):
        return [{"name": "blender", "command": "launcher", "args": []}], []

    _, _, captured = await _start_engine_until_backend(tmp_path, monkeypatch, disabled=True, live=live)
    assert captured["configs"] == []


async def test_why_live_blender_is_off_reaches_the_boot_warnings(tmp_path, monkeypatch):
    def live(workspace):
        return [], ["live Blender is off: no Blender at /usr/bin/blender"]

    instance, _, captured = await _start_engine_until_backend(tmp_path, monkeypatch, live=live)
    assert captured["configs"] == []
    assert "live Blender is off: no Blender at /usr/bin/blender" in instance.tool_warnings


async def test_a_real_session_start_opens_no_blender_and_no_display(tmp_path, monkeypatch):
    """The Engine's own start, the real managed entry (launched with the fake nested server) and
    the real MCP client: the bridge runs; Blender and its display do not."""
    _needs_live(tmp_path)
    real = blender_live.managed_servers

    def live(workspace):
        entries, warnings = real(workspace)
        return [{**e, "args": ["-I", str(FAKE_LAUNCHER), *e["args"][3:], "--background"]} for e in entries], warnings

    instance, ws, captured = await _start_engine_until_backend(tmp_path, monkeypatch, live=live, real_mcp=True)
    assert len(captured["bridges"]) == 1 and captured["blenders"] == [] and captured["nested"] == []
    for _ in range(50):  # the failed start cleaned up, as a closed session does
        if not _bridges():
            break
        await asyncio.sleep(0.1)
    assert _bridges() == []


# --- computer use on the nested window, up to what needs a real window ------------------


def _runtime_live(monkeypatch):
    import importlib.util
    monkeypatch.setattr(sys, "dont_write_bytecode", True)  # no __pycache__ in the mounted runtime
    spec = importlib.util.spec_from_file_location("dream_live_blender_runtime", blender_live.RUNTIME / "live.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_get_window_is_the_nested_display_window_the_launcher_names(monkeypatch):
    live_module = _runtime_live(monkeypatch)
    asked = []
    control = SimpleNamespace(request=lambda command: asked.append(command) or "0x4c00003")
    live = live_module.LiveBlender("/usr/bin/blender", background=False, control=control, screen=(1600, 1000))
    monkeypatch.setattr(live, "ensure", lambda: ("127.0.0.1", 9876, 1))
    assert live.find_window() == "0x4c00003" and asked == ["window"]
    background = live_module.LiveBlender("/usr/bin/blender", background=True, control=control, screen=(1600, 1000))
    monkeypatch.setattr(background, "ensure", lambda: ("127.0.0.1", 9876, 1))
    with pytest.raises(RuntimeError, match="background"):
        background.find_window()


def test_blender_fills_the_nested_screen(monkeypatch, tmp_path):
    live_module = _runtime_live(monkeypatch)
    started = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            started.update(argv=argv, env=kwargs["env"])
            self.pid = 3

        def poll(self):
            return None

        def wait(self):
            return 0

    monkeypatch.setattr(live_module.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(live_module, "LOG", tmp_path / "log")
    monkeypatch.setattr(live_module.socket, "create_connection", lambda *a, **k: open(os.devnull))
    control = SimpleNamespace(request=lambda command: ":91")
    live = live_module.LiveBlender("/usr/bin/blender", background=False, control=control, screen=(1600, 1000))
    live.ensure()
    assert started["argv"][:7] == ["/usr/bin/blender", "--factory-startup", "--window-geometry", "0", "0",
                                   "1600", "1000"]
    assert started["env"]["DISPLAY"] == ":91" and started["env"]["XAUTHORITY"] == "/tmp/xauth"


async def test_computer_open_accepts_the_window_id_live_blender_reports(tmp_path, monkeypatch):
    from dream.computer import Computer

    computer = Computer(tmp_path, tmp_path / "captures")
    attached = {}

    async def state(target):  # no X11 call, no screenshot: only the id contract is under test
        attached.update(target)
        return {}

    async def observe(token, target):
        return {"target_id": token}

    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(computer, "capabilities", lambda: {"desktop": True})
    monkeypatch.setattr(computer, "_desktop_state", state)
    monkeypatch.setattr(computer, "_observe", observe)
    window = "Blender window: 0x4c00003".split(": ", 1)[1]  # get_window's answer
    await computer.open("desktop", window_id=window)
    assert attached == {"kind": "desktop", "window_id": str(0x4c00003)}
