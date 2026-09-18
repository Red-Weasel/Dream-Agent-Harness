"""The user's standing instructions — a small, hand-editable file of preferences that gets
injected into every system prompt. Unlike memory, this is explicit and always-on: what
the user tells Dream to do or avoid, session after session, without having to repeat it.

The store is a single markdown file (``config.INSTRUCTIONS_FILE``). ``/instructions`` in
the TUI is the front door; these are the primitives it drives.
"""

from __future__ import annotations

from .. import config


def load() -> str:
    """The current instructions text, or "" if none are set."""
    path = config.INSTRUCTIONS_FILE
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace").strip()


def save(text: str) -> None:
    """Replace the instructions with ``text`` (creating the parent dir)."""
    path = config.INSTRUCTIONS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


def clear() -> None:
    """Remove the instructions file. A no-op if it isn't there."""
    config.INSTRUCTIONS_FILE.unlink(missing_ok=True)


def as_prompt_section() -> str:
    """The instructions rendered as a system-prompt section, or "" if none are set."""
    text = load()
    if not text:
        return ""
    return f"\n## The user's instructions\n{text}\n"


def project_instructions(workspace) -> str:
    """The workspace's own instructions, the way Claude Code reads CLAUDE.md.

    ``DREAM.md`` first, then ``CLAUDE.md`` when it is there too — a repo that
    already carries instructions for another agent should not have to repeat
    them for me. Missing files cost nothing. A file long enough to crowd out the
    prompt is truncated, and the note says so rather than pretending it fitted.
    """
    from pathlib import Path

    if not workspace:
        return ""
    root = Path(workspace)
    parts: list[str] = []
    for fname in config.PROJECT_INSTRUCTION_FILES:
        f = root / fname
        try:
            if not f.is_file():
                continue
            raw = f.read_bytes()
        except OSError:
            continue
        cap = config.PROJECT_INSTRUCTIONS_MAX
        text = raw[:cap].decode("utf-8", errors="replace").strip()
        if not text:
            continue
        if len(raw) > cap:
            text += (f"\n\n_[truncated: {fname} is {len(raw):,} bytes; the first "
                     f"{cap:,} are above. Read the file for the rest.]_")
        parts.append(f"### {fname}\n{text}")
    if not parts:
        return ""
    return ("\n## This project's instructions\n"
            "The workspace carries its own standing instructions. They rank under "
            "the user's own instructions above and over your defaults.\n\n"
            + "\n\n".join(parts) + "\n")
