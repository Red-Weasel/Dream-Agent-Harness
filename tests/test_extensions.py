"""Extension controls against isolated packages/settings; no models or services."""
from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from claude_agent_sdk import SdkMcpTool

from dream import config, extensions, plugins
from dream.skills import loader
from dream.tools import installed_skill_tools, registry


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DREAM_EXTENSION_SETTINGS", str(tmp_path / "data" / "config" / "extensions.json"))
    monkeypatch.setenv("DREAM_SKILL_DIRS", str(tmp_path / "skills"))
    monkeypatch.setattr(config, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(config, "CUSTOM_TOOLS_DIR", tmp_path / "tools")
    monkeypatch.setattr(config, "MCP_CONFIG_PATH", tmp_path / "mcp.json")
    monkeypatch.setattr(installed_skill_tools, "_CACHE", None)
    monkeypatch.setattr(extensions, "_RUNTIME", {})
    monkeypatch.setattr(extensions, "_USAGE_WARNINGS", [])
    plugins.load()
    yield
    plugins.load(tmp_path / "none")


def skill(root, name="portable", body="Read the evidence before choosing."):
    path = root / name
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(f"---\nname: {name}\ndescription: A portable procedure\n---\n{body}")
    return path


def plugin(root, name="sample", manifest_name=None):
    path = root / name
    path.mkdir(parents=True)
    (path / "plugin.yaml").write_text(f"name: {manifest_name or name}\ndescription: Sample plugin\ncapabilities: preview, file-read\n")
    return path


def tool_module(root, marker=None):
    root.mkdir(parents=True, exist_ok=True)
    text = "from claude_agent_sdk import tool\n"
    if marker:
        text += f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n"
    text += "@tool('sample_tool', 'Sample', {'type':'object','properties':{}})\nasync def sample_tool(args):\n    return {'content':[{'type':'text','text':'worked'}]}\n"
    path = root / "sample.py"
    path.write_text(text)
    return path


def trust_tool(path, owner=None):
    identifier = extensions.tool_module_id(path, owner)
    review = extensions.review_module(identifier)
    extensions.trust_module(identifier, review["sha256"])


def test_legacy_skills_keep_defaults_and_global_trees_are_not_modified(tmp_path, monkeypatch):
    codex = tmp_path / ".codex" / "skills"
    package = skill(codex)
    original = (package / "SKILL.md").read_bytes()
    monkeypatch.setenv("DREAM_SKILL_DIRS", str(codex))
    found = installed_skill_tools.installed(refresh=True)
    assert [s.name for s in found] == ["portable"]
    assert not extensions.settings_path().exists(), "discovery must not migrate by rewriting files"
    extensions.set_enabled("skill:portable", False)
    assert installed_skill_tools.installed() == []
    assert loader.index_lines(found) == [] and loader.find(found, "") == []
    assert "disabled" in loader.load_body(found[0])
    assert "disabled" in loader.bundled_file(found[0], "SKILL.md")
    extensions.set_enabled("skill:portable", True)
    assert [s.name for s in installed_skill_tools.installed()] == ["portable"]
    assert (package / "SKILL.md").read_bytes() == original
    assert list(package.iterdir()) == [package / "SKILL.md"]
    assert extensions.usage("skill:portable")["state"] == "not_observed"
    assert "Read the evidence" in loader.load_body(found[0])
    assert extensions.usage("skill:portable")["counts"] == {"opened": 1}


def test_discovery_only_reads_frontmatter_and_metadata(tmp_path, monkeypatch):
    package = skill(tmp_path / "skills", body="secret body" * 10000)
    # Path.read_text was the eager body-loading path. Discovery must not call it.
    original = Path.read_text

    def no_body_read(path, *args, **kwargs):
        if path.name == "SKILL.md":
            raise AssertionError("eager skill body load")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", no_body_read)
    found, warnings = loader.discover([package.parent])
    assert found[0].description == "A portable procedure" and not warnings
    assert not extensions.settings_path().exists()


def test_portable_package_metadata_survives_move_and_never_registers_hooks(tmp_path):
    source = skill(tmp_path / "skills")
    (source / "manifest.json").write_text(json.dumps({
        "format": "dream-skill/v1", "name": "portable", "version": "1.0", "portable": True,
        "capabilities": ["read-files", "review-evidence"], "hooks": {"before_tool": "touch PWNED"},
    }))
    (source / "scripts").mkdir()
    (source / "scripts" / "example.py").write_text("raise RuntimeError('must never auto-execute')")
    moved = tmp_path / "another-provider" / "portable"
    shutil.copytree(source, moved)
    found, warnings = loader.discover([moved])
    assert found[0].portable and found[0].capabilities == ("read-files", "review-evidence")
    assert found[0].version == "1.0" and any("ignored" in w for w in warnings)
    assert "scripts/example.py" in loader.load_body(found[0])
    assert extensions.settings()["hooks"] == {}
    assert not (moved / "PWNED").exists()
    extensions.set_enabled("skill:portable", False)
    assert "disabled" in loader.load_body(found[0]), "id is independent of install path"


@pytest.mark.parametrize("bad", ["{", "[]", '{"version":99}', '{"version":1,"overrides":{"skill:x":"false"}}',
                                '{"version":1,"overrides":{"skill:x":false},"overrides":{}}',
                                '{"version":1,"extra":NaN}',
                                '{"version":1,"usage":{"skill:x":{"counts":{"opened":-1}}}}'])
def test_malformed_settings_fail_closed_and_are_never_overwritten(tmp_path, bad):
    path = extensions.settings_path()
    path.parent.mkdir(parents=True)
    path.write_text(bad)
    assert not extensions.is_enabled("skill:x")
    with pytest.raises(extensions.SettingsError):
        extensions.set_enabled("skill:x", True)
    assert not extensions.record_usage("skill:x", "opened")
    assert path.read_text() == bad
    view = extensions.status(refresh=True)
    assert any("settings invalid" in w for w in view["warnings"])
    assert extensions.usage("skill:x")["state"] == "unavailable"


def test_atomic_overrides_usage_and_unknown_fields_survive_updates(tmp_path):
    extensions.set_enabled("skill:a", False)
    path = extensions.settings_path()
    data = extensions.settings()
    data["future_metadata"] = {"keep": True}
    path.write_text(json.dumps(data))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: extensions.record_usage("skill:a", "opened"), range(40)))
    assert extensions.usage("skill:a")["counts"] == {"opened": 40}
    assert extensions.settings()["future_metadata"] == {"keep": True}
    assert extensions.settings()["overrides"] == {"skill:a": False}
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(path.parent.glob(".extensions-*")) == []
    extensions.clear_override("skill:a")
    assert extensions.is_enabled("skill:a")


