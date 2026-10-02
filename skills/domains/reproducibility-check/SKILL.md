---
name: reproducibility-check
description: "Rerun an analysis or experiment from its materials, compare results with the reported ones and log every difference."
---

# Reproducibility check

Use this when the owner wants to know whether a result can be regenerated: their own earlier analysis, a colleague's
notebook, or a paper's published code and data.

## Method

1. Collect the materials: code, data, environment description, and the exact results to compare against (tables,
   figures, numbers). Record versions or commit hashes. For a repository, `github_get_tree` and `github_read_file`
   help to survey it before copying anything.
2. Record the environment you run in: language and package versions, operating system, hardware where it matters.
   Use a fresh, isolated environment inside the workspace through `run_bash`.
3. Follow the documented steps exactly the first time, without fixing anything. Log each step's command, output and
   any error in a `note`.
4. When a step fails, make the smallest change that gets past it, record it, and continue. Distinguish missing
   materials from errors in them.
5. Compare the regenerated outputs with the reported ones: numbers with a stated tolerance, figures side by side (look
   with `see`). Classify differences as exact match, within tolerance, small difference, or different conclusion.
6. Where results differ, try to find the cause: random seeds, package versions, data versions, undocumented steps.

## Output

A reproducibility report saved with `write_file`: materials and versions, environment, steps with results, changes
needed, a comparison table of reported against reproduced values, and a verdict (reproduced, partly reproduced, not
reproduced) with the reasons.

## Checks

- The run started from the published materials, not from a patched copy.
- Every change is listed with why it was needed.
- Tolerances were set before comparing.

## Pitfalls

- Fixing code quietly and then calling the result reproduced.
- Comparing against a different version of the data.
- Ignoring randomness: run stochastic steps with several seeds.

Limits: reproducing a result checks the computation, not whether the method was right.
