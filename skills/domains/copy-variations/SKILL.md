---
name: copy-variations
description: "Generate distinct copy alternatives against a brief, score them on stated criteria and recommend a shortlist to test."
---

# Copy variations

Use this when the owner needs options for a headline, tagline, product description, call to action, subject line or
ad, and wants a reasoned shortlist rather than a pile.

## Method

1. Pin the brief: the audience, the single message, the action wanted, the channel and its limits (characters, lines),
   the voice, and words that must or must not appear. Read the brief or voice guide with `read_file`; ask with
   `ask_user_input` for what is missing.
2. Choose angles before writing: for example the benefit, the problem, a number, a question, social proof, contrast,
   curiosity. Different angles give real alternatives; synonyms do not.
3. Write two or three versions per angle, within the length limit. Count characters with `run_bash` when the limit
   is strict.
4. Score every version against criteria agreed with the brief: clarity at a glance, relevance to the audience, fit
   with the voice, distinctiveness, and truthfulness of every claim. Use a simple 1-5 scale and keep the table.
5. Shortlist three that differ from each other, and suggest how to test them (which to pair, what to measure).

## Output

A table saved with `write_file`: angle, text, character count, scores, notes. Then the shortlist with a sentence each
on why, and any claims that need checking before use.

## Checks

- Every version fits the length limit and includes required terms.
- No claim appears that the product cannot support.
- The shortlist covers different angles.

## Pitfalls

- Twenty variations of one idea.
- Clever lines that hide the message.
- Superlatives and guarantees nobody can back up.

Limits: scoring is a judgement aid; real results come from testing with the audience.
