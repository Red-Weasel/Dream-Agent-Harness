"""DREAM-106: map once, update after -- the understand skill's status check, update mode, big-repo draft path and the
installed dependency trees it skips.

Owner, 2026-09-24, after the compact map of a 952-file workspace took hours (893 of the files were installed tools:
597 Playwright browser files, 296 pip-installed libraries): "this should only have to be done once. Is there a way that
once this is done for a repo, it doesnt have to be completed again?" -- and "yes" to making the big-repo shortcut part
of the skill.

Every plugin script and every glue command here runs through the real run_bash sandbox (DREAM-105), the way the model
runs them; the model's own writes (batch files, layers, tour, domain graph, summaries) are hand-written. Pinned:
(a) a finished map with nothing changed is reported current and nothing is scanned, batched or rewritten;
(b) one edit, one addition and one deletion redo only those files -- every other node byte-identical -- in a git
    repository with uncommitted edits and in a folder without git;
(c) installed Python packages and Playwright browsers are skipped by default, named with their file counts and reasons,
    and come back when the user names them;
(d) above 150 files the glue drafts every batch from the parser and names the key files the model summarises for real.
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
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "understand"
GLUE = SKILL / "glue.py"
UA_PLUGIN = Path(os.environ.get("UA_DIR", str(Path.home() / ".understand-anything" / "repo"))).expanduser() / "understand-anything-plugin"
UA_SKILLS = UA_PLUGIN / "skills"
FILE_TYPES = ("file", "config", "document", "service", "pipeline", "table", "schema", "resource", "endpoint")

pytestmark = pytest.mark.skipif(
    not (UA_SKILLS / "understand" / "scan-project.mjs").is_file()
    or not (UA_PLUGIN / "packages" / "core" / "dist" / "index.js").is_file()
    or shutil.which("node") is None or shutil.which("bwrap") is None,
    reason="needs the Understand-Anything clone with its built core, node and bubblewrap")


# ---- running things the way the model does ---------------------------------------------------------------------------

def _sh(command: str, ws: Path) -> tuple[int, str]:
    """One run_bash command: the policy decides in auto mode, then the real sandbox runs it (DREAM-105)."""
    from dream.core.execution import ExecutionContext, ExecutionScope, execute_bash
    result, contained = asyncio.run(execute_bash(command, ExecutionContext(ExecutionScope(ws), mode="auto"), timeout=300))
    assert contained
    return result.returncode, result.output.decode("utf-8", "replace")


def _run(command: str, ws: Path) -> str:
    rc, out = _sh(command, ws)
    assert rc == 0, f"{command}: exit {rc}\n{out}"
    return out


def glue_rc(ws: Path, *args: str) -> tuple[int, str]:
    return _sh("python3 .ua/tmp/ua_glue.py " + " ".join(shlex.quote(a) for a in args), ws)


def glue(ws: Path, *args: str) -> str:
    rc, out = glue_rc(ws, *args)
    assert rc == 0, f"glue {args}: exit {rc}\n{out}"
    assert "Traceback" not in out, out
    return out


def ua(ws: Path, script: str, *args) -> str:
    runner = "python3" if script.endswith(".py") else "node"
    return _run(f'{runner} "$UA_SKILLS/understand/{script}" ' + " ".join(shlex.quote(str(a)) for a in args), ws)


def printed(text: str, script: str) -> str:
    """The exact command line a glue command printed for a plugin script (the model copies it)."""
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith(f'node "$UA_SKILLS/understand/{script}"')]
    assert lines, f"no {script} command in:\n{text}"
    return lines[0]


def _git(ws: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=ws, check=True,
                          capture_output=True, text=True).stdout.strip()


def _write(ws: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def _node_type(category: str, path: str) -> str:
    return {"config": "config", "docs": "document"}.get(category) or (
        ("pipeline" if "workflows" in path else "resource" if path.endswith(".tf") else "service") if category == "infra"
        else ("schema" if path.endswith((".graphql", ".proto", ".prisma")) else "table") if category == "data" else "file")


def _path_of(node: dict) -> str:
    return node.get("filePath") or node["id"].split(":", 1)[1]


class Model:
    """The model's part of the pipeline, hand-written. `generation` marks every summary it writes, so a test can tell a
    node written in this run from one carried over from an earlier run."""

    def __init__(self, generation: int):
        self.generation = generation
        self.analysed: list[str] = []

    def project(self, ws: Path) -> None:                                   # step 1, full map only
        (ws / ".ua/tmp/project.json").write_text(json.dumps(
            {"name": ws.name, "description": "A fixture project.", "frameworks": []}))

    def batch(self, ws: Path, b: dict, structure: dict) -> None:           # step 4, one batch
        results = {r["path"]: r for r in structure.get("results") or []}
        nodes, edges = [], []
        for f in b["files"]:
            self.analysed.append(f["path"])
            fid = f"{_node_type(f['fileCategory'], f['path'])}:{f['path']}"
            nodes.append({"id": fid, "type": fid.split(":")[0], "name": Path(f["path"]).name, "filePath": f["path"],
                          "summary": f"What {f['path']} does (analysis {self.generation}).",
                          "tags": ["fixture", "source", f"gen-{self.generation}"], "complexity": "simple"})
            r = results.get(f["path"], {})
            for kind, items in (("function", r.get("functions") or []), ("class", r.get("classes") or [])):
                for s in items:
                    sid = f"{kind}:{f['path']}:{s['name']}"
                    nodes.append({"id": sid, "type": kind, "name": s["name"], "filePath": f["path"],
                                  "lineRange": [s["startLine"], s["endLine"]],
                                  "summary": f"{s['name']} (analysis {self.generation}).",
                                  "tags": ["fixture", kind, f"gen-{self.generation}"], "complexity": "simple"})
                    edges.append({"source": fid, "target": sid, "type": "contains", "direction": "forward", "weight": 1.0})
            for target in b["batchImportData"].get(f["path"]) or []:
                edges.append({"source": fid, "target": f"file:{target}", "type": "imports", "direction": "forward", "weight": 0.7})
        (ws / f".ua/intermediate/batch-{b['batchIndex']}.json").write_text(json.dumps({"nodes": nodes, "edges": edges}))

    def layers_and_tour(self, ws: Path, listing: str) -> None:             # step 6, from glue `nodes`
        ids = [line.split(" -- ")[0].strip() for line in listing.splitlines() if " -- " in line and not line.startswith(" ")]
        groups: dict[str, list[str]] = {}
        for i in ids:
            path = i.split(":", 1)[1]
            groups.setdefault(path.split("/")[0] if "/" in path else "(root)", []).append(i)
        (ws / ".ua/intermediate/layers.json").write_text(json.dumps([
            {"id": f"layer:{name.strip('()')}", "name": name.strip("()").title(), "description": f"The {name} files.",
             "nodeIds": members} for name, members in sorted(groups.items())]))
        tour = [{"order": k + 1, "title": f"Look at {i}", "description": "Read it.", "nodeIds": [i]} for k, i in enumerate(ids[:4])]
        (ws / ".ua/intermediate/tour.json").write_text(json.dumps(tour))

    def domain(self, ws: Path, step_files: list[str]) -> None:             # step 8
        steps = [{"id": f"step:work:s{k}", "type": "step", "name": f"Step {k}", "summary": f"Does part {k}.",
                  "tags": ["step"], "complexity": "simple", "filePath": path} for k, path in enumerate(step_files)]
        (ws / ".ua/intermediate/domain.json").write_text(json.dumps({
            "nodes": [{"id": "domain:work", "type": "domain", "name": "Work", "summary": "The work.", "tags": ["work"],
                       "complexity": "simple"},
                      {"id": "flow:work", "type": "flow", "name": "Do work", "summary": "Does the work.", "tags": ["flow"],
                       "complexity": "simple", "domainMeta": {"entryPoint": "cli", "entryType": "cli"}}, *steps],
            "edges": [{"source": "domain:work", "target": "flow:work", "type": "contains_flow", "direction": "forward", "weight": 1.0},
                      *[{"source": "flow:work", "target": s["id"], "type": "flow_step", "direction": "forward",
                         "weight": round(0.1 * (k + 1), 1)} for k, s in enumerate(steps)]]}))


def map_project(ws: Path, model: Model, *, status_args: tuple[str, ...] = (), domain_files: list[str] | None = None,
                summarise=None) -> dict:
    """Steps 1-9 as the SKILL.md and the glue's `next:` lines prescribe, from whatever state `.ua/` is in."""
    log: dict[str, str] = {}
    (ws / ".ua/tmp").mkdir(parents=True, exist_ok=True)
    shutil.copy(GLUE, ws / ".ua/tmp/ua_glue.py")                            # step 1: the glue, then status
    log["status"] = st = glue(ws, "status", *status_args)
    if "The map is current" in st:
        return log
    update = "update mode" in st
    if not update:
        model.project(ws)
    _run(printed(st, "scan-project.mjs"), ws)                                # step 2: the scan status printed
    glue(ws, "imports-input")
    ua(ws, "extract-import-map.mjs", ws / ".ua/tmp/imports-in.json", ws / ".ua/tmp/imports-out.json")
    log["scan-result"] = sr = glue(ws, "scan-result")
    if "nothing to analyse" not in sr:
        _run(printed(sr, "compute-batches.mjs") if update else f'node "$UA_SKILLS/understand/compute-batches.mjs" "$PWD"', ws)
        log["batch-inputs"] = bi = glue(ws, "batch-inputs")
        batches = json.loads((ws / ".ua/intermediate/batches.json").read_text())
        if "big-repo path" in bi:                                            # step 3's other branch
            _run(printed(bi, "extract-structure.mjs"), ws)
            log["draft"] = dr = glue(ws, "draft")
            if summarise:
                summarise(ws, dr)
                log["draft-2"] = glue(ws, "draft")
            model.analysed += [f["path"] for b in batches["batches"] for f in b["files"]]
        else:                                                                # step 4
            for b in batches["batches"]:
                i = b["batchIndex"]
                ua(ws, "extract-structure.mjs", ws / f".ua/tmp/extract-in-{i}.json", ws / f".ua/tmp/extract-out-{i}.json")
                glue(ws, "structure", str(i))
                model.batch(ws, b, json.loads((ws / f".ua/tmp/extract-out-{i}.json").read_text()))
    log["merge"] = ua(ws, "merge-batch-graphs.py", ws)                      # step 5
    if not update:                                                           # step 6
        log["nodes"] = glue(ws, "nodes")
        model.layers_and_tour(ws, log["nodes"])
    rc, log["assemble"] = glue_rc(ws, "assemble")                           # step 7
    assert rc == 0 and "problems: 0" in log["assemble"], log["assemble"]
    log["fingerprints"] = ua(ws, "build-fingerprints.mjs", ws / ".ua/intermediate/fingerprint-input.json")
    if not update:                                                           # step 8
        model.domain(ws, domain_files or [])
    log["finish-rc"], log["finish"] = glue_rc(ws, "finish")                 # step 9
    return log


