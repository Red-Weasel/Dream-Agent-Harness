---
name: debugging
description: "Root cause before any fix: reproduce, read the error, one hypothesis at a time; three failed fixes stops."
---

# Debugging

No fix without a root-cause investigation first. A fix that addresses a symptom is a failure, even when
the symptom goes away.

1. **Investigate before touching code.**
   - Read the error completely: message, stack trace, line numbers, exit status. It often names the cause.
   - Reproduce it reliably with `run_bash` (the project's own test or a minimal command). If it does not
     reproduce, gather more evidence; do not guess.
   - Check what changed: `git log`, `git diff`, new dependencies, config, environment.
   - In a multi-component path, add temporary logging at each boundary (what enters, what leaves), run
     once, and read where it breaks. Remove the instrumentation when done.
   - Trace the bad value backwards to where it originates; fix at the source, not where it was noticed.
2. **Find the pattern.** Locate similar code that works. Read the reference implementation completely.
   List every difference, however small. Note the dependencies, settings and assumptions involved.
3. **One hypothesis, one change.** State it: "X is the root cause because Y." Make the smallest change
   that tests it. Confirmed → step 4. Not confirmed → a new hypothesis; never stack fixes.
4. **Fix with a failing test.** Write the test that reproduces the bug (the project's framework, or a
   one-off script) and watch it fail; then the single fix; then watch it pass and run the surrounding
   tests. Use `str_replace_edit` for the change. Never weaken or delete a test to make it pass.
5. **Three failed fixes = stop.** If each fix reveals a new problem elsewhere, or needs a large refactor,
   the design is the bug. Say so and bring the evidence to the user instead of a fourth attempt.

Say "I don't understand X" when it is true. Ask, research, or instrument — do not pretend.

Signals you are guessing: "quick fix for now", "just try changing X", "it's probably X", "skip the test,
I'll verify by hand", "one more attempt" after two failures.
