---
name: literature-review
description: "Search, screen and synthesise the literature on a question, with a record of what was searched and citations for every claim."
---

# Literature review

Use this when the owner needs to know what is already known about a question: the main findings, where studies agree
or conflict, and where the gaps are.

## Method

1. Write the question and its scope: population or system, intervention or factor, comparison, outcomes, time range,
   study types to include. Put inclusion and exclusion criteria in a `note` before searching.
2. Search systematically with `web_search` and `browse`: scholarly indexes, publisher pages, preprint servers. Record
   each search (source, terms, date, number of results) so it can be repeated. Check the Library with
   `library_search` for papers the owner already holds.
3. Screen titles and abstracts against the criteria, then read the full text of those that pass (`read_file` for
   local PDFs). Log why each excluded paper was excluded.
4. Extract from each included study: design, sample, measures, main result with effect size and uncertainty, and
   limitations. Keep the extraction as a table.
5. Synthesise by theme or outcome, not paper by paper. Weigh by design and size; say where evidence is thin, conflicting
   or from one group only.

## Output

A review saved with `write_file` (and in the Library with `library_create` if wanted): question and criteria, search
log, a flow count (found, screened, included), an evidence table, a synthesis with citations beside each claim, gaps,
and limitations of the review itself.

## Checks

- Every cited paper was actually read at least to the level the claim needs.
- Citations are complete and resolve (DOI or stable link).
- Effect sizes and sample sizes are quoted, not only "significant".

## Pitfalls

- Stopping at the first page of results, or only including papers that agree.
- Treating preprints and peer-reviewed papers the same without saying so.
- Citing a paper for a claim found only in its introduction.

Limits: a review by one reader without a second screener can miss or misjudge studies; say so when it matters.
