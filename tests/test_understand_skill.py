"""DREAM-102: the compact Dream-native `understand` skill -- a repository map for the Understand panel, step by step.

Owner, 2026-09-23: [feedback] The plugin's 58,868-byte
SKILL.md is written for Claude Code subagents and a shell that can see the plugin folder; in Dream the scripts run
with run_bash, which finds the plugin folder as `$UA_SKILLS` (DREAM-105), and the live MiMo run spent
~90 tool calls re-deriving its environment.

What is pinned here: (1) the curated package and its size; (2) the four `.ua/` files in the dashboard's shapes, checked
by the skill's own glue; (3) the curated `understand` outranks the plugin's for the name and the map phrases while the
plugin's skill stays reachable; (4) a dry run of every deterministic step on a fixture repository through the real
run_bash (on the host, no sandbox since 2026-10-11) -- the commands come from the SKILL.md itself -- with the model's
part hand-written; (5) the suites.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import dream.config as config
from dream import plugins
from dream.skills import loader
from dream.skills.selection import MAX_GUIDANCE_CHARS, guidance_budget, select_for_task
from dream.tools import installed_skill_tools

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "understand"
GLUE = SKILL / "glue.py"
UA_PLUGIN = Path(os.environ.get("UA_DIR", str(Path.home() / ".understand-anything" / "repo"))).expanduser() / "understand-anything-plugin"
UA_SKILLS = UA_PLUGIN / "skills"
SCRIPTS = ("scan-project.mjs", "extract-import-map.mjs", "compute-batches.mjs", "extract-structure.mjs",
           "merge-batch-graphs.py", "build-fingerprints.mjs")
# the dock's request (dream/gui/static/understand.js ASK), the explicit selection that must keep working
ASK = ("Use the understand skill (Understand-Anything) to map this project into .ua/knowledge-graph.json, then tell me "
       "when the map is ready. Run its helper scripts with run_bash from $UA_SKILLS (the plugin is installed and built; "
       "skip locating it). Exclude Dream's own state folders: pass --exclude \".dream/**,.remember/**\" to its status "
       "command.")

needs_plugin = pytest.mark.skipif(
    not (UA_SKILLS / "understand" / "scan-project.mjs").is_file()
    or not (UA_PLUGIN / "packages" / "core" / "dist" / "index.js").is_file() or shutil.which("node") is None,
    reason="needs the Understand-Anything clone with its built core, and node (see plugins/understand-anything)")


def _module(path: Path, name: str):
    """Import a script by path without leaving a __pycache__ beside it (skill_open would list one as a bundled file)."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    before, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = before
    return mod


@pytest.fixture(scope="module")
def curated():
    skills, warnings = loader.discover(config.bundled_skill_dirs())
    assert not warnings, warnings
    return skills


@pytest.fixture(scope="module")
def skill_text() -> str:
    return (SKILL / "SKILL.md").read_text(encoding="utf-8")


def _body(text: str) -> str:
    return text.split("---", 2)[2]


# ---- (1) the package -------------------------------------------------------------------------------------------------

def test_it_ships_as_a_small_curated_package(curated, skill_text):
    by_name = {s.name: s for s in curated}
    assert "understand" in config.CURATED_SKILLS and "understand" in by_name
    skill = by_name["understand"]
    assert skill.curated and skill.root == SKILL
    assert skill.description.startswith("Use when") and "knowledge graph" in skill.description
    assert len(skill_text) <= 8000, len(skill_text)                     # whole even on a 16k window (guidance_budget = 9,830)
    # Where the cap comes from (2026-10-02): the loader reads a SKILL.md up to BODY_MAX_CHARS, but a session is GIVEN
    # 15 % of its window in characters (selection.guidance_budget) and a longer workflow is cut with "[Workflow
    # shortened.". 8,000 is this skill's own budget (DREAM-102) and must stay inside the smallest window's share.
    assert 8000 < guidance_budget(16384) < loader.BODY_MAX_CHARS
    manifest = json.loads((SKILL / "manifest.json").read_text())
    assert manifest["format"] == "dream-skill/v1" and manifest["name"] == "understand" and manifest["portable"] is True
    assert {"run_bash", "read_file", "write_file", "skill_file"} <= set(manifest["capabilities"])
    wheel = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert "skills/understand" in wheel["only-include"]
    assert GLUE.is_file() and (SKILL / "references" / "schema.md").is_file()
    # 2026-10-01: the glue's fallback copies the real file with the shell ($DREAM_SKILLS, readable by run_bash),
    # never rebuilt from skill_file's text, which shortens long files
    assert "references/schema.md" in skill_text and 'cp "$DREAM_SKILLS/understand/glue.py"' in skill_text
    # the bundle is what skill_open lists, so the model can fetch the two files it names -- and nothing else is there
    assert set(loader.bundled_names(skill)) == {"glue.py", "manifest.json", "references/schema.md"}


def test_the_pipeline_is_imperative_and_names_every_script_and_result(skill_text):
    body = _body(skill_text)
    for script in SCRIPTS:
        assert script in body, script
    for name in ("knowledge-graph.json", "domain-graph.json", "diff-overlay.json", "meta.json", ".understandignore"):
        assert name in body, name
    assert "--exclude-analysis-data" in body and "--exclude" in body
    assert "skill_file(" in body and "ua_run" not in body
    # DREAM-105: every script runs with run_bash from $UA_SKILLS, the plugin folder
    for script in SCRIPTS:
        assert f'"$UA_SKILLS/understand/{script}"' in body, script
    # numbered steps a local model follows in order, and every glue command the dry run uses
    steps = re.findall(r"^## (\d+)\. ", body, re.M)
    assert steps == [str(i) for i in range(1, len(steps) + 1)] and len(steps) >= 8, steps
    for command in ("status", "imports-input", "scan-result", "batch-inputs", "structure", "tasks", "brief", "draft",
                    "nodes", "assemble", "finish"):
        assert f"`{command}" in body or f"ua_glue.py {command}" in body, command
    # no confirmation pauses, no gate that stops, no shell trip into the plugin folder
    assert not re.search(r"wait for (?:the user'?s? )?confirmation|confirm to continue|ask (?:the user )?(?:whether|if) ", body, re.I)
    assert not re.search(r"\b(?:cd|ls|find)\b[^\n]*(?:\.understand-anything|SKILL_DIR|PLUGIN_ROOT)", body)
    assert re.search(r"over 100 files", body, re.I)
    # gate note 7: no hand-off to the plugin's file-analyzer (its prompt shells into the plugin dir and emits `step:` nodes).
    # Owner decision 2026-10-01: the analysis goes to Dream's own workers, as the upstream skill runs its analyzers in
    # parallel (test_step_4_sends_every_worker_task_in_one_reply has the dispatch).
    assert "file-analyzer" not in body and "analyses every batch itself" not in body
    assert "parts" in body or "part-" in body                          # long batches are split, never edited in place
    assert "legacy" in body and ".understand-anything/" in body        # gate note 5: the legacy data folder is explained


def test_the_default_excludes_are_the_glues_ignore_file(skill_text, glue):
    """DREAM-106 moved the default .understandignore from a fenced block the model copied into the glue: `status` writes
    it when it is missing, before it compares anything, so the scan and the status check use the same rules."""
    assert ".understandignore" in _body(skill_text) and "```\n.ua/" not in skill_text
    lines = {line.strip() for line in glue.DEFAULT_IGNORE.splitlines() if line.strip()}
    assert {".ua/", ".understand-anything/", ".dream/", ".remember/"} <= lines
    assert any(line.startswith("*.") for line in lines)                 # binary patterns the scan's defaults lack


def test_the_first_real_action_is_the_status_check(skill_text):
    """DREAM-106 (1): step 1 asks the glue for the map's state before anything is scanned, and follows it: a current map
    stops the run, update mode redoes only what changed, no map runs the whole pipeline."""
    body = _body(skill_text)
    step1 = body.split("## 1.", 1)[1].split("## 2.", 1)[0]
    assert "`status`" in step1 and "scan-project.mjs" not in step1
    assert "The map is current" in step1 and "stop" in step1.lower()
    assert "update mode" in step1 and "no map" in step1
    assert step1.index("`status`") < step1.index("project.json")        # README and manifest only for a full map
    assert "draft" in body and "150 files" in body                     # the big-repo path is part of the skill


# ---- (3) selection and name precedence ------------------------------------------------------------------------------

@pytest.mark.parametrize("prompt", [
    ASK, "$understand", "use the understand skill on this repository",
    "Map this repo", "map this codebase please", "Map the whole project into a knowledge graph",
    "understand map", "Run the Understand-Anything map on this project", "knowledge graph of this project",
    "Build a code map of this repository for the Understand panel", "/understand",
])
def test_map_phrases_select_the_curated_skill_first(curated, prompt):
    guidance = select_for_task(prompt, curated)
    assert guidance.names and guidance.names[0] == "understand", (prompt, guidance.names)


