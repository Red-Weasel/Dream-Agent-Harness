# DREAM-035 — Screenshot argument contracts before preview effects

Recorded: `2026-09-10T20:34:00-05:00`
Work items: `DREAM-035`
Outcome: `implemented`
Actor: Codex lead; screenshot_contract_audit read-only; screenshot_contract_gate independent reviewer.

## Request

Continue after [vision status](2026-09-10-harness-vision-status.md) under the
predeclared phase9 contract.

## Changes

Baseline /tmp/dream035-screenshot-contract-baseline.json contains550 source hashes.
Lead owns screenshot schemas and argument checks in studio.py, a new CPU test
module and operator/tracking docs. Preserve existing valid browser screenshot
tests. Advertise existing constraints and reject malformed destinations/steps
before preview loading or capture. No Preview/encoding/permission changes.

## Validation

Read-only audit reproduced six malformed schema admissions, including an invalid
extension reaching a synthetic failing preview loader and hiding its correction.
Probe /tmp/dream035-screenshot-schema-audit.py. Root graph-first requests failed
or timed out; focused reads followed. No implementation verdict yet.

## Unfinished work

No unfinished implementation in phase9. The prior post-test Playwright diagnostic
remains unattributed. Live native/provider/clean-install and owner acceptance are
unperformed. No owned fixture process or lock remains.

## Next steps

Start phase10 only under its declared contract and fresh baseline: align optional
schema selection/catalog estimates with admission, preserving mandatory schemas.

## Files changed

- `dream/tools/studio.py`
- `tests/test_screenshot_contract.py`
- `docs/runtime-controls.md`
- `docs/harness-review-packet.md`
- `docs/superpowers/plans/2026-09-10-harness-adaptation-loop.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/DECISIONS.md`
- `docs/project/updates/2026-09-10-harness-screenshot-contract.md`

Candidate1: native/schema fixture module initially20 failed/40 passed0.39s;
after implementation60 passed0.32s. Log /tmp/dream035-screenshot-red.log. This
includes malformed extension/newline/dotfile, destination exclusivity/types,
step/code constraints and finite-delay guards before any synthetic preview load,
plus valid1/100 and1/12 step boundaries and case-insensitive extensions. Positive
memory controls retain default200ms/PNG; valid input still surfaces preview errors.
Compatibility tightening: explicit empty unused destination fields must be omitted;
wrong types are no longer coerced in direct handlers. Raw and resolved paths both
require supported image extensions. Existing valid browser assertions unchanged.
Broader CPU/browser regression active; fresh gate pending.

Lead broader run144 passed,2 existing synchronous-asyncio-marker warnings22.68s:
timeout120 .venv/bin/python -m pytest -q tests/test_screenshot_contract.py
tests/test_studio_tools.py tests/test_preview.py tests/test_tool_validation.py
tests/test_tool_skill_reliability.py tests/test_schema_budget_real.py --tb=short.
During that run only unused test import removal and finite-delay error wording
changed; the final60-case fixture rerun passed0.32s afterward. A proactive schema
review then anchored extension matching to basename starts, avoiding repeated
suffix scans for a long invalid name. Five path controls added, including10k
invalid basename and nested hidden filenames. Focused final validation follows;
no timing or benchmark claim. Source frozen for a fresh tool-contract gate.

Final candidate focused validation108 passed,2 existing marker warnings1.74s:
tests/test_screenshot_contract.py tests/test_tool_validation.py
tests/test_schema_budget_real.py. Screenshot fixture module now65 cases. Fresh
screenshot_contract_gate independently verifies final source hash
490ce3a1d273fdd2b80d07844137666d2911a4c571ce16babc3800f4c60b8804.
Separate council_readonly_audit checks advisory enforcement using read-only
source/synthetic arguments; it owns no tracked files and runs no provider.

Fresh gate attempt1 FAIL, one medium finding: Path(save_path).suffix normalizes
trailing /, // and /. before the raw-extension check. The schema rejects x.png/,
x.png// and x.png/. but actual direct handlers loaded/captured normalized x.png.
Fresh broader149 tests passed2 warnings20.53s;123 independent observations found
this counterexample, not123 successful safety assertions. Evidence:
/tmp/dream035-phase9-judge-probe.py. Correct original-string validation before
normalization, preserve resolved-extension checks, add all three forbidden-effect
regressions and return to the same judge. Prior FAIL remains recorded.

Candidate2 reproduced3 native FAIL/3 schema PASS for all three suffixes, then
shares the same original-string pattern between schema and handler before path
resolution. Resolved-extension validation remains. Final focused114 passed,
2 existing marker warnings2.40s; screenshot module now71 cases. Same judge reviewing
ee71590b39bb6ffa7405231bb2ae418129e5b83a6febc6bb05de516115c5d857.

