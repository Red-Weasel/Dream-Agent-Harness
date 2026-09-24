"""DREAM-102: the compact Dream-native `understand` skill -- a repository map for the Understand panel, step by step.

Owner, 2026-09-23: "Can you rewrite the skill shorter and get the same result? -- i dont see why youd need 30,000
characters to accomplish 'map this repo' ... just fix it so it at least behaves correctly." The plugin's 58,868-byte
SKILL.md is written for Claude Code subagents and a shell that can see the plugin folder; in Dream the scripts run
with run_bash, whose sandbox shows the plugin folder read-only as `$UA_SKILLS` (DREAM-105), and the live MiMo run spent
~90 tool calls re-deriving its environment.

What is pinned here: (1) the curated package and its size; (2) the four `.ua/` files in the dashboard's shapes, checked
by the skill's own glue; (3) the curated `understand` outranks the plugin's for the name and the map phrases while the
plugin's skill stays reachable; (4) a dry run of every deterministic step on a fixture repository through the real
run_bash sandbox -- the commands come from the SKILL.md itself -- with the model's part hand-written; (5) the suites.
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
       "skip locating it). Exclude Dream's own state folders: pass --exclude \".dream/**,.remember/**\" to the scan.")

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
    manifest = json.loads((SKILL / "manifest.json").read_text())
    assert manifest["format"] == "dream-skill/v1" and manifest["name"] == "understand" and manifest["portable"] is True
    assert {"run_bash", "read_file", "write_file", "skill_file"} <= set(manifest["capabilities"])
    wheel = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert "skills/understand" in wheel["only-include"]
    assert GLUE.is_file() and (SKILL / "references" / "schema.md").is_file()
    assert "references/schema.md" in skill_text and 'path="glue.py"' in skill_text
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
    # DREAM-105: every script runs with run_bash from $UA_SKILLS, the plugin folder the sandbox shows read-only
    for script in SCRIPTS:
        assert f'"$UA_SKILLS/understand/{script}"' in body, script
    # numbered steps a local model follows in order, and every glue command the dry run uses
    steps = re.findall(r"^## (\d+)\. ", body, re.M)
    assert steps == [str(i) for i in range(1, len(steps) + 1)] and len(steps) >= 8, steps
    for command in ("imports-input", "scan-result", "batch-inputs", "structure", "nodes", "assemble", "finish"):
        assert f"`{command}" in body or f"ua_glue.py {command}" in body, command
    # no confirmation pauses, no gate that stops, no shell trip into the plugin folder
    assert not re.search(r"wait for (?:the user'?s? )?confirmation|confirm to continue|ask (?:the user )?(?:whether|if) ", body, re.I)
    assert not re.search(r"\b(?:cd|ls|find)\b[^\n]*(?:\.understand-anything|SKILL_DIR|PLUGIN_ROOT)", body)
    assert re.search(r"over 100 files", body, re.I)
    # gate note 7: no hand-off to the plugin's file-analyzer (its prompt shells into the plugin dir and emits `step:` nodes)
    assert "file-analyzer" not in body and "analyses every batch itself" in body
    assert "parts" in body or "part-" in body                          # long batches are split, never edited in place
    assert "legacy" in body and ".understand-anything/" in body        # gate note 5: the legacy data folder is explained


def test_the_default_excludes_are_in_the_ignore_block(skill_text):
    block = re.search(r"\.understandignore[^\n]*\n\s*```\n(.*?)```", skill_text, re.S)
    assert block, "the SKILL.md must carry the .understandignore content in a fenced block"
    lines = {line.strip() for line in block.group(1).splitlines() if line.strip()}
    assert {".ua/", ".understand-anything/", ".dream/", ".remember/"} <= lines
    assert any(line.startswith("*.") for line in lines)                 # binary patterns the scan's defaults lack


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
    assert any("reachable as 'understand-anything:understand'" in w for w in installed_skill_tools.warnings())
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


# ---- (4) the dry run: every deterministic step, the SKILL.md's own commands, the real run_bash sandbox --------------

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
    (a refusal raises), then the real sandbox runs it with the skills' script folders mounted read-only."""
    from dream.core.execution import ExecutionContext, ExecutionScope, execute_bash
    result, contained = asyncio.run(execute_bash(command, ExecutionContext(ExecutionScope(Path(workspace)), mode="auto")))
    assert contained
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