# ---- the fixture project ---------------------------------------------------------------------------------------------

LEDGER = {
    "README.md": "# Ledger\n\nA small ledger that records entries and reports totals.\n",
    "pyproject.toml": '[project]\nname = "ledger"\nversion = "0.1.0"\ndescription = "Records ledger entries and reports totals."\n',
    "ledger/__init__.py": '"""The ledger package."""\nfrom .book import Book\n\n__all__ = ["Book"]\n',
    "ledger/book.py": (
        '"""An in-memory book of entries."""\nfrom . import store\n\n\nclass Book:\n    """Entries and their total."""\n\n'
        "    def __init__(self, path):\n        self.path = path\n        self.items = store.load(path)\n\n"
        "    def add(self, amount, note):\n        self.items.append({'amount': amount, 'note': note})\n"
        "        store.save(self.path, self.items)\n\n    def total(self):\n        return sum(i['amount'] for i in self.items)\n"),
    "ledger/store.py": (
        '"""JSON storage for the entries."""\nimport json\nimport os\n\n\n'
        "def load(path):\n    \"\"\"Every entry, or an empty list.\"\"\"\n    if not os.path.exists(path):\n        return []\n"
        "    with open(path, encoding='utf-8') as f:\n        data = json.load(f)\n    if not isinstance(data, list):\n"
        "        raise ValueError('corrupt store')\n    return data\n\n\n"
        "def save(path, items):\n    \"\"\"Write the entries atomically.\"\"\"\n    tmp = path + '.tmp'\n"
        "    with open(tmp, 'w', encoding='utf-8') as f:\n        json.dump(items, f)\n    os.replace(tmp, path)\n"
        "    return len(items)\n"),
    "ledger/cli.py": (
        '"""Command line: ledger add AMOUNT NOTE."""\nimport sys\n\nfrom .book import Book\n\n\n'
        "def main(argv=None):\n    \"\"\"Add one entry and print the total.\"\"\"\n    argv = sys.argv[1:] if argv is None else argv\n"
        "    if len(argv) != 3 or argv[0] != 'add':\n        print('usage: ledger add AMOUNT NOTE')\n        return 2\n"
        "    book = Book('ledger.json')\n    book.add(float(argv[1]), argv[2])\n    print(book.total())\n    return 0\n\n\n"
        "if __name__ == '__main__':\n    raise SystemExit(main())\n"),
    "tests/test_book.py": "from ledger.book import Book\n\n\ndef test_total(tmp_path):\n    assert Book(str(tmp_path / 'l.json')).total() == 0\n",
    "tests/test_cli.py": "from ledger import cli\n\n\ndef test_usage():\n    assert cli.main([]) == 2\n",
    "docs/guide.md": "# Guide\n\nRun `ledger add 5 lunch` to record an entry.\n",
    # Dream's own state: never in the map
    ".dream/state.json": "{}\n",
}
EDITED, ADDED, DELETED = "ledger/store.py", "ledger/report.py", "ledger/cli.py"
EDIT = LEDGER[EDITED].replace("    return len(items)\n", "    return len(items)\n\n\ndef count(path):\n    \"\"\"How many entries.\"\"\"\n"
                              "    return len(load(path))\n")
REPORT = ('"""Monthly report of the entries."""\nfrom . import store\n\n\n'
          "def monthly(path):\n    \"\"\"Totals by note.\"\"\"\n    out = {}\n    for item in store.load(path):\n"
          "        out[item['note']] = out.get(item['note'], 0) + item['amount']\n    return out\n")


def _ledger(tmp_path: Path, *, git: bool) -> Path:
    ws = tmp_path / ("ledger-git" if git else "ledger-plain")
    _write(ws, LEDGER)
    if git:
        _git(ws, "init", "-q")
        _git(ws, "add", ".")
        _git(ws, "commit", "-q", "-m", "init")
    return ws


def _tree(ws: Path) -> dict[str, tuple[int, int]]:
    """Every file under .ua/: its mtime and size."""
    return {str(p.relative_to(ws)): (p.stat().st_mtime_ns, p.stat().st_size) for p in (ws / ".ua").rglob("*") if p.is_file()}


# ---- (a) a current map: nothing runs ---------------------------------------------------------------------------------