@pytest.mark.parametrize("prompt", [
    "I don't understand this error; why does the test fail?",
    "Map the API response fields to a DTO class",
    "Fix the checkout button bug and add a regression test.",
    "Create a sitemap for the marketing website",
    "What is a knowledge base? Explain in two sentences.",
    "Thanks, that helped.",
    # gate note 4: a map as a thing in the code, or mapping one thing onto another
    "Fix the bug in the project map view",
    "Map this code to the new API",
    "Add a code map widget",
])
def test_ordinary_words_do_not_select_it(curated, prompt):
    assert "understand" not in select_for_task(prompt, curated).names, prompt


def test_the_dock_prompt_carries_the_whole_workflow_on_a_32k_window(curated):
    for window in (16384, 32768, 200_000):
        guidance = select_for_task(ASK, curated, max_chars=guidance_budget(window))
        assert guidance.names[0] == "understand"
        assert "[Workflow shortened." not in guidance.text and not guidance.warnings, window
        assert "## 9." in guidance.text or "## 8." in guidance.text     # the last steps made it in
    short = select_for_task(ASK, curated)                               # a small or unknown window: today's behaviour
    assert short.names[0] == "understand" and len(short.text) <= MAX_GUIDANCE_CHARS


def test_the_ask_copy_is_the_docks_own_request():
    """DREAM-106: the dock's request routes Dream's excludes through the glue's `status` (it prints the scan command);
    this copy is what the selection tests above use, so it must be the dock's text exactly."""
    if shutil.which("node") is None:
        pytest.skip("needs node to evaluate the dock's string")
    source = (ROOT / "dream" / "gui" / "static" / "understand.js").read_text(encoding="utf-8")
    literal = re.search(r"const ASK = ([\s\S]*?);\n", source).group(1)
    run = subprocess.run(["node", "-e", f"process.stdout.write({literal})"], capture_output=True, text=True, check=True)
    assert run.stdout == ASK and "status command" in ASK


@pytest.fixture
def with_plugin(monkeypatch):
    if not (ROOT / "plugins" / "understand-anything" / "skills" / "understand" / "SKILL.md").is_file():
        pytest.skip("the Understand-Anything plugin's skills are not installed (plugins/understand-anything/skills)")
    monkeypatch.delenv("DREAM_SKILL_DIRS", raising=False)
    monkeypatch.delenv("DREAM_EXTERNAL_SKILLS", raising=False)
    monkeypatch.setattr(installed_skill_tools, "_CACHE", None)
    plugins.load(ROOT / "plugins")                                     # conftest hands the roster back afterwards
    assert any(p.name == "understand-anything" and p.enabled for p in plugins.loaded())
    return installed_skill_tools.installed(refresh=True)


def test_the_curated_skill_wins_the_name_and_the_plugin_stays_reachable(with_plugin):
    by_name = {s.name: s for s in with_plugin}
    assert by_name["understand"].curated and by_name["understand"].root == SKILL
    alias = by_name["understand-anything:understand"]
    assert not alias.curated and alias.provenance == "dream-plugin"
    assert alias.root.resolve() == (UA_SKILLS / "understand").resolve()
    # DREAM-103: Dream's curated understand-dashboard and understand-domain hold those two bare names; the plugin's
    # copies are aliased like its `understand`
    for sibling in ("understand-chat", "understand-anything:understand-dashboard", "understand-diff",
                    "understand-anything:understand-domain", "understand-explain", "understand-figma",
                    "understand-knowledge", "understand-onboard"):
        assert sibling in by_name and by_name[sibling].provenance == "dream-plugin", sibling
    assert any("reachable as 'understand-anything:understand'" in n for n in installed_skill_tools.notes())
    assert not any("understand-anything:understand'" in w for w in installed_skill_tools.warnings())  # DREAM-170
    # selection: the dock's prompt and $understand pick the curated one; the qualified name picks the plugin's
    picked = select_for_task(ASK, with_plugin, max_chars=guidance_budget(200_000))
    assert picked.names == ("understand",) and '"$UA_SKILLS/understand/' in picked.text and "Phase 0" not in picked.text
    assert select_for_task("$understand map it", with_plugin).names == ("understand",)
    assert select_for_task("$understand-anything:understand", with_plugin).names == ("understand-anything:understand",)
    # skill_open: both by name
    ours = asyncio.run(installed_skill_tools.skill_open.handler({"name": "understand"}))
    theirs = asyncio.run(installed_skill_tools.skill_open.handler({"name": "understand-anything:understand"}))
    assert not ours.get("is_error") and '"$UA_SKILLS/understand/' in ours["content"][0]["text"]
    assert not theirs.get("is_error") and "Phase 0" in theirs["content"][0]["text"]


def test_a_plain_collision_still_keeps_only_the_first_root(tmp_path):
    """Only a plugin-owned duplicate gets the qualified name; two plain roots behave as before (see test_skill_loader)."""
    for root, desc in (("a", "override"), ("b", "copy")):
        d = tmp_path / root / "dup"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: dup\ndescription: {desc}\n---\nbody\n")
    skills, warnings = loader.discover([tmp_path / "a", tmp_path / "b"])
    assert [s.name for s in skills] == ["dup"] and any("shadowed" in w for w in warnings)


# ---- (2) the schema check -------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def glue():
    return _module(GLUE, "ua_glue_under_test")


def _graph(**over):
    g = {"version": "1.0.0",
         "project": {"name": "p", "languages": ["python"], "frameworks": [], "description": "d",
                     "analyzedAt": "2026-09-23T00:00:00Z", "gitCommitHash": "abc"},
         "nodes": [{"id": "file:a.py", "type": "file", "name": "a.py", "filePath": "a.py", "summary": "s",
                    "tags": ["t"], "complexity": "simple"},
                   {"id": "function:a.py:f", "type": "function", "name": "f", "filePath": "a.py", "summary": "s",
                    "tags": ["t"], "complexity": "simple", "lineRange": [1, 12]}],
         "edges": [{"source": "file:a.py", "target": "function:a.py:f", "type": "contains", "direction": "forward", "weight": 1.0}],
         "layers": [{"id": "layer:core", "name": "Core", "description": "d", "nodeIds": ["file:a.py"]}],
         "tour": [{"order": 1, "title": "t", "description": "d", "nodeIds": ["file:a.py"]}]}
    g.update(over)
    return g


def test_the_check_knows_the_13_node_types_and_26_edge_types(glue):
    assert len(glue.NODE_TYPES) == 13 and len(glue.EDGE_TYPES) == 26
    assert set(glue.FILE_TYPES) < set(glue.NODE_TYPES) and {"function", "class", "module", "concept"} < set(glue.NODE_TYPES)
    assert glue.check_graph(_graph(), glue.NODE_TYPES, glue.EDGE_TYPES, layered=True) == []


@pytest.mark.parametrize("change, expected", [
    (lambda g: g["nodes"][1].__setitem__("type", "method"), "type 'method'"),
    (lambda g: g["edges"][0].__setitem__("type", "uses"), "type 'uses'"),
    (lambda g: g["edges"].append({"source": "file:a.py", "target": "file:zz.py", "type": "imports", "direction": "forward", "weight": 0.7}), "file:zz.py"),
    (lambda g: g["layers"][0].__setitem__("nodeIds", []), "file:a.py is in 0 layers"),
    (lambda g: g["project"].pop("gitCommitHash"), "project lacks gitCommitHash"),
    (lambda g: g["nodes"][0].__setitem__("tags", []), "lacks tags"),
    (lambda g: g["edges"][0].__setitem__("weight", 1.5), "weight"),
    (lambda g: g["nodes"].append(dict(g["nodes"][0])), "duplicate"),
    # gate: understand_routes previews depend on filePath -- a file-level node without one, or with an absolute one
    (lambda g: g["nodes"][0].pop("filePath"), "lacks filePath"),
    (lambda g: g["nodes"][0].__setitem__("filePath", "/abs/a.py"), "relative"),
])
def test_the_check_names_each_schema_problem(glue, change, expected):
    graph = _graph()
    change(graph)
    problems = glue.check_graph(graph, glue.NODE_TYPES, glue.EDGE_TYPES, layered=True)
    assert problems and any(expected in p for p in problems), problems


def test_the_domain_check_uses_the_domain_types(glue):
    domain = _graph(nodes=[
        {"id": "domain:launches", "type": "domain", "name": "Launches", "summary": "s", "tags": ["t"], "complexity": "simple"},
        {"id": "flow:record", "type": "flow", "name": "Record", "summary": "s", "tags": ["t"], "complexity": "simple"},
        {"id": "step:record:save", "type": "step", "name": "Save", "summary": "s", "tags": ["t"], "complexity": "simple", "filePath": "a.py"}],
        edges=[{"source": "domain:launches", "target": "flow:record", "type": "contains_flow", "direction": "forward", "weight": 1.0},
               {"source": "flow:record", "target": "step:record:save", "type": "flow_step", "direction": "forward", "weight": 0.1}],
        layers=[], tour=[])
    assert glue.check_graph(domain, glue.DOMAIN_NODES, glue.DOMAIN_EDGES, layered=False) == []
    assert any("type 'file'" in p for p in glue.check_graph(_graph(layers=[], tour=[]), glue.DOMAIN_NODES, glue.DOMAIN_EDGES, layered=False))


