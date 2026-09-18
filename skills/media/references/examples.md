# Recover an interrupted export

If an export was submitted before an interruption, use the existing project ID.
For a previously returned project `project-42`, inspect its jobs:

```text
media_read {"action":"jobs","payload":{"project_id":"project-42"}}
```

Substitute the actual returned ID. If the original job is still queued or running,
report that state and poll again later. If it completed, inspect its returned
output. If it failed, read its error and correct that cause before submitting a
replacement. Missing output or an unknown state does not prove no export ran.

For a supplied image, inspect its format and dimensions first:

```text
image_metadata {"path":"product.png"}
```

Metadata supports dimension/format claims only. Inspect pixels through see when
vision is available. This example starts no generation and loads no model.
