"""The user's standing instructions — a small, hand-editable file of preferences that gets
injected into every system prompt. Unlike memory, this is explicit and always-on: what
the user tells Dream to do or avoid, session after session, without having to repeat it.

The store is a single markdown file (``config.INSTRUCTIONS_FILE``). ``/instructions`` in
the TUI is the front door; these are the primitives it drives.
"""

from __future__ import annotations

from .. import config


class Sourced(str):
    """A prompt section's text, carrying which stretch of it came from where (DREAM-257): `parts` is (source,
    detail, characters) in order, the characters adding up to the text. Recorded while the files are read, so what
    a prompt says of a file is the file as it was read then, whatever it holds later."""
    parts: tuple = ()


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


def project_instructions(workspace, *, create=True) -> str:
    """Bootstrap and capture canonical project rules, compatible custom files and visible errors."""
    from .project_bootstrap import capture, POINTERS
    if not workspace:
        return ""
    sources, notices = capture(workspace, config.PROJECT_INSTRUCTIONS_MAX, create=create)
    blocks = []
    for source in sources:
        # Only exact, complete generated pointers are deduplicated. Edited pointers are custom rules.
        if not source.truncated and source.raw == POINTERS.get(source.name, "").encode("utf-8"):
            if any(s.name == "DREAM.md" for s in sources):
                continue
        text = source.raw.decode("utf-8", errors="replace").strip()
        if source.truncated:
            text += (f"\n\n_[truncated: {source.name} is {source.size:,} bytes; the first "
                     f"{config.PROJECT_INSTRUCTIONS_MAX:,} are above. Read the file for the rest.]_")
        if text:
            blocks.append(("project_file", {"file": source.name, "file_bytes": source.size,
                "file_sha256": source.digest, "truncated": source.truncated}, f"### {source.name}\n{text}\n\n"))
    for diagnostic in notices:
        blocks.append(("project_instruction_status", diagnostic,
            f"[Project instructions unavailable: {diagnostic['file']}: {diagnostic['reason']}. "
            "Report the limitation; do not invent missing rules.]\n"))
    if not blocks:
        return ""
    heading = ("\n## This project's instructions\n"
        "The user's current instructions and global preferences take precedence over project files. "
        "DREAM.md takes precedence over compatible AGENTS.md and CLAUDE.md project guidance; "
        "preserve their non-conflicting rules. Role-specific tool restrictions still apply.\n\n")
    if blocks[-1][2].endswith("\n\n"):
        source, detail, block = blocks[-1]
        blocks[-1] = (source, detail, block[:-1])
    section = Sourced(heading + "".join(block for _, _, block in blocks))
    section.parts = (("project_instructions_heading", None, len(heading)),
                     *((source, detail, len(block)) for source, detail, block in blocks))
    return section


def worker_prompt(role: str, head: str, *, workspace=None, agent=None):
    """Standing rules from the captured session; no lead messages, wake or project-memory retrieval.

    Provenance is authoritative even when it records no standing rules. Plain legacy
    heads may fall back to this session's explicitly bound workspace, never global tool ctx.
    """
    from .system_prompt import SystemPrompt
    allowed = {"global_instructions", "project_instructions", "project_instructions_heading",
               "project_file", "project_instruction_status"}
    chunks = []
    spans = getattr(head, "provenance", None)
    if spans is not None:
        for start, end, source, detail in spans:
            if source in allowed:
                chunks.append((head[start:end], source, detail))
    elif workspace is not None:
        global_rules = as_prompt_section()
        if global_rules:
            chunks.append((global_rules, "global_instructions", None))
        project = project_instructions(workspace, create=False)
        at = 0
        for source, detail, size in getattr(project, "parts", ()):
            chunks.append((project[at:at + size], source, detail))
            at += size
    chunks.append((role, "subagent_role_prompt", {"agent": agent} if agent else None))
    text, result_spans, at = [], [], 0
    for index, (content, source, detail) in enumerate(chunks):
        block = content + ("\n" if index < len(chunks) - 1 else "")
        text.append(block)
        result_spans.append((at, at + len(block), source, detail))
        at += len(block)
    result = SystemPrompt("".join(text))
    result.provenance = tuple(result_spans)
    return result
