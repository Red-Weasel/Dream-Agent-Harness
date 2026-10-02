---
name: code-review
description: "Review a code change for correctness, risk and missing tests; report findings by severity with file and line."
---

# Code review

Use this when asked to review a diff, a branch, a pull request or a set of changed files. The goal is to find what
would break or mislead, not to restyle working code.

## Method

1. Establish the change: what it claims to do, the base it applies to and which files moved. Run `git diff <base>` or
   `git show` through `run_bash`, or read the files with `read_file`. For a remote repository use `github_get_tree` and
   `github_read_file`. Use `project_outline` when you need the surrounding architecture.
2. Read every changed hunk in context: open the whole function or component, and `grep` for callers of anything whose
   signature, return value or side effect changed.
3. Check, in this order: correctness (edge cases, off-by-one, null or empty input, error paths, concurrency, resource
   cleanup), contracts (callers, public interfaces, stored data formats, migrations), security (untrusted input,
   injection, secrets, permission checks), tests (does a test fail without the change and pass with it?), then clarity.
4. Run the project's own checks when they exist and are cheap: the named test files, the linter, the type checker. Say
   which ran and what they reported. Do not run anything that deploys, publishes or writes outside the workspace.
5. For each finding, confirm it by reading the code path again or with a small reproduction. Drop anything you cannot
   support.

## Output

A short verdict (ready, ready with changes, or not ready), then findings ordered by severity:

- **Blocker / Major / Minor / Nit**, `path:line`, what goes wrong, how it would show up, and a concrete fix.
- A "Checked" list: what you verified and how, and what you could not check.

Keep praise to one line. Save a long review with `write_file` and give the path.

## Checks

- Every finding names a location and a failure you can describe, not a preference.
- Suggested fixes compile in your head against the surrounding code; quote the exact lines they replace.
- Tests that the change needs are named: the input, the expected result and where the test belongs.

## Pitfalls

- Reviewing only the diff and missing a caller that now breaks.
- Reporting style disagreements as defects, or burying one real blocker among twenty nits.
- Claiming tests pass when you did not run them.

Limits: a review finds likely defects; it does not prove their absence. Leave the decision to merge with the author.
