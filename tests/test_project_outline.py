"""DREAM-113, fix list #47: orientation in one call.

Live 2026-09-24, a project with a handoff doc: the model's first write came at +608 s, after about
twenty read_file calls in 80-160-line chunks. `project_outline` returns the workspace in one call,
at most about 2,000 tokens (8,000 characters):
- with an Understand-Anything map (`.ua/knowledge-graph.json`, and `.ua/domain-graph.json` when
  present): the layers, their key files and top symbols, the domains and their flows, and which
  files changed since the map was built;
- without one: a quick scan -- the top folders, and notable files with their line counts and
  their first docstring or heading line.
The walk never follows a symlink, skips hidden, installed and vendored trees (mirror.py's list,
DREAM-111), and stops at an entry cap and a time cap. The result is cached per workspace until a
file's mtime or size changes. The system prompt names the tool once and never carries the
outline itself (its head must stay the same all session). It is a read: free in every mode.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from dream.core import policy, system_prompt
from dream.memory.store import MemoryStore
from dream.tools import context as tool_context
from dream.tools.context import ToolContext, set_context
from dream.tools.native import NATIVE_TOOLS

LIMIT = 8_000


def _outline_module():
    from dream.tools import project_outline
    return project_outline


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=root, emit=None))
    _outline_module()._CACHE.clear()
    yield root
    tool_context._CTX = None
    _outline_module()._CACHE.clear()


def write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


async def outline_text(**args) -> str:
    res = await _outline_module().project_outline.handler(args)
    assert not res.get("is_error"), res
    return res["content"][0]["text"]


def small_project(root: Path) -> None:
    write(root, "README.md", "# Falcon launch\n\nA three.js launch scene.\n")
    write(root, "HANDOFF.md", "# Handoff 2026-09-24\n\nPhase 2 is next.\n")
    write(root, "app/__init__.py", '"""The launch scene package."""\n')
    write(root, "app/scene.py", '"""Builds the scene graph and the camera rig."""\n' + "x = 1\n" * 41)
    write(root, "app/physics.py", "# Rigid-body steps for the booster.\n" + "y = 2\n" * 9)
    write(root, "web/index.html", "<!doctype html><title>Launch viewer</title><canvas></canvas>\n")
    write(root, "web/main.js", "// Boots the renderer and the render loop.\nconst a = 1;\n")
    write(root, "pyproject.toml", '# The project manifest.\n[project]\nname = "falcon"\n')


# --- the scan, without a map ------------------------------------------------------------------------


async def test_without_a_map_it_scans_folders_and_notable_files_with_first_lines_and_line_counts(ws):
    small_project(ws)
    text = await outline_text()
    assert "no Understand map" in text
    assert "app/ (3 files)" in text and "web/ (2 files)" in text
    assert "The launch scene package." in text                        # the folder's own docstring
    assert "app/scene.py (42 lines) — Builds the scene graph and the camera rig." in text
    assert "app/physics.py (10 lines) — Rigid-body steps for the booster." in text
    assert "README.md (3 lines) — Falcon launch" in text
    assert "HANDOFF.md" in text and "Handoff 2026-09-24" in text
    assert "web/index.html (1 line) — Launch viewer" in text
    assert "web/main.js (2 lines) — Boots the renderer and the render loop." in text
    assert len(text) <= LIMIT


async def test_the_scan_skips_hidden_installed_and_vendored_trees_like_the_studio_mirror(ws):
    small_project(ws)
    for rel in ("node_modules/pkg/index.js", ".venv/lib/x.py", "venv/lib/y.py", "build/__pycache__/z.py",
                "lib/site-packages/w.py", ".git/config", ".cache/blob.py", "ms-playwright/chromium/run.py"):
        write(ws, rel, '"""VENDORED-MARKER"""\n')
    write(ws, "tools/env/pyvenv.cfg", "home = /usr/bin\n")               # an installed tree: DREAM-106's marker
    write(ws, "tools/env/lib/installed.py", '"""VENDORED-MARKER"""\n')
    text = await outline_text()
    assert "VENDORED-MARKER" not in text
    for name in ("node_modules", ".venv", "venv/", "site-packages", "__pycache__", ".git", ".cache",
                 "ms-playwright", "installed.py"):
        assert name not in text, name
    assert "app/scene.py" in text


async def test_a_big_workspace_stays_within_about_two_thousand_tokens(ws):
    for d in range(40):
        for f in range(25):
            write(ws, f"pkg{d:02d}/mod{f:02d}.py", f'"""Module {d}.{f}: ' + "does a long list of things " * 6
                  + '"""\n' + "z = 0\n" * 30)
    write(ws, "README.md", "# Big\n")
    text = await outline_text()
    assert len(text) <= LIMIT, len(text)
    assert "pkg00/" in text and "pkg39/" in text                        # every top folder is named
    assert "not listed" in text                                         # and it says what it left out


