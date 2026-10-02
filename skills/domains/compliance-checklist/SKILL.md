---
name: compliance-checklist
description: "Turn a regulation, standard or policy the user supplies into a checklist of testable requirements with evidence to collect."
---

# Compliance checklist

Use this when the owner has a specific text (a regulation, a standard, a customer's security questionnaire, an internal
policy) and needs to know what it requires of them in practical, checkable terms.

## Method

1. Work from the text supplied. Read it with `read_file` or `library_read`; if only a name is given, fetch the
   official version with `web_search` and `browse` and record its version and date. Do not work from memory.
2. Establish scope: which parts apply to the owner's situation (role, size, activities, locations). Ask with
   `ask_user_input` for facts that decide applicability, and record the answers.
3. Extract each obligation as one line: the requirement in plain words, the source section, whether it is mandatory or
   recommended, and who in the organisation would own it.
4. For each obligation, write how to test it and what evidence shows it is met (a document, a setting, a record, a
   log). Where the text is vague, say so and note the interpretation used.
5. If the owner describes their current state, mark each item met, partly met, not met or unknown, with the reason.

## Output

A checklist saved as CSV or Markdown with `write_file`: ID, section, requirement, applies (yes, no, check), owner,
test, evidence, status, notes. Add a summary of gaps and a list of interpretations to confirm. `open_for_print` gives a
printable version.

## Checks

- Every line cites its section; nothing is added that the text does not require (good ideas go in a separate list).
- Scope decisions are recorded with their reasons.
- The version and date of the source text are on the checklist.

## Pitfalls

- Summarising sections instead of extracting obligations.
- Treating "should" guidance as mandatory, or the reverse.
- Marking an item met without naming the evidence.

This is document review support, not legal advice; a qualified lawyer in the relevant jurisdiction should confirm anything you rely on.
