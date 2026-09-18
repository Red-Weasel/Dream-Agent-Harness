# DREAM-035 — Single-model priorities

Recorded: `2026-09-11T11:17:02-05:00`
Work items: `DREAM-035`
Outcome: `implemented`
Actor: Codex lead; Astra task-quality and efficiency authors; independent non-author Astra judges.

## Request

Implement approved single-model priorities1–4: proportionate verification,
recoverable context, attributable effort/outcome measurements, and qualified
visual/execution prerequisites. Avoid benchmarks and owner runtime changes.

## Changes

All four implementations and the full-run CLI integration repair are independently
accepted and integrated. Verification carries exact task scope; recent archives
provide exact JSON-safe locators; timing separates configured/prepared/sent values
and immutable failure corrections; committed assessments remain separate from
task success. Missing HTTP visual prerequisites refuse before verifier inference.
Existing execution containment and live effort controls remain documented.

Root preserved591 baseline hashes and the existing dirty tree. Isolated authors
never edited main; root integrated exact gated paths without fuzz. Existing Astra
threads were reused as non-author judges because the thread cap prevented fresh
threads. No new-thread or external Claude/Grok-feedback claim is made.

## Validation

All phase gates passed; exact counts and earlier failures are retained below.
The final CLI repair passed511 selected,14 unchanged independent and6 new probes.
All13 final source/test hashes match /tmp/dream-single-final-hashes.json. Both
HTTP patch orders match. Initial full run failed4 tests plus2 fixture teardown
errors with4079 passes,35 skips and7 warnings; repair addresses both root causes.
Corrected full rerun passed **4097 passed, 35 skipped, 7 warnings in 288.09s**, exit0.
/tmp/dream-single-full2.log and /tmp/dream-single-final-verification.json record
the final result; all13 reviewed hashes stayed unchanged. Both full sessions ended.

## Unfinished work

None within the bounded implementation scope; final tracking is recorded below.
Live model quality/latency,
provider image acceptance, learned tuning, native startup, clean install and owner
production acceptance remain unqualified. No dream/memory source changed; its
retrieval evaluation was not required. No model loads, benchmarks or owner
Dream/model restart occurred. New Python behavior needs a new application process.

## Next steps

For DREAM-014/015, freeze a small comparable task set and independent artifact
criteria before a targeted actual-model trial; the new telemetry can attribute
settings but cannot choose an optimum by itself. Use the review packet
for external Claude/Grok feedback. New Python behavior takes effect in a new Dream
application process; the owner's running instance was not touched. Preserve
original failed logs and both source manifests when resuming. No test process or
implementation agent remains active at handoff.

## Files changed

- `artifacts/harness-single-model/2026-09-11/manifest.json`
- `docs/capability-contract.md`
- `docs/harness-evaluation.md`
- `docs/harness-review-packet.md`
- `docs/performance.md`
- `docs/project/CURRENT.md`
- `docs/project/DECISIONS.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-11-single-model-priorities.md`
- `docs/runtime-controls.md`
- `docs/superpowers/plans/2026-09-10-harness-adaptation-loop.md`
- `dream/core/backends/openai_compat.py`
- `dream/core/engine.py`
- `dream/core/loop.py`
- `dream/core/subagents.py`
- `dream/telemetry/turn.py`
- `dream/tools/memory_tools.py`
- `dream/tui/app.py`
- `tests/test_cli_completion.py`
- `tests/test_session_recovery_listing.py`
- `tests/test_turn_measurement.py`
- `tests/test_verifier_fork.py`
- `tests/test_verifier_prerequisites.py`
- `tests/test_verifier_scope.py`

## Chronological evidence

Initial root graph transport closed; focused reads followed bounded attempts.
A template lookup at updates/TEMPLATE.md failed harmlessly; actual templates/update.md
was read. Later notes supersede interim candidate statuses without erasing failures.