def test_a_map_with_nothing_changed_is_current_and_nothing_is_touched(tmp_path):
    ws = _ledger(tmp_path, git=True)
    first = map_project(ws, Model(1), domain_files=["ledger/store.py"])
    assert "no map" in first["status"] and "problems: 0" in first["finish"] and first["finish-rc"] == 0, first["finish"]
    state = json.loads((ws / ".ua/map-state.json").read_text())
    assert state["gitCommitHash"] == _git(ws, "rev-parse", "HEAD")
    assert sorted(state["files"]) == sorted(p for p in LEDGER if not p.startswith(".dream/"))

    before = _tree(ws)
    time.sleep(0.05)
    started = time.monotonic()
    out = glue(ws, "status")
    took = time.monotonic() - started
    assert "The map is current" in out, out
    assert state["builtAt"] in out and "9 files" in out and _git(ws, "rev-parse", "HEAD")[:7] in out, out
    assert "stop" in out.lower()
    assert _tree(ws) == before                          # no scan, no batches, no rewrite: every .ua file untouched
    assert took < 15, took                              # seconds, not a pipeline

    # the check is by content: a touched but unchanged file is still current
    os.utime(ws / EDITED, None)
    assert "The map is current" in glue(ws, "status") and _tree(ws) == before
    # ... and the ignore rules are part of the map: changing them is not current (the docs leave the scope)
    ignore = ws / ".ua/.understandignore"
    rules = ignore.read_text()
    ignore.write_text(rules + "docs/\n")
    out = glue(ws, "status")
    assert "update mode" in out and "ignore rules changed" in out and "docs/guide.md" in out, out
    ignore.write_text(rules)
    assert "The map is current" in glue(ws, "status")


# ---- (b) one edit, one addition, one deletion ------------------------------------------------------------------------

@pytest.mark.parametrize("git", [True, False], ids=["git-uncommitted", "no-git"])
def test_an_update_redoes_only_the_changed_files(tmp_path, git):
    ws = _ledger(tmp_path, git=git)
    first = map_project(ws, Model(1), domain_files=["ledger/store.py", DELETED])
    assert first["finish-rc"] == 0, first["finish"]
    old = json.loads((ws / ".ua/knowledge-graph.json").read_text())
    old_layers = {L["id"]: L["nodeIds"] for L in old["layers"]}
    deleted_ids = {n["id"] for n in old["nodes"] if _path_of(n) == DELETED}
    assert deleted_ids and any(e["target"] in deleted_ids for e in old["edges"] if _path_of({"id": e["source"]}) != DELETED)

    (ws / EDITED).write_text(EDIT)                      # uncommitted in the git variant
    (ws / ADDED).write_text(REPORT)
    (ws / DELETED).unlink()
    model = Model(2)
    log = map_project(ws, model)
    st = log["status"]
    assert "update mode" in st, st
    assert re.search(r"1 edited\b", st) and re.search(r"1 added\b", st) and re.search(r"1 deleted\b", st), st
    assert EDITED in st and ADDED in st and DELETED in st
    assert json.loads((ws / ".ua/tmp/changed-files.json").read_text()) == sorted([EDITED, ADDED])
    assert sorted(set(model.analysed)) == sorted([EDITED, ADDED])      # one file changed -> one file analysed
    batches = json.loads((ws / ".ua/intermediate/batches.json").read_text())
    assert sorted(f["path"] for b in batches["batches"] for f in b["files"]) == sorted([EDITED, ADDED])

    new = json.loads((ws / ".ua/knowledge-graph.json").read_text())
    old_by_id = {n["id"]: n for n in old["nodes"]}
    kept = [n for n in new["nodes"] if _path_of(n) not in (EDITED, ADDED)]
    assert {n["id"] for n in kept} == {n["id"] for n in old["nodes"] if _path_of(n) not in (EDITED, DELETED)}
    for n in kept:                                      # carried over verbatim: summaries, symbols, key order
        assert json.dumps(n) == json.dumps(old_by_id[n["id"]]), n["id"]
    fresh = [n for n in new["nodes"] if _path_of(n) in (EDITED, ADDED)]
    assert fresh and all("(analysis 2)" in n["summary"] for n in fresh)
    assert "function:ledger/store.py:count" in {n["id"] for n in fresh}
    # the deleted file: its nodes and every edge touching them are gone
    assert not [n for n in new["nodes"] if _path_of(n) == DELETED]
    assert not [e for e in new["edges"] if e["source"] in deleted_ids or e["target"] in deleted_ids]
    edges = {(e["source"], e["target"], e["type"]) for e in new["edges"]}
    assert ("file:ledger/book.py", "file:ledger/store.py", "imports") in edges       # unchanged -> re-analysed kept
    assert ("file:ledger/report.py", "file:ledger/store.py", "imports") in edges     # the new file's own
    # layers keep their assignments; the new file joins its folder's layer; the tour drops the missing id
    new_layers = {L["id"]: L["nodeIds"] for L in new["layers"]}
    assert set(new_layers) == set(old_layers)
    for lid, members in old_layers.items():
        assert [i for i in members if i not in deleted_ids] == [i for i in new_layers[lid] if i != "file:" + ADDED]
    assert "file:" + ADDED in new_layers["layer:ledger"]
    assert not [i for s in new["tour"] for i in s["nodeIds"] if i in deleted_ids]
    assert [s["order"] for s in new["tour"]] == list(range(1, len(new["tour"]) + 1))
    # checked like a full run; the domain step on the deleted file is a problem the model fixes
    assert "problems: 0" in log["assemble"]
    assert log["finish-rc"] != 0 and DELETED in log["finish"] and "step:work:s1" in log["finish"], log["finish"]
    dom = json.loads((ws / ".ua/intermediate/domain.json").read_text())
    dom["nodes"] = [n for n in dom["nodes"] if n["id"] != "step:work:s1"]
    dom["edges"] = [e for e in dom["edges"] if e["target"] != "step:work:s1"]
    (ws / ".ua/intermediate/domain.json").write_text(json.dumps(dom))
    rc, out = glue_rc(ws, "finish")
    assert rc == 0 and "problems: 0" in out, out
    # meta, fingerprints and the recorded state follow the new inventory
    meta = json.loads((ws / ".ua/meta.json").read_text())
    assert meta["analyzedFiles"] == 9 and meta["gitCommitHash"] == (_git(ws, "rev-parse", "HEAD") if git else "none")
    prints = json.loads((ws / ".ua/fingerprints.json").read_text())["files"]
    assert ADDED in prints and DELETED not in prints
    state = json.loads((ws / ".ua/map-state.json").read_text())
    assert ADDED in state["files"] and DELETED not in state["files"]
    assert "The map is current" in glue(ws, "status")


def test_an_update_with_nothing_to_analyse_still_removes_what_left(tmp_path):
    ws = _ledger(tmp_path, git=False)
    assert map_project(ws, Model(1), domain_files=["ledger/store.py"])["finish-rc"] == 0
    (ws / "docs/guide.md").unlink()
    log = map_project(ws, Model(2))
    assert "update mode" in log["status"] and "nothing to analyse" in log["scan-result"], log["scan-result"]
    assert log["finish-rc"] == 0, log["finish"]
    new = json.loads((ws / ".ua/knowledge-graph.json").read_text())
    assert "document:docs/guide.md" not in {n["id"] for n in new["nodes"]}
    assert "The map is current" in glue(ws, "status")


# ---- (c) installed dependency trees ----------------------------------------------------------------------------------

