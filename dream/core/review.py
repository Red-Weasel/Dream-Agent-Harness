"""Ad-hoc code review — hand my own working diff to an independent reader.

The autonomous loop already has the shape this needs: the thing that did the work
never gets to grade it, and a grader that couldn't run reports *that*, not PASS.
This is the same discipline aimed at a diff instead of a contract, available on
demand rather than only at the end of a loop.

The reviewer is injected as an ``ask`` callable, so this module stays backend-blind
(the Claude SDK and the local MachX loop both reduce to "prompt in, text out") and
testable without a model. Everything git here is READ-ONLY — ``diff`` / ``status`` /
``rev-parse`` / ``ls-files``, never a command that mutates the tree, the index, or
history. A review must never be able to change the thing it is reviewing.

Four honest outcomes, and none of them is a fake clean bill of health:
``reviewed`` (the model looked and said what it found — possibly nothing),
``no-changes``, ``unavailable`` (no repo, git failed, or the reviewer died), and
``unparseable`` (it answered, but not in a way that can be read as findings).
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

# Ceiling on the diff text handed to the reviewer. A 300-file refactor must come
# back truncated-with-a-marker rather than blowing the context: a review of half a
# diff is useful as long as it says so.
MAX_DIFF_CHARS = 60_000

_GIT_TIMEOUT_S = 60

SEVERITIES = ("critical", "high", "medium", "low")

Ask = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class Finding:
    file: str
    line: int | None
    severity: str  # one of SEVERITIES, or "unknown"
    summary: str
    fix: str


@dataclass(frozen=True)
class DiffPayload:
    status: str  # ok | no-changes | not-a-repo | error
    label: str  # what was diffed, in words, for the prompt
    diff: str
    files: tuple[str, ...]
    binary: tuple[str, ...]
    untracked: tuple[str, ...]
    truncated: bool
    detail: str  # why, when the status isn't ok — plus anything not in the diff


@dataclass(frozen=True)
class ParsedReview:
    status: str  # findings | none | unparseable
    findings: tuple[Finding, ...]
    detail: str


@dataclass(frozen=True)
class ReviewResult:
    status: str  # reviewed | no-changes | unavailable | unparseable
    findings: tuple[Finding, ...]
    detail: str
    files: tuple[str, ...]
    truncated: bool


# --- gathering the diff ------------------------------------------------------


def _git(workspace: Path, *args: str) -> tuple[int, str, str]:
    """Run one git command, capturing bytes and decoding lossily — a diff of a
    latin-1 source file is not a reason to crash. GIT_OPTIONAL_LOCKS=0 keeps even
    `git diff` from refreshing (writing) the index behind the user's back."""
    proc = subprocess.run(
        ["git", *args],
        cwd=str(workspace),
        capture_output=True,
        timeout=_GIT_TIMEOUT_S,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", "replace"),
        proc.stderr.decode("utf-8", "replace").strip(),
    )


def _empty(status: str, detail: str, label: str = "") -> DiffPayload:
    return DiffPayload(status, label, "", (), (), (), False, detail)


def _names(paths: tuple[str, ...], limit: int = 20) -> str:
    """A clipped list that admits it was clipped — "20 of 25 shown" is a caveat the
    reviewer can act on; a silent slice looks like the whole truth."""
    shown = ", ".join(paths[:limit])
    return shown if len(paths) <= limit else f"{shown} (+{len(paths) - limit} more)"


def _truncate(diff: str) -> tuple[str, bool]:
    if len(diff) <= MAX_DIFF_CHARS:
        return diff, False
    head = diff[:MAX_DIFF_CHARS]
    cut = head.rfind("\n")  # never hand over half a line
    if cut > 0:
        head = head[:cut]
    marker = (
        f"\n\n[DIFF TRUNCATED — {len(head)} of {len(diff)} characters shown. "
        "The rest of this change was NOT included and has NOT been reviewed.]\n"
    )
    return head + marker, True


