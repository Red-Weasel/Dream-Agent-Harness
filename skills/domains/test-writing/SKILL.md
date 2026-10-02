---
name: test-writing
description: "Design and add tests that fail first, cover edge cases and pin the behaviour a change is meant to have."
---

# Test writing

Use this when a change needs tests, when a bug should be pinned before it is fixed, or when asked to raise coverage of
a behaviour that matters.

## Method

1. Find the project's test conventions before writing anything: `list_dir` the tests folder, `read_file` two nearby
   tests and the test configuration, and `grep` for fixtures you can reuse. Match the framework, naming and layout.
2. Write down the behaviour as cases in `update_plan` or a `note`: the normal path, the boundaries (empty, one, many,
   maximum), invalid input, failure of a dependency, and anything order- or time-dependent.
3. For a bug, write the test that reproduces it first and run it through `run_bash` to watch it fail for the stated
   reason. A test that passes before the fix proves nothing about the fix.
4. Keep each test about one behaviour. Assert on observable results, not on private details that a refactor would
   change. Use the real code path where it is fast; replace only slow or external parts (network, clock, randomness)
   and say what the replacement stands in for.
5. Add the tests with `write_file` or `str_replace_edit`, run the named test files, then run them again after the fix.
   Record both runs.

## Output

- The new or changed test files and what each test proves, one line per test.
- The failing run before the change and the passing run after it, with the exact command.
- Cases deliberately left out and why.

## Checks

- Each test fails when the behaviour it guards is broken; try breaking it once if unsure.
- Test names read as statements of behaviour.
- No test depends on another test's leftovers, on wall-clock time, or on network access unless marked.

## Pitfalls

- Mocking the very thing under test, so the test only checks the mock.
- Snapshot or golden files regenerated to make a failure go away.
- Asserting too little (status only) or too much (every field, so any change breaks it).

Limits: tests show the cases they cover. Say which risks remain untested.
