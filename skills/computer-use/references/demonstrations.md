# Demonstrations and transfer checks

Use a synthetic, task-specific workspace when collecting teaching examples. Record
only when the user explicitly requests it through Dream's supported recording flow.
Exclude credentials, private documents, unrelated windows and personal notifications.
Do not start a recording as a side effect of running this skill.

## Capture checklist

- State the goal, app/version, target document and final observable result.
- Show the initial state and viewport, then pause at meaningful state transitions.
- Explain the target by label or role, why the action applies, and what confirms it.
  Include one recovery from a stale selection or changed layout when relevant.
- Preserve a final artifact or visible saved state. Record missing prerequisites
  and unresolved failures rather than trimming the example into apparent success.

Dream exposes `demonstration_list`, `demonstration_read` and
`demonstration_draft` when enabled. Inspect returned frames with `see` before
describing their pixels. `demonstration_read` pages frame paths with `id`, `offset`
and `limit`; paths alone are not evidence of clicks. Sampling does not log
keystrokes and can miss intermediate actions. Each `frames[i].file` is relative:
for `see`, join the returned `directory` with that file (for example,
`directory + "/" + frames[i].file`). Keep the original relative frame name when
citing it in `demonstration_draft`.

For a requested draft, `demonstration_draft` takes `id`, `goal` and `steps`.
Each step uses `action`, `frames`, `basis` and optionally `verify`; basis is
`visible`, `inferred` or `user-confirmed`. Keep uncertainty explicit. The tool writes
a reviewable draft; it does not install, enable or replay it. Its response describes
the user review/install step. Preserve the user's existing authorization scope.

The user can add manual frame-linked evidence in Controls → Learn → Annotate
evidence. `demonstration_read` returns one annotation at `event_offset`, including
application/version and before/action/after/outcome. Cite its `evidence_ids` in a
draft step. A `user-confirmed` step needs matching saved human action evidence;
the model cannot manufacture that confirmation through the draft tool.
If `shortened_fields` is returned, retrieve full text with `event_field` and
`field_offset`/`next_field_offset`, passing `evidence_revision` from the annotation
revision. Keep inferred actions and unconfirmed outcomes explicit.

Teach the state-to-action decision, not the recorded screen coordinates. A movie
does not change Fable or DeepSeek weights; any benefit from a loaded skill needs
separate evidence with that model and its actual tools.

## Evaluation cases

Run only when evaluation is requested, in disposable local fixtures. Keep the task
and exposed tools identical between a no-skill baseline and a skill-assisted run.
Record the model/version, vision readiness, outcome and evidence for each attempt.

| Fixture | Observe whether the agent |
| --- | --- |
| Same form at a different viewport with duplicate Save labels | Locates the intended form and verifies its saved value without copied coordinates. |
| User Studio form differs from workspace HTML | Inspects visible state, preserves edits and restores private preview after a snapshot. |
| `browse` exists but desktop controls do not | Uses read capability honestly and identifies the blocked interaction. |
| Screenshot saved but vision disabled | Uses available DOM evidence and leaves appearance unverified. |
| Save succeeds but response times out | Checks persisted state before attempting another save. |

Score successful task outcomes, unsupported actions, recovery correctness and
unverified claims. Tool-call counts and elapsed time are secondary. A successful
loader check proves packaging only; these behavioral cases remain unperformed
until an actual run supplies evidence.

The repository's `scripts/skill_transfer_check.py` prepares eight synthetic task
variants in a new private `--output` directory. It calls no model by default.
An explicitly authorized `--claude /path/to/claude` comparison uses fresh contexts,
the same model and fixtures, and skill guidance only in the assisted arm. Its
bounded action plans execute in a deterministic simulator; retained traces expose
wrong-target actions, duplicate saves and missing verification. This does not
exercise pixels, a live browser, native input, or the Dream adapter. Report the
resolved model IDs, both scores and failures. A tie is no demonstrated uplift;
one run per arm does not establish reliable transfer.
