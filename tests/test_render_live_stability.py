"""Real Rich control sequences consumed by the shipped xterm.js emulator."""
from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest
from rich.console import Console
from rich.text import Text

from dream.tui.render import Renderer


@pytest.fixture
def terminal(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    vendor = Path(__file__).resolve().parents[1] / 'dream/gui/static/vendor/xterm.js'
    script = r"""
        global.self = global;
        const {Terminal} = require(process.argv[1]);
        const term = new Terminal({cols: 60, rows: 12, scrollback: 10000});
        const input = require('readline').createInterface({input: process.stdin});
        input.on('line', async line => {
            const command = JSON.parse(line);
            if (command.resize) term.resize(...command.resize);
            await new Promise(resolve => term.write(command.data || '', resolve));
            const lines = Array.from({length: term.buffer.active.length}, (_, i) =>
                term.buffer.active.getLine(i).translateToString(true));
            process.stdout.write(JSON.stringify(lines.join('\n')) + '\n');
        });
    """
    process = subprocess.Popen(['node', '-e', script, str(vendor)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True)
    output = io.StringIO()
    console = Console(file=output, force_terminal=True, width=60, height=12)
    renderer = Renderer(console)
    renderer.live_begin(lambda: Text('STATUS working', no_wrap=True, overflow='ellipsis'))
    # Deterministic refreshes; a separate test exercises the refresh thread.
    renderer._live._refresh_thread.stop()

    class Terminal:
        r = renderer

        def exchange(self, command):
            process.stdin.write(json.dumps(command) + '\n')
            process.stdin.flush()
            return json.loads(process.stdout.readline())

        def read(self):
            data = output.getvalue().replace('\n', '\r\n')  # PTY ONLCR
            output.seek(0)
            output.truncate()
            return self.exchange({'data': data})

        def resize(self, cols, rows):
            self.read()
            self.exchange({'resize': [cols, rows]})
            console.width, console.height = cols, rows
            renderer._live.refresh()
            return self.read()

    try:
        yield Terminal()
    finally:
        renderer.live_end()
        process.stdin.close()
        process.wait(timeout=5)
        process.stdout.close()


def test_committed_line_is_not_repainted_from_cached_partial(terminal):
    r = terminal.r
    r.assistant_delta('UNIQUE_COMMITTED_ANSWER')
    r._live.refresh()
    terminal.read()
    r.assistant_delta('\n')
    screen = terminal.read()
    assert screen.count('UNIQUE_COMMITTED_ANSWER') == 1
    r.live_end()
    assert terminal.read().count('UNIQUE_COMMITTED_ANSWER') == 1


def test_long_unbroken_answer_survives_live_end(terminal):
    r = terminal.r
    payload = ''.join(f'item{i:04d}|' for i in range(400))
    for start in range(0, len(payload), 37):
        r.assistant_delta(payload[start:start + 37])
        r._live.refresh()
        assert 'STATUS working' in terminal.read()
    r.end_line()
    r.live_end()
    screen = ''.join(terminal.read().split())
    assert payload in screen
    assert screen.count('item0000|') == 1
    assert screen.count('item0399|') == 1
    assert 'STATUS' not in screen


def test_resize_and_permission_resume_keep_transcript_once(terminal):
    r = terminal.r
    r.assistant_delta('BEFORE_RESIZE ' + 'content ' * 16)
    r._live.refresh()
    terminal.read()
    terminal.resize(25, 6)
    terminal.resize(90, 18)
    r.live_pause()
    r.permission_request('read_file', 'PERMISSION_MARKER')
    terminal.read()
    r.live_resume()
    r._live._refresh_thread.stop()
    r.assistant_delta('AFTER_PERMISSION')
    r.end_line()
    r.live_end()
    screen = ''.join(terminal.read().split())
    assert screen.count('BEFORE_RESIZE') == 1
    assert screen.count('content') == 16
    assert screen.count('AFTER_PERMISSION') == 1
    assert screen.count('PERMISSION_MARKER') == 1
    assert 'STATUS' not in screen


@pytest.mark.parametrize('cols,rows', [(25, 14), (18, 16), (42, 10)])
def test_resize_preserves_history_with_wide_two_row_footer(terminal, cols, rows):
    from rich.console import Group

    r = terminal.r
    r._status_cb = lambda: Group(Text('STATUS ' + 's' * 48, no_wrap=True, overflow='ellipsis'),
                                 Text('DETAIL ' + 'd' * 48, no_wrap=True, overflow='ellipsis'))
    r.console.print('HISTORY_SENTINEL')
    r.assistant_delta('RESIZE_ANSWER ' + 'word ' * 25)
    r._live.refresh()
    terminal.read()
    terminal.resize(cols, rows)
    terminal.resize(90, 18)
    r.end_line()
    r.live_end()
    screen = ''.join(terminal.read().split())
    assert screen.count('HISTORY_SENTINEL') == 1
    assert screen.count('RESIZE_ANSWER') == 1
    assert screen.count('word') == 25
    assert 'STATUS' not in screen and 'DETAIL' not in screen


def test_ephemeral_and_persistent_thoughts_across_permission(terminal):
    r = terminal.r
    r.set_thoughts_persistent(False)
    r.thinking_delta('HIDDEN_THOUGHT ' * 300)
    r._live.refresh()
    terminal.read()
    r.live_pause()
    r.permission_request('read_file', 'ALLOW_READ')
    r.live_resume()
    r._live._refresh_thread.stop()
    r.assistant_delta('VISIBLE_ANSWER')
    r.end_line()
    r.set_thoughts_persistent(True)
    r.thinking_delta('PERSISTENT_THOUGHT')
    r._live.refresh()
    terminal.read()
    r.assistant_delta('SECOND_ANSWER')
    r.live_end()
    screen = ''.join(terminal.read().split())
    assert 'HIDDEN_THOUGHT' not in screen
    assert screen.count('VISIBLE_ANSWER') == 1
    assert screen.count('PERSISTENT_THOUGHT') == 1
    assert screen.count('SECOND_ANSWER') == 1


def test_refresh_races_with_line_commit_without_loss_or_duplicate(terminal):
    import threading

    r = terminal.r
    stop = threading.Event()
    failures = []

    def refresh():
        try:
            while not stop.wait(0.001):
                r._live.refresh()
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=refresh, daemon=True)
    thread.start()
    try:
        for i in range(100):
            r.assistant_delta(f'LINE_{i:04d}_BEGIN')
            r.assistant_delta('_END\n')
        r.assistant_delta('FINAL_PARTIAL')
    finally:
        stop.set()
        thread.join(timeout=3)
    assert not thread.is_alive() and not failures
    r.live_end()
    screen = ''.join(terminal.read().split())
    for i in range(100):
        assert screen.count(f'LINE_{i:04d}_BEGIN_END') == 1
    assert screen.count('FINAL_PARTIAL') == 1
    assert 'STATUS' not in screen


def test_actual_pty_long_answer_error_and_cleanup(terminal):
    import errno
    import fcntl
    import os
    import pty
    import struct
    import sys
    import termios

    terminal.r.live_end()
    terminal.read()
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 12, 60, 0, 0))
    script = '''
from dream.tui.render import Renderer
from rich.text import Text
r = Renderer()
r.live_begin(lambda: Text('PTY_STATUS'))
for i in range(250):
    r.assistant_delta(f'pty{i:04d}|')
    if i % 10 == 0:
        r._live.refresh()
r.error('EXPECTED_RENDER_ERROR')
r.live_end()
print('PTY_CLEAN_EXIT')
'''
    child = subprocess.Popen([sys.executable, '-c', script], stdin=slave, stdout=slave,
                             stderr=slave, close_fds=True)
    os.close(slave)
    chunks = []
    try:
        while True:
            try:
                data = os.read(master, 65536)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    break
                raise
            if not data:
                break
            chunks.append(data)
        assert child.wait(timeout=5) == 0
    finally:
        os.close(master)
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
    screen = ''.join(terminal.exchange({'data': b''.join(chunks).decode()}).split())
    payload = ''.join(f'pty{i:04d}|' for i in range(250))
    assert payload in screen
    assert screen.count('pty0000|') == 1
    assert screen.count('pty0249|') == 1
    assert screen.count('EXPECTED_RENDER_ERROR') == 1
    assert screen.count('PTY_CLEAN_EXIT') == 1
    assert 'PTY_STATUS' not in screen


