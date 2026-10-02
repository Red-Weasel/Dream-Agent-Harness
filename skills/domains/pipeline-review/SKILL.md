---
name: pipeline-review
description: "Review a sales pipeline: stage hygiene, stale and at-risk deals, forecast realism and the next action for each deal."
---

# Pipeline review

Use this for a weekly or monthly look at open deals, before a forecast call, or when a pipeline feels bigger than the
results it produces.

## Method

1. Get the export with its field meanings: deal, account, stage, amount, close date, owner, last activity, next step.
   Read it with `read_file` and load it into a script through `run_bash`.
2. Check hygiene: missing amounts or close dates, close dates in the past, deals with no activity for longer than the
   stage allows, deals without a next step, duplicates.
3. Check stage honesty: each stage should have exit criteria (for example "decision maker met"). Flag deals whose notes
   do not show the criteria for their stage.
4. Look at movement since the last review: new, advanced, slipped (close date pushed), lost. Repeated slips are a
   signal.
5. Build the forecast in two views: weighted by stage probability, and a judged commit list of deals with evidence.
   Compare both to the target.
6. For each deal at risk, write the single next action that would move or disqualify it.

## Output

- Summary: total and weighted pipeline, coverage against target, and the three biggest risks.
- A table of flagged deals: issue, evidence, next action, owner.
- A stage funnel or trend with `chart_display_v0`; the full list saved with `write_file`.

## Checks

- Totals match the export.
- Every flag cites the field or note that triggered it.
- Next actions are specific and owned.

## Pitfalls

- Treating a large pipeline as a healthy one.
- Keeping dead deals open to protect the numbers.
- Changing probabilities by feel without stage evidence.

Limits: the review is only as good as the data entered; call out gaps instead of filling them.
