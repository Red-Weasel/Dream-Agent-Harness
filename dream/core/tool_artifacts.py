"""Private, lossless UTF-8 observations; no inference about a tool's outcome.

Call save with the ORIGINAL result before any preview cut. Success means fsynced
bytes were published without replacement. Failures raise ArtifactError; callers
must keep the failure visible, never fabricate a receipt or replay an effect.

Handles bind workspace inode/path, session, run and call identities. Raw result
bytes (including secrets) stay local, mode 0600; metadata contains hashes only.
Every managed path component is opened without following links. Cooperating
publishers serialize quota checks with flock. This is not a sandbox against
same-user shell access: that user can delete/change files, rewrite code or move
open directories. Reads verify the expected digest; a handle is not permission
for a different session/run. Linux/POSIX file operations are required.

Workspace/.dream group writes require a proven user-private group AND safe access
ACLs. All ancestors are checked; sticky shared parents require trusted child
ownership. Original files and artifact directories remain strictly owner-only.
Reads may remove a verified internal temporary alias left by an interrupted
publication; originals are never replaced. Unverified leftovers remain charged
against the quota. This housekeeping never reruns the underlying tool.
"""
from __future__ import annotations

import base64
import errno
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import grp
import pwd
import os
from pathlib import Path
import re
import secrets
import stat
import struct

try:
    import fcntl
except ImportError:  # fail closed on platforms without descriptor locks
    fcntl = None

MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_DISK_BYTES = 128 * 1024 * 1024
MAX_READ_BYTES = 32 * 1024
MAX_FILES = 4096
_HANDLE = re.compile(r'dream-artifact:v1:([0-9a-f]{64}):([0-9a-f]{64}):([0-9a-f]{64}):([0-9a-f]{64}):([0-9a-f]{64}):(0|[1-9][0-9]{0,9})\Z')
_PARTS = ('.dream', 'tool-artifacts', 'v1')
_FINAL = re.compile(r'[0-9a-f]{64}\.utf8\Z')
_TEMP = re.compile(r'\.([0-9a-f]{64})\.([0-9a-f]{64})\.[0-9a-f]{32}\.tmp\Z')
_LEGACY_TEMP = re.compile(r'\.[0-9a-f]{32}\.tmp\Z')