async def test_plugin_disable_prevents_import_exposure_and_cached_handler_execution(tmp_path):
    p = plugin(config.PLUGINS_DIR, "directory-name", manifest_name="owner")
    skill(p / "skills")
    marker = tmp_path / "imported"
    tool_module(p / "tools", marker)
    plugins.load()
    extensions.set_enabled("plugin:owner", False)
    built = registry.build()
    assert not marker.exists() and "sample_tool" not in built["names"]
    assert installed_skill_tools.installed(refresh=True) == []
    inventory = extensions.catalog()
    by = {row["id"]: row for row in inventory["extensions"]}
    assert not by["skill:portable"]["enabled"] and by["skill:portable"]["parent"] == "plugin:owner"
    assert not by["tool:plugin/owner/sample"]["enabled"]
    extensions.set_enabled("plugin:owner", True)
    plugins.load()
    trust_tool(p / "tools" / "sample.py", "owner")
    built = registry.build()
    assert marker.exists() and "sample_tool" in built["names"]
    loaded = next(t for t in built["tools"] if t.name == "sample_tool")
    result = await loaded.handler({})
    assert result["content"][0]["text"] == "worked"
    assert extensions.usage("plugin:owner")["counts"]["completed"] == 1
    extensions.set_enabled("plugin:owner", False)
    assert extensions.filter_tools([loaded]) == []
    assert (await loaded.handler({}))["is_error"]
    assert extensions.usage("plugin:owner")["counts"]["attempted"] == 1


async def test_disabled_custom_module_and_individual_tool(tmp_path):
    marker = tmp_path / "imported"
    tool_module(config.CUSTOM_TOOLS_DIR, marker)
    extensions.set_enabled("tool:custom/sample", False)
    assert "sample_tool" not in registry.build()["names"] and not marker.exists()
    extensions.set_enabled("tool:custom/sample", True)
    trust_tool(config.CUSTOM_TOOLS_DIR / "sample.py")
    built = registry.build()
    tool = next(t for t in built["tools"] if t.name == "sample_tool")
    extensions.set_enabled("tool:sample_tool", False)
    assert extensions.filter_tools([tool]) == []
    assert "sample_tool" not in registry.build()["names"]
    assert (await tool.handler({}))["is_error"]
    assert extensions.usage("tool:sample_tool")["state"] == "not_observed"


