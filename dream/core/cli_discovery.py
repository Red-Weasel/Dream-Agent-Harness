"""Where an installed provider CLI lives on this machine (DREAM-246).

A desktop launcher's PATH often lacks the folders a shell profile adds (nvm, ~/.grok/bin, ~/.local/bin), so a CLI
that is installed looked absent. This module is the one lookup the picker, the Council choices and the code that
actually spawns a CLI share, so what is shown as installed is the file that gets run.

It answers installation only. It never says anything about sign-in, account access or model access: it reads no
credential or configuration file, runs no process and no shell, sources no profile, changes no environment variable
and stores nothing. A found file is one the current user may execute according to the filesystem; nothing more is
claimed about it (where a symlink leads, who wrote it, or whether it is the genuine CLI).

Lookup order for a known name: PATH (shutil.which), then these fixed per-user folders, first match wins:
~/.local/bin, ~/.grok/bin, ~/.npm-global/bin, then <nvm>/versions/node/vX.Y.Z/bin from the highest stable version
down. <nvm> is NVM_DIR when it is set to an absolute path, otherwise ~/.nvm. Nothing else under the home folder is
looked at.

One more thing is answered for the spawning code (node_child_env): whether the found CLI is a `#!/usr/bin/env node`
script whose own folder holds the node it needs while the child's PATH has none, which is how an nvm install looks
from a desktop launcher. For that it reads at most the first 256 bytes of the found CLI file itself and returns a
separate environment mapping for the child. The parent's environment is still never changed.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
from typing import Mapping

# The only file names this module will look for. They are fixed: never a path, never flags, never user argv.
KNOWN_CLIS = ('claude', 'codex', 'grok', 'gemini', 'agy')

_USER_BIN_DIRS = (('.local', 'bin'), ('.grok', 'bin'), ('.npm-global', 'bin'))
# nvm names a version folder vMAJOR.MINOR.PATCH. Anything else (pre-releases, aliases, stray files) is skipped.
_NODE_VERSION = re.compile(r'v(\d{1,6})\.(\d{1,6})\.(\d{1,6})')
# Entries read from <nvm>/versions/node before giving up on the rest. Beyond this many the choice among the
# unread ones is not defined, which no real install approaches.
_MAX_NODE_ENTRIES = 512


def _usable(path: str) -> bool:
    """A regular file (symlinks followed) the current user may execute. Any filesystem error means no."""
    try:
        return os.path.isfile(path) and os.access(path, os.X_OK)
    except (OSError, ValueError):
        return False


def _home() -> str | None:
    try:
        home = os.path.expanduser('~')
    except (OSError, KeyError, RuntimeError):
        return None
    return home if os.path.isabs(home) else None


def _nvm_bin_dirs(home: str | None) -> list[str]:
    """nvm's node bin folders, highest stable version first. One bounded directory listing, errors mean none."""
    root = os.environ.get('NVM_DIR') or ''
    if not os.path.isabs(root):
        if home is None:
            return []
        root = os.path.join(home, '.nvm')
    base = os.path.join(root, 'versions', 'node')
    versions = []
    try:
        with os.scandir(base) as entries:
            for seen, entry in enumerate(entries):
                if seen >= _MAX_NODE_ENTRIES:
                    break
                match = _NODE_VERSION.fullmatch(entry.name)
                if match:
                    versions.append((tuple(int(part) for part in match.groups()), entry.name))
    except (OSError, ValueError):
        return []
    versions.sort(reverse=True)
    return [os.path.join(base, name, 'bin') for _, name in versions]


def find_cli(name: str) -> str | None:
    """The absolute path of an installed known CLI, or None. Installation only, never sign-in or access.

    A PATH hit is returned as PATH lookup gave it (made absolute); a fallback hit is checked to be an executable
    regular file. An unknown name is refused with ValueError before anything is looked up.
    """
    if not isinstance(name, str) or name not in KNOWN_CLIS:
        raise ValueError('Unknown provider CLI name')
    on_path = shutil.which(name)
    if on_path:
        return os.path.abspath(on_path)
    home = _home()
    folders = [os.path.join(home, *parts) for parts in _USER_BIN_DIRS] if home else []
    folders += _nvm_bin_dirs(home)
    for folder in folders:
        candidate = os.path.join(folder, name)
        if _usable(candidate):
            return candidate
    return None


