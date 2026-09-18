---
name: coding
description: Implement, debug or review code and interactive interfaces in the existing workspace.
---

# Coding

1. Identify the requested behavior and one concrete check. Use `list_dir`, `grep` and `read_file` to find the existing implementation and nearby tests. Preserve unrelated edits; inspect a file before changing it.
2. For a defect, capture the failing input, error and expected result. Trace that path and change the cause. For a feature, reuse the existing architecture and make the smallest complete change.
3. Use `str_replace_edit` for exact edits, `write_file` for new files and `run_bash` for the project's existing checks. Read errors and exit status. After an unchanged failure, change the hypothesis or inspect a dependency instead of repeating the call.
4. Verify the actual trigger and affected behavior. A successful edit is not a passing test. Report changed files, observed checks and any remaining blocker.

For a webpage, prototype, wireframe or design system, read [Studio interfaces](references/studio.md) with `skill_file` only when needed. Deliver a usable file through `show_to_user` or `done` after checking it.

Read [worked examples](references/examples.md) with `skill_file` when a concrete tool sequence would help.