Phase32 candidate: current request replaces unbounded reachable-state enumeration;
shared verifier guidance stops after required coverage and preserves broad requests.
Red test invocation first hit pytest's reserved request parameter, then four expected
missing-scope/contract failures in0.55s. After implementation41 tests passed with
one browser test deselected in0.48s. Logs /tmp/dream-single-p32-{red,red2,green}.log.
Four-path candidate hashes and diff frozen under /tmp/dream-single-p32*. Independent
non-author Astra gate running. No measured latency/behavior claim follows.

Phase35 audit: execution prerequisite guidance and typed refusal already exist.
No execution source change warranted; qualify existing tests. Missing visual-tool
preflight remains concrete. Graph transport again closed; focused known-source
reads followed. Two guessed source/test paths were absent; no files were changed.


Phase33 isolated author candidate passed39 checks in3.56s. Root read its exact
7-line production addition and new recovery tests; independent gate pending.
Paths /tmp/dream-single-recovery-{candidate,report.md,hashes.json,verification.log}.
Initial four expected missing-locator failures and one test-only source-marker
assertion failure are retained. No main recovery integration yet.

Phase35 pre-build regression reproduced10 missing-tool failures with1 positive
control in0.27s (/tmp/dream-single-p35-red.log). Existing execution-readiness suite
passed43 in0.33s (/tmp/dream-single-execution-check.log), with probe/process/provider
operations denied by its fixtures. No execution source change is needed.

Additional changed paths so far:
- `dream/core/backends/openai_compat.py`
- `dream/core/subagents.py`
- `tests/test_verifier_fork.py`
- `tests/test_verifier_scope.py`
- `tests/test_verifier_prerequisites.py`
- `docs/runtime-controls.md`


Isolated Phase35 candidate now passes96 cases with1 existing browser case deselected.
/tmp/dream-single-p35-final2.log preserves final0.63s check. Preflight checks actual
registered/allowed show_html, get_webview_logs, save_screenshot and see before
inference. Other subagents retain their prior path. Fixture-only intermediate
failure expected researcher to have no image tool; it actually allows see. The
text-only positive control now uses filer. Original95pass/1fail retained in
/tmp/dream-single-p35-final.log. A failed patch context made no production changes;
corrected after reading exact lines. One automatic approval review timed out;
its one permitted retry completed. No unsafe-action rejection or owner approval
was inferred. Phase35 main integration and independent gate remain pending.

Phase34 isolated candidate author reports162 passes in10.99s,17 new measurement
checks. Root read source diff and report; independent task-quality gate running.
Separate efficiency reviewer independently gates Phase33. Both authored candidates
remain frozen. No production integration claimed for either yet.


Phase32 independent PASS all six criteria:41 selected plus24 independent probes,
1 intentional browser deselection, no skips/warnings. Actual ask/done dispatch,
300-check Unicode requirements tail, directed/deferred modes, HTTP/length/filter/
round-bound failures and attribution qualified. Four hashes unchanged. Report
/tmp/dream-single-p32-gate.json and .md; first judge probes miscounted filer posts
and guessed an SDK factory (11failed/13passed), corrected without candidate edits.
Original judge artifacts retained. Root treats Phase32 as accepted.

Phase33 first independent gate FAIL: new exact listing IDs containing surrounding
whitespace opened a different trimmed-ID session.39 selected passed; independent
2failed/5passed. Root coauthored same-scope correction in isolated candidate:
remove ID trimming entirely. IDs are opaque; absent exact IDs cannot fall back to
another archive. Legacy manually padded IDs now require exact values. New3 red
cases reproduced the issue; repaired42-case suite passed3.67s. Original independent
probes remain unchanged for same-judge recheck. Artifacts /tmp/dream-single-p33-gate*
and /tmp/dream-single-p33-repair*. This is one formal failure, not three blockers.
No main recovery integration yet. Phase34 and35 independent gates running.


Phase33 SAME-judge round2 PASS:49 cases in4.37s, including all7 unchanged independent
probes and both original failures. Root checked verdict and two frozen hashes,
verified main baseline before copying only memory_tools.py and new listing tests.
Exact-ID compatibility change is documented in runtime controls. Prior FAIL and
all artifacts remain. Phase33 accepted and integrated after Phase32.

