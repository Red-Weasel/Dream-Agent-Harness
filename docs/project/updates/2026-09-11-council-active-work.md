# DREAM-050 — Named model catalogs and active Council work

Recorded: `2026-09-11T22:30:40-05:00`
Work items: `DREAM-050`
Outcome: `implemented`
Actor: Codex lead; Astra council_catalog contributor (catalog/native effort plus cross-review).

## Request

Owner requested frontier model dropdowns, reasoning effort, and Council members
that can actively hop in and polish project files, instead of only consultation.
Acceptance for this implementation: readable catalogs and custom fallback;
per-model effort; full tool-enabled member turns in the current workspace;
sequential team relay; original main restored; failures stop the relay; private
read-only review remains intact. Simultaneous resident editing is explicitly not
implemented. Preserved the existing dirty tree using source hash baseline
/tmp/dream-council-active-baseline.json; no blanket staging/reset/cleanup.

## Changes

Council main/member model fields are selects with readable names. Codex reads
bounded installed cache metadata; bundled Claude/Gemini/Grok/OpenAI catalogs use
official documented identifiers and retain custom IDs. Catalogs are suggestions,
not proof of account access. Supported native Codex Max/Ultra now forward unchanged;
HTTP adapters retain their current range. Selected members show model names.

Assign to selected member invokes the ordinary App turn/tools/permissions path.
Run team relay visits the configured members sequentially. All provider handoffs
stay on the lifecycle owner task; each generation uses existing cancellable turns.
Original main/model/effort are restored in finally. Incomplete, failed or stopped
work does not start another member or replay a task. Restoration failures remain
visible. A disconnected browser cannot cancel a handoff, and UI requires refresh
before repeating uncertain work. No additional Council work time limit was added.

Independent review keeps the existing private consultation implementation.
Operator docs and local README distinguish editing, relay and review. The decision
is recorded in DECISIONS.md. Roster storage remains backward compatible.

Cross-review found legacy effort normalization and default-model restoration
edge cases; both were repaired and tested. The reviewer found no additional
blocking defect after those changes. This was an independent contributor review,
not a completely fresh-context verifier; another requested agent could not start
because the tool reported the thread limit.

## Validation

Observed final host gate, Python .venv, fixture providers and GPU-disabled Chromium:
241 passed in15.26s. Command: timeout150 .venv/bin/python -m pytest -q with
 tests/test_council_ui.py, test_council_work.py, test_council_runtime.py,
 test_council_config.py, test_council_effort.py, test_model_catalog.py,
 test_council_handoff.py, test_council_context.py, test_council_startup.py,
 test_cli_review_isolation.py, test_cli_agent.py, test_guided_workflows_runtime.py.
Full output: /tmp/dream-council-final-gate.txt. No live model or render invoked.
Real Engine round-trip fixture uses real AnyIO cancellation scopes and fake
backends; it checks lifecycle task identity, workspace and conversation continuity.
It does not establish actual provider reconnect success or artifact quality.

Observed node --check for council.js, Python compile, and git diff --check passed.
Viewed the desktop fixture screenshot /tmp/dream-council-desktop.png. Mobile
interaction and overflow checks ran in the browser suite.

Initial new tests failed before implementation. A fixture task lookup error was
corrected before using its failures as implementation evidence. Browser testing
caught model label ambiguity, repaired with explicit labels; stale UI fixtures
were updated for selects and the already-existing permission_mode_status call.
One sandbox-local async fixture run stalled and was interrupted; the bounded host
rerun passed. Contributor broader CLI run hit five trusted-system-Python failures
inside the restricted execution environment; the final host CLI checks passed.
Graph exploration in the lead timed out in automatic approval; focused reads
were used. Contributor graph indexing/search succeeded. No review rejection of
implementation occurred. Tracking initially reported the not-yet-written handoff;
Final snapshot tracking passed:64 dated records,20 changed source paths checked.

## Unfinished work

No live provider/account validation, actual Astra polish, GPU/model loading,
owner application restart, render, GitHub push or owner acceptance performed.
One member per provider plus main remains; editing turns are serialized, and
simultaneous resident editing backends are not supported. Catalog freshness and
provider-specific account eligibility require future validation. Existing local
servers are attached, not automatically loaded or killed. Native session-private
state does not transfer; bounded Dream-visible history does.

## Next steps

Owner: restart Dream to load source, select the Payback workspace, open Council,
enable ChatGPT · Codex, select GPT-6 Astra and effort, Apply Council, enter the
polish task and Assign to selected member. Review the attributed transcript and
actual files. A live provider error should be inspected before any retry.
DREAM-050 engineering implementation is complete for hop-in/relay; simultaneous
resident workers remain a separate extension of the original broader request.
No automatic replay or running task was left by this implementation. Test jobs
finished; temporary evidence only under /tmp. Remote publication remains separate.

## Files changed

- `README.md`
- `docs/desktop.md`
- `docs/project/CURRENT.md`
- `docs/project/DECISIONS.md`
- `docs/project/MASTER_PLAN.md`
- `docs/runtime-controls.md`
- `dream/core/backends/cli_agent.py`
- `dream/core/council_config.py`
- `dream/core/model_catalog.py`
- `dream/gui/static/council.js`
- `dream/tui/app.py`
- `dream/tui/council.py`
- `tests/test_council_config.py`
- `tests/test_council_effort.py`
- `tests/test_council_handoff.py`
- `tests/test_council_runtime.py`
- `tests/test_council_ui.py`
- `tests/test_council_work.py`
- `tests/test_model_catalog.py`
- `docs/project/updates/2026-09-11-council-active-work.md`
