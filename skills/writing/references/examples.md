# Revise a supplied memo without changing facts

```text
read_file {"path":"draft.md"}
```

If the source says `Orion has a USD 12,500 budget and an October 31, 2026 deadline`,
a concise revision can retain both values: `Orion's budget is USD 12,500. The
deadline is October 31, 2026.` These are illustrative facts, not defaults to add.
Preserve whether the source calls the budget approved, proposed or estimated.

After preparing the complete requested revision:

```text
write_file {"path":"revised.md","content":"Orion's budget is USD 12,500. The deadline is October 31, 2026.\n"}
read_file {"path":"revised.md"}
```

Use content from the actual inspected source. Compare names, dates, numbers and
qualifiers, then confirm the saved file matches the requested tone and purpose.
Saving an email draft does not send it. Report unresolved facts instead of
silently turning assumptions into assertions.
