# Dream terminal companions

Five generated character illustrations for the optional Characters terminal skin.
Revised with OpenAI image generation on 2026-10-08 under DREAM-274. The owner
explicitly retained the original small-bot proportions and requested less friendly
expressions: closed mouths, lowered lids/brows and relaxed arms. The wolf uses
black/ivory coloring. Adult/human body proportions were rejected and are not
installed. These are decorative characters, not provider logos or authentication.

| Asset | Default provider |
|---|---|
| `penguin.png` | Codex |
| `fox.png` | Gemini / Antigravity |
| `wolf.png` | Grok |
| `robot.png` | Claude |
| `rabbit.png` | Local / fallback |

All files are RGBA PNGs with genuine alpha transparency, retained without
re-encoding. Render at a small bounded size with `object-fit: contain`. Keep the
full silhouette intact. DREAM-276 places companions on a freely draggable board
overlay with no reserved gutter. Defaults avoid footer controls and folded-header
buttons; user-selected positions may overlap text. Home or Tools → Character →
Reset position restores the default. Movement is a local display preference.
The UI selects only these bundled assets; it does not fetch remote character
images or run a 3D renderer. Character choice is cosmetic and must not change
provider, permissions, model, terminal process or handoff behavior.

The supplied orb designs are saved privately as future subagent references; they
are not served or selected in V1. Original friendly assets are backed up locally
under `.dream/design-references/companions-2026-10-08/original-friendly/`.
