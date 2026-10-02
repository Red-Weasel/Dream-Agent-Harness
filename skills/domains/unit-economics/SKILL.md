---
name: unit-economics
description: "Work out per-unit revenue, cost, margin, acquisition cost, lifetime value and payback, with every assumption stated."
---

# Unit economics

Use this when someone wants to know whether each customer, order or unit makes money, how long it takes to earn back
what it cost to win, or how a price or cost change would move that.

## Method

1. Define the unit first (a customer, a subscription, an order, a product sold) and the period. Everything else is
   per that unit.
2. Gather inputs with their source: price and discounts, variable costs per unit (materials, payment fees, delivery,
   support), acquisition spend and the number of new units it produced, retention or repeat rate by cohort. Read files
   with `read_file`; use `web_search` only for public benchmarks, labelled as such.
3. Compute in a script through `run_bash`: contribution margin per unit; acquisition cost as spend divided by new
   units in the same period; lifetime value as margin per period times expected life (from observed retention, capped
   at a horizon you state), optionally discounted at a stated rate; payback as acquisition cost divided by margin per
   period.
4. Show sensitivity: change each main input by a plausible range and report how payback and lifetime value move.
5. Split by segment or channel when the averages hide very different groups.

## Output

- A one-screen summary: margin per unit, acquisition cost, lifetime value, value-to-cost ratio, payback.
- The assumptions table with sources and confidence.
- A sensitivity table, and a chart with `chart_display_v0` when it helps.
- The calculation file saved with `write_file` so the numbers can be rerun.

## Checks

- Fixed costs are not mixed into per-unit contribution unless that is the stated method.
- Acquisition spend and new units come from the same period and channels.
- Lifetime assumptions are grounded in observed retention, not a hoped-for one.

## Pitfalls

- Using revenue instead of margin in lifetime value.
- Blending organic and paid customers so acquisition cost looks low.
- Infinite lifetimes from a retention rate near 100 %.

This is analysis support, not financial, tax or investment advice; have figures that drive a decision checked by a qualified professional.
