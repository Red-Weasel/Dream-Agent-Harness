---
name: cash-flow-forecast
description: "Build a driver-based short-term cash forecast (for example 13 weeks): receipts, payments, closing balance and risks."
---

# Cash flow forecast

Use this when someone needs to know how much cash they will have week by week, whether a low point is coming, or how
a decision (a hire, a large order, a payment delay) changes the position.

## Method

1. Fix the frame: start date, number of periods (13 weeks is a common choice), opening cash per account and currency.
   Take the opening balance from a bank statement, not from the accounts.
2. List receipts by source: collections from customers (from open invoices and typical days to pay), new sales
   (volume times price, delayed by payment terms), other income. List payments: suppliers by due date, payroll on its
   dates, rent, tax, debt service, planned purchases.
3. Build it as a script or CSV through `run_bash` with drivers in one block and weekly rows computed from them, so a
   changed assumption flows through. Save it with `write_file`.
4. Compute closing cash each week and the minimum balance against any facility or covenant limit.
5. Run two or three scenarios by changing drivers only: slower collections, lower sales, a delayed large payment.
6. After each real week, compare forecast to actual and adjust the drivers that missed (a `note` keeps the history).

## Output

- A weekly table: opening cash, receipts by type, payments by type, net flow, closing cash.
- The assumptions list with their source and confidence.
- The low point: week, amount and headroom; scenario results beside the base case.
- A line chart of closing cash with `chart_display_v0`.

## Checks

- Opening cash matches the bank; each week's closing equals next week's opening.
- Payroll, tax and debt dates are on the right weeks.
- Receipts are not counted twice (open invoices and new sales).

## Pitfalls

- Forecasting profit instead of cash: accruals, depreciation and payment terms change the timing.
- Averages that hide a large single payment.
- Optimistic collections with no allowance for late payers.

This is analysis support, not financial, tax or investment advice; have figures that drive a decision checked by a qualified professional.
