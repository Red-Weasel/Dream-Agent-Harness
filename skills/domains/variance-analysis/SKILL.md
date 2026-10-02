---
name: variance-analysis
description: "Explain budget versus actual (or period versus period): size each variance, split it into drivers and flag what needs action."
---

# Variance analysis

Use this when actual results differ from a budget, forecast or prior period and someone needs to know why, which
differences matter and what to do next.

## Method

1. Agree the basis: which two sets of numbers, the period, the level of detail and a materiality threshold (for
   example 5 % and a fixed amount; both must be exceeded). Read the inputs with `read_file`.
2. Put both sets side by side in a script or table through `run_bash`: line item, plan, actual, difference, difference
   as a percentage. Mark each as favourable or unfavourable with a sign convention stated once.
3. For each material line, split the difference into drivers you can defend. Revenue: volume, price and mix. Costs:
   rate and usage, or headcount and cost per head. Timing (a cost that moved between months) is its own driver.
4. Check the drivers add back to the total difference; a remainder goes on its own line, labelled unexplained.
5. Look for explanations in the source material (orders, invoices, payroll, notes); ask the owner with
   `ask_user_input` for what the data cannot show. Keep assumptions visible.
6. Say which variances are one-off, which repeat and what the forecast should now assume.

## Output

- A headline: total difference and the two or three drivers behind most of it.
- A table of material variances with driver split, favourable or unfavourable, and the explanation or open question.
- Actions: what to investigate, what to change in the forecast, who needs to know.
- A bar chart of drivers with `chart_display_v0` when it helps; a file via `write_file` for the full table.

## Checks

- Driver pieces sum to each line's variance; line variances sum to the total.
- Signs are consistent across revenue and cost lines.
- Nothing below the threshold is presented as significant.

## Pitfalls

- Explaining every line and hiding the few that matter.
- Calling a timing difference a saving.
- Treating a budget error as a performance problem without saying so.

This is analysis support, not financial, tax or investment advice; have figures that drive a decision checked by a qualified professional.