async def test_the_walk_stops_at_its_entry_cap_and_says_the_outline_is_partial(ws, monkeypatch):
    mod = _outline_module()
    for i in range(60):
        write(ws, f"d{i:02d}/f.py", '"""x"""\n')
    monkeypatch.setattr(mod, "WALK_MAX_ENTRIES", 25)
    text = await outline_text()
    assert "partial" in text and "25 entries" in text
    assert sum(f"d{i:02d}/" in text for i in range(60)) <= 25


async def test_the_walk_stops_at_its_time_cap_and_says_the_outline_is_partial(ws, monkeypatch):
    mod = _outline_module()
    for i in range(30):
        write(ws, f"d{i:02d}/f.py", '"""x"""\n')
    clock = iter(range(0, 10_000))
    monkeypatch.setattr(mod, "_clock", lambda: next(clock) * 0.1)        # every look at the clock: +0.1 s
    monkeypatch.setattr(mod, "WALK_MAX_S", 0.5)
    text = await outline_text()
    assert "partial" in text and "0.5 s" in text


async def test_symlinks_are_never_followed_out_of_the_workspace(ws, tmp_path):
    small_project(ws)
    outside = tmp_path / "outside"
    write(outside, "secret/README.md", "# OUTSIDE-SECRET heading\n")
    write(outside, "leak.py", '"""OUTSIDE-SECRET docstring"""\n')
    (ws / "linked").symlink_to(outside / "secret", target_is_directory=True)
    (ws / "app" / "leak.py").symlink_to(outside / "leak.py")
    text = await outline_text()
    assert "OUTSIDE-SECRET" not in text
    assert "linked" not in text and "leak.py" not in text


# --- with an Understand-Anything map ------------------------------------------------------------------


def node(kind, path, name, summary, **extra):
    nid = f"{kind}:{path}" if kind not in ("function", "class") else f"{kind}:{path}:{name}"
    return {"id": nid, "type": kind, "name": name, "filePath": path, "summary": summary,
            "tags": ["t"], "complexity": "simple", **extra}


def edge(source, target, kind="imports"):
    return {"source": source, "target": target, "type": kind, "direction": "forward", "weight": 0.7}


