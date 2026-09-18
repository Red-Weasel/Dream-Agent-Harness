# DREAM-037 — Public-safe harness publication

Recorded: `2026-09-12T10:48:15+00:00`
Work items: `DREAM-037, DREAM-054`
Outcome: `released`
Actor: Codex lead (export, README, portability, packaging, publication); Astra dream_native_design (independent privacy review and UI fixture repairs).

## Request

Owner explicitly authorized pushing the harness and rewriting GitHub's main README,
excluding memories and personal information. Scope includes removing previously
tracked personal/runtime content from the current public tree. Rewriting old remote
history remains a separate explicit decision. Preserve all local originals.

## Changes

Public source is built in /tmp/dream-public-release-20260912 from an explicit code,
test, curated-skill, synthetic-evaluation and public-documentation allowlist. No
private development handoffs, local instructions/configuration, root images/archives,
runtime databases, memory/session/log exports or workspace outputs are selected.
Generic public contributor/tracking files replace local records only in the export.
README is rewritten for installation, providers, tools, Council, Studio, optional
memory, execution/recovery controls and actual limitations. Public setup/runtime/
privacy/development guides accompany it. Ignore rules exclude local state.

Per-user external-tool defaults replace absolute developer paths without changing
those directories for the existing user. Tests replace personal paths/narratives,
recalled preferences and captured identifiers with neutral synthetic fixtures.
Default model-scan test now uses an isolated model file instead of requiring real
host models. Workspace backend fixture supplies the current provider interface.
Recovered unknown-tool fixture uses a genuinely unregistered synthetic tool rather
than the now-specialized disabled-vision response.

## Validation

Independent Astra review scanned source/tests/scripts/skills, then the 543-file
candidate. Personal examples it found were removed. No matched credential patterns,
private identifiers, runtime stores or broken public-document links remained except
two subsequently generalized comment blocks. Generated PNG metadata was checked:
C2PA generation provenance, no matched personal name/path/email/GPS metadata.
A bounded scan is not a universal privacy certification. Inherited Git history
was explicitly excluded from candidate content review.

Wheel built successfully and layout audit passed (247 files); required branding/
workspace assets present, no matched personal identifiers in wheel text. Final candidate wheel rebuilt after source synchronization:247files, SHA256
e8596c1af086744bde73102dc67b3cd6fc904ddb14a779d2979458561172f6d1; reviewed package source matches.

Initial path suite40passed/4sandbox-skipped. Restricted fixture run12failed/50passed:
local sockets and trusted interpreter ownership unavailable in Codex isolation.
Approved host portability gate130passed/1failed: old unknown-tool fixture expected
generic error for see; corrected synthetic unknown-tool fixture then11passed.
Initial full candidate model-free run: 48 failed, 4430 passed, 43 skipped, 7 warnings
in 328.73 seconds (/tmp/dream-public-full-tests.log). Failures were stale fixtures:
UI startup read expectations, fake-engine effort/save-state attributes, missing new
static assets in the hand-built wheel, and the specialized see error. UI fixtures
were repaired by Astra with81focused passes; wheel fixture repaired with69passes;
fake-engine fixture correction passed93 tests. Final candidate full rerun passed4478 tests,43skipped,7warnings in311.79s
(/tmp/dream-public-final-tests.log). No failures remain in that run.
No live provider/model, GPU render or benchmark run.

Remote publication observed: ordinary push d1660a5→07bec6a succeeded. GitHub About
updated to model-neutral Dream wording. GitHub API confirmed exact main SHA,543
matching blob paths/hashes, and byte-identical rewritten README. Source index and
all543 committed SHA256 values matched /tmp/dream-public-release-manifest.json.
Staged whitespace check passed. Independent final review found no remaining matched
personal/credential content, and all7 reviewer-repaired files match verified originals.
GitHub Actions run34689773251 was in progress at last inspection; no remote CI pass
is claimed. /tmp/dream-public-release-verification.json stores exact verification.

## Unfinished work

DREAM-037 publication complete within current-tree scope. DREAM-054 remains planned:
old Git history contains earlier personal/runtime material. Prepared parentless
commitf08664198eb969fb847604c31aeaa97c0396de1d has zero parents and the identical tree
to publishedmain07bec6ad0df07bc7a7f2dc902229e1c54001ec48. It has not been pushed.
Changing main ancestry needs an explicit history-rewrite decision; no current-source
cleanliness claim extends to old commits, caches or downloaded copies. No open PRs,
tags, other heads or forks were reported at preparation time. Recheck before rewriting.
All local originals remain. Private history bundle mode0600 is in
/tmp/dream-public-history-before-20260912.bundle. No owner runtime restart/model load.

## Next steps

Ask the owner to approve the concrete replacement of main history. Review scope is
/tmp/dream-history-cleanup-review.md. If approved, recheck remote state and use an
exact old-head lease; do not retain a public branch/tag pointing to the old history.
Keep local originals and private backup. Follow GitHub's documented sensitive-data
removal process for cached references if needed; no support message was sent.
Do not merge the original private development branch into the cleaned public branch.
Continue future public updates through an audited source export.

GitHub source release does not complete DREAM-052's remaining memory/concurrency
scope or establish universal production readiness. Remote CI completion can be
checked by run ID. Final local tracking check passed:69dated records and all42source paths covered
against /tmp/dream-public-release-baseline.json. git diff --check passed.

## Files changed

- `.gitignore`
- `README.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-12-public-harness-release.md`
- `docs/public/development.md`
- `docs/public/getting-started.md`
- `docs/public/privacy.md`
- `docs/public/runtime.md`
- `dream/config.py`
- `dream/local/machx.py`
- `dream/local/models.py`
- `dream/tools/custom/preview_tui.py`
- `tests/fixtures/README.md`
- `tests/fixtures/codex_mcp_toolcall.jsonl`
- `tests/fixtures/codex_simple.jsonl`
- `tests/fixtures/codex_toolcall.jsonl`
- `tests/fixtures/gemini_error.jsonl`
- `tests/fixtures/gemini_simple.jsonl`
- `tests/fixtures/gemini_toolcall.jsonl`
- `tests/fixtures/grok_maxturns.jsonl`
- `tests/fixtures/grok_simple.jsonl`
- `tests/fixtures/grok_thinking.jsonl`
- `tests/test_active_settings.py`
- `tests/test_cli_agent.py`
- `tests/test_council_runtime.py`
- `tests/test_desktop_bridge.py`
- `tests/test_diagnostics.py`
- `tests/test_execution_readiness.py`
- `tests/test_gemini_adapter.py`
- `tests/test_grok_adapter.py`
- `tests/test_local_preflight.py`
- `tests/test_model_scan_bounds.py`
- `tests/test_models.py`
- `tests/test_performance_controls.py`
- `tests/test_permission_hardening.py`
- `tests/test_policy.py`
- `tests/test_policy_hardening.py`
- `tests/test_project_workspace_controls.py`
- `tests/test_studio_controls.py`
- `tests/test_text_tool_calls.py`
- `tests/test_workspace_prompt.py`