# ---- step 4: the analysis goes to workers ----------------------------------------------------------------------------
# Owner, 2026-10-02: the skill mapped a project with ONE agent ("The lead analyses every batch itself; no subagents.").
# What the owner's runs of 2026-10-02 showed (the engine's and Dream's runtime logs): the lead sent 2-5 `task` calls a
# reply where it had sent 15 the night before; five workers that answered with their whole analysis ran into the
# 16,384-token reply cap after ~265 s each and came back "treat as incomplete". So: the glue cuts the batches into
# tasks that fit a worker's lane, the lead's `task` prompts are one line each (all of them fit one reply), a worker
# writes its own batch file and answers three lines, and `tasks` names what is still missing.

LANE_TOKENS = 32768        # a worker's context on a local engine: the lane size (engine.slot_ctx's default)
REPLY_TOKENS = 16384       # the reply cap the engine logged for every worker request
TRIGGER = 0.80             # behaviour.context_trigger's default: past it a worker stops and returns its report
# Estimates, not measurements: a source line as read_file returns it, a node with its edges as compact JSON, and the
# worker's system text with its tool schemas (1,159 to 1,720 tokens in the owner's runs).
TOKENS_PER_LINE, TOKENS_PER_NODE, WORKER_BASE_TOKENS, CHARS_PER_TOKEN = 12, 170, 2000, 3


