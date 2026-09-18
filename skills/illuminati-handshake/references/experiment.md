# Experiment and promotion evidence

Use this reference when creating the lab experiment or its file-based fallback.
Keep each experiment separate from the installed working tool/skill.

Record the task-specific acceptance criteria, an adequate baseline, the candidate
method and primary sources, and a fixed trial/time/resource budget. Identify the
input fixtures, expected outcomes, and failure cases before tuning the candidate.
If cases come from user data, keep their existing access and retention boundaries.

Call `capability_lab_create` with `name`, `goal`, `criteria` (a list of acceptance
statements), and `method` (`"tool"` or `"rl"`). It returns `id`, `directory`, and
`manifest`, and creates `acceptance.md`, `research.md`, and `experiment.json`
under `workspace/.dream/labs/<id>/`. Use the returned directory rather than
guessing an ID or path. Put primary-source findings in `research.md` and concrete
baseline/held-out checks in `acceptance.md`; keep experiment metadata consistent.

Use ordinary file tools to build candidate code and fixtures in that directory.
Execute bounded checks through the normal `run_bash` policy. Keep development and
held-out cases separate, and retain commands, exit status, outputs, and costs.
`capability_lab_inspect(id)` returns the manifest, file hashes, and reported
evaluation. Treat these as an inventory of evidence, not independent verification.
Inspect the relevant outputs and run acceptance checks before making a success
claim. Neither lab tool installs, enables, or promotes the candidate.

If lab tools are unavailable, keep the same acceptance/research/experiment files,
candidate code, separate fixtures, and results in an isolated workspace directory;
identify this as the file-based fallback. Do not install dependencies globally or
overwrite the working implementation to make an experiment run. Pin relevant
dependencies and preserve commands/seeds needed to reproduce the result; do not
put credentials in manifests or logs.

Evaluation evidence must identify which candidate and fixtures were executed,
pass/fail outcomes against the predefined criteria, resource costs, and limitations.
An unevaluated candidate is a draft. A successful smoke test is evidence of startup,
not evidence of task competence. Repeatability and held-out results matter more
than a polished demonstration.

Prepare the plugin candidate inside the experiment directory. Its `plugin.yaml`
must contain `enabled: false`; place reviewed tools in `tools/` and portable
SKILL.md packages in `skills/`. Keep supporting evaluation evidence with the
candidate, and check Dream's catalog for an existing enable override before
placing anything into an active plugin root. A manifest default cannot neutralize
an already saved `plugin:<name>` enable override.

Before promotion, inspect generated code and external effects, rerun relevant
regression/held-out checks, preserve the previous version, and verify how to roll
back. Present the candidate, evidence, intended enable changes, and rollback
through the normal user-reviewed workflow. There is no automatic lab promotion
API. Do not turn copying files or running `capability_lab_inspect` into an implied
approval. A portable package is not permission to execute scripts or enable hooks.
Configured Python entry files also require explicit operator review and approval
of their exact SHA-256 before import. Enabling a plugin does not provide this
trust, and changed source needs a fresh review. Keep candidates inert until then.