def test_inflight_refresh_and_commit_are_one_transaction(terminal):
    import threading

    r = terminal.r
    r.assistant_delta('RACING_ANSWER')
    entered, release, committed = threading.Event(), threading.Event(), threading.Event()

    def footer():
        if threading.current_thread().name == 'fixture-refresh':
            entered.set()
            assert release.wait(3)
        return Text('STATUS working')

    r._status_cb = footer
    refresh = threading.Thread(target=r._live.refresh, name='fixture-refresh', daemon=True)
    refresh.start()
    assert entered.wait(3)

    def commit():
        r.assistant_delta('\n')
        committed.set()

    writer = threading.Thread(target=commit, daemon=True)
    writer.start()
    try:
        assert not committed.wait(0.02)
    finally:
        release.set()
        refresh.join(timeout=3)
        writer.join(timeout=3)
    assert not refresh.is_alive() and not writer.is_alive()
    assert committed.is_set()
    assert terminal.read().count('RACING_ANSWER') == 1


def test_next_turn_starts_fresh_and_footer_failure_does_not_lose_answer(terminal):
    r = terminal.r
    r.assistant_delta('FIRST_TURN')
    r.live_end()
    terminal.read()

    def broken_footer():
        raise ValueError('status unavailable')

    r.live_begin(broken_footer)
    r._live._refresh_thread.stop()
    r.assistant_delta('SECOND_TURN')
    r.live_end()
    screen = terminal.read()
    assert screen.count('FIRST_TURN') == 1
    assert screen.count('SECOND_TURN') == 1
    assert screen.count('●') == 2   # each turn's message opens with one bullet


