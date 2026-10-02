---
name: experiment-design
description: "Design an experiment: hypothesis, variables, controls, randomisation, sample size and analysis plan written down before data."
---

# Experiment design

Use this when the owner wants to test a change, compare treatments or measure an effect, in a lab, in software (an
A/B test) or in the field, and wants the result to be believable.

## Method

1. State the hypothesis as a testable prediction, with the primary outcome and the smallest effect that would matter
   in practice.
2. Define variables: the one manipulated, the outcome measured (how, when, with what instrument), and those held
   constant or recorded as covariates.
3. Choose the design: between or within subjects, factorial, before-after with control. Decide the control condition
   and, where possible, blinding.
4. Plan assignment: randomisation method, stratification or blocking for known strong factors, and how allocation is
   concealed.
5. Estimate the sample size with a power calculation through `run_bash` (state the effect size, variance estimate,
   significance level and power used, and where the estimates came from). Add a margin for dropouts.
6. Write the analysis plan before collecting data: the primary test, secondary analyses, how missing data and outliers
   are handled, and the stopping rule.
7. List threats to validity (confounding, measurement error, learning effects, peeking) and how the design limits them.

## Output

A protocol saved with `write_file`: hypothesis, outcomes, variables, design, assignment, sample size calculation,
procedure, analysis plan, threats and mitigations, and a data recording template.

## Checks

- The primary outcome and analysis are fixed before data collection.
- The power calculation's inputs are sourced and the arithmetic is reproducible.
- Someone else could run the procedure from the document.

## Pitfalls

- Several outcomes with no primary one, then reporting whichever worked.
- Stopping early when the result looks good.
- Randomising at one level (for example classes) and analysing at another (students).

Limits: studies involving people or animals may need ethics approval and consent; check before starting.
