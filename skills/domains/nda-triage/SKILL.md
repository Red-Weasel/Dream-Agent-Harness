---
name: nda-triage
description: "Sort an incoming non-disclosure agreement into standard, needs edits or escalate, with the reasons and the edits to request."
---

# NDA triage

Use this when a confidentiality agreement arrives and the owner needs a quick, reasoned call on whether it can be
signed as is.

## Method

1. Confirm the basics with `read_file`: parties, whether it is mutual or one-way (and in which direction), the purpose,
   the governing law and the signature block.
2. Check the core terms against a sensible baseline (the owner's own template if one exists in the Library; find it
   with `library_search`):
   - definition of confidential information: bounded, with the usual exclusions (already known, public, independently
     developed, received from a third party);
   - permitted use limited to the stated purpose; permitted disclosure to advisers and staff who need to know;
   - duration of the obligation and of the agreement; return or destruction of material;
   - compelled disclosure handled with notice where lawful.
3. Flag terms that go beyond confidentiality: non-solicitation, non-compete, exclusivity, assignment of ideas or
   intellectual property, liquidated damages, one-sided indemnities, unusual jurisdiction. Check for a residuals
   clause (a right to use what people remember unaided): it can hollow out the protection.
4. Decide the category. Standard: baseline met, nothing extra. Needs edits: fixable points, list them. Escalate:
   terms outside a confidentiality agreement's normal scope, or anything the owner has marked as a red line.

## Output

A short triage note: category, the three most important reasons, a table of issues with clause, concern and
requested edit, and a one-line recommendation. Save it with `write_file` if asked.

## Checks

- Direction is right: for a one-way NDA, confirm who is disclosing.
- Every flagged term quotes the clause.
- The category follows from the listed issues.

## Pitfalls

- Signing a "simple NDA" that contains a non-solicit or IP clause.
- Missing that the definition covers everything ever disclosed, forever.
- Ignoring the purpose clause, which limits what the information may be used for.

This is document review support, not legal advice; a qualified lawyer in the relevant jurisdiction should confirm anything you rely on.