- `dream/tools/memory_tools.py`
- `tests/test_session_recovery_listing.py`

- `docs/project/DECISIONS.md`


Phase35 independent PASS all six criteria:96 selected/1 browser deselected and49
independent probes, no skips/warnings/forbidden I/O. Every required-tool subset,
auto/directed dispatch, allowlist exclusions, provider filtering, availability
changes, actual done/next-request attribution and other-agent routes covered.
Three hashes and six unchanged execution paths checked. Report
/tmp/dream-single-p35-gate.json. Accepted isolated candidate awaits priority3
integration order; no endpoint image acceptance or actual model behavior claim.

Phase34 first formal FAIL found four issues despite162 selected passes: relative
model paths exposed directories; failed preparation could duplicate turn identity;
post-result error/cancellation retained completed timing; review-only resume could
attach old worker evidence to an unrelated current turn. Independent10 probes
returned4pass/6fail. Report /tmp/dream-single-p34-gate-report.json and unchanged
probes /tmp/dream-single-p34-gate-probes/test_gate.py. Original author repairs the
same candidate under the same criteria; same judge will recheck. No measurement
source integrated yet. This is one formal failure at this gate, not an impasse.

Mid-session tracking passed45 dated records and13 changed paths. Final tracking
will follow integration. All pre-existing files and prior handoffs preserved.

- `artifacts/harness-single-model/2026-09-11/manifest.json`


Phase34 pre-submission repair passed all10 original independent probes and22
measurement cases, then226 existing selected regressions. Root source review
caught an additional repair regression before submitting it: acknowledgment had
moved inside bind_context and ahead of final bookkeeping. Original success boundary
keeps required input until those operations close. Author is restoring that
boundary and adding context-exit/emission-failure tests. This is pre-submission
review, not another formal gate failure. No repaired measurement source in main.
The first combined-directory probe invocation encountered duplicate conftest option
registration; separate finite test processes resolved that invocation issue.


Phase34 repaired candidate is frozen for SAME judge: final32 measurement/timing
checks pass5.96s and10 unchanged gate probes pass1.77s. Broader232 checks passed
26.78s before a final narrow callback-identity placement change; the gate will
rerun current selection. Packet /tmp/dream-single-p34-repair-author-report.json,
manifest and patch with the same prefix. First candidate copied to
/tmp/dream-single-measurement-v1. Root reviewed final lifecycle ordering and dry-run
integration succeeds with zero fuzz against the current accepted verifier changes.
Result/timing callbacks are explicitly final:false; final runtime/Engine status is
final:true after context, callback and acknowledgment. Prep/review-only identities
remain null where unknown. Native effective settings remain unknown.

Evidence retention limitation disclosed by contributor: one duplicate-conftest
collection error log was overwritten by a later passing invocation. Its occurrence
is recorded in the repair report; original bytes are unavailable. Two report-only
shell invocations used unavailable bare python and exited127, then were rerun with
python3. Neither changed production files. Other named gate failures, first
candidate, original probes and final verification logs remain preserved.


Phase34 SAME-judge round2 FAIL: original four findings resolved,10 original probes
pass1.78s. Expanded existing/new selection275pass/2fail identified F5: required
sources acknowledged before timing.finish and RunMeter.record. Two original
retention tests fail in candidate and pass against original source. Report
/tmp/dream-single-p34-gate-r2-report.json. Author is designing a coherent ordering
repair before editing; all pre-ack source-retention boundaries remain mandatory.
Two formal FAILs at this gate. No third gate submitted, no measurement main edit.
Owner asked status and received the exact three-passed/one-under-repair status.


Root coordination correction: after the author completed its previous turn,
follow-up send_message calls queued F5 requests without activating a new turn.
Root detected this and used followup_task; owner was informed of the delay.
No source or evidence changed during that interval. Author proposed the coherent
pre-ack primary row / explicit late correction contract; root approved before
edits. The shared plan records the rationale and fixed acceptance boundaries.
Original probes/F5 tests stay unchanged; no third formal verdict yet.