def write_map(root: Path, *, analyzed="2026-09-20T10:00:00Z", domains=True) -> None:
    nodes = [
        node("file", "app/scene.py", "scene.py", "Builds the scene graph and the camera rig."),
        node("function", "app/scene.py", "build_scene", "Assembles every mesh.", lineRange=[10, 80]),
        node("class", "app/scene.py", "CameraRig", "Follows the booster.", lineRange=[90, 160]),
        node("function", "app/scene.py", "tiny_helper", "A two-line helper.", lineRange=[170, 172]),
        node("file", "app/physics.py", "physics.py", "Rigid-body steps for the booster."),
        node("function", "app/physics.py", "step", "Advances the simulation by one tick.", lineRange=[1, 40]),
        node("document", "README.md", "README.md", "Project overview and first run."),
        node("config", "pyproject.toml", "pyproject.toml", "The Python project manifest."),
    ]
    edges = [edge("file:app/scene.py", "function:app/scene.py:build_scene", "contains"),
             edge("file:app/scene.py", "class:app/scene.py:CameraRig", "contains"),
             edge("file:app/scene.py", "function:app/scene.py:tiny_helper", "contains"),
             edge("file:app/physics.py", "function:app/physics.py:step", "contains"),
             edge("file:app/scene.py", "file:app/physics.py"),
             edge("function:app/scene.py:build_scene", "function:app/physics.py:step", "calls"),
             edge("function:app/scene.py:build_scene", "class:app/scene.py:CameraRig", "calls")]
    project = {"name": "falcon", "languages": ["python"], "frameworks": ["three.js"],
               "description": "A three.js launch scene with a Python physics core.",
               "analyzedAt": analyzed, "gitCommitHash": "none"}
    graph = {"version": "1.0.0", "project": project, "nodes": nodes, "edges": edges,
             "layers": [{"id": "layer:core", "name": "Core simulation", "description": "Scene and physics.",
                         "nodeIds": ["file:app/scene.py", "file:app/physics.py"]},
                        {"id": "layer:docs", "name": "Documentation", "description": "What to read first.",
                         "nodeIds": ["document:README.md", "config:pyproject.toml"]}],
             "tour": []}
    write(root, ".ua/knowledge-graph.json", json.dumps(graph))
    if domains:
        dnodes = [{"id": "domain:launch", "type": "domain", "name": "Launch", "summary": "Getting to orbit.",
                   "tags": ["t"], "complexity": "simple"},
                  {"id": "flow:liftoff", "type": "flow", "name": "Liftoff sequence", "summary": "Ignition to MECO.",
                   "tags": ["t"], "complexity": "simple"},
                  {"id": "step:liftoff:ignite", "type": "step", "name": "Ignite", "summary": "Engines on.",
                   "tags": ["t"], "complexity": "simple", "filePath": "app/physics.py"}]
        dedges = [edge("domain:launch", "flow:liftoff", "contains_flow"),
                  edge("flow:liftoff", "step:liftoff:ignite", "flow_step")]
        write(root, ".ua/domain-graph.json", json.dumps({"version": "1.0.0", "project": project, "nodes": dnodes,
                                                         "edges": dedges, "layers": [], "tour": []}))


def set_mtime(p: Path, when: float) -> None:
    os.utime(p, (when, when))


async def test_with_a_map_it_gives_layers_key_files_top_symbols_and_domains(ws):
    small_project(ws)
    write_map(ws)
    old = time.time() - 30 * 86400
    for p in ws.rglob("*"):
        if p.is_file() and ".ua" not in p.parts:
            set_mtime(p, old)                              # nothing changed since the map
    text = await outline_text()
    assert "Understand map" in text and ".ua/knowledge-graph.json" in text and "2026-09-20" in text
    assert "A three.js launch scene with a Python physics core." in text
    assert "Core simulation" in text and "Scene and physics." in text and "Documentation" in text
    assert "app/scene.py — Builds the scene graph and the camera rig." in text
    assert "build_scene" in text and "CameraRig" in text and "step" in text
    assert text.index("build_scene") < text.index("tiny_helper") if "tiny_helper" in text else True
    assert "- Launch — Getting to orbit. Flows: Liftoff sequence" in text   # domains and their flows
    assert "no Understand map" not in text and "Changed since the map" not in text
    assert len(text) <= LIMIT


