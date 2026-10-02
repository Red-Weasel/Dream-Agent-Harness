---
name: story-development
description: "Develop a story from an idea: premise, characters with wants and flaws, structure beats, and scene list, before drafting prose."
---

# Story development

Use this when the owner has an idea for a story (a novel, a short story, a script, a game narrative) and wants a
solid shape before writing scenes.

## Method

1. Capture the idea and the constraints: form, length, audience, genre and tone, and anything the owner already knows
   must happen. Ask with `ask_user_input` for the two or three answers that change the most.
2. Write the premise in one or two sentences: who wants what, what stands in the way, and what is at stake if they
   fail. Offer two or three versions that pull in different directions and let the owner choose.
3. Build the main characters: what each wants, what they need (often different), a flaw that makes the want harder,
   and how they change or refuse to. Give the opposition its own reasons.
4. Lay out the structure as beats: the opening situation, the event that disturbs it, the commitment, rising
   complications, the midpoint turn, the low point, the climax and the new situation. Adapt the pattern to the form
   rather than forcing it.
5. Break beats into a scene list: for each scene, whose point of view, what they want in the scene, what goes wrong,
   and what has changed by the end.
6. Keep the story bible in one file with `write_file` and update it as choices are made; `remember` the owner's
   stated preferences if they want them kept across sessions.

## Output

A development document: premise options and the chosen one, character sheets, beat outline, scene list, open
questions, and a short sample passage in the intended voice if asked.

## Checks

- Every scene changes something; scenes that do not are cut or merged.
- The climax depends on a choice by the protagonist, not luck.
- Character wants and the stakes are clear by the end of the opening beats.

## Pitfalls

- Worldbuilding or backstory that never reaches the page.
- A passive protagonist things happen to.
- Copying the plot of a well-known work too closely; borrow patterns, not specifics.

Limits: structure is a tool; the owner's taste decides when to break it.
