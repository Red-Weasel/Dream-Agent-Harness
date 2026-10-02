---
name: contract-review
description: "Review a contract clause by clause against the owner's position; list issues by risk with suggested wording."
---

# Contract review

Use this when the owner has a draft agreement (their own or the other side's) and wants to know what it commits them
to, where it departs from what they need, and what to ask for.

## Method

1. Establish the frame before reading closely: which party the owner is, the deal in one sentence, the governing law
   stated in the document, and the owner's must-haves and deal-breakers. If the position is unclear, ask with
   `ask_user_input`. Check `library_search` for earlier versions or the owner's standard terms.
2. Read the whole document with `read_file` once for structure: parties, definitions, obligations, payment, term and
   termination, liability, indemnities, intellectual property, confidentiality, data, disputes, boilerplate. List any
   documents it incorporates by reference (terms, policies, schedules, order forms) and the order of precedence when
   they conflict; get and read those too.
3. Go clause by clause. For each, record: what it says in plain words, who it favours, how it interacts with the
   definitions and other clauses, and whether it meets the owner's position.
4. Pay particular attention to: caps and exclusions of liability, indemnities, automatic renewal and notice periods,
   termination rights, exclusivity, assignment, payment and late fees, ownership of work product, and anything that
   survives termination.
5. Look for what is missing (no cap, no termination for convenience, no data protection terms) as well as what is
   there.
6. Draft suggested wording for each issue you would negotiate, keeping the document's defined terms.

## Output

An issues list saved with `write_file`: clause reference, summary, risk (high, medium, low) and why, the owner's
position, suggested change or question. Then a short summary of the top issues and any missing clauses. Quote the
original text you rely on.

## Checks

- Every issue cites a clause number and quotes the relevant words.
- Defined terms are read with their definitions.
- Suggested wording fits the document's style and terms.

## Pitfalls

- Reviewing the clause text while ignoring a definition that changes its meaning.
- Treating every departure from a template as a problem.
- Assuming a rule from one jurisdiction applies everywhere.

This is document review support, not legal advice; a qualified lawyer in the relevant jurisdiction should confirm anything you rely on.
