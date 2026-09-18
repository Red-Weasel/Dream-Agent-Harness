"""GitHub tools on a faked `gh`: no network, the CLI's answers are scripted."""

from __future__ import annotations

import base64
import json

import pytest

from dream.tools import context as tool_context, github_tools
from dream.tools.context import ToolContext, set_context
from dream.tools.github_tools import (
    GITHUB_TOOLS, github_get_tree, github_import_files, github_list_repos, github_read_file,
)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=tmp_path))
    calls: list[tuple[str, ...]] = []
    answers: dict[str, tuple[int, str]] = {}

    async def fake_gh(*args, timeout=60.0):
        calls.append(args)
        for key, ans in answers.items():
            if key in " ".join(args):
                return ans
        return 1, "no scripted answer for " + " ".join(args)

    monkeypatch.setattr(github_tools, "_gh", fake_gh)
    yield tmp_path, calls, answers
    tool_context._CTX = None


def _text(res):
    return res["content"][0]["text"]


def _contents(text: str, size=None):
    return json.dumps({"encoding": "base64", "content": base64.b64encode(text.encode()).decode(),
                       "size": size if size is not None else len(text)})


@pytest.mark.asyncio
async def test_list_repos_resolves_default_branches(ws):
    _, calls, answers = ws
    answers["user/repos"] = (0, '{"name":"me/app","default_branch":"main","description":"the app","updated":"2026"}\n'
                                '{"name":"me/kit","default_branch":"trunk","description":null,"updated":"2025"}\n')
    out = _text(await github_list_repos.handler({}))
    assert "me/app  (default: main)  the app" in out and "me/kit  (default: trunk)" in out
    assert "user/repos" in " ".join(calls[-1])
    assert (await github_list_repos.handler({"owner": "bad name"})).get("is_error")


@pytest.mark.asyncio
async def test_get_tree_filters_by_prefix_and_reports_more(ws):
    _, calls, answers = ws
    answers["git/trees"] = (0, "src/theme.ts\t120\nsrc/app.tsx\t900\nREADME.md\t10\n")
    out = _text(await github_get_tree.handler({"owner": "me", "repo": "app", "path_prefix": "src", "limit": 1}))
    assert "src/theme.ts" in out and "README.md" not in out and "1 more" in out
    assert "repos/me/app/git/trees/HEAD?recursive=1" in " ".join(calls[-1])
    out = _text(await github_get_tree.handler({"owner": "me", "repo": "app", "ref": "v2", "path_prefix": "nope"}))
    assert "No files under 'nope'" in out
    assert (await github_get_tree.handler({"owner": "me/../x", "repo": "app"})).get("is_error")


@pytest.mark.asyncio
async def test_read_file_returns_text_and_refuses_binary_and_directories(ws):
    _, _, answers = ws
    answers["contents/src/theme.ts"] = (0, _contents("export const primary = '#D97757';"))
    answers["contents/logo.png"] = (0, _contents("\x89PNG\x00\x00"))
    answers["contents/src?"] = (0, "[]")
    out = _text(await github_read_file.handler({"owner": "me", "repo": "app", "path": "src/theme.ts"}))
    assert "#D97757" in out and out.startswith("--- me/app@HEAD:src/theme.ts ---")
    res = await github_read_file.handler({"owner": "me", "repo": "app", "path": "logo.png"})
    assert res.get("is_error") and "binary" in _text(res)
    res = await github_read_file.handler({"owner": "me", "repo": "app", "path": "src"})
    assert res.get("is_error") and "directory" in _text(res)


@pytest.mark.asyncio
async def test_import_files_lands_paths_inside_the_workspace_and_reports_failures(ws):
    root, _, answers = ws
    answers["contents/src/theme.ts"] = (0, _contents("tokens"))
    answers["contents/styles/global.css"] = (0, _contents("body{}"))
    answers["contents/missing.css"] = (1, "HTTP 404: Not Found")
    res = await github_import_files.handler({"owner": "me", "repo": "app", "ref": "main",
                                             "paths": ["src/theme.ts", "styles/global.css", "missing.css", "../../etc/passwd"]})
    assert not res.get("is_error")
    out = _text(res)
    assert "Imported 2 of 4" in out and "imported/app/src/theme.ts" in out
    assert (root / "imported" / "app" / "src" / "theme.ts").read_text() == "tokens"
    assert (root / "imported" / "app" / "styles" / "global.css").read_text() == "body{}"
    assert "missing.css: HTTP 404" in out and "refused (path escapes" in out
    assert not (root.parent / "etc").exists()
    res = await github_import_files.handler({"owner": "me", "repo": "app", "paths": ["a"], "dest": "../out"})
    assert res.get("is_error") and "inside the workspace" in _text(res)
    res = await github_import_files.handler({"owner": "me", "repo": "app", "paths": ["x"] * 61})
    assert res.get("is_error") and "at most 60" in _text(res)


@pytest.mark.asyncio
async def test_a_missing_or_logged_out_gh_is_a_plain_error(ws, monkeypatch):
    _, _, answers = ws
    answers["user/repos"] = (1, "To get started with GitHub CLI, please run:  gh auth login")
    res = await github_list_repos.handler({})
    assert res.get("is_error")


@pytest.mark.asyncio
async def test_the_real_gh_wrapper_names_the_fix_when_gh_is_absent(monkeypatch):
    monkeypatch.setattr(github_tools.shutil, "which", lambda _n: None)
    rc, msg = await github_tools._gh("api", "user")
    assert rc == 127 and "gh auth login" in msg


def test_the_four_tools_are_exported():
    assert [t.name for t in GITHUB_TOOLS] == ["github_list_repos", "github_get_tree", "github_read_file", "github_import_files"]


async def test_writable_gh_cannot_inherit_host_credentials(tmp_path, monkeypatch):
    fake = tmp_path / "gh"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setattr(github_tools.shutil, "which", lambda name: str(fake))
    code, message = await github_tools._gh("api", "user")
    assert code == 127 and "trusted system" in message


async def test_github_uses_owned_timeout_and_only_related_credentials(monkeypatch):
    from dream.core import execution
    monkeypatch.setenv("DREAM_UNRELATED_FIXTURE_SECRET", "never-forward")
    monkeypatch.setenv("GH_TOKEN", "fixture-token")
    monkeypatch.setattr(github_tools.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(execution, "_trusted_system_file", lambda path: True)
    async def owned(argv, **kwargs):
        assert argv == ["/usr/bin/gh", "api", "user"]
        assert kwargs["env"]["GH_TOKEN"] == "fixture-token"
        assert "DREAM_UNRELATED_FIXTURE_SECRET" not in kwargs["env"]
        assert kwargs["timeout"] == 3
        return execution.ProcessResult(124, b"", timed_out=True)
    monkeypatch.setattr(execution, "run_owned", owned)
    code, message = await github_tools._gh("api", "user", timeout=3)
    assert code == 124 and "owned processes were stopped" in message
