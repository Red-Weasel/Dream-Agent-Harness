"""DREAM-124: live Blender's binary is configurable (DREAM_BLENDER), with the same trust rule.

The default stays /usr/bin/blender and its sandbox is unchanged. Another binary (an official
blender.org build unpacked by root under /opt) must be root-owned with no user-writable path
to it, and so must every entry of its folder, which the sandbox then mounts read-only. No test
starts Blender, a sandbox or a display.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dream.core import execution as ex
from dream.media import blender_live

SOURCE = "(DREAM_BLENDER or the blender.binary runtime setting)"


def _binary_seen_with(env_value: str | None) -> str:
    env = {k: v for k, v in os.environ.items() if k != "DREAM_BLENDER"}
    if env_value is not None:
        env["DREAM_BLENDER"] = env_value
    code = "from dream.media import blender_live; print(blender_live.BLENDER)"
    return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                          check=True).stdout.strip()


def test_the_default_binary_is_the_system_package():
    assert _binary_seen_with(None) == "/usr/bin/blender"
    assert _binary_seen_with("") == "/usr/bin/blender"


def test_dream_blender_names_the_binary():
    assert _binary_seen_with("/opt/blender-4.5.14-linux-x64/blender") == "/opt/blender-4.5.14-linux-x64/blender"


def _fake_build(tmp_path: Path) -> Path:
    folder = tmp_path / "blender-4.5.14-linux-x64"
    (folder / "lib").mkdir(parents=True)
    (folder / "lib" / "libfake.so").write_bytes(b"")
    binary = folder / "blender"
    binary.write_bytes(b"")
    binary.chmod(0o755)
    return binary


def test_a_missing_binary_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(blender_live, "BLENDER", tmp_path / "no-blender")
    reason = blender_live.unavailable(tmp_path)
    assert reason.startswith(f"no Blender at {tmp_path / 'no-blender'}")


def test_a_user_owned_binary_is_refused_with_a_clear_reason(tmp_path, monkeypatch):
    binary = _fake_build(tmp_path)
    monkeypatch.setattr(blender_live, "BLENDER", binary)
    reason = blender_live.unavailable(tmp_path)
    assert reason == (f"the Blender at {binary} is not root-owned or can be changed by a user; "
                      "live Blender runs only a binary installed by root " + SOURCE)
    entries, warnings = blender_live.managed_servers(tmp_path)
    assert entries == [] and warnings == [f"live Blender is off: {reason}"]


def test_a_user_owned_entry_in_the_build_folder_is_refused(tmp_path, monkeypatch):
    binary = _fake_build(tmp_path)
    monkeypatch.setattr(blender_live, "BLENDER", binary)
    monkeypatch.setattr(blender_live, "_trusted_system_file", lambda path: True)  # the binary alone passes
    reason = blender_live.unavailable(tmp_path)
    assert reason == (f"the Blender folder {binary.parent} holds {binary.parent}, which is not root-owned "
                      "or can be changed by a user; install the build as root " + SOURCE)


def test_the_folder_check_finds_a_user_owned_entry(tmp_path):
    binary = _fake_build(tmp_path)
    assert blender_live._untrusted_in(binary.parent) == binary.parent


def test_the_folder_check_passes_a_root_owned_tree():
    folder = Path("/etc/ssl/certs")
    if not folder.is_dir() or any(p.lstat().st_uid != 0 for p in (folder, *folder.iterdir())):
        pytest.skip("no root-owned sample tree here")
    assert blender_live._untrusted_in(folder) is None


def test_a_build_folder_is_mounted_read_only_and_the_default_mounts_nothing_more(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.setattr(blender_live, "BLENDER", Path("/usr/bin/blender"))  # the default, whatever DREAM_BLENDER says
    default = blender_live._scope(workspace)
    assert all(not r.is_relative_to("/usr") for r in default.read_roots)
    binary = _fake_build(tmp_path)
    monkeypatch.setattr(blender_live, "BLENDER", binary)
    monkeypatch.setattr(blender_live, "_untrusted_in", lambda folder: None)
    scope = blender_live._scope(workspace)
    assert scope.read_roots == (*default.read_roots, binary.parent.resolve())
    fds = {root: 100 + i for i, root in enumerate((scope.workspace, *scope.read_roots))}
    x11, xauth = tmp_path / "x11", tmp_path / "xauth"
    x11.mkdir()
    xauth.write_bytes(b"")
    argv = ex.blender_live_argv(scope, "/usr/bin/bwrap", ["run"], seccomp_fd=9, mount_fds=fds,
                                x11_dir=x11, xauth=xauth)
    at = argv.index(str(binary.parent.resolve()))
    assert argv[at - 2:at] == ["--ro-bind-fd", str(fds[binary.parent.resolve()])]
    assert "--dev-bind" not in argv and "/dev/dri" not in " ".join(argv)  # no device pass-through


def test_the_system_binary_needs_no_extra_mount(tmp_path):
    assert blender_live._blender_folder(Path("/usr/bin/blender")) is None
    binary = _fake_build(tmp_path)
    assert blender_live._blender_folder(binary) == binary.parent.resolve()


def test_a_binary_directly_in_a_top_level_folder_is_refused(monkeypatch, tmp_path):
    binary = _fake_build(tmp_path)  # stands in for /opt/blender: the whole of /opt would be mounted
    monkeypatch.setattr(blender_live, "BLENDER", binary)
    monkeypatch.setattr(blender_live, "_trusted_system_file", lambda path: True)
    monkeypatch.setattr(blender_live, "_blender_folder", lambda blender: Path("/opt"))
    reason = blender_live.unavailable(tmp_path)
    assert reason == (f"the Blender at {binary} must sit in its own folder, "
                      "such as /opt/blender-4.5.14-linux-x64/blender " + SOURCE)


def test_the_launcher_takes_the_binary_from_its_arguments(tmp_path, monkeypatch, capsys):
    binary = _fake_build(tmp_path)
    started = []
    monkeypatch.setattr(blender_live.subprocess, "Popen", lambda *a, **k: started.append(a))
    monkeypatch.setattr(blender_live, "BLENDER", blender_live.BLENDER)  # restored after the test
    assert blender_live.main(["--workspace", str(tmp_path), "--display", ":0", "--blender", str(binary)]) == 1
    err = capsys.readouterr().err
    assert f"live Blender did not start: the Blender at {binary} is not root-owned" in err
    assert "Nothing ran outside the sandbox" in err and started == []


def _binary_seen_with_settings(tmp_path: Path, settings: dict, env_value: str | None = None) -> str:
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "runtime-settings.json").write_text(json.dumps(settings))
    env = {k: v for k, v in os.environ.items() if k != "DREAM_BLENDER"}
    env["DREAM_ROOT"] = str(tmp_path)
    if env_value is not None:
        env["DREAM_BLENDER"] = env_value
    code = "from dream.media import blender_live; print(blender_live.BLENDER)"
    return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                          check=True).stdout.strip()


def test_the_runtime_setting_names_the_binary_and_the_environment_wins(tmp_path):
    settings = {"version": 1, "blender": {"binary": "/opt/blender-4.5.14-linux-x64/blender"}}
    assert _binary_seen_with_settings(tmp_path, settings) == "/opt/blender-4.5.14-linux-x64/blender"
    assert _binary_seen_with_settings(tmp_path, settings, "/opt/other/blender") == "/opt/other/blender"
    assert _binary_seen_with_settings(tmp_path, {"version": 1}) == "/usr/bin/blender"
    # a value that is not a path is not dropped silently: it becomes a relative path, which is refused
    assert _binary_seen_with_settings(tmp_path, {"version": 1, "blender": {"binary": ""}}) == "."


def test_the_setting_survives_a_profile_save(tmp_path):
    from dream.core import profiles
    path = tmp_path / "runtime-settings.json"
    path.write_text(json.dumps({"version": 1, "blender": {"binary": "/opt/b/blender"}}))
    profiles.save_settings("lean", path=path)
    assert json.loads(path.read_text())["blender"] == {"binary": "/opt/b/blender"}


@pytest.mark.parametrize("relative", ["blender", "./blender", "opt/blender-4.5.14-linux-x64/blender", "."])
def test_a_relative_path_is_refused(tmp_path, monkeypatch, relative):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "blender").symlink_to("/usr/bin/blender")  # even one that resolves to the system package
    monkeypatch.setattr(blender_live, "BLENDER", Path(relative))
    assert blender_live.unavailable(tmp_path) == (f"the Blender path {Path(relative)} is not absolute; "
                                                  "DREAM_BLENDER or the blender.binary runtime setting must name one")


def _usable_here(monkeypatch, workspace: Path) -> None:
    """The launcher's own checks pass here, with a stand-in for the owner's display (nothing connects to it)."""
    monkeypatch.setattr(blender_live, "x11_display", lambda value=None: (":0", 0))
    reason = blender_live.unavailable(workspace)
    if reason:
        pytest.skip(f"live Blender unavailable here: {reason}")


class _Recorded:
    """subprocess.Popen for the launcher's sandbox: records the argv, runs nothing."""
    calls: list = []

    def __init__(self, argv, **kwargs):
        _Recorded.calls.append(list(argv))

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0


