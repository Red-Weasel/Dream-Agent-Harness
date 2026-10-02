---
name: dependency-upgrade
description: "Plan, apply and verify a dependency version bump: read the changes, update locks, run checks, keep a way back."
---

# Dependency upgrade

Use this when a library, framework, runtime or tool needs a newer version, whether for a fix, a feature or a security
advisory.

## Method

1. Record the starting point: current version, where it is declared, the lock file and who imports it (`grep`). Note
   the project's test and build commands. Use `checkpoint_list` or the version control state so you can return.
2. Read what changed between the versions: the release notes and changelog with `web_search` and `browse`, preferring
   the project's own pages. List breaking changes, deprecations and new minimum requirements.
3. Map each breaking change to the places it touches in this code. If the jump is large, plan intermediate versions.
4. Change the declaration and regenerate the lock file with the project's own tool through `run_bash`. Do not edit
   lock files by hand. Keep unrelated upgrades out of the same change.
5. Fix the code the upgrade breaks with `str_replace_edit`, one cause at a time, re-running the failing check each
   time.
6. Run the project's tests, build and type checks. Compare warnings with the baseline; new deprecation warnings are
   future work to list, not noise.

## Output

- From and to versions, and the reason for the upgrade.
- The breaking changes that applied here and the edits each needed.
- Commands run with their results, before and after.
- How to roll back (previous version and lock file) and any follow-up.

## Checks

- The lock file changed only for the intended packages and their required dependencies.
- Tests that exercise the upgraded library ran, not only unrelated ones.
- Nothing was pinned to an older version silently to make a check pass.

## Pitfalls

- Trusting a version number's size: minor releases can break behaviour too.
- Upgrading many packages at once, so a failure cannot be traced.
- Installing from an unexpected source or a look-alike package name.

Limits: an upgrade is verified by the checks that ran. Name what the tests do not cover.
