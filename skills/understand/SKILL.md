---
name: understand
description: "Use when the user wants this repository or project mapped into a knowledge graph -- 'map this repo', 'understand map', 'knowledge graph of this project', the Understand panel's request -- producing .ua/knowledge-graph.json for the Understand-Anything dashboard."
---

# Understand: map this project into `.ua/`

Result: `.ua/knowledge-graph.json`, `.ua/domain-graph.json`, `.ua/diff-overlay.json` and `.ua/meta.json`, then a one-paragraph report. Do the steps in order, none skipped, without stopping to ask: the user wants the finished map. Announce each step as `[step N/9] <name>`.

Helpers:
- **Plugin scripts** run only through `ua_run(skill="understand", script="<name>", args=[...], cwd=ROOT)`, every call with `cwd=ROOT`. The plugin folder is outside the sandbox: never cd, ls, find or read it. Paths in `args` are absolute; `ROOT` is the workspace path from run_bash `pwd`.
- **Glue** is `python3 .ua/tmp/ua_glue.py <command>` through run_bash. It prepares the scripts' inputs, assembles the four files and checks them. Never write those JSON files by hand.

## 1. Prepare
- run_bash `pwd` gives ROOT.
- Get the glue: `skill_open(name="understand")` prints `Directory: <dir>`; then `copy_files(files=[{"src": "<dir>/glue.py", "dest": ".ua/tmp/ua_glue.py"}])`. Without copy_files: `skill_file(name="understand", path="glue.py")` and write_file its text (after the `understand/glue.py:` line) to `.ua/tmp/ua_glue.py`.
- If `.ua/.understandignore` does not exist, write_file it with exactly these lines (keep an existing one unchanged):
```
.ua/
.understand-anything/
.dream/
.remember/
*.lock
*.pyc
*.so
*.dll
*.bin
*.sqlite*
*.wasm
```
(The scan itself drops node_modules, vendor, venv, dist, build, images, fonts, media and minified files.)
- read_file README and the manifest (package.json, pyproject.toml, Cargo.toml, go.mod ...), then write_file `.ua/tmp/project.json`: `{"name": "<project>", "description": "<1-2 sentences>", "frameworks": ["<frameworks you saw>"]}`.

## 2. Scan
- ua_run script `scan-project.mjs`, args `[ROOT, ROOT + "/.ua/tmp/scan.json", "--exclude-analysis-data"]`; if the user gave exclusions add `"--exclude", "<p1,p2>"` (comma-separated).
- glue `imports-input`, then ua_run `extract-import-map.mjs`, args `[ROOT + "/.ua/tmp/imports-in.json", ROOT + "/.ua/tmp/imports-out.json"]`. (A legacy `.understand-anything/` folder makes the glue stop and say so: rename it, `mv .understand-anything .understand-anything.old`, then redo step 2.)
- glue `scan-result` writes `.ua/intermediate/scan-result.json` and prints the file count by category, the excluded count and the languages. Tell the user those numbers (over 100 files: say it will take a while) and continue.

## 3. Batch
- ua_run `compute-batches.mjs`, args `[ROOT]` writes `.ua/intermediate/batches.json`.
- glue `batch-inputs` writes `.ua/tmp/extract-in-<i>.json` per batch and prints each batch: its files (path, language, lines, category) and each file's imports.

## 4. Analyse every batch
For each batch index i printed above:
1. ua_run `extract-structure.mjs`, args `[ROOT + "/.ua/tmp/extract-in-<i>.json", ROOT + "/.ua/tmp/extract-out-<i>.json"]`, then glue `structure <i>`: the functions, classes and exports the parser found (with line ranges) and the sections/services/endpoints/steps/resources of non-code files.
2. read_file each source file in the batch: summaries come from the code, not the file name.
3. write_file `.ua/intermediate/batch-<i>.json` = `{"nodes": [...], "edges": [...]}` in ONE call. Over 40 nodes: write `batch-<i>-part-1.json`, `batch-<i>-part-2.json` ... instead, each a complete `{"nodes","edges"}` for a few files. Never patch a half-written file.

