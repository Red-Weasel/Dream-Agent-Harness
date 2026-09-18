# Recover from an ambiguous edit

Suppose config.txt contains both `primary=30` and `backup=30`. A broad replacement
of `30` with `60` is refused because it matches twice. Inspect before retrying:

```text
read_file {"path":"config.txt"}
str_replace_edit {"path":"config.txt","old_string":"primary=30","new_string":"primary=60"}
read_file {"path":"config.txt"}
```

Check the actual result contains `primary=60` and still contains `backup=30`.
If the text differs, stop that edit and identify the current unique target.
Changing the match resolves ambiguity; replaying the broad call does not.

For a timed-out media export, read the existing job state using the actual project
ID before submitting another job. A timeout describes the wait, not necessarily
the side effect. Report missing evidence explicitly and preserve the exact next
inspection in note when handing off.
