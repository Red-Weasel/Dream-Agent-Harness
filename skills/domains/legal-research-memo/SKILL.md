---
name: legal-research-memo
description: "Research a legal question from primary sources and write a memo: question, short answer, sources, analysis, caveats."
---

# Legal research memo

Use this when the owner needs a structured, sourced answer to a legal question: what a rule says, how it has been
applied, or what the options are.

## Method

1. Frame the question precisely: the jurisdiction, the date that matters, the facts assumed, and what decision the
   answer supports. Record assumptions; ask with `ask_user_input` if the jurisdiction or key facts are unknown.
2. Find primary sources first with `web_search` and `browse`: the statute or regulation from an official site, then
   decisions that apply it, then official guidance. Use commentary to find sources, not as authority.
3. Verify each source: the current version, any amendments since, whether a decision was appealed or overruled. Keep a
   `note` table of source, citation, date, the passage relied on and how it was verified.
4. Analyse: apply the rule to the facts, address the strongest counter-argument, and say where the law is unsettled.
5. Write the memo; state confidence honestly.

## Output

A memo saved with `write_file` (and in the Library with `library_create` if the owner wants it kept):

- Question presented and assumptions.
- Short answer, two or three sentences.
- Sources, with citations and links.
- Analysis, then counter-arguments and uncertainty.
- Next steps and what a lawyer should confirm.

## Checks

- Every legal proposition has a verified source; nothing is cited that was not read.
- Quotations are exact and page or section references are given.
- The jurisdiction and date are stated at the top.

## Pitfalls

- Inventing or misremembering a citation. If a source cannot be found, say so.
- Applying one jurisdiction's rule to another.
- Presenting commentary or a blog summary as the law.

This is research support, not legal advice; a qualified lawyer in the relevant jurisdiction should confirm anything you rely on.
