"""Tweaks: the page exposes controls, the host persists what the user changes.

A tweakable page wraps its defaults in one marked JSON block inside an inline
script:

    const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
      "primaryColor": "#D97757",
      "fontSize": 16
    }/*EDITMODE-END*/;

When the page posts ``__edit_mode_set_keys`` with ``{edits: {fontSize: 18}}``,
the host merges the keys into that block and writes the file back, so the change
survives a reload. The block must be valid JSON (double-quoted keys and strings)
and there must be exactly one in the root file; anything else is refused, never
guessed at.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

BEGIN, END = "/*EDITMODE-BEGIN*/", "/*EDITMODE-END*/"
_BLOCK = re.compile(re.escape(BEGIN) + r"(.*?)" + re.escape(END), re.S)


class TweakError(ValueError):
    pass


def read_block(text: str) -> dict[str, Any]:
    """The current defaults, or a TweakError naming what is wrong."""
    blocks = _BLOCK.findall(text)
    if len(blocks) != 1:
        raise TweakError(f"expected exactly one {BEGIN}…{END} block, found {len(blocks)}")
    try:
        data = json.loads(blocks[0])
    except ValueError as e:
        raise TweakError(f"the EDITMODE block is not valid JSON ({e}); keys and strings "
                         f"must be double-quoted, no trailing commas, no comments") from None
    if not isinstance(data, dict):
        raise TweakError("the EDITMODE block must be a JSON object")
    return data


def apply(text: str, edits: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Merge ``edits`` into the block; return (new text, merged defaults).
    Only the keys in ``edits`` change; the rest of the file is untouched byte
    for byte. Values must be JSON-serializable."""
    if not isinstance(edits, dict):
        raise TweakError("edits must be an object of key → value")
    merged = {**read_block(text), **edits}
    try:
        body = json.dumps(merged, indent=2, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        raise TweakError(f"an edit is not JSON-serializable: {e}") from None
    new = _BLOCK.sub(lambda _m: BEGIN + body + END, text, count=1)
    return new, merged


def apply_to_file(path: Path, edits: dict[str, Any]) -> dict[str, Any]:
    """Read, merge, write. Refuses before writing when the block is bad."""
    with path.open("r", encoding="utf-8", newline="") as fh:  # keep CRLF as CRLF
        text = fh.read()
    new, merged = apply(text, edits)
    if new != text:
        # Atomic: a reader (the hidden frame, a poll, the user's editor) never sees a
        # half-written file with no EDITMODE block. Same directory, so the
        # replace is a rename; the mode of the original is kept.
        import os
        import tempfile

        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(new)
            try:
                os.chmod(tmp, path.stat().st_mode & 0o7777)
            except OSError:
                pass
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    return merged
