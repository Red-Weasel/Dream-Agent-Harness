---
name: thinking-out-loud
description: "Use when the user is thinking out loud: musing, a half-formed idea, 'what if', 'not sure if it's worth it', 'don't take this as instructions yet'. Untangle it with them; build nothing until they say go."
---

# Thinking out loud

The user senses an idea matters, but it came out as a ramble of moving parts. Take it apart with them, turn by
turn, until they have clarity and a decision. Treat every message as thinking, not instructions: do not edit files,
write code or start work until they clearly say go. Looking facts up (`read_file`, `list_dir`, `grep`) is part of the
job.

## First reply

```
You raised several questions and topics:

<Topic, in their words>:
- <one-sentence answer to something they asked, or a short correction>

Question 1: In one sentence, what is your idea about?
Question 2: What was your first thought, or what made you start thinking about this?
```

Ask exactly those two questions on the first reply.

## Every reply after

- One line: the idea, in their one-sentence framing.
- What they said or decided, as short bullets under clear labels. Answer their direct questions first, honestly.
- `What exists today:` checked facts only. Look them up; leave out anything you could not confirm and raise it as
  an open question instead.
- Two plain questions that each offer the concrete choices. If their wording has two readings, ask which they mean.
- Use their names for things from the moment they give them.

## Closing

When the decisions are mostly made, send: `Here's where we landed.` then The idea / Decided / Still open (each with
your recommended default, plus an honest flag on any weakness) / Next step. Ask whether that is where they want it to
land. When they say go, the recap is the plan.

A worked conversation is in `references/example.md`; read it when unsure of the shape.
