---
name: outreach-draft
description: "Draft a tailored first message and two follow-ups for a prospect, grounded in their situation, for the owner to review and send."
---

# Outreach draft

Use this when the owner wants to contact a prospect or re-engage a quiet account and needs messages that sound like a
person who did their homework. Dream drafts; the owner reviews and sends.

## Method

1. Gather the inputs: who the recipient is (role, company), the reason to write now (a trigger from account research,
   a referral, an event), what the owner offers and the single next step wanted (a short call, a reply, a document).
   Read any brief with `read_file` or `library_read`; ask with `ask_user_input` if the reason to write is missing.
2. Pick one angle that links the trigger to a likely problem. One message, one idea.
3. Write the first message: a subject line under eight words; an opening that refers to their situation, not to the
   sender; one or two sentences on the problem and how others handled it; a specific, easy next step. Under 120 words.
4. Write two follow-ups, each adding something new (a relevant example, a short resource, a different angle) rather
   than "just checking in". Space them several business days apart and say so.
5. Check the facts in every sentence against the sources. Remove claims about results you cannot support.
6. Save the drafts with `write_file`, or show them for editing.

## Output

For each message: subject, body, the fact it relies on with its source, and the suggested send day. Offer one
alternative subject line.

## Checks

- Each message names the recipient's situation in the first two lines.
- One call to action, and it is easy to say yes to.
- No invented familiarity, fake urgency or misleading "Re:" subjects.

## Pitfalls

- Talking about the product before the problem.
- Templates with visible placeholders or the wrong company name.
- Follow-ups that repeat the first message louder.

Limits: Dream does not send messages. Sending, and compliance with the owner's email and marketing rules, stay with the
owner.
