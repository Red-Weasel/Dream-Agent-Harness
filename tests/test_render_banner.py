"""DREAM-153: the TUI banner shows the package version, dream.__version__, even when the
installed distribution's metadata says otherwise (an editable install keeps its old
dist-info until it is reinstalled)."""
import importlib.metadata

from rich.console import Console

import dream
from dream.tui.render import Renderer


def test_banner_shows_the_package_version_not_the_installed_metadata(monkeypatch):
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.1.0")
    console = Console(record=True, force_terminal=True, width=100)
    Renderer(console).show_logo()
    assert f"v{dream.__version__}" in console.export_text()
