from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from dream import config
from dream.desktop import startup
from dream.tui import app


@pytest.mark.asyncio
async def test_desktop_initializes_project_before_provider_app(monkeypatch, tmp_path):
    observed = []
    def construct(**kwargs):
        observed.append((tmp_path / 'DREAM.md').read_text())
        return SimpleNamespace(run=AsyncMock())
    monkeypatch.setattr(app, 'App', construct)
    await startup.run({'workspace': str(tmp_path), 'choice': {'kind': 'provider', 'provider': 'codex'}},
                      tmp_path / 'status.json')
    assert observed and 'canonical project instruction source' in observed[0]


def test_terminal_initializes_at_construction_without_engine_start(tmp_path):
    instance = app.App(provider='codex', workspace=tmp_path, gui=False)
    assert instance.engine is None
    assert (tmp_path / 'DREAM.md').is_file()
    assert instance.project_instruction_warnings == []


@pytest.mark.asyncio
async def test_readonly_project_warning_is_retained_and_does_not_prevent_startup(monkeypatch, tmp_path):
    root = tmp_path / 'readonly'; root.mkdir(mode=0o500)
    published = []
    monkeypatch.setattr(startup, 'publish', lambda path, state, message: published.append(message))
    monkeypatch.setattr(app, 'App', lambda **kwargs: SimpleNamespace(run=AsyncMock()))
    try:
        await startup.run({'workspace': str(root), 'choice': {'kind': 'provider', 'provider': 'codex'}},
                          tmp_path / 'status.json')
        assert any('read_only_directory' in message for message in published)
        assert not (root / 'DREAM.md').exists()
    finally:
        root.chmod(0o700)


def test_terminal_retains_visible_instruction_diagnostics(tmp_path):
    (tmp_path / 'DREAM.md').symlink_to(tmp_path / 'missing')
    instance = app.App(provider='codex', workspace=tmp_path, gui=False)
    assert any('symlink' in message for message in instance.project_instruction_warnings)
    assert instance.engine is None


@pytest.mark.asyncio
async def test_invalid_provider_selection_does_not_create_project_files(tmp_path):
    with pytest.raises(ValueError, match='supported provider'):
        await startup.run({'workspace': str(tmp_path), 'choice': {'kind': 'provider', 'provider': 'invalid'}},
                          tmp_path / 'status.json')
    assert not (tmp_path / 'DREAM.md').exists()


from test_project_library_lifecycle import app as project_app


@pytest.mark.asyncio
async def test_project_switch_initializes_before_new_engine(project_app, tmp_path, monkeypatch):
    import asyncio
    from dream.projects.library import ProjectLibrary
    target = tmp_path / 'new-project'; target.mkdir()
    project = ProjectLibrary().create('New', str(target))
    original = project_app._boot_engine
    def boot():
        assert (target / 'DREAM.md').is_file()
        return original()
    monkeypatch.setattr(project_app, '_boot_engine', boot)
    waiter = asyncio.create_task(project_app._runtime_control({'action': 'project_open', 'project_id': project['id']}))
    request = await asyncio.wait_for(project_app._gui_prompts.get(), 1)
    project_app.owner = asyncio.current_task()
    await project_app._execute_council_control(request)
    assert (await waiter)['restoration'] == 'new_chat'
    assert project_app.project_instruction_warnings == []
