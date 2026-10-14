"""The environment for a git command Dream runs by itself on a repository it does not trust (DREAM-290).

A repository's own config can name commands that ordinary reads run. A workspace that ships a .git/config -- or a
model that writes one in a mode that does not ask before writes -- made Dream's own git calls (the Understand map's
freshness, /review) run a command on the host with no prompt (2026-10-12 security review and its gate). For one call
this turns off:
- `core.fsmonitor` (`git status`) and a filter's clean/smudge/process (`git diff` against the working tree), for every
  filter defined outside the user's own global/system config (git-lfs there keeps working);
- hooks (`core.hooksPath=/dev/null`): a read that refreshes the index writes it and runs `post-index-change`; with
  GIT_OPTIONAL_LOCKS=0 it does not write where it can avoid it;
- submodules' own config (`diff.ignoreSubmodules=dirty`): status/diff otherwise run a child git in each checked-out
  submodule, whose filters the enumeration below never sees. A submodule's moved commit still shows; its uncommitted
  edits do not;
- a partial clone's lazy fetch (GIT_NO_LAZY_FETCH=1 and `protocol.allow=never`): a missing object made git run the
  upload-pack command a promisor remote in the config names.

The overrides travel as GIT_CONFIG_KEY_n/GIT_CONFIG_VALUE_n, not `-c key=value`: a filter's name may hold '=', which
`-c` would split in the wrong place and so leave that filter on.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

_FILTER_COMMANDS = r"^filter\..*\.(clean|smudge|process)$"
_USERS_OWN = {b"system", b"global"}


def untrusted_read_env(repo: str | Path, timeout: float) -> dict[str, str]:
    """os.environ plus overrides that keep `git status`/`git diff` in `repo` from running a command the repository's
    config names. Raises subprocess.CalledProcessError if the config cannot be read (then nothing is known to be safe)."""
    listed = subprocess.run(
        ["git", "config", "--show-scope", "--null", "--name-only", "--get-regexp", _FILTER_COMMANDS],
        cwd=repo, capture_output=True, timeout=timeout)
    if listed.returncode not in (0, 1):    # 1: no such key
        raise subprocess.CalledProcessError(listed.returncode, listed.args, listed.stdout, listed.stderr)
    fields = listed.stdout.split(b"\0")
    keys = sorted({key.decode("utf-8", "surrogateescape") for scope, key in zip(fields[0::2], fields[1::2])
                   if scope not in _USERS_OWN})
    env = dict(os.environ, GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0")
    overrides = [("core.fsmonitor", "false"), ("core.hooksPath", "/dev/null"), ("diff.ignoreSubmodules", "dirty"),
                 ("protocol.allow", "never"), *((key, "") for key in keys)]
    start = int(env.get("GIT_CONFIG_COUNT") or 0)
    for i, (key, value) in enumerate(overrides, start):
        env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = key, value
    env["GIT_CONFIG_COUNT"] = str(start + len(overrides))
    return env
