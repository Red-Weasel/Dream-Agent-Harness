# Spreadsheet handling

`read_file(path="book.xlsx", sheet="Revenue", limit=20000)` reads a named sheet.
The workbook output includes cell addresses, raw values and formulas. A cached
formula value is labeled separately and may be absent or stale. Reading does not
recalculate. Check dates, number formats and units against the user's meaning.

For CSV/TSV, inspect delimiter, encoding, headers and quoted fields. Use Python's
csv module rather than splitting lines on commas. For JSON, inspect whether the
root is an object or array and keep null distinct from an empty string or zero.

Use an installed spreadsheet library for XLSX editing through `run_bash`; verify
its availability first. Preserve formulas, styles, sheet names, external links and
macros that the chosen library actually supports. Do not silently discard a macro
project or claim that raw ZIP/XML edits preserve every Excel feature. Save a new
output and reopen it. If no suitable editor exists, CSV is a supported data export,
not an equivalent replacement for a styled/formula workbook.

Keep a reproducible transformation and validate source/result row counts, group
totals, missing/duplicate handling and one independent example. Flag text beginning
with spreadsheet formula prefixes when exporting untrusted values for spreadsheet
opening; use an explicit text-safe export appropriate to the reader. Do not evaluate
arbitrary cell text as Python or shell code.
