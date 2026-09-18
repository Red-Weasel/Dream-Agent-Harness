---
name: data-analysis
description: Inspect, clean, calculate and explain CSV, XLSX and other structured datasets or charts.
---

# Data analysis

1. Inspect the source with `read_file`. For XLSX, select `sheet` by its exact name; for long output, use `offset`/`limit`. Identify headers, row counts, units, dates, missing values and duplicates before calculating.
2. Write down the metric and denominator. Keep raw input unchanged. Use `run_bash` with Python's csv/json/statistics modules or an already installed spreadsheet library for reproducible transformations. Treat formula strings as data; never execute dataset text as code.
3. Validate one small example manually, then check totals, ranges, row counts and excluded records. A cached spreadsheet formula result may be stale. Do not claim recalculation unless a spreadsheet engine actually ran it.
4. Save useful results with `write_file` or the format-aware script. Reopen the result and compare key values to the calculation. Label chart axes, units, time range and missing data; cite the input and disclose uncertainty.

Read [spreadsheet handling](references/spreadsheets.md) with `skill_file` for XLSX edits or export choices. Summarize the decision-relevant result, then provide the file. Do not silently replace missing values with zero.

Read [worked examples](references/examples.md) with `skill_file` when a concrete tool sequence would help.