**Nodes.** One per file; type by category: code, script, markup -> `file`; config -> `config`; docs -> `document`; infra -> `service` (Dockerfile, compose, k8s), `pipeline` (CI workflow) or `resource` (terraform); data -> `table` (SQL), `schema` (graphql/proto/prisma) or `endpoint` (OpenAPI). Id `<type>:<path>`. Add `function:<path>:<name>` and `class:<path>:<name>` nodes, with `"lineRange": [start, end]` from the parser, for exported symbols, functions of 10+ lines and classes with 2+ methods.
Every node: `id`, `type`, `name` (file name or symbol), `filePath`, `summary` (1-2 specific sentences: what it does, its role), `tags` (3-5, lowercase-hyphenated), `complexity` (`simple` under 50 lines, `moderate` to 200, `complex` beyond). No other types.

**Edges** `{"source", "target", "type", "direction": "forward", "weight"}`:
- `contains` 1.0: file -> each of its function/class nodes.
- `imports` 0.7: `file:<path>` -> `file:<target>` for EVERY import target the batch listing printed for that file, one edge each.
- `calls` 0.8, `inherits` 0.9, `implements` 0.9, `exports` 0.8 (file -> its exported symbol), `depends_on` 0.6, `tested_by` 0.5 (production file -> test file).
- Non-code: `configures` 0.6, `documents` 0.5, `deploys` 0.7, `triggers` 0.6, `defines_schema` 0.8, `migrates` 0.7, `related` 0.5.
Only these types. A target is a node you wrote or `file:<path>` of another scanned file; no self-edges.

The lead analyses every batch itself; no subagents.

## 5. Merge
ua_run `merge-batch-graphs.py`, args `[ROOT]` writes `.ua/intermediate/assembled-graph.json` (exit 0 required). Read its report: if it dropped nodes or edges you wrote (unknown type, dangling target), fix that batch file and run it again. Keep its "Could not fix" lines for the report.

## 6. Layers and tour
glue `nodes` prints every file-level node id with its summary. Then:
- write_file `.ua/intermediate/layers.json`: a JSON array of `{"id": "layer:<kebab>", "name", "description", "nodeIds": [...]}` -- 2-7 layers by directory and role (entry points, core logic, data, config, docs, infrastructure, tests); every printed id in exactly one layer.
- write_file `.ua/intermediate/tour.json`: a JSON array of 5-10 `{"order": 1, "title", "description", "nodeIds": [1-5 ids]}` steps teaching the project from README and entry point outward (fewer for a tiny project).

## 7. Assemble
glue `assemble` writes `.ua/knowledge-graph.json`, `.ua/meta.json`, `.ua/diff-overlay.json` and `.ua/intermediate/fingerprint-input.json`, then prints the counts and every problem (a scanned file with no node, a batch file the merge skipped or that changed after it, a type outside the schema, a file node in no layer, an unknown id it dropped). Fix a problem at its source (layers.json, tour.json, or a batch file and step 5) and run `assemble` again until it prints `problems: 0`.
Then ua_run `build-fingerprints.mjs`, args `[ROOT + "/.ua/intermediate/fingerprint-input.json"]`; it prints `Fingerprints baseline: N files` (a failure is reported, not fatal).

## 8. Domain graph
write_file `.ua/intermediate/domain.json` = `{"nodes": [...], "edges": [...]}`: 1-6 `domain:<kebab>` nodes (business areas), 1-5 `flow:<kebab>` nodes per domain (a process; `"domainMeta": {"entryPoint": "<trigger>", "entryType": "http|cli|event|cron|manual"}`), 2-8 `step:<flow>:<kebab>` nodes per flow, each with `filePath`; all with `name`, `summary`, `tags`, `complexity`. Edges: `contains_flow` 1.0 (domain -> flow), `flow_step` weights 0.1, 0.2 ... in step order (flow -> step), `cross_domain` 0.6 (domain -> domain). Only what the code really does; a tiny project has one domain.

## 9. Finish and report
glue `finish` builds `.ua/domain-graph.json` from domain.json, validates the four files and prints the summary; fix what it lists (steps 6-8) and rerun until `problems: 0`. Leave `.ua/tmp` and `.ua/intermediate` in place. Report in one paragraph: project name, files analysed by category, nodes by type, edges by type, the layer names, the tour steps, what was excluded (the ignore file's rules and the user's patterns) and any warnings, ending "The map is ready: .ua/knowledge-graph.json".

Full type tables and file shapes: `skill_file(name="understand", path="references/schema.md")` -- only if a type question comes up.
