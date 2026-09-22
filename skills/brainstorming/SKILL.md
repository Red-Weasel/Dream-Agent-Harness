---
name: brainstorming
description: "Design before building: classify the change, ask what matters, present a short design, wait for the yes."
---

# Brainstorming

Never build from a one-line idea. Design first, then get an explicit yes. The ceremony scales with the
task; the approval gate never does.

1. **Classify out loud** — spike, bounded, or architectural — so the user can override. A spike answers a
   feasibility question and keeps no code. Bounded changes a flow that already exists here: read it
   (`list_dir`, `grep`, `read_file`), then a short design in chat. Architectural adds a subsystem or changes
   interfaces: questions, 2-3 approaches, a sectioned design, and a spec written to
   `docs/plans/<date>-<topic>-design.md`. When in doubt take the heavier path; hidden complexity found
   mid-task upgrades it, never downgrades. Read [the three paths](references/paths.md) with `skill_file`
   when you need the full rules.
2. **Ask one question per message.** `questions_v2` for 1-3 tappable choices; open questions are fine.
   Ask only what changes the design; the workspace answers the rest.
3. **Present the design, not a menu.** Lead with the recommendation and why. Cut every feature the request
   did not ask for. For each unit: what it does, how it is used, what it depends on. Cover data flow,
   errors, and how it will be verified.
4. **The gate.** End your turn after presenting. Build only after an explicit yes — a two-sentence design
   still gets the gate; "simple" tasks are where unexamined assumptions waste the most work.
5. **After the yes** follow the `coding` workflow: tests first where the project has them,
   `str_replace_edit` for exact edits, verify the actual trigger.