def test_a_symlink_swapped_after_the_check_cannot_run_the_user_file(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    _usable_here(monkeypatch, workspace)
    link = workspace / "blender"
    link.symlink_to("/usr/bin/blender")  # user-owned symlink to the root-owned binary: passes the check
    user_file = workspace / "evil"
    user_file.write_text("#!/bin/sh\necho EXECUTED-USER-FILE\n")
    user_file.chmod(0o755)
    monkeypatch.setattr(blender_live, "BLENDER", link)
    entries, warnings = blender_live.managed_servers(workspace)
    assert not warnings
    args = entries[0]["args"]
    assert args[args.index("--blender") + 1] == "/usr/bin/blender"  # the resolved, checked path travels
    link.unlink()
    link.symlink_to(user_file)  # the swap
    _Recorded.calls = []
    monkeypatch.setattr(blender_live.subprocess, "Popen", _Recorded)
    assert blender_live.main(args[3:]) == 0
    (argv,) = _Recorded.calls
    assert argv[argv.index("--blender") + 1] == "/usr/bin/blender"
    assert str(user_file) not in argv and str(link) not in argv


def test_the_launcher_refuses_a_path_that_is_not_resolved(tmp_path, monkeypatch, capsys):
    workspace = tmp_path / "project"
    workspace.mkdir()
    _usable_here(monkeypatch, workspace)
    link = tmp_path / "blender"
    link.symlink_to("/usr/bin/blender")
    _Recorded.calls = []
    monkeypatch.setattr(blender_live.subprocess, "Popen", _Recorded)
    monkeypatch.setattr(blender_live, "BLENDER", blender_live.BLENDER)  # restored after the test
    assert blender_live.main(["--workspace", str(workspace), "--display", ":0", "--blender", str(link)]) == 1
    err = capsys.readouterr().err
    assert f"the launcher runs only a resolved Blender path, not {link}" in err and _Recorded.calls == []


def test_a_root_owned_convenience_symlink_runs_the_resolved_path_inside_the_mounted_folder(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    _usable_here(monkeypatch, workspace)
    binary = _fake_build(tmp_path)
    convenience = tmp_path / "blender"
    convenience.symlink_to(binary.parent)  # like /opt/blender -> /opt/blender-4.5.14-linux-x64
    real = binary.resolve()
    real_lstat = Path.lstat

    def lstat_as_root(self):  # the gate's stub: the fake build reads as root-owned
        info = real_lstat(self)
        if Path(self).is_relative_to(tmp_path) and not Path(self).is_relative_to(workspace):
            return os.stat_result((info.st_mode & ~0o022, *info[1:4], 0, *info[5:]))
        return info
    monkeypatch.setattr(Path, "lstat", lstat_as_root)
    monkeypatch.setattr(blender_live, "_trusted_system_file", lambda path: Path(path) == real or ex._trusted_system_file(path))
    monkeypatch.setattr(blender_live, "BLENDER", convenience / "blender")
    entries, warnings = blender_live.managed_servers(workspace)
    assert not warnings, warnings
    args = entries[0]["args"]
    assert args[args.index("--blender") + 1] == str(real)
    _Recorded.calls = []
    monkeypatch.setattr(blender_live.subprocess, "Popen", _Recorded)
    assert blender_live.main(args[3:]) == 0
    (argv,) = _Recorded.calls
    assert argv[argv.index("--blender") + 1] == str(real)
    at = argv.index(str(real.parent))  # the resolved folder is mounted, so the resolved path exists inside
    assert argv[at - 2] == "--ro-bind-fd" and str(convenience) not in argv
