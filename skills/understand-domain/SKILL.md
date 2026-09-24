---
name: understand-domain
description: "Use when the user wants the business domains of this project mapped -- 'domain graph', 'business flows', 'map the business domains', /understand-domain -- producing .ua/domain-graph.json (domains, flows, steps) for the Understand dock's Domain view."
---

# Understand domain: business domains, flows and steps into `.ua/domain-graph.json`

Result: `.ua/domain-graph.json`, then a short report. Do the steps in order without stopping to ask. Announce each as `[step N/5] <name>`. You write the domain graph yourself; do not hand it to another agent.

Helpers:
- `ROOT` is the workspace path from run_bash `pwd`.
- **Glue** is `python3 .ua/tmp/ua_glue.py <command>` through run_bash: the `understand` skill's helper. It prints what you need and checks what you write.
- The plugin's one script runs only as `ua_run(skill="understand-domain", script="extract-domain-context.py", args=[ROOT], cwd=ROOT)`. The plugin folder is outside the sandbox: never cd, ls, find or read it.

## 1. Prepare
- run_bash `pwd` gives ROOT.
- `skill_open(name="understand-domain")` prints `Directory: <dir>`. Copy the glue from the sibling `understand` skill, even when `.ua/tmp/ua_glue.py` exists (it may be older): `copy_files(files=[{"src": "<dir>/../understand/glue.py", "dest": ".ua/tmp/ua_glue.py"}])`. Without copy_files: `skill_file(name="understand", path="glue.py")` and write_file its text (after the `understand/glue.py:` line) to `.ua/tmp/ua_glue.py`.

## 2. Gather
- list_dir `.ua`. If it lists `knowledge-graph.json`, the map `.ua/knowledge-graph.json` exists: skip the scan.
- No map: run the ua_run call above. It writes `.ua/intermediate/domain-context.json` (file tree, entry points, exports and imports, README and manifest).
- glue `domain-context` prints the material: from the map (layers, every file with its summary and symbols, the links between them) or from the scan (entry points, file signatures, README). A `problem:` line says what to run first.
- read_file the source behind an entry point only when the listing does not show what it does.

## 3. Write the domain graph
write_file `.ua/intermediate/domain.json` = `{"nodes": [...], "edges": [...]}` in ONE call:
- 1-6 `domain:<kebab>` nodes: business areas (optional `"domainMeta": {"entities": [...], "businessRules": [...]}`).
- 1-5 `flow:<kebab>` nodes per domain: a process something triggers, with `"domainMeta": {"entryPoint": "<e.g. POST /orders>", "entryType": "http|cli|event|cron|manual"}`.
- 2-8 `step:<flow>:<kebab>` nodes per flow, in order, each with `filePath` (the project file that does it, relative) and `"lineRange": [start, end]` when known.
- Every node: `id`, `type`, `name`, `summary` (1-2 specific sentences), `tags` (2-4, lowercase-hyphenated), `complexity` (`simple`, `moderate` or `complex`).
- Edges `{"source", "target", "type", "direction": "forward", "weight"}`: `contains_flow` 1.0 (domain -> flow), `flow_step` 0.1, 0.2 ... in step order (flow -> step), `cross_domain` 0.6 (domain -> domain, with a `description`).
Only what the code really does, in the project's own terms; a tiny project has one domain.
No map: also write_file `.ua/tmp/project.json` = `{"name": "<project>", "description": "<1-2 sentences>", "frameworks": [...]}` from the README and manifest.

## 4. Build and check
glue `domain` builds `.ua/domain-graph.json` from domain.json (with the map's project data, or project.json), checks it and prints the counts and `problems: N`. A problem (a type other than domain/flow/step, an edge to a missing node, a flow or step with no edge into it, a step without a real filePath, a bad entryType) leaves the graph unsaved: fix domain.json and run `domain` again until `problems: 0`.

## 5. Report
One paragraph: each domain with its flows, the number of steps, the cross-domain links, and whether it came from the map or the scan. Then tell the user it is in the **Understand ⌬** dock (sidebar): the dashboard's **Domain** view. When `.ua/knowledge-graph.json` does not exist, tell them the Domain view appears only once a map exists (run "map this repo"; the map writes its own domain graph).
