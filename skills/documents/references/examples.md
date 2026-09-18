# Read a specific source range

For a request about pages 11 through 20, keep the page selection explicit.

```text
read_file {"path":"report.pdf","page_start":11,"page_count":10,"limit":20000}
```

If output is truncated, preserve the same page range and use the returned offset
for the next call. Cite the actual page markers beside facts. Do not infer that a
missing fact is absent from the whole PDF after reading only these pages.

For a DOCX brief, inspect text and save the requested summary separately.

```text
read_file {"path":"brief.docx","limit":12000}
```

If the returned text states `Project: Orion`, `Budget USD: 12500`, and
`Deadline: 2026-10-31`, those values can support a summary citing brief.docx.
Write only inspected facts, then reopen the saved summary. This verifies its
content; page layout requires a separate rendered check.
