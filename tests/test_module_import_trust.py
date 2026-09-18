"""Explicit source approval precedes configurable Python module execution."""
from __future__ import annotations

import hashlib
import json
import os
import py_compile
from pathlib import Path

import pytest

from dream import config, extensions, plugins
from dream.tools import installed_skill_tools, registry


@pytest.fixture(autouse=True)
def roots(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CUSTOM_TOOLS_DIR", tmp_path / "custom")
    monkeypatch.setattr(config, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(config, "MCP_CONFIG_PATH", tmp_path / "mcp.json")
    monkeypatch.setenv("DREAM_SKILL_DIRS", str(tmp_path / "skills"))
    monkeypatch.setenv("DREAM_EXTENSION_SETTINGS", str(tmp_path / "extensions.json"))
    monkeypatch.setattr(extensions, "_RUNTIME", {})
    monkeypatch.setattr(extensions, "_USAGE_WARNINGS", [])
    monkeypatch.setattr(installed_skill_tools, "_CACHE", None)
    plugins.load()
    yield
    plugins.load(tmp_path / "absent")


def write_tool(root, marker, name="example", value="approved"):
    root.mkdir(parents=True, exist_ok=True)
    path = root / (name + ".py")
    path.write_text(
        "from pathlib import Path\nfrom claude_agent_sdk import tool\n"
        f"Path({str(marker)!r}).write_text({value!r})\n"
        f"@tool({name!r}, 'Fixture', {{'type':'object','properties':{{}}}})\n"
        f"async def {name}(args):\n    return {{'content':[{{'type':'text','text':{value!r}}}]}}\n"
    )
    return path


def approve(path, plugin=None):
    identifier = extensions.tool_module_id(path, plugin)
    review = extensions.review_module(identifier)
    extensions.trust_module(identifier, review["sha256"])
    return review


def test_default_or_enabled_module_never_imports_without_review(tmp_path):
    marker = tmp_path / "import-effect"
    path = write_tool(config.CUSTOM_TOOLS_DIR, marker)
    before = path.read_bytes()
    built = registry.build()
    assert "example" not in built["names"] and not marker.exists()
    assert any("import blocked" in warning for warning in built["warnings"])
    result = extensions.set_enabled("tool:custom/example", True)
    assert result["configured_enabled"] and not result["enabled"]
    assert "example" not in registry.build()["names"] and not marker.exists()
    row = next(row for row in extensions.catalog()["extensions"] if row["id"] == "tool:custom/example")
    assert row["configured_enabled"] and not row["enabled"] and row["trust_state"] == "required"
    assert path.read_bytes() == before and extensions.settings()["trusted_modules"] == {}


def test_review_is_read_only_and_trust_does_not_change_disabled_override(tmp_path):
    marker = tmp_path / "effect"
    path = write_tool(config.CUSTOM_TOOLS_DIR, marker)
    review = extensions.review_module("tool:custom/example")
    assert review["source"] == path.read_text()
    assert review["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert not marker.exists() and not extensions.settings_path().exists()
    extensions.set_enabled(review["id"], False)
    extensions.trust_module(review["id"], review["sha256"])
    assert extensions.settings()["overrides"][review["id"]] is False
    assert "example" not in registry.build()["names"] and not marker.exists()
    extensions.set_enabled(review["id"], True)
    assert "example" in registry.build()["names"] and marker.read_text() == "approved"


def test_stale_review_cannot_approve_changed_source(tmp_path):
    path = write_tool(config.CUSTOM_TOOLS_DIR, tmp_path / "effect")
    review = extensions.review_module("tool:custom/example")
    path.write_text(path.read_text().replace("approved", "modified"))
    with pytest.raises(extensions.SettingsError, match="changed since review"):
        extensions.trust_module(review["id"], review["sha256"])
    assert extensions.settings()["trusted_modules"] == {}
    assert not (tmp_path / "effect").exists()


async def test_changed_bytes_revoke_import_schema_and_cached_handler_even_with_same_mtime(tmp_path):
    marker = tmp_path / "effect"
    path = write_tool(config.CUSTOM_TOOLS_DIR, marker)
    review = approve(path)
    loaded = next(t for t in registry.build()["tools"] if t.name == "example")
    assert loaded.handler.__module__ == "dream.tools.custom.example"
    before = path.stat()
    path.write_text(path.read_text().replace("approved", "modified"))  # same length
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    marker.unlink()
    assert extensions.filter_tools([loaded]) == []
    assert (await loaded.handler({}))["is_error"]
    assert "example" not in registry.build()["names"] and not marker.exists()
    assert extensions.settings()["trusted_modules"][review["id"]]["sha256"] == review["sha256"]
    catalog = {row["id"]: row for row in extensions.catalog()["extensions"]}
    assert not catalog['tool:example']['enabled']
    assert catalog['tool:example']['blocked_by'] == review['id']
    approve(path)
    assert (await loaded.handler({}))["is_error"], "new approval cannot bless an older cached handler"
    new = next(t for t in registry.build()["tools"] if t.name == "example")
    assert marker.read_text() == "modified"
    assert (await new.handler({}))["content"][0]["text"] == "modified"


def test_compile_uses_approved_bytes_not_a_second_source_read(tmp_path, monkeypatch):
    marker = tmp_path / "effect"
    path = write_tool(config.CUSTOM_TOOLS_DIR, marker)
    approve(path)
    original = extensions.approved_module_source

    def race(path, plugin=None):
        raw, snapshot = original(path, plugin)
        path.write_text(path.read_text().replace("approved", "modified"))
        return raw, snapshot

    monkeypatch.setattr(extensions, "approved_module_source", race)
    built = registry.build()
    assert marker.read_text() == "approved"
    assert "example" not in built["names"], "changed current bytes cannot expose the older loaded tool"


def test_untrusted_bytecode_cache_cannot_replace_reviewed_source(tmp_path):
    marker = tmp_path / "effect"
    path = write_tool(config.CUSTOM_TOOLS_DIR, marker)
    source, info = path.read_bytes(), path.stat()
    path.write_bytes(source.replace(b"approved", b"modified"))
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    py_compile.compile(str(path), doraise=True)
    path.write_bytes(source)
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    approve(path)
    assert "example" in registry.build()["names"]
    assert marker.read_text() == "approved"


def test_plugin_enable_is_not_source_trust_and_new_files_are_not_grandfathered(tmp_path):
    root = config.PLUGINS_DIR / "directory"
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text("name: owner\nenabled: false\n")
    path = write_tool(root / "tools", tmp_path / "first")
    plugins.load()
    extensions.set_enabled("plugin:owner", True)
    assert "example" not in registry.build()["names"] and not (tmp_path / "first").exists()
    approve(path, "owner")
    assert "example" in registry.build()["names"]
    write_tool(root / "tools", tmp_path / "second", name="new_tool")
    assert "new_tool" not in registry.build()["names"] and not (tmp_path / "second").exists()
    extensions.set_enabled("plugin:owner", False)
    assert "example" not in registry.build()["names"]


def test_no_trust_migration_from_install_presence_usage_or_old_timestamps(tmp_path):
    path = write_tool(config.CUSTOM_TOOLS_DIR, tmp_path / "effect")
    os.utime(path, (100000, 100000))
    extensions.record_usage("tool:custom/example", "completed")
    assert "example" not in registry.build()["names"]
    assert extensions.settings()["trusted_modules"] == {}


@pytest.mark.parametrize("bad", [{"tool:custom/example": True},
                                {"tool:custom/example": {"path": "/tmp/x", "sha256": False, "approved_at": "now"}},
                                {"skill:x": {"path": "/tmp/x", "sha256": "a" * 64, "approved_at": "now"}}])
def test_invalid_trust_store_fails_closed_without_erasing_it(tmp_path, bad):
    path = write_tool(config.CUSTOM_TOOLS_DIR, tmp_path / "effect")
    data = extensions.settings()
    data["trusted_modules"] = bad
    raw = json.dumps(data)
    extensions.settings_path().write_text(raw)
    assert "example" not in registry.build()["names"] and not (tmp_path / "effect").exists()
    with pytest.raises(extensions.SettingsError):
        approve(path)
    assert extensions.settings_path().read_text() == raw


def test_unconfigured_paths_symlinks_and_oversized_sources_are_not_approved(tmp_path, monkeypatch):
    candidate = write_tool(tmp_path / ".dream" / "labs" / "candidate", tmp_path / "effect")
    with pytest.raises(extensions.SettingsError, match="found 0"):
        extensions.review_module("tool:custom/example")
    config.CUSTOM_TOOLS_DIR.mkdir()
    alias = config.CUSTOM_TOOLS_DIR / "example.py"
    alias.symlink_to(candidate)
    with pytest.raises(OSError):
        extensions.review_module("tool:custom/example")
    alias.unlink()
    path = write_tool(config.CUSTOM_TOOLS_DIR, tmp_path / "effect")
    monkeypatch.setattr(extensions, "MAX_MODULE_BYTES", 16)
    with pytest.raises(extensions.SettingsError, match="16 bytes"):
        approve(path)
    assert not (tmp_path / "effect").exists()
