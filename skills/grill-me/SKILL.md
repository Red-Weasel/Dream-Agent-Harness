---
name: grill-me
description: "Use when the user asks to be grilled, or wants a plan, design or decision challenged before anything is built."
---

# Grill me

Find every decision the plan still leaves open and have the user make each one before any work starts.

1. **Map the decisions.** List what the plan depends on. A decision is ready to ask once everything it depends on
   is settled.
2. **Ask in rounds.** Each round asks every ready decision at once, numbered. For each: the options you see, the one
   you would pick, and a one-line reason. Hold back anything that depends on an answer you have not heard yet.
3. **Form or text.** With Studio open, one `questions_v2` form per round: each decision a `text-options` question
   with `options: ["<your pick> (recommended)", "<other option>", …]`; the form adds "Decide for me" and "Other" by
   itself, so add no deferral options of your own. Without Studio, write each as **Q1 · title**, the question and options, then
   **Pick:** your recommendation. End your turn after asking.
4. **Look facts up yourself.** When a decision depends on something the workspace can show (framework, config,
   versions, file layout), check it with `read_file`, `list_dir` or `grep` before asking, and state what you found.
   Ask the user only for choices.
5. **Repeat.** Answers settle some branches and open others; ask the next round. "Decide for me" means your pick:
   say so in the next round.
6. **Close.** When nothing is open, list every decision made, numbered, and ask the user to confirm the list. Build
   nothing until they do.

Idea from Matt Pocock's grill-me skill: https://github.com/mattpocock/skills
