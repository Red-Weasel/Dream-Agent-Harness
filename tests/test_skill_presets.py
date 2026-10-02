"""DREAM-170 skill presets, phase 1: Default is no filter at all (the tools, the tools sent at 16K/75K/250K and the
system prompt are byte-identical whether the presets file is missing, corrupt or says Default); a preset narrows the
tools, MCP servers and skills a session starts with, never below the 19 core tools, and never mid-session."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_schema_deferral import _FakeClient, _text_round  # noqa: E402

from dream import config, presets  # noqa: E402
from dream.core import system_prompt  # noqa: E402
from dream.core.backends.openai_compat import OpenAICompatBackend, _tool_schema  # noqa: E402
from dream.core.profiles import PROFILES  # noqa: E402
from dream.core.subagents import local_subagents  # noqa: E402
from dream.memory.store import MemoryStore  # noqa: E402
from dream.skills import loader  # noqa: E402
from dream.tools import installed_skill_tools as ist, registry  # noqa: E402
from dream.tools.capability_tools import CAPABILITY_TOOLS  # noqa: E402
from dream.tools.demonstration_tools import DEMONSTRATION_TOOLS  # noqa: E402
from dream.tools.moe_tools import moe_tools  # noqa: E402
from dream.tools.native import NATIVE_TOOLS  # noqa: E402

EXTRA = list(NATIVE_TOOLS) + DEMONSTRATION_TOOLS + CAPABILITY_TOOLS + list(moe_tools())  # what the Engine adds
LEGAL_GROUPS = {"web", "library_tools", "export_tools", "vision"}   # plus the one tool show_to_user


@pytest.fixture(autouse=True)
def _fresh_session(monkeypatch):
    monkeypatch.setattr(presets, "_SESSION", None)
    monkeypatch.setattr(presets, "_SESSION_NAME", "Default")
    monkeypatch.setattr(ist, "_CACHE", None)


def _user_file(content: str | None) -> None:
    path = presets.user_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if content is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(content)


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _backend(tools, window):
    prov = SimpleNamespace(key="machx", label="MachX", base_url="http://x/v1", multimodal=False, api_key=lambda: "n")
    b = OpenAICompatBackend(provider=prov, model="m", system_prompt="S" * 100, tools=tools, permission_cb=None,
                            subagents=local_subagents(), profile=PROFILES["lean" if window <= 32768 else "frontier"])
    b.n_ctx = window
    b._client = _FakeClient([_text_round("ok")] * 3)
    return b


async def _sent(tools, window):
    b = _backend(tools, window)
    [ev async for ev in b.ask("hello")]
    return b._client.payloads[0]["tools"]


def _snapshot(tmp_path) -> dict:
    """What a session starts with: the registry's tools (names and schemas), the tools actually sent per window and
    the system prompt on a fixed store."""
    name, _warning = presets.start_session()
    built = registry.build(extra_tools=EXTRA)
    store = MemoryStore(tmp_path / "fixed.db")
    try:
        prompt = system_prompt.build_system_prompt(store, "s", workspace=tmp_path)
    finally:
        store.close()
    return {"preset": name, "tools": _sha([_tool_schema(t) for t in built["tools"]]),
            **{f"sent_{w}": _sha(asyncio.run(_sent(built["tools"], w))) for w in (16384, 75000, 250000)},
            "prompt": _sha(str(prompt))}


def test_default_is_byte_identical_whatever_the_presets_file_says(tmp_path, monkeypatch):
    runs = {}
    for label, content in (("missing", None), ("corrupt", "{not json"), ("wrong shape", "[1, 2]"),
                           ("default", json.dumps({"active": "Default"})),
                           ("unknown name", json.dumps({"active": "No such preset"}))):
        _user_file(content)
        monkeypatch.setattr(presets, "_SESSION", None)
        runs[label] = _snapshot(tmp_path)
    assert all(run == runs["missing"] for run in runs.values()), runs
    assert runs["missing"]["preset"] == "Default"


def test_a_corrupt_file_is_reported_and_never_overwritten():
    _user_file("{not json")
    name, warning = presets.start_session()
    assert name == "Default" and presets.session() is None and "presets.json" in warning
    with pytest.raises(presets.PresetsError):
        presets.set_active("Legal")
    assert presets.user_path().read_text() == "{not json"


def test_legal_cuts_the_tools_to_the_core_and_its_groups():
    _user_file(json.dumps({"active": "Legal"}))
    assert presets.start_session() == ("Legal", None)
    built = registry.build(extra_tools=EXTRA)
    names = set(built["names"])
    groups = {t.name: t.handler.__module__.rsplit(".", 1)[-1] for t in built["catalog"]}
    assert len(built["catalog"]) == 113 and len(names) == 35
    assert names == set(presets.CORE_TOOLS) | {n for n, g in groups.items() if g in LEGAL_GROUPS} | {"show_to_user"}


def test_the_core_tools_survive_an_empty_preset():
    _user_file(json.dumps({"active": "Bare", "presets": {"Bare": {}}}))
    assert presets.start_session() == ("Bare", None)
    assert set(registry.build(extra_tools=EXTRA)["names"]) == set(presets.CORE_TOOLS)
    assert len(presets.CORE_TOOLS) == 19


def test_the_builtin_presets_name_real_skills_and_tool_groups():
    groups = {t.handler.__module__.rsplit(".", 1)[-1] for t in list(registry._BASE_TOOLS) + EXTRA}
    shipped = json.loads(presets.BUILTIN_PATH.read_text())["presets"]
    assert set(shipped) == {"Software Development", "Finance", "Sales", "Legal", "Security", "Scientific", "Creative"}
    for name, preset in shipped.items():
        assert set(preset["tools"]) <= groups | {t.name for t in list(registry._BASE_TOOLS) + EXTRA}, name
        assert set(preset["skills"]) <= set(config.CURATED_SKILLS) | {d.name for d in (config.ROOT / "skills" / "domains").iterdir()}, name


def _skill(root: Path, name: str, desc: str) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\nbody\n")


def test_the_skills_index_and_search_follow_the_preset(tmp_path, monkeypatch):
    _skill(tmp_path / "skills", "clause-notes", "Keep notes on contract clauses.")
    monkeypatch.setenv("DREAM_SKILL_DIRS", str(tmp_path / "skills"))
    monkeypatch.setattr(config, "bundled_skill_dirs", lambda: [tmp_path / "skills" / "research"])
    _skill(tmp_path / "skills", "research", "Find and weigh sources.")
    _skill(tmp_path / "skills", "coding", "Change code.")
    # Default: the curated index only, and search sees every enabled skill
    presets.start_session()
    assert [ln.split(" — ")[0] for ln in ist.index_lines()] == ["research"]
    assert {s.name for s in ist.installed(refresh=True)} == {"clause-notes", "research", "coding"}
    # A preset: its own skills, curated or not, are listed; the others are neither listed nor found nor opened
    _user_file(json.dumps({"active": "Mine", "presets": {"Mine": {"skills": ["clause-notes", "research"]}}}))
    presets.start_session()
    assert [ln.split(" — ")[0] for ln in ist.index_lines()] == ["clause-notes", "research"]
    found = asyncio.run(ist.skill_find.handler({"query": ""}))["content"][0]["text"]
    assert "clause-notes" in found and "coding" not in found
    assert asyncio.run(ist.skill_open.handler({"name": "coding"})).get("is_error")


def test_the_tools_stay_the_same_within_a_session():
    _user_file(json.dumps({"active": "Legal"}))
    presets.start_session()
    tools = registry.build(extra_tools=EXTRA)["tools"]
    b = _backend(tools, 250000)
    first, skills = b._request_tools(), [s.name for s in ist.installed()]
    presets.set_active("Default")                  # the owner switches: that is for the NEXT session
    assert presets.active() == "Default" and presets.session() is not None
    assert b._request_tools() == first and [s.name for s in ist.installed()] == skills


def test_a_save_with_a_stale_revision_is_refused():
    presets.set_active("Legal")
    listed = presets.listing()
    assert listed["active"] == "Legal" and listed["names"][0] == "Default"
    presets.set_active("Finance", expected_sha256=listed["sha256"])
    with pytest.raises(presets.Conflict):
        presets.set_active("Sales", expected_sha256=listed["sha256"])
    with pytest.raises(ValueError):
        presets.set_active("No such preset")
    assert presets.active() == "Finance"


async def test_an_excluded_mcp_server_never_starts(tmp_path, monkeypatch):
    from test_blender_live import _start_engine_until_backend

    def live(workspace):
        return [{"name": "blender", "command": "a", "args": []}, {"name": "notes", "command": "b", "args": []}], []

    _user_file(json.dumps({"active": "Mine", "presets": {"Mine": {"mcp": ["notes"]}}}))
    instance, _, captured = await _start_engine_until_backend(tmp_path, monkeypatch, live=live)
    assert [c["name"] for c in captured["configs"]] == ["notes"] and instance.preset == "Mine"


def test_an_intended_plugin_alias_is_a_note_and_a_plain_duplicate_still_warns(tmp_path, monkeypatch):
    from dream import plugins

    _skill(tmp_path / "curated", "understand", "Ours.")
    _skill(tmp_path / "plugin", "understand", "The plugin's.")
    _skill(tmp_path / "other", "understand", "A plain copy.")
    owner = SimpleNamespace(name="understand-anything")
    monkeypatch.setattr(plugins, "owner", lambda path: owner if "plugin" in path.parts else None)
    notes: list[str] = []
    skills, warnings = loader.discover([tmp_path / "curated", tmp_path / "plugin", tmp_path / "other"], aliases=notes)
    assert [s.name for s in skills] == ["understand", "understand-anything:understand"]
    assert len(notes) == 1 and "understand-anything:understand" in notes[0]
    assert len(warnings) == 1 and "shadowed" in warnings[0] and str(tmp_path / "other") in warnings[0]


async def test_the_workspace_overview_edits_and_the_terminal_command(tmp_path, monkeypatch):
    from rich.console import Console
    from dream.tui.app import App

    monkeypatch.setattr(presets, "_CATALOG", list(registry._BASE_TOOLS) + EXTRA)
    over = presets.overview([])
    costs = {p["name"]: p["cost"] for p in over["presets"]}
    assert over["session"] == over["active"] == "Default" and costs["Legal"]["tools"] == 35
    assert costs["Default"]["tools"] == 113 and {"name": "web", "tools": 2, "core": 0, "names": ["web_search", "browse"]} in over["groups"]
    presets.edit("create", {"name": "Contracts"}, over["sha256"])
    sha = presets.listing()["sha256"]
    with pytest.raises(presets.Conflict):                                   # the second view's stale revision
        presets.edit("create", {"name": "Other"}, over["sha256"])
    for op, args in (("assign", {"preset": "Contracts", "kind": "skills", "item": "writing", "on": True}),
                     ("assign", {"preset": "Contracts", "kind": "skills", "item": "writing", "on": True}),
                     ("assign", {"preset": "Legal", "kind": "tools", "item": "web", "on": False}),
                     ("rename", {"preset": "Contracts", "to": "Deals"})):
        presets.edit(op, args, sha)
        sha = presets.listing()["sha256"]
    mine = json.loads(presets.user_path().read_text())["presets"]
    assert mine["Deals"]["skills"] == ["writing"] and "web" not in mine["Legal"]["tools"]      # assign is idempotent
    for op, args, why in (("delete", {"preset": "Legal"}, "cannot be deleted"), ("rename", {"preset": "Legal", "to": "X"}, "keeps"),
                          ("assign", {"preset": "Default", "kind": "skills", "item": "a", "on": True}, "nothing to change")):
        with pytest.raises(ValueError, match=why):
            presets.edit(op, args, sha)
    presets.edit("reset", {"preset": "Legal"}, sha)
    presets.edit("delete", {"preset": "Deals"}, presets.listing()["sha256"])
    assert json.loads(presets.user_path().read_text())["presets"] == {}
    app = App.__new__(App)
    shown = []
    app.renderer = SimpleNamespace(console=Console(record=True), system=shown.append, error=shown.append)
    app.engine = SimpleNamespace(preset="Default", store=None)
    await app._command("/preset finance")
    assert presets.active() == "Finance" and "/new applies it" in shown[-1]
    await app._command("/preset")
    assert "Default this session, Finance next" in app.renderer.console.export_text()


def test_a_malformed_user_preset_is_skipped_with_a_note():
    _user_file(json.dumps({"active": "Broken", "presets": {"Broken": {"skills": "research"}, "Fine": {}}}))
    listed = presets.listing()
    assert "Fine" in listed["names"] and "Broken" not in listed["names"] and "Broken" in listed["notes"][0]
    assert presets.start_session()[0] == "Default"


def test_remove_moves_your_skill_to_the_trash_and_never_touches_a_builtin(monkeypatch):
    from dream.skills import editor

    monkeypatch.delenv("DREAM_SKILL_DIRS", raising=False)
    text = "---\nname: {0}\ndescription: Mine.\n---\nbody\n"
    editor.create("clause-notes", text.format("clause-notes"))
    research = editor.read("research")
    assert research["origin"] == "builtin" and editor.read("clause-notes")["origin"] == "yours"
    with pytest.raises(ValueError, match="cannot be deleted"):
        editor.remove("research", research["sha256"])
    copy = editor.save("research", research["content"] + "\nmine\n", research["sha256"])
    assert copy["origin"] == "copy"
    with pytest.raises(editor.Conflict):
        editor.remove("research", research["sha256"])
    assert editor.remove("research", copy["sha256"])["restored"] is True
    gone = editor.remove("clause-notes", editor.read("clause-notes")["sha256"])
    assert gone["restored"] is False and Path(gone["trash"], "SKILL.md").is_file()
    names = {s["name"] for s in editor.listing()["skills"]}
    assert "clause-notes" not in names and editor.read("research")["origin"] == "builtin"


def test_gate1_items_default_reserved_symlink_kept_plugin_agents_filtered(tmp_path):
    from dream import plugins
    from dream.core import subagents
    from test_plugins import _plugin

    _user_file(json.dumps({"presets": {"Default": {}, "Mine": {"plugins": ["other"]}}}))
    listed = presets.listing()
    assert listed["names"].count("Default") == 1 and str(presets.user_path()) in listed["notes"][0]
    real = tmp_path / "elsewhere.json"
    real.write_text(presets.user_path().read_text())
    presets.user_path().unlink()
    presets.user_path().symlink_to(real)
    presets.set_active("Mine")
    assert presets.user_path().is_symlink() and json.loads(real.read_text())["active"] == "Mine"
    _plugin(tmp_path / "plugins", "full", agent=True)
    plugins.load(tmp_path / "plugins")
    assert "greeter" in subagents.local_subagents()                  # Default: no filter
    presets.start_session()                                           # Mine keeps plugin "other", not "full"
    assert "greeter" not in subagents.local_subagents()


def test_a_plugin_skill_is_edited_as_your_copy_and_the_plugin_files_stay(tmp_path):
    from dream import plugins
    from dream.skills import editor
    from test_plugins import _plugin

    source = _plugin(tmp_path / "plugins", "full", skill=True) / "skills" / "greet" / "SKILL.md"
    before = source.read_bytes()
    plugins.load(tmp_path / "plugins")
    ist.inventory(refresh=True)
    theirs = editor.read("greet-people")
    assert theirs["origin"] == "plugin"
    mine = editor.save("greet-people", theirs["content"] + "\nMy way.\n", theirs["sha256"])
    assert mine["origin"] == "copy" and source.read_bytes() == before
    assert editor.remove("greet-people", mine["sha256"])["restored"] is True
    assert editor.read("greet-people")["origin"] == "plugin" and source.read_bytes() == before


def test_gate2_malformed_assign_is_a_400_and_use_without_a_revision_keeps_other_edits():
    _user_file(json.dumps({"presets": {"X": "bad"}}))
    with pytest.raises(presets.PresetsError, match="needs lists"):
        presets.edit("assign", {"preset": "X", "kind": "skills", "item": "a", "on": True}, presets.listing()["sha256"])
    _user_file(json.dumps({"presets": {"Mine": {"skills": ["writing"]}}}))    # another view's edit, just saved
    presets.set_active("Mine")                                                  # /preset: no revision
    assert json.loads(presets.user_path().read_text()) == {"presets": {"Mine": {"skills": ["writing"]}}, "active": "Mine"}


def test_gate2_a_plugin_skill_copy_follows_its_plugin(tmp_path):
    from dream import extensions, plugins
    from dream.skills import editor
    from test_plugins import _plugin

    _plugin(tmp_path / "plugins", "full", skill=True)
    plugins.load(tmp_path / "plugins")
    ist.inventory(refresh=True)
    theirs = editor.read("greet-people")
    mine = editor.save("greet-people", theirs["content"] + "\nMine.\n", theirs["sha256"])
    assert mine["origin"] == "copy" and mine["copy_of"] == "full:greet-people"
    _user_file(json.dumps({"active": "P", "presets": {"P": {"plugins": ["full"]}, "Q": {}}}))
    presets.start_session()                                   # a preset with the plugin covers the copy
    assert "greet-people" in {s.name for s in ist.installed(refresh=True)}
    presets.set_active("Q")
    presets.start_session()
    assert "greet-people" not in {s.name for s in ist.installed()}
    presets.set_active("Default")
    presets.start_session()
    extensions.set_enabled(extensions.extension_id("plugin", "full"), False)   # the plugin off: the copy off too
    assert "greet-people" not in {s.name for s in ist.installed(refresh=True)}