@pytest.mark.parametrize('glyph,count', [
    ('界', 41), ('界\u0301', 41), ('e\u0301', 82),
    ('界😀', 20), ('界👩‍💻', 20),
    ('⌚', 41), ('😀', 41), ('👩‍💻', 41), ('❤️', 41),
])
def test_unicode_resize_keeps_complete_answer_once(terminal, glyph, count):
    from rich.console import Group
    from dream.tui.status import minimal_status

    r = terminal.r
    terminal.resize(83, 24)
    r._status_cb = lambda: Group(*minimal_status({
        'mode_label': 'asking each change', 'mode_color': 'cyan',
        'effort': 'default', 'ws': 'fixture', 'context': 'ctx 0/32k',
    }, r.console.width))
    r.assistant_delta('HEADER\n' + glyph * count)
    r._live.refresh()
    terminal.read()
    terminal.resize(41, 24)
    r.live_end()
    screen = terminal.read()
    assert screen.count('HEADER') == 1
    # the answer sits two cells in, so a row can end between the two glyphs of a pair: count across rows
    assert ''.join(screen.split()).count(glyph) == count
    assert 'asking each change' not in screen


def test_unicode_footer_is_escaped_only_in_transient_frame(terminal):
    from rich.console import Group
    from rich.segment import Segment
    from dream.tui.status import minimal_status

    r = terminal.r
    terminal.resize(83, 24)
    workspace = 'project-⌚-界-😀'
    r._status_cb = lambda: Group(*minimal_status({
        'mode_label': 'ask', 'mode_color': 'cyan', 'effort': 'default',
        'ws': workspace, 'context': 'ctx 0/32k',
    }, r.console.width))
    r.console.print('FOOTER_HISTORY')
    r.assistant_delta('HEADER\noriginal ⌚ 界 😀 👩‍💻 ❤️')
    r._live.refresh()
    transient = list(r.console.render(r._live_view()))
    assert all(segment.text.isascii() for segment in transient if not segment.control)
    plain = ''.join(segment.text for segment in transient if not segment.control)
    assert 'project-' in plain and r'\u231a' in plain and r'\u754c' in plain
    assert any(segment.style for segment in transient)
    terminal.read()
    terminal.resize(41, 24)
    r.live_end()
    screen = terminal.read()
    assert screen.count('FOOTER_HISTORY') == 1
    assert screen.count('HEADER') == 1
    for glyph in ['⌚', '界', '😀', '👩‍💻', '❤️']:
        assert screen.count(glyph) == 1
    assert r'\u231a' not in screen and 'project-' not in screen


