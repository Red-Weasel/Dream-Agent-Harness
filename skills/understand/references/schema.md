# Understand-Anything schema, as the dashboard reads it

Everything below mirrors the plugin's `packages/core/src/schema.ts` (the dashboard validates with it) and the type
tables of its own `understand` skill. `glue.py assemble` and `glue.py finish` check the produced files against the
same lists.

## The four files in `.ua/`

- `knowledge-graph.json`: `{"version": "1.0.0", "project": {"name", "languages": [], "frameworks": [], "description",
  "analyzedAt": "<ISO 8601>", "gitCommitHash"}, "nodes": [...], "edges": [...], "layers": [{"id", "name",
  "description", "nodeIds": []}], "tour": [{"order", "title", "description", "nodeIds": [], "languageLesson"?}]}`.
  Every file-level node sits in exactly one layer; every `nodeIds` entry is an existing node id.
- `domain-graph.json`: the same envelope (same `project`), nodes of the domain types, `layers: []`, `tour: []`.
- `diff-overlay.json`: `{"version", "baseBranch", "generatedAt", "changedFiles": [], "changedNodeIds": [],
  "affectedNodeIds": []}`. A fresh map has empty lists; the dashboard shows an overlay only when `changedNodeIds` is
  non-empty (the plugin's `understand-diff` skill fills it).
- `meta.json`: `{"lastAnalyzedAt", "gitCommitHash", "version": "1.0.0", "analyzedFiles": <count>}`.

Node paths (`filePath`) are relative to the project root. `gitCommitHash` is `git rev-parse HEAD`, or `none` outside
a repository (the panel then reports freshness as unknown).

## Node types (13) and id conventions

| Type | Meaning | Id |
|---|---|---|
| `file` | source code file (also scripts, HTML/CSS) | `file:<relative-path>` |
| `function` | function or method | `function:<relative-path>:<name>` |
| `class` | class, interface or type | `class:<relative-path>:<name>` |
| `module` | logical module or package (reserved for higher-level analysis) | `module:<name>` |
| `concept` | abstract concept or pattern (reserved) | `concept:<name>` |
| `config` | configuration file (YAML, JSON, TOML, env) | `config:<relative-path>` |
| `document` | documentation (Markdown, RST, TXT) | `document:<relative-path>` |
| `service` | deployable service definition (Dockerfile, compose, k8s) | `service:<relative-path>` |
| `table` | database table or migration | `table:<relative-path>:<table-name>` |
| `endpoint` | API endpoint or route definition | `endpoint:<relative-path>:<endpoint-name>` |
| `pipeline` | CI/CD pipeline configuration | `pipeline:<relative-path>` |
| `schema` | schema definition (GraphQL, Protobuf, Prisma) | `schema:<relative-path>` |
| `resource` | infrastructure resource (Terraform, CloudFormation) | `resource:<relative-path>` |

Node fields: `id`, `type`, `name`, `summary` (never empty), `tags` (never empty), `complexity` (`simple` |
`moderate` | `complex`); `filePath` for every file-level node; `lineRange: [start, end]` for functions and classes;
`languageNotes` optional. The merge script also accepts the aliases `func` -> `function`, `low/medium/high` ->
`simple/moderate/complex`, and strips a project-name prefix from ids; do not rely on it.

## Edge types (26) by category, with the weights the plugin expects

| Category | Types (weight) |
|---|---|
| Structural | `imports` 0.7, `exports` 0.8, `contains` 1.0, `inherits` 0.9, `implements` 0.9 |
| Behavioral | `calls` 0.8, `subscribes` 0.5, `publishes` 0.5, `middleware` 0.5 |
| Data flow | `reads_from` 0.5, `writes_to` 0.5, `transforms` 0.5, `validates` 0.5 |
| Dependencies | `depends_on` 0.6, `tested_by` 0.5 (production -> test), `configures` 0.6 |
| Semantic | `related` 0.5, `similar_to` 0.5 |
| Infrastructure | `deploys` 0.7, `serves` 0.5, `provisions` 0.5, `triggers` 0.6 |
| Schema / data | `migrates` 0.7, `documents` 0.5, `routes` 0.5, `defines_schema` 0.8 |

Edge fields: `source`, `target` (existing node ids), `type`, `direction` (`forward`), `weight` (0 to 1),
`description` optional. The merge script deduplicates edges by (source, target, type), drops edges to missing nodes
and canonicalises `tested_by` to production -> test.

## Domain graph types

Nodes `domain:<kebab>`, `flow:<kebab>`, `step:<flow>:<kebab>` (same required fields; `domainMeta` optional:
`entities`, `businessRules`, `crossDomainInteractions` on a domain; `entryPoint`, `entryType` = `http` | `cli` |
`event` | `cron` | `manual` on a flow; `filePath` and `lineRange` on a step). Edges `contains_flow` 1.0 (domain ->
flow), `flow_step` (flow -> step, weights 0.1, 0.2, ... increasing in step order, all within 0-1), `cross_domain` 0.6
(domain -> domain, `description` optional). Every flow has a `contains_flow` edge in, every step a `flow_step` edge in.

## A batch file (`.ua/intermediate/batch-<i>.json`)

```json
{"nodes": [
  {"id": "file:src/util.py", "type": "file", "name": "util.py", "filePath": "src/util.py",
   "summary": "Date and path helpers shared by the CLI and the API layer.", "tags": ["utility", "helpers", "dates"],
   "complexity": "simple"},
  {"id": "function:src/util.py:format_date", "type": "function", "name": "format_date", "filePath": "src/util.py",
   "lineRange": [10, 25], "summary": "Formats a datetime as ISO 8601 with the local offset.",
   "tags": ["utility", "dates", "formatting"], "complexity": "simple"},
  {"id": "document:README.md", "type": "document", "name": "README.md", "filePath": "README.md",
   "summary": "Project overview with installation and first-run steps.", "tags": ["documentation", "overview", "entry-point"],
   "complexity": "simple"}],
 "edges": [
  {"source": "file:src/util.py", "target": "function:src/util.py:format_date", "type": "contains", "direction": "forward", "weight": 1.0},
  {"source": "file:src/cli.py", "target": "file:src/util.py", "type": "imports", "direction": "forward", "weight": 0.7},
  {"source": "document:README.md", "target": "file:src/cli.py", "type": "documents", "direction": "forward", "weight": 0.5}]}
```

The merge script reads only files named `batch-<i>.json` or `batch-<i>-part-<k>.json`; any other name is dropped
silently.