async def test_with_a_map_it_names_files_changed_or_added_since_the_map(ws):
    small_project(ws)
    write_map(ws, analyzed="2026-09-20T10:00:00Z")
    built = 1_789_898_400.0                                   # 2026-09-20T10:00:00Z
    for p in ws.rglob("*"):
        if p.is_file() and ".ua" not in p.parts:
            set_mtime(p, built - 3600)
    set_mtime(ws / "app" / "physics.py", built + 3600)        # edited after the map
    write(ws, "app/new_module.py", '"""Added later."""\n')    # not in the map at all
    text = await outline_text()
    assert "Changed since the map" in text and "app/physics.py" in text
    assert "Not in the map" in text and "app/new_module.py" in text


async def test_without_a_domain_graph_the_map_outline_still_works(ws):
    small_project(ws)
    write_map(ws, domains=False)
    text = await outline_text()
    assert "Core simulation" in text and "Domains" not in text


async def test_a_big_map_stays_within_about_two_thousand_tokens(ws):
    nodes, edges, layers = [], [], []
    for l in range(7):
        ids = []
        for f in range(80):
            path = f"layer{l}/file{f:02d}.py"
            nodes.append(node("file", path, f"file{f:02d}.py", "Summary " + "words " * 30))
            ids.append(f"file:{path}")
            for s in range(6):
                nodes.append(node("function", path, f"fn_{f}_{s}", "Does a thing.", lineRange=[s * 10, s * 10 + 9]))
                edges.append(edge(f"file:{path}", f"function:{path}:fn_{f}_{s}", "contains"))
        layers.append({"id": f"layer:l{l}", "name": f"Layer {l}", "description": "A layer. " * 10, "nodeIds": ids})
    graph = {"version": "1.0.0", "project": {"name": "big", "description": "Big.", "analyzedAt": "2026-09-20T10:00:00Z",
                                            "languages": [], "frameworks": []},
             "nodes": nodes, "edges": edges, "layers": layers, "tour": []}
    write(ws, ".ua/knowledge-graph.json", json.dumps(graph))
    text = await outline_text()
    assert len(text) <= LIMIT, len(text)
    assert all(f"Layer {l}" in text for l in range(7))                     # every layer is named
    assert "not listed" in text


async def test_an_unreadable_map_falls_back_to_the_scan_and_says_why(ws):
    small_project(ws)
    write(ws, ".ua/knowledge-graph.json", "{not json")
    text = await outline_text()
    assert "could not read the Understand map" in text
    assert "app/scene.py" in text


async def test_a_map_folder_that_leads_out_of_the_workspace_is_not_read(ws, tmp_path):
    small_project(ws)
    elsewhere = tmp_path / "elsewhere"
    write_map(elsewhere)
    (ws / ".ua").symlink_to(elsewhere / ".ua", target_is_directory=True)
    text = await outline_text()
    assert "Core simulation" not in text and "no Understand map" in text


# --- the cache ----------------------------------------------------------------------------------------


async def test_the_outline_is_cached_until_a_file_changes(ws, monkeypatch):
    mod = _outline_module()
    small_project(ws)
    builds = []
    real = mod._render

    def counting(*a, **k):
        builds.append(1)
        return real(*a, **k)

    monkeypatch.setattr(mod, "_render", counting)
    first = await outline_text()
    assert await outline_text() == first and len(builds) == 1           # nothing changed: the cached text
    p = ws / "app" / "physics.py"
    p.write_text(p.read_text() + "z = 3\n")                              # an edit (size and mtime move)
    second = await outline_text()
    assert len(builds) == 2 and "app/physics.py (11 lines)" in second
    write(ws, "app/extra.py", '"""A new file."""\n')                     # a new file
    third = await outline_text()
    assert len(builds) == 3 and "app/extra.py" in third
    write_map(ws)                                                        # a map arrives
    fourth = await outline_text()
    assert len(builds) == 4 and "Core simulation" in fourth


async def test_the_cache_is_per_workspace(ws, tmp_path):
    small_project(ws)
    first = await outline_text()
    other = tmp_path / "other"
    write(other, "OTHER.md", "# The other project\n")
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=other, emit=None))
    second = await outline_text()
    assert "The other project" in second and "The other project" not in first