INSTALLED = {
    "app/main.py": '"""Entry point."""\nfrom app import util\n\n\nif __name__ == "__main__":\n    print(util.double(2))\n',
    "app/util.py": '"""Helpers."""\n\n\ndef double(x):\n    return 2 * x\n',
    "README.md": "# App\n\nDoubles numbers.\n",
    # pip install --target tools/pylibs
    "tools/pylibs/requests/__init__.py": '"""Requests."""\n',
    "tools/pylibs/requests/api.py": "def get(url):\n    return url\n",
    "tools/pylibs/requests-2.31.0.dist-info/METADATA": "Name: requests\nVersion: 2.31.0\n",
    "tools/pylibs/requests-2.31.0.dist-info/RECORD": "requests/__init__.py,,\n",
    "tools/pylibs/idna/__init__.py": "",
    "tools/pylibs/idna-3.7.dist-info/METADATA": "Name: idna\n",
    # PLAYWRIGHT_BROWSERS_PATH=tools/pw-browsers
    "tools/pw-browsers/chromium-1134/INSTALLATION_COMPLETE": "",
    "tools/pw-browsers/chromium-1134/chrome-linux/resources.json": "{}\n",
    "tools/pw-browsers/chromium-1134/chrome-linux/about.txt": "chromium\n",
    "tools/pw-browsers/firefox-1463/INSTALLATION_COMPLETE": "",
    "tools/pw-browsers/firefox-1463/firefox/application.ini": "[App]\nName=Firefox\n",
    # look-alikes that are the project's own: setuptools metadata of the project, a Firefox extension's source
    "src/myproj.egg-info/PKG-INFO": "Name: myproj\n",
    "src/myproj/__init__.py": '"""My project."""\n',
    "firefox-extension/manifest.json": '{"name": "ext"}\n',
}


def test_installed_dependency_trees_are_skipped_named_and_can_come_back(tmp_path):
    ws = tmp_path / "workspace"
    _write(ws, INSTALLED)
    (ws / ".ua/tmp").mkdir(parents=True)
    shutil.copy(GLUE, ws / ".ua/tmp/ua_glue.py")
    st = glue(ws, "status")
    assert "no map" in st
    assert re.search(r"tools/pylibs\b.*\b6 files\b.*installed Python packages", st), st
    assert re.search(r"tools/pw-browsers\b.*\b5 files\b.*Playwright", st), st
    scan_cmd = printed(st, "scan-project.mjs")
    assert "/tools/pylibs/" in scan_cmd and "/tools/pw-browsers/" in scan_cmd and "--exclude-analysis-data" in scan_cmd
    assert "egg-info" not in scan_cmd and "firefox-extension" not in scan_cmd
    _run(scan_cmd, ws)
    paths = {f["path"] for f in json.loads((ws / ".ua/tmp/scan.json").read_text())["files"]}
    assert not [p for p in paths if p.startswith("tools/")]
    assert {"app/main.py", "src/myproj/__init__.py", "src/myproj.egg-info/PKG-INFO", "firefox-extension/manifest.json"} <= paths
    glue(ws, "imports-input")
    ua(ws, "extract-import-map.mjs", ws / ".ua/tmp/imports-in.json", ws / ".ua/tmp/imports-out.json")
    report = glue(ws, "scan-result")                    # the report names each tree, its file count and the reason
    assert re.search(r"tools/pylibs\b.*\b6 files\b.*installed Python packages", report), report
    assert re.search(r"tools/pw-browsers\b.*\b5 files\b.*Playwright", report), report
    assert "--include" in report
    # the user names one: it is mapped, the other stays skipped
    st = glue(ws, "status", "--include", "tools/pylibs")
    scan_cmd = printed(st, "scan-project.mjs")
    assert "/tools/pylibs/" not in scan_cmd and "/tools/pw-browsers/" in scan_cmd, scan_cmd
    assert "tools/pylibs" in st and "user" in st.lower()
    _run(scan_cmd, ws)
    paths = {f["path"] for f in json.loads((ws / ".ua/tmp/scan.json").read_text())["files"]}
    assert "tools/pylibs/requests/api.py" in paths and not [p for p in paths if p.startswith("tools/pw-browsers/")]


def test_a_map_from_before_the_state_file_updates_from_its_fingerprints_and_drops_the_trees(tmp_path):
    """The owner's workspace: mapped by the earlier skill, installed tools included, no .ua/map-state.json -- only the
    plugin's fingerprints.json. The next run compares against the fingerprints, re-analyses nothing that did not change
    and drops the installed trees' nodes in update mode."""
    ws = tmp_path / "workspace"
    _write(ws, INSTALLED)
    first = map_project(ws, Model(1), status_args=("--include", "tools/pylibs,tools/pw-browsers"), domain_files=["app/main.py"])
    assert first["finish-rc"] == 0, first["finish"]
    old = json.loads((ws / ".ua/knowledge-graph.json").read_text())
    assert [n for n in old["nodes"] if _path_of(n).startswith("tools/")]
    for name in (".ua/map-state.json", ".ua/tmp/run.json", ".ua/intermediate/scan-hashes.json",
                 ".ua/intermediate/finished-graph.json"):
        (ws / name).unlink()                                              # as the earlier skill left .ua
    model = Model(2)
    log = map_project(ws, model)
    st = log["status"]
    assert "update mode" in st and "0 edited" in st and "0 added" in st, st
    assert re.search(r"left the scope: tools/pylibs -- 6 files", st) and re.search(r"left the scope: tools/pw-browsers -- 5 files", st), st
    assert "nothing to analyse" in log["scan-result"] and model.analysed == []
    assert log["finish-rc"] == 0, log["finish"]
    new = json.loads((ws / ".ua/knowledge-graph.json").read_text())
    assert not [n for n in new["nodes"] if _path_of(n).startswith("tools/")]
    old_by_id = {n["id"]: n for n in old["nodes"]}
    for n in new["nodes"]:
        assert json.dumps(n) == json.dumps(old_by_id[n["id"]]), n["id"]
    assert not [i for L in new["layers"] for i in L["nodeIds"] if "tools/" in i]
    assert "The map is current" in glue(ws, "status")


def test_site_packages_and_a_virtual_environment_count_as_installed_trees(tmp_path):
    ws = tmp_path / "ws"
    _write(ws, {"main.py": "print(1)\n", "env/pyvenv.cfg": "home = /usr/bin\n", "env/lib/python3.12/site-packages/x/__init__.py": "",
                "vendorlibs/lib/python3.12/site-packages/y/__init__.py": "", "vendorlibs/README.md": "# mine\n"})
    (ws / ".ua/tmp").mkdir(parents=True)
    shutil.copy(GLUE, ws / ".ua/tmp/ua_glue.py")
    st = glue(ws, "status")
    assert re.search(r"\benv\b.*virtual environment", st), st
    assert "vendorlibs/lib/python3.12/site-packages" in st
    assert "/vendorlibs/README.md" not in printed(st, "scan-project.mjs")


# ---- (d) the big-repo path ---------------------------------------------------------------------------------------------

def _big_project(ws: Path) -> None:
    files = {"README.md": "# Orbit\n\nOrbit simulates satellites around a planet.\n",
             "package.json": json.dumps({"name": "orbit-web", "description": "The web viewer for orbit runs."}) + "\n",
             "main.py": '"""Orbit command line: run a simulation and print the report."""\nfrom orbit import core\n\n\n'
                        "def run():\n    return core.step(1)\n\n\nif __name__ == '__main__':\n    run()\n",
             "orbit/__init__.py": "",
             "orbit/core.py": '"""Physics core: advances every body by one time step."""\n\n\n'
                              "def step(dt):\n    \"\"\"Advance the simulation by dt seconds.\"\"\"\n    return dt\n"}
    for k in range(150):
        name = f"orbit/mod_{k:03d}.py"
        if k % 3 == 0:          # a docstring
            head = f'"""Handles satellite family {k} and its orbital elements."""\n'
        elif k % 3 == 1:        # a leading comment only
            head = f"# Converts telemetry frames of kind {k} into state vectors.\n"
        else:                   # nothing: the draft must still not be the file name
            head = ""
        files[name] = (head + "from orbit import core\n\n\n"
                       f"def handle_{k}(frame):\n    total = 0\n    for part in frame:\n        total += core.step(part)\n"
                       "    if total < 0:\n        raise ValueError('negative')\n    return total\n")
    for k in range(10):
        files[f"docs/topic_{k}.md"] = f"# Topic {k}\n\nHow orbit handles topic number {k}.\n"
    _write(ws, files)


