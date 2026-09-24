"""DREAM-105: run_bash's sandbox shows installed skills' script folders read-only -- and never more than that.

The evaluator's notes: node installed under a shared prefix (/opt) would have mounted all of it, and a root the sandbox
refuses (the home directory itself) would have stopped every run_bash call.
"""
from __future__ import annotations

from pathlib import Path

from dream.core import skill_runtime


def _node_install(prefix: Path, extra: tuple[str, ...] = ()) -> Path:
    """node's tarball layout (bin, include/node, lib/node_modules, share/doc|man, the three text files) plus `extra`."""
    (prefix / "bin").mkdir(parents=True)
    (prefix / "include" / "node").mkdir(parents=True)
    (prefix / "lib" / "node_modules").mkdir(parents=True)
    (prefix / "share" / "doc").mkdir(parents=True)
    (prefix / "share" / "man").mkdir()
    for name in ("CHANGELOG.md", "LICENSE", "README.md"):
        (prefix / name).write_text("")
    node = prefix / "bin" / "node"
    node.write_text("#!/bin/sh\n")
    node.chmod(0o755)
    for name in extra:
        (prefix / name).mkdir(parents=True)
    return node


def test_only_a_node_only_prefix_is_mounted(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    nvm = _node_install(tmp_path / "home" / ".nvm" / "versions" / "node" / "v24.0.0")
    tarball = _node_install(tmp_path / "node-v24-linux-x64")
    shared = _node_install(tmp_path / "opt", extra=("other-app",))           # node installed with --prefix=/opt
    # a shared prefix whose top level looks like node's, with other tools' data one level down (the evaluator's F3b)
    hidden = _node_install(tmp_path / "opt2", extra=("lib/other-app", "share/other-tool"))
    # node's tarball extracted into ~/.local (--strip-components=1): never mounted, even when it holds nothing else,
    # since ~/.local/share keeps other tools' credentials (the evaluator's F4b)
    local = _node_install(tmp_path / "home" / ".local")
    npmrc = _node_install(tmp_path / "home" / ".nvm" / "versions" / "node" / "v25.0.0", extra=("etc",))  # etc/npmrc
    for node, expected in ((nvm, nvm.parent.parent), (tarball, tarball.parent.parent), (shared, None), (hidden, None),
                           (local, None), (npmrc, None)):
        monkeypatch.setattr(skill_runtime.shutil, "which", lambda name, node=node: str(node))
        assert skill_runtime._node_prefix() == (expected.resolve() if expected else None), node


def test_a_root_the_sandbox_would_refuse_is_dropped_not_passed_on(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    node = _node_install(home)                                                 # node installed with --prefix=$HOME
    monkeypatch.setattr(skill_runtime.shutil, "which", lambda name: str(node))
    monkeypatch.setattr(skill_runtime, "UA_ROOT", home)                        # UA_DIR pointed at the home directory
    monkeypatch.setattr(skill_runtime, "UA_SKILLS", home / "bin")
    assert skill_runtime.script_roots() == ()
    assert skill_runtime.script_env() == {}
    # the mirror of validate()'s forbidden trees includes the display folder (DREAM-109 gate note)
    assert not skill_runtime._usable(Path("/tmp/.X11-unix/node"))
