---
name: financial-statements
description: "Read and compare income statements, balance sheets and cash flow statements; explain what changed and why it matters."
---

# Financial statements

Use this when given one or more sets of accounts (a filing, an export, a spreadsheet) and asked what they show, how two
periods or two companies compare, or whether the numbers hang together.

## Method

1. Identify what you hold: entity, period end, currency, units (thousands or millions), audited or not, and which of
   the three statements are present. Open files with `read_file`; for a public company, find the original filing with
   `web_search` and `browse` rather than a summary site. Check the owner's `library_search` for earlier notes.
2. Load the figures into a small table (CSV or a script through `run_bash`) so every ratio is computed, not typed.
   Keep one row per line item and one column per period.
3. Tie the statements together before interpreting them: assets equal liabilities plus equity; net income flows to
   retained earnings and the cash flow statement; closing cash matches the balance sheet, after reconciling what
   each statement counts as cash (cash equivalents, restricted cash, overdrafts). A break means a wrong extract, a
   restatement or a missing item; find it first.
4. Compute what the question needs, showing the formula: growth, gross and operating margin, working capital days,
   leverage and interest cover, free cash flow, conversion of profit to cash.
5. Explain movements from the notes and the narrative sections: one-off items, acquisitions, accounting changes,
   currency. Separate what the documents state from your reading of it.

## Output

- A summary of three to six sentences answering the question.
- A table of the key figures and ratios by period, with units, and the formula for each ratio.
- Notable items with the page or note they come from, and open questions.
- A chart with `chart_display_v0` when a trend is the point; a file with `write_file` when the table is long.

## Checks

- The three statements tie; any difference is shown and explained.
- Units and signs are consistent (costs negative or positive, never both).
- Every figure quoted can be traced to a source line.

## Pitfalls

- Comparing periods of different length, or restated with unrestated figures.
- Treating adjusted or non-standard measures as if they were statutory.
- Reading a margin change without checking the revenue mix behind it.

This is analysis support, not financial, tax or investment advice; have figures that drive a decision checked by a qualified professional.
