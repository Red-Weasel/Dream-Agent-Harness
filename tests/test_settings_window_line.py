"""/settings says which context window the session fills and compacts against (the owner, 2026-10-11: the table's
numeric.ctx_window 200000 read as this session's window, while the engine served 262,000). That row is only the
window Dream assumes for Claude when Claude reports none; the line above the table names the real one."""
from types import SimpleNamespace

from rich.console import Console

from dream.tui.render import Renderer


async def test_settings_names_the_window_this_session_compacts_against(tmp_path):
    from dream.tui.app import App
    app = App(provider='machx', model='fixture', workspace=tmp_path)
    app.renderer = Renderer(Console(width=200, record=True))
    app.engine = SimpleNamespace(provider=SimpleNamespace(key='machx'), model='fixture',
                                 backend=SimpleNamespace(_window=lambda: 262000))
    await app._settings_command('')
    text = app.renderer.console.export_text()
    assert "context window in use: 262,000 tokens, as the model server reports it" in text
    assert "numeric.ctx_window below is only Claude's fallback" in text
    assert 'numeric.ctx_window' in text.split('context window in use')[1]          # the row is still listed after it
    app.renderer = Renderer(Console(width=200, record=True))
    await app._settings_command('get numeric.ctx_window')                            # one key: no extra line
    assert 'context window in use' not in app.renderer.console.export_text()
