"""Native file and shell tools, including Dream's enforced run_bash executor.

Backends can expose these directly or through Dream MCP. Provider-owned Bash must
not inherit the native executor's containment evidence.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from . import mirror
from .context import ctx, err, ok

_MAX_READ = 200_000
_BASH_TIMEOUT = 120


def _resolve(raw: str) -> Path:
    """Relative paths live in the session workspace; absolute paths pass through."""
    return (ctx().workspace / Path(raw).expanduser()).resolve()


@tool(
    "read_file",
    "Read text or PDF/DOCX/XLSX/PPTX extracts. Images: metadata; ZIP/TAR: listings. "
    "No OCR, macros or transcription. Use query for keyword excerpts with source "
    "locations. Defaults: 12000 characters, 10 PDF pages. Follow offset hints for more. "
    "Offsets are CHARACTERS; start_line/line_count read lines. No approval needed.",
    {"type": "object", "properties": {
        "path": {"type": "string"},
        "offset": {"type": "integer", "minimum": 0, "description": "CHARACTERS, not lines; default 0."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50000, "description": "CHARACTERS, not lines; default 12000."},
        "start_line": {"type": "integer", "minimum": 1, "description": "First line, 1-based."},
        "line_count": {"type": "integer", "minimum": 1, "maximum": 2000, "description": "Lines; default 200."},
        "page_start": {"type": "integer", "minimum": 1, "description": "First PDF page; default 1."},
        "page_count": {"type": "integer", "minimum": 1, "maximum": 50, "description": "PDF page count; default 10."},
        "sheet": {"type": "string", "description": "Exact XLSX sheet; default up to 50."},
        "query": {"type": "string", "maxLength": 256, "description": "Keywords within selected extraction; omit for full text."},
    }, "required": ["path"]},
)
async def read_file(args: dict[str, Any]) -> dict[str, Any]:
    from ..files import read_document
    from .context import in_thread

    raw = args.get("path")
    if not isinstance(raw, str) or not raw:
        return err("read_file needs a 'path' argument.")
    try:
        p = _resolve(raw)
        if not p.is_file():
            return err(f"No file at {p}")
        if 'start_line' in args or 'line_count' in args:
            return ok(await in_thread(_read_lines, p, int(args.get('start_line') or 1), int(args.get('line_count') or 200)))
        options = {key: args[key] for key in ('offset', 'limit', 'page_start', 'page_count', 'sheet', 'query') if key in args}
        text = await in_thread(read_document, p, workspace=ctx().workspace, **options)
        limit = args.get('limit')
        if isinstance(limit, int) and limit < 1000:
            text += (f"\n[read_file: limit is in CHARACTERS — this returned {limit} characters. "
                     "For lines, call read_file with start_line and line_count.]")
        return ok(text)
    except Exception as e:
        return err(f"Could not read {raw}: {type(e).__name__}: {e}")


def _read_lines(p, start_line: int, line_count: int) -> str:
    """Lines [start_line, start_line + line_count) of a text file, 1-based, with a hint that
    says where the block sits and where the next one starts."""
    lines = p.read_text(encoding='utf-8', errors='replace').splitlines(keepends=True)
    total = len(lines)
    start = max(1, start_line)
    end = min(total, start + max(1, line_count) - 1)
    if start > total:
        return f"[lines {start}-{start} of {total}: past the end of file]"
    body = ''.join(lines[start - 1:end])
    if not body.endswith('\n'):
        body += '\n'
    tail = 'end of file' if end >= total else f'next: start_line={end + 1}'
    return body + f"[lines {start}-{end} of {total}; {tail}]"


@tool(
    "write_file",
    "Write (create or overwrite) a text file; `append` adds to its end (write big "
    "files in ~300-line parts). For a design deliverable, `asset` registers it as a "
    "version of that named asset (see register_assets); omit for support files.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "append": {"type": "boolean"},
            "asset": {"type": "string"},
            "group": {"type": "string", "enum": ["Type", "Colors", "Spacing", "Components", "Brand"]},
        },
        "required": ["path", "content"],
    },
)
async def write_file(args: dict[str, Any]) -> dict[str, Any]:
    raw = args.get("path")
    if not raw:
        return err("write_file needs a 'path' argument.")
    content = args.get("content", "")
    append = args.get("append") is True
    p = _resolve(raw)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if append:
            with p.open("a", encoding="utf-8") as f:
                f.write(content)
        else:
            p.write_text(content, encoding="utf-8")
    except Exception as e:
        return err(f"{type(e).__name__}: {e}")
    mirror.file_written(p)   # the shown page reloads in the owner's pane (DREAM-104)
    note = (f"Appended {len(content)} chars to {p} (now {p.stat().st_size:,} bytes)" if append
            else f"Wrote {len(content)} chars to {p}")
    asset = str(args.get("asset") or "").strip()
    if asset:
        from .context import in_thread
        from .project import _register

        ws = ctx().workspace.resolve()
        if not p.is_relative_to(ws):
            return ok(note + f"\nNot registered as '{asset}': the file is outside the workspace.")
        rel = str(p.relative_to(ws))
        group = str(args.get("group") or "Brand").strip().title()
        from .project import GROUPS

        if group not in GROUPS:
            return ok(note + f"\nNot registered as '{asset}': group must be one of {', '.join(GROUPS)}.")
        try:
            summary = await in_thread(_register, [{"path": rel, "asset": asset, "group": group,
                                                   "status": "needs-review", "subtitle": "",
                                                   "viewport": None}], ws)
            note += "\n" + summary
        except Exception as e:
            note += f"\nWritten, but not registered as '{asset}': {type(e).__name__}: {e}"
    return ok(note)


@tool(
    "list_dir",
    "List the entries in a directory. No approval needed.",
    {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
)
async def list_dir(args: dict[str, Any]) -> dict[str, Any]:
    raw = args.get("path")
    if not raw:
        return err("list_dir needs a 'path' argument.")
    p = _resolve(raw)
    if not p.is_dir():
        return err(f"Not a directory: {p}")
    entries = sorted(f"{e.name}{'/' if e.is_dir() else ''}" for e in p.iterdir())
    return ok("\n".join(entries) or "(empty)")


_SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH", "SESSION")

# Variables a shell genuinely needs, kept even if their name trips the hints above.
_ENV_KEEP = {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TERM", "TMPDIR", "PWD"}


def _scrubbed_env() -> dict[str, str]:
    """Compatibility helper: fixed ordinary variables, never a secret denylist."""
    from ..core.execution import minimal_environment
    return minimal_environment(inherit=("EDITOR", "DREAM_MONITOR"))


# Redirections that throw stderr away. A command that hides its own errors and then
# prints nothing is indistinguishable from one that succeeded quietly — and that
# ambiguity is what a small model gets stuck on.
_SUPPRESSED_RE = re.compile(r"2\s*>\s*(?:/dev/null|&\s*1\s*>\s*/dev/null)|2>&-")


def _describe_run(command: str, rc: int, text: str) -> str:
    """Say what happened, not just what was printed.

    Observed failure (2026-08-24): a model ran ``cat /etc/firmware 2>/dev/null`` —
    a path that does not exist — got back ``(exit 0)`` and an empty line, and had no
    way to tell that from a command that worked and had nothing to say. It re-ran the
    identical command five times before the loop guard stopped the turn.

    A tool result is the model's only sense organ. When it carries no information,
    saying so explicitly — and naming the reason the information is missing — is what
    lets the model change approach instead of repeating itself.
    """
    body = text if text.strip() else ""
    head = f"(exit {rc})"
    if body:
        return f"{head}\n{body}" if rc == 0 else (
            f"{head} — FAILED\n{body}\nThe command returned a non-zero exit status. "
            f"Read the output above before retrying; running it again unchanged will "
            f"fail the same way.")

    # Nothing was printed. Explain the silence.
    notes: list[str] = []
    if _SUPPRESSED_RE.search(command):
        notes.append(
            "Your command sent stderr to /dev/null, so any error it hit was thrown "
            "away before Dream saw it. Re-run WITHOUT the '2>/dev/null' to find out "
            "what actually happened — a missing path, a directory where a file was "
            "expected, or a permission denial all look like this.")
    if rc == 0:
        notes.append("Exit 0 with no output usually means the command genuinely had "
                     "nothing to report — the file was empty, the search matched "
                     "nothing, or the tool is silent on success.")
    else:
        notes.append(f"It failed (exit {rc}) and printed nothing, which usually means "
                     f"the error went to a stream that was discarded.")
    notes.append("Do NOT repeat this exact command — it will return exactly this "
                 "again. Change the command or verify your assumption another way "
                 "(e.g. `ls -la <path>` to check something exists and what type it is).")
    return f"{head} — no output.\n" + "\n".join(notes)


@tool(
    "run_bash",
    "Run a bash command and return its combined stdout/stderr (asks for approval; read with "
    "read_file/list_dir/grep instead). Verified workspace "
    "containment is a prerequisite for routine contained commands; consequential actions "
    "require approval. An unavailable execution prerequisite cannot be fixed by rewriting "
    "the command. The sandbox sees this workspace, plus installed skills' script folders "
    "read-only ($UA_SKILLS); it has public internet through "
    "a proxy (HTTP_PROXY/HTTPS_PROXY are set: curl, wget, pip, npm and git use them), "
    "while this machine's own services (localhost) and the local network are unreachable. "
    "Avoid '2>/dev/null': suppressing errors hides the reason a "
    "command did nothing, which is the single most common way to get stuck.",
    {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
)
async def run_bash(args: dict[str, Any]) -> dict[str, Any]:
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        return err("run_bash needs a 'command' argument.")
    from ..core.execution import ExecutionUnavailable
    mark = mirror.shell_mark()   # images modified from here on were written by this call (DREAM-111)
    try:
        from ..core.execution import current_execution, execute_bash
        result, contained = await execute_bash(
            command, current_execution(ctx().workspace), timeout=_BASH_TIMEOUT, max_output=_MAX_READ)
    except ExecutionUnavailable as e:
        return err(f"{type(e).__name__}: {e}\n"
                   "Execution prerequisite unavailable. Rewriting the command will not resolve "
                   "this failure; report the prerequisite issue and await an environment change.")
    except Exception as e:
        return err(f"{type(e).__name__}: {e}")
    # A script that rewrote the page the owner is watching reloads it (DREAM-104, one
    # stat); the newest image the call wrote shows in the owner's pane (DREAM-111).
    await mirror.after_shell(mark)
    text = result.output.decode("utf-8", "replace")
    if result.truncated:
        text += "\n[...truncated]"
    if result.timed_out:
        return err(f"Command timed out (limit {_BASH_TIMEOUT}s or scope expiry); "
                   f"owned processes terminated and reaped.\n{text}")
    description = _describe_run(command, result.returncode, text)
    if not contained:
        description = "[explicitly approved uncontained execution]\n" + description
    return err(description) if result.returncode != 0 else ok(description)


from .files import FILE_TOOLS  # noqa: E402 — after the four above are defined
from .measure_image import measure_image  # noqa: E402 — its own module, so the local backend may defer it
from .visual_check import visual_check  # noqa: E402 — likewise (DREAM-097)
from .project_outline import project_outline  # noqa: E402 — likewise (DREAM-113)

NATIVE_TOOLS = [read_file, write_file, list_dir, run_bash, *FILE_TOOLS, measure_image, visual_check,
                project_outline]
