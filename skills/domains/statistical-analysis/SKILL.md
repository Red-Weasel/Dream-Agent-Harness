---
name: statistical-analysis
description: "Choose the right statistical test, check its assumptions, run it reproducibly and report effect sizes with uncertainty."
---

# Statistical analysis

Use this when a dataset needs a statistical answer: a difference between groups, a relationship, a trend, or a model,
with results someone can check.

## Method

1. Restate the question and the analysis plan if one exists. Identify the outcome type (continuous, count, binary,
   time-to-event), the unit of analysis, and whether observations are independent, paired or clustered.
2. Inspect the data with a script through `run_bash`: counts, missing values, ranges, distributions and obvious
   errors. Record every cleaning step in the script, never by hand.
3. Choose the method that fits the question and the data structure, and check its assumptions (normality of residuals,
   equal variances, linearity, independence). If they fail, use a robust, non-parametric or better-suited model and
   say why.
4. Run the analysis in the script. Report the estimate, a confidence interval and the test result; for several
   comparisons, correct for multiplicity or say that the analysis is exploratory.
5. Plot what supports the conclusion: distributions by group, the fitted relationship, residuals. Use
   `chart_display_v0` for simple charts or save image files from the script and check them with `see`.
6. Interpret in plain words, including what the analysis cannot show (causation from observational data, results
   outside the sampled range).

## Output

- A results summary: estimate, interval, test, sample sizes.
- The script and its output saved with `write_file` so the numbers can be rerun.
- Figures, the assumptions checked and their results, and limitations.

## Checks

- Numbers in the text match the script's output exactly.
- The unit of analysis matches the design.
- Effect sizes are reported, not only p-values.

## Pitfalls

- Trying tests until one gives a small p-value.
- Treating repeated measures as independent.
- Dropping outliers without a rule set in advance.

Limits: the analysis answers the question asked of the data collected; it does not repair a flawed design.