async def test_engine_middleware_wraps_sdk_and_http_tools_and_preserves_module(tmp_path):
    from dataclasses import replace

    p = plugin(config.PLUGINS_DIR)
    tool_module(p / "tools")
    plugins.load()
    trust_tool(p / "tools" / "sample.py", "sample")
    observed = []

    def bind(tool):
        async def middleware(args):
            observed.append(tool.name)
            return await tool.handler(args)
        return replace(tool, handler=middleware)

    built = registry.build(wrap_tool=bind)
    wrapped = next(t for t in built["tools"] if t.name == "sample_tool")
    assert wrapped.handler.__module__ == "dream.plugins.sample.tools.sample"
    assert wrapped._dream_plugin_id == "plugin:sample"
    assert wrapped._dream_extension_id == "tool:plugin/sample/sample"
    await wrapped.handler({})
    assert observed == ["sample_tool"]
    extensions.set_enabled("plugin:sample", False)
    assert not extensions.tool_enabled(wrapped)
    assert (await wrapped.handler({}))["is_error"]
    # The SDK was built from this same middleware-bearing list, not _BASE_TOOLS.
    assert built["server"]["instance"] is not None


async def test_mcp_filter_preserves_provenance_and_guard_blocks_cached_remote_tools(tmp_path):
    p = plugin(config.PLUGINS_DIR)
    (p / "mcp.json").write_text(json.dumps({"servers": [{"name": "calc", "command": "/bin/false"}]}))
    plugins.load()
    cfg = plugins.mcp_servers()[0]
    assert cfg["_dream_plugin"] == "sample"
    called = []

    async def handler(args):
        called.append(args)
        return {"content": []}

    remote = SdkMcpTool(name="calc__sum", description="sum", input_schema={}, handler=handler)
    # The old MCP client only carries the server prefix. Config filtering must
    # retain enough provenance for a plugin toggle to reach this cached tool.
    guarded = extensions.guard_tool(remote)
    extensions.set_enabled("mcp:calc", False)
    assert extensions.filter_mcp_configs([cfg]) == []
    assert extensions.filter_tools([guarded]) == []
    assert (await guarded.handler({}))["is_error"] and not called
    extensions.set_enabled("mcp:calc", True)
    extensions.set_enabled("plugin:sample", False)
    assert extensions.filter_mcp_configs([cfg]) == []
    assert plugins.mcp_servers() == []
    assert (await guarded.handler({}))["is_error"] and not called
    extensions.set_enabled("plugin:sample", True)
    assert extensions.filter_mcp_configs([cfg]) == [cfg]
    await guarded.handler({"n": 2})
    assert called == [{"n": 2}]
    assert extensions.usage("mcp:calc")["counts"] == {"attempted": 1, "completed": 1}
    assert extensions.usage("plugin:sample")["counts"] == {"attempted": 1, "completed": 1}


def test_catalog_counts_provenance_warning_and_absent_usage(tmp_path):
    skill(tmp_path / "skills", "one")
    broken = skill(tmp_path / "skills", "two")
    (broken / "manifest.json").write_text("{")
    extensions.set_enabled("skill:two", False)
    view = extensions.catalog(refresh=True)
    assert view["counts"]["skill"] == {"total": 2, "enabled": 1, "disabled": 1}
    assert any("invalid manifest.json" in w for w in view["warnings"])
    by = {r["id"]: r for r in view["extensions"]}
    assert by["skill:one"]["usage"] == {"state": "not_observed", "counts": {}}
    assert by["skill:one"]["provenance"] == "external-read-only"
    assert "never used" not in view["usage_note"]


def test_disabling_a_manifest_disabled_plugin_has_no_implicit_enable(tmp_path):
    p = plugin(config.PLUGINS_DIR)
    with (p / "plugin.yaml").open("a") as out:
        out.write("enabled: false\n")
    plugins.load()
    assert not plugins.loaded()[0].enabled
    extensions.set_enabled("plugin:sample", True)
    assert plugins.loaded()[0].enabled
    extensions.clear_override("plugin:sample")
    assert not plugins.loaded()[0].enabled
