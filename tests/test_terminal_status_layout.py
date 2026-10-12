"""Minimal Workflow status stays readable and inside the terminal cell budget."""
from types import SimpleNamespace

import pytest
from prompt_toolkit.formatted_text import HTML, to_formatted_text
from prompt_toolkit.formatted_text.utils import fragment_list_to_text
from rich.cells import cell_len

from dream.tui.status import minimal_status


@pytest.mark.parametrize('width', [12, 24, 40, 80, 130])
@pytest.mark.parametrize('busy', [False, True])
def test_status_is_two_cell_bounded_rows_with_unicode_and_long_paths(width, busy):
    data = dict(mode_label='asking each change', mode_color='#22d3ee',
                effort_short='high', ws='~/very-long-parent/' * 10 + '项目-repo',
                gerund='Working' if busy else None, elapsed=15)
    rows = minimal_status(data, width)
    assert len(rows) == 2
    assert all(cell_len(row.plain) <= width and '\n' not in row.plain for row in rows)
    assert rows[0].plain.endswith('high')
    if width >= 40:
        assert '项目-repo' in rows[1].plain
        if busy and width >= 80:
            assert 'Working' in rows[0].plain


def test_app_defaults_to_minimal_status_with_workspace_and_effort(tmp_path, monkeypatch):
    from dream.tui.app import App
    app = App(provider='machx', model='fixture', workspace=tmp_path)
    app.engine = SimpleNamespace(store=object())
    data = dict(mode_label='asking each change', mode_color='#22d3ee',
                effort_short='medium', ws='~/sample-repo', tools=100, context='ctx 15k/262k (6%)',
                mind_plain='many memories', monitor_compact='many GPU rows')
    monkeypatch.setattr(app, '_status_data', lambda: data)
    assert app.show_tasks is False
    plain = fragment_list_to_text(to_formatted_text(app._toolbar()))
    lines = plain.splitlines()
    # The prompt box's bottom line (2026-10-10), carrying the prompt optimizer's buttons (2026-10-11), then the two rows.
    assert len(lines) == 3 and set(lines[0][:10]) == {'─'} and '[ Optimizer: ' in lines[0]
    assert 'sample-repo' in plain and 'medium' in plain
    assert '15k/262k (6%)' in plain
    assert 'GPU' not in plain and 'memories' not in plain
    # Working keeps the same prompt box (2026-10-11): its top line and the prompt above that bottom line and two rows.
    rows = [row.plain for row in app._footer().renderables]
    assert len(rows) == 5 and rows[1].startswith('you › ') and '[ Optimizer: ' in rows[2]


def test_expanded_status_clips_wide_names_before_the_monitor_column():
    from dream.tui.monitor import compose_pt, pt_lines
    left = '<style fg="#a78bfa">' + '界' * 90 + '&lt;repo&gt;</style>'
    body = compose_pt([(left, 1)], pt_lines([[('GPU ' + '界' * 50, '#ffffff')]]), 100)
    plain = fragment_list_to_text(to_formatted_text(HTML(body)))
    assert cell_len(plain) <= 100
    assert 'GPU' in plain
    assert plain.count('界') < 140


@pytest.mark.parametrize('width,workspace', [(80, '项目-repo'), (40, 'sample-repo')])
def test_live_status_budgets_escaped_paths_without_hiding_context_or_effort(width, workspace):
    data = dict(mode_label='asking each change', mode_color='#f1a76a', effort_short='high',
                ws=workspace, context='ctx 15k/262k (6%)', gerund='Working', elapsed=12)
    rows = minimal_status(data, width, ascii_only=True)
    assert all(row.plain.isascii() and row.cell_len <= width for row in rows)
    assert rows[0].plain.endswith('high')
    assert rows[1].plain.endswith('ctx 15k/262k (6%)')
    if width == 80:
        assert r'\u9879\u76ee-repo' in rows[1].plain


def test_idle_status_preserves_semantic_colors_and_only_repo_folder(tmp_path, monkeypatch):
    from dream.tui.app import App
    app = App(provider='machx', model='fixture', workspace=tmp_path)
    app.engine = SimpleNamespace(store=object())
    monkeypatch.setattr(app, '_status_data', lambda: dict(
        mode_label='asking each change', mode_color='#f1a76a', effort_short='high',
        ws='/parent/private/path/sample-repo', context='ctx 15k/262k (6%)'))
    fragments = to_formatted_text(app._toolbar())
    plain = fragment_list_to_text(fragments)
    assert 'sample-repo' in plain and '/parent/' not in plain
    assert any('#edbd62' in style and 'high' in text for style, text in fragments)
    assert any('#ff9a52' in style and 'sample-repo' in text for style, text in fragments)
    assert any('#f5cf70' in style and 'ctx' in text for style, text in fragments)


@pytest.mark.parametrize('width', [40, 80])
def test_confirmed_files_take_priority_over_activity_and_tools(width):
    rows = minimal_status(dict(mode_label='asking each change', mode_color='#f1a76a',
        effort_short='high', ws='/parent/repo', context='ctx 6%', files_changed=3,
        tools=12, gerund='Working', elapsed=10), width, ascii_only=True)
    assert 'files 3' in rows[0].plain and rows[0].plain.endswith('high')
    assert ('tools 12' in rows[0].plain) == (width >= 76)
    assert all(row.cell_len <= width for row in rows)


@pytest.mark.asyncio
async def test_changes_command_lists_confirmed_paths_literally(tmp_path):
    import io
    from rich.console import Console
    from dream.tui.app import App
    app = App(provider='machx', model='fixture', workspace=tmp_path)
    app.engine = SimpleNamespace(store=object())
    app.changed_files = {'[red]file.txt', 'ok.py'}
    app.tool_count = 7
    output = io.StringIO()
    app.renderer.console = Console(file=output, width=120, color_system=None)
    assert await app._command('/changes') is False
    text = output.getvalue()
    assert 'Files changed: 2' in text and 'Tool calls: 7' in text
    assert '[red]file.txt' in text and 'ok.py' in text
    assert 'Shell, recursive folder and external edits are not counted' in ' '.join(text.split())
