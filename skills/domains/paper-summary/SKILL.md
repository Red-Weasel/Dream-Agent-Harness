---
name: paper-summary
description: "Summarise a research paper: its question, methods, main claims with the evidence for each, limitations and how far to trust it."
---

# Paper summary

Use this when the owner hands over a paper (or a link to one) and wants to know what it found and whether it holds up,
without reading it all.

## Method

1. Get the full text: `read_file` for a local PDF, `browse` for an open-access page. If only the abstract is
   available, say so and limit the summary to it. Note the venue, year and whether it is peer reviewed.
2. Read in this order: abstract, figures and tables, methods, results, then discussion. Figures often show what the
   text overstates; look at image figures with `media_read` or `see` when needed.
3. Extract the question, the design (sample, setting, measures, comparison) and the main claims. For each claim, find
   the result that supports it: the number, the interval, the figure or table.
4. Assess: sample size and selection, controls, confounding, multiple comparisons, missing data, whether conclusions go
   beyond the data, conflicts of interest and funding as disclosed.
5. Place it in context if asked: a quick `web_search` for replications, comments or retractions.

## Output

A summary of about a page, saved with `write_file` if wanted:

- Citation and link.
- The question and the answer in two sentences.
- Methods in brief.
- Main claims, each with its supporting result.
- Limitations stated by the authors and ones they did not state.
- A trust rating (strong, moderate, weak) with the reason.

## Checks

- Every number quoted matches the paper.
- Claims are the paper's, not the press release's.
- The summary separates the authors' conclusions from your assessment.

## Pitfalls

- Summarising the abstract only while implying the paper was read.
- Confusing statistical significance with a large or important effect.
- Missing a retraction or correction.

Limits: one paper is one piece of evidence; say so when the owner is about to rely on it alone.