class BigModel(Model):
    def layers_and_tour(self, ws: Path, listing: str) -> None:
        """Too many files to list: `nodes` names folders, and the layers take whole folders by path."""
        folders = re.findall(r"^\s+(\S+/) \(\d+ files?\)", listing, re.M)
        assert "orbit/" in folders and "docs/" in folders, listing
        (ws / ".ua/intermediate/layers.json").write_text(json.dumps([
            {"id": "layer:simulation", "name": "Simulation", "description": "The orbit package and its entry point.",
             "paths": ["orbit/", "main.py"]},
            {"id": "layer:docs", "name": "Docs", "description": "README and topic pages.", "paths": ["docs/", "README.md"]},
            {"id": "layer:config", "name": "Config", "description": "Everything else.", "paths": ["."]}]))
        (ws / ".ua/intermediate/tour.json").write_text(json.dumps([
            {"order": 1, "title": "Start", "description": "The README.", "nodeIds": ["document:README.md"]},
            {"order": 2, "title": "Entry", "description": "The command line.", "nodeIds": ["file:main.py"]},
            {"order": 3, "title": "Core", "description": "The physics.", "nodeIds": ["file:orbit/core.py"]}]))


def test_a_big_repo_is_drafted_and_only_the_key_files_are_summarised(tmp_path):
    ws = tmp_path / "orbit"
    _big_project(ws)
    refined: dict[str, str] = {}

    def summarise(ws: Path, draft_out: str) -> None:
        """The model reads each key file the draft named and writes a real summary for it."""
        section = draft_out.split("key files", 1)[1]
        keys = re.findall(r"^\s+(\S+) \(", section, re.M)
        assert "main.py" in keys and "orbit/core.py" in keys, draft_out
        for path in keys:
            refined[path] = f"Real summary of {path}, written from its code."
        (ws / ".ua/intermediate/summaries.json").write_text(json.dumps(refined))

    model = BigModel(1)
    log = map_project(ws, model, domain_files=["orbit/core.py"], summarise=summarise)
    assert "big-repo path" in log["batch-inputs"], log["batch-inputs"]
    assert len(log["batch-inputs"].splitlines()) < 40                     # not a listing of 160+ files
    batches = json.loads((ws / ".ua/intermediate/batches.json").read_text())
    for b in batches["batches"]:                                           # a complete draft for every batch
        assert (ws / f".ua/intermediate/batch-{b['batchIndex']}.json").is_file(), b["batchIndex"]
    assert log["finish-rc"] == 0 and "problems: 0" in log["finish"], log["finish"]
    assert len(log["nodes"].splitlines()) < 60, log["nodes"]              # step 6 lists folders, not every file

    kg = json.loads((ws / ".ua/knowledge-graph.json").read_text())
    by_id = {n["id"]: n for n in kg["nodes"]}
    files = [n for n in kg["nodes"] if n["type"] in FILE_TYPES]
    assert len(files) == 165
    for n in kg["nodes"]:                                                   # never the file name
        assert n["summary"].strip() and n["summary"].strip().rstrip(".") not in (n["name"], n["filePath"], Path(n["filePath"]).stem)
    drafted = [k for k in range(150) if f"orbit/mod_{k:03d}.py" not in refined]      # the files that kept their drafts
    doc, comment, bare, bare2 = (next(k for k in drafted if k % 3 == 0), next(k for k in drafted if k % 3 == 1),
                                 *[k for k in drafted if k % 3 == 2][:2])
    assert len(drafted) >= 100 and len(refined) <= 30                       # the model's work is bounded by the key files
    assert f"satellite family {doc} and its orbital elements" in by_id[f"file:orbit/mod_{doc:03d}.py"]["summary"]
    assert f"telemetry frames of kind {comment}" in by_id[f"file:orbit/mod_{comment:03d}.py"]["summary"]
    assert by_id[f"file:orbit/mod_{bare:03d}.py"]["summary"] != by_id[f"file:orbit/mod_{bare2:03d}.py"]["summary"]
    assert f"handle_{bare}" in by_id[f"file:orbit/mod_{bare:03d}.py"]["summary"]    # no comment: what the parser found
    assert "Topic 7" in by_id["document:docs/topic_7.md"]["summary"]
    assert "web viewer for orbit runs" in by_id["config:package.json"]["summary"]
    for path, text in refined.items():                                      # the key files carry the model's words
        assert by_id[next(i for i in by_id if i.endswith(":" + path) and by_id[i]["type"] in FILE_TYPES)]["summary"] == text
    fn = by_id["function:orbit/mod_003.py:handle_3"]
    assert fn["lineRange"][0] >= 1 and fn["lineRange"][1] > fn["lineRange"][0]
    edges = {(e["source"], e["target"], e["type"]) for e in kg["edges"]}
    assert ("file:orbit/mod_003.py", "function:orbit/mod_003.py:handle_3", "contains") in edges
    assert ("file:orbit/mod_003.py", "file:orbit/core.py", "imports") in edges
    assert ("file:orbit/core.py", "function:orbit/core.py:step", "exports") in edges
    for n in files:
        assert 3 <= len(n["tags"]) <= 5 and all(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", t) for t in n["tags"]), n
        assert n["complexity"] in ("simple", "moderate", "complex")
    assert set(model.analysed) == {n["filePath"] for n in files}
    assert "The map is current" in glue(ws, "status")


def test_a_later_big_run_starts_without_the_last_runs_real_summaries(tmp_path):
    """The model's summaries.json belongs to the files as they were: an update that drafts main.py again must not put
    last time's summary on the edited file. And a parser output older than its input is named, never drafted from."""
    ws = tmp_path / "orbit"
    _big_project(ws)

    def summarise(ws: Path, draft_out: str) -> None:
        (ws / ".ua/intermediate/summaries.json").write_text(json.dumps({"main.py": "OLD real summary of main.py."}))
    assert map_project(ws, BigModel(1), domain_files=["orbit/core.py"], summarise=summarise)["finish-rc"] == 0
    for k in range(150):                                                    # a big edit: the update drafts again
        p = ws / f"orbit/mod_{k:03d}.py"
        p.write_text(p.read_text() + "\n")
    (ws / "main.py").write_text('"""Orbit command line, now with a report flag."""\nfrom orbit import core\n\n\n'
                                "if __name__ == '__main__':\n    core.step(2)\n")
    log = map_project(ws, BigModel(2))
    assert "big-repo path" in log["batch-inputs"] and log["finish-rc"] == 0, log["finish"]
    node = next(n for n in json.loads((ws / ".ua/knowledge-graph.json").read_text())["nodes"] if n["id"] == "file:main.py")
    assert "OLD" not in node["summary"] and "report flag" in node["summary"], node
    # the parser's output must be newer than the input batch-inputs wrote
    out = ws / ".ua/tmp/extract-out-all.json"
    os.utime(out, (1, 1))
    rc, text = glue_rc(ws, "draft")
    assert rc != 0 and "extract-out-all.json" in text and "Traceback" not in text, text


def test_the_draft_names_what_is_missing_instead_of_a_traceback(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".ua/tmp").mkdir(parents=True)
    shutil.copy(GLUE, ws / ".ua/tmp/ua_glue.py")
    rc, out = glue_rc(ws, "draft")
    assert rc != 0 and "Traceback" not in out and "problem:" in out, out


# ---- the drafting rules, on text -------------------------------------------------------------------------------------

def _glue_module():
    spec = importlib.util.spec_from_file_location("ua_glue_update_under_test", GLUE)
    mod = importlib.util.module_from_spec(spec)
    before, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = before
    return mod


@pytest.mark.parametrize("path, language, category, text, expected", [
    ("a.py", "python", "code", '#!/usr/bin/env python3\n"""Parses flight plans into legs.\n\nMore."""\nimport os\n', "Parses flight plans into legs."),
    ("a.go", "go", "code", "// Copyright 2024 ACME. All rights reserved.\n// License: MIT.\n\n// Package route finds the shortest path.\npackage route\n", "shortest path"),
    ("a.c", "c", "code", "#include <stdio.h>\n#define N 3\nint main(void) { return 0; }\n", None),
    ("a.ts", "typescript", "code", "/**\n * Renders the orbit chart.\n */\nexport const x = 1;\n", "Renders the orbit chart."),
    ("guide.md", "markdown", "docs", "# Install\n\nRun the installer, then log in.\n", "Install"),
    ("pyproject.toml", "toml", "config", '[project]\nname = "x"\ndescription = "Tracks invoices."\n', "Tracks invoices."),
    # a docstring that only repeats the file name says nothing: what the parser found instead
    ("pkg/utils.py", "python", "code", '"""utils.py"""\n\n\ndef clamp(x, lo, hi):\n    return max(lo, min(x, hi))\n', "clamp"),
])
def test_drafted_file_summaries_come_from_the_content(path, language, category, text, expected):
    glue = _glue_module()
    summary = glue.draft_summary(path, language, category, text, {})
    assert summary and summary.strip().rstrip(".") not in (path, Path(path).stem)
    if expected:
        assert expected in summary, summary
    else:
        assert "#include" not in summary and "stdio" not in summary.split("(")[0], summary


def test_layers_can_name_whole_folders_and_the_most_specific_one_wins():
    glue = _glue_module()
    ids = ["file:a/x.py", "file:a/b/y.py", "document:README.md", "file:c/z.py"]
    layers = [{"id": "layer:a", "name": "A", "paths": ["a/"]}, {"id": "layer:b", "name": "B", "paths": ["a/b"]},
              {"id": "layer:rest", "name": "Rest", "paths": ["."]}, {"id": "layer:docs", "name": "Docs", "nodeIds": ["document:README.md"]}]
    got = glue.expand_layer_paths(layers, {i: i.split(":", 1)[1] for i in ids})
    assert got == {"layer:a": ["file:a/x.py"], "layer:b": ["file:a/b/y.py"], "layer:rest": ["file:c/z.py"], "layer:docs": []}


# ---- gate 1 (2026-09-24 08:34): the evaluator's findings, pinned before the fixes -----------------------------------

def _until_step_7(ws: Path, model: Model, *, status_args: tuple[str, ...] = (), layers: bool = True,
                  fingerprints: bool = True) -> str:
    """Steps 1-7 as map_project, then the session dies: no domain graph, no finish (the evaluator's p2_abandoned.py)."""
    (ws / ".ua/tmp").mkdir(parents=True, exist_ok=True)
    shutil.copy(GLUE, ws / ".ua/tmp/ua_glue.py")
    st = glue(ws, "status", *status_args)
    update = "update mode" in st
    if not update:
        model.project(ws)
    _run(printed(st, "scan-project.mjs"), ws)
    glue(ws, "imports-input")
    ua(ws, "extract-import-map.mjs", ws / ".ua/tmp/imports-in.json", ws / ".ua/tmp/imports-out.json")
    sr = glue(ws, "scan-result")
    if "nothing to analyse" not in sr:
        _run(printed(sr, "compute-batches.mjs") if update else 'node "$UA_SKILLS/understand/compute-batches.mjs" "$PWD"', ws)
        glue(ws, "batch-inputs")
        for b in json.loads((ws / ".ua/intermediate/batches.json").read_text())["batches"]:
            i = b["batchIndex"]
            ua(ws, "extract-structure.mjs", ws / f".ua/tmp/extract-in-{i}.json", ws / f".ua/tmp/extract-out-{i}.json")
            model.batch(ws, b, json.loads((ws / f".ua/tmp/extract-out-{i}.json").read_text()))
    ua(ws, "merge-batch-graphs.py", ws)
    if not update and layers:
        model.layers_and_tour(ws, glue(ws, "nodes"))
    glue_rc(ws, "assemble")
    if fingerprints:
        ua(ws, "build-fingerprints.mjs", ws / ".ua/intermediate/fingerprint-input.json")
    return st


def test_a_run_that_never_finished_is_never_current(tmp_path):
    """BLOCKING 1: map-state.json is bound to the map finish validated. (a) a first map that died after step 7, with
    fingerprints written; (a') the same with assemble problems and no layers; (b) a --full rebuild that died after
    assemble; (c) an update that died after assemble, whose edit the user then reverted."""
    ws = _ledger(tmp_path / "a", git=False)
    _until_step_7(ws, Model(1))
    assert (ws / ".ua/fingerprints.json").is_file() and not (ws / ".ua/map-state.json").exists()
    st = glue(ws, "status")
    assert "The map is current" not in st and "no map" in st and "did not finish" in st, st

    ws = _ledger(tmp_path / "a2", git=False)
    _until_step_7(ws, Model(1), layers=False)
    st = glue(ws, "status")
    assert "The map is current" not in st and "no map" in st, st

    ws = _ledger(tmp_path / "b", git=False)
    assert map_project(ws, Model(1), domain_files=["ledger/store.py"])["finish-rc"] == 0
    (ws / ".ua/intermediate/layers.json").unlink()
    (ws / ".ua/intermediate/tour.json").unlink()
    _until_step_7(ws, Model(2), status_args=("--full",), layers=False, fingerprints=False)
    st = glue(ws, "status")
    assert "The map is current" not in st and "no map" in st and "did not finish" in st, st   # the rebuild goes on

    ws = _ledger(tmp_path / "c", git=False)
    assert map_project(ws, Model(1), domain_files=["ledger/store.py"])["finish-rc"] == 0
    finished = json.loads((ws / ".ua/knowledge-graph.json").read_text())
    original = (ws / EDITED).read_text()
    (ws / EDITED).write_text(EDIT)
    _until_step_7(ws, Model(2))
    assert "function:ledger/store.py:count" in {n["id"] for n in json.loads((ws / ".ua/knowledge-graph.json").read_text())["nodes"]}
    (ws / EDITED).write_text(original)                                   # the user reverts the edit
    st = glue(ws, "status")
    assert "The map is current" not in st and "update mode" in st and "did not finish" in st, st
    log = map_project(ws, Model(3))
    assert log["finish-rc"] == 0, log["finish"]
    kg = json.loads((ws / ".ua/knowledge-graph.json").read_text())
    assert "function:ledger/store.py:count" not in {n["id"] for n in kg["nodes"]}   # the last finished map, restored
    assert [json.dumps(n) for n in kg["nodes"]] == [json.dumps(n) for n in finished["nodes"]]
    assert "The map is current" in glue(ws, "status")


def test_a_status_run_mid_map_keeps_the_scan_time_hashes(tmp_path):
    """Note 10: the model re-checks status in the middle of a run and the user edits a file after it was analysed;
    finish records the files as they were scanned, so the next status sees the edit."""
    ws = _ledger(tmp_path, git=False)
    _until_step_7(ws, Model(1))
    glue(ws, "status")                                                   # mid-run
    (ws / EDITED).write_text(EDIT)                                       # after the analysis
    Model(1).domain(ws, ["ledger/store.py"])
    rc, out = glue_rc(ws, "finish")
    assert rc == 0 and "problems: 0" in out, out
    st = glue(ws, "status")
    assert "update mode" in st and EDITED in st, st


def test_the_fingerprints_fallback_is_only_for_a_map_from_before_the_state_file(tmp_path):
    """A DREAM-106 run leaves .ua/tmp/run.json, so its own step-7 fingerprints never pass for a legacy map; a legacy map
    (no run.json, no map-state.json) still updates rather than rebuilds, and an update of it that dies resumes."""
    ws = tmp_path / "legacy"
    _write(ws, INSTALLED)
    assert map_project(ws, Model(1), status_args=("--include", "tools/pylibs,tools/pw-browsers"),
                       domain_files=["app/main.py"])["finish-rc"] == 0
    (ws / ".ua/map-state.json").unlink()
    (ws / ".ua/tmp/run.json").unlink()                                   # as the earlier skill left .ua/tmp
    (ws / ".ua/intermediate/finished-graph.json").unlink(missing_ok=True)
    (ws / ".ua/intermediate/scan-hashes.json").unlink(missing_ok=True)   # a map from before DREAM-106 never has one
    _until_step_7(ws, Model(2))                                          # the legacy update dies after assemble
    st = glue(ws, "status")
    assert "update mode" in st and "did not finish" in st, st
    assert map_project(ws, Model(3))["finish-rc"] == 0
    assert not [n for n in json.loads((ws / ".ua/knowledge-graph.json").read_text())["nodes"] if _path_of(n).startswith("tools/")]


def test_a_file_name_that_is_not_utf8_is_skipped_like_the_scan_does(tmp_path):
    """BLOCKING 3: without git, a Latin-1 file name crashed status; the scan skips such names, so status does too."""
    for git in (False, True):
        ws = tmp_path / ("git" if git else "plain")
        _write(ws, {"main.py": "print(1)\n", "README.md": "# X\n\nY.\n"})
        with open(os.path.join(os.fsencode(ws), b"caf\xe9.txt"), "wb") as f:
            f.write(b"a latin-1 named file\n")
        if git:
            _git(ws, "init", "-q")
            _git(ws, "add", ".")
            _git(ws, "commit", "-q", "-m", "i")
        log = map_project(ws, Model(1), domain_files=["main.py"])
        assert "2 files to map" in log["status"] and log["finish-rc"] == 0, (log["status"], log["finish"])
        assert "The map is current" in glue(ws, "status")


def test_installed_tree_lookalikes_are_not_skipped(tmp_path):
    """Note 4: two of the project's own egg-info folders, a browser-version folder name without an install marker next
    to project files; a real browsers root (only install dirs plus .links); a mixed folder; a conda prefix."""
    ws = tmp_path / "ws"
    _write(ws, {"main.py": "print(1)\n",
                "src/alpha/__init__.py": '"""Alpha."""\n', "src/beta/__init__.py": '"""Beta."""\n',
                "src/alpha.egg-info/PKG-INFO": "Name: alpha\n", "src/beta.egg-info/PKG-INFO": "Name: beta\n",
                "src/core.py": '"""Core."""\n',
                "patches/firefox-115/fix.diff": "--- a\n+++ b\n", "patches/apply.py": '"""Applies the patches."""\n',
                "pw/chromium-1243/INSTALLATION_COMPLETE": "", "pw/chromium-1243/x.json": "{}\n",
                "pw/firefox-1463/INSTALLATION_COMPLETE": "", "pw/firefox-1463/y.json": "{}\n", "pw/.links/abc": "/x\n",
                "mixed/webkit-2100/INSTALLATION_COMPLETE": "", "mixed/webkit-2100/z.json": "{}\n",
                "mixed/notes.md": "# Mixed\n\nOur notes.\n",
                "legacylibs/old-1.0.egg-info/installed-files.txt": "../old/__init__.py\n", "legacylibs/old/__init__.py": "",
                "condaenv/conda-meta/history": "==> 2026 <==\n", "condaenv/lib/python3.12/os.py": '"""OS."""\n',
                "condaenv/bin/pip": "#!/usr/bin/env python\n"})
    (ws / ".ua/tmp").mkdir(parents=True)
    shutil.copy(GLUE, ws / ".ua/tmp/ua_glue.py")
    st = glue(ws, "status")
    skipped = dict(re.findall(r"^  (\S+) -- \d+ files: (.*)$", st, re.M))
    assert set(skipped) == {"pw", "mixed/webkit-2100", "legacylibs", "condaenv"}, st
    assert "conda" in skipped["condaenv"] and "Playwright" in skipped["pw"]
    scan = printed(st, "scan-project.mjs")
    assert "/src/" not in scan and "/patches/" not in scan and "/mixed/" not in scan.replace("/mixed/webkit-2100/", "")


def test_nothing_in_scope_says_so_instead_of_failing_at_the_merge(tmp_path):
    """Note 5: an empty scope (0 files) broke at the merge; status says "nothing to map", names what was skipped and how
    to bring it back, and writes no run."""
    ws = tmp_path / "ws"
    _write(ws, {"logo.png": "x", "notes.log": "x\n", "libs/pkg-1.0.dist-info/METADATA": "Name: pkg\n", "libs/pkg/a.py": "a = 1\n"})
    (ws / ".ua/tmp").mkdir(parents=True)
    shutil.copy(GLUE, ws / ".ua/tmp/ua_glue.py")
    st = glue(ws, "status")
    assert "nothing to map" in st and "libs" in st and "--include" in st and "scan-project.mjs" not in st, st
    assert not (ws / ".ua/tmp/run.json").exists()


def test_a_later_exclude_wins_and_an_include_that_changes_nothing_is_not_a_rule(tmp_path):
    """Note 6: --include a tree, finish; a later --exclude of it takes it out again; an --include of a folder that was
    never skipped does not change the rules, so the map stays current."""
    ws = tmp_path / "ws"
    _write(ws, {"main.py": "print(1)\n", "docs/a.md": "# A\n\nB.\n", "libs/pkg-1.dist-info/METADATA": "x\n",
                "libs/pkg/__init__.py": '"""p"""\n'})
    assert map_project(ws, Model(1), status_args=("--include", "libs"), domain_files=["main.py"])["finish-rc"] == 0
    assert "The map is current" in glue(ws, "status", "--include", "docs")
    st = glue(ws, "status", "--exclude", "libs/")
    assert "update mode" in st and "libs" in st and ',libs/"' in printed(st, "scan-project.mjs"), st


def test_update_mode_on_the_big_path_never_asks_for_layers(tmp_path):
    """Note 7: in update mode the big-repo path's printed next steps skip step 6, like the per-batch path's."""
    ws = tmp_path / "orbit"
    _big_project(ws)
    assert map_project(ws, BigModel(1), domain_files=["orbit/core.py"])["finish-rc"] == 0
    for name in [f"orbit/mod_{k:03d}.py" for k in range(150)] + ["orbit/core.py", "main.py"]:   # 152 files: > 150
        p = ws / name
        p.write_text(p.read_text() + "\n")
    log = map_project(ws, BigModel(2))
    assert log["finish-rc"] == 0
    for text in (log["batch-inputs"], log["draft"]):
        assert "skip step 6" in text and "assemble" in text, text


def test_documents_binaries_and_tags_in_the_draft():
    """Note 8: a document's summary has no Markdown header marks or YAML front matter; a binary file gets a type-and-size
    summary; the draft leaves room for the merge's "tested" tag."""
    glue = _glue_module()
    doc = "---\ntitle: Setup guide\nlayout: page\n---\n\n# Setup\n\n## Install\n\nRun the installer, then log in.\n"
    summary = glue.draft_summary("docs/setup.md", "markdown", "docs", doc, {})
    assert "#" not in summary and "layout" not in summary and "---" not in summary and "Run the installer" in summary, summary
    front_only = "---\ntitle: Release notes\ndescription: What changed in each release.\n---\n"
    assert "What changed in each release" in glue.draft_summary("docs/notes.md", "markdown", "docs", front_only, {})
    assert glue.binary_kind(b"\x7fELF\x02\x01\x01" + b"\0" * 64) and glue.binary_kind(b"text\0more") and not glue.binary_kind(b"plain text\n")
    assert len(glue.draft_tags("a/b/c/d.py", "python", "code", ("entry-point",))) <= 4


def test_a_binary_file_is_never_read_as_text(tmp_path):
    """Note 8: a binary in scope (an ELF with no extension) gets a type-and-size summary and stays out of the parser input."""
    ws = tmp_path / "orbit"
    _big_project(ws)
    (ws / "bin").mkdir()
    (ws / "bin/engine").write_bytes(b"\x7fELF\x02\x01\x01" + b"\0" * 4096 + b"payload" * 1000)
    log = map_project(ws, BigModel(1), domain_files=["orbit/core.py"])
    assert log["finish-rc"] == 0, log["finish"]
    parser_input = json.loads((ws / ".ua/tmp/extract-in-all.json").read_text())
    assert "bin/engine" not in {f["path"] for f in parser_input["batchFiles"]}
    node = next(n for n in json.loads((ws / ".ua/knowledge-graph.json").read_text())["nodes"] if n["id"] == "file:bin/engine")
    assert node["summary"].startswith("Binary file") and "ELF" in node["summary"] and "KB" in node["summary"], node


def test_a_domain_step_on_a_file_outside_the_map_is_named(tmp_path):
    """Note 9: finish names a step whose file is still on disk but now excluded, not only a deleted one."""
    ws = _ledger(tmp_path, git=False)
    assert map_project(ws, Model(1), domain_files=["ledger/store.py", "docs/guide.md"])["finish-rc"] == 0
    log = map_project(ws, Model(2), status_args=("--exclude", "docs/"))
    assert log["finish-rc"] != 0 and "docs/guide.md" in log["finish"] and "step:work:s1" in log["finish"], log["finish"]


def test_key_files_take_the_most_imported_and_skip_barrels():
    """BLOCKING 2: with more than 30 entry-point candidates (barrel index.ts files and scripts) the list still has the
    most-imported files, no barrel, at most 10 entry points and 30 files in all."""
    glue = _glue_module()
    files, texts, importers = [], {}, glue.Counter()

    def add(path, text, importer_count=0, category="code"):
        files.append({"path": path, "fileCategory": category, "sizeLines": text.count("\n")})
        texts[path] = text
        importers[path] = importer_count
    for k in range(35):                                  # barrels: tiny index.ts files that only re-export
        add(f"src/feat{k}/index.ts", f"export * from './impl';\nexport {{ thing{k} }} from './thing';\n")
    for k in range(12):                                  # real entry points of different sizes
        add(f"bin/tool{k}.js", "#!/usr/bin/env node\n" + "console.log(1);\n" * (20 + k))
    add("src/types.ts", "export interface A { a: number }\n" * 60, importer_count=59)
    add("src/store.ts", "export const store = {};\n" * 40, importer_count=27)
    add("src/I18nContext.tsx", "export const I18n = 1;\n" * 30, importer_count=22)
    for k in range(20):
        add(f"src/views/view{k}.tsx", "export const View = () => null;\n" * (30 + k))
    keys = glue.key_files(files, texts, importers)
    paths = [p for p, _ in keys]
    assert {"src/types.ts", "src/store.ts", "src/I18nContext.tsx"} <= set(paths), keys
    assert not [p for p in paths if p.endswith("/index.ts")], keys
    assert sum(1 for _, why in keys if why == "entry point") <= 10 and len(keys) <= 30
    assert paths.index("src/types.ts") < paths.index("src/store.ts")


# ---- gate 2 (09:12): the read cap, an unfinished first run without run.json, licence texts ------------------------

def test_a_text_file_past_the_read_cap_drafts_without_a_traceback(tmp_path):
    """The draft reads at most MAX_READ of a file while the parser reports every symbol's real line: a generated source
    over 1 MB with functions past the cut crashed `draft` (IndexError in comment_above). Now such a symbol simply has
    no comment above it, and the big-repo map finishes."""
    ws = tmp_path / "bigtext"
    files = {"README.md": "# Big text\n\nA project with one large generated source file.\n"}
    for k in range(160):
        files[f"pkg/m{k:03d}.py"] = f'"""Module {k}."""\n\n\ndef f{k}(x):\n    return x + {k}\n'
    files["src/generated_tables.js"] = "".join(
        f"// Computes table row {k}.\nexport function row{k}(a, b) {{\n" + "".join(f"  a = a + b * {j};\n" for j in range(10))
        + "  return a;\n}\n\n" for k in range(6000))
    assert len(files["src/generated_tables.js"].encode()) > (1 << 20)
    _write(ws, files)

    class TablesModel(Model):
        def layers_and_tour(self, ws: Path, listing: str) -> None:           # a big map: layers by folder
            (ws / ".ua/intermediate/layers.json").write_text(json.dumps([
                {"id": "layer:code", "name": "Code", "description": "The modules and the generated tables.",
                 "paths": ["pkg/", "src/"]},
                {"id": "layer:docs", "name": "Docs", "description": "The README.", "paths": ["README.md"]}]))
            (ws / ".ua/intermediate/tour.json").write_text(json.dumps([
                {"order": 1, "title": "Start", "description": "The README.", "nodeIds": ["document:README.md"]},
                {"order": 2, "title": "Tables", "description": "The generated tables.",
                 "nodeIds": ["file:src/generated_tables.js"]}]))

    log = map_project(ws, TablesModel(1), domain_files=["README.md"])
    assert "big-repo path" in log["batch-inputs"] and "Traceback" not in log["draft"], log["draft"]
    assert log["finish-rc"] == 0, log["finish"]


def test_the_comment_lookup_stops_at_the_end_of_what_was_read():
    glue = _glue_module()
    lines = ["// a helper", "export function f() {", "}"]
    assert glue.comment_above(lines, 2, "javascript").strip() == "a helper"
    assert glue.comment_above(lines, 40_000, "javascript") == ""          # a symbol past the read cut


def test_an_unfinished_first_run_without_run_json_is_not_a_legacy_map(tmp_path, monkeypatch):
    """A first DREAM-106 run that died after step 7 leaves fingerprints.json like a pre-106 map; with run.json removed
    it read as a legacy map and so as current. Its scan-hashes.json (or finished-graph.json) still marks it."""
    ua = tmp_path / ".ua"
    (ua / "intermediate").mkdir(parents=True)
    (ua / "knowledge-graph.json").write_text('{"nodes": [], "edges": []}')
    (ua / "fingerprints.json").write_text('{"files": {"a.py": {"contentHash": "x"}}}')
    monkeypatch.chdir(tmp_path)
    assert _glue_module().legacy_state() is not None                    # a real pre-106 map: adopted
    (ua / "intermediate" / "scan-hashes.json").write_text("{}")
    assert _glue_module().legacy_state() is None                        # an unfinished DREAM-106 run: never legacy
    (ua / "intermediate" / "scan-hashes.json").unlink()
    (ua / "intermediate" / "finished-graph.json").write_text("{}")
    assert _glue_module().legacy_state() is None


def test_licence_and_notice_texts_are_never_key_files():
    glue = _glue_module()
    body = "\n".join(f"line {i} of real code" for i in range(40))
    files = [{"path": p, "fileCategory": "code", "language": lang, "sizeLines": 400}
             for p, lang in (("vendor/utilsBundle.js.LICENSE", "javascript"), ("COPYING.LGPLv2.1", "1"),
                             ("third_party/NOTICE", "text"), ("LICENSE", "text"), ("app/core.py", "python"))]
    texts = {f["path"]: body for f in files}
    importers = {f["path"]: 0 for f in files}
    keys = dict(glue.key_files(files, texts, importers))
    assert list(keys) == ["app/core.py"], keys
