---
name: understand
description: "Use when the user wants this repository or project mapped into a knowledge graph -- 'map this repo', 'understand map', 'knowledge graph of this project', the Understand panel's request -- producing .ua/knowledge-graph.json for the Understand-Anything dashboard."
---

# Understand: map this project into `.ua/`

Result: `.ua/knowledge-graph.json`, `.ua/domain-graph.json`, `.ua/diff-overlay.json` and `.ua/meta.json`, then a one-paragraph report. Do the steps in order without stopping to ask: the user wants the finished map. Announce each step as `[step N/9] <name>`.

Helpers:
- **Plugin scripts** run with run_bash from the workspace, as `node "$UA_SKILLS/understand/<script>" ...` (`python3` for .py). `$UA_SKILLS` is the plugin's skills folder, readable in the sandbox; never cd, ls or edit it.
- **Glue** is `python3 .ua/tmp/ua_glue.py <command>` through run_bash. It prepares the scripts' inputs, assembles the four files and checks them (never write those by hand); do what its `next:` lines say.

## 1. Status
- Get the glue: `skill_open(name="understand")` prints `Directory: <dir>`; then `copy_files(files=[{"src": "<dir>/glue.py", "dest": ".ua/tmp/ua_glue.py"}])`. Without copy_files: `skill_file(name="understand", path="glue.py")` and write_file its text (after the `understand/glue.py:` line) to `.ua/tmp/ua_glue.py`.
- glue `status` (add `--exclude "<p1,p2>"` for paths the user leaves out, `--include "<dir>"` for a skipped folder the user wants) compares the map with the files, writes `.ua/.understandignore` if missing, skips installed packages and browsers, and prints one of:
  - `The map is current` or `nothing to map`: tell the user what it printed (when built, what it covers), and stop.
  - `update mode`: only edited, added and deleted files are redone. Do steps 2-9 as its `next:` lines say.
  - `no map`: the full map. read_file README and the manifest (package.json, pyproject.toml ...), then write_file `.ua/tmp/project.json`: `{"name": "<project>", "description": "<1-2 sentences>", "frameworks": ["<frameworks you saw>"]}`.

## 2. Scan
- Run the scan `status` printed: `node "$UA_SKILLS/understand/scan-project.mjs" "$PWD" "$PWD/.ua/tmp/scan.json" --exclude-analysis-data --exclude "<its patterns>"`.
- glue `imports-input`, then `node "$UA_SKILLS/understand/extract-import-map.mjs" "$PWD/.ua/tmp/imports-in.json" "$PWD/.ua/tmp/imports-out.json"`. (A legacy `.understand-anything/` folder stops the glue: rename it, `mv .understand-anything .understand-anything.old`, and redo step 2.)
- glue `scan-result` writes `.ua/intermediate/scan-result.json` and prints the files by category, what was excluded or skipped and the languages. Tell the user those numbers (over 100 files: say it will take a while) and continue.

## 3. Batch
- `node "$UA_SKILLS/understand/compute-batches.mjs" "$PWD"` (update mode: the `--changed-files` form scan-result printed) writes `.ua/intermediate/batches.json`.
- glue `batch-inputs` writes `.ua/tmp/extract-in-<i>.json` per batch and prints each batch's files and their imports. Over 150 files it prints the big-repo path instead: one parser run, then glue `draft` writes every batch file and names the key files you summarise from their code; then step 5.

## 4. Analyse every batch
For each batch index i printed above:
1. `node "$UA_SKILLS/understand/extract-structure.mjs" "$PWD/.ua/tmp/extract-in-<i>.json" "$PWD/.ua/tmp/extract-out-<i>.json"`, then glue `structure <i>`: what the parser found, with line ranges.
2. read_file each source file in the batch: summaries come from the code, not the file name.
3. write_file `.ua/intermediate/batch-<i>.json` = `{"nodes": [...], "edges": [...]}` in ONE call. Over 40 nodes: write `batch-<i>-part-1.json`, `batch-<i>-part-2.json` ... instead, each a complete `{"nodes","edges"}` for a few files. Never patch a half-written file.

