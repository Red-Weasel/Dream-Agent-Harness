---
name: account-research
description: "Brief a company from public sources before a sales conversation: what it does, recent changes, likely needs, who to talk to."
---

# Account research

Use this before a first meeting, an outreach sequence or an account plan, when you need a short, sourced picture of a
company rather than a pile of links.

## Method

1. Pin down the company: legal name, website, headquarters and the division you care about. Many names are shared;
   confirm with the domain.
2. Check what the owner already knows: `recall` and `library_search` for earlier notes, meetings or proposals.
3. Collect public facts with `web_search` and `browse`, preferring the company's own pages, filings, press releases and
   job listings over aggregators: what they sell and to whom, size and growth signals, leadership, recent news
   (funding, launches, acquisitions, layoffs, new markets), technology or suppliers they mention.
4. Turn facts into hypotheses about needs: what problem might each change create, and how the owner's offer relates.
   Mark each hypothesis as a guess to test in conversation.
5. Identify roles likely involved in a decision (from public pages and listings). Record names only as publicly
   presented in a business role; do not dig into private lives or personal contact details.
6. Save the brief with `write_file` or into the Library with `library_create` so the next conversation can build on
   it; `remember` one line about the account if the owner wants it kept.

## Output

A one-page brief: company snapshot (five lines), recent developments with dates and links, three hypotheses about
needs, questions to ask, possible roles to involve, and sources.

## Checks

- Every fact has a link and a date; anything older than a year is labelled.
- Hypotheses are separated from facts.
- The brief fits on a page and answers "why talk to them now?".

## Pitfalls

- Copying marketing claims as facts.
- Mixing up two companies with similar names.
- Collecting personal information that has nothing to do with the business conversation.

Limits: public sources show what a company chooses to publish. Confirm needs with the customer, not the brief.
