"""Turn a session's log into something you can read, paste, or keep.

Selecting a long session out of the terminal does not work — the moment the
selection scrolls, prompt_toolkit repaints and the selection is gone — and
scraping a screen was never the right way to get at data the harness already
writes down. Every turn is appended to ``data/sessions/<id>.jsonl`` as it
happens, so an export is a format change, not a capture.

Markdown by default because the thing people do with a session is paste it
somewhere. ``--json`` hands back the raw log for anything programmatic.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

# A tool result can be a whole file. Full text goes to the log; an export meant
# for reading gets the head, marked. --full overrides.
RESULT_CLIP = 2000


def read_log(path: str | Path) -> Iterator[dict[str, Any]]:
    """Events from a session jsonl, skipping anything unparseable — a truncated
    final line (the session is still being written) must not lose the rest."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict):
                yield ev


def _ts(raw: Any) -> str:
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(str(raw)).strftime("%H:%M:%S")
    except ValueError:
        return str(raw)[:19]


def to_markdown(events: list[dict[str, Any]], *, title: str = "Dream session",
                full: bool = False, tools: bool = True) -> str:
    """Render a session as markdown."""
    out: list[str] = [f"# {title}", ""]
    counts: dict[str, int] = {}
    for ev in events:
        counts[str(ev.get("role") or "?")] = counts.get(str(ev.get("role") or "?"), 0) + 1
    stamps = [_ts(e.get("ts")) for e in events if e.get("ts")]
    if stamps:
        out.append(f"*{len(events)} events · {stamps[0]}–{stamps[-1]}*")
        out.append("")

    for ev in events:
        role = str(ev.get("role") or "")
        body = ev.get("content")
        body = "" if body is None else str(body)
        stamp = _ts(ev.get("ts"))
        if role == "user":
            out += [f"## You · {stamp}", "", body.strip(), ""]
        elif role == "assistant":
            out += [f"## Dream · {stamp}", "", body.strip(), ""]
        elif role == "tool_use":
            if not tools:
                continue
            out += [f"<details><summary>🔧 {ev.get('tool') or 'tool'}</summary>", "",
                    "```json", body.strip()[: (10**9 if full else 4000)], "```", ""]
        elif role == "tool_result":
            if not tools:
                continue
            clipped = body if full or len(body) <= RESULT_CLIP else (
                body[:RESULT_CLIP] + f"\n… [{len(body) - RESULT_CLIP} more chars]")
            out += ["```", clipped.rstrip(), "```", "</details>", ""]
        elif body.strip():
            out += [f"> *{role}:* {body.strip()}", ""]
    return "\n".join(out).rstrip() + "\n"


def export_session(log_path: str | Path, dest: str | Path, *, fmt: str = "md",
                   full: bool = False, tools: bool = True,
                   title: str | None = None) -> tuple[Path, int]:
    """Write the session at ``log_path`` to ``dest``. Returns (path, n_events).
    ``title`` (set_project_title) replaces the default "Dream session <stem>"."""
    events = list(read_log(log_path))
    dest = Path(dest).expanduser()
    if dest.is_dir():
        dest = dest / (Path(log_path).stem + (".md" if fmt == "md" else ".json"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        dest.write_text(json.dumps(events, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        dest.write_text(
            to_markdown(events, title=title or f"Dream session {Path(log_path).stem}",
                        full=full, tools=tools),
            encoding="utf-8")
    return dest, len(events)