def _hand_project(root: Path, batches: list[list[dict]]) -> None:
    """A project as the scripts of steps 2 to 4 leave it before the workers start, hand-written so the plugin is not
    needed. A file is {"path", "lines", "symbols": [(kind, name, start, end)], "imports", "category", "language"}."""
    files = [f for b in batches for f in b]
    for f in files:
        p = root / f["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(f.get("bytes") or b"x = 1\n" * f["lines"])

    def entry(f):
        return {"path": f["path"], "language": f.get("language", "python"), "sizeLines": f["lines"],
                "fileCategory": f.get("category", "code")}
    inter, tmp = root / ".ua" / "intermediate", root / ".ua" / "tmp"
    inter.mkdir(parents=True)
    tmp.mkdir(parents=True)
    (inter / "scan-result.json").write_text(json.dumps({
        "name": "hand", "description": "d", "languages": ["python"], "frameworks": [], "totalFiles": len(files),
        "files": [entry(f) for f in files], "importMap": {f["path"]: f.get("imports", []) for f in files}}))
    (inter / "batches.json").write_text(json.dumps({"totalFiles": len(files), "totalBatches": len(batches), "batches": [
        {"batchIndex": i, "files": [entry(f) for f in b], "neighborMap": {},
         "batchImportData": {f["path"]: f.get("imports", []) for f in b}} for i, b in enumerate(batches, 1)]}))
    for i, b in enumerate(batches, 1):
        (tmp / f"extract-out-{i}.json").write_text(json.dumps({"results": [
            {"path": f["path"], "totalLines": f["lines"], "exports": [],
             "functions": [{"name": n, "startLine": a, "endLine": z, "params": []}
                           for kind, n, a, z in f.get("symbols", []) if kind == "function"],
             "classes": [{"name": n, "startLine": a, "endLine": z, "methods": ["get", "put"]}
                         for kind, n, a, z in f.get("symbols", []) if kind == "class"]}
            for f in b if not f.get("bytes")]}))
    shutil.copy(GLUE, tmp / "ua_glue.py")


def _plan(root: Path) -> list[dict]:
    return json.loads((root / ".ua/tmp/tasks.json").read_text())["tasks"]


def _size(task: dict) -> tuple[int, int, int]:
    """(lines a worker reads, nodes it writes, files it touches) for one task of the plan."""
    lines = sum(z - a + 1 for u in task["units"] for a, z in u["reads"])
    nodes = sum((1 if u["head"] else 0) + len(u["symbols"]) for u in task["units"])
    return lines, nodes, len(task["units"])


def _part(task: dict, extra_edges=()) -> dict:
    """A task's batch file exactly as its brief tells the worker to write it: the file nodes that are this task's, a
    node per listed symbol with its line range, `contains` for each, one `imports` edge per listed target."""
    nodes, edges = [], []
    for u in task["units"]:
        if u["head"]:
            nodes.append({"id": u["node"], "type": u["node"].split(":", 1)[0], "name": Path(u["path"]).name,
                          "filePath": u["path"], "summary": f"Fixture summary of {u['path']}: what it does and its role.",
                          "tags": ["fixture", "rocketlog", "test" if u["path"].startswith("tests/") else "source"],
                          "complexity": "simple" if u["lines"] < 50 else "moderate" if u["lines"] <= 200 else "complex"})
            edges += [{"source": u["node"], "target": t, "type": "imports", "direction": "forward", "weight": 0.7}
                      for t in u["imports"]]
        for s in u["symbols"]:
            nodes.append({"id": s["id"], "type": s["kind"], "name": s["name"], "filePath": u["path"],
                          "lineRange": s["lines"], "summary": f"Fixture {s['kind']} {s['name']}.",
                          "tags": ["fixture", s["kind"], "python"], "complexity": "simple"})
            edges.append({"source": u["node"], "target": s["id"], "type": "contains", "direction": "forward", "weight": 1.0})
    written = {n["id"] for n in nodes}
    edges += [{"source": a, "target": b, "type": t, "direction": "forward", "weight": w}
              for a, b, t, w in extra_edges if a in written]
    return {"nodes": nodes, "edges": edges}


def _write_part(root: Path, task: dict, extra_edges=()) -> None:
    (root / task["file"]).write_text(json.dumps(_part(task, extra_edges), separators=(",", ":")))


BIG = [  # batch 1: twenty small modules; batch 2: one long module of 120 functions; batch 3: a document and a binary
    [{"path": f"pkg/mod_{k:02d}.py", "lines": 90, "symbols": [("function", "load", 5, 30), ("class", "Box", 40, 80)],
      "imports": [f"pkg/mod_{(k + 1) % 20:02d}.py"]} for k in range(20)],
    [{"path": "pkg/long.py", "lines": 2400, "symbols": [("function", f"step_{k:03d}", 1 + 20 * k, 18 + 20 * k) for k in range(120)],
      "imports": ["pkg/mod_00.py"]}],
    [{"path": "README.md", "lines": 30, "category": "docs", "language": "markdown"},
     {"path": "pkg/logo.bin", "lines": 1, "bytes": b"\x00\x01\x02binary"}],
]


def test_tasks_cut_every_batch_to_fit_a_worker_lane(tmp_path, glue):
    _hand_project(tmp_path, BIG)
    _glue(tmp_path, "tasks")
    plan = _plan(tmp_path)
    assert len(plan) > len(BIG)                                          # batches of 20 files and 2,400 lines are cut
    for task in plan:
        lines, nodes, files = _size(task)
        assert lines <= glue.WORKER_LINES and nodes <= glue.WORKER_NODES and files <= glue.WORKER_FILES, task["id"]
        assert all(z - a + 1 <= 200 for u in task["units"] for a, z in u["reads"])      # one read_file call each
        # the lane: the brief, what it reads, the JSON it writes -- and once more for a rewrite after a failed check
        brief = _glue(tmp_path, "brief", task["id"])
        assert len(brief) <= config.TOOL_RESULT_CAP // 2                 # one run_bash result, never clamped
        written = nodes * TOKENS_PER_NODE
        assert written <= REPLY_TOKENS // 2, (task["id"], written)       # one write_file call inside the reply cap
        fill = WORKER_BASE_TOKENS + len(brief) // CHARS_PER_TOKEN + lines * TOKENS_PER_LINE + 2 * written
        assert fill <= TRIGGER * LANE_TOKENS, (task["id"], fill)
    # nothing is lost or written twice: every file's node in exactly one task, every node-worthy symbol in one
    heads = [u["node"] for task in plan for u in task["units"] if u["head"]]
    assert sorted(heads) == sorted([f"file:pkg/mod_{k:02d}.py" for k in range(20)]
                                   + ["file:pkg/long.py", "document:README.md", "file:pkg/logo.bin"])
    symbols = [s["id"] for task in plan for u in task["units"] for s in u["symbols"]]
    assert len(symbols) == len(set(symbols)) == 20 * 2 + 120
    assert all(s["lines"][1] - s["lines"][0] >= 9 or s["kind"] == "class" for t in plan for u in t["units"] for s in u["symbols"])
    # the long file: its first task has the node and the head, the later ones only their own run of symbols
    long_units = [u for task in plan for u in task["units"] if u["path"] == "pkg/long.py"]
    assert len(long_units) >= 4 and [u["head"] for u in long_units] == [True] + [False] * (len(long_units) - 1)
    assert long_units[0]["reads"][0][0] == 1 and long_units[1]["reads"][0][0] > 1
    binary = next(u for task in plan for u in task["units"] if u["path"] == "pkg/logo.bin")
    assert binary["reads"] == [] and binary["binary"]
    # the names are the merge's: batch-<i>.json for a batch that is one task, else parts 1..n
    by_batch: dict[int, list[str]] = {}
    for task in plan:
        assert glue.BATCH_NAME.match(Path(task["file"]).name), task["file"]
        by_batch.setdefault(task["batch"], []).append(Path(task["file"]).name)
    assert by_batch[3] == ["batch-3.json"]
    for i in (1, 2):
        assert by_batch[i] == [f"batch-{i}-part-{k}.json" for k in range(1, len(by_batch[i]) + 1)] and len(by_batch[i]) > 1
    assert [task["id"] for task in plan] == [f"{task['batch']}-{k}" for i in (1, 2, 3)
                                             for k, task in enumerate([t for t in plan if t["batch"] == i], 1)]


def test_the_task_listing_has_the_lead_send_every_task_in_one_reply(tmp_path):
    _hand_project(tmp_path, BIG)
    out = _glue(tmp_path, "tasks")
    plan = _plan(tmp_path)
    assert f"{len(plan)} worker tasks" in out
    assert "ONE reply" in out and "only `task` calls" in out and f"ALL {len(plan)}" in out
    assert 'subagent_type "coder"' in out                                # the worker writes a file and runs the check
    assert "queues the rest" in out and "Do not send a few and wait" in out
    # the prompt is one line, so every call fits the lead's one reply; the ids are on one line, not a line each
    prompt = next(line.strip() for line in out.splitlines() if "ua_glue.py brief <id>" in line)
    assert len(prompt) < 120 and "run_bash" in prompt
    ids = next(line for line in out.splitlines() if line.startswith("ids:"))
    assert ids.split()[1:] == [task["id"] for task in plan]
    assert len(out.splitlines()) <= 8 and "rerun glue tasks" in out
    assert len(plan) * (len(prompt) + 80) // CHARS_PER_TOKEN < REPLY_TOKENS // 4    # all the calls, a quarter of a reply


def test_a_brief_is_the_workers_whole_instruction(tmp_path, glue):
    _hand_project(tmp_path, BIG)
    _glue(tmp_path, "tasks")
    plan = _plan(tmp_path)
    first = plan[0]
    brief = _glue(tmp_path, "brief", first["id"])
    assert f"task {first['id']}" in brief and first["file"] in brief
    # what to read, by LINE: the owner's runs read with `limit`, which counts characters
    assert "start_line=1 line_count=90" in brief and "never offset or limit" in brief
    for u in first["units"]:
        assert u["path"] in brief and u["node"] in brief
        assert all(target in brief for target in u["imports"])
        assert all(f"{s['kind']} {s['name']} [{s['lines'][0]}, {s['lines'][1]}]" in brief for s in u["symbols"])
    # what to write, how to check it, what to answer
    assert "write_file" in brief and "ONE call" in brief and '{"nodes": [...], "edges": [...]}' in brief
    assert f"python3 .ua/tmp/ua_glue.py check {first['id']}" in brief
    assert "three lines" in brief and "never the JSON" in brief
    assert f"task {first['id']}: {first['file']}" in brief and "\ncheck: " in brief and "\nnotes: " in brief
    # the rules are the schema's: every edge type the brief names is one the check accepts
    named = set(re.findall(r"\b([a-z_]+) \d\.\d\b", brief))
    assert {"contains", "imports", "calls", "tested_by", "documents"} <= named <= set(glue.EDGE_TYPES)
    assert all(f"`{t}`" in brief or f'"{t}"' in brief for t in ("simple", "moderate", "complex"))
    # a later run of a long file: its own lines only, and the file's node is not its to write
    later = next(task for task in plan if task["units"][0]["path"] == "pkg/long.py" and not task["units"][0]["head"])
    text = _glue(tmp_path, "brief", later["id"])
    a, z = later["units"][0]["reads"][0][0], later["units"][0]["reads"][-1][1]
    assert f"lines {a}-{z} of 2400" in text and "start_line=1 " not in text
    assert "another task writes" in text and "file:pkg/long.py" in text
    # the binary is never read
    assert "do not read it" in _glue(tmp_path, "brief", plan[-1]["id"])
    rc, out, err = _glue_rc(tmp_path, "brief", "9-9")
    assert rc == 1 and "Traceback" not in err and "no task 9-9" in out and plan[0]["id"] in out


def test_the_check_passes_a_part_written_as_briefed_and_names_what_is_wrong(tmp_path):
    _hand_project(tmp_path, BIG)
    _glue(tmp_path, "tasks")
    plan = _plan(tmp_path)
    task, path = plan[0], tmp_path / plan[0]["file"]
    rc, out, _ = _glue_rc(tmp_path, "check", task["id"])
    assert rc == 1 and "no " + task["file"] in out                       # nothing written yet
    _write_part(tmp_path, task)
    lines, nodes, files = _size(task)
    out = _glue(tmp_path, "check", task["id"])
    assert out.strip().splitlines()[-1].startswith(f"ok: {task['file']}") and f"{nodes} nodes" in out
    good = json.loads(path.read_text())

    def broken(change) -> str:
        part = json.loads(json.dumps(good))
        change(part)
        path.write_text(json.dumps(part))
        rc, out, err = _glue_rc(tmp_path, "check", task["id"])
        assert rc == 1 and "Traceback" not in err and "ok:" not in out, out
        assert "write the whole file again" in out
        return out
    head = task["units"][0]["node"]
    dropped = broken(lambda p: p["nodes"].pop(0))
    assert head in dropped and "is missing" in dropped
    stray = broken(lambda p: p["nodes"].append(dict(p["nodes"][0], id="file:pkg/mod_19.py", filePath="pkg/mod_19.py")))
    assert "file:pkg/mod_19.py is not this task's" in stray
    assert "lacks summary" in broken(lambda p: p["nodes"][0].__setitem__("summary", ""))
    assert "type 'uses'" in broken(lambda p: p["edges"][0].__setitem__("type", "uses"))
    assert "function:nowhere.py:f is not a node" in broken(lambda p: p["edges"][0].__setitem__("target", "function:nowhere.py:f"))
    # an edge may name any scanned file's node or any symbol another task writes: the merge joins the parts
    other = plan[-1]["units"][0]["node"]
    path.write_text(json.dumps(dict(good, edges=good["edges"] + [
        {"source": head, "target": other, "type": "related", "direction": "forward", "weight": 0.5},
        {"source": head, "target": "function:pkg/long.py:step_119", "type": "calls", "direction": "forward", "weight": 0.8}])))
    assert "ok:" in _glue(tmp_path, "check", task["id"])
    path.write_text(json.dumps(good)[: len(json.dumps(good)) // 2])     # a reply cut at the token limit
    rc, out, err = _glue_rc(tmp_path, "check", task["id"])
    assert rc == 1 and "not valid JSON" in out and "Traceback" not in err


def test_tasks_names_what_is_missing_then_once_more_then_do_it_yourself(tmp_path):
    _hand_project(tmp_path, BIG)
    first = _glue(tmp_path, "tasks")
    plan = _plan(tmp_path)
    missing, bad = plan[1], plan[-1]
    for task in plan:
        if task is not missing:
            _write_part(tmp_path, task)
    (tmp_path / bad["file"]).write_text('{"nodes": [], "edges": []}')     # a worker that wrote an empty file
    second = _glue(tmp_path, "tasks")
    assert f"{len(plan) - 2} of {len(plan)} tasks done" in second
    assert f"{missing['id']} (no file)" in second and f"{bad['id']} (" in second and "is missing" in second
    assert "once more" in second and "ONE reply" in second and "ua_glue.py brief <id>" in second
    assert first != second and "yourself" not in second
    third = _glue(tmp_path, "tasks")                                      # still missing after the second round
    assert f"{missing['id']} (no file)" in third and "yourself" in third and f"brief {missing['id']}" in third
    assert "once more" not in third
    _write_part(tmp_path, missing)
    _write_part(tmp_path, bad)
    done = _glue(tmp_path, "tasks")
    assert f"all {len(plan)} tasks done" in done and "merge (step 5)" in done
    # the next run starts over: batch-inputs clears the batch files and the rounds, and the parser's old output is
    # not taken for the new batches'
    _glue(tmp_path, "batch-inputs")
    assert not (tmp_path / ".ua/tmp/tasks.json").exists() and not (tmp_path / missing["file"]).exists()
    rc, out, err = _glue_rc(tmp_path, "tasks")
    assert rc == 1 and "older than" in out and "extract-structure.mjs" in out and "Traceback" not in err
    for i in (1, 2, 3):
        os.utime(tmp_path / f".ua/tmp/extract-out-{i}.json", None)        # the parser run again
    assert f"ALL {len(plan)}" in _glue(tmp_path, "tasks")


def test_tasks_before_the_parser_names_the_missing_step(tmp_path):
    _hand_project(tmp_path, BIG)
    (tmp_path / ".ua/tmp/extract-out-2.json").unlink()
    rc, out, err = _glue_rc(tmp_path, "tasks")
    assert rc == 1 and "Traceback" not in err and "extract-out-2.json" in out and "extract-structure.mjs" in out
    for command in (("check",), ("brief",)):
        rc, out, err = _glue_rc(tmp_path, *command)
        assert rc == 2 and "Traceback" not in err
    rc, out, err = _glue_rc(tmp_path, "check", "1-1")
    assert rc == 1 and "glue tasks" in out and "Traceback" not in err     # no plan yet


def test_step_4_sends_every_worker_task_in_one_reply(skill_text, glue):
    body = _body(skill_text)
    step3, step4 = body.split("## 3.", 1)[1].split("## 4.", 1)[0], body.split("## 4.", 1)[1].split("## 5.", 1)[0]
    # (a) every task in ONE reply; the extra calls wait their turn (the settings redo of 2026-10-01), none is refused
    assert "`tasks`" in step4 and "ONE reply" in step4 and "only `task` calls" in step4 and "EVERY task" in step4
    assert "queues the rest" in step4 and re.search(r"not a few at a time", step4, re.I)
    # (b) a worker fits its lane: few files, its own batch file, three lines back -- and it can write: `coder`
    assert "`coder`" in step4 and "`explorer`" not in step4             # the explorer has no write_file and no run_bash
    assert "three lines" in step4 and "never the analysis" in step4 and "its own" in step4
    assert re.search(r"32[K,]", step4)
    # (c) a failed or incomplete task goes out once more, then the lead does it
    assert "once more" in step4 and "yourself" in step4 and "`brief <id>`" in step4
    # the big-repo path's key-file summaries go to workers the same way (its lines are the glue's `draft` output)
    assert "`draft`" in step3 and "`explorer`" in step3 and "ONE reply" in step3
    # the node and edge rules have one home, the brief: the lead does not carry them for workers that never see them
    assert "**Nodes.**" not in body and "tested_by" not in body and "tested_by 0.5" in glue.RULES
    from dream.core.subagents import local_subagents
    tools = local_subagents()
    assert {"read_file", "write_file", "run_bash"} <= set(tools["coder"].tool_names)
    assert "write_file" not in tools["explorer"].tool_names


def test_the_draft_sends_each_key_file_to_a_worker(glue):
    text = glue.KEY_NEXT % {"todo": 7, "after": "then the merge (step 5)"}
    assert "ONE reply" in text and "only `task` calls" in text and 'subagent_type "explorer"' in text
    assert "start_line=1, line_count=200" in text and "never offset or limit" in text
    assert "ONLY 1-2 specific sentences" in text and "summaries.json" in text and "rerun glue draft" in text
    assert "once more" in text and "yourself" in text and "queues the rest" in text
    assert not re.search(r"^\s+\S+ \(", text, re.M)                      # no line reads as a key file's own line


# ---- (4) the dry run: every deterministic step, the SKILL.md's own commands, the real run_bash -------------------------

FIXTURE = {
    "README.md": "# Rocketlog\n\nA tiny command-line tool that records rocket launches in a JSON file.\n",
    "pyproject.toml": '[project]\nname = "rocketlog"\nversion = "0.1.0"\ndescription = "Records rocket launches from the command line."\ndependencies = ["click"]\n',
    "rocketlog/__init__.py": "from .launch import record\n\n__all__ = ['record']\n",
    "rocketlog/launch.py": (
        "\"\"\"Launch records.\"\"\"\nfrom . import store\n\n\nclass Launch:\n    \"\"\"One recorded launch.\"\"\"\n\n"
        "    def __init__(self, name, stage):\n        self.name = name\n        self.stage = stage\n\n"
        "    def as_dict(self):\n        return {'name': self.name, 'stage': self.stage}\n\n"
        "    def label(self):\n        return f'{self.name} (stage {self.stage})'\n\n\n"
        "def record(name, stage):\n    \"\"\"Record a launch and return it.\"\"\"\n    if not name:\n        raise ValueError('name required')\n"
        "    if stage < 1:\n        raise ValueError('stage must be positive')\n    launch = Launch(name, stage)\n"
        "    entries = store.load_all()\n    entries.append(launch.as_dict())\n    store.save(entries)\n    return launch\n"),
    "rocketlog/store.py": (
        "\"\"\"JSON storage.\"\"\"\nimport json\nimport os\n\nPATH = os.environ.get('ROCKETLOG_FILE', 'launches.json')\n\n\n"
        "def load_all():\n    \"\"\"Read every entry, or an empty list.\"\"\"\n    if not os.path.exists(PATH):\n        return []\n"
        "    with open(PATH, encoding='utf-8') as f:\n        data = json.load(f)\n    if not isinstance(data, list):\n"
        "        raise ValueError('corrupt store')\n    return data\n\n\n"
        "def save(entries):\n    \"\"\"Write the entries atomically.\"\"\"\n    tmp = PATH + '.tmp'\n    with open(tmp, 'w', encoding='utf-8') as f:\n"
        "        json.dump(entries, f, indent=1)\n    os.replace(tmp, PATH)\n    return len(entries)\n"),
    "rocketlog/cli.py": (
        "\"\"\"Command line.\"\"\"\nimport sys\n\nfrom .launch import record\n\n\ndef main(argv=None):\n"
        "    \"\"\"rocketlog NAME STAGE\"\"\"\n    argv = sys.argv[1:] if argv is None else argv\n    if len(argv) != 2:\n"
        "        print('usage: rocketlog NAME STAGE')\n        return 2\n    launch = record(argv[0], int(argv[1]))\n"
        "    print('recorded', launch.label())\n    return 0\n\n\nif __name__ == '__main__':\n    raise SystemExit(main())\n"),
    "tests/test_store.py": (
        "import os\n\nfrom rocketlog import store\n\n\ndef test_roundtrip(tmp_path, monkeypatch):\n"
        "    monkeypatch.setattr(store, 'PATH', str(tmp_path / 'l.json'))\n    assert store.load_all() == []\n"
        "    assert store.save([{'name': 'a', 'stage': 1}]) == 1\n    assert store.load_all()[0]['name'] == 'a'\n"),
    "Dockerfile": "FROM python:3.12-slim\nWORKDIR /app\nCOPY rocketlog ./rocketlog\nCMD [\"python\", \"-m\", \"rocketlog.cli\"]\n",
    # noise the map must not contain: Dream's own state, a vendored tree, an image
    ".dream/state.json": "{}\n",
    ".remember/notes.txt": "private notes\n",
    "node_modules/pkg/index.js": "module.exports = 1;\n",
    "docs/logo.png": "\x89PNG\r\n",
}
SOURCE_FILES = 8      # everything above the noise line


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _sh(command: str, workspace: Path):
    """One run_bash command exactly as the model's run_bash runs it (DREAM-105): the policy decides first in auto mode
    (a refusal raises), then the host runs it (no sandbox since 2026-10-11) with the skills' script folders in its
    environment."""
    from dream.core.execution import ExecutionContext, ExecutionScope, execute_bash
    result, contained = asyncio.run(execute_bash(command, ExecutionContext(ExecutionScope(Path(workspace)), mode="auto")))
    assert not contained
    return result


def _ua(skill, script, args, cwd):
    """A plugin script run the way the SKILL.md prescribes: `node|python3 "$UA_SKILLS/<skill>/<script>" ...` through
    run_bash from the workspace."""
    runner = "python3" if script.endswith(".py") else "node"
    result = _sh(f'{runner} "$UA_SKILLS/{skill}/{script}" ' + " ".join(shlex.quote(str(a)) for a in args), Path(cwd))
    text = result.output.decode("utf-8", "replace")
    assert result.returncode == 0, text
    return text


def _glue(root: Path, *args: str) -> str:
    run = subprocess.run([sys.executable, str(root / ".ua" / "tmp" / "ua_glue.py"), *args], cwd=root,
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, f"glue {args}: exit {run.returncode}\n{run.stdout}\n{run.stderr}"
    return run.stdout


# the model's own edges in the fixture: a node of the task (first) to any scanned file's node
EXTRA_EDGES = (("service:Dockerfile", "file:rocketlog/cli.py", "deploys", 0.7),
               ("document:README.md", "file:rocketlog/__init__.py", "documents", 0.5),
               ("config:pyproject.toml", "file:rocketlog/__init__.py", "configures", 0.6),
               ("file:rocketlog/store.py", "file:tests/test_store.py", "tested_by", 0.5))


@pytest.fixture(scope="module")
def mapped(tmp_path_factory, skill_text):
    """The whole pipeline on the fixture, the model's steps hand-written. Skipped without the plugin."""
    if not (UA_SKILLS / "understand" / "scan-project.mjs").is_file() or not (
            UA_PLUGIN / "packages" / "core" / "dist" / "index.js").is_file() or shutil.which("node") is None:
        pytest.skip("needs the Understand-Anything clone with its built core and node")
    root = tmp_path_factory.mktemp("rocketlog")
    for rel, text in FIXTURE.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    head = _git(root, "rev-parse", "HEAD")
    log: list[str] = []

    # step 1 -- the glue copied in, then status (DREAM-106): no map yet, so it writes the default ignore file and prints
    # the scan command; the project facts for a full map
    (root / ".ua" / "tmp").mkdir(parents=True)
    shutil.copy(GLUE, root / ".ua" / "tmp" / "ua_glue.py")
    status = _sh("python3 .ua/tmp/ua_glue.py status", root)
    status_text = status.output.decode("utf-8", "replace")
    assert status.returncode == 0 and "no map" in status_text, status_text
    assert (root / ".ua" / ".understandignore").is_file()
    (root / ".ua" / "tmp" / "project.json").write_text(json.dumps(
        {"name": "rocketlog", "description": "Records rocket launches from the command line.", "frameworks": ["Docker"]}))
    # step 2 -- the scan exactly as status printed it, import map, scan-result
    scan_command = next(line.strip() for line in status_text.splitlines()
                        if line.strip().startswith('node "$UA_SKILLS/understand/scan-project.mjs"'))
    assert '"$PWD/.ua/tmp/scan.json" --exclude-analysis-data --exclude ".dream/**,.remember/**"' in scan_command
    scanned = _sh(scan_command, root)
    assert scanned.returncode == 0, scanned.output
    log.append(scanned.output.decode("utf-8", "replace"))
    log.append(_glue(root, "imports-input"))
    log.append(_ua("understand", "extract-import-map.mjs", [root / ".ua/tmp/imports-in.json", root / ".ua/tmp/imports-out.json"], root))
    scan_line = _glue(root, "scan-result")
    log.append(scan_line)
    # step 3 -- batches
    log.append(_ua("understand", "compute-batches.mjs", [root], root))
    batch_listing = _glue(root, "batch-inputs")
    log.append(batch_listing)
    batches = json.loads((root / ".ua/intermediate/batches.json").read_text())
    # step 4 -- the parser per batch, then the worker tasks. Each worker is simulated (_part writes what its brief
    # lists); its two run_bash calls, the brief and the check, run through the real run_bash as a `coder` worker's do.
    structure_text = ""
    for b in batches["batches"]:
        i = b["batchIndex"]
        log.append(_ua("understand", "extract-structure.mjs", [root / f".ua/tmp/extract-in-{i}.json", root / f".ua/tmp/extract-out-{i}.json"], root))
        structure_text += _glue(root, "structure", str(i))
    tasks_text = _glue(root, "tasks")
    briefs, checks = {}, {}
    for task in _plan(root):
        for command, kept in (("brief", briefs), ("check", checks)):
            if command == "check":
                _write_part(root, task, EXTRA_EDGES)
            ran = _sh(f"python3 .ua/tmp/ua_glue.py {command} {task['id']}", root)
            kept[task["id"]] = ran.output.decode("utf-8", "replace")
            assert ran.returncode == 0, kept[task["id"]]
    tasks_done = _glue(root, "tasks")
    # step 5 -- merge
    merge = _ua("understand", "merge-batch-graphs.py", [root], root)
    log.append(merge)
    # step 6 -- layers and tour from the printed node ids
    node_listing = _glue(root, "nodes")
    ids = [line.split(" -- ")[0] for line in node_listing.splitlines() if " -- " in line]
    core = [i for i in ids if i.startswith("file:rocketlog/")]
    tests_ = [i for i in ids if i.startswith("file:tests/")]
    other = [i for i in ids if i not in core and i not in tests_]
    (root / ".ua/intermediate/layers.json").write_text(json.dumps([
        {"id": "layer:core", "name": "Core", "description": "The rocketlog package.", "nodeIds": core},
        {"id": "layer:tests", "name": "Tests", "description": "pytest suite.", "nodeIds": tests_},
        {"id": "layer:project", "name": "Project", "description": "Docs, config and container.", "nodeIds": other}]))
    (root / ".ua/intermediate/tour.json").write_text(json.dumps([
        {"order": 1, "title": "Overview", "description": "Start at the README.", "nodeIds": ["document:README.md"]},
        {"order": 2, "title": "Entry point", "description": "The CLI calls record().", "nodeIds": ["file:rocketlog/cli.py", "file:rocketlog/launch.py"]},
        {"order": 3, "title": "Storage", "description": "JSON persistence.", "nodeIds": ["file:rocketlog/store.py"]}]))
    # step 7 -- assemble, fingerprints
    assemble = _glue(root, "assemble")
    log.append(assemble)
    log.append(_ua("understand", "build-fingerprints.mjs", [root / ".ua/intermediate/fingerprint-input.json"], root))
    # step 8 -- the domain graph's nodes and edges (the model's); step 9 -- finish
    (root / ".ua/intermediate/domain.json").write_text(json.dumps({
        "nodes": [{"id": "domain:launch-records", "type": "domain", "name": "Launch records", "summary": "Recording launches.",
                   "tags": ["records"], "complexity": "simple", "domainMeta": {"entities": ["Launch"]}},
                  {"id": "flow:record-launch", "type": "flow", "name": "Record a launch", "summary": "CLI to JSON file.",
                   "tags": ["cli"], "complexity": "simple", "domainMeta": {"entryPoint": "rocketlog NAME STAGE", "entryType": "cli"}},
                  {"id": "step:record-launch:validate", "type": "step", "name": "Validate input", "summary": "Name and stage checks.",
                   "tags": ["validation"], "complexity": "simple", "filePath": "rocketlog/launch.py"},
                  {"id": "step:record-launch:save", "type": "step", "name": "Save entries", "summary": "Atomic JSON write.",
                   "tags": ["storage"], "complexity": "simple", "filePath": "rocketlog/store.py"}],
        "edges": [{"source": "domain:launch-records", "target": "flow:record-launch", "type": "contains_flow", "direction": "forward", "weight": 1.0},
                  {"source": "flow:record-launch", "target": "step:record-launch:validate", "type": "flow_step", "direction": "forward", "weight": 0.1},
                  {"source": "flow:record-launch", "target": "step:record-launch:save", "type": "flow_step", "direction": "forward", "weight": 0.2}]}))
    finish = _glue(root, "finish")
    log.append(finish)
    return {"root": root, "head": head, "log": log, "scan_line": scan_line, "batch_listing": batch_listing,
            "structure_text": structure_text, "node_listing": node_listing, "merge": merge, "assemble": assemble,
            "finish": finish, "batches": batches, "tasks_text": tasks_text, "tasks_done": tasks_done, "briefs": briefs,
            "checks": checks}


@needs_plugin
def test_scan_honours_the_ignore_file_and_the_users_exclusions(mapped):
    root = mapped["root"]
    scan = json.loads((root / ".ua/tmp/scan.json").read_text())
    paths = {f["path"] for f in scan["files"]}
    assert paths == {"README.md", "pyproject.toml", "rocketlog/__init__.py", "rocketlog/launch.py", "rocketlog/store.py",
                     "rocketlog/cli.py", "tests/test_store.py", "Dockerfile"}
    assert scan["totalFiles"] == SOURCE_FILES and scan["stats"]["byCategory"] == {"code": 5, "docs": 1, "config": 1, "infra": 1}
    assert scan["filteredByIgnore"] == 2                                 # .dream/ and .remember/: the user's rules, not the defaults
    assert "8 files" in mapped["scan_line"] and "excluded by the ignore rules" in mapped["scan_line"]
    result = json.loads((root / ".ua/intermediate/scan-result.json").read_text())
    assert result["name"] == "rocketlog" and result["languages"] == sorted(result["languages"])
    assert result["importMap"]["rocketlog/launch.py"] == ["rocketlog/store.py"]
    assert result["importMap"]["rocketlog/cli.py"] == ["rocketlog/launch.py"]
    assert set(result["importMap"]["tests/test_store.py"]) == {"rocketlog/__init__.py", "rocketlog/store.py"}   # `from rocketlog import store`
    assert result["files"] == scan["files"] and "scriptCompleted" not in result


@needs_plugin
def test_batches_and_structure_give_the_model_what_it_writes_from(mapped):
    assert mapped["batches"]["totalFiles"] == SOURCE_FILES
    listing = mapped["batch_listing"]
    assert "rocketlog/launch.py (python, " in listing and "imports: rocketlog/store.py" in listing
    assert "Dockerfile (dockerfile, " in listing and "infra" in listing
    text = mapped["structure_text"]
    assert re.search(r"function record lines \d+-\d+ params name, stage", text)
    assert re.search(r"class Launch lines \d+-\d+ methods", text) and "as_dict" in text
    assert "UNREADABLE" not in text


@needs_plugin
def test_the_workers_tasks_cover_the_fixture_and_pass_their_checks(mapped):
    """Step 4 on the real parser's output: one task per small batch, each brief names its files' reads and import
    targets, each part passes its check, and the second `tasks` sends the lead on to the merge."""
    plan = _plan(mapped["root"])
    assert [task["id"] for task in plan] == ["1-1", "2-1", "3-1"]
    assert [Path(task["file"]).name for task in plan] == ["batch-1.json", "batch-2.json", "batch-3.json"]
    assert "ALL 3" in mapped["tasks_text"] and "all 3 tasks done" in mapped["tasks_done"]
    briefs = "".join(mapped["briefs"].values())
    for path in ("README.md", "pyproject.toml", "rocketlog/launch.py", "tests/test_store.py", "Dockerfile"):
        assert f"- {path} (" in briefs, path
    assert "imports: file:rocketlog/store.py" in briefs and re.search(r"function record \[\d+, \d+\]", briefs)
    assert "service:Dockerfile" in briefs and "document:README.md" in briefs and "config:pyproject.toml" in briefs
    assert all(text.strip().splitlines()[-1].startswith("ok: ") for text in mapped["checks"].values())


@needs_plugin
def test_the_merge_keeps_every_node_and_import_edge(mapped):
    root = mapped["root"]
    assembled = json.loads((root / ".ua/intermediate/assembled-graph.json").read_text())
    ids = {n["id"] for n in assembled["nodes"]}
    assert {"file:rocketlog/launch.py", "function:rocketlog/launch.py:record", "class:rocketlog/launch.py:Launch",
            "function:rocketlog/store.py:save", "document:README.md", "config:pyproject.toml", "service:Dockerfile"} <= ids
    imports = [(e["source"], e["target"]) for e in assembled["edges"] if e["type"] == "imports"]
    assert ("file:rocketlog/launch.py", "file:rocketlog/store.py") in imports and ("file:rocketlog/cli.py", "file:rocketlog/launch.py") in imports
    assert "Could not fix" not in mapped["merge"]
    assert "file:rocketlog/launch.py -- " in mapped["node_listing"]


@needs_plugin
def test_the_four_files_are_present_valid_and_what_the_routes_serve(mapped, glue):
    from dream.gui import understand_routes
    root, head = mapped["root"], mapped["head"]
    for name in ("knowledge-graph.json", "domain-graph.json", "diff-overlay.json", "meta.json", "fingerprints.json"):
        assert (root / ".ua" / name).is_file(), name
    graph = json.loads((root / ".ua/knowledge-graph.json").read_text())
    assert glue.check_graph(graph, glue.NODE_TYPES, glue.EDGE_TYPES, layered=True) == []
    assert graph["version"] == "1.0.0" and graph["project"]["gitCommitHash"] == head and graph["project"]["name"] == "rocketlog"
    assert graph["project"]["frameworks"] == ["Docker"] and "python" in graph["project"]["languages"]
    assert [L["name"] for L in graph["layers"]] == ["Core", "Tests", "Project"] and len(graph["tour"]) == 3
    file_nodes = [n for n in graph["nodes"] if n["type"] in glue.FILE_TYPES]
    assert len(file_nodes) == SOURCE_FILES
    assigned = [i for L in graph["layers"] for i in L["nodeIds"]]
    assert sorted(assigned) == sorted(n["id"] for n in file_nodes)
    assert all(not Path(n["filePath"]).is_absolute() for n in graph["nodes"])
    meta = json.loads((root / ".ua/meta.json").read_text())
    assert meta == {"lastAnalyzedAt": graph["project"]["analyzedAt"], "gitCommitHash": head, "version": "1.0.0", "analyzedFiles": SOURCE_FILES}
    overlay = json.loads((root / ".ua/diff-overlay.json").read_text())
    assert overlay["changedNodeIds"] == [] and overlay["affectedNodeIds"] == [] and overlay["changedFiles"] == []
    domain = json.loads((root / ".ua/domain-graph.json").read_text())
    assert domain["project"] == graph["project"] and domain["layers"] == [] and domain["tour"] == []
    assert glue.check_graph(domain, glue.DOMAIN_NODES, glue.DOMAIN_EDGES, layered=False) == []
    assert "problems: 0" in mapped["assemble"] and "problems: 0" in mapped["finish"]
    assert "Fingerprints baseline: 8 files" in "".join(mapped["log"])
    # the routes: counts, relativised paths, freshness against the same commit
    status = understand_routes.status(root)["graph"]
    assert status["nodes"] == len(graph["nodes"]) and status["name"] == "rocketlog"
    served = understand_routes.load_graph(root)
    assert {n["filePath"] for n in served["nodes"]} <= {f for f in FIXTURE if not f.startswith((".dream", ".remember", "node_modules", "docs/"))}
    assert understand_routes.freshness(root, graph)["status"] == "fresh"


@needs_plugin
def test_the_finish_report_has_the_numbers_the_model_repeats(mapped):
    text = mapped["finish"]
    assert re.search(r"\d+ nodes \(", text) and re.search(r"\d+ edges \(", text)
    assert "layers: Core, Tests, Project" in text and "3 tour steps" in text
    assert "file " in text and "function " in text and "imports " in text     # counts by type


@needs_plugin
def test_a_broken_layer_or_batch_is_reported_by_the_glue(mapped, tmp_path):
    """A model that leaves a file node out of every layer, or invents a type, gets it named -- and a non-zero exit."""
    root = mapped["root"]
    layers_path = root / ".ua/intermediate/layers.json"
    original = layers_path.read_text()
    try:
        layers = json.loads(original)
        layers[0]["nodeIds"] = layers[0]["nodeIds"][1:]
        layers_path.write_text(json.dumps(layers))
        run = subprocess.run([sys.executable, str(root / ".ua/tmp/ua_glue.py"), "assemble"], cwd=root, capture_output=True, text=True)
        assert run.returncode != 0 and "is in 0 layers" in run.stdout, run.stdout
    finally:
        layers_path.write_text(original)
        assert "problems: 0" in _glue(root, "assemble")


# ---- gate fixes (2026-09-23 evening): an incomplete map is never "done"; the two tool calls run without a prompt ------

def _glue_rc(root: Path, *args: str):
    run = subprocess.run([sys.executable, str(root / ".ua" / "tmp" / "ua_glue.py"), *args], cwd=root,
                         capture_output=True, text=True, timeout=60)
    return run.returncode, run.stdout, run.stderr


def _copy_of(mapped, tmp_path: Path) -> Path:
    work = tmp_path / "work"
    shutil.copytree(mapped["root"], work, symlinks=True)
    return work


@needs_plugin
def test_a_truncated_batch_file_is_named_and_the_map_is_not_done(mapped, tmp_path, glue):
    """The evaluator's C6: a batch file written in two halves, first half on disk. The merge skips it with a stderr
    warning and exit 0; `nodes`, `assemble` and `finish` must say which scanned files have no node, name the broken
    file, and never print `problems: 0` -- and meta.json must count the files actually in the graph."""
    work = _copy_of(mapped, tmp_path)
    path = work / ".ua/intermediate/batch-1.json"
    whole = path.read_text()
    lost = {n["filePath"] for n in json.loads(whole)["nodes"] if n["type"] in glue.FILE_TYPES}
    path.write_text(whole[: len(whole) // 2])
    _ua("understand", "merge-batch-graphs.py", [work], work)                # upstream: warning only, exit 0
    rc, out, err = _glue_rc(work, "assemble")
    assert rc != 0 and "Traceback" not in err, (out, err)
    assert re.search(r"\d+ scanned files? ha(?:ve|s) no node", out) and all(p in out for p in sorted(lost)[:2]), out
    assert "batch-1.json" in out and "not valid JSON" in out and "problems: 0" not in out
    meta = json.loads((work / ".ua/meta.json").read_text())
    kg = json.loads((work / ".ua/knowledge-graph.json").read_text())
    present = {n["filePath"] for n in kg["nodes"] if n["type"] in glue.FILE_TYPES}
    assert meta["analyzedFiles"] == len(present) < SOURCE_FILES
    rc, out, _ = _glue_rc(work, "finish")
    assert rc != 0 and "have no node" in out and "problems: 0" not in out
    rc, out, _ = _glue_rc(work, "nodes")
    assert "have no node" in out or "missing" in out.lower()             # the listing warns before layers are written
    # the whole file back, merge again -> clean
    path.write_text(whole)
    _ua("understand", "merge-batch-graphs.py", [work], work)
    assert "problems: 0" in _glue(work, "assemble") and "problems: 0" in _glue(work, "finish")


@needs_plugin
def test_stale_merges_and_misnamed_batch_files_are_named(mapped, tmp_path):
    work = _copy_of(mapped, tmp_path)
    inter = work / ".ua/intermediate"
    # a batch file rewritten after the merge: the assembled graph is stale
    b = inter / "batch-1.json"
    b.write_text(b.read_text())
    os.utime(b, None)
    os.utime(inter / "assembled-graph.json", (1, 1))
    rc, out, _ = _glue_rc(work, "assemble")
    assert rc != 0 and re.search(r"(?:changed|written) after the (?:last )?merge", out) and "step 5" in out
    # a name the merge silently drops
    (inter / "batch_9.json").write_text('{"nodes": [], "edges": []}')
    rc, out, _ = _glue_rc(work, "assemble")
    assert rc != 0 and "batch_9.json" in out and "batch-<i>.json" in out


@needs_plugin
def test_a_legacy_data_folder_and_a_missing_batch_index_give_named_problems(mapped, tmp_path):
    work = _copy_of(mapped, tmp_path)
    rc, out, err = _glue_rc(work, "structure", "9")
    assert rc != 0 and "Traceback" not in err and "extract-out-9.json" in out and "1, 2, 3" in out, (out, err)
    rc, out, err = _glue_rc(work, "structure")
    assert rc == 2 and "Traceback" not in err
    (work / ".understand-anything").mkdir()                              # the plugin's scripts now use this folder
    for command in ("imports-input", "assemble", "finish"):
        rc, out, err = _glue_rc(work, command)
        assert rc != 0 and "Traceback" not in err, (command, out, err)
        assert ".understand-anything" in out and "legacy" in out.lower(), (command, out)


# ---- policy: the skill's two host-side tool calls -------------------------------------------------------------------

def test_copying_a_bundled_skill_file_into_the_workspace_needs_no_prompt(tmp_path):
    from dream.core import policy
    ws = tmp_path / "ws"
    ws.mkdir()
    call = {"files": [{"src": str(GLUE), "dest": ".ua/tmp/ua_glue.py"}]}
    assert policy.decide("copy_files", call, "accept-edits", ws) == ("allow", "bundled skill file into the workspace")
    assert policy.decide("copy_files", call, "auto", ws)[0] == "allow"
    assert policy.decide("copy_files", call, "ask", ws)[0] == "ask"      # ask mode still asks, as for any write
    assert policy.decide("copy_files", call, "plan", ws)[0] == "deny"
    # refusals: a source that is not a bundled skill file, a destination outside, a move, a symlink out of a skill root
    for bad in ({"files": [{"src": "/etc/passwd", "dest": ".ua/tmp/x"}]},
                {"files": [{"src": str(GLUE), "dest": str(tmp_path / "elsewhere.py")}]},
                {"files": [{"src": str(GLUE), "dest": ".ua/tmp/ua_glue.py", "move": True}]},
                {"files": [{"src": str(GLUE), "dest": ".ua/tmp/ua_glue.py"}, {"src": "/etc/hostname", "dest": "h"}]},
                {"files": [{"src": str(SKILL), "dest": ".ua/tmp/whole-skill"}]}):
        decision, reason = policy.decide("copy_files", bad, "auto", ws)
        assert decision == "ask" and reason.startswith("outside workspace"), (bad, decision, reason)


def test_a_symlink_inside_a_skill_root_does_not_open_the_allowance(tmp_path, monkeypatch):
    import dream.config as config_module
    from dream.core import policy
    root = tmp_path / "skills" / "fake"
    root.mkdir(parents=True)
    (root / "escape.py").symlink_to("/etc/hostname")
    monkeypatch.setattr(config_module, "bundled_skill_dirs", lambda: [root])
    ws = tmp_path / "ws"
    ws.mkdir()
    decision, reason = policy.decide("copy_files", {"files": [{"src": str(root / "escape.py"), "dest": "e.py"}]}, "auto", ws)
    assert decision == "ask" and reason.startswith("outside workspace")


def test_the_plugin_scripts_follow_their_inputs_outside_the_workspace_without_a_sandbox(tmp_path):
    """DREAM-105/DREAM-102 round 6, after the owner's 2026-10-11 decision (no shell sandbox): build-fingerprints,
    extract-structure and extract-import-map take their project root from an INPUT file the model writes, and every
    script writes through any symlink the workspace holds. Until 2026-10-11 run_bash's sandbox kept both inside the
    workspace; run_bash now runs on the host with the owner's access, so an input naming another folder is read and
    written there. Pinned on a stand-in folder under tmp_path, so a returning boundary shows up here. The policy lets
    all three commands run, and the same scripts inside the workspace still work."""
    if not (UA_SKILLS / "understand" / "build-fingerprints.mjs").is_file() or not (
            UA_PLUGIN / "packages" / "core" / "dist" / "index.js").is_file() or not shutil.which("node"):
        pytest.skip("needs the Understand-Anything clone with its built core and node")
    ws, outside = tmp_path / "ws", tmp_path / "outside"
    (ws / ".ua" / "tmp").mkdir(parents=True)
    (ws / "sub").mkdir()
    (outside / "src").mkdir(parents=True)
    (outside / "src" / "mod.py").write_text("def outside_function_dp_7731():\n    return 1\n\n\nclass OutsideClassDP:\n    pass\n")
    (outside / "README.md").write_text("# DP-7731\n")
    (ws / "sub" / ".ua").symlink_to(outside / "ua-target", target_is_directory=True)  # a symlink out of the workspace
    (outside / "ua-target").mkdir()
    fp_in, st_in, st_out = ws / ".ua/tmp/evil-fp.json", ws / ".ua/tmp/evil-in.json", ws / ".ua/tmp/evil-out.json"
    fp_in.write_text(json.dumps({"projectRoot": str(outside), "filePaths": ["src/mod.py"], "gitCommitHash": "x"}))
    st_in.write_text(json.dumps({"projectRoot": str(outside), "batchImportData": {}, "batchFiles": [
        {"path": "src/mod.py", "language": "python", "sizeLines": 6, "fileCategory": "code"},
        {"path": "README.md", "language": "markdown", "sizeLines": 1, "fileCategory": "docs"}]}))
    before = sorted(str(p.relative_to(outside)) for p in outside.rglob("*"))
    for command in (f'node "$UA_SKILLS/understand/build-fingerprints.mjs" {shlex.quote(str(fp_in))}',
                    f'node "$UA_SKILLS/understand/extract-structure.mjs" {shlex.quote(str(st_in))} {shlex.quote(str(st_out))}',
                    f'node "$UA_SKILLS/understand/generate-ignore.mjs" {shlex.quote(str(ws / "sub"))}'):
        _sh(command, ws)                                            # the exit may be an error; what it touched counts
    after = sorted(str(p.relative_to(outside)) for p in outside.rglob("*"))
    assert set(after) - set(before) == {".ua", ".ua/fingerprints.json", "ua-target/.understandignore"}   # written there
    text = st_out.read_text() if st_out.is_file() else ""
    assert "outside_function_dp_7731" in text and "DP-7731" in text                                    # and read
    # the same scripts inside the workspace still work
    (ws / "src").mkdir()
    (ws / "src" / "mod.py").write_text("def inside_function():\n    return 1\n")
    st_in.write_text(json.dumps({"projectRoot": str(ws), "batchImportData": {}, "batchFiles": [
        {"path": "src/mod.py", "language": "python", "sizeLines": 2, "fileCategory": "code"}]}))
    _ua("understand", "extract-structure.mjs", [st_in, st_out], ws)
    assert "inside_function" in st_out.read_text()


def test_the_plugin_scripts_run_with_run_bash_like_any_command(tmp_path):
    """DREAM-105: no runner tool and no approval step. The plugin's scripts run with run_bash, the way Claude Code runs
    them with Bash; run_bash finds the plugin's clone (and node), and skill_open says so."""
    from dream.core import skill_runtime
    from dream.tools import registry
    assert "ua_run" not in {t.name for t in registry._BASE_TOOLS}
    assert not (ROOT / "dream" / "tools" / "understand_tools.py").exists()
    assert not (ROOT / "plugins" / "understand-anything" / "tools" / "ua_run.py").exists()
    if not (UA_SKILLS / "understand" / "scan-project.mjs").is_file():
        pytest.skip("the Understand-Anything clone is not installed")
    assert skill_runtime.UA_ROOT.resolve() in skill_runtime.script_roots()
    assert skill_runtime.script_env()["UA_SKILLS"] == str(UA_SKILLS.resolve())
    found, _ = loader.discover([UA_SKILLS])
    plugin_understand = next(s for s in found if s.name == "understand")
    line = installed_skill_tools.directory_line(plugin_understand, tmp_path)
    assert "readable by run_bash" in line and "sandbox" not in line, line
