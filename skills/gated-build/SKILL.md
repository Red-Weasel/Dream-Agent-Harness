---
name: gated-build
description: "Phased build-and-gate loop: pass criteria first, a fresh verifier per phase, fix exactly what it names."
---

# Gated build

A long build without checkpoints drifts. This loop makes drift cheap to catch: plan → build a phase →
an independent gate verifies it → fix or advance. Alpha is the plan, Omega is the finished build that
passed every gate.

1. **Plan into phases.** Split the work into phases sized so no unreviewed stretch runs long. For EACH
   phase write its pass criteria BEFORE building: concrete, checkable statements (tests that must pass,
   behaviors that must hold, constraints that must not break). Record them with `note` or in
   `docs/plans/` so the gate judges the contract, not vibes.
2. **Build the phase.** Work continuously inside it (the `coding` workflow: tests first, exact edits).
   No gate-skipping, no merging two phases to dodge a checkpoint.
3. **The gate.** Spawn a FRESH verifier that did none of the building — `fork_verifier_agent` when it is
   available, otherwise a `task` subagent with a read-and-verify prompt. Give it: the phase's pass
   criteria, the diff or changed files, and how to run the relevant checks itself. It must verify, not
   read: run the checks, probe the criteria adversarially, and return `PASS` or `FAIL` with numbered
   findings (file, what is wrong, which criterion it fails). Gates never edit the build.
4. **Fail → fix → same gate.** Fix exactly what the gate named plus any real root cause under it, then
   re-present to the SAME gate with the fix. Never argue a gate into a pass, never weaken a test or a
   criterion, never swap verifiers to shop for a friendlier verdict. Three FAILs at one gate = stop the
   loop and bring the findings and the disagreement to the user.
5. **Pass → next phase, new gate.** Fresh eyes every checkpoint.
6. **Omega.** After the last gate: the full verification the project requires (whole test suite, docs
   updated), then report phases, gate verdicts — including the failures and what they caught — and the
   final state. Preserve intermediate results throughout so a stopped loop loses nothing.
