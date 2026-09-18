# DREAM-079 — V4.1 native tool protocol

Recorded: `2026-09-16T13:07:05-05:00`
Work items: `DREAM-079`
Outcome: `implemented`
Actor: Codex.

## Request

Fix Dream's loaded V4.1 refusing every chat with "tool definitions are not supported
yet". Implement native tool support rather than removing Dream's tools. Acceptance:
reference-equivalent schemas/history/results, structured output, no DSML call syntax
in visible replies, malformed/truncated calls execute nothing, and safe live
qualification. Baseline `/tmp/dream-v41-tools-20260916.json`:799 source hashes.
Existing dirty Dream tree and untracked engine experiment artifacts preserved.

## Changes

The failure was reproduced in a CPU prompt probe before editing. The engine also
received assistant history already rendered as Qwen text. The OpenAI request parser
now preserves original assistant prose and structured calls alongside legacy text.
V4.1 consumes the native data. Other templates retain their existing text input.

The encoder renders V4.1 schemas and DSML, namespaces, JSON arguments and Python-
style schema spacing, preserves reasoning with tools, merges tool results/user
messages and sorts results by prior call IDs. The output parser validates complete
native calls, duplicate parameters and JSON values. Completed calls become OpenAI
tool_calls. Partial/aborted calls have no executable result. An authoritative empty
native call array prevents the generic Qwen fallback from executing quoted prose.

The streaming server recognizes V4.1 DSML, including malformed native tags, holds
call text until parsing finishes, and drops incomplete marker tails. Non-streaming
stop handling also recognizes V4.1 markers. No sampling, GPU allocation, context,
parallel, or kernel behavior was changed. Tools were not disabled.

Engine source changes (external sibling repository):
- include/ie/deepseek41_prompt.hpp
- include/ie/tokenizer.hpp
- src/model/deepseek41_prompt.cpp
- src/engine/ds41_engine.cpp
- src/server/openai_proto.cpp
- src/server/openai_server.cpp
- tests/unit/openai_proto_test.cpp
- tools/CMakeLists.txt
- tools/ds41_prompt_test.cpp
- tools/ds41_protocol_fixture.cpp (new CPU bridge)
- tools/ds41_protocol_test.py (new reference comparison)

Sources were staged in `/tmp/dream-ds41-port`, tested, then copied with original-
hash guards and backups under `original/`. Rebuilt executable replaces the file;
the owner process retains its old executable mapping. Graph discovery was used;
engine graph text search with a server path filter returned insufficient results,
so focused source reads were used. No agents were delegated.

## Validation

- Initial CPU probe reproduced the tools refusal (exit1).
- `cmake --build build --target ie ie_ds41_prompt_test ie_ds41_protocol_fixture
  openai_proto_test deepseek4_tokenizer_test -j 2` completed. A final incremental
  rebuild of changed targets also completed. Logs in `/tmp/dream-ds41-port/`:
  `build.log`, `build-final.log`. Aggregate-initializer and OpenMP-option warnings
  remain; no build errors.
- `tools/ds41_protocol_test.py build/tools/ie-ds41-protocol-fixture
  ~/models/DeepSeek-V4.1-Flash/encoding`:654 protocol checks pass.
  Shipped golden files1 and2 match byte for byte. Includes schema unicode/escaping,
  namespaces, numeric formatting, mixed tool/user ordering, thinking modes, native
  output, malformed/truncated output and real HTTP request/response serialization.
- Checkpoint `encoding/test_encoding.py`:50 passed. Invoked with bytecode and
  pytest cache disabled; reference/model files were not edited.
- Reference `prompt_golden.py` generated15 text/thinking cases in staging;
  rebuilt `ie-ds41-prompt-test` passed their byte and token-ID comparisons, shipped
  supported files and unsupported input refusals. `prompt-test-final.log` records it.
- Shipped files3/4 use internal reminders/tasks; file5 uses images. Those remain
  unsupported and explicitly refused. No claim that vision/internal tasks work.
- `openai_proto_test`:all OK, including retained native history and authoritative
  no-call responses. `deepseek4_tokenizer_test`:18 prompt goldens/parser cases pass;
  GGUF token sections explicitly skipped because its configured path was unavailable.
- Initial request/response test used an unsupported chat_template_kwargs field,
  exposing a fixture mismatch in thinking mode. Corrected it to MachX's actual
  enable_thinking field; final654 checks include both modes. Numeric probing found
  Python/nlohmann exponent15 formatting differed; repaired and added ten regressions.
- Dream: `DREAM_DS41_PROTOCOL_FIXTURE='~/machx-inference-engine/build/tools/ie-ds41-protocol-fixture'
  .venv/bin/python -m pytest -q tests/test_v41_tool_protocol.py
  tests/test_stream_completion.py tests/test_openai_truncation.py`:22 passed in0.45s.
  Three new checks use actual C++ encoder/parser/OpenAI serialization with scripted
  transport: one read-only fixture tool executes exactly once, its result reencodes,
  and malformed/incomplete calls execute nothing. No actual filesystem tool or GPU.
- Host inspection: owner V4.1 PID2407820 on11435,ctx75000,parallel1,two cards.
  /health ok,inflight0,queued0. /props reports arch deepseek_v41,load_s44.
  Available host RAM about39GiB; model already occupies resources. GPU sensors
  sampled35–49C. This is not clearance to load another model. No server stopped.
- Full suite, live generation and native UI tool loop are unperformed. Reference
  parsing/serialized SSE tests do not substitute for live streaming qualification.
- Engine and Dream `git diff --check` passed. Final tracking passed:92 dated
  records,5 changed Dream source paths checked against the session snapshot.

## Unfinished work

Live qualification is blocked pending owner approval to reload the existing idle
GPU process. The approval question remains pending. Running inode13501253 is the
old binary; rebuilt inode13544260 contains this repair. The same refusal will
persist in the currently loaded process until it is replaced. No task-owned server
or GPU job is running. The owner's process/session and load lock remain untouched.
No unrelated source or historical handoff was rewritten. No owner acceptance,
release or universal V4.1 agent reliability is claimed.

## Next steps

After owner approval, recheck /health and process identity; preserve the original
launch settings; gracefully replace that server, not a second parallel copy.
Recheck available GPU/RAM after unload before loading. Run ordinary chat with tools
present, then a small safe native tool call and continuation through Dream; verify
no DSML leaks, tool executes once, and follow-up history succeeds. Record failure
and logs if the live model emits malformed calls or exposes another issue.
If the owner reloads themselves, use Dream's normal close/reopen/model selection
and retry in the saved project. Keep this handoff and append new live evidence.

CPU reproduction needs no GPU: run the engine protocol script above and set
DREAM_DS41_PROTOCOL_FIXTURE for the new Dream integration tests. Without that
variable the optional three integration checks skip explicitly.

## Files changed

- `tests/test_v41_tool_protocol.py`
- `docs/desktop.md`
- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/updates/2026-09-16-v41-native-tools.md`
