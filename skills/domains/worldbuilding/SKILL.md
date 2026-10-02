---
name: worldbuilding
description: "Build a consistent setting bible: geography, history, rules, cultures and a canon log that keeps later writing consistent."
---

# Worldbuilding

Use this for fiction, games or campaigns that need a setting which stays consistent as it grows, especially across
many sessions or several writers.

## Method

1. Start from what the stories need, not an encyclopaedia: the genre, the scale (one city or a continent), and the
   questions the plot will ask of the world. Ask with `ask_user_input` for the owner's fixed ideas.
2. Define the rules first: what is possible here that is not in our world (magic, technology, biology), what it costs,
   and what it cannot do. Limits create stories.
3. Sketch the places the stories touch: geography, climate, resources, travel times. Sketch the history as a short
   timeline of events that still matter.
4. Build the societies that appear: who holds power and how, what people believe, what they eat, trade and argue
   about, and how outsiders are treated. Give each group something it wants.
5. Keep a canon log: every fact established in a story gets a line with where it was established. Check new material
   against it with `grep` before writing.
6. Store everything in a small set of files with `write_file` (rules, places, peoples, timeline, canon log) and keep
   them current.

## Output

A setting bible as files: overview, rules and their costs, places, timeline, peoples and factions, glossary of names,
and the canon log. A one-page summary for new collaborators.

## Checks

- Rules are applied the same way everywhere; exceptions are explained.
- Names follow consistent patterns per culture and are not easily confused.
- The canon log is updated whenever a story adds a fact.

## Pitfalls

- Detail nobody will read while the story's needs go unanswered.
- Cultures drawn as single-trait stereotypes or lifted from real peoples without care.
- Contradictions creeping in because facts live only in drafts.

Limits: the bible serves the stories; cut what does not.