# --- a read, everywhere ----------------------------------------------------------------------------


def test_it_is_a_native_tool_in_its_own_module_so_its_schema_may_defer():
    tool = next(t for t in NATIVE_TOOLS if t.name == "project_outline")
    assert tool.handler.__module__ == "dream.tools.project_outline"
    assert tool.description.split(". ")[0].startswith("Outline this workspace in one call")


@pytest.mark.parametrize("mode", policy.MODES)
def test_it_is_read_only_and_free_in_every_mode(mode, tmp_path):
    assert policy.capability("project_outline") == policy.READONLY
    assert policy.capability("mcp__dream__project_outline") == policy.READONLY
    assert policy.decide("project_outline", {}, mode, tmp_path)[0] == "allow"


def test_a_custom_tool_cannot_claim_the_name(monkeypatch):
    from dream.tools import registry

    class Impostor:
        name = "project_outline"
        description = "not the real one"
        input_schema = {"type": "object", "properties": {}}

        async def handler(self, args):  # pragma: no cover - never registered
            return {"content": []}

    assert "project_outline" in policy.builtin_names()
    monkeypatch.setattr(registry, "_load_custom_tools", lambda: ([(Impostor(), Path("/tmp/impostor.py"))], []))
    built = registry.build()
    assert "project_outline" not in built["custom_names"]
    assert any("project_outline" in w and "collides with a built-in" in w for w in built["warnings"])


# --- the system prompt names it once and never carries the outline ----------------------------------


def test_the_system_prompt_names_the_tool_once_and_does_not_inject_the_outline(tmp_path):
    workspace = tmp_path / "ws"
    small_project(workspace)
    write(workspace, "app/distinct.py", '"""A PROMPT-HEAD-CANARY docstring."""\n')
    store = MemoryStore(tmp_path / "t.db")
    try:
        prompt = system_prompt.build_system_prompt(store, "s", workspace=workspace)
        again = system_prompt.build_system_prompt(store, "s", workspace=workspace)
    finally:
        store.close()
    head = prompt[:prompt.index(system_prompt.WAKE_HEADER)]
    assert head.count("project_outline") == 1 and prompt.count("project_outline") == 1
    assert "PROMPT-HEAD-CANARY" not in prompt and "app/scene.py" not in prompt
    assert again == prompt


# --- the DREAM-113 gate's findings -------------------------------------------------------------------


def long_rel(tag: str, depth: int = 14) -> str:
    """A path of about 2,900 characters: every component at the 200-byte limit's side of normal."""
    return "/".join([f"{tag}{'d' * 196}{i:02d}" for i in range(depth)] + [f"{tag}{'f' * 190}.py"])


def unusual_map(root: Path, *, languages=0, long_paths=0, names=False) -> None:
    """B2: maps like the ones that gave the gate 13,886 / 15,936 / 33,966 characters."""
    write_map(root)
    graph = json.loads((root / ".ua" / "knowledge-graph.json").read_text())
    project = graph["project"]
    if languages:
        project["languages"] = [f"language-number-{i:03d}-" + "x" * 30 for i in range(languages)]
        project["frameworks"] = [f"framework-number-{i:03d}-" + "y" * 30 for i in range(languages)]
    if names:
        project["name"] = "N" * 2_000
        project["description"] = "D" * 9_000
        for layer in graph["layers"]:
            layer["name"], layer["description"] = "L" * 3_000, "E" * 3_000
    for i in range(long_paths):
        rel = long_rel(f"m{i}")
        graph["nodes"].append(node("file", rel, "x.py", "S" * 400))
        graph["layers"][0]["nodeIds"].append(f"file:{rel}")
        write(root, rel, "x = 1\n")                                            # changed since the map
        write(root, long_rel(f"n{i}"), "y = 2\n")                              # not in the map
    (root / ".ua" / "knowledge-graph.json").write_text(json.dumps(graph))


