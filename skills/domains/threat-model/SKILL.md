---
name: threat-model
description: "Model threats to a system you own: assets, entry points, trust boundaries, likely threats and the mitigations that matter."
---

# Threat model

Use this at design time, before a release, or after a significant change, to decide which security work is worth doing
first for a system the owner builds or operates.

## Method

1. Describe the system from what exists, not from memory: `project_outline`, `list_dir` and `read_file` for code and
   configuration; architecture notes if any. Draw the data flow in text: users, services, stores, third parties, and
   the arrows between them.
2. List the assets: data (credentials, personal data, payment data, business records), capabilities (sending email,
   moving money, admin actions) and availability that matters.
3. Mark the trust boundaries: where data crosses from less trusted to more trusted (internet to server, user input to
   query, tenant to tenant, service to service).
4. At each boundary, walk through the STRIDE categories: spoofing (impersonation), tampering, repudiation (denial
   of having acted), information disclosure, denial of service and elevation of privilege. Write concrete threats,
   not category names.
5. For each threat, note existing controls (confirm them in code or configuration with `grep`), then rate likelihood
   and impact on a simple scale and propose mitigations.
6. Pick the few mitigations that remove the most risk for the effort and record accepted risks explicitly.

## Output

A threat model saved with `write_file`: system description and data flow, assets, boundaries, a threat table (ID,
boundary, threat, existing control with evidence, likelihood, impact, mitigation, owner), accepted risks, and the top
five actions.

## Checks

- Each claimed control is verified in the code or configuration, or marked as assumed.
- Every boundary has at least one threat considered.
- Actions are specific enough to become tasks (`task_add` if the owner wants them tracked).

## Pitfalls

- Listing generic threats that do not apply to this system.
- Forgetting internal and administrative paths, backups and logs.
- A model written once and never updated after the design changes.

Defensive use only: systems the owner runs or is authorised to assess. Do not probe, scan or test third-party systems, and do not build exploits.
