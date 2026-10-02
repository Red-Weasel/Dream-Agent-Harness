---
name: visual-brief
description: "Write a visual brief for an image, illustration or layout, then review produced drafts against it with the vision tools."
---

# Visual brief

Use this when an image, illustration, poster, slide or page layout is to be made (by a person or with Dream's media
tools) and the result should match an intention rather than luck.

## Method

1. Establish purpose and context: where it will appear, size and aspect ratio, audience, the one thing a viewer should
   feel or understand, and fixed elements (logo, text, product).
2. Gather references: images the owner supplies (look with `see` or `media_read`), existing brand material, and
   examples of what to avoid. Describe what each reference contributes rather than asking for a copy of it.
3. Write the brief: subject and composition (focal point, framing, placement of text), style (medium, lighting, colour
   palette with values where known), mood, typography, required and forbidden elements, and technical specs.
4. When Dream makes the draft, turn the brief into a precise prompt for `media_create` or a page built with
   `show_html`; keep the prompt with the brief.
5. Review each draft against the brief item by item with `see` (and `visual_check` or `measure_image` for layout and
   size). Note what matches, what misses and the specific change for the next round.

## Output

A brief saved with `write_file`: purpose, specs, references with what to take from each, composition, style, text,
must and must-not lists. For each review round, a short list of matches, misses and changes.

## Checks

- Specs (size, ratio, text) are exact.
- The review cites what is visible in the draft, not what the prompt asked for.
- Text in images is spelled correctly and readable at the final size.

## Pitfalls

- Mood words with no concrete visual direction.
- Asking to imitate a living artist's distinctive style or to reproduce someone else's image.
- Approving a draft from a thumbnail and finding problems at full size.

Limits: generated images can contain errors (hands, text, logos); check before use and respect image rights.