class ArtifactError(Exception):
    """Public failure code; underlying exception messages/paths are never exposed."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _key(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _identity(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 1024:
        raise ArtifactError('invalid_identity')
    try:
        return _key(value)
    except UnicodeError:
        raise ArtifactError('invalid_identity') from None


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ArtifactError('invalid_range')
    return value


def _acl_writers(fd):
    """Effective named POSIX ACL writers on a pinned Linux descriptor.

    ENODATA means no access ACL. ENOTSUP means this filesystem does not support
    POSIX ACLs. Other errors and unknown formats fail closed; mode/group ownership
    alone cannot establish safety when an access ACL is unreadable.
    """
    try:
        raw = os.getxattr(fd, 'system.posix_acl_access')
    except OSError as exc:
        if exc.errno in (errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP):
            return False
        raise ArtifactError('unsafe_acl') from None
    if len(raw) < 4 or len(raw) > 65536 or (len(raw) - 4) % 8 or struct.unpack_from('<I', raw)[0] != 2:
        raise ArtifactError('unsafe_acl')
    entries, named = {}, []
    for tag, permissions, identity in struct.iter_unpack('<HHI', raw[4:]):
        if tag not in (1, 2, 4, 8, 16, 32) or permissions > 7:
            raise ArtifactError('unsafe_acl')
        key = (tag, identity)
        if key in entries or (tag not in (2, 8) and identity != 0xffffffff):
            raise ArtifactError('unsafe_acl')
        entries[key] = permissions
        if tag in (2, 8):
            if identity == 0xffffffff:
                raise ArtifactError('unsafe_acl')
            named.append((tag, permissions, identity))
    if not all((tag, 0xffffffff) in entries for tag in (1, 4, 32)):
        raise ArtifactError('unsafe_acl')
    mask = entries.get((16, 0xffffffff))
    if named and mask is None:
        raise ArtifactError('unsafe_acl')
    return any(permissions & (mask if mask is not None else 7) & 2
               and (tag == 8 or identity != os.geteuid()) for tag, permissions, identity in named)


def _unsafe_writers(info, fd):
    """Permit group writes only when local accounts prove a user-private group.

    Unknown/unresolvable membership fails closed; no permission modes are changed.
    Artifact directories/files themselves retain the stricter owner-only check.
    """
    if _acl_writers(fd) or info.st_mode & 0o002:
        return True
    if not info.st_mode & 0o020:
        return False
    try:
        owner = pwd.getpwuid(os.geteuid())
        group = grp.getgrgid(info.st_gid)
        return (info.st_gid != owner.pw_gid
                or any(member != owner.pw_name for member in group.gr_mem)
                or any(user.pw_gid == info.st_gid and user.pw_uid != owner.pw_uid for user in pwd.getpwall()))
    except (KeyError, OSError):
        return True


def _private(info, *, directory=False, forbidden=0o077, nlinks=1, fd=None):
    unsafe = _unsafe_writers(info, fd) if directory and forbidden == 0o022 else info.st_mode & forbidden
    if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or info.st_uid != os.geteuid() or unsafe
            or (not directory and info.st_nlink != nlinks)):
        raise ArtifactError('unsafe_path')


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_uid, info.st_gid, info.st_mode, info.st_nlink)


@dataclass(frozen=True)
class ArtifactReceipt:
    version: int
    handle: str
    sha256: str
    full_bytes: int
    workspace_key: str
    session_key: str
    run_key: str
    call_key: str

    def metadata(self) -> dict:
        """Safe metadata only, suitable for UI; does not claim tool success."""
        return dict(vars(self))


class ArtifactStore:
    def __init__(self, workspace, session_id: str, run_id: str, *,
                 max_result_bytes=MAX_RESULT_BYTES, max_disk_bytes=MAX_DISK_BYTES,
                 max_read_bytes=MAX_READ_BYTES):
        self.max_result_bytes = _integer(max_result_bytes, 1, MAX_RESULT_BYTES)
        self.max_disk_bytes = _integer(max_disk_bytes, 1, MAX_DISK_BYTES)
        self.max_read_bytes = _integer(max_read_bytes, 4, MAX_READ_BYTES)
        self.session_key, self.run_key = _identity(session_id), _identity(run_id)
        if fcntl is None or not all(hasattr(os, x) for x in ('O_NOFOLLOW', 'O_DIRECTORY', 'geteuid', 'getxattr')):
            raise ArtifactError('unsupported_platform')
        try:
            raw = os.fspath(workspace)
            if not isinstance(raw, str) or not os.path.isabs(raw) or '..' in Path(raw).parts:
                raise ArtifactError('unsafe_path')
            self.workspace = os.path.normpath(raw)
            with self._root() as fd:
                info = os.fstat(fd)
                _private(info, directory=True, forbidden=0o022, fd=fd)
                self.workspace_key = _key(f'{self.workspace}\0{info.st_dev}\0{info.st_ino}')
        except (OSError, TypeError, ValueError):
            raise ArtifactError('unsafe_path') from None

    @contextmanager
    def _root(self):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open('/', flags)
        try:
            trusted = (0, os.geteuid())
            for part in Path(self.workspace).parts[1:]:
                parent = os.fstat(fd)
                shared = _unsafe_writers(parent, fd)
                # A foreign owner can relocate a private descendant even without
                # group/other write bits. A trusted sticky parent protects owned
                # children (e.g. /tmp); nonsticky shared ancestors cannot do so.
                if parent.st_uid not in trusted or (shared and not parent.st_mode & stat.S_ISVTX):
                    raise ArtifactError('unsafe_ancestor')
                inner = os.open(part, flags, dir_fd=fd)
                if shared and os.fstat(inner).st_uid not in trusted:
                    os.close(inner)
                    raise ArtifactError('unsafe_ancestor')
                os.close(fd)
                fd = inner
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def _folder(self, create=False):
        try:
            with self._root() as root:
                info = os.fstat(root)
                _private(info, directory=True, forbidden=0o022, fd=root)
                if self.workspace_key != _key(f'{self.workspace}\0{info.st_dev}\0{info.st_ino}'):
                    raise ArtifactError('scope')
                fd = os.dup(root)
            try:
                for index, part in enumerate(_PARTS):
                    if create:
                        try:
                            os.mkdir(part, 0o700, dir_fd=fd)
                        except FileExistsError:
                            pass
                        else:
                            os.fsync(fd)
                    inner = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                    os.close(fd)
                    fd = inner
                    _private(os.fstat(fd), directory=True, forbidden=0o022 if index == 0 else 0o077, fd=fd)
                yield fd
            finally:
                os.close(fd)
        except FileNotFoundError:
            raise ArtifactError('missing') from None
        except OSError:
            raise ArtifactError('storage_io') from None

    def _receipt(self, handle):
        match = _HANDLE.fullmatch(handle) if isinstance(handle, str) else None
        if match is None:
            raise ArtifactError('invalid_handle')
        workspace, session, run, call, digest, size = match.groups()
        if (workspace, session, run) != (self.workspace_key, self.session_key, self.run_key):
            raise ArtifactError('scope')
        if int(size) > self.max_result_bytes:
            raise ArtifactError('result_limit')
        return ArtifactReceipt(1, handle, digest, int(size), workspace, session, run, call)

    @staticmethod
    def _name(receipt):
        return _key(receipt.handle) + '.utf8'

    def _verified(self, folder, receipt):
        return self._verified_file(folder, self._name(receipt), receipt.sha256, receipt.full_bytes)

    def _verified_file(self, folder, name, digest, size, *, repair_temp=None):
        """Keep the verified inode open through any crash-temp cleanup."""
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=folder)
        try:
            before = os.fstat(fd)
            _private(before, nlinks=2 if repair_temp else 1)
            if before.st_size != size or size > MAX_RESULT_BYTES:
                raise ArtifactError('integrity')
            chunks, remaining = [], size + 1
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b''.join(chunks)
            after = os.fstat(fd)
            named = os.stat(name, dir_fd=folder, follow_symlinks=False)
            if (len(data) != size or hashlib.sha256(data).hexdigest() != digest
                    or _signature(before) != _signature(after) or _signature(named) != _signature(after)):
                raise ArtifactError('integrity')
            _private(after, nlinks=2 if repair_temp else 1)
            if repair_temp:
                temporary = os.stat(repair_temp, dir_fd=folder, follow_symlinks=False)
                if _signature(temporary) != _signature(after):
                    raise ArtifactError('integrity')
                # Exactly two links, BOTH in this owner-only folder, and the
                # full digest agrees. Never remove a managed original or an
                # outside/arbitrary alias. A crash here is safely repeatable.
                os.unlink(repair_temp, dir_fd=folder)
                os.fsync(folder)
                final = os.stat(name, dir_fd=folder, follow_symlinks=False)
                _private(final)
                if (final.st_dev, final.st_ino) != (after.st_dev, after.st_ino):
                    raise ArtifactError('integrity')
            return data
        finally:
            os.close(fd)

    def _recover(self, folder, receipt, *, only_target=False):
        """Under the directory lock, repair only verified link/unlink crash pairs.

        New temp names carry the target key and content digest, allowing unrelated
        saves to recover too. Legacy random temp aliases need the matching receipt
        to supply the digest; otherwise both names are preserved and charged to
        quota. Incomplete single-link temps are likewise kept, never guessed away.
        External or arbitrary hardlinks fail closed. Scanning and repair I/O have
        fixed ceilings independent of how many files an outside process added.
        """
        groups, used, count = {}, 0, 0
        with os.scandir(folder) as entries:
            for entry in entries:
                count += 1
                if count > MAX_FILES * 2:
                    raise ArtifactError('disk_limit')
                info = entry.stat(follow_symlinks=False)
                if info.st_nlink not in (1, 2):
                    raise ArtifactError('unsafe_path')
                _private(info, nlinks=info.st_nlink)
                used += info.st_size
                if used > MAX_DISK_BYTES * 2:
                    raise ArtifactError('disk_limit')
                groups.setdefault((info.st_dev, info.st_ino), []).append((entry.name, info))
        repaired_bytes = 0
        for entries in groups.values():
            name, info = entries[0]
            if info.st_nlink == 1:
                continue
            if len(entries) != 2:
                raise ArtifactError('unsafe_path')
            finals = [(n, i) for n, i in entries if _FINAL.fullmatch(n)]
            if len(finals) != 1:
                raise ArtifactError('unsafe_path')
            final, final_info = finals[0]
            temporary, temp_info = next((n, i) for n, i in entries if n != final)
            if _signature(final_info) != _signature(temp_info):
                raise ArtifactError('integrity')
            match = _TEMP.fullmatch(temporary)
            is_target = final == self._name(receipt)
            if match:
                target, digest = match.groups()
                if final != target + '.utf8':
                    raise ArtifactError('unsafe_path')
                if is_target and digest != receipt.sha256:
                    raise ArtifactError('integrity')
            elif _LEGACY_TEMP.fullmatch(temporary):
                if not is_target:
                    continue  # no trusted digest: retain both names and count both
                digest = receipt.sha256
            else:
                raise ArtifactError('unsafe_path')
            if only_target and not is_target:
                continue
            expected_size = receipt.full_bytes if is_target else final_info.st_size
            repaired_bytes += expected_size
            if repaired_bytes > MAX_DISK_BYTES:
                raise ArtifactError('disk_limit')
            self._verified_file(folder, final, digest, expected_size, repair_temp=temporary)
            count -= 1
            used -= temp_info.st_size
        # Write capacity must not block an existing verified read because an
        # unrelated publication left its temporary alias. The independent scan
        # ceiling above still bounds reads; only the requested pair is repaired.
        if not only_target and count > MAX_FILES:
            raise ArtifactError('disk_limit')
        return used, count

    def save(self, original: str, call_id: str) -> ArtifactReceipt:
        """Persist exact strict UTF-8; retrying an identical save is idempotent."""
        if not isinstance(original, str):
            raise ArtifactError('invalid_text')
        if len(original) > self.max_result_bytes:
            raise ArtifactError('result_limit')
        try:
            data = original.encode('utf-8')
        except UnicodeError:
            raise ArtifactError('invalid_utf8') from None
        if len(data) > self.max_result_bytes:
            raise ArtifactError('result_limit')
        call = _identity(call_id)
        digest = hashlib.sha256(data).hexdigest()
        handle = ':'.join(('dream-artifact', 'v1', self.workspace_key, self.session_key,
                           self.run_key, call, digest, str(len(data))))
        receipt = self._receipt(handle)
        with self._folder(create=True) as folder:
            fcntl.flock(folder, fcntl.LOCK_EX)
            try:
                used, count = self._recover(folder, receipt)
                try:
                    self._verified(folder, receipt)
                except FileNotFoundError:
                    pass
                else:
                    # A previous attempt may have published then failed its fsync.
                    os.fsync(folder)
                    return receipt
                if count >= MAX_FILES or used + len(data) > self.max_disk_bytes:
                    raise ArtifactError('disk_limit')
                temporary = f'.{self._name(receipt)[:-5]}.{digest}.{secrets.token_hex(16)}.tmp'
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=folder)
                try:
                    view = memoryview(data)
                    while view:
                        n = os.write(fd, view)
                        if n <= 0:
                            raise OSError('short write')
                        view = view[n:]
                    os.fsync(fd)
                    written = os.fstat(fd)
                    os.link(temporary, self._name(receipt), src_dir_fd=folder, dst_dir_fd=folder,
                            follow_symlinks=False)
                    named = os.stat(self._name(receipt), dir_fd=folder, follow_symlinks=False)
                    if (written.st_dev, written.st_ino) != (named.st_dev, named.st_ino):
                        raise ArtifactError('integrity')
                finally:
                    os.close(fd)
                    os.unlink(temporary, dir_fd=folder)
                os.fsync(folder)
                self._verified(folder, receipt)
                return receipt
            finally:
                fcntl.flock(folder, fcntl.LOCK_UN)

    def load_original(self, handle: str) -> str:
        """Host-only checkpoint hydration of one verified, bounded original.

        This is not registered as a model tool. Tool reads retain their page
        limit. Loading here verifies once rather than rereading the complete
        original for each page of a large checkpoint.
        """
        receipt = self._receipt(handle)
        with self._folder() as folder:
            fcntl.flock(folder, fcntl.LOCK_EX)
            try:
                self._recover(folder, receipt, only_target=True)
                data = self._verified(folder, receipt)
            finally:
                fcntl.flock(folder, fcntl.LOCK_UN)
        try:
            return data.decode('utf-8')
        except UnicodeError:
            raise ArtifactError('integrity') from None

    def read(self, handle: str, *, offset=0, limit=4096, unit='characters', query=None) -> dict:
        """Verify <=8MiB, return <=max_read_bytes. Bytes use base64 when split mid-UTF8.

        Search is a literal, case-sensitive first match at/after a character offset.
        Each page supplies an exact continuation offset; no line-length limitation.
        """
        receipt = self._receipt(handle)
        _integer(offset, 0, self.max_result_bytes)
        _integer(limit, 1, self.max_read_bytes)
        if unit not in ('characters', 'bytes'):
            raise ArtifactError('invalid_range')
        if query is not None and (not isinstance(query, str) or not 1 <= len(query) <= 256 or unit != 'characters'):
            raise ArtifactError('invalid_query')
        with self._folder() as folder:
            fcntl.flock(folder, fcntl.LOCK_EX)
            try:
                self._recover(folder, receipt, only_target=True)
                data = self._verified(folder, receipt)
            finally:
                fcntl.flock(folder, fcntl.LOCK_UN)
        try:
            whole = data.decode('utf-8')
        except UnicodeError:
            raise ArtifactError('integrity') from None
        content = whole if unit == 'characters' else data
        if offset > len(content):
            raise ArtifactError('invalid_range')
        found = None
        if query is not None:
            at = whole.find(query, offset)
            found = at >= 0
            offset = at if found else len(whole)
        piece = content[offset:offset + limit]
        if unit == 'characters':
            piece = piece.encode('utf-8')[:self.max_read_bytes].decode('utf-8', 'ignore')
            text, encoded = piece, None
        else:
            encoded = base64.b64encode(piece).decode('ascii')
            try:
                text = piece.decode('utf-8')
            except UnicodeError:
                text = None
        end = offset + len(piece)
        return {'version': 1, 'handle': handle, 'sha256': receipt.sha256, 'full_bytes': receipt.full_bytes,
                'unit': unit, 'offset': offset, 'next_offset': end, 'eof': end == len(content),
                'found': found, 'text': text, 'base64': encoded, 'untrusted': True}