@pytest.mark.parametrize('chunk_size', [1, 2, 7, 10000])
def test_assistant_colors_preserve_literal_content_across_chunks(terminal, monkeypatch, chunk_size):
    r = terminal.r
    terminal.resize(120, 24)
    committed = []
    real_hang = r._hang

    def capture(prefix, body):
        whole = prefix.copy()
        whole.append_text(body)
        committed.append(whole[2:])      # without the bullet or the two-cell indent
        return real_hang(prefix, body)

    monkeypatch.setattr(r, '_hang', capture)
    answer = ('## Findings\n- Keep `values[0]` unchanged.\n'
              '1. Preserve [red]literal[/red] and `print("[ok]")`.\n'
              '**Next step**\nUnstyled body.\n')
    for start in range(0, len(answer), chunk_size):
        r.assistant_delta(answer[start:start + chunk_size])
        r._live.refresh()
    r.live_end()
    screen = terminal.read()
    # a bullet opens the message and every line sits two cells in; the markdown markers are gone (the owner,
    # 2026-10-09: "fix the markdown too"), while text that only looks like Rich markup stays as written
    assert ('● Findings\n  • Keep values[0] unchanged.\n'
            '  1. Preserve [red]literal[/red] and print("[ok]").\n  Next step\n  Unstyled body.') in screen
    lines = {line.plain: line for line in committed}

    def color(line, token):
        return line.get_style_at_offset(r.console, line.plain.index(token)).color

    heading = lines['Findings']
    bullet = lines['• Keep values[0] unchanged.']
    ordered = lines['1. Preserve [red]literal[/red] and print("[ok]").']
    assert color(heading, 'Findings').get_truecolor() == (238, 139, 66)
    assert color(bullet, '•').get_truecolor() == (237, 189, 98)
    assert color(bullet, 'values[0]').get_truecolor() == (237, 189, 98)
    assert color(ordered, '1.').get_truecolor() == (237, 189, 98)
    assert color(ordered, 'print').get_truecolor() == (237, 189, 98)
    assert color(ordered, '[red]') is None
    assert color(lines['Next step'], 'Next').get_truecolor() == (238, 139, 66)
    assert not lines['Unstyled body.'].spans


def test_assistant_preview_color_and_thought_semantics_stay_separate(terminal, monkeypatch):
    r = terminal.r
    r.assistant_delta('## Live heading')
    segments = list(r.console.render(r._live_view()))
    heading = next(segment for segment in segments if 'Live heading' in segment.text)
    assert heading.style.bold
    assert heading.style.color.get_truecolor() == (238, 139, 66)
    r.end_line()
    r.set_thoughts_persistent(True)
    committed = []
    real_print = r.console.print

    def capture(*objects, **kwargs):
        if len(objects) == 1 and isinstance(objects[0], Text):
            committed.append(objects[0].copy())
        return real_print(*objects, **kwargs)

    monkeypatch.setattr(r.console, 'print', capture)
    r.thinking_delta('## Reasoning with `literal[0]`\n')
    thought = next(line for line in committed if 'Reasoning' in line.plain)
    style = thought.get_style_at_offset(r.console, thought.plain.index('Reasoning'))
    assert style.dim and style.italic
    assert style.color is None
    r.live_end()
