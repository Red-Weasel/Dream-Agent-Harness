# DREAM-026 — Exact GLM weight planning and successful live load

Recorded: `2026-09-07T15:24:23.449204-05:00`
Work items: `DREAM-026`
Outcome: `verified`
Actor: Codex; independent read-only reviewer glm_residency_review.

## Request

Owner reported that the rebuilt GLM loader failed to start. Continue the authorized
engine fix, preserve other workloads and verify the actual load rather than relying
only on model-free tests. Prior source and executable were backed up under
/tmp/machx-weight-plan-fix; Dream session baseline is
/tmp/dream-weight-plan-baseline.json. Existing dirty work was preserved.

## Changes

The launch log showed stage 0's estimated weight budget was 5.42 GiB while the
loader projected 5.46 GiB. The September 6 automatic cache consumed the remaining
headroom, exposing the underestimate. The duplicate planner used source tensor
dtype while the loader chooses destination precision by tensor role. For example,
stored F16 router tensors are uploaded as F32. The existing tests did not exercise
this disagreement.

Removed the duplicate heuristic. Glm5NextModel::plan_device_weights now runs the
same metadata-only first pass as load_impl. A scratch model has no allocator and
returns before the VRAM guard, weight-payload access or uploads. Real loading
retains the guard and the existing allocation path. No larger arbitrary padding,
OOM override, pinning rollback or reduction in context was used to mask the defect.

Engine changes (external to Dream): include/ie/glm5next.hpp,
src/model/glm5next.cpp, src/engine/engine.cpp, tests/CMakeLists.txt,
tests/unit/glm5_weight_plan_test.cpp (new), SERVER_CONTROLS.md,
MASTER_DEV_PLAN.md. Applied with hash checks and source/executable backups.
No Dream runtime source or provider-native configuration changed this session.

## Validation

New regression first failed to compile against the old missing planning API. An
initial fixture Tensor naming collision was corrected before the recorded red run.
The finished fixture contains KDA/dense and MLA/MoE stages with stored F16/uploaded
F32 roles, Q8 projections, independently calculated per-stage/full totals, Q8-off,
empty-range and missing-tensor checks. Six compiled host tests passed in 0.08s:
weight plan, memory policy, GLM server, server capabilities, engine options and
serve options. Engine build succeeded with existing compiler warnings.
Independent review found no blocking issues in the shared pass or test fixture.

Actual six-shard GLM metadata projection, without GPU initialization or weight
payload reads: stage 0 = 5,867,203,944 bytes / 5.46426 GiB; stage 1 =
4,713,733,520 bytes / 4.39001 GiB.

Before the live test, xpu-smi showed normal physical B70s and only 26/34 MiB used
VRAM. Its utilization/process/temperature APIs were unsupported; intel_gpu_top
also could not read the xe engines. Dream's existing DRM fdinfo sampler and sysfs
resolved the missing checks: both discrete GPUs 0% utilization, no inference
clients, only an idle small Xorg allocation; package/VRAM temperatures 44–46 C.
The integrated GPU was running the desktop. About 225 GiB host RAM was available.
No existing ie/llama-server/ollama server or active build was found before loading.

Live test used Dream's machx.serve, the same GLM Q4_K_XL six-shard selection, two
GPUs, context 32768 and the owner's sampling/thinking/effort defaults. It refused
if the port was already occupied. Process 843179 started on port 11435, passed the
previous failure point, pinned both stages, passed both VRAM alias self-tests with
zero bad pages/slots and reached serving. /props confirmed context 32768 and:

- Pinned host banks: 132,611,309,568 bytes / 123.50 GiB.
- Memory-mapped host banks: 52,867,104,768 bytes / 49.24 GiB.
- Allocated GPU expert caches: 23,146,135,552 / 24,270,340,096 bytes,
  or 21.56 / 22.60 GiB.
- xpu-smi total VRAM: about 28.09 GiB on each GPU.
- free reported about 201 GiB system RAM used and 47 GiB available. Swap was
  almost fully used, compared with 5.2 GiB before loading; other workload
  responsiveness and sustained memory pressure were not benchmarked.

One bounded local prompt, temperature 0 and thinking disabled for this request,
asked 2 + 2 and returned exactly 4 with HTTP 200 and finish_reason stop. Server
thinking/max-effort defaults were unchanged. This 27-input/1-output-token check
is functional evidence, not a quality/throughput benchmark. Dream's actual
residency_summary read and formatted the live /props data successfully.

Evidence: /tmp/machx-weight-plan-fix/{red.log,build.log,ctest.log,actual-plan.log,
preflight.json,live-load.json,live-load.log,live-props.json,live-response.json,
gpu0-loaded.json,gpu1-loaded.json}. Executable SHA256:
29806470f375b9b17a703928f9905707897fc4917c9aae82e3de379562883bab.
Engine graph queries did not find relevant indexed symbols; focused source reads
were used. The Dream sampler was discovered/read through the graph.
Full Dream suite and long-context/performance tests were not run; no Dream code
changed. Tracking passed: 14 dated records and all four session-changed Dream paths covered.

## Unfinished work

The reported startup refusal is fixed and the same model is running. Host residency
is explicitly partial; 49.24 GiB remains reclaimable mmap-backed banks and may read
from disk. GPU caches duplicate selected host experts. These counters do not prove
all model weights are uniquely resident. Long-context quality, sustained speed and
owner acceptance remain open. No engineering worker or build is still running.

## Next steps

Use dream local to attach to the existing GLM server without another load. Server
PID 843179 is deliberately left running on 127.0.0.1:11435 for the owner; Dream's
existing attach path does not unload a server it did not start. No other GPU process
was stopped. Model shutdown still follows owner authorization.

Recovery files in /tmp/machx-weight-plan-fix/before and ie.before preserve the
pre-fix state. That executable has the known weight-budget defect, so rollback
alone does not solve this launch error. Compare current files to stage before any
restore; preserve later work and do not reset the dirty engine repository. Keep
backups until owner validation. Remaining DREAM-026 work is broader qualification,
not another immediate restart.

## Files changed

- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/HISTORY.md`
- `docs/project/updates/2026-09-07-glm-weight-plan.md`
