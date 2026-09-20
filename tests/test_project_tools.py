"""update_todos, set_project_title, save_as_template, register/unregister_assets —
against a temporary Library."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from dream.library.store import Library
from dream.tools import context as tool_context, library_tools, project
from dream.tools.context import ToolContext, set_context
from dream.tools.project import (
    PROJECT_TOOLS, manifest, project_title, register_assets, save_as_template,
    set_project_title, unregister_assets, update_todos,
)
from dream.tui.plan import PlanTracker


@pytest.fixture
def ws(tmp_path, monkeypatch):
    lib = Library(tmp_path / "lib" / "library.db", tmp_path / "lib" / "blobs")
    monkeypatch.setattr(library_tools, "_LIB", lib)
    root = tmp_path / "ws"; root.mkdir()
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=root))
    project._STATE["title"] = ""
    yield root, lib
    tool_context._CTX = None
    lib.close()


def _text(res):
    return res["content"][0]["text"]


def _failed(res):
    return bool(res.get("is_error"))


@pytest.mark.asyncio
async def test_update_todos_is_a_full_replace_the_plan_panel_understands(ws):
    res = await update_todos.handler({"todos": [{"name": "Read the kit", "completed": True},
                                                 {"name": "Draft three options", "completed": False}]})
    assert not _failed(res) and "2 task(s), 1 done" in _text(res)
    plan = PlanTracker()
    plan.ingest("update_todos", {"todos": [{"name": "Read the kit", "completed": True},
                                           {"name": "Draft three options", "completed": False}]})
    assert [(t["content"], t["status"]) for t in plan._tasks] == [
        ("Read the kit", "completed"), ("Draft three options", "pending")]
    plan.ingest("update_todos", {"todos": [{"name": "stringly", "completed": "yes"}]})
    assert plan._tasks[0]["status"] == "pending", "only a real boolean completes a task"
    plan.ingest("TodoWrite", {"todos": [{"content": "old shape", "status": "in_progress"}]})
    assert [(t["content"], t["status"]) for t in plan._tasks] == [("old shape", "in_progress")]
    assert _failed(await update_todos.handler({"todos": [{"completed": True}]}))
    assert "cleared" in _text(await update_todos.handler({"todos": []}))


@pytest.mark.asyncio
async def test_set_project_title_names_the_session(ws):
    assert project_title() == ""
    res = await set_project_title.handler({"title": "  Acme Onboarding  "})
    assert not _failed(res) and project_title() == "Acme Onboarding"
    assert _failed(await set_project_title.handler({"title": ""}))
    assert _failed(await set_project_title.handler({"title": "x" * 121}))


@pytest.mark.asyncio
async def test_save_as_template_zips_the_workspace_without_junk_into_the_library(ws):
    root, lib = ws
    (root / "index.html").write_text("<p>hi</p>")
    (root / "frames").mkdir(); (root / "frames" / "a.jsx").write_text("x")
    (root / "node_modules").mkdir(); (root / "node_modules" / "big.js").write_text("nope")
    (root / ".git").mkdir(); (root / ".git" / "HEAD").write_text("ref")
    res = await save_as_template.handler({"title": "Acme Deck", "description": "a deck",
                                          "intro_text": "give me your logo"})
    assert not _failed(res) and "/templates/acme-deck.zip" in _text(res)
    (f,) = lib.by_name("acme-deck.zip", folder="/templates")
    out = lib.materialize(f.id, root.parent / "out.zip")
    z = zipfile.ZipFile(out)
    names = set(z.namelist())
    assert {"template.json", "index.html", "frames/a.jsx"} <= names
    assert not any(n.startswith(("node_modules", ".git")) for n in names)
    meta = json.loads(z.read("template.json"))
    assert meta["title"] == "Acme Deck" and meta["intro_text"] == "give me your logo"
    # saving again is a new version of the same template
    res = await save_as_template.handler({"title": "Acme Deck"})
    assert "updated to v2" in _text(res)


@pytest.mark.asyncio
async def test_register_assets_files_versions_and_keeps_a_manifest(ws):
    root, lib = ws
    (root / "type.html").write_text("<h1>Type</h1>")
    (root / "colors.html").write_text("<div>Colors</div>")
    res = await register_assets.handler({"items": [
        {"path": "type.html", "asset": "Type scale", "group": "Type", "subtitle": "v1"},
        {"path": "colors.html", "asset": "Palette", "group": "colors", "status": "approved"},
    ]})
    assert not _failed(res), _text(res)
    m = manifest()
    assert {(e["asset"], e["group"], e["status"]) for e in m} == {
        ("Type scale", "Type", "needs-review"), ("Palette", "Colors", "approved")}
    assert lib.by_name("Type scale", folder="/design-system/Type")
    assert lib.by_name("manifest.json", folder="/design-system")
    # re-registering the same pair resets its status and is a new Library version
    (root / "type.html").write_text("<h1>Type v2</h1>")
    await register_assets.handler({"items": [{"path": "type.html", "asset": "Type scale",
                                              "group": "Type", "status": "changes-requested"}]})
    m = manifest()
    e = next(x for x in m if x["asset"] == "Type scale")
    assert e["status"] == "changes-requested" and e["version"] == 2 and len(m) == 2


@pytest.mark.asyncio
async def test_register_assets_refuses_bad_items_before_touching_anything(ws):
    root, lib = ws
    (root / "ok.html").write_text("x")
    for bad in ({"path": "ok.html"}, {"path": "missing.html", "asset": "A"},
                {"path": "ok.html", "asset": "A", "group": "Shapes"},
                {"path": "ok.html", "asset": "A", "status": "meh"},
                {"path": "../ok.html", "asset": "A"}):
        res = await register_assets.handler({"items": [{"path": "ok.html", "asset": "Fine"}, bad]})
        assert _failed(res) and "Nothing was registered" in _text(res), bad
    assert manifest() == [] and not lib.by_name("Fine", folder="/design-system/Brand")


@pytest.mark.asyncio
async def test_unregister_by_asset_path_or_both(ws):
    root, _ = ws
    for n in ("a.html", "b.html", "c.html"):
        (root / n).write_text(n)
    await register_assets.handler({"items": [
        {"path": "a.html", "asset": "Button", "group": "Components"},
        {"path": "b.html", "asset": "Button", "group": "Components"},
        {"path": "c.html", "asset": "Card", "group": "Components"},
    ]})
    assert len(manifest()) == 3
    res = await unregister_assets.handler({"items": [{"asset": "Button", "path": "b.html"}]})
    assert "Removed 1" in _text(res) and len(manifest()) == 2
    await unregister_assets.handler({"items": [{"asset": "Button"}]})
    assert [e["asset"] for e in manifest()] == ["Card"]
    await unregister_assets.handler({"items": [{"path": "c.html"}]})
    assert manifest() == []
    assert _failed(await unregister_assets.handler({"items": [{}]}))


def test_the_project_tools_are_exported():
    assert [t.name for t in PROJECT_TOOLS] == [
        "update_todos", "update_plan", "project_note", "set_project_title", "save_as_template",
        "register_assets", "unregister_assets"]


# --- write_file registers on request; Studio serves the manifest ------------------------


@pytest.mark.asyncio
async def test_write_file_with_an_asset_name_registers_the_file(ws):
    from dream.tools.native import write_file

    root, lib = ws
    res = await write_file.handler({"path": "components.html", "content": "<button>Go</button>",
                                    "asset": "Buttons", "group": "components"})
    assert not _failed(res) and "Registered" in _text(res)
    (e,) = manifest()
    assert e["asset"] == "Buttons" and e["group"] == "Components" and e["status"] == "needs-review"
    assert lib.by_name("Buttons", folder="/design-system/Components")
    plain = await write_file.handler({"path": "notes.md", "content": "x"})
    assert not _failed(plain) and "Registered" not in _text(plain) and len(manifest()) == 1
    odd = await write_file.handler({"path": "odd.html", "content": "x", "asset": "Odd", "group": "Shapes"})
    assert not _failed(odd) and "group must be one of" in _text(odd) and len(manifest()) == 1


@pytest.mark.asyncio
async def test_studio_serves_the_manifest_with_renderable_content(ws):
    from starlette.testclient import TestClient

    from dream.gui.bus import EventBus
    from dream.gui.server import StudioServer

    root, _ = ws
    (root / "type.html").write_text("<h1>Type</h1>")
    (root / "tokens.css").write_text("body{}")
    await register_assets.handler({"items": [
        {"path": "type.html", "asset": "Type scale", "group": "Type", "subtitle": "first cut"},
        {"path": "tokens.css", "asset": "Tokens", "group": "Colors"},
    ]})
    srv = StudioServer(EventBus())
    with TestClient(srv.app) as client:
        assert client.get("/api/assets").status_code == 401
        rows = client.get("/api/assets", headers={"X-Dream-Token": srv.token}).json()["assets"]
    by = {r["asset"]: r for r in rows}
    assert by["Type scale"]["content"] == "<h1>Type</h1>" and by["Type scale"]["subtitle"] == "first cut"
    assert by["Tokens"]["content"] is None, "a stylesheet is not renderable; no open button"


def test_the_design_tab_escapes_model_text_and_reuses_the_artifact_frame():
    ui = (Path(__file__).parent.parent / "dream" / "gui" / "static" / "index.html").read_text()
    assert 'id="tab-design"' in ui and "loadAssets" in ui and "'/api/assets'" in ui
    fn = ui[ui.index("async function loadAssets"):]
    fn = fn[:fn.index("\n}\n")]
    for model_text in ("esc(r.asset)", "esc(r.status", "esc(r.subtitle"):
        assert model_text in fn, model_text
    assert "addArtifact(" in fn and "openArtifact(" in fn
