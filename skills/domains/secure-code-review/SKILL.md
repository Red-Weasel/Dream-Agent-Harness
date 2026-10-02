---
name: secure-code-review
description: "Review code you own for security flaws: injection, broken auth, secret exposure, unsafe input handling; report with fixes."
---

# Secure code review

Use this when asked to check the owner's code for security problems, before exposing a service, or after a finding
elsewhere suggests a pattern to look for.

## Method

1. Map the attack surface with `project_outline` and `grep`: routes and handlers, file uploads, command execution,
   database queries, template rendering, deserialisation, outbound requests, authentication and session code.
2. Follow untrusted input from where it enters to where it is used. At each sink, check what makes it safe:
   parameterised queries, argument lists instead of shell strings, output encoding for the context, path
   normalisation against a base directory, allow-lists for redirects and outbound hosts.
3. Check authentication and authorisation on every sensitive operation: is identity verified, is the object owned by
   the caller, are admin paths protected server-side?
4. Look for secrets in code, configuration and history (`grep` for keys, tokens, passwords; `git log -p` through
   `run_bash`, scoped to recent commits or the relevant paths on a large repository). Check that errors and logs do
   not expose them. A secret that ever reached the history is exposed: it must be rotated at its issuer; deleting it
   from the code is not enough.
5. Check cryptography use: well-known libraries, no home-made schemes, secure randomness for tokens, modern password
   hashing.
6. Confirm each finding by reading the full path or with a local test against the owner's own code. Never test
   against live systems you do not operate.

## Output

Findings saved with `write_file`, ordered by severity: `path:line`, weakness, how untrusted input reaches it, impact,
fix with a code sketch, and how to test the fix. Add a list of areas reviewed and not reviewed.

## Checks

- Every finding shows a concrete path from input to sink, or is labelled as a hardening suggestion.
- Fixes address the cause (for example parameterising the query), not the symptom (filtering one character).
- No secret found is copied into the report; name its location only.

## Pitfalls

- Trusting client-side checks.
- Stopping at the first finding in a file.
- Reporting scanner output without checking it.

Defensive use only: systems the owner runs or is authorised to assess. Do not probe, scan or test third-party systems, and do not build exploits.
