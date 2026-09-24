"""DREAM-103: the Understand-Anything sibling skills in Dream.

Owner, 2026-09-23, after the Understand map run: "i want the skills and tool calls to work properly ... prob need to make
fix to all unless the one just utilizes the others". The map (DREAM-102's curated `understand`) uses none of the plugin's
eight siblings; each is its own command. Two cannot work in Dream as written: `understand-dashboard` resolves the plugin
through shell paths the sandbox cannot see and launches a web server, although Dream already shows the map in its
Understand dock (sidebar "Understand ⌬", DREAM-087); `understand-domain` resolves PLUGIN_ROOT through shell candidates,
runs its script by a relative path and hands the analysis to a subagent. Dream ships curated replacements under those
two bare names; the plugin's copies stay reachable as `understand-anything:<name>`. The other six stay upstream and are
proved here.

Pinned: (1) the two packages and their texts; (2) a dry run of `understand-domain` through `policy.decide` and the real
sandboxed `ua_run` -- a project without a map (the plugin's extract-domain-context.py), a project with one (derived),
and broken domain.json files named by the glue and never saved; (3) name precedence and the phrase table; (4) the six
upstream siblings: explicit selection, whole at a 32k window, no plugin paths in the read-only four, and the
knowledge/figma script calls through the trusted-runner policy (parse-knowledge-base.py runs for real).
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from unittest import mock

import pytest

import dream.config as config
from dream import plugins
from dream.skills import loader
from dream.skills.selection import guidance_budget, select_for_task
from dream.tools import installed_skill_tools

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
DASHBOARD, DOMAIN = SKILLS / "understand-dashboard", SKILLS / "understand-domain"
GLUE = SKILLS / "understand" / "glue.py"
UA_TOOL = ROOT / "plugins" / "understand-anything" / "tools" / "ua_run.py"
UA_PLUGIN = Path(os.environ.get("UA_DIR", str(Path.home() / ".understand-anything" / "repo"))).expanduser() / "understand-anything-plugin"
UA_SKILLS = UA_PLUGIN / "skills"
CORE_SCHEMA = UA_PLUGIN / "packages" / "core" / "dist" / "schema.js"
UPSTREAM_SIX = ("understand-chat", "understand-diff", "understand-explain", "understand-onboard",
                "understand-knowledge", "understand-figma")
READ_ONLY_FOUR = UPSTREAM_SIX[:4]
# criterion (1): what neither curated text may contain -- plugin-path resolution, the web-server toolchain, a trip into the
# plugin folder, or handing the work to another agent
FORBIDDEN = re.compile(r"PLUGIN_ROOT|SKILL_DIR|\bpnpm\b|\bnpx\b|\bvite\b|understand-anything-plugin|~/\.understand-anything"
                       r"|\bsubagents?\b|\bdispatch|domain-analyzer|\bagents/|\btask\(", re.I)
INTO_PLUGIN = re.compile(r"\b(?:cd|ls|find)\b[^\n]*(?:\.understand-anything|understand-anything-plugin|SKILL_DIR|PLUGIN_ROOT|plugins/)")


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


def _body(text: str) -> str:
    return text.split("---", 2)[2]


@pytest.fixture(scope="module")
def curated():
    skills, warnings = loader.discover(config.bundled_skill_dirs())
    assert not warnings, warnings
    return skills


@pytest.fixture(scope="module")
def glue():
    return _module(GLUE, "ua_glue_for_siblings")


@pytest.fixture
def with_plugin(monkeypatch):
    if not (ROOT / "plugins" / "understand-anything" / "skills" / "understand-domain" / "SKILL.md").is_file():
        pytest.skip("the Understand-Anything plugin's skills are not installed (plugins/understand-anything/skills)")
    monkeypatch.delenv("DREAM_SKILL_DIRS", raising=False)
    monkeypatch.delenv("DREAM_EXTERNAL_SKILLS", raising=False)
    monkeypatch.setattr(installed_skill_tools, "_CACHE", None)
    plugins.load(ROOT / "plugins")                                     # conftest hands the roster back afterwards
    assert any(p.name == "understand-anything" and p.enabled for p in plugins.loaded())
    return installed_skill_tools.installed(refresh=True)


# ---- (1) the two curated packages ------------------------------------------------------------------------------------

@pytest.mark.parametrize("name, limit", [("understand-dashboard", 2000), ("understand-domain", 5000)])
def test_both_ship_as_small_curated_packages_registered_like_understand(curated, name, limit):
    by_name = {s.name: s for s in curated}
    assert name in config.CURATED_SKILLS and name in by_name
    skill = by_name[name]
    assert skill.curated and skill.root == SKILLS / name and skill.provenance == "dream-owned"
    assert skill.description.startswith("Use when")
    text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    assert len(text) <= limit, len(text)
    manifest = json.loads((SKILLS / name / "manifest.json").read_text())
    assert manifest["format"] == "dream-skill/v1" and manifest["name"] == name and manifest["portable"] is True
    assert manifest["capabilities"] and "list_dir" in manifest["capabilities"]
    wheel = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert f"skills/{name}" in wheel["only-include"]
    # the glue is the understand skill's: neither package bundles a second copy that could drift
    assert set(loader.bundled_names(skill)) == {"manifest.json"}


EXTENSIONS_DOC = ROOT / "docs" / "extensions.md"


@pytest.mark.skipif(not EXTENSIONS_DOC.is_file(), reason="docs/extensions.md is not in this checkout (dream-sync does not publish docs/)")
@pytest.mark.parametrize("name", ["understand-dashboard", "understand-domain"])
def test_the_extensions_doc_names_both(name):
    text = EXTENSIONS_DOC.read_text()
    assert f"**{name}**" in text or f"`{name}`" in text


@pytest.mark.parametrize("name", ["understand-dashboard", "understand-domain"])
def test_neither_text_resolves_the_plugin_launches_a_toolchain_or_hands_off_the_work(name):
    text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    assert not FORBIDDEN.search(text), FORBIDDEN.search(text)
    assert not INTO_PLUGIN.search(text), INTO_PLUGIN.search(text)
    assert not re.search(r"wait for (?:the user'?s? )?confirmation|confirm to continue", text, re.I)


def test_the_dashboard_is_a_pointer_to_the_dock_that_launches_nothing():
    body = _body((DASHBOARD / "SKILL.md").read_text(encoding="utf-8"))
    for needed in ("Understand ⌬", ".ua/knowledge-graph.json", "domain-graph.json", "list_dir", "map this repo",
                   "Ask for a map of this project"):
        assert needed in body, needed
    # nothing to run: no command, runtime, runner or address anywhere in it
    assert not re.search(r"\b(?:npm|node|python3?|run_bash|ua_run|bash|localhost)\b|127\.0\.0\.1|https?://|--host", body), body
    # every sentence that mentions launching, starting, serving, running or installing says not to
    for sentence in re.split(r"(?<=[.!?:;])\s+|\n", body):
        if re.search(r"\b(?:launch|start|serv(?:e|er|ing)|run|install)\w*", sentence, re.I):
            assert re.search(r"\b(?:never|not|nothing|no)\b", sentence, re.I), sentence
    # the labels it names are the ones Dream shows: the sidebar entry and the dock's button
    assert "['understand','⌬','Understand']" in (ROOT / "dream" / "gui" / "static" / "workspace.js").read_text()
    assert ">Ask for a map of this project<" in (ROOT / "dream" / "gui" / "static" / "understand.js").read_text()


def test_the_domain_skill_prescribes_the_runner_call_the_glue_and_the_shape():
    body = _body((DOMAIN / "SKILL.md").read_text(encoding="utf-8"))
    assert 'ua_run(skill="understand-domain", script="extract-domain-context.py", args=[ROOT], cwd=ROOT)' in body
    for needed in (".ua/knowledge-graph.json", ".ua/intermediate/domain-context.json", ".ua/intermediate/domain.json",
                   ".ua/domain-graph.json", "copy_files", "<dir>/../understand/glue.py", ".ua/tmp/ua_glue.py",
                   "`domain-context`", "`domain`", "contains_flow", "flow_step", "cross_domain", "filePath",
                   "entryType", "problems: 0", "Understand ⌬"):
        assert needed in body, needed
    steps = re.findall(r"^## (\d+)\. ", body, re.M)
    assert steps == [str(i) for i in range(1, len(steps) + 1)] and len(steps) >= 4, steps
    assert "yourself" in body                                          # the lead writes the graph; no hand-off
    # the report says where to look, and that without a map the Domain view does not appear yet (coordinator follow-up)
    report = body.split("## %s. " % steps[-1], 1)[1]
    assert "Understand ⌬" in report and "`.ua/knowledge-graph.json` does not exist" in report, report
    assert "Domain view appears only once a map exists" in report and '"map this repo"' in report, report


# ---- (2) the understand-domain dry run -------------------------------------------------------------------------------

def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _session(workspace: Path):
    from dream.tools.context import ToolContext, bind_context
    return bind_context(ToolContext(store=None, working=None, browser=None, session_id="ua-siblings",  # type: ignore[arg-type]
                                    workspace=Path(workspace)))


def _ua_call(tool, call, workspace):
    """One real ua_run call in a session whose workspace is `workspace` (the sandbox boundary)."""
    with _session(workspace):
        return asyncio.run(tool.ua_run.handler(call))


def _ua(tool, skill, script, args, cwd):
    """The real ua_run, called the way the skill text prescribes (absolute paths, cwd=ROOT) -- and only after the
    trusted-runner policy has let exactly that call through in auto mode."""
    from dream.core import policy
    call = {"skill": skill, "script": script, "args": [str(a) for a in args], "cwd": str(cwd)}
    with mock.patch.object(policy, "runner_trusted", lambda name: True):
        assert policy.decide("ua_run", call, "auto", Path(cwd)) == ("allow", "trusted runner inside workspace"), call
    result = _ua_call(tool, call, Path(cwd))
    text = result["content"][0]["text"]
    assert not result.get("is_error"), text
    return text


def _copy_glue(root: Path) -> str:
    """Step 1 as the skill text writes it: copy_files from `<dir>/../understand/glue.py`, `<dir>` being what skill_open
    prints for understand-domain -- allowed without a prompt, then done by the real copy_files."""
    from dream.core import policy
    from dream.tools import files as file_tools
    call = {"files": [{"src": f"{DOMAIN.resolve()}/../understand/glue.py", "dest": ".ua/tmp/ua_glue.py"}]}
    assert policy.decide("copy_files", call, "auto", root) == ("allow", "bundled skill file into the workspace")
    with _session(root):
        result = asyncio.run(file_tools.copy_files.handler(call))
    assert not result.get("is_error"), result
    assert (root / ".ua/tmp/ua_glue.py").read_bytes() == GLUE.read_bytes()
    return result["content"][0]["text"]


def _glue_rc(root: Path, *args: str):
    run = subprocess.run([sys.executable, str(root / ".ua" / "tmp" / "ua_glue.py"), *args], cwd=root,
                         capture_output=True, text=True, timeout=60)
    return run.returncode, run.stdout, run.stderr


def _glue(root: Path, *args: str) -> str:
    rc, out, err = _glue_rc(root, *args)
    assert rc == 0, f"glue {args}: exit {rc}\n{out}\n{err}"
    return out


SHOP = {
    "README.md": "# Shopline\n\nA small order service: customers place orders over HTTP and invoices are issued nightly.\n",
    "pyproject.toml": '[project]\nname = "shopline"\nversion = "0.1.0"\ndescription = "Orders and invoices."\ndependencies = ["flask"]\n',
    "shopline/__init__.py": "",
    "shopline/api.py": (
        "from flask import Flask, request\n\nfrom .orders import place_order\n\napp = Flask(__name__)\n\n\n"
        "@app.post('/orders')\ndef create_order():\n    body = request.get_json()\n"
        "    return place_order(body['customer'], body['items'])\n"),
    "shopline/orders.py": (
        "from . import store\n\n\ndef validate(customer, items):\n    if not customer or not items:\n"
        "        raise ValueError('customer and items required')\n\n\ndef place_order(customer, items):\n"
        "    validate(customer, items)\n    order = {'customer': customer, 'items': items, 'status': 'placed'}\n"
        "    store.save(order)\n    return order\n"),
    "shopline/store.py": "ORDERS = []\n\n\ndef save(order):\n    ORDERS.append(order)\n\n\ndef all_orders():\n    return list(ORDERS)\n",
    "shopline/billing.py": (
        "from .store import all_orders\n\n\ndef issue_invoices():\n    invoices = []\n    for order in all_orders():\n"
        "        invoices.append({'customer': order['customer'], 'lines': len(order['items'])})\n    return invoices\n"),
    "shopline/cli.py": (
        "import argparse\n\nfrom .billing import issue_invoices\n\n\ndef main(argv=None):\n"
        "    parser = argparse.ArgumentParser(prog='shopline')\n    sub = parser.add_subparsers(dest='command')\n"
        "    sub.add_parser('invoice')\n    args = parser.parse_args(argv)\n    if args.command == 'invoice':\n"
        "        print(issue_invoices())\n"),
    "tests/test_orders.py": "from shopline.orders import place_order\n\n\ndef test_place():\n    assert place_order('a', [1])['status'] == 'placed'\n",
    # Dream's own state in the workspace: the plugin's scan does not skip it, the listing must
    ".dream/cache.py": "SESSION = 'private'\n",
}


def _domain_json() -> dict:
    """Step 3, hand-written the way the skill text says: domains -> flows (entryPoint/entryType) -> ordered steps with
    the file that does each, and the three edge kinds."""
    def node(nid, ntype, name, summary, tags, **extra):
        return {"id": nid, "type": ntype, "name": name, "summary": summary, "tags": tags, "complexity": "simple", **extra}
    nodes = [
        node("domain:ordering", "domain", "Ordering", "Customers place orders that are validated and stored.",
             ["orders", "customers"], domainMeta={"entities": ["Order"], "businessRules": ["an order needs items"]}),
        node("domain:billing", "domain", "Billing", "Invoices are issued from the stored orders.", ["invoices"],
             domainMeta={"entities": ["Invoice"]}),
        node("flow:place-order", "flow", "Place an order", "An HTTP request becomes a stored order.", ["http", "orders"],
             domainMeta={"entryPoint": "POST /orders", "entryType": "http"}),
        node("flow:issue-invoices", "flow", "Issue invoices", "The CLI turns every stored order into an invoice.",
             ["cli", "invoices"], domainMeta={"entryPoint": "shopline invoice", "entryType": "cli"}),
        node("step:place-order:receive-request", "step", "Receive the request", "Reads the customer and items.",
             ["http"], filePath="shopline/api.py", lineRange=[8, 11]),
        node("step:place-order:validate", "step", "Validate the order", "Rejects an order without customer or items.",
             ["validation"], filePath="shopline/orders.py", lineRange=[4, 6]),
        node("step:place-order:save", "step", "Save the order", "Appends the order to the store.", ["storage"],
             filePath="shopline/store.py"),
        node("step:issue-invoices:collect", "step", "Collect orders", "Reads every stored order.", ["storage"],
             filePath="shopline/billing.py"),
        node("step:issue-invoices:write", "step", "Write invoices", "One invoice line count per order.", ["invoices"],
             filePath="shopline/billing.py"),
    ]
    edge = lambda s, t, kind, w, **x: {"source": s, "target": t, "type": kind, "direction": "forward", "weight": w, **x}
    edges = [
        edge("domain:ordering", "flow:place-order", "contains_flow", 1.0),
        edge("domain:billing", "flow:issue-invoices", "contains_flow", 1.0),
        edge("flow:place-order", "step:place-order:receive-request", "flow_step", 0.1),
        edge("flow:place-order", "step:place-order:validate", "flow_step", 0.2),
        edge("flow:place-order", "step:place-order:save", "flow_step", 0.3),
        edge("flow:issue-invoices", "step:issue-invoices:collect", "flow_step", 0.1),
        edge("flow:issue-invoices", "step:issue-invoices:write", "flow_step", 0.2),
        edge("domain:billing", "domain:ordering", "cross_domain", 0.6, description="invoices read the stored orders"),
    ]
    return {"nodes": nodes, "edges": edges}


def _fixture_repo(root: Path) -> str:
    for rel, text in SHOP.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    return _git(root, "rev-parse", "HEAD")


needs_domain_script = pytest.mark.skipif(
    not UA_TOOL.is_file() or not (UA_SKILLS / "understand-domain" / "extract-domain-context.py").is_file()
    or shutil.which("python3") is None or shutil.which("bwrap") is None,
    reason="needs the ua_run plugin tool, the Understand-Anything clone (understand-domain/extract-domain-context.py), "
           "python3 and bubblewrap")


@pytest.fixture(scope="module")
def scanned(tmp_path_factory):
    """Steps 1-4 on a project with no map: the glue copied in, the plugin's scan through the real sandboxed ua_run, the
    listing, the model's domain.json and project.json (hand-written), the glue's build."""
    if not UA_TOOL.is_file() or not (UA_SKILLS / "understand-domain" / "extract-domain-context.py").is_file() or (
            shutil.which("python3") is None or shutil.which("bwrap") is None):
        pytest.skip("needs the ua_run plugin tool, the Understand-Anything clone, python3 and bubblewrap")
    root = tmp_path_factory.mktemp("shopline")
    head = _fixture_repo(root)
    tool = _module(UA_TOOL, "ua_run_for_siblings")
    copied = _copy_glue(root)                                                          # step 1
    assert not (root / ".ua/knowledge-graph.json").exists()                            # step 2: no map -> the scan
    scan = _ua(tool, "understand-domain", "extract-domain-context.py", [root], root)
    context = _glue(root, "domain-context")
    (root / ".ua/intermediate/domain.json").write_text(json.dumps(_domain_json(), indent=1))   # step 3
    (root / ".ua/tmp/project.json").write_text(json.dumps(
        {"name": "shopline", "description": "Orders over HTTP and nightly invoices.", "frameworks": ["Flask"]}))
    rc, built, err = _glue_rc(root, "domain")                                         # step 4
    return {"root": root, "head": head, "copied": copied, "scan": scan, "context": context, "rc": rc, "built": built,
            "err": err, "tool": tool}


@needs_domain_script
def test_the_scan_runs_in_the_sandbox_and_the_listing_names_the_entry_points(scanned):
    root = scanned["root"]
    ctx = json.loads((root / ".ua/intermediate/domain-context.json").read_text())
    assert ctx["projectRoot"] == str(root) and ctx["fileCount"] >= 7
    text = scanned["context"]
    assert "source: the scan" in text
    assert "/orders" in text and "shopline/api.py" in text and "invoice" in text          # the route and the CLI command
    assert "place_order" in text and "issue_invoices" in text                             # file signatures
    assert "Shopline" in text                                                             # the README
    assert ".dream/" not in text                                                          # Dream's own state is not the project


@needs_domain_script
def test_the_glue_builds_a_domain_graph_the_dashboard_can_load(scanned, glue):
    from dream.gui import understand_routes
    root = scanned["root"]
    assert scanned["rc"] == 0, (scanned["built"], scanned["err"])
    assert "problems: 0" in scanned["built"] and "Traceback" not in scanned["err"]
    assert not (root / ".ua/knowledge-graph.json").exists()                               # no map was needed
    dg = json.loads((root / ".ua/domain-graph.json").read_text())
    assert dg["version"] == "1.0.0" and dg["layers"] == [] and dg["tour"] == []
    project = dg["project"]
    assert project["name"] == "shopline" and project["frameworks"] == ["Flask"] and project["languages"] == ["python"]
    assert project["gitCommitHash"] == scanned["head"] and re.match(r"\d{4}-\d\d-\d\dT", project["analyzedAt"])
    assert {n["type"] for n in dg["nodes"]} == {"domain", "flow", "step"}
    assert {e["type"] for e in dg["edges"]} == {"contains_flow", "flow_step", "cross_domain"}
    assert glue.check_graph(dg, glue.DOMAIN_NODES, glue.DOMAIN_EDGES, layered=False) == []   # finish's own check
    assert re.search(r"9 nodes \(", scanned["built"]) and "domains: Ordering, Billing" in scanned["built"]
    served = understand_routes.load_graph(root, "domain-graph.json")                     # what /domain-graph.json serves
    assert len(served["nodes"]) == 9 and all(not Path(n.get("filePath", "x")).is_absolute() for n in served["nodes"])


@needs_domain_script
def test_the_dashboards_own_validator_keeps_every_node_and_edge(scanned):
    """The dashboard runs the plugin core's validateGraph on /domain-graph.json and silently drops what fails it."""
    node = shutil.which("node")
    if node is None or not CORE_SCHEMA.is_file():
        pytest.skip("needs node and the plugin's built core (packages/core/dist/schema.js)")
    script = ("import(process.argv[1]).then(m => { const fs = require('fs'); const r = m.validateGraph("
              "JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))); console.log(JSON.stringify({success: r.success, "
              "fatal: r.fatal || null, nodes: r.data ? r.data.nodes.length : 0, edges: r.data ? r.data.edges.length : 0, "
              "issues: r.issues.map(i => i.level + ': ' + i.message)})); })")
    run = subprocess.run([node, "-e", script, str(CORE_SCHEMA), str(scanned["root"] / ".ua/domain-graph.json")],
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["success"] and result["fatal"] is None, result
    assert result["nodes"] == 9 and result["edges"] == 8, result
    assert not [i for i in result["issues"] if i.startswith("dropped")], result


def _no_domains(d):
    """Only flows and steps: the Domain view's overview lists domain nodes, so it would show nothing."""
    d["nodes"] = [n for n in d["nodes"] if n["type"] != "domain"]
    d["edges"] = [e for e in d["edges"] if e["type"] == "flow_step"]


def _orphan_flow(d):
    """A flow no domain contains, with its own step: both are well-formed and both would be invisible."""
    node = {"type": "flow", "name": "Ship an order", "summary": "Packs the order.", "tags": ["shipping"], "complexity": "simple"}
    d["nodes"].append({"id": "flow:ship-order", **node, "domainMeta": {"entryPoint": "ship", "entryType": "manual"}})
    d["nodes"].append({"id": "step:ship-order:pack", **node, "type": "step", "name": "Pack", "filePath": "shopline/orders.py"})
    d["edges"].append({"source": "flow:ship-order", "target": "step:ship-order:pack", "type": "flow_step",
                       "direction": "forward", "weight": 0.1})


def _flow_without_steps(d):
    """A flow its domain contains but that lists no steps: the Domain view would show an empty flow."""
    d["nodes"].append({"id": "flow:refund-order", "type": "flow", "name": "Refund an order", "summary": "Returns the money.",
                       "tags": ["refunds"], "complexity": "simple", "domainMeta": {"entryPoint": "refund", "entryType": "manual"}})
    d["edges"].append({"source": "domain:billing", "target": "flow:refund-order", "type": "contains_flow",
                       "direction": "forward", "weight": 1.0})


@pytest.mark.parametrize("change, expected", [
    (lambda d: d["nodes"][2].__setitem__("type", "process"), "type 'process' is not in the schema"),
    (lambda d: d["edges"].append({"source": "flow:place-order", "target": "step:place-order:ship", "type": "flow_step",
                                  "direction": "forward", "weight": 0.4}), "step:place-order:ship is not a node"),
    (lambda d: d["nodes"][6].pop("filePath"), "step:place-order:save lacks filePath"),
    (lambda d: d["nodes"][6].__setitem__("filePath", "shopline/shipping.py"), "not a file in the project"),
    (lambda d: d["nodes"][6].__setitem__("filePath", "/etc/hostname"), "relative"),
    (lambda d: d["edges"].pop(4), "step:place-order:save has no flow_step edge into it"),
    (lambda d: d["edges"].pop(0), "flow:place-order has no contains_flow edge into it"),
    (lambda d: d["nodes"][2]["domainMeta"].__setitem__("entryType", "webhook"), "entryType"),
    (lambda d: d["nodes"][4].__setitem__("lineRange", [8]), "lineRange"),
    (lambda d: d["edges"][2].__setitem__("type", "calls"), "type 'calls' is not in the schema"),
    (lambda d: d["nodes"][0].__setitem__("name", 7), "name must be text"),
    (lambda d: d["nodes"][0]["domainMeta"].__setitem__("entities", "Order"), "domainMeta.entities must be a list"),
    # gate (blocking 3): what the dashboard's DomainGraphView cannot show -- no domain at all, an edge between the wrong
    # kinds of node, a flow no domain contains or a step no such flow lists (DomainGraphView.tsx buildDomainDetail)
    (_no_domains, "no domain node"),
    (lambda d: d["edges"][0].__setitem__("source", "step:place-order:save"), "contains_flow must go domain -> flow"),
    (lambda d: d["edges"][2].__setitem__("source", "domain:ordering"), "flow_step must go flow -> step"),
    (lambda d: d["edges"][7].__setitem__("target", "flow:place-order"), "cross_domain must go domain -> domain"),
    (_orphan_flow, "flow:ship-order has no contains_flow edge into it from a domain"),
    (_orphan_flow, "step:ship-order:pack has no flow_step edge into it from a flow under a domain"),
    # gate notes 4 and 5: a non-finite number anywhere; a step pointing into tool data rather than the project's code
    (lambda d: d["nodes"][4].__setitem__("lineRange", [float("nan"), 11]), "nodes[4].lineRange[0] is not a finite number"),
    (lambda d: d["edges"][2].__setitem__("weight", float("inf")), "edges[2].weight is not a finite number"),
    (lambda d: d["nodes"][6].__setitem__("filePath", ".git/config"), "is inside .git/"),
    (lambda d: d["nodes"][6].__setitem__("filePath", ".ua/knowledge-graph.json"), "is inside .ua/"),
    (lambda d: d["nodes"][6].__setitem__("filePath", "./.dream/state.json"), "is inside .dream/"),
    (lambda d: d["nodes"][6].__setitem__("filePath", ".remember/notes.txt"), "is inside .remember/"),
    # re-gate note 7: a line range that cannot be real, a blank summary, a domain linked to itself, a flow with no steps
    (lambda d: d["nodes"][4].__setitem__("lineRange", [0, 11]), "lineRange must be [start, end] with 1 <= start <= end"),
    (lambda d: d["nodes"][4].__setitem__("lineRange", [11, 8]), "lineRange must be [start, end] with 1 <= start <= end"),
    (lambda d: d["nodes"][0].__setitem__("summary", "   "), "domain:ordering: summary is blank"),
    (lambda d: d["edges"][7].__setitem__("target", "domain:billing"), "cross_domain edge must link two different domains"),
    (_flow_without_steps, "flow:refund-order has no flow_step edge out of it"),
])
def test_a_broken_domain_json_is_named_and_never_saved(tmp_path, change, expected):
    """Unknown type, dangling edge, a step without a real filePath, a broken hierarchy, a value the dashboard would
    silently drop: each is a named problem, a non-zero exit, and the saved graph is left as it was."""
    root = tmp_path / "p"
    for rel in ("shopline/api.py", "shopline/orders.py", "shopline/store.py", "shopline/billing.py"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(SHOP[rel])
    (root / ".ua/tmp").mkdir(parents=True)
    (root / ".ua/intermediate").mkdir(parents=True)
    shutil.copy(GLUE, root / ".ua/tmp/ua_glue.py")
    good = _domain_json()
    (root / ".ua/intermediate/domain.json").write_text(json.dumps(good))
    assert "problems: 0" in _glue(root, "domain")
    saved = (root / ".ua/domain-graph.json").read_bytes()
    broken = json.loads(json.dumps(good))
    change(broken)
    (root / ".ua/intermediate/domain.json").write_text(json.dumps(broken))
    rc, out, err = _glue_rc(root, "domain")
    assert rc != 0 and "Traceback" not in err, (out, err)
    assert expected in out and "problems: 0" not in out, out
    assert (root / ".ua/domain-graph.json").read_bytes() == saved                          # not silently saved
    assert "not written" in out


def test_the_domain_glue_names_missing_inputs_instead_of_a_traceback(tmp_path):
    root = tmp_path / "p"
    (root / ".ua/tmp").mkdir(parents=True)
    shutil.copy(GLUE, root / ".ua/tmp/ua_glue.py")
    rc, out, err = _glue_rc(root, "domain-context")                                       # neither the map nor the scan
    assert rc != 0 and "Traceback" not in err
    assert 'ua_run(skill="understand-domain", script="extract-domain-context.py"' in out, out
    rc, out, err = _glue_rc(root, "domain")                                               # no domain.json yet
    assert rc != 0 and "Traceback" not in err and ".ua/intermediate/domain.json" in out, out
    (root / ".ua/intermediate").mkdir()
    (root / ".ua/intermediate/domain.json").write_text('{"nodes": [')                     # half-written
    rc, out, err = _glue_rc(root, "domain")
    assert rc != 0 and "Traceback" not in err and "problem" in out, out
    (root / ".ua/intermediate/domain.json").write_text('[1, 2]')                          # not the shape
    rc, out, err = _glue_rc(root, "domain")
    assert rc != 0 and "Traceback" not in err and '"nodes"' in out, out
    (root / ".understand-anything").mkdir()                                               # the plugin's legacy folder
    rc, out, _ = _glue_rc(root, "domain-context")
    assert rc != 0 and "legacy" in out.lower()


MAPPED_KG = {
    "version": "1.0.0",
    "project": {"name": "shopline-mapped", "languages": ["python"], "frameworks": ["Flask"],
                "description": "Orders over HTTP and nightly invoices.", "analyzedAt": "2026-09-23T00:00:00Z",
                "gitCommitHash": "abc123"},
    "nodes": [
        {"id": "file:shopline/api.py", "type": "file", "name": "api.py", "filePath": "shopline/api.py",
         "summary": "Flask app whose POST /orders route places an order.", "tags": ["http"], "complexity": "simple"},
        {"id": "function:shopline/api.py:create_order", "type": "function", "name": "create_order",
         "filePath": "shopline/api.py", "lineRange": [8, 11], "summary": "The POST /orders handler.", "tags": ["http"],
         "complexity": "simple"},
        {"id": "file:shopline/orders.py", "type": "file", "name": "orders.py", "filePath": "shopline/orders.py",
         "summary": "Validates and stores orders.", "tags": ["orders"], "complexity": "simple"},
        {"id": "function:shopline/orders.py:place_order", "type": "function", "name": "place_order",
         "filePath": "shopline/orders.py", "lineRange": [9, 13], "summary": "Validates, builds and saves an order.",
         "tags": ["orders"], "complexity": "simple"},
        {"id": "file:shopline/store.py", "type": "file", "name": "store.py", "filePath": "shopline/store.py",
         "summary": "In-memory order store.", "tags": ["storage"], "complexity": "simple"},
        {"id": "file:shopline/billing.py", "type": "file", "name": "billing.py", "filePath": "shopline/billing.py",
         "summary": "Turns stored orders into invoices.", "tags": ["invoices"], "complexity": "simple"},
    ],
    "edges": [
        {"source": "file:shopline/api.py", "target": "file:shopline/orders.py", "type": "imports", "direction": "forward", "weight": 0.7},
        {"source": "function:shopline/api.py:create_order", "target": "function:shopline/orders.py:place_order",
         "type": "calls", "direction": "forward", "weight": 0.8},
        {"source": "file:shopline/billing.py", "target": "file:shopline/store.py", "type": "imports", "direction": "forward", "weight": 0.7},
    ],
    "layers": [{"id": "layer:api", "name": "API", "description": "HTTP entry points.", "nodeIds": ["file:shopline/api.py"]},
               {"id": "layer:core", "name": "Core", "description": "Orders, storage and billing.",
                "nodeIds": ["file:shopline/orders.py", "file:shopline/store.py", "file:shopline/billing.py"]}],
    "tour": [{"order": 1, "title": "Entry point", "description": "Start at the route.", "nodeIds": ["file:shopline/api.py"]}],
}


def test_with_a_map_the_domain_graph_is_derived_from_it(tmp_path, glue):
    """The map exists: no scan (step 2 skips it), the listing comes from the map, the graph carries the map's project."""
    root = tmp_path / "mapped"
    for rel in ("shopline/api.py", "shopline/orders.py", "shopline/store.py", "shopline/billing.py"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(SHOP[rel])
    (root / ".ua/tmp").mkdir(parents=True)
    (root / ".ua/knowledge-graph.json").write_text(json.dumps(MAPPED_KG))
    shutil.copy(GLUE, root / ".ua/tmp/ua_glue.py")
    text = _glue(root, "domain-context")
    assert "source: the map" in text and "shopline-mapped" in text
    assert "API" in text and "Core" in text                                               # the layers
    assert "POST /orders route places an order" in text                                   # file summaries
    assert "create_order" in text and "place_order" in text                               # symbols per file
    assert "imports" in text or "calls" in text                                           # how the files connect
    assert not (root / ".ua/intermediate/domain-context.json").exists()
    (root / ".ua/intermediate").mkdir(exist_ok=True)
    (root / ".ua/intermediate/domain.json").write_text(json.dumps(_domain_json()))
    out = _glue(root, "domain")
    assert "problems: 0" in out
    dg = json.loads((root / ".ua/domain-graph.json").read_text())
    assert dg["project"] == MAPPED_KG["project"]                                          # the map's own project data
    assert glue.check_graph(dg, glue.DOMAIN_NODES, glue.DOMAIN_EDGES, layered=False) == []
    assert json.loads((root / ".ua/knowledge-graph.json").read_text()) == MAPPED_KG       # the map is untouched


def test_the_glue_keeps_every_existing_subcommand(glue):
    for command in ("imports-input", "scan-result", "batch-inputs", "structure", "nodes", "assemble", "finish",
                    "domain-context", "domain"):
        assert command in glue.__doc__, command
    assert glue.main([]) == 2 and glue.main(["no-such-command"]) == 2


# ---- (3) names and selection -----------------------------------------------------------------------------------------

def test_the_curated_skills_win_the_bare_names_and_the_plugin_copies_stay_reachable(with_plugin):
    by_name = {s.name: s for s in with_plugin}
    for name, ours, theirs in (("understand-dashboard", "Understand ⌬", "PLUGIN_ROOT"),
                               ("understand-domain", "ua_glue.py", "PLUGIN_ROOT")):
        assert by_name[name].curated and by_name[name].root == SKILLS / name
        alias = by_name[f"understand-anything:{name}"]
        assert not alias.curated and alias.provenance == "dream-plugin"
        assert alias.root.resolve() == (UA_SKILLS / name).resolve()
        assert any(f"reachable as 'understand-anything:{name}'" in w for w in installed_skill_tools.warnings())
        # the qualified name selects only the plugin's copy; the bare name only ours
        assert select_for_task(f"$understand-anything:{name}", with_plugin).names == (f"understand-anything:{name}",)
        assert select_for_task(f"${name}", with_plugin).names == (name,)
        # skill_open reaches both
        mine = asyncio.run(installed_skill_tools.skill_open.handler({"name": name}))
        plugin = asyncio.run(installed_skill_tools.skill_open.handler({"name": f"understand-anything:{name}"}))
        assert not mine.get("is_error") and ours in mine["content"][0]["text"]
        assert not plugin.get("is_error") and theirs in plugin["content"][0]["text"]
    for sibling in UPSTREAM_SIX:                                                          # untouched, under their own names
        assert sibling in by_name and by_name[sibling].provenance == "dream-plugin", sibling
        assert f"understand-anything:{sibling}" not in by_name


# The gate's principle (coordinator, 2026-09-23): the implicit rules of the three Understand skills are ALLOWLISTS. A
# missed implicit match is cheap -- the owner can name the skill, and the dock's own request does -- while a false one
# turns a bug fix into a map or domain build. So: a request (or clause) that starts with the verb, optionally after
# "please" / "can|could|would|will you"; the target phrase; then only an allowed follower before the end or clause
# punctuation. A question never selects one implicitly. Explicit selection ($name, "use the X skill", /understand) is
# unchanged.
UNDERSTAND_FAMILY = {"understand", "understand-dashboard", "understand-domain"}
ASK = ("Use the understand skill (Understand-Anything) to map this project into .ua/knowledge-graph.json, then tell me "
       "when the map is ready. Its helper scripts run through the ua_run tool (the plugin is installed and built; skip "
       "locating it). Exclude Dream's own state folders: pass --exclude \".dream/**,.remember/**\" to the scan.")


@pytest.mark.parametrize("prompt", [
    "$understand-dashboard", "use the understand-dashboard skill", "/understand-dashboard",
    # (i) a view verb and the Understand dashboard / panel / dock, the knowledge graph dashboard or the map dashboard
    "show the knowledge graph dashboard", "Open the Understand panel", "open the Understand dock",
    "Show me the understand panel", "Could you show the knowledge graph dashboard?", "Show the understand dashboard for this repo",
    # (ii) the whole request is "open / show (me) the (understand) map"
    "open the map", "Show me the map", "Open the map.", "please open the understand map",
])
def test_view_phrases_select_the_dashboard_alone(curated, prompt):
    names = select_for_task(prompt, curated).names
    assert names and names[0] == "understand-dashboard", (prompt, names)
    assert "understand" not in names and "understand-domain" not in names, (prompt, names)


@pytest.mark.parametrize("prompt", [
    "$understand-domain", "use the understand-domain skill", "/understand-domain",
    # a build verb, "(a|the) domain (graph|map|model)" or "the business (domains|flows|processes)", an allowed follower
    "Build a domain graph of this project", "Map the business domains", "Extract the business processes",
    "Generate the domain map", "Update the domain graph", "Can you build a domain graph for this repo?",
    "please map the business flows now",
])
def test_domain_phrases_select_the_domain_skill(curated, prompt):
    names = select_for_task(prompt, curated).names
    assert names and names[0] == "understand-domain", (prompt, names)
    assert "understand" not in names and "understand-dashboard" not in names, (prompt, names)


@pytest.mark.parametrize("prompt", [
    # DREAM-102's acceptance phrases (tests/test_understand_skill.py; ASK is the dock's request) and the coordinator's
    # must-map list: the map comes first, and alone of the three
    ASK, "$understand", "use the understand skill on this repository",
    "Map this repo", "map this codebase please", "Map the whole project into a knowledge graph", "understand map",
    "Run the Understand-Anything map on this project", "knowledge graph of this project",
    "Build a code map of this repository for the Understand panel", "/understand",
    "map this repo", "map the whole project into a knowledge graph", "Can you map this repo?", "Map this repo, then open the map",
    "Map out the whole codebase", "map out the entire repo",
])
def test_map_phrases_still_select_only_the_map(curated, prompt):
    names = select_for_task(prompt, curated).names
    assert names and names[0] == "understand", (prompt, names)
    assert "understand-dashboard" not in names and "understand-domain" not in names, (prompt, names)


@pytest.mark.parametrize("prompt", [
    "build the knowledge graph", "build a knowledge graph of this repo", "make a knowledge graph for this project",
    "refresh the knowledge graph", "turn this repo into a knowledge graph", "Could you build the knowledge graph?",
    "Generate a knowledge graph of this repo", "create a knowledge graph", "update the knowledge graph",
    "rebuild the project's knowledge graph", "Build a fresh knowledge graph", "Can you build a knowledge graph of this repo?",
    "Update the knowledge graph, then explain the auth module with it", "Would you please build the knowledge graph?",
    # gate note 1: an unqualified target may go on only with ", then" and a verb that reads the map
    "Build the knowledge graph, then open the map", "Build the knowledge graph for the project",
])
def test_requests_that_build_the_knowledge_graph_still_select_the_map(curated, prompt):
    names = select_for_task(prompt, curated).names
    assert names and names[0] == "understand", (prompt, names)
    assert "understand-dashboard" not in names and "understand-domain" not in names, (prompt, names)


@pytest.mark.parametrize("prompt", [
    # the gate's cases (blocking 1): coding requests that name the domain graph, the business flows or a map file
    "Fix the domain graph route in understand_routes.py so it returns 404 when the file is missing",
    "The domain graph parser throws on empty input, fix it", "Fix the crash when the domain flows list is empty",
    "Write unit tests for the domain flows", "Update the domain map serializer to handle nulls",
    "Rename the business processes enum to Workflows", "Fix the business domains dropdown",
    "Why is the domain graph empty in the dashboard?", "Fix the domain graph loading bug in understand_routes.py",
    "Trace the business flows bug in the payments service",
    "Open the map.ts file", "Show me the map.js source", "Open the map.py file and fix the bug",
    "Display the map markers correctly on mobile", "Open the map editor and fix the zoom bug",
    "See the map legend bug in issue 42", "Fix the bug: when I open the knowledge graph view it crashes",
    "Show me the domain view component",
    # the gate's cases (blocking 2): a knowledge graph as a product, a source other than this repo, or a question
    "Refresh the knowledge graph cache when the TTL expires", "Update the knowledge graph embeddings in the RAG pipeline",
    "Update the knowledge graph builder to skip node_modules", "Make the knowledge graph load faster in the dashboard",
    "Create a knowledge graph for the movie recommendation feature", "Create a knowledge graph for our docs site in Neo4j",
    "Make a knowledge graph visualization in D3", "Create a knowledge graph diagram of the auth flow",
    "Create a knowledge graph from these documents", "Build a knowledge graph from Wikipedia articles",
    "Make a knowledge graph out of my notes", "How long does it take to generate the knowledge graph?",
    "How do I build a knowledge graph of this repo?", "Is there a way to build the knowledge graph faster?",
    # gate note 8 (pre-existing in DREAM-102's rule, same class)
    "Fix the Understand panel's resize bug", "Tell me what the understand map shows about the auth layer",
    # coordinator correction before the re-gate: in a domain-driven codebase the domain model is ordinary code
    "Update the domain model", "Build the domain model", "Refactor the domain model",
])
def test_the_gate_cases_select_no_understand_skill(curated, prompt):
    names = select_for_task(prompt, curated).names
    assert not UNDERSTAND_FAMILY & set(names), (prompt, names)


@pytest.mark.parametrize("prompt", [
    # a request that USES an existing map (coordinator follow-up, round 2)
    "explain the auth module using the knowledge graph", "Explain the auth module using the knowledge graph",
    "what does the knowledge graph say about the payment flow", "use the knowledge graph to find where refunds are handled",
    "query the knowledge graph for the retry logic", "look up the session store in the knowledge graph",
    "What does the knowledge graph of this project say about auth?", "query the knowledge graph for the project's entry points",
    # a knowledge graph as a thing to code (round 3)
    "Build a knowledge graph database in Python", "create a knowledge graph library", "Make a knowledge graph API with FastAPI",
    "Generate a knowledge graph schema for Neo4j", "build a knowledge graph extractor for PDFs",
    "Create a knowledge graph class in Python", "make a knowledge graph endpoint for the search service",
    "Build a knowledge graph in Python", "create a knowledge graph with networkx", "Build a knowledge graph in C++",
    "Load the CSV into a knowledge graph database", "implement a knowledge graph class", "add a knowledge graph endpoint",
    # asking whether one exists (round 3)
    "Is there a knowledge graph for this repo?", "Is there already a knowledge graph of this codebase?",
    "Do we already have a knowledge graph of this repo?", "do we have a knowledge graph",
    "does this project have a knowledge graph yet?",
    # ordinary words
    "Open the site map", "Show the map of Europe", "Fix the bug in the project map view", "Open the dashboard",
    "Register a new domain name for the website", "Fix the bug in the business flows module", "Write a business plan",
    "What is a domain model? Explain briefly.", "Thanks, that helped.", "Add a dashboard page to the admin app",
    "Fix the crash when I update the knowledge graph", "Fix the bug that happens when we map this repo",
])
def test_use_code_questions_and_ordinary_words_select_no_understand_skill(curated, prompt):
    names = select_for_task(prompt, curated).names
    assert not UNDERSTAND_FAMILY & set(names), (prompt, names)


@pytest.mark.parametrize("prompt", [
    # anything outside the allowlists selects none of them, including phrasings that did before this gate: bare
    # "domain graph" / "business flows" (the brief's scope-3 examples), requests without a verb at their head, followers
    # the list does not know, and polite requests that start like a question
    "domain graph", "business flows", "Show me the business flows in this codebase",
    "Extract the business processes of this app into a domain graph", "Can you open the codebase map?", "show the domain graph",
    "I'd like a knowledge graph of this codebase", "Build the knowledge graph in the background",
    "Do you have time to make a knowledge graph of this repo?", "Is there any chance you could build the knowledge graph?",
    "Refresh the map", "Take me to the map", "map this codebase and its business flows",
    # re-gate: only the request's opening can make an implicit match, so a request after a preface sentence is a miss
    "The map is stale. Rebuild the knowledge graph.", "Thanks! Now map this repo.", "I added a module.\nMap this repo.",
    # a colon after the target introduces a description, as it does before one
    "Map this repo: skip the tests folder",
])
def test_anything_outside_the_allowlists_selects_no_understand_skill(curated, prompt):
    names = select_for_task(prompt, curated).names
    assert not UNDERSTAND_FAMILY & set(names), (prompt, names)


@pytest.mark.parametrize("prompt, named", [
    # a question never selects implicitly -- a clause headed by a question word and ending in "?" is enough...
    ("What happens if I map this repo?", None), ("Map this repo. Why is it so slow?", None),
    ("Where do I open the map?", None), ("How do I map the business domains?", None),
    # ...but naming the skill still works inside a question
    ("Why is the domain graph empty? $understand-domain", "understand-domain"),
    ("How do I use the understand-dashboard skill?", "understand-dashboard"),
    ("Why does the map look stale?\n/understand", "understand"),   # a slash command counts at the start of a line
])
def test_questions_never_select_implicitly_but_named_skills_still_do(curated, prompt, named):
    names = select_for_task(prompt, curated).names
    if named is None:
        assert not UNDERSTAND_FAMILY & set(names), (prompt, names)
    else:
        assert names and names[0] == named, (prompt, names)


@pytest.mark.parametrize("prompt", [
    # re-gate (blocking 1): a description inside a bug report -- a quoted step, a stage list, a button label, a checklist,
    # expected/actual, repro steps -- is not a request
    "Step 7 of the understand skill: build the knowledge graph. The glue crashes there with KeyError 'layers'. Fix it.",
    "The map run stops at step 5: assemble, then build the knowledge graph. Fix glue.py.",
    "The understand skill's last step is: refresh the knowledge graph. Make it skip .dream/.",
    "In the dock, the button says: Map this repo. Rename it to Build the map.",
    "Our pipeline: fetch the docs, update the knowledge graph, re-index. Add retries to the fetch.",
    "pipeline.py has three stages: parse the PDFs, build the knowledge graph, index it. The second stage crashes.",
    "In ingest.py, after parsing, update the knowledge graph. Right now it silently skips that. Fix it.",
    "TODO in graph.py: refresh the knowledge graph. Implement it with networkx.",
    "Here's the checklist from CONTRIBUTING.md:\n- Map the codebase\n- Write tests\nFix the typo in the second item.",
    "The README says: generate the domain graph. Update that line to say generate the domain model.",
    "Expected: open the Understand panel. Actual: nothing happens. Fix workspace.js.",
    "Repro: open the Understand dock, press Ask for a map of this project, nothing happens. Please fix.",
    "Steps to reproduce:\n1. Open the Understand panel\n2. Press Ask for a map of this project\n3. Nothing happens",
    # the same class beyond the listed cases: steps as separate sentences, a checklist without list markers, "1)" lists,
    # an example, stage numbers
    "The pipeline has three stages. Parse the PDFs. Build the knowledge graph. Index it. The second stage crashes.",
    "The checklist says:\nWrite tests\nMap the codebase\nFix the second item.",
    "1) Open the Understand panel\n2) Press the Map tab\nThe frame stays blank.",
    "Update the docs, e.g. build the knowledge graph first. The example is wrong.",
    "Stage 2. Build the knowledge graph. It crashes with KeyError.",
    "Both of these fail; open the map, then show the understand panel.",
])
def test_descriptions_inside_a_bug_report_select_no_understand_skill(curated, prompt):
    names = select_for_task(prompt, curated).names
    assert not UNDERSTAND_FAMILY & set(names), (prompt, names)


@pytest.mark.parametrize("prompt, understand_skill", [
    # re-gate (b): when coding's own action pattern matched too, an Understand skill does not displace it -- coding keeps
    # first place and the Understand skill is second, so a misfire costs a second workflow, not the turn
    # (a qualified target: gate note 1 lets only those go on with further instructions)
    ("Map this repo, then write a unit test for the parser", "understand"),
    ("Build the knowledge graph of this repo, then add an endpoint for search", "understand"),
    ("Open the Understand panel, then write the login page", "understand-dashboard"),
    ("Build the domain graph of this repo, then add a feature flag for it", "understand-domain"),
])
def test_coding_keeps_first_place_when_its_own_action_pattern_matched(curated, prompt, understand_skill):
    names = select_for_task(prompt, curated).names
    assert names[:2] == ("coding", understand_skill), (prompt, names)


@pytest.mark.parametrize("prompt", [
    # beyond the listed cases: a bug report can open with the very label it is about ("Map this repo. Nothing happens.");
    # a request that reads as a bug report -- fix, bug, crash, error, broken, fails, nothing happens, a traceback -- never
    # selects an Understand skill implicitly (naming it still does)
    "Map this repo. Nothing happens when I click it. Fix it.", "Map this repo: nothing happens",
    "Build the knowledge graph: KeyError 'layers' in glue.py", "Open the Understand panel; it crashes on load",
    "map this repo\nTraceback (most recent call last):\n  File glue.py", "Build the domain graph. It fails with KeyError.",
    "Map this repo. It doesn't work since the update.", "Refresh the knowledge graph; the last run hangs at step 5.",
    "Build the knowledge graph, then fix the search endpoint",
    # ...and without a single bug word: what follows the request is a description, not another instruction
    "Map this repo. The dock is empty.", "Map this repo; the panel stays blank afterwards",
    "Map the business domains. The Domain view is empty.", "Map this repo, then the panel stays blank",
    "Open the Understand panel. Nothing shows.", "Build the knowledge graph and it never finishes",
    # ...and a symptom carried inside an instruction-shaped step
    "Map this repo. Open the dock and it's empty.", "Map this repo. Look, the dock is empty.", "Map this repo. Check: empty.",
    "Map this repo and open the dock, it shows 0 nodes", "Refresh the knowledge graph, it's stale",
])
def test_a_request_that_reads_as_a_bug_report_selects_no_understand_skill(curated, prompt):
    names = select_for_task(prompt, curated).names
    assert not UNDERSTAND_FAMILY & set(names), (prompt, names)


@pytest.mark.parametrize("prompt", [
    # ...while a pure map request, where coding matched only on a noun ("repo", "repository"), still loads the map alone,
    # also when it goes on with further instructions
    "map this repo", "Map this repo", "map this codebase please", "Build a code map of this repository for the Understand panel",
    "Map this repo.\nSkip the tests folder.", "Map this repo and tell me when the map is ready", "Map this repo, please",
    "Map this repo. Then open the map.", "Can you map this repo? Exclude the vendor folder.",
    "Map this repo, don't include the tests", "Map this repo and explain the auth layer",
])
def test_a_pure_map_request_still_loads_only_the_map(curated, prompt):
    assert select_for_task(prompt, curated).names == ("understand",), prompt


@pytest.mark.parametrize("prompt", [
    # gate note 1: an UNQUALIFIED build target ("a/the knowledge graph", "the business processes", "the domain graph") that
    # goes on with coding sentences is a coding request. Only a qualified target (this repo, the code map, ... of/for/on
    # this|my|our repo) may continue with further instructions; an unqualified one only with ", then" and a verb that
    # reads the map. "for the project" does not qualify (the cheap direction).
    "Create a knowledge graph. Use spaCy for entity extraction and store it in Neo4j.",
    "Update the knowledge graph. Add a last_updated field to the Node class and write tests for it.",
    "Make a knowledge graph. Include people, places and events from the novel.",
    "Create the knowledge graph. Read the PDFs in docs/ and extract entities.",
    "Generate the knowledge graph. Write a Cypher export for it.",
    "Build the knowledge graph for the project. Use networkx and write it to graph.json.",
    "Update the knowledge graph now. Keep the old nodes and add the new papers.",
    "Create the business processes. Add a Process class per step.",
    "Build the domain graph. Use the Order aggregate as the root entity in domain.py.",
    "Update the business processes, then run the tests",
    "Build the knowledge graph, then add an endpoint for search", "Map the codebase. Use networkx for it.",
])
def test_an_unqualified_target_with_coding_sentences_selects_no_understand_skill(curated, prompt):
    names = select_for_task(prompt, curated).names
    assert not UNDERSTAND_FAMILY & set(names), (prompt, names)


@pytest.mark.parametrize("prompt, named", [
    # re-gate note 3: a slash command counts only at the start of the request or of a line, never with a "/" after it
    ("Fix the bug in /understand/routes when the graph is missing", None),
    ("The /understand command doesn't show in the picker; fix it", None),
    ("Rename the /understand-domain route handler", None),
    ("/understand/routes crashes when the graph is missing", None),
    ("/understand", "understand"), ("/understand please", "understand"),
    ("/understand-domain", "understand-domain"), ("/understand-dashboard", "understand-dashboard"),
    ("The graph looks stale.\n/understand", "understand"),
])
def test_slash_commands_count_only_at_the_start_of_a_line(curated, prompt, named):
    names = select_for_task(prompt, curated).names
    if named is None:
        assert not UNDERSTAND_FAMILY & set(names), (prompt, names)
    else:
        assert names and names[0] == named, (prompt, names)


@pytest.mark.parametrize("prompt, named", [
    # re-gate note 4: the dock's empty state says "run the understand skill on this project"; "run" (and "invoke") name a
    # skill like "use" does, quoted or not
    ("Run the understand skill on this project", "understand"),
    ("run the `understand` skill on this project", "understand"),
    ("Please run the understand-domain skill", "understand-domain"),
    ("invoke the understand-dashboard skill", "understand-dashboard"),
    ("Don't run the understand skill; fix the parser tests instead", None),
])
def test_run_and_invoke_name_a_skill_like_use(curated, prompt, named):
    names = select_for_task(prompt, curated).names
    if named is None:
        assert "understand" not in names, (prompt, names)
    else:
        assert names and names[0] == named, (prompt, names)


@pytest.mark.parametrize("prompt", ["x" + ";do" * 10000, "x" + ", is" * 8000])
def test_the_question_guard_stays_linear_on_long_prompts(curated, prompt):
    """Re-gate note 2: the old guard rescanned to the end of the sentence from every , ; : -- 907 and 785 ms here."""
    import time
    best = float("inf")
    for _ in range(3):
        started = time.perf_counter()
        select_for_task(prompt, curated)
        best = min(best, time.perf_counter() - started)
    assert best < 0.150, best


# ---- (4) the six upstream siblings -----------------------------------------------------------------------------------

@pytest.mark.parametrize("name", UPSTREAM_SIX)
def test_each_upstream_sibling_selects_when_named_and_fits_whole_at_32k(with_plugin, name):
    for prompt in (f"${name}", f"use the {name} skill", f"Use the {name} skill now, please."):
        names = select_for_task(prompt, with_plugin).names
        assert names and names[0] == name and "understand" not in names, (prompt, names)
    guidance = select_for_task(f"${name}", with_plugin, max_chars=guidance_budget(32768))
    assert guidance.names == (name,)
    assert "[Workflow shortened." not in guidance.text and not guidance.warnings, name
    last = [line for line in _body((UA_SKILLS / name / "SKILL.md").read_text(encoding="utf-8")).splitlines() if line.strip()][-1]
    assert last.strip() in guidance.text, name                                            # the whole text made it in


@pytest.mark.parametrize("name", READ_ONLY_FOUR)
def test_the_read_only_four_name_no_plugin_path(name):
    text = (UA_SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    # no plugin path, no bundled script and no runtime invocation ("node" alone is a graph word in these texts)
    assert not re.search(r"PLUGIN_ROOT|SKILL_DIR|understand-anything-plugin|packages/|\bagents/|\bpnpm\b|\bnpx\b|"
                         r"\.mjs\b|\.py\b|\bnode\s+[\"'<$./~]|\bpython3?\s+[\"'<$./~]", text), name
    assert "knowledge-graph.json" in text                                                 # all they need is the map


def test_knowledge_and_figma_script_calls_through_the_trusted_runner_policy(tmp_path, monkeypatch):
    """The skill_open runner hint says `ua_run, with cwd = the workspace path`; made that way, every knowledge call and
    figma's merge are allowed like a workspace write. figma-scan.mjs takes the Figma file key or URL as a bare token,
    which the rule cannot vouch for, so it asks (it also needs FIGMA_TOKEN and api.figma.com, neither of which the
    sandbox provides)."""
    from dream.core import policy
    ws = tmp_path / "ws"
    (ws / "wiki").mkdir(parents=True)
    monkeypatch.setattr(policy, "runner_trusted", lambda name: True)
    allowed = [{"skill": "understand-knowledge", "script": "parse-knowledge-base.py", "args": [str(ws)], "cwd": str(ws)},
               {"skill": "understand-knowledge", "script": "parse-knowledge-base.py", "args": [str(ws / "wiki")], "cwd": str(ws)},
               {"skill": "understand-knowledge", "script": "merge-knowledge-graph.py", "args": [str(ws)], "cwd": str(ws)},
               {"skill": "understand-figma", "script": "figma-merge.mjs", "args": [str(ws)], "cwd": str(ws)}]
    for call in allowed:
        assert policy.decide("ua_run", call, "auto", ws) == ("allow", "trusted runner inside workspace"), call
        assert policy.decide("ua_run", call, "accept-edits", ws)[0] == "allow"
    scan = {"skill": "understand-figma", "script": "figma-scan.mjs", "args": [str(ws), "AbCdEf0123456789"], "cwd": str(ws)}
    decision, reason = policy.decide("ua_run", scan, "auto", ws)
    assert decision == "ask" and reason.startswith("outside workspace"), (decision, reason)
    monkeypatch.setattr(policy, "runner_trusted", lambda name: False)                    # untrusted: as before
    assert policy.decide("ua_run", allowed[0], "auto", ws) == ("ask", "tool may change things")


WIKI = {
    "index.md": "# Wiki index\n\n## Concepts\n\n- [[attention]] -- how tokens look at each other\n- [[transformer]]\n\n"
                "## Sources\n\n- [[paper-notes]]\n",
    "attention.md": "---\ntitle: Attention\n---\n# Attention\n\nAttention weighs tokens; the [[transformer]] stacks it.\n",
    "transformer.md": "# Transformer\n\nA stack of [[attention]] layers and feed-forward blocks.\n",
    "paper-notes.md": "# Paper notes\n\nNotes on the original paper; see [[attention]].\n",
    "log.md": "# Log\n\n- 2026-09-23 created\n",
    "CLAUDE.md": "# Schema\n\nWiki articles live at the root; raw sources in raw/.\n",
    "raw/paper.txt": "Attention is all you need.\n",
}


def test_parse_knowledge_base_runs_for_real_in_the_sandbox(tmp_path):
    if not UA_TOOL.is_file() or not (UA_SKILLS / "understand-knowledge" / "parse-knowledge-base.py").is_file() or (
            shutil.which("python3") is None or shutil.which("bwrap") is None):
        pytest.skip("needs the ua_run plugin tool, the Understand-Anything clone, python3 and bubblewrap")
    ws = tmp_path / "wiki"
    for rel, text in WIKI.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(text)
    tool = _module(UA_TOOL, "ua_run_for_knowledge")
    _ua(tool, "understand-knowledge", "parse-knowledge-base.py", [ws], ws)
    manifest = json.loads((ws / ".ua/intermediate/scan-manifest.json").read_text())
    ids = {n["id"] for n in manifest["nodes"]}
    assert {i for i in ids if "attention" in i} and {i for i in ids if "transformer" in i}, sorted(ids)
    assert manifest["edges"], "wikilinks become edges"
    _ua(tool, "understand-knowledge", "merge-knowledge-graph.py", [ws], ws)
    assembled = json.loads((ws / ".ua/intermediate/assembled-graph.json").read_text())
    assert assembled["nodes"] and assembled["edges"]
    assert {n["id"] for n in manifest["nodes"]} <= {n["id"] for n in assembled["nodes"]}  # nothing the scan found is lost
    assert not (tmp_path / ".ua").exists()                                                # it wrote only inside the workspace
