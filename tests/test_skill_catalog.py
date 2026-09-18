"""Discover active installed skill packages without executing plugin code."""
import json
from pathlib import Path

from dream import config, plugins
from dream.skills import loader


def _package(root, name, version="1.0.0"):
    package = root / version
    (package / ".codex-plugin").mkdir(parents=True)
    (package / ".codex-plugin/plugin.json").write_text(json.dumps({"name": name, "skills": "./skills"}))
    skill = package / "skills" / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Version {version}\n---\nSteps")
    return package


def _home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("DREAM_SKILL_DIRS", raising=False)
    monkeypatch.setenv("DREAM_EXTERNAL_SKILLS", "1")
    monkeypatch.setattr(config, "SKILL_DIR_PATTERNS", [])
    monkeypatch.setattr(plugins, "_LOADED", [])
    codex = tmp_path / ".codex"
    codex.mkdir()
    return codex


def test_agent_skills_and_enabled_plugin_current_version_are_discovered(tmp_path, monkeypatch):
    codex = _home(tmp_path, monkeypatch)
    agents = tmp_path / ".agents/skills/authoring"
    agents.mkdir(parents=True)
    (agents / "SKILL.md").write_text("---\nname: authoring\ndescription: Shared skill\n---\nSteps")
    cache = codex / "plugins/cache/official"
    _package(cache / "drawing", "drawing", "1.9.0")
    current = _package(cache / "drawing", "drawing", "1.10.0")
    _package(cache / "disabled", "disabled")
    _package(cache / "uninstalled", "uninstalled")
    (codex / "config.toml").write_text('[plugins."drawing@official"]\nenabled = true\n[plugins."disabled@official"]\nenabled = false\n')
    roots = config.skill_dirs()
    found, _ = loader.discover(roots)
    assert {s.name for s in found} == {"authoring", "drawing"}
    assert current / "skills" in roots
    assert len([p for p in roots if "drawing" in p.parts]) == 1


def test_remote_install_marker_loads_skills_unless_explicitly_disabled(tmp_path, monkeypatch):
    codex = _home(tmp_path, monkeypatch)
    root = codex / "plugins/cache/openai-curated-remote/remote"
    package = _package(root, "remote")
    (root / ".codex-remote-plugin-install.json").write_text('{"schema_version":1,"remote_plugin_id":"test"}')
    assert package / "skills" in config.skill_dirs()
    (codex / "config.toml").write_text('[plugins."remote@openai-curated-remote"]\nenabled = false\n')
    assert package / "skills" not in config.skill_dirs()


def test_explicit_skill_roots_do_not_add_external_catalog(tmp_path, monkeypatch):
    codex = _home(tmp_path, monkeypatch)
    _package(codex / "plugins/cache/official/drawing", "drawing")
    (codex / "config.toml").write_text('[plugins."drawing@official"]\nenabled = true\n')
    selected = tmp_path / "selected"
    selected.mkdir()
    monkeypatch.setenv("DREAM_SKILL_DIRS", str(selected))
    assert config.skill_dirs() == [selected]


def test_plugin_skill_path_cannot_escape_its_package(tmp_path, monkeypatch):
    codex = _home(tmp_path, monkeypatch)
    package = _package(codex / "plugins/cache/official/drawing", "drawing")
    (package / ".codex-plugin/plugin.json").write_text('{"skills":"../../../../../../private"}')
    (tmp_path / "private").mkdir()
    (codex / "config.toml").write_text('[plugins."drawing@official"]\nenabled = true\n')
    assert tmp_path / "private" not in config.skill_dirs()
