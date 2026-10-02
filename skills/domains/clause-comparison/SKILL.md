---
name: clause-comparison
description: "Compare two versions of a contract or clause: list every change, what it does in effect, and which side it favours."
---

# Clause comparison

Use this when a counterparty returns a marked-up draft, when two versions of an agreement must be reconciled, or when
the owner wants a clause compared with their standard wording.

## Method

1. Identify both versions: file names, dates, who produced each, and which one is the baseline. Read them with
   `read_file` or `library_read`.
2. Produce a mechanical comparison first so nothing is missed: normalise whitespace and numbering, then run a word- or
   sentence-level diff through `run_bash`. Do not rely on tracked changes alone; they can be incomplete.
3. Group the raw differences into meaningful changes: a deleted qualifier ("reasonable"), a changed number or period,
   a new obligation, a moved clause, a changed definition.
4. For each change, explain its effect in plain words and who it favours. Follow changed definitions to every clause
   that uses them.
5. Mark each change as accept, reject or discuss against the owner's stated position, and draft a counter-proposal
   where needed.

## Output

A comparison table saved with `write_file`: location, old text, new text, effect, favours, recommendation. Put the
changes that shift risk or money first. Add a short list of changes that are only editorial.

## Checks

- The count of changes in the table matches the mechanical diff (editorial ones included in their own list).
- Changed definitions are traced to where they apply.
- Quotes are exact.

## Pitfalls

- Missing a change because the clause was moved as well as edited.
- Overlooking small words that carry weight: "may" and "shall", "including" and "limited to".
- Accepting "clean-up" edits without reading them.

This is document review support, not legal advice; a qualified lawyer in the relevant jurisdiction should confirm anything you rely on.