**Nodes.** One per file; type by category: code, script, markup -> `file`; config -> `config`; docs -> `document`; infra -> `service` (Dockerfile, compose, k8s), `pipeline` (CI workflow) or `resource` (terraform); data -> `table` (SQL), `schema` (graphql/proto/prisma) or `endpoint` (OpenAPI). Id `<type>:<path>`. Add `function:<path>:<name>` and `class:<path>:<name>` nodes, with `"lineRange": [start, end]` from the parser, for exported symbols, functions of 10+ lines and classes with 2+ methods.
Every node: `id`, `type`, `name` (file name or symbol), `filePath`, `summary` (1-2 specific sentences: what it does, its role), `tags` (3-5, lowercase-hyphenated), `complexity` (`simple` under 50 lines, `moderate` to 200, `complex` beyond). No other types.

**Edges** `{"source", "target", "type", "direction": "forward", "weight"}`:
- `contains` 1.0: file -> each of its function/class nodes.
- `imports` 0.7: `file:<path>` -> `file:<target>` for EVERY import target the batch listing printed, one edge each.
- `calls` 0.8, `inherits` 0.9, `implements` 0.9, `exports` 0.8 (file -> its exported symbol), `depends_on` 0.6, `tested_by` 0.5 (production file -> test file).
- Non-code: `configures` 0.6, `documents` 0.5, `deploys` 0.7, `triggers` 0.6, `defines_schema` 0.8, `migrates` 0.7, `related` 0.5.
Only these types. A target is a node you wrote or `file:<path>` of another scanned file; no self-edges.

The lead analyses every batch itself; no subagents.

## 5. Merge
`python3 "$UA_SKILLS/understand/merge-batch-graphs.py" "$PWD"` writes `.ua/intermediate/assembled-graph.json` (exit 0 required). If its report dropped nodes or edges you wrote (unknown type, dangling target), fix that batch file and rerun it; report its "Could not fix" lines.

## 6. Layers and tour
glue `nodes` lists the file-level node ids with summaries (folders, for a big map). Then:
- write_file `.ua/intermediate/layers.json`: a JSON array of `{"id": "layer:<kebab>", "name", "description", "nodeIds": [...]}` -- 2-7 layers by directory and role (entry points, core logic, data, config, docs, infrastructure, tests); every file in exactly one layer (`"paths": ["<folder>/"]` takes a whole folder).
- write_file `.ua/intermediate/tour.json`: a JSON array of 5-10 `{"order": 1, "title", "description", "nodeIds": [1-5 ids]}` steps from README and entry point outward (fewer for a tiny project).

## 7. Assemble
glue `assemble` writes `.ua/knowledge-graph.json`, `.ua/meta.json`, `.ua/diff-overlay.json` and `.ua/intermediate/fingerprint-input.json`, then prints the counts and every problem; fix each at its source (layers.json, tour.json, or a batch file and step 5) and rerun until `problems: 0`.
Then `node "$UA_SKILLS/understand/build-fingerprints.mjs" "$PWD/.ua/intermediate/fingerprint-input.json"` (a failure is reported, not fatal).

## 8. Domain graph
write_file `.ua/intermediate/domain.json` = `{"nodes": [...], "edges": [...]}`: 1-6 `domain:<kebab>` nodes (business areas), 1-5 `flow:<kebab>` nodes per domain (a process; `"domainMeta": {"entryPoint": "<trigger>", "entryType": "http|cli|event|cron|manual"}`), 2-8 `step:<flow>:<kebab>` nodes per flow, each with `filePath`; all with `name`, `summary`, `tags`, `complexity`. Edges: `contains_flow` 1.0 (domain -> flow), `flow_step` weights 0.1, 0.2 ... in step order (flow -> step), `cross_domain` 0.6 (domain -> domain). Only what the code really does; a tiny project has one domain.

## 9. Finish and report
glue `finish` builds `.ua/domain-graph.json` from domain.json, validates the four files and prints the summary; at `problems: 0` it records the map's state for the next `status`. Fix what it lists (steps 6-8) and rerun until `problems: 0`. Leave `.ua/tmp` and `.ua/intermediate` in place. Report in one paragraph: project name, files analysed by category, nodes and edges by type, the layer names, the tour steps, what was excluded or skipped and any warnings, ending "The map is ready: .ua/knowledge-graph.json".

Full type tables and file shapes: `skill_file(name="understand", path="references/schema.md")` -- only for a type question.
