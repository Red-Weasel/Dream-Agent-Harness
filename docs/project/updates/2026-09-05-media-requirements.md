# DREAM-023 / DREAM-025 — Owner media requirements and subscription access

Recorded: `2026-09-05T12:28:20-05:00`
Work items: `DREAM-023, DREAM-024, DREAM-025`
Outcome: `proposed`
Actor: Codex, requirements analysis and read-only documentation research.

## Request

The owner answered the media intake questions. Acceptance for this session:
preserve those answers, verify material subscription-access claims, distinguish
proposals from delivered functionality, and retain the long-term avatar goal.

Owner requirements:

- Prioritize website animation, animated explainers and videos demonstrating or
  promoting software. The owner delegates technical and creative defaults.
- Support local generation on whichever computer runs Dream and cloud access
  through existing Fable, ChatGPT, Grok and Gemini subscriptions. No API keys,
  additional subscriptions or additional generation spend. Higgsfield membership
  exists but the owner does not want it to underpin this project. No membership
  cancellation was requested or performed.
- Broad animation ambition includes the previously discussed 2D, character, 3D
  and interactive forms. This is breadth of desired capability, not evidence
  that all can be delivered in one stage or run on every host.
- Reference-based edits, identity/product consistency and reusable assets are
  desired. Long, high-quality output and the ability to correct/tweak it matter.
- No firm UI, duration, resolution or format requirement. Propose useful defaults.
- Ultimate goal: a photorealistic avatar driven by the agent, conversing with
  the owner in real time. Recorded as proposed DREAM-025, separate from offline
  creation and from still-deferred narrated learning under DREAM-024.

## Changes

Updated CURRENT and MASTER_PLAN with the owner's answers, added DREAM-025 and
this dated history entry. Prior handoffs and pre-existing source work remain
intact. No runtime code, rules, credentials or subscriptions changed.

Proposed direction for the next design discussion:

1. Make software storytelling and local animation/rendering the first complete
   workflow, preserving editable scenes and imported real product visuals.
2. Use conversation plus previews, scene selection and simple controls initially;
   propose advanced timeline editing separately as concrete needs emerge.
3. Start verification with a 60–90-second, 1080p/30fps software explainer and an
   editable website animation. These are proposed test targets, not owner-set
   limits. Assemble longer projects from revisable scenes. Higher-resolution and
   longer exports require resource testing rather than an unlimited promise.
4. Qualify local workflows by available hardware and integrate existing
   subscription media capabilities where a supported path is verified. Distinguish
   native automation, website handoff and imported assets. No automatic paid
   fallback is part of this proposal. Precise edits to coded text/timing and
   probabilistic regeneration of image/video pixels need different expectations.
5. Evaluate the conversational avatar separately: speech input/output, turn
   taking, interruptions, synchronized face motion and renderer performance.
   Interface planning must not imply that voice work or a model load has begun.

## Validation

Read existing project tracking and inspected git status before edits. Saved the
372-path baseline with `python3 scripts/check_project_tracking.py snapshot
--output /tmp/dream-media-requirements-20260905.json`.

Current official documentation findings, observed September 5:

- [OpenAI authentication](https://learn.chatgpt.com/docs/auth) documents ChatGPT
  subscription sign-in for Codex clients. [Image generation](https://learn.chatgpt.com/docs/image-generation)
  documents built-in image creation/editing against general Codex usage limits,
  including interactive CLI instructions. This is a candidate subscription route;
  this session did not verify Dream's installed CLI, noninteractive invocation,
  output events or asset retrieval. It does not establish video access.
- [Claude image capabilities](https://support.claude.com/en/articles/9002504-can-claude-produce-images)
  distinguishes generated HTML/SVG visuals from photo/illustration generation.
  Claude can remain a creative/code assistant without being advertised as a
  native photographic image backend.
- [Gemini video help](https://support.google.com/gemini/answer/16126339?hl=en)
  documents signed-in plan-dependent video creation, references, conversational
  edits and downloads. It does not establish a direct keyless Dream integration.
- [Grok app documentation](https://docs.x.ai/grok/overview) documents Imagine
  images/videos. Its [FAQ](https://docs.x.ai/grok/faq) documents shared subscription
  usage and optional extra usage. The [Imagine API](https://docs.x.ai/developers/model-capabilities/imagine)
  is a separate documented execution interface; no keyless external media adapter
  was established here. Do not infer all API usage is necessarily excluded from
  every subscription's allowance; the owner's no-key requirement still applies.

The OpenAI Markdown-page fetches failed; regular official HTML pages supplied
the evidence. Research used the built-in web tool; no Firecrawl credit-consuming
calls, model loads, provider generation requests or installs were made.
Observed: `python3 scripts/check_project_tracking.py check --snapshot
/tmp/dream-media-requirements-20260905.json` passed, with four dated records and
all four session-changed paths covered. No runtime test or live account check ran.

## Unfinished work

Pineapple check: subscription tiers, Dream adapter compatibility, keyless Gemini
and Grok automation, local performance and real-time avatar feasibility remain
unverified. The exact service meant by the owner's Fable subscription also needs
mapping to its installed client/account during qualification, without guessing
product identity or entitlement. No credentials were requested or inspected.

The owner has delegated defaults, not approved an implementation specification.
The usefulness and acceptability of browser-assisted/manual media handoff remain
open where native subscription integration is unavailable. Preserve that
limitation explicitly. No new worker or process needs cleanup; no uncertain
external operation needs replay or reconciliation.

## Next steps

Propose the staged DREAM-023 design using these requirements when the owner
requests the plan. Verify the installed subscription clients' media capabilities
before promising automation, and expose unsupported capabilities honestly.
Agree on any website handoff limitation. Keep DREAM-025 a separate feasibility
track and DREAM-024 deferred. Take a fresh tracking snapshot before later edits.

## Files changed

- `docs/project/CURRENT.md`
- `docs/project/MASTER_PLAN.md`
- `docs/project/HISTORY.md`
- `docs/project/updates/2026-09-05-media-requirements.md`
