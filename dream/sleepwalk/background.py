"""Background runs (DREAM-161): a systemd user timer that runs `dream sleepwalk run-due` every 15 minutes while the
owner is logged in (no linger), also with Dream closed. Off by default; turning it off removes both unit files.

The unit is written from this installation at the moment it is turned on; every path in it is under the home
folder (%h), and Dream refuses to turn it on otherwise."""
from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .. import config

NAME = "dream-sleepwalk"


def _folder() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    tool = shutil.which("systemctl")
    if tool is None:
        raise ValueError("systemctl was not found; background runs need a systemd user session")
    return subprocess.run([tool, "--user", *args], capture_output=True, text=True, timeout=30)


def state() -> str:
    """on, off or unavailable, as `systemctl --user is-enabled` reports it."""
    try:
        return "on" if _systemctl("is-enabled", f"{NAME}.timer").stdout.strip() == "enabled" else "off"
    except (ValueError, OSError, subprocess.SubprocessError):
        return "unavailable"


def _plain(value: str) -> str:
    """Unit files get plain values only: Dream refuses (rather than escapes) any character systemd would interpret."""
    if any(c in value for c in '\n\r"\\%$\'') or not value.isprintable():
        raise ValueError(f"background runs cannot use {value!r}: it contains a character a systemd unit would interpret")
    return value


def _home(path: Path) -> str:
    path, home = Path(os.path.abspath(path)), Path.home()   # not resolved: a venv's python is a link out of it
    if not path.is_relative_to(home):
        raise ValueError(f"background runs need Dream inside your home folder ({path} is not)")
    return "%h/" + _plain(path.relative_to(home).as_posix())


def _logind(prop: str) -> str:
    try:
        return subprocess.run(["loginctl", "show-user", getpass.getuser(), "-p", prop, "--value"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def logged_in() -> bool:
    """The owner has an active login session now (logind); unknown counts as no."""
    return _logind("State") == "active"


def linger() -> bool:
    return _logind("Linger") == "yes"


def turn_on() -> str:
    python, root = _home(Path(sys.executable)), _home(config.ROOT)
    folder = _folder()
    folder.mkdir(parents=True, exist_ok=True)
    # PATH finds the CLI runners as this session does; DREAM_ROOT (and any plugin or trust settings location this
    # session uses) selects the same store, plugins and trusted connectors.
    home = str(Path.home())
    path = ":".join("%h" + e[len(home):] if e == home or e.startswith(home + "/") else e
                    for e in _plain(os.environ.get("PATH", "")).split(":"))       # home entries as %h too
    env = {"PATH": path, "DREAM_ROOT": root,
           **{k: _home(Path(os.environ[k]).expanduser()) for k in ("DREAM_PLUGINS_DIR", "DREAM_EXTENSION_SETTINGS") if os.environ.get(k)}}
    (folder / f"{NAME}.service").write_text(
        "[Unit]\nDescription=Dream Sleepwalk: run due automations\n\n[Service]\nType=oneshot\n"
        f"WorkingDirectory={root}\n" + "".join(f'Environment="{k}={v}"\n' for k, v in env.items())
        + f'ExecStart="{python}" -m dream sleepwalk run-due\n', encoding="utf-8")
    (folder / f"{NAME}.timer").write_text(
        "[Unit]\nDescription=Dream Sleepwalk background runs\n\n[Timer]\nOnCalendar=*:0/15\nPersistent=true\n\n"
        "[Install]\nWantedBy=timers.target\n", encoding="utf-8")
    for args in (("daemon-reload",), ("enable", "--now", f"{NAME}.timer")):
        done = _systemctl(*args)
        if done.returncode:
            raise ValueError(f"systemctl --user {' '.join(args)} failed: {done.stderr.strip()[:200]}")
    return state()


def turn_off() -> str:
    _systemctl("disable", "--now", f"{NAME}.timer")
    for suffix in ("timer", "service"):
        (_folder() / f"{NAME}.{suffix}").unlink(missing_ok=True)
    _systemctl("daemon-reload")
    return state()