def locate_command(command: str) -> str | None:
    """find_cli for a known CLI name. Any other command (an adapter pointed at an explicit path, a custom provider
    command) gets the PATH lookup it always had and never the fallback folders."""
    if isinstance(command, str) and command in KNOWN_CLIS:
        return find_cli(command)
    if not isinstance(command, str) or not command:
        return None
    return shutil.which(command)


# --- the child environment for an nvm-style install ----------------------------------------------------------------

# How much of the found CLI file is read to see its interpreter line. Never more.
_SHEBANG_BYTES = 256
# PATH entries looked at when checking whether the child already has a node. Beyond this the answer is "it has
# none", which only means the CLI's own folder is put in front.
_MAX_PATH_ENTRIES = 256
_ENV_PROGRAMS = (b'/usr/bin/env', b'/bin/env')


def _runs_under_env_node(path: str) -> bool:
    """Whether the file starts with `#!/usr/bin/env node` (or `env -S node ...`). Reads at most _SHEBANG_BYTES of a
    regular file (symlinks followed, as the kernel would to run it); anything else, or any error, means no."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_CLOEXEC', 0)
                     | getattr(os, 'O_NOCTTY', 0))
    except (OSError, ValueError):
        return False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return False
        head = os.read(fd, _SHEBANG_BYTES)
    except (OSError, ValueError):
        return False
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    if not head.startswith(b'#!'):
        return False
    line, newline, _ = head.partition(b'\n')
    if not newline and len(head) >= _SHEBANG_BYTES:
        return False                      # the interpreter line did not fit in what was read; do not guess
    words = line[2:].split()
    if len(words) < 2 or words[0] not in _ENV_PROGRAMS:
        return False
    if words[1] == b'-S':
        words = words[1:]
    return len(words) >= 2 and words[1] == b'node'


def _path_has_node(path_value: str) -> bool:
    """Whether a PATH value already leads to an executable `node`. Only absolute entries count."""
    for seen, folder in enumerate(path_value.split(os.pathsep)):
        if seen >= _MAX_PATH_ENTRIES:
            break
        if folder and os.path.isabs(folder) and _usable(os.path.join(folder, 'node')):
            return True
    return False


def node_child_env(name: str, found: str | None, env: Mapping[str, str] | None = None) -> dict[str, str] | None:
    """The environment a child needs so the found CLI can start its node, or None when nothing has to change.

    `name` is the known CLI name that was looked up and `found` is what find_cli returned for it, before any symlink
    was resolved. `env` is the environment the caller was going to give the child; None means the parent's.

    A mapping is returned only when all of this holds: `name` is a known CLI and `found` is an absolute path to a
    file of that name; that file is a `#!/usr/bin/env node` script; a `node` the current user may execute sits in the
    same folder; and the child's PATH would not otherwise find a node. The mapping is then a copy of `env` (or of the
    parent's environment when `env` is None) with that one folder put in front of PATH. Nothing else is added, so a
    caller's restricted environment stays restricted, and neither `env` nor os.environ is modified.

    None means "spawn exactly as before". The folder is never taken from user argv or configuration: it is the
    folder of the file the shared lookup found. Everything executable in that folder becomes reachable by bare name
    in the child, in front of the rest of PATH, as it is in the owner's own shell with nvm loaded.
    """
    if not isinstance(name, str) or name not in KNOWN_CLIS:
        return None
    if not isinstance(found, str) or not os.path.isabs(found) or os.path.basename(found) != name:
        return None
    folder = os.path.dirname(found)
    if not folder or os.pathsep in folder or '\x00' in folder:
        return None                       # one PATH entry exactly, never a way to add several
    base = os.environ if env is None else env
    current = base.get('PATH')
    if not isinstance(current, str):
        current = None
    # Cheapest check first; the CLI file itself is read only when the other two conditions already hold.
    if not _usable(os.path.join(folder, 'node')):
        return None
    if _path_has_node(os.defpath if current is None else current):
        return None
    if not _runs_under_env_node(found):
        return None
    child = dict(base)
    # An absent PATH means the system default search; keep that behind the one added folder. An empty PATH stays
    # without a second entry (an empty entry would mean the working directory).
    rest = os.defpath if current is None else current
    child['PATH'] = folder + os.pathsep + rest if rest else folder
    return child
