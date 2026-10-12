"""Small, cell-bounded status rows shared by the idle and streaming terminal."""
from __future__ import annotations

from rich.cells import cell_len
from rich.text import Text


def _line(value: str, width: int, style: str = "#c6bbaa") -> Text:
    # Status values can contain paths: keep control characters off the terminal.
    value = " ".join("".join(ch if ch.isprintable() else " " for ch in str(value)).split())
    width = max(0, width)
    row = Text(value, style=style, no_wrap=True, overflow="crop")
    if row.cell_len > width:
        row.truncate(max(0, width - 1), overflow="crop")
        if width:
            row.append(".")
    return row


def minimal_status(d: dict, width: int, *, ascii_only: bool = False) -> list[Text]:
    """Two rows, no wrapping: permissions/effort, then workspace/context."""
    if ascii_only:
        # Budget escaped values before aligning columns, not after rendering.
        d = {key: value.encode("ascii", "backslashreplace").decode("ascii")
             if isinstance(value, str) else value for key, value in d.items()}
    width = max(1, width)
    effort = str(d.get("effort_short") or d.get("effort") or "default")
    right = _line(effort, min(width // 2, 24), "#edbd62")
    busy = d.get("gerund")
    activity = f" | {busy} {int(d.get('elapsed') or 0)}s" if busy else ""
    counts = ""
    if "files_changed" in d:
        counts = f" | files {d['files_changed']}"
        if width >= 76:
            counts += f" | tools {d.get('tools', 0)}"
    # Confirmed work takes priority over the decorative activity verb.
    left = _line(">> " + d["mode_label"] + counts + (activity if not counts else ""), max(0, width - right.cell_len - 2), d["mode_color"])
    if counts and "files " in left.plain:
        start = left.plain.index("files ")
        left.stylize("#f5cf70", start, left.plain.find(" |", start) if " |" in left.plain[start:] else len(left.plain))
    top = left.copy()
    top.append(" " * max(0, width - left.cell_len - right.cell_len))
    top.append_text(right)
    context = str(d.get('context') or 'ctx --')
    context_row = _line(context, max(1, width // 2), "#f5cf70")
    # Keep the repository's final component visible when its parents are long.
    workspace = str(d["ws"]).rstrip("/").rsplit("/", 1)[-1] or "/"
    budget = max(1, width - context_row.cell_len - 7)
    if cell_len(workspace) > budget:
        tail = workspace.rsplit("/", 1)[-1]
        workspace = "../" + tail
    bottom = _line("repo " + workspace, budget + 5, "#ff9a52")
    bottom.append(" " * max(1, width - bottom.cell_len - context_row.cell_len))
    bottom.append_text(context_row)
    bottom.truncate(width, overflow='crop')
    return [top, bottom]
