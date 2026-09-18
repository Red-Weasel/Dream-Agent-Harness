# Sum a CSV with quoted names and missing amounts

```text
read_file {"path":"sales.csv"}
```

For rows `"North, Central",10.10`, `"North, Central",2.20`, `South,20.00`
and `South,`, the region includes a comma. Parse with csv.DictReader. Use Decimal
for currency and count the blank amount as excluded when the task requires that.
Save this script using write_file, then run it through run_bash in the workspace.

```python
import csv, json
from decimal import Decimal
from pathlib import Path
with open("sales.csv", newline="", encoding="utf-8") as source:
    rows = list(csv.DictReader(source))
totals, excluded = {}, 0
for row in rows:
    if not row["amount"].strip():
        excluded += 1
        continue
    region = row["region"]
    totals[region] = totals.get(region, Decimal("0")) + Decimal(row["amount"])
result = {"totals": {k: format(v, ".2f") for k, v in totals.items()},
          "rows": len(rows), "excluded": excluded}
Path("totals.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
```

```text
run_bash {"command":"python summarize_sales.py"}
read_file {"path":"totals.json"}
```

For these example inputs, check North, Central = 12.30, South = 20.00,
4 source rows and 1 excluded row. These are expected values, not observed results.
Invalid numeric text should raise a visible error; inspect it before deciding
whether to correct or exclude that row.