def _node_type(entry: dict) -> str:
    """The skill's category -> type rule, as the model applies it."""
    cat, path = entry["fileCategory"], entry["path"]
    if cat == "config":
        return "config"
    if cat == "docs":
        return "document"
    if cat == "infra":
        return "pipeline" if "workflows" in path or path.endswith("Jenkinsfile") else "resource" if path.endswith(".tf") else "service"
    if cat == "data":
        return "schema" if path.endswith((".graphql", ".proto", ".prisma")) else "table"
    return "file"


def _simulate_batches(root: Path, batches: dict, structures: dict[int, dict]) -> None:
    """Write batch-<i>.json exactly as step 4 tells the model to: one node per file, function/class nodes with line
    ranges from the parser, `contains` for each, one `imports` edge per import target, and a few non-code edges."""
    for b in batches["batches"]:
        i = b["batchIndex"]
        results = {r["path"]: r for r in structures[i]["results"]}
        nodes, edges = [], []
        for f in b["files"]:
            ntype = _node_type(f)
            fid = f"{ntype}:{f['path']}"
            lines = f["sizeLines"]
            nodes.append({"id": fid, "type": ntype, "name": Path(f["path"]).name, "filePath": f["path"],
                          "summary": f"Fixture summary of {f['path']}: what it does and its role.",
                          "tags": ["fixture", "rocketlog", "test" if f["path"].startswith("tests/") else "source"],
                          "complexity": "simple" if lines < 50 else "moderate" if lines <= 200 else "complex"})
            r = results.get(f["path"], {})
            for fn in r.get("functions") or []:
                nid = f"function:{f['path']}:{fn['name']}"
                nodes.append({"id": nid, "type": "function", "name": fn["name"], "filePath": f["path"],
                              "lineRange": [fn["startLine"], fn["endLine"]], "summary": f"Fixture function {fn['name']}.",
                              "tags": ["fixture", "function", "python"], "complexity": "simple"})
                edges.append({"source": fid, "target": nid, "type": "contains", "direction": "forward", "weight": 1.0})
            for cls in r.get("classes") or []:
                nid = f"class:{f['path']}:{cls['name']}"
                nodes.append({"id": nid, "type": "class", "name": cls["name"], "filePath": f["path"],
                              "lineRange": [cls["startLine"], cls["endLine"]], "summary": f"Fixture class {cls['name']}.",
                              "tags": ["fixture", "class", "python"], "complexity": "simple"})
                edges.append({"source": fid, "target": nid, "type": "contains", "direction": "forward", "weight": 1.0})
            for target in b["batchImportData"].get(f["path"]) or []:
                edges.append({"source": fid, "target": f"file:{target}", "type": "imports", "direction": "forward", "weight": 0.7})
            extra = {"Dockerfile": ("deploys", "file:rocketlog/cli.py", 0.7), "README.md": ("documents", "file:rocketlog/__init__.py", 0.5),
                     "pyproject.toml": ("configures", "file:rocketlog/__init__.py", 0.6),
                     "rocketlog/store.py": ("tested_by", "file:tests/test_store.py", 0.5)}
            if f["path"] in extra:
                etype, target, weight = extra[f["path"]]
                edges.append({"source": fid, "target": target, "type": etype, "direction": "forward", "weight": weight})
        (root / ".ua" / "intermediate" / f"batch-{i}.json").write_text(json.dumps({"nodes": nodes, "edges": edges}, indent=1))


