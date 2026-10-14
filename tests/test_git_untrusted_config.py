"""2026-10-12 security review (DREAM-290): Dream's own git calls -- the Understand map's freshness (`git status`, polled
by the dashboard) and /review's `git diff` -- ran commands the repository's config names: `core.fsmonitor`, and a
filter driver's clean/process. A workspace shipping a .git/config, or a model writing one in a mode that does not ask
before writes, ran a host command through Dream with no prompt. Those calls now switch both off; filters from the
user's own global config (git-lfs) keep working."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from dream.core.review import gather_diff
from dream.gui.understand_routes import freshness

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def hostile(tmp_path, monkeypatch):
    """A repository whose own config names four commands, one through a filter whose name holds '='."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "empty-global"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo, marks = tmp_path / "repo", tmp_path / "marks"
    repo.mkdir()
    marks.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("one\n")
    (repo / "b.dat").write_text("one\n")
    (repo / ".gitattributes").write_text("*.txt filter=evil\n*.dat filter=a=b\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    head = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "config", "core.fsmonitor", f"touch {marks}/fsmonitor; false")
    _git(repo, "config", "filter.evil.clean", f"touch {marks}/clean; cat")
    _git(repo, "config", "filter.evil.process", f"touch {marks}/process; false")
    _git(repo, "config", "filter.a=b.clean", f"touch {marks}/equals; cat")
    (repo / "a.txt").write_text("one\ntwo\n")
    (repo / "b.dat").write_text("one\ntwo\n")
    return repo, marks, head


def test_review_diff_runs_no_command_the_repository_names(hostile):
    repo, marks, _ = hostile
    payload = gather_diff(repo)
    assert sorted(p.name for p in marks.iterdir()) == []
    assert "+two" in payload.diff and set(payload.files) == {"a.txt", "b.dat"}


def test_understand_freshness_runs_no_command_the_repository_names(hostile):
    repo, marks, head = hostile
    result = freshness(repo, {"project": {"gitCommitHash": head}})
    assert sorted(p.name for p in marks.iterdir()) == []
    assert result["status"] == "dirty" and set(result["changedFiles"]) == {"a.txt", "b.dat"}


def test_a_filter_from_the_users_own_global_config_still_runs(tmp_path, monkeypatch):
    """git-lfs and the like are the user's: only a filter the repository itself defines is switched off."""
    marks = tmp_path / "marks"
    marks.mkdir()
    (tmp_path / "global").write_text(f'[filter "mine"]\n\tclean = "touch {marks}/global-clean; cat"\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "global"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("one\n")
    (repo / ".gitattributes").write_text("*.txt filter=mine\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    for mark in marks.iterdir():
        mark.unlink()
    (repo / "a.txt").write_text("one\ntwo\n")
    assert "+two" in gather_diff(repo).diff
    assert (marks / "global-clean").exists()


# ---- gate round 1: three more ways a read ran a command the repository names -----------------------------------------

def _repo(tmp_path, monkeypatch, name="repo"):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "empty-global"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo, marks = tmp_path / name, tmp_path / "marks"
    repo.mkdir()
    marks.mkdir(exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    return repo, marks


def _ran(repo, marks, commit=None):
    for mark in marks.iterdir():
        mark.unlink()
    gather_diff(repo)
    head = commit or _git(repo, "rev-parse", "HEAD").strip()
    freshness(repo, {"project": {"gitCommitHash": head}})
    return sorted(p.name for p in marks.iterdir())


def test_a_submodules_own_config_names_no_command(tmp_path, monkeypatch):
    repo, marks = _repo(tmp_path, monkeypatch)
    origin, _ = _repo(tmp_path, monkeypatch, "sub-origin")
    (origin / "s.txt").write_text("one\n")
    (origin / ".gitattributes").write_text("*.txt filter=evil\n")
    _git(origin, "add", ".")
    _git(origin, "commit", "-qm", "s")
    (repo / "top.txt").write_text("x\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "top")
    _git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(origin), "sub")
    _git(repo, "commit", "-qm", "sub")
    config = repo / ".git" / "modules" / "sub" / "config"
    config.write_text(config.read_text() + f'[filter "evil"]\n\tclean = "touch {marks}/subclean; cat"\n'
                      f'\tprocess = "touch {marks}/subprocess; false"\n[core]\n\tfsmonitor = "touch {marks}/subfsmonitor; false"\n')
    (repo / "sub" / "s.txt").write_text("two\n")
    assert _ran(repo, marks) == []


def test_a_repository_hook_does_not_run_when_a_read_refreshes_the_index(tmp_path, monkeypatch):
    import os
    import time
    repo, marks = _repo(tmp_path, monkeypatch)
    (repo / "a.txt").write_text("one\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "a")
    hooks = repo / "tools"
    hooks.mkdir()
    (hooks / "post-index-change").write_text(f"#!/bin/sh\ntouch {marks}/post-index-change\n")
    (hooks / "post-index-change").chmod(0o755)
    _git(repo, "config", "core.hooksPath", "tools")
    time.sleep(1.1)
    os.utime(repo / "a.txt", None)      # stat changed, content not: a refresh rewrites the index entry
    assert _ran(repo, marks) == []


def test_a_promisor_remote_named_in_the_config_is_never_fetched_from(tmp_path, monkeypatch):
    repo, marks = _repo(tmp_path, monkeypatch)
    (repo / "b.txt").write_text("blob-content-unique-123\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "b")
    blob = _git(repo, "rev-parse", "HEAD:b.txt").strip()
    (repo / ".git" / "objects" / blob[:2] / blob[2:]).unlink()
    for key, value in (("core.repositoryformatversion", "1"), ("extensions.partialClone", "evil"),
                       ("remote.evil.url", str(tmp_path / "nowhere")), ("remote.evil.promisor", "true"),
                       ("remote.evil.uploadpack", f"touch {marks}/uploadpack; false")):
        _git(repo, "config", key, value)
    (repo / "b.txt").write_text("blob-content-unique-456\n")
    assert _ran(repo, marks) == []                                                   # a missing blob
    assert _ran(repo, marks, commit="1234567890abcdef1234567890abcdef12345678") == []   # a map's unknown commit
