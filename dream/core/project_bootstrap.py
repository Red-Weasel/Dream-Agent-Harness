"""Safely publish canonical workspace instructions and capture the files actually read.

All operations are relative to a pinned directory descriptor. Publication is a
hard link of a fully written temporary file: another reader sees absent or complete,
never a partially written DREAM.md. Existing entries are never replaced.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import fcntl
import hashlib
import grp
import pwd
import os
from pathlib import Path
import stat
import uuid

TEMPLATE = """# Project instructions

DREAM.md is this workspace's canonical project instruction source. Edit this file
with the project's purpose, conventions, relevant commands and completion checks.
The user's current instructions and global preferences take precedence.

## Working agreement

Read relevant project files before changing them. Preserve unrelated work.
Verify the requested outcome and report what was checked and what remains uncertain.
Do not claim completion from a plan or a worker's assertion alone.
"""
POINTERS = {
    "CLAUDE.md": "<!-- Dream managed pointer v1; edit DREAM.md for shared project rules. -->\n@DREAM.md\n",
    "AGENTS.md": "<!-- Dream managed pointer v1; edit DREAM.md for shared project rules. -->\nRead DREAM.md in this directory before working. It is the canonical project instruction source.\n",
}
NAMES = ("DREAM.md", "AGENTS.md", "CLAUDE.md")
_MAX_READ = 16 * 1024 * 1024
_ACL_FAILURES = frozenset({'extended_access_acl', 'access_acl_inspection_failed', 'access_acl_unsupported'})


@dataclass(frozen=True)
class Source:
    name: str
    raw: bytes
    size: int
    digest: str
    truncated: bool


def _owned(info):
    return info.st_uid == os.geteuid()


def _unsafe_writers(info):
    if info.st_mode & 0o002:
        return True
    if not info.st_mode & 0o020:
        return False
    # Standard user-private-group workspaces commonly use 775/664. Permit that
    # group only when the local account database identifies no other member.
    try:
        owner = pwd.getpwuid(os.geteuid())
        group = grp.getgrgid(info.st_gid)
        return (info.st_gid != owner.pw_gid
            or any(member != owner.pw_name for member in group.gr_mem)
            or any(user.pw_gid == info.st_gid and user.pw_uid != owner.pw_uid for user in pwd.getpwall()))
    except KeyError:
        return True


def _check_access_acl(fd):
    """Reject extended access ACLs on the exact opened object, conservatively.

    stat mode bits do not identify named users/groups granted access by an ACL.
    No xattr (ENODATA), or a filesystem that cannot implement POSIX ACL xattrs
    (ENOTSUP), leaves the mode/NSS checks authoritative. Other failures are unknown,
    never evidence of safe permissions. Default ACLs are a separate inheritance
    policy: a newly created temporary file is checked again before publication.
    """
    try:
        os.getxattr(fd, 'system.posix_acl_access')
    except AttributeError:
        raise PermissionError('access_acl_unsupported') from None
    except OSError as exc:
        if exc.errno in (errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP):
            return
        raise PermissionError('access_acl_inspection_failed') from None
    raise PermissionError('extended_access_acl')


@contextmanager
def _directory(workspace):
    # Walk every component without following directory symlinks, including ancestors.
    path = Path(os.path.abspath(os.fspath(workspace)))
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            parent = os.fstat(fd)
            _check_access_acl(fd)
            # A foreign ancestor owner can move even a mode-700 descendant. A
            # non-sticky shared directory lets other writers rename its children.
            # Root and this user are trusted; root-owned /tmp is safe only because
            # its sticky bit protects an owned child entry from other users.
            trusted_owners = (0, os.geteuid())
            if parent.st_uid not in trusted_owners:
                raise PermissionError('unsafe_ancestor_directory')
            shared = _unsafe_writers(parent)
            if shared and not parent.st_mode & stat.S_ISVTX:
                raise PermissionError('unsafe_ancestor_directory')
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            if shared and os.fstat(child).st_uid not in trusted_owners:
                os.close(child)
                raise PermissionError('unsafe_ancestor_directory')
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        _check_access_acl(fd)
        if not _owned(info) or _unsafe_writers(info):
            raise PermissionError('unsafe_directory')
        # Directory flock serializes Dream callers without another visible lock file.
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd, info
    finally:
        os.close(fd)


def _matches(fd, name):
    return sorted(n for n in os.listdir(fd) if n.casefold() == name.casefold())


def _publish(fd, name, text):
    """False means a concurrent existing entry won. Never unlink/replace that entry."""
    _check_access_acl(fd)
    if _matches(fd, name):
        return False
    temporary = '.dream-instructions-' + uuid.uuid4().hex
    child = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600, dir_fd=fd)
    try:
        with os.fdopen(child, 'wb') as output:
            _check_access_acl(output.fileno())
            output.write(text.encode('utf-8'))
            output.flush()
            os.fsync(output.fileno())
        _check_access_acl(fd)
        if _matches(fd, name):
            return False
        try:
            os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        except FileExistsError:
            return False
        os.fsync(fd)
        return True
    finally:
        os.unlink(temporary, dir_fd=fd)


def _read(fd, name, cap):
    entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
    if stat.S_ISLNK(entry.st_mode):
        raise PermissionError('symlink')
    if not stat.S_ISREG(entry.st_mode):
        raise PermissionError('not_regular_file')
    if not _owned(entry):
        raise PermissionError('foreign_owned_file')
    if _unsafe_writers(entry):
        raise PermissionError('unsafe_file_permissions')
    if entry.st_size > _MAX_READ:
        raise ValueError('instruction_file_too_large')
    child = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
    with os.fdopen(child, 'rb') as source:
        before = os.fstat(source.fileno())
        if before != entry:
            raise RuntimeError('file_changed_before_read')
        _check_access_acl(source.fileno())
        raw = source.read(_MAX_READ + 1)
        after = os.fstat(source.fileno())
        current = os.stat(name, dir_fd=fd, follow_symlinks=False)
    # Ignore atime, which this read can itself change.
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_uid, s.st_mode)
    if identity(before) != identity(after) or identity(before) != identity(current) or len(raw) != before.st_size:
        raise RuntimeError('file_changed_during_read')
    return Source(name, raw[:cap], len(raw), hashlib.sha256(raw).hexdigest(), len(raw) > cap)


def capture(workspace, cap, *, create=True):
    """Return (read snapshots, diagnostic records), creating absent files safely.

    Diagnostics contain fixed reasons and basenames only, never exception paths or
    arbitrary directory contents. Missing/unreadable rules remain visible to the model.
    """
    sources, notices = [], []
    def notice(name, reason):
        notices.append({'file': name, 'reason': reason})
    try:
        with _directory(workspace) as (fd, info):
            writable = bool(info.st_mode & stat.S_IWUSR)
            if create and not writable:
                notice('DREAM.md', 'read_only_directory')
            if create and not _matches(fd, 'DREAM.md') and writable:
                try:
                    _publish(fd, 'DREAM.md', TEMPLATE)
                except OSError as exc:
                    notice('DREAM.md', str(exc) if str(exc) in _ACL_FAILURES else 'creation_failed')
            for canonical in NAMES:
                matches = _matches(fd, canonical)
                if len(matches) > 1:
                    notice(canonical, 'ambiguous_case_variants')
                    continue
                if not matches:
                    continue
                name = matches[0]
                try:
                    source = _read(fd, name, cap)
                except (OSError, ValueError, RuntimeError) as exc:
                    reason = str(exc) if str(exc) in _ACL_FAILURES | {
                        'symlink', 'not_regular_file', 'foreign_owned_file', 'unsafe_file_permissions',
                        'instruction_file_too_large', 'file_changed_before_read', 'file_changed_during_read',
                    } else 'read_failed'
                    notice(canonical, reason)
                    continue
                sources.append(source)
            # Native import requires exact case on case-sensitive filesystems and a safe canonical file.
            if create and any(s.name == 'DREAM.md' for s in sources) and writable:
                for name, text in POINTERS.items():
                    try:
                        _publish(fd, name, text)
                    except OSError as exc:
                        notice(name, str(exc) if str(exc) in _ACL_FAILURES else 'pointer_creation_failed')
            # A same-owner rename cannot redirect descriptor-relative writes, but it
            # can change which directory the session's workspace path now names.
            # Do not attribute that detached directory's rules to the replacement.
            current = os.stat(workspace, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                sources.clear()
                notice('workspace', 'workspace_changed_during_capture')
    except (OSError, ValueError, TypeError) as exc:
        reason = (str(exc) if str(exc) in _ACL_FAILURES | {'unsafe_directory', 'unsafe_ancestor_directory'}
                  else 'workspace_unavailable')
        sources.clear()
        notice('workspace', reason)
    return sources, notices


def initialize(workspace):
    """Workspace-open hook: initialize files and return bounded, display-safe warnings.

    Prompt preparation captures again, recording source hashes and any continuing
    failure. No source contents or arbitrary exception strings enter these notices.
    """
    from .. import config
    _, notices = capture(workspace, config.PROJECT_INSTRUCTIONS_MAX)
    return [f"Project instructions unavailable: {item['file']}: {item['reason']}. "
            "The workspace is still usable; review its instruction files." for item in notices]
