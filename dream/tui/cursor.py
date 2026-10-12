"""Up and Down inside a wrapped draft (the owner, 2026-10-10: [feedback]).

prompt_toolkit's own Up and Down move by the draft's lines, so a long one-line draft that wraps across several screen
rows jumped straight to the message history. These move one SCREEN row, keeping the column, and leave the old behaviour
(history) to the top and bottom rows.
"""
from __future__ import annotations


def row_target(window, step: int) -> tuple[int, int] | None:
    """(line, column) one screen row above (step -1) or below (step +1) the cursor, or None at the draft's top or
    bottom row, or when the window has not been drawn. `window` is the prompt's input window: its last draw says
    which text sits on which screen row; the prompt's own words before the text count as screen columns there."""
    info = getattr(window, 'render_info', None)
    processed = getattr(getattr(window, 'content', None), '_last_get_processed_line', None)
    if info is None or processed is None or step not in (-1, 1):
        return None
    try:
        starts = info.visible_line_to_row_col          # screen row -> (line, screen column its text starts at)
        y, x = info.cursor_position.y, info.cursor_position.x
        if y + step not in starts:
            return None
        line, first = starts[y + step]
        to_text = processed(line).display_to_source
        left = info._rowcol_to_yx.get((line, first), (0, 0))[1]   # where that row begins on screen
        following = starts.get(y + step + 1)
        column = to_text(first + max(0, x - left))
        if following and following[0] == line:
            column = min(column, to_text(following[1]) - 1)
        return line, max(to_text(first), column)
    except (AttributeError, KeyError, IndexError, TypeError):
        return None                                    # a prompt_toolkit that draws differently: keep the old keys


def move(buffer, window, step: int) -> bool:
    """Move `buffer`'s cursor one screen row; False when there is no row there (the caller keeps the old keys)."""
    target = row_target(window, step)
    if target is None:
        return False
    line, column = target
    lines = buffer.document.lines
    column = min(column, len(lines[line])) if line < len(lines) else column
    buffer.cursor_position = buffer.document.translate_row_col_to_index(line, column)
    return True
