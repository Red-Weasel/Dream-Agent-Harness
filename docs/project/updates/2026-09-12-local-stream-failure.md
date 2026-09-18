# DREAM-053 — Local stream failure diagnosis and repair

Recorded: `2026-09-12T01:05:06-05:00`
Work items: `DREAM-053`
Outcome: `implemented`
Actor: Codex lead (diagnosis, adapter repair, integration and tracking); Astra dream_native_design (independent review, session audit and test storage isolation).

## Request

Owner asked to inspect the recent failing session, distinguish development interference
from harness defects and repair the failure point. This took priority over remaining
DREAM-052 backend enhancements. Acceptance: local generation has no implicit profile
read cap; explicit read limits remain effective; failures retain diagnostic category;
no replay or automatic uncertain-state clearing. Preserve the existing dirty checkout.
Attribution snapshot: /tmp/dream-redesign-execution-baseline.json.

## Changes

MachX and canonical loopback HTTP generation use DREAM_LLM_READ_TIMEOUT_S, default
None, instead of the profile idle default (balanced: 300 seconds). Hosted HTTP
retains its profile default unless explicitly overridden. Connect stays 15 seconds;
write/pool behavior is unchanged. Context reports the actual client read timeout.
Failure results and runtime records now include exception_type, stage,
read_timeout_known and read_timeout_s; no raw response, prompt, credential or URL.
ReadTimeout guidance explains that the server might still be working and requires
checking it before reconciliation. Stop and request ownership remain authoritative.
See ADR-040 and the runtime/coordination guides.

Shared test fixtures now isolate DATA_DIR and its import-time derived DB_PATH and
SESSIONS_DIR. Fourteen exact fixture transcripts from this task were identified by
content/hash and checked against the owner database (zero matching session IDs).
They were moved intact to /tmp/dream-fixture-session-quarantine/ using the manifest
/tmp/dream-test-storage-artifacts.json. No owner transcript or database was removed.
Subsequent fixture checks produced no new or modified owner transcripts.

## Validation

Observed owner session 20260911-233401-5bca, workspace Payback:

- Blender probe succeeded: Cycles registered/selectable, Eevee available in 4.0.2.
  GPU rendering was not established. Two see calls returned actual image payloads.
- Request 15 began at 2026-09-12T05:16:04.852Z. First activity was 120.857 seconds,
  first text 350.877 seconds; the stream failed after 657.894 seconds. The turn ended
  at 05:27:02.731Z (00:27 CDT) with stream_error and no final request usage.
- Server timing reported 119.553 seconds prefill and 606.251 seconds decoding,
  12,209 decoded tokens, 725.804 seconds overall. Generation continued after the
  client stream failed. Both turn time caps were null, not an expired run budget.
- The 05:46:29Z retry failed in 21 milliseconds with zero tools/tokens. Its local
  request coordinator still held the previous uncertain request. This explains
  the immediate retry refusal independently of the original transport diagnosis.
- The old ledger did not persist the exception class. ReadTimeout is strongly
  supported by the effective 300-second client cap, stream timings and server
  continuation, but is not directly recorded. Context exhaustion is not established.
  Adapter source predates this turn; later memory-status edits do not explain it.
  Older cli.log errors predate the incident and were not treated as current failures.

Read-only host checks found the original Dream PID 1035974 absent and port 11435
refusing connections; server logs also recorded shutdown with zero in-flight work.
A second approved host check reconfirmed both and the exact uncertain request
(a3315db4c1164a76917d1fd62fd84068). Its state was backed up to
/tmp/dream-failed-request-before-reconcile.json and explicitly reconciled through
EndpointCoordinator with confirmed_idle=True. Observed resulting state: idle.
No request replay, model load, owner stop/restart, render or package change.
Sandbox process absence alone was not accepted as proof; host checks were necessary.
The shutdown actor is unknown.

Final gate, isolated fixtures with approved host loopback access:

```bash
timeout 120 .venv/bin/python -m pytest -q \
  tests/test_local_read_timeout.py tests/test_stream_failure.py \
  tests/test_backend_resilience.py tests/test_stream_completion.py \
  tests/test_local_request_coordination.py tests/test_http_interrupt.py \
  tests/test_test_storage_isolation.py tests/test_memory_save_status.py \
  tests/test_runtime_controls.py tests/test_workspace_design.py \
  tests/test_desktop_chat.py tests/test_desktop_companion.py \
  tests/test_studio_media_playback.py --tb=short
```

118 passed, 2 existing Starlette/AnyIO deprecation warnings, 20.49 seconds.
Evidence: /tmp/dream-session-repair-gate.txt. Real buffered loopback SSE tests wait
120 milliseconds against a 30-millisecond profile cap: default read completes and
releases ownership; an explicit cap raises ReadTimeout and leaves uncertainty.
Both receive exactly one request. Classification covers localhost, IPv4/IPv6 and a
remote 127-prefixed hostname. Diagnostic tests verify bounded metadata without
secret response text. Storage tests exercise actual WorkingMemory and Engine
initialization before browser/provider access.

Intermediate failure: metadata key kind collided with RunMeter.record's positional
argument. It was corrected to exception_type and the full focused gate rerun.
Astra review identified the remote 127-prefixed hostname edge; canonical endpoint
classification repaired it before the final gate. Astra's broader storage check
encountered four existing test_workspace_prompt fake-backend failures (missing
provider attribute); that suite already supplies its own DB/session paths. These
are not relabelled passes and are outside the passing gate above. No full-suite or
live-provider claim. Graph discovery again returned Transport closed; focused
source reads were used. New fresh-agent spawning was unavailable at the thread cap;
the existing Astra contributor independently reviewed lead adapter code.

Final tracking check passed: 68 dated records, all 40 changed source paths covered
against /tmp/dream-redesign-execution-baseline.json. git diff --check passed.

## Unfinished work

Owner next-session qualification remains: load repaired source on the next normal
Dream start and resume from saved project files. Do not blindly rerender or replay
an uncertain command. No unattended monitor remains. All task fixture runs completed;
the backed-up old coordinator record is evidence, not a record to restore routinely.
The exact old transport exception cannot be recovered from its ledger.

DREAM-052 tasks 1–5 and bounded task 6 additions are implemented as described in the
[design handoff](2026-09-12-dream-redesign-execution.md). Durable-first memory/deferred
indexing, concurrent editing with isolated writers and arbitrary output lineage remain.
No production-wide qualification, owner acceptance or GitHub publication is claimed.

## Next steps

Next owner run: use the intended Payback workspace, inspect the saved animation and
latest script before changing it, and check actual playback/visual evidence. If it
fails, inspect request_failure and turn_result.failure in the runtime ledger; both
now distinguish transport category and effective timeout. Do not use an increased
profile idle value as a substitute for the dedicated local HTTP read setting.

Next implementation under DREAM-052: durable-first memory save ordering and deferred
indexing lifecycle before concurrent writer orchestration. Preserve the dirty tree
and use the shared plan and review brief. The design and repair records jointly cover
this session's source changes; tracking is checked against the initial snapshot.

## Files changed

- `docs/inference-coordination.md`
- `docs/project/CURRENT.md`
- `docs/project/DECISIONS.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-12-local-stream-failure.md`
- `docs/runtime-controls.md`
- `dream/core/backends/openai_compat.py`
- `dream/core/engine.py`
- `dream/gui/static/workspace.js`
- `tests/conftest.py`
- `tests/test_local_read_timeout.py`
- `tests/test_test_storage_isolation.py`
