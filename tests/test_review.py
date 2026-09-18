"""Ad-hoc code review: gather the diff, ask an independent reader, parse it back.

The through-line of every test here is the same one the autonomous loop's evaluator
learned: a review that could not run must never read as "no problems found". A dead
reviewer, a garbled reply, a diff that isn't there — each gets its own status, and
none of them is a clean bill of health.

Every git test builds a real throwaway repo in tmp_path; nothing touches Dream's own.
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from dream.core import review


def _git(repo, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=str(repo), capture_output=True, check=True,
    )
    return out.stdout.decode("utf-8", "replace")


@pytest.fixture
def repo(tmp_path):
    """A repo with one commit: a.py at 'return 1'."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "test@example.com")
    _git(r, "config", "user.name", "Test")
    (r / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _git(r, "add", "a.py")
    _git(r, "commit", "-qm", "init")
    return r


# --- gather_diff -------------------------------------------------------------


def test_gather_diff_sees_working_tree_changes(repo):
    (repo / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")

    payload = review.gather_diff(repo)

    assert payload.status == "ok"
    assert payload.files == ("a.py",)
    assert "return 2" in payload.diff
    assert payload.truncated is False


def test_gather_diff_sees_staged_and_unstaged_together_by_default(repo):
    (repo / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    (repo / "b.py").write_text("B = 1\n", encoding="utf-8")
    _git(repo, "add", "b.py")

    payload = review.gather_diff(repo)

    assert payload.status == "ok"
    assert set(payload.files) == {"a.py", "b.py"}


def test_gather_diff_staged_only_ignores_the_unstaged_edit(repo):
    (repo / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    (repo / "b.py").write_text("B = 1\n", encoding="utf-8")
    _git(repo, "add", "b.py")

    payload = review.gather_diff(repo, staged=True)

    assert payload.status == "ok"
    assert payload.files == ("b.py",)
    assert "return 2" not in payload.diff


def test_gather_diff_reports_no_changes_on_a_clean_tree(repo):
    payload = review.gather_diff(repo)

    assert payload.status == "no-changes"
    assert payload.diff == ""
    assert payload.files == ()


def test_gather_diff_outside_a_git_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()

    payload = review.gather_diff(plain)

    assert payload.status == "not-a-repo"
    assert payload.detail


def test_gather_diff_against_a_base_ref(repo):
    (repo / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "second")
    (repo / "a.py").write_text("def f():\n    return 3\n", encoding="utf-8")

    payload = review.gather_diff(repo, base="HEAD~1")

    assert payload.status == "ok"
    # base → working tree, so both the committed and the uncommitted edit are in.
    assert "return 3" in payload.diff
    assert "return 1" in payload.diff


def test_gather_diff_on_a_bad_base_is_an_error_not_no_changes(repo):
    payload = review.gather_diff(repo, base="no-such-ref")

    assert payload.status == "error"
    assert payload.detail


def test_gather_diff_refuses_a_base_that_looks_like_an_option(repo):
    # git diff --output=<path> WRITES a file; a ref is never an option.
    payload = review.gather_diff(repo, base="--output=pwned")

    assert payload.status == "error"
    assert not (repo / "pwned").exists()


def test_untracked_files_are_named_even_though_git_diff_cannot_show_them(repo):
    (repo / "new.py").write_text("NEW = 1\n", encoding="utf-8")

    payload = review.gather_diff(repo)

    # git diff can't see an untracked file, and adding it would be a mutating
    # command — so it has to at least be named, never silently dropped.
    assert payload.untracked == ("new.py",)
    assert payload.status == "no-changes"
    assert "new.py" in payload.detail


def test_a_clipped_untracked_list_says_it_was_clipped(repo):
    for i in range(25):
        (repo / f"n{i:02d}.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")

    payload = review.gather_diff(repo)

    assert len(payload.untracked) == 25
    assert "+5 more" in payload.detail
    assert "+5 more" in review.build_review_prompt(payload)


def test_a_huge_diff_is_truncated_with_an_explicit_marker(repo):
    line = "x = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'\n"
    big = line * (review.MAX_DIFF_CHARS // len(line) + 500)
    (repo / "big.py").write_text(big, encoding="utf-8")
    _git(repo, "add", "big.py")

    payload = review.gather_diff(repo)

    assert payload.status == "ok"
    assert payload.truncated is True
    assert len(payload.diff) <= review.MAX_DIFF_CHARS + 400  # body + marker
    assert "TRUNCATED" in payload.diff
    # And the reviewer must be told, or it will grade the whole change on a slice.
    assert "TRUNCATED" in review.build_review_prompt(payload)


def test_binary_files_do_not_break_the_diff(repo):
    (repo / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00original")
    _git(repo, "add", "logo.png")
    _git(repo, "commit", "-qm", "add binary")
    (repo / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00changed!!")

    payload = review.gather_diff(repo)

    assert payload.status == "ok"
    assert payload.binary == ("logo.png",)
    assert isinstance(payload.diff, str)
    assert "logo.png" in review.build_review_prompt(payload)


def test_undecodable_text_does_not_raise(repo):
    (repo / "a.py").write_bytes(b"# caf\xe9 not utf-8\ndef f():\n    return 2\n")

    payload = review.gather_diff(repo)

    assert payload.status == "ok"
    assert "return 2" in payload.diff  # decoded lossily, not crashed


# --- build_review_prompt -----------------------------------------------------


def test_prompt_carries_the_diff_the_format_and_the_do_not_invent_rule(repo):
    (repo / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    payload = review.gather_diff(repo)

    prompt = review.build_review_prompt(payload, focus="error handling")

    assert "return 2" in prompt
    assert "a.py" in prompt
    assert "FINDING:" in prompt and "FIX:" in prompt
    assert "NO FINDINGS" in prompt
    assert "error handling" in prompt


# --- parse_findings ----------------------------------------------------------


def test_parse_well_formed_findings():
    parsed = review.parse_findings(
        "Here's what I found.\n"
        "FINDING: dream/core/review.py:42 | high | the timeout is never applied\n"
        "FIX: pass timeout=30 to subprocess.run\n"
        "\n"
        "FINDING: dream/tui/app.py:12 | low | unused import\n"
        "FIX: drop the import\n"
    )

    assert parsed.status == "findings"
    assert len(parsed.findings) == 2
    first = parsed.findings[0]
    assert first.file == "dream/core/review.py"
    assert first.line == 42
    assert first.severity == "high"
    assert "timeout" in first.summary
    assert "subprocess.run" in first.fix
    assert parsed.findings[1].severity == "low"


def test_parse_tolerates_markdown_dressing_and_a_missing_line_number():
    parsed = review.parse_findings(
        "- **FINDING:** dream/core/review.py | medium | swallows the error\n"
        "  **FIX:** re-raise it\n"
    )

    assert parsed.status == "findings"
    assert len(parsed.findings) == 1
    assert parsed.findings[0].file == "dream/core/review.py"
    assert parsed.findings[0].line is None
    assert parsed.findings[0].severity == "medium"


def test_parse_explicit_no_findings():
    parsed = review.parse_findings("I read the whole diff carefully.\n\nNO FINDINGS\n")

    assert parsed.status == "none"
    assert parsed.findings == ()


def test_parse_garbage_is_unparseable_not_clean():
    parsed = review.parse_findings(
        "Sure! I'd be happy to help you review this code. What would you like to know?"
    )

    assert parsed.status == "unparseable"
    assert parsed.findings == ()


def test_parse_empty_reply_is_unparseable_not_clean():
    assert review.parse_findings("").status == "unparseable"
    assert review.parse_findings("   \n\n").status == "unparseable"


def test_a_finding_marker_with_nothing_in_it_is_unparseable():
    assert review.parse_findings("FINDING:\nFIX:\n").status == "unparseable"


# --- run_review --------------------------------------------------------------


def _asker(reply: str, seen: list[str] | None = None):
    async def ask(prompt: str) -> str:
        if seen is not None:
            seen.append(prompt)
        return reply

    return ask


async def test_run_review_reports_findings(repo):
    (repo / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    seen: list[str] = []
    ask = _asker("FINDING: a.py:2 | high | returns the wrong value\nFIX: return 1\n", seen)

    result = await review.run_review(repo, ask)

    assert result.status == "reviewed"
    assert len(result.findings) == 1
    assert result.findings[0].file == "a.py"
    assert result.files == ("a.py",)
    assert "return 2" in seen[0]  # the reviewer actually got the diff


async def test_run_review_clean_diff_is_reviewed_with_no_findings(repo):
    (repo / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")

    result = await review.run_review(repo, _asker("NO FINDINGS"))

    assert result.status == "reviewed"
    assert result.findings == ()


async def test_run_review_unparseable_reply_is_never_clean(repo):
    (repo / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")

    result = await review.run_review(repo, _asker("sure thing, happy to help!"))

    assert result.status == "unparseable"
    assert result.findings == ()
    assert "happy to help" in result.detail  # the reply is quoted, not hidden


async def test_run_review_when_the_reviewer_raises_is_unavailable(repo):
    (repo / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")

    async def ask(prompt: str) -> str:
        raise RuntimeError("model server refused the connection")

    result = await review.run_review(repo, ask)

    assert result.status == "unavailable"
    assert result.findings == ()
    assert "RuntimeError" in result.detail
    assert "refused the connection" in result.detail


async def test_run_review_no_changes_never_calls_the_reviewer(repo):
    called = False

    async def ask(prompt: str) -> str:
        nonlocal called
        called = True
        return "NO FINDINGS"

    result = await review.run_review(repo, ask)

    assert result.status == "no-changes"
    assert called is False


async def test_run_review_outside_a_repo_is_unavailable(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()

    result = await review.run_review(plain, _asker("NO FINDINGS"))

    assert result.status == "unavailable"
    assert result.findings == ()


async def test_run_review_passes_the_truncation_flag_through(repo):
    line = "x = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'\n"
    (repo / "big.py").write_text(line * (review.MAX_DIFF_CHARS // len(line) + 500),
                                 encoding="utf-8")
    _git(repo, "add", "big.py")

    result = await review.run_review(repo, _asker("NO FINDINGS"))

    assert result.status == "reviewed"
    assert result.truncated is True


async def test_an_interrupted_review_propagates_rather_than_looking_unavailable(repo):
    (repo / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")

    async def ask(prompt: str) -> str:
        raise asyncio.CancelledError

    # Ctrl-C mid-review is the user stopping, not the reviewer failing — it must
    # reach the TUI's own cancel handling instead of being logged as a status.
    with pytest.raises(asyncio.CancelledError):
        await review.run_review(repo, ask)


# --- integrator hardening: a review must never report a false all-clear ------


def test_a_long_answer_mentioning_no_findings_is_unparseable_not_clean():
    """The one failure mode a review may not have. A reviewer that rambles past
    its own 'no findings' — or whose real findings failed to parse — must never
    come back clean."""
    text = (
        "I looked at the diff carefully. There are no findings in the test "
        "files themselves. However the change in auth.py drops the signature "
        "check entirely, and the token is then trusted without verification, "
        "which lets any caller mint a session. I would treat that as critical "
        "and fix it before merging anything else in this branch."
    )
    parsed = review.parse_findings(text)
    assert parsed.status == "unparseable"
    assert parsed.status != "none"


def test_a_bare_no_findings_answer_is_still_clean():
    for text in ("No findings.", "  no findings  ",
                 "No findings — the diff looks correct."):
        assert review.parse_findings(text).status == "none"


def test_git_diff_cannot_execute_a_hostile_textconv(tmp_path):
    """A repo-local diff.<name>.textconv is a shell command git runs to render a
    blob. Reviewing an untrusted clone must not execute it."""
    import subprocess

    def g(*a):
        subprocess.run(["git", *a], cwd=tmp_path, capture_output=True)

    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (tmp_path / "a.bin").write_bytes(b"\x00hello")
    (tmp_path / ".gitattributes").write_text("*.bin diff=evil\n")
    g("config", "diff.evil.textconv", f"sh -c 'touch {tmp_path}/PWNED; echo x'")
    g("add", "-A")
    g("commit", "-qm", "one")
    (tmp_path / "a.bin").write_bytes(b"\x00changed")

    review.gather_diff(str(tmp_path))
    assert not (tmp_path / "PWNED").exists(), "git diff executed a repo-local command"
