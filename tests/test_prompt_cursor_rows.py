"""Up and Down move one screen row inside a wrapped draft (dream.tui.cursor), drawn by prompt_toolkit itself.

The owner, 2026-10-10: in the terminal, only Left and Right moved through a long message; Up jumped to history.
"""
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.layout import BufferControl, Window
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.layout.screen import Screen, WritePosition
from prompt_toolkit.layout.mouse_handlers import MouseHandlers

from dream.tui import cursor

WIDTH = 20


def drawn(text: str, at: int):
    """The draft drawn as the prompt draws it: wrapped at WIDTH, with a two-cell prompt before the first row."""
    buffer = Buffer(document=Document(text, at), multiline=True)
    window = Window(BufferControl(buffer, input_processors=[BeforeInput('> ')]), wrap_lines=True)
    window.write_to_screen(Screen(), MouseHandlers(), WritePosition(0, 0, WIDTH, 10), '', False, None)
    return buffer, window


async def test_up_and_down_walk_the_rows_of_one_long_wrapped_line():
    text = 'abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOP'     # one line, three screen rows at width 20
    buffer, window = drawn(text, 45)                                     # on the third row
    assert cursor.move(buffer, window, -1)
    assert buffer.cursor_position == 25                                # same screen column, one row up
    buffer, window = drawn(text, 25)
    assert cursor.move(buffer, window, -1)
    assert buffer.cursor_position == 5                                 # the first row starts after the 2-cell prompt
    buffer, window = drawn(text, 5)
    assert cursor.move(buffer, window, -1) is False                      # the top row: history keeps the key
    assert cursor.move(buffer, window, 1) and buffer.cursor_position == 25


async def test_down_from_the_last_row_is_left_to_history():
    buffer, window = drawn('short draft', 5)
    assert cursor.move(buffer, window, 1) is False and cursor.move(buffer, window, -1) is False


async def test_rows_across_lines_land_on_the_shorter_line_end():
    text = 'first line\n' + 'x' * 30                                   # the second line wraps onto two rows
    buffer, window = drawn(text, len(text))                              # its end, on its second row
    assert cursor.move(buffer, window, -1)
    assert (buffer.document.cursor_position_row, buffer.document.cursor_position_col) == (1, 10)  # same line, row up
    buffer, window = drawn(text, len('first line\n') + 19)                # far right of the second line's first row
    assert cursor.move(buffer, window, -1)
    assert (buffer.document.cursor_position_row, buffer.document.cursor_position_col) == (0, 10)  # clipped to its end


async def test_nothing_drawn_yet_moves_nothing():
    buffer = Buffer(document=Document('abc', 1), multiline=True)
    never_drawn = Window(BufferControl(buffer))
    assert cursor.move(buffer, never_drawn, -1) is False and buffer.cursor_position == 1