def gather_diff(
    workspace: str | Path, *, staged: bool = False, base: str | None = None
) -> DiffPayload:
    """What has changed in ``workspace``, ready to be reviewed.

    Default is everything since the last commit (staged and unstaged together);
    ``staged`` narrows to the index, ``base`` diffs a ref against the working tree.
    """
    ws = Path(workspace)
    if base is not None and base.startswith("-"):
        # A ref never starts with a dash, and `git diff --output=x` writes a file:
        # anything option-shaped here is a mistake or an attempt at one.
        return _empty("error", f"invalid base ref: {base!r}")

    try:
        code, _, err = _git(ws, "rev-parse", "--show-toplevel")
        if code != 0:
            return _empty("not-a-repo", err or f"{ws} is not a git repository")

        # --no-textconv matters as much as --no-ext-diff: a repo-local
        # `diff.<name>.textconv` is a shell command git runs to render a blob, so
        # reviewing a hostile clone would execute it. Reviewing must stay read-only
        # in the strong sense — it reads code Dream does not trust yet.
        args = ["diff", "--no-color", "--no-ext-diff", "--no-textconv"]
        if staged:
            args.append("--cached")
        if base:
            args.append(base)
        elif not staged:
            # An unborn repo has no HEAD to diff against; its index is all there is.
            has_head = _git(ws, "rev-parse", "--verify", "--quiet", "HEAD")[0] == 0
            args.append("HEAD" if has_head else "--cached")

        code, stat_out, err = _git(ws, *args, "--numstat")
        if code != 0:
            return _empty("error", err or "git diff failed")
        code, diff, err = _git(ws, *args)
        if code != 0:
            return _empty("error", err or "git diff failed")

        _, untracked_out, _ = _git(ws, "ls-files", "--others", "--exclude-standard")
    except (OSError, subprocess.SubprocessError) as e:
        return _empty("error", f"git unavailable — {type(e).__name__}: {e}")

    files: list[str] = []
    binary: list[str] = []
    for line in stat_out.splitlines():
        cols = line.split("\t")
        if len(cols) != 3:
            continue
        files.append(cols[2])
        if cols[0] == "-" and cols[1] == "-":
            binary.append(cols[2])
    untracked = tuple(p for p in untracked_out.splitlines() if p.strip())

    if staged and base:
        label = f"staged changes vs {base}"
    elif staged:
        label = "staged changes"
    elif base:
        label = f"working tree vs {base}"
    else:
        label = "working tree vs the last commit"

    # git diff cannot show an untracked file and `git add -N` would mutate the
    # index, so the only honest option is to name them.
    detail = (
        f"{len(untracked)} untracked file(s) not included in the diff: "
        + _names(untracked)
        if untracked
        else ""
    )
    if not diff.strip():
        return DiffPayload(
            "no-changes", label, "", (), (), untracked, False,
            detail or "No changes to review.",
        )

    diff, truncated = _truncate(diff)
    return DiffPayload(
        "ok", label, diff, tuple(files), tuple(binary), untracked, truncated, detail
    )


# --- the reviewer's prompt ---------------------------------------------------

_PROMPT = """\
You are an independent code reviewer. You did NOT write this change and you owe it
no benefit of the doubt — read it as if you have to sign your name under "this is
correct". Review ONLY the diff below.

What you're looking at: {label}
Files changed ({n}):
{files}
{notes}
Look for, in this order: correctness bugs, unhandled failure and error paths, data
loss or corruption, security holes, resource leaks, races, broken or missing tests,
and API/contract breaks. Ignore style preferences.{focus}

Report each defect in exactly this shape, one per defect:

FINDING: <path/to/file.py>:<line> | <critical|high|medium|low> | <what is wrong, one line>
FIX: <the specific change that fixes it>

Rules:
- Only report what this diff actually shows. Do NOT invent findings to look thorough,
  and do NOT pad with observations, praise, or a summary.
- If the change is genuinely sound, reply with exactly: NO FINDINGS
- If something stopped you from reviewing it properly, SAY SO in plain words instead.
  Never answer NO FINDINGS for a diff you could not actually read.

DIFF:
{diff}
"""


def build_review_prompt(payload: DiffPayload, focus: str | None = None) -> str:
    """The prompt for an independent reviewer, including every caveat about what it
    is NOT seeing — a reviewer that doesn't know the diff is partial will grade the
    whole change on a slice of it."""
    notes: list[str] = []
    if payload.binary:
        notes.append(
            "Binary files (contents not shown, do not guess at them): "
            + _names(payload.binary)
        )
    if payload.untracked:
        notes.append(
            f"{len(payload.untracked)} new untracked file(s), NOT in the diff below — "
            "you have not seen their contents: " + _names(payload.untracked)
        )
    if payload.truncated:
        notes.append(
            "This diff is TRUNCATED. You are seeing only part of the change; review "
            "what is here and say plainly that the rest was not shown to you."
        )
    return _PROMPT.format(
        label=payload.label,
        n=len(payload.files),
        files="\n".join(f"  {f}" for f in payload.files) or "  (none listed)",
        notes=("\n" + "\n".join(notes) + "\n") if notes else "",
        focus=f"\n\nThe user asked you to pay particular attention to: {focus}" if focus else "",
        diff=payload.diff,
    )


# --- reading the reviewer's answer -------------------------------------------

