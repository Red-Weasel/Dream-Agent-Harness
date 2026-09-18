# Progress and delivery checks

## Repeated observations

The compatible HTTP adapter retains its existing exact-call repeat guard. It also
compares text returned by `read_file`, `list_dir`, and `read_url` for the same exact
target. After three unchanged observations across different argument sets, it adds
guidance to use the evidence already obtained or identify a specific missing fact.
Changing paging arguments alone is not a reason to repeat an unchanged read.

This comparison adds advice, not another stop limit. New text, different targets,
image-bearing observations and polling tools are excluded. Existing mutation
invalidation clears affected read history; a permission refusal does not imply a
file changed. Each worker has its own history. This does not measure all productive
work or diagnose a slow model from elapsed time.

## Output review

A scheduled end-of-turn HTTP verifier review now contributes to the final result.
A standalone ASCII PASS requires successful page loading, log inspection,
screenshot capture and image inspection. The returned image must reach a completed
verifier request. A new page or screenshot invalidates earlier visual evidence;
a missing check or unresolved tool failure prevents PASS. These are minimum
execution checks, not proof of artistic quality. Other review text means the output
needs attention; it may describe a defect or incomplete inspection. Failed,
missing or empty review evidence is unverified. Findings remain available for the
next user turn, with a bounded summary shown immediately.

A failed latest `done` call produces `delivery_failed` even if the model says it is
finished. It clears an older scheduled page review; a later successful delivery
can resolve the failure. Suppressed repeated calls do not erase the last actual
prepared artifact. `delivery_attempt` records this separately from review.

The result carries `delivery_review` separately from the assistant's answer. A
nominally successful response becomes `verification_findings` or
`verification_unverified` when its scheduled review did not pass. Existing failure
reasons such as a round limit retain precedence. The autonomous loop already rejects
these incomplete results; no automatic repair loop or new runtime ceiling is added.
Ordinary chat without a scheduled review remains ordinary chat, and a later unrelated
request does not inherit an old verification failure.

Live steering supplies the original active request and subsequent corrections to
the review. Later corrections can amend or cancel earlier requirements. A directed
mid-turn probe is still a tool result; it is not a full delivery review. A PASS is
the verifier's report about its scoped checks, not owner acceptance or a guarantee
of artistic quality. Studio load and console checks alone do not prove playback.

Imported and handed-off images must decode within fixed validation limits before
being published as ready: at most 25 million pixels per frame, 250 million total
frame pixels and 1,000 frames. Exceeding a bound is an explicit validation failure.
This checks supported image bytes, not composition or likeness. Video validation
still uses stream metadata; successful full-frame decoding and visual review need
separate evidence.

ComfyUI history with explicit incomplete or contradictory running status leaves
the job pending and imports no preview assets. Malformed or failed status cannot
publish success. Legacy history without status retains its output-based contract.

## Tool outcome evidence

Expand Turn timing to see observed result counts: reported errors, reported
successes and unknown outcomes. Missing or non-boolean error flags stay unknown.
Fixed categories and review status accompany existing model/settings timing. The
new counters retain no arguments, results, paths, IDs or raw tool names.

These are observed events, not deduplicated calls, proven recoveries or task-success
scores. They do not automatically change models, effort or permissions. Normal use
can provide calibration evidence without a benchmark run.

Gemini tool results retain the original call ID so completed file activity leaves
the pending state. A missing or unrecognized Gemini terminal status reports a
failed turn even if the CLI exits cleanly; partial output and usage remain visible.

## Environment facts before expensive work

`media_action(action="status")` reports the selected workspace and configured shell
read/target roots, tool-image setting and host encoder discovery. Each fact states
its source. Host executable presence does not establish that its libraries are
available inside a shell sandbox. Enabling image tools does not establish endpoint
image acceptance. These fields remain unverified until an appropriate actual check.

Status does not load a model, launch a render or grant host access. If file tools
and shell disagree, compare the actual selected workspace and execution scope
before changing paths or attempting package repairs.

## Evidence still needed

Use normal tasks to collect the exact tool request/result, relevant saved artifact,
model/settings and observed outcome when a problem occurs. A successful recovery is
useful evidence too. Keep transcripts, screenshots and recordings private; extract
only a reviewed reusable lesson into a skill. Do not infer a model-quality uplift
from synthetic tests. Model/task calibration remains deferred until representative
live-use evidence is available.
