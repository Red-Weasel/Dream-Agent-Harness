# Revise one saved document

```text
library_resolve {"reference":"launch plan"}
```

Read the selected returned ID, then use that same ID for replacement. For example,
if resolution returns `file-42` and reading confirms current version 3:

```text
library_read {"file_id":"file-42"}
library_replace {"file_id":"file-42","content":"Launch target remains October 31, 2026.","expected_current_version":3}
library_read {"file_id":"file-42"}
```

The ID, version and content above illustrate argument shape. Substitute observed
values and the complete requested revision. Never copy example content into a real
item. On a conflict, read the new current version, merge the intended changes and
retry with that observed version. Confirm the returned ID is still the same item
and the reopened text contains the revision. See [tools](tools.md) for binary edits.
