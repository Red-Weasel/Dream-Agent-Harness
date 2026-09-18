# DREAM-040 Vision tool repair

Recorded: `2026-09-11T15:13:21-05:00`
Work items: `DREAM-040`
Outcome: `implemented`
Actor: Codex lead and Astra vision implementer.

## Request

Owner reports that the local Vision model receives instructions to call see but
no see schema, and repeatedly mistakes screenshot paths for potential visual input.
Fix capability/tool wiring and contradictory guidance without interrupting the
owner's ongoing work. Continue the pending README/main publication separately.

## Changes

Root owns screenshot/tool guidance and documentation. Astra implementer owns
backend/provider/engine capability normalization and new CPU vision tests.
Accept explicit configuration or declared capability as such; do not call it live
image qualification. Preserve explicit vision=False. Ordinary status reads remain
read-only. A path or metadata result is never visual evidence.

## Validation

Baseline /tmp/dream-vision-baseline.json. Root graph calls returned Transport
closed; focused source reads followed. Fresh reviewer graph access worked.

Implemented model-scoped MachX features.vision normalization at connection,
explicit profile override precedence, aligned see registration/image forwarding and
Engine context, and declaration invalidation on model changes. The launcher captures
an architecture declaration; it is not runtime projector or image-acceptance proof.
Read-only /props and /models inspection found no live image capability field. Joining
an existing server without launch metadata needs explicit image configuration.

Screenshot results now match session image settings. Static screenshot, image
metadata, demonstration and system guidance no longer imply an unavailable hidden
see helper. Disabled see refuses before resolving or reading the image path.

Five root regression cases first failed on contradictory screenshot hints and a
missing disabled-handler guard (/tmp/dream-vision-guidance-red.log); all five passed
after repair. Added user-view cases bring screenshot-contract coverage to78 passing.
The first combined vision suite passed212 cases in1.10s before the fresh review fix
(/tmp/dream-vision-combined.log). The core agent separately passed136 cases in1.04s.
A mocked HTTP transport with the real see handler verified exact base64 image bytes
in the outgoing image_url payload. These are transport fixtures, not model vision.

Fresh independent Astra review caught historical image_url content still reaching a
text-only model after a switch, despite see being removed. Its reproduction observed
images_sent=1 with enabled=False. Repair now replaces retained images with explicit omission notices and preserves
adjacent text. Both author regressions failed before repair then passed. The unchanged
independent next-request reproduction now sends0 images; final independent subset
passed90 cases in0.36s. Reviewer also verified a201-tool deferred schema fixture: see
can be revealed through tool_schema(name="see"). Root corrected guidance to distinguish
deferral from disabled image input; reviewer reread the final wording and found no
remaining contradiction within scope.
An independent pre-repair subset passed87 cases; its sandbox attempt timed out at120s,
while the bounded outside-sandbox run completed. Earlier author sandbox thread runs
also stalled and were interrupted; corrected CPU runs exposed pre-start context and
schema-identity regressions, which were repaired. No timed-out run is counted as passing.
No actual image submission, inference, model load, driver probe or owner restart.

Final combined model-free suite: **235 passed in1.12s**, exit0,
/tmp/dream-vision-final.log. Command: timeout120 .venv/bin/pytest -q followed by
screenshot_contract, local_vision_capabilities, vision_status, capability_integration,
provider_capabilities, model_profile_switch, schema_deferral, council_handoff and
compaction test modules. Ten final source/test hashes are recorded in
/tmp/dream-vision-final-hashes.json. An initial approval-review timeout prevented
this test process from starting; its permitted retry completed. The author's
separate edit/test escalation also timed out before execution, then split normal
edits and bounded CPU tests succeeded. These were approval timeouts, not test passes.

Tracking passed with52 dated records and all16 source paths changed since the
vision baseline covered; the broader Blender baseline passed all26 changed paths.

## Unfinished work

None within this repair's implementation scope. Live image acceptance, loaded
projector readiness and owner runtime verification remain unperformed. The current
Dream process still has its old Python code; no model/work was interrupted.
GitHub publication remains blocked pending explicit approval of the refreshed payload.

## Next steps

After current work finishes, restart Dream to load the repair. The normal MachX
launcher captures architecture vision declarations (DeepSeek4 included); when
attaching without that declaration, configure image input explicitly for the
image-capable model. Verify one image only after owner resource preflight and when
ready; do not assume transport tests qualify live model perception. Contributors
and all final CPU test processes have finished. Preserve the dirty tree and evidence.

## Files changed

- `dream/core/backends/openai_compat.py`
- `dream/core/engine.py`
- `dream/core/capabilities.py`
- `tests/test_local_vision_capabilities.py`
- `dream/tools/vision.py`
- `docs/runtime-controls.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-11-vision-tool-repair.md`

- `dream/tools/studio.py`
- `dream/tools/files.py`
- `dream/tools/demonstration_tools.py`
- `dream/core/system_prompt.py`
- `tests/test_screenshot_contract.py`
- `README.md`
