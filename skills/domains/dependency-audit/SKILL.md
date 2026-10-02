---
name: dependency-audit
description: "Find known-vulnerable or unmaintained dependencies in a project you own and plan upgrades by real exposure."
---

# Dependency audit

Use this for a periodic health check of a project's third-party packages, after a public advisory, or before a
release.

## Method

1. Inventory from the lock files, not the loose declarations: `list_dir` and `read_file` to find them, then produce a
   list of name, exact version and whether it is direct or pulled in by another package.
2. Run the ecosystem's own audit tool if the project has one available (for example the package manager's audit
   command) through `run_bash`. Read its output carefully; it may be incomplete.
3. For each reported advisory, check the original advisory with `web_search` and `browse`: affected versions, fixed
   version, and the conditions needed to exploit it.
4. Judge exposure in this code: is the vulnerable function or feature used (`grep`), is the package only used in
   development or tests, is the vulnerable input reachable from outside?
5. Look beyond advisories: packages with no release for years, archived repositories, a single maintainer on a
   critical package, or names that look like a typo of a popular one.
6. Plan: upgrade (the dependency-upgrade skill covers doing it), replace, remove if unused, or accept with a reason.

## Output

An audit report saved with `write_file`: inventory counts; a table of findings (package, version, advisory ID, severity
as published, exposure here with evidence, action, target version); unmaintained or suspicious packages; and the order
of work.

## Checks

- Versions come from the lock file.
- Each severity judgement for this project cites the usage found or not found.
- Advisory IDs link to their source.

## Pitfalls

- Treating every advisory as urgent, or ignoring transitive packages.
- Upgrading to a version that does not contain the fix.
- Running audit tools that upload the full dependency list to a third party without the owner's agreement.

Defensive use only: systems the owner runs or is authorised to assess. Do not probe, scan or test third-party systems, and do not build exploits.
