---
name: library
description: Find, read, update or restore a Library file while preserving its identity and version history.
---

# Library

1. Find the exact item with `library_resolve` or `library_search`; use `library_list` for an inventory. If several documents match, inspect candidates and resolve ambiguity before writing. A search snippet is not the document.
2. Use `library_read` for text. For binary documents or local editing, use `library_materialize` to create a workspace copy, then ordinary file tools. Keep its returned identity/version information.
3. Save an edited item with `library_replace`, using the materialized `path` or the original `file_id` and observed `expected_current_version`. Use `library_create` only for a new item the user wants saved. A new local filename does not create a new document identity.
4. On a version conflict, reread and merge before replacing. Never drop the conflict guard to force success. Reopen the saved version and report the document name and actual change.

`library_manage` can inspect history, restore an old version as a new version, or undo a soft deletion. Purge is permanent and requires the user's explicit request. Read [identity and versions](references/tools.md) with `skill_file` for these operations.

Read [worked examples](references/examples.md) with `skill_file` when a concrete tool sequence would help.