Council read-only audit returned bounded no-findings. Synthetic construction
checks covered three CLI restrictions, seven forged HTTP mutation/delegation calls
and five SDK denials. Existing SDK/fake-transport module6 passed3 warnings0.26s.
Evidence /tmp/dream035-council-readonly-audit/probe.py. No provider executable was
launched. Native CLI isolation remains trusted-provider behavior, not independently
proven OS isolation. This audit supports no new implementation phase; live provider
compliance, native qualification and production acceptance remain unperformed.

Same-judge attempt2 PASS:155 broad CPU/browser tests passed2 existing warnings
20.65s. Original123 probes completed successfully after correction;36 extra
malformed paths rejected before resolution or preview access. No blocking phase9
finding remains. Reviewed hash ee71590b39bb6ffa7405231bb2ae418129e5b83a6febc6bb05de516115c5d857.
The run also emitted an unhandled Playwright TargetClosedError after execution;
its origin is not established and the passing exit does not erase that diagnostic.
Final model-free integration active with disposable DREAM_ROOT, timeout360,
pytest -q -ra --tb=short -o faulthandler_timeout=20 and log
/tmp/dream035-final-integration.log. Source frozen; await actual result.

Final integration completed: exit0, 2767 passed,35 skipped,7 warnings in242.22s.
Skips remain34 model-loading guards and1 migration-state fixture; warnings remain
two dependency deprecations, two synchronous-asyncio-marker warnings and three SDK
read-tool shadow warnings. No TargetClosedError or Future-exception text appeared
in that log; this is non-reproduction, not a diagnosed fix. The earlier diagnostic
was captured only in judge tool session61151/chunk e3f403, immediately after100%,
with no traceback/test name: Future exception was never retrieved / TargetClosedError
(Target page, context or browser has been closed). Preserve it as unresolved.

Fresh readiness_evidence_audit reviewed all phase handoffs and current limits,
found no newly demonstrated product defect warranting another implementation phase,
and separated offline evidence from native/provider/clean-install/trust qualification.
Existing host-trusted Preview/extensions/MCP/provider restrictions remain explicit
boundaries; there is no universal containment or model-quality claim.

Owner now explicitly requested Astra subagents and additional fresh verification.
Runtime permits four concurrent agents total, so lead launched three independent
fresh-context gpt-6-astra/high assignments: astra_final_verifier (pure CPU cross-phase
contracts), astra_preview_diagnostic (one bounded instrumented attribution attempt,
no source edits), astra_adaptation_reflection (bounded primary-source research and
falsifiable next hypotheses). Lead retains all source/tracking ownership. All three
are active; source remains frozen. No external model feedback is attributed to
Claude/Grok, and no prior external-transmission rejection is retried.

Astra diagnostic preparation found no explicit --disable-gpu guard in the reviewed
existing browser fixtures. Prior headless runs did not establish hardware device
use; statements describing them as CPU fixtures describe intended test scope,
not observed GPU telemetry. No model loads, GPU probes or inference benchmarks
were initiated. The new /tmp diagnostic plugin explicitly adds --disable-gpu and
enables asyncio debug. Its restricted-sandbox attempt failed30 tests/4 passed in
9.44s because Chromium launches were denied (Operation not permitted), plus one
unraisable transport/event-loop warning. No page workload ran and the original
diagnostic was not reproduced. Lead authorized one identical approved CPU-fixture
retry with a separate log; preserve the failed environment attempt. Artifact root
/tmp/dream035-astra-preview-diagnostic. No tracked fixture changes.

Final Astra reconciliation `2026-09-10T20:54:00-05:00`: astra_final_verifier PASS,
242 independent focused CPU tests in1.13s, no failures/skips/warnings, plus40 new
assertion-bearing probes. Frozen backend/Studio hashes matched. Evidence:
/tmp/dream035-astra-final-tests.log, /tmp/dream035-astra-final-probes.py and.json.
The approved instrumented Preview retry passed34 tests21.79s with asyncio debug
and --disable-gpu, no diagnostic/warning matches. Both instrumentation and earlier
sandbox failure remain material differences; original TargetClosedError stays
unreproduced/unattributed. No implementation is justified for that observation.

Astra adaptation reflection found a new concrete estimated-budget defect: schema
selection priced an optional CJK tool at358 versus admission1153. Window4096,
fraction.1 and fixed messages/output caused refusal (-19remaining); deferral
admitted the same input. This is pure-module evidence, not live tokenizer/quality
proof. Its proposed phase10 includes catalog packing, fake-backend reproduction,
mandatory-schema preservation, discovery and cache behavior. All phase9 source
work is complete; tracking passed30 records/9changed paths. No owned fixture
process remains. The next iteration retains the active broader goal.