# Tolerant of markdown dressing ("- **FINDING:**") because this also runs on local
# models, which decorate whatever you ask them for.
_MARKER = r"^[ \t]*(?:[-*+]\s*)?\*{0,2}\s*%s\s*\*{0,2}\s*:[ \t]*\*{0,2}[ \t]*"
_FINDING_RE = re.compile(_MARKER % "FINDING", re.IGNORECASE | re.MULTILINE)
_FIX_RE = re.compile((_MARKER % "FIX") + r"(.+)", re.IGNORECASE | re.MULTILINE)
_NONE_RE = re.compile(r"\bno[\s_-]*findings\b", re.IGNORECASE)
# Longest answer that a bare "no findings" may still speak for (see _parse).
_NONE_MAX_CHARS = 200
_LINE_RE = re.compile(r"^(.+?):(\d+)(?:-\d+)?$")


def _clean(s: str) -> str:
    return s.strip().strip("*`").strip()


def _split_location(token: str) -> tuple[str, int | None]:
    token = _clean(token)
    m = _LINE_RE.match(token)
    if m:
        return m.group(1), int(m.group(2))
    return token, None


def _severity(*texts: str) -> str:
    for text in texts:
        low = text.lower()
        for sev in SEVERITIES:
            if re.search(rf"\b{sev}\b", low):
                return sev
    return "unknown"


def _parse_block(block: str) -> Finding | None:
    lines = block.splitlines()
    header = _clean(lines[0]) if lines else ""
    fix_m = _FIX_RE.search(block)
    fix = _clean(fix_m.group(1)) if fix_m else ""
    if not header:
        return None

    parts = [p.strip() for p in header.split("|")]
    if len(parts) >= 3:
        loc, sev_raw, summary = parts[0], parts[1], " | ".join(parts[2:])
    elif len(parts) == 2:
        loc, sev_raw, summary = parts[0], "", parts[1]
    else:
        # No separators at all: take a leading path-ish token as the location.
        head, _, rest = header.partition(" ")
        if any(c in head for c in "./") and rest:
            loc, sev_raw, summary = head, "", rest
        else:
            loc, sev_raw, summary = "", "", header

    summary = _clean(summary)
    if not summary:
        return None
    path, line = _split_location(loc)
    return Finding(path, line, _severity(sev_raw, header), summary, fix)


def parse_findings(text: str) -> ParsedReview:
    """Read a reviewer's reply into findings.

    Deliberately asymmetric: findings are parsed leniently, but "clean" is only
    ever granted on an explicit NO FINDINGS. Anything else — a refusal, a preamble,
    an empty reply, a mangled format — comes back ``unparseable``, because silence
    that reads as approval is the one failure this whole module exists to prevent.
    """
    blocks = _FINDING_RE.split(text or "")[1:]
    findings = tuple(f for f in (_parse_block(b) for b in blocks) if f is not None)
    if findings:
        return ParsedReview("findings", findings, "")
    # "No findings" only counts as a clean verdict when it is essentially the
    # WHOLE answer. Searching the full text let a long response that merely
    # mentions the phrase ("no findings in the tests, but auth.py leaks a key")
    # report clean when its findings failed to parse — a false all-clear is the
    # one failure mode a review must never have. Anything longer is unparseable,
    # which is honest.
    body = " ".join((text or "").split())
    if _NONE_RE.search(body) and len(body) <= _NONE_MAX_CHARS:
        return ParsedReview("none", (), "")
    excerpt = " ".join((text or "").split())[:300]
    return ParsedReview(
        "unparseable", (),
        f"the reviewer answered but not in findings form: {excerpt}" if excerpt
        else "the reviewer returned nothing",
    )


# --- the entry point ---------------------------------------------------------


async def run_review(
    workspace: str | Path,
    ask: Ask,
    *,
    staged: bool = False,
    base: str | None = None,
    focus: str | None = None,
) -> ReviewResult:
    """Gather the diff, have ``ask`` review it, and report what came back.

    ``ask`` is any async "prompt in, text out" callable — an SDK query, a local
    backend turn, a fake in a test. Its failure is reported as ``unavailable``;
    a review that could not run is never a review that found nothing.
    """
    payload = gather_diff(workspace, staged=staged, base=base)
    if payload.status == "no-changes":
        return ReviewResult("no-changes", (), payload.detail, (), False)
    if payload.status != "ok":
        return ReviewResult("unavailable", (), payload.detail, (), False)

    try:
        text = await ask(build_review_prompt(payload, focus))
    except Exception as e:  # noqa: BLE001 — the reviewer dying is a status, not a crash
        return ReviewResult(
            "unavailable", (), f"reviewer unavailable — {type(e).__name__}: {e}",
            payload.files, payload.truncated,
        )

    parsed = parse_findings(text)
    status = "unparseable" if parsed.status == "unparseable" else "reviewed"
    return ReviewResult(
        status, parsed.findings, parsed.detail or payload.detail,
        payload.files, payload.truncated,
    )
