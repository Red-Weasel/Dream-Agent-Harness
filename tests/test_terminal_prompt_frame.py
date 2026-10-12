"""The prompt box and the tile's Chat controls on Dream's terminal (the owner, 2026-10-10).

The prompt sits between two full-width lines, as in Claude Code: one above it (between turns) and one atop the
status rows under it, idle and working alike so the tile never jumps. Read x2 and the context figure reach a
DeepSleep tile through the terminal's own control route."""
from types import SimpleNamespace

from prompt_toolkit.formatted_text import fragment_list_to_text, to_formatted_text

from dream.gui.server import READ_AGAIN

STATUS = dict(mode_label='auto mode on', mode_color='#fbbf24', effort_short='high', ws='~/sample-repo',
              context='ctx 15k/262k (6%)')


def app_with_status(tmp_path, monkeypatch, width=60):
    import shutil
    from dream.tui.app import App
    app = App(provider='machx', model='fixture', workspace=tmp_path)
    app.engine = SimpleNamespace(store=object())
    monkeypatch.setattr(app, '_status_data', lambda: dict(STATUS))
    monkeypatch.setattr(shutil, 'get_terminal_size', lambda fallback=(80, 24): __import__('os').terminal_size((width, 30)))
    return app


def test_a_full_width_line_closes_the_prompt_box_above_the_status_rows(tmp_path, monkeypatch):
    app = app_with_status(tmp_path, monkeypatch)
    app.prompt_optimizer = True
    lines = fragment_list_to_text(to_formatted_text(app._toolbar())).splitlines()
    # The bottom line carries the prompt optimizer's two buttons near its right end (2026-10-11).
    assert len(lines) == 3 and lines[0] == '─' * 19 + ' [ Optimizer: on ]  [ Skip next: off ] ' + '──', lines
    assert 'auto mode on' in lines[1] and 'sample-repo' in lines[2] and '15k/262k' in lines[2]
    # The same box while a turn works (2026-10-11): its top line, the prompt, the labelled bottom line, the status.
    working = [r.plain for r in app._footer().renderables]
    assert len(working) == 5 and set(working[0]) == {'─'} and working[1].startswith('you › '), working
    assert working[2].endswith(' [ Optimizer: on ]  [ Skip next: off ] ──') and set(working[2][:10]) == {'─'}


def test_read_twice_doubles_only_what_is_typed_in_the_terminal(tmp_path, monkeypatch):
    app = app_with_status(tmp_path, monkeypatch)
    assert app._read_twice('Fix the cache key') == 'Fix the cache key'           # off by default
    import asyncio
    assert asyncio.run(app._runtime_control({'action': 'read_twice'})) == {'read_twice': True}
    assert app._read_twice('Fix the cache key') == 'Fix the cache key' + READ_AGAIN + 'Fix the cache key'
    assert app._read_twice('/model') == '/model'                                  # commands stay as typed
    assert asyncio.run(app._runtime_control({'action': 'read_twice'})) == {'read_twice': False}


def test_the_tile_reads_mode_read_twice_and_context_in_one_call(tmp_path, monkeypatch):
    app = app_with_status(tmp_path, monkeypatch)
    import asyncio
    status = asyncio.run(app._runtime_control({'action': 'tile_status'}))
    assert status['mode'] in status['modes'] and status['labels'][status['mode']]
    assert status['read_twice'] is False and status['context'] == 'ctx 15k/262k (6%)'
