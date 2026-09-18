---
name: media
description: Create or edit images, video, animation and media projects; inspect outputs and recover jobs.
---

# Media

1. Read supplied references before describing them. `image_metadata` checks size/format; `see` inspects visible content only when the active model supports vision. Metadata alone does not show what an image depicts.
2. Use `media_read(action="status")` to check available workflows, tools and limitations. For a new editable animation, use `media_create(action="create")`; default to the service's short preview settings unless the user specifies otherwise.
3. Read [media operations](references/operations.md) with `skill_file` for project, import, render and job payloads. Use returned project IDs and current revisions. Reuse user assets and authorized local/subscription routes.
4. Render a short sample before an expensive final export. Inspect representative frames and dimensions/duration. Share a usable draft in the user's Studio while iterating: `show_html` only loads your hidden preview; `show_to_user` presents an HTML preview to the user. For image/video files, use a small workspace HTML wrapper with relative media paths. Label drafts and unverified visuals honestly. Poll the returned job until it reaches a terminal state; a queued job is not a delivered video.
5. Return the produced asset/file and identify any unverified audio, motion or generation. On failure read the job error and change the failing input. Reconcile interrupted jobs before retrying side effects.

A project, prompt or subscription handoff is not generated media. Do not claim generation without an output. Starting GPU generation needs the existing resource and workflow confirmations; never fabricate them or fall back to a paid API.

Read [worked examples](references/examples.md) with `skill_file` when a concrete tool sequence would help.
