---
name: proposal-builder
description: "Assemble a proposal from discovery notes: the problem in the client's words, scope, options with pricing, timeline and next steps."
---

# Proposal builder

Use this when a prospect has asked for a proposal or quote and the discovery notes are in hand.

## Method

1. Read the notes and earlier messages (`read_file`, `library_read`). Extract the client's stated problem, goals,
   constraints, decision makers, deadline and any budget signal. If a key fact is missing, ask with `ask_user_input`
   rather than guess.
2. Restate the problem and the outcome the client wants, in their words, in one paragraph. This is what they will
   check first.
3. Define scope: what is included, what is not, assumptions, and what the client must provide. Exclusions prevent
   more disputes than inclusions.
4. Offer two or three options that differ in outcome, not only in size, each with price, timeline and what it
   delivers. Mark the recommended one and why.
5. Add proof that fits (a comparable piece of work), the plan with milestones, terms that matter (payment schedule,
   validity date, change handling) and a clear next step.
6. Produce the document: Markdown with `write_file`, a deck with `gen_pptx`, or a printable page with
   `open_for_print`. Keep it short enough to read in ten minutes.

## Output

A proposal with: summary, problem and goals, options table (scope, price, timeline), recommended option, plan and
milestones, assumptions and exclusions, terms, next step. List open points for the owner separately.

## Checks

- Every number (price, dates, quantities) is consistent across the document.
- The scope matches what was discussed; nothing promised that the owner has not agreed.
- The client could sign or reply from the last page.

## Pitfalls

- Leading with company history instead of the client's problem.
- One option only, turning the decision into yes or no.
- Vague deliverables that invite scope creep.

Limits: pricing, terms and legal wording are the owner's decisions; have contract terms reviewed by the right person.
