---
name: reconciliation
description: "Match two ledgers or statements line by line, list the breaks with likely causes and the adjustments that would clear them."
---

# Reconciliation

Use this when two records of the same money should agree and do not: bank against ledger, sub-ledger against control
account, supplier statement against payables, or two systems exporting the same transactions.

## Method

1. Get both sides for the same period with their closing balances. Read them with `read_file`; note the date range,
   currency, sign convention and which fields each side has (date, amount, reference, description).
2. Normalise in a script through `run_bash`: one sign convention, amounts as integers in the currency's minor unit
   (or decimals, never binary floating point), dates as dates, references trimmed and upper-cased.
3. Match in passes, keeping every pass's results: exact reference and amount; amount and date within a small window;
   amount only when it is unique on both sides; many-to-one groups (one payment for several invoices). Never force a
   match that two candidates could fill.
4. Everything left is a break. Classify each: timing (in transit, cleared after the period end), missing on one side,
   duplicate, amount difference, wrong period, wrong account.
5. Start from the two closing balances and show the list of reconciling items that explains the difference to the cent.
6. Propose adjustments only for errors; timing items clear by themselves and are only listed.

## Output

- A statement: balance on side A, reconciling items grouped by type, balance on side B, and a remaining difference of
  zero (or the amount still unexplained).
- The break list as a CSV via `write_file`: both sides' details, class, proposed action.
- Counts and totals for matched, broken and adjusted items.

## Checks

- Totals of matched items agree on both sides.
- The reconciling items explain the full difference; any residual is shown, not rounded away.
- Each proposed adjustment has a reason and a source line.

## Pitfalls

- Matching on amount alone where amounts repeat.
- Netting a missing item against an unrelated difference so the total looks right.
- Using balances from different cut-off times.

This is analysis support, not financial, tax or investment advice; have figures that drive a decision checked by a qualified professional.