Interruption recovery detail: main source currently contains accepted32/33 only.
The root-created tests/test_verifier_prerequisites.py remains the initial red
preflight fixture until accepted35 is integrated; replace it with the exact gated
candidate, not a deleted/disabled test. Phase35 final candidate includes its
updated Phase32 scope-test fixture. Apply final accepted measurement diff with
zero fuzz first, then the Phase35 preflight hunk/tests, preserving both contributions
to openai_compat.py. Verify hashes of all non-overlapping accepted files and compose
the HTTP file in both orders before full main regression. Shared591-file snapshot
remains the only session-diff baseline. No current full-suite pass claimed.


Phase34 F5 repair frozen for third SAME-judge gate. Author selection329 passed
in29.76s; all10 original independent probes pass unchanged in1.73s. This is author
verification, not a gate verdict. Root read the complete production diff and
final repair delta. Primary finish/record precedes acknowledgment, late failures
append immutable revision1 corrections, and failed primary bookkeeping preserves
required input with truthful local status when possible. Frozen packet
/tmp/dream-single-p34-f5-author-report.json and manifest/patch at the same prefix.
Same judge was explicitly activated via followup_task; final review is running.


Phase34 third SAME-judge PASS:329 selected in30.64s,10 original unchanged probes
in1.78s and4 new independent shared-meter/correction/actual-OSError probes in2.06s.
All five findings resolved, no new blocking findings, no skips/warnings. Report
/tmp/dream-single-p34-gate-r3-report.json. All six candidate and original test hashes
verified. Exactly two formal failures preceded the pass; no circuit breaker invoked.

Root integrated Phase34 with zero-fuzz patch after checking all non-overlapping
baselines, then Phase35 preflight and exact accepted fixtures. Both HTTP composition
orders match6b87a18997b6d3149e458b9c661ab257d3505e4a1d46b11b1a43c414a5b1e290.
All12 integrated source/test hashes verified in /tmp/dream-single-integrated-hashes.json.
No ignored/rejected hunks or tests deleted. Full model-free regression started in
normal threading with software Chromium and disposable DREAM_ROOT, owned session18269,
/tmp/dream-single-full.log; result pending. A non-author Astra actor separately
checks final integration and documentation; no duplicate full suite requested.

- `dream/telemetry/turn.py`
- `dream/core/engine.py`
- `dream/tui/app.py`
- `dream/core/loop.py`
- `tests/test_turn_measurement.py`
- `docs/performance.md`
- `docs/capability-contract.md`
- `docs/harness-evaluation.md`

- `docs/harness-review-packet.md`


Combined regression FAILED:4 failed,4079 passed,35 skipped,7 warnings,2 teardown
errors in284.58s, exit1. /tmp/dream-single-full.log preserved; owned session18269
ended. Thirty-four skips guard model loading; one migration fixture is already
migrated. Existing seven warnings comprise Starlette/httpx and AnyIO deprecations,
two sync pytest asyncio marks and three SDK read-tool shadow warnings.

Four failures are in CLI-loop integration omitted from selected Phase34 gates.
Two terminal-evidence tests use an incomplete SimpleNamespace Engine and fail
before consuming declared fake processes (also causing two teardown errors).
Two actual Engine failed-exit tests observe interrupted rather than expected error
after Loop closes on a failed result. Root activated the same author for isolated
diagnosis before edits; same judge will check the repair. All original selected
gate passes remain scoped evidence, not combined acceptance. This is a failed
root regression, not an independent third formal FAIL. A fresh graph lookup for
the newly implicated CLI test again returned Transport closed; focused reads used.

Independent integration review reports code/hash/doc PASS with six probes, but
does not override the full failure. Its initial two probe failures expected four
schemas instead of the actual seven allowed verifier tools; corrected fixture
expectation passed, original v1/log retained. Two operator wording corrections
were applied: prerequisite failures are distinct, and App emits references after
guided-store commits. Root progress-tail observations missed earlier failure
markers; owner was immediately given the actual terminal failure result.


