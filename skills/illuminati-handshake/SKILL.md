---
name: illuminati-handshake
description: Build a bounded capability experiment when existing Dream tools demonstrably cannot solve a task.
---

# Capability experiment

Use this optional workflow for a demonstrated capability gap, not ordinary task planning.

1. Define the missing behavior and a concrete acceptance case. Search existing tools/skills before building another one. Distinguish missing capability from disabled tools, failed arguments or unavailable services.
2. Research a suitable method and choose the smallest testable candidate. Create a lab with `capability_lab_create(name, goal, criteria, method="tool")`. Read [experiment guidance](references/experiment.md) before building in its returned directory.
3. Bound trial count, runtime and resources. Implement with existing file tools and `run_bash`; compare baseline, success cases and held-out failures. `capability_lab_inspect` inventories evidence but does not independently verify or install it.
4. For supplied recordings, read [demonstration evidence](references/demonstrations.md). Use [learning experiments](references/learning.md) only when a deterministic baseline is inadequate and reward/reset/held-out evaluation are defined.
5. Report observed outcomes and limitations. Keep generated plugin candidates disabled until the existing review/trust controls authorize promotion. This workflow grants no additional model-load, spending, capture or execution permission.
