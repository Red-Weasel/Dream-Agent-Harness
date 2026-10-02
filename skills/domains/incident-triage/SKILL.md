---
name: incident-triage
description: "Triage a suspected security incident on your own systems: scope it, contain it, preserve evidence and keep a timeline."
---

# Incident triage

Use this when something suggests a compromise or leak on a system the owner operates: an alert, an odd login, a
leaked key, unexpected changes or traffic.

## Method

1. Start a timeline immediately with `note`: every observation and action with its time and source. Keep facts and
   guesses apart.
2. Preserve before changing. Capture volatile state first (running processes, network connections, logged-in
   sessions, memory if the owner has a tool for it), then copy relevant logs, configuration and suspicious files to a
   safe location with `run_bash` (read-only copies, with checksums). Record where each came from. Do not power a
   machine off: that loses the volatile state.
3. Scope: what is affected (hosts, accounts, keys, data), since when, and how it was noticed. Search the logs you
   hold with `grep` for the first sign and for related activity (same address, same account, same file).
4. Contain proportionately, starting with any credential being abused: revoke it and its sessions first, then
   rotate other exposed secrets, disable compromised accounts, isolate an affected host (from the network, still
   running), block a clearly malicious source. Ask the owner before any step that interrupts service or destroys data.
5. Decide who must be told: internal owners, customers, providers, and any reporting duty the owner has. Dream drafts;
   the owner decides and sends.
6. Once contained, list what is needed for recovery and for finding the root cause.

## Output

An incident record saved with `write_file`: summary, severity, timeline, affected assets, evidence collected (with
checksums and locations), containment actions and their times, open questions, notifications to consider, and next
steps.

## Checks

- Evidence was copied before anything was changed or deleted.
- Each containment step is recorded with who approved it.
- The scope statement separates confirmed from suspected.

## Pitfalls

- Wiping or rebuilding before collecting evidence.
- Rotating one key and missing copies of it elsewhere.
- Tipping off an active intruder through noisy changes when quiet containment was possible.

Defensive use only: systems the owner runs or is authorised to assess. Do not probe, scan or test third-party systems, and do not build exploits. Serious incidents may need specialist responders and legal advice.