@pytest.mark.parametrize("shape", [dict(languages=400), dict(long_paths=8), dict(languages=400, long_paths=8, names=True)])
async def test_an_unusual_map_stays_within_about_two_thousand_tokens(ws, shape):
    small_project(ws)
    unusual_map(ws, **shape)
    text = await outline_text()
    assert len(text) <= LIMIT, len(text)
    assert "Core simulation" in text or "LLLL" in text                  # still an outline of the map
    if shape.get("languages"):
        assert "language-number-000" in text and "more" in text.split("\n", 3)[2]
    if shape.get("long_paths"):
        head = text.split("\nLayers", 1)[0]
        assert "Changed since the map" in head and "Not in the map" in head
        assert max(len(line) for line in head.splitlines()) <= 700


async def test_long_paths_in_the_scan_stay_within_the_bound(ws):
    for i in range(12):
        write(ws, long_rel(f"s{i}", depth=4) + ".md", "# " + "H" * 3_000 + "\n")
    for i in range(8):
        write(ws, f"HANDOFF-{i}-" + "h" * 200 + ".md", "# handoff\n")
    text = await outline_text()
    assert len(text) <= LIMIT, len(text)
    assert max(len(line) for line in text.splitlines()) <= 260          # long paths are clipped, "…" in the middle
    assert sum(".md (1 line)" in line and "…" in line for line in text.splitlines()) >= 3   # so the deep files fit too


async def test_a_rebuilt_map_is_read_again(ws):
    """H3: the walk skips .ua, so only the map files' own mtime and size in the cache's digest see a rebuild."""
    small_project(ws)
    write_map(ws)
    assert "Core simulation" in await outline_text()
    p = ws / ".ua" / "knowledge-graph.json"
    graph = json.loads(p.read_text())
    graph["layers"][0]["name"] = "Rebuilt simulation core"
    p.write_text(json.dumps(graph))
    later = p.stat().st_mtime + 5
    os.utime(p, (later, later))
    text = await outline_text()
    assert "Rebuilt simulation core" in text and "Core simulation" not in text


@pytest.mark.parametrize("swap", ["file", "folder"])
async def test_a_file_or_folder_swapped_for_a_link_after_the_walk_is_not_read(ws, tmp_path, monkeypatch, swap):
    """S4, like DREAM-111's page server: the walk sees an innocent file, then it (or its folder) becomes a
    link out before the first lines are read. Every read opens one component at a time, never through a link."""
    mod = _outline_module()
    write(ws, "app/scene.py", '"""innocent scene"""\n')
    outside = tmp_path / "outside"
    write(outside, "scene.py", '"""OUTSIDE-SECRET scene"""\n')
    walked = mod._walk

    def racing(root):
        result = walked(root)
        if swap == "file":
            (ws / "app" / "scene.py").unlink()
            (ws / "app" / "scene.py").symlink_to(outside / "scene.py")
        else:
            import shutil
            shutil.rmtree(ws / "app")
            (ws / "app").symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(mod, "_walk", racing)
    text = await outline_text()
    assert "OUTSIDE-SECRET" not in text
    assert "app/scene.py (unreadable)" in text


async def test_a_map_file_that_links_out_is_named_in_the_header(ws, tmp_path):
    """B6: the .ua folder is the workspace's own; only its map file links out, and the header names that file."""
    small_project(ws)
    elsewhere = tmp_path / "elsewhere"
    write_map(elsewhere)
    (ws / ".ua").mkdir()
    (ws / ".ua" / "knowledge-graph.json").symlink_to(elsewhere / ".ua" / "knowledge-graph.json")
    text = await outline_text()
    assert ".ua/knowledge-graph.json leads outside it and is not read" in text
    assert ".ua/ leads outside" not in text and "Core simulation" not in text
