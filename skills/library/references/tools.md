# Library identity and versions

`library_search(query, top_k, title_only)` finds candidates; `library_resolve(reference)`
resolves an ID, local path or description to an item or ambiguity. Read the selected
file with `library_read(file_id, version?)`. For a binary or local edit, materialize
it with `library_materialize(file_id, dest, version?)`.

A materialized path carries identity metadata. `library_replace(path=...)` uses it
and the checked-out version to preserve the same document and detect a conflict.
When replacing by ID, supply `file_id`, content/path, and the observed
`expected_current_version`. On conflict, reread the current file and merge; do not
invent a version or drop the guard. An editor producing a new local filename does
not make the document a new Library item.

`library_create(path=...)` or `library_create(content=..., name=...)` is for a new
item. Use it when the task calls for saving to the Library, not automatically for
every temporary file. `library_manage` actions include versions, rename, move,
trash, delete, undelete and restore_version. Delete is recoverable; restoring an
old version appends a new one. Purge destroys all versions and requires the user's
explicit request. Do not confuse a restore with overwriting history.
