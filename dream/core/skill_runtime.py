"""Folders of installed skill scripts that run_bash can run (DREAM-105).

A skill that ships scripts -- the Understand-Anything plugin's node/python helpers -- runs them with the shell like any
other command, the way Claude Code runs them with Bash. run_bash's sandbox holds only the workspace, so these folders are
added to it READ-ONLY: a script can read its own code and its runtime, and can still write only inside the workspace.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

UA_ROOT = Path(os.environ.get("UA_DIR", str(Path.home() / ".understand-anything" / "repo"))).expanduser()
UA_SKILLS = UA_ROOT / "understand-anything-plugin" / "skills"
_SYSTEM = tuple(Path(p) for p in ("/usr", "/bin", "/sbin", "/lib", "/lib64"))   # the sandbox shows these already


# node's own install tree, level by level: a prefix is mounted only when it holds exactly this and nothing else
_NODE_LAYOUT = {"bin", "include", "lib", "share", "CHANGELOG.md", "LICENSE", "README.md"}
_NODE_SUBDIRS = {"include": {"node"}, "lib": {"node_modules"}, "share": {"doc", "man", "systemtap"}}
# ExecutionScope.validate refuses these as roots; one of them here would stop every run_bash call, not just the scripts
_NEVER = tuple(Path(p) for p in ("/", "/home", "/tmp", "/var", "/etc"))
_SYSTEM_TREES = tuple(Path(p) for p in ("/proc", "/sys", "/dev", "/run", "/usr", "/bin", "/sbin", "/lib", "/lib64"))


def _node_only(prefix: Path) -> bool:
    """True when the prefix holds node's install and nothing else -- checked below the top level too, because a shared
    prefix (~/.local, /opt) keeps other tools' data inside its lib/ and share/."""
    try:
        if not {e.name for e in prefix.iterdir()} <= _NODE_LAYOUT:
            return False
        for sub, allowed in _NODE_SUBDIRS.items():
            if (prefix / sub).is_dir() and not {e.name for e in (prefix / sub).iterdir()} <= allowed:
                return False
    except OSError:
        return False
    return (prefix / "include" / "node").is_dir()


def _node_prefix() -> Path | None:
    """node's install prefix when it lives outside the system trees and holds node alone: nvm's
    ~/.nvm/versions/node/vX, or an unpacked node tarball. ~/.local is never mounted, whatever it holds; any other
    shared prefix (/opt, a conda env) fails the node-only check. node then comes from the system trees or is missing."""
    found = shutil.which("node")
    if not found:
        return None
    exe = Path(found).resolve()
    if any(exe.is_relative_to(p) for p in _SYSTEM):
        return None
    prefix = exe.parent.parent
    if prefix == (Path.home() / ".local").resolve() or not _node_only(prefix):
        return None
    return prefix


def _usable(root: Path) -> bool:
    home = Path.home().resolve()
    return not (root in _NEVER or root == home or any(root == t or root.is_relative_to(t) for t in _SYSTEM_TREES))


def script_roots() -> tuple[Path, ...]:
    """The read-only folders run_bash's sandbox adds: the Understand-Anything clone (its skills import
    ../../packages/core) and node's install prefix. A root the sandbox would refuse is dropped rather than passed on."""
    roots: list[Path] = []
    if UA_SKILLS.is_dir():
        roots.append(UA_ROOT.resolve())
    prefix = _node_prefix()
    if prefix is not None:
        roots.append(prefix)
    return tuple(r for r in dict.fromkeys(roots) if _usable(r))


def script_env() -> dict[str, str]:
    """What run_bash's environment gains: `$UA_SKILLS`, the plugin's skills folder the skills name their scripts by,
    and node's bin on PATH when node lives outside the system trees."""
    env: dict[str, str] = {}
    roots = script_roots()
    if UA_SKILLS.is_dir() and UA_ROOT.resolve() in roots:
        env["UA_SKILLS"] = str(UA_SKILLS.resolve())
    prefix = _node_prefix()
    if prefix is not None and prefix in roots:
        env["PATH"] = f"{prefix / 'bin'}:{os.environ.get('PATH') or '/usr/local/bin:/usr/bin:/bin'}"
    return env
