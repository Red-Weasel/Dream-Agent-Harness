---
name: handoff
description: "Use when the user asks for a handoff, or wants the current work continued by a fresh session, another model or agent, or another person."
---

# Handoff

Write one document that lets someone who never saw this conversation carry on from where it stands.

1. **File.** Save it as `.dream/handoffs/<topic>.md` in the workspace (`list_dir` the folder first; if the name is
   taken, add `-2`, `-3`, …). Reply with its full path and a line the user can paste into the next session:
   `Continue from the handoff at <path>`.
2. **Aim.** If the user said what the next session is for, write for that; otherwise write for the next step.
3. **Sections, in order:**
   - **Goal**: what the work is for, in a few sentences.
   - **Where it stands**: what exists, what works, and how each claim was checked. Mark anything unchecked.
   - **Decisions**: only those still in force, each with its reason.
   - **Open problems**: failures, unknowns, risks.
   - **Next step**: one concrete action to start with.
   - **Skills to load**: the Dream skills that fit the next step, as `$name` (a failing test: `$debugging`; edits
     with tests: `$coding`; a design still open: `$brainstorming`).
   - **Where to look**: paths, commands, commit ids, URLs.
4. **Point, don't paste.** Anything already written down (specs, `PLAN.md`, docs, commits, diffs, logs) goes in as a
   path or link. The document should be a small fraction of the conversation.
5. **Leave out** passwords, keys, tokens and personal details.

Idea from Matt Pocock's handoff skill: https://github.com/mattpocock/skills