@pytest.fixture(scope="module")
def mapped(tmp_path_factory, skill_text):
    """The whole pipeline on the fixture, the model's steps hand-written. Skipped without the plugin."""
    if not (UA_SKILLS / "understand" / "scan-project.mjs").is_file() or not (
            UA_PLUGIN / "packages" / "core" / "dist" / "index.js").is_file() or shutil.which("node") is None or (
            shutil.which("bwrap") is None):
        pytest.skip("needs the Understand-Anything clone with its built core, node and bubblewrap")
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

    # step 1 -- prepare: the glue copied in, the ignore file from the SKILL.md's block, the project facts
    (root / ".ua" / "tmp").mkdir(parents=True)
    shutil.copy(GLUE, root / ".ua" / "tmp" / "ua_glue.py")
    ignore = re.search(r"\.understandignore[^\n]*\n\s*```\n(.*?)```", skill_text, re.S).group(1)
    (root / ".ua" / ".understandignore").write_text(ignore)
    (root / ".ua" / "tmp" / "project.json").write_text(json.dumps(
        {"name": "rocketlog", "description": "Records rocket launches from the command line.", "frameworks": ["Docker"]}))
    # step 2 -- scan, import map, scan-result
    log.append(_ua("understand", "scan-project.mjs",
                   [root, root / ".ua/tmp/scan.json", "--exclude-analysis-data", "--exclude", ".dream/**,.remember/**"], root))
    log.append(_glue(root, "imports-input"))
    log.append(_ua("understand", "extract-import-map.mjs", [root / ".ua/tmp/imports-in.json", root / ".ua/tmp/imports-out.json"], root))
    scan_line = _glue(root, "scan-result")
    log.append(scan_line)
    # step 3 -- batches
    log.append(_ua("understand", "compute-batches.mjs", [root], root))
    batch_listing = _glue(root, "batch-inputs")
    log.append(batch_listing)
    batches = json.loads((root / ".ua/intermediate/batches.json").read_text())
    # step 4 -- structure per batch, then the model's batch files (simulated)
    structures, structure_text = {}, ""
    for b in batches["batches"]:
        i = b["batchIndex"]
        log.append(_ua("understand", "extract-structure.mjs", [root / f".ua/tmp/extract-in-{i}.json", root / f".ua/tmp/extract-out-{i}.json"], root))
        structures[i] = json.loads((root / f".ua/tmp/extract-out-{i}.json").read_text())
        structure_text += _glue(root, "structure", str(i))
    _simulate_batches(root, batches, structures)
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
            "finish": finish, "batches": batches}


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


def test_the_plugin_scripts_are_confined_to_the_workspace_whatever_their_inputs_say(tmp_path):
    """DREAM-105, keeping DREAM-102's round-6 guarantee: build-fingerprints, extract-structure and extract-import-map take
    their project root from an INPUT file the model writes, and every script writes through any symlink the workspace
    holds. The scripts run with run_bash inside its sandbox -- the workspace read-write, the skills' script folders and
    node read-only, nothing else of the host -- so neither can reach outside. The policy lets all three commands run."""
    if not (UA_SKILLS / "understand" / "build-fingerprints.mjs").is_file() or not (
            UA_PLUGIN / "packages" / "core" / "dist" / "index.js").is_file() or not shutil.which("node") or not shutil.which("bwrap"):
        pytest.skip("needs the Understand-Anything clone with its built core, node and bubblewrap")
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
    assert sorted(str(p.relative_to(outside)) for p in outside.rglob("*")) == before   # nothing written outside
    assert not (outside / ".ua").exists() and not any((outside / "ua-target").iterdir())
    text = st_out.read_text() if st_out.is_file() else ""
    assert "outside_function_dp_7731" not in text and "OutsideClassDP" not in text and "DP-7731" not in text   # nor read
    # the same scripts inside the workspace still work: the boundary is the workspace, not the plugin
    (ws / "src").mkdir()
    (ws / "src" / "mod.py").write_text("def inside_function():\n    return 1\n")
    st_in.write_text(json.dumps({"projectRoot": str(ws), "batchImportData": {}, "batchFiles": [
        {"path": "src/mod.py", "language": "python", "sizeLines": 2, "fileCategory": "code"}]}))
    _ua("understand", "extract-structure.mjs", [st_in, st_out], ws)
    assert "inside_function" in st_out.read_text()


def test_the_plugin_scripts_run_with_run_bash_like_any_command(tmp_path):
    """DREAM-105: no runner tool and no approval step. The plugin's scripts run with run_bash, the way Claude Code runs
    them with Bash; the sandbox shows the plugin's clone (and node) read-only, and skill_open says so."""
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
    assert "readable in run_bash's sandbox" in line and "cannot see" not in line, line