Author confirmed both root causes before edits. Root approved a narrow integration
repair: real Engine test fixture; preserve observed failure during ordinary
GeneratorExit closure, while actual CancelledError remains interrupted. Pre-build
contract is appended to the shared plan. Same author currently running in isolated
post-integration copy; main remains the previously reviewed source. No request to
the owner or new automatic tuning. Full rerun is justified by the demonstrated
regression after same-judge repair verification.


Integration repair frozen for SAME judge:511 selected passed38.14s across the
accepted lifecycle set,14 new close/cancel controls and all five CLI modules;
original10 probes passed1.83s and4 correction probes passed2.16s unchanged.
Packet /tmp/dream-single-cli-measurement-author-report.json and manifest/patch.
Production delta is only the three Engine cancellation/closure handlers. Root
read all three changed files and patch dry-run passes with zero fuzz. Current
main remains unchanged pending independent verdict.

Test-authoring evidence: initial matrix attempted context entry/exit across tasks,
causing7 setup errors alongside8 intended red failures; corrected single-owner
probe gave8 expected failures/11passes before production changes. Both logs
/tmp/dream-single-cli-measurement-red.log and -red-r2.log remain preserved. The
existing failed-exit and required-retention tests are unchanged.


The repair judge's first expanded test launch did not execute because automatic
approval review timed out. Its single permitted retry is running. This is neither
a test failure nor an unsafe-action rejection. The judge also adds same-owner
cancellation during the next backend read after an observed failure.


CLI integration-repair SAME-judge PASS:511 selected in38.40s,10 original probes
in1.89s,4 original correction probes in2.16s and6 new next-read cancellation
probes in0.88s. Three frozen hashes and four unchanged evidence hashes verified
before/after. Report /tmp/dream-single-cli-gate-report.json. Root verified main
baselines and applied exactly three files with zero fuzz, then checked13 final
hashes in /tmp/dream-single-final-hashes.json. The original12-path manifest remains
unchanged for its prior integration report.

Root's first full-rerun launch did not execute because automatic approval review
timed out. The one permitted retry launched successfully as owned session98879,
/tmp/dream-single-full2.log. Same bounded disposable-root/software-Chromium command
as the first run, with the independently reviewed source repair. Result pending.


Full-rerun command (normal-threading approval, disposable state; no model opt-in):

```sh
DREAM_ROOT="$(mktemp -d /tmp/dream-single-full2-root-XXXXXX)" \
PYTHONPATH=/tmp/dream035-astra-preview-diagnostic \
PYTHONDONTWRITEBYTECODE=1 timeout 360s .venv/bin/python -m pytest \
-q -ra --tb=long -o verbosity_assertions=2 -p no:cacheprovider \
-p dream035_cpu_preview_diag > /tmp/dream-single-full2.log 2>&1
```

The already inspected temporary plugin only adds --disable-gpu to owned Chromium
launches. No production browser workaround or screenshot retry was introduced.
Tracking during the rerun passed45 dated records and23 changed source paths.


Final integration checkpoint `2026-09-11T12:50:14-05:00`: corrected full suite **4097 passed, 35 skipped, 7 warnings in 288.09s**,
exit0. All13 final source/test hashes verified unchanged after the run. Both owned
full-suite sessions18269 and98879 ended; contributors and judges completed. The
first full failure was repaired and rerun, not hidden or relabeled. The35 skips
and7 warnings retain the reasons recorded above. No model opt-in, benchmark,
owner restart, install/build or native/live-provider qualification occurred.
Final machine evidence: /tmp/dream-single-final-verification.json; repository
manifest preserves both full attempts, all gate dispositions and exact hashes.

Final tracking: Tracking passed (45 dated records). Session snapshot:23 changed source paths checked.
No remaining blocker or active verification process within this implementation scope.
