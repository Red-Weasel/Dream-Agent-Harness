---
name: frontend-design
description: "Distinctive UI: a concept from the subject, deliberate type, token colors, spacing rhythm, screenshot review."
---

# Frontend design

Work like a design lead whose clients must never be mistaken for one another. The failure mode to avoid
is the generic default: the framework's font stack, a gradient hero, three feature cards, gray-on-white.

1. **Ground the concept in the subject.** Before any code, write three sentences: who this is for, what
   it should feel like (two or three adjectives with a reason each), and one concrete visual idea taken
   from the subject matter itself (its materials, era, geography, motion). Every later choice traces
   back to those sentences.
2. **Typography carries the identity.** Pick a display face and a text face that argue for the concept
   (Google Fonts are fine); set a type scale (5-6 sizes with a clear ratio), line-height by size, and
   measure (45-75 characters). Headings are not just bigger body text.
3. **Color is a system, not a splash.** Define tokens on `:root` (background, surface, text, muted,
   accent, accent-contrast, border), a dark-mode set under `prefers-color-scheme: dark`, and check
   contrast (4.5:1 for text). One accent used with intent beats five used at random.
4. **Rhythm and layout.** One spacing scale (4/8-based), a real grid, generous whitespace where the
   content breathes and density where it works. Compose sections so the eye has a path.
5. **Motion with a purpose.** Transitions for state changes only, 150-300 ms, respect
   `prefers-reduced-motion`. No decorative animation.
6. **Build and look at it.** Write the page with `write_file`, open it with `show_html`, then
   `screenshot_user_view` or `multi_screenshot` and `see` the result at phone and desktop widths. Judge
   it against the three sentences from step 1; fix what reads as a template. Check keyboard focus,
   alt text, and that the layout has no horizontal scroll at 360 px.
7. **Hand over** the files and one paragraph on the concept and the type/color decisions, so the next
   person can extend the identity instead of breaking it.
