---
name: verifying
description: Verify task outcomes or recover when stuck, a command failed, or work was interrupted.
---

# Verification and recovery

1. Compare the user's requested outcome with what is actually present. Use `read_file`, relevant project checks through `run_bash`, or the appropriate preview/tool result. Inspect the output and exit status before making a completion claim.
2. Match evidence to the claim: a file edit proves a write; a passing test checks its assertions; opening a service proves connectivity. None proves untested quality, speed or production readiness. State which checks ran and which did not.
3. If blocked, classify the result: missing path/tool, invalid arguments, permission refusal, unavailable service, or a failed operation. Read the error without suppressing it. Test one cause or use another suitable tool. An identical retry without changed input or state adds no evidence.
4. After a timeout or interruption, inspect existing output, job status and `read_notes` before replaying writes or external actions. Use `note` to preserve completed work, unresolved effects and the exact next step.
5. Finish by naming the result, supporting checks and any remaining gap. Continue independent useful work when one part is blocked. Do not substitute placeholders, hidden errors or bypassed permissions for completion.

Read [worked examples](references/examples.md) with `skill_file` when a concrete tool sequence would help.
