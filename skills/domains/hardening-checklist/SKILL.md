---
name: hardening-checklist
description: "Check a service, host or application configuration against a security baseline; list gaps with the change and how to verify it."
---

# Hardening checklist

Use this before exposing a service, after setting up a new host or application, or on a regular schedule for systems
the owner runs.

## Method

1. Identify the target and a baseline: the vendor's security guide or a recognised benchmark for this product and
   version, found with `web_search` and `browse`. Record the baseline's name and version.
2. Collect the current configuration read-only: configuration files with `read_file`, settings and versions with
   `run_bash` commands that only report (list listening ports, users and permissions, installed versions, enabled
   services). Do not change anything during collection.
3. Check the essentials first:
   - supported versions with security updates applied;
   - only needed services listening, bound to the right interfaces;
   - default accounts and passwords removed; least privilege for service accounts;
   - strong authentication for administration; encryption in transit;
   - logging enabled and kept; backups present and restorable;
   - secrets outside code and readable only by what needs them.
4. Compare the rest of the baseline item by item, marking met, not met or not applicable with evidence.
5. For each gap, write the change, its risk of breaking something, and a command or check that proves it applied.

## Output

A checklist saved with `write_file`: item, baseline reference, current value (evidence), status, recommended change,
verification step, priority. Then the short list of changes to make first. Offer `task_add` for tracking.

## Checks

- Every status has evidence from the actual system, not from documentation.
- Changes are proposed, not applied, unless the owner asked for them.
- Verification steps are commands or observations anyone can repeat.

## Pitfalls

- Applying a benchmark written for a different version.
- Hardening one layer while leaving an exposed admin panel.
- Changes that lock the owner out (test access paths first).

Defensive use only: systems the owner runs or is authorised to assess. Do not probe, scan or test third-party systems, and do not build exploits.
