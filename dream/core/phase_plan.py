"""Strict operator JSON plans for bounded, declarative PhaseRunner file checks.

No commands, code, plugin names or imports are accepted from a plan. Predicates
establish only observed file properties, never execution of task-specific tests.
Text is exact UTF-8 bytes. Lines are LF-delimited, counting a final unterminated
line; nonempty lines contain something other than ASCII space/tab/CR/VT/FF.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import weakref

from .phase_runner import PhaseLimits, PhaseSpec
from ..memory.project import project_key
from .tool_artifacts import ArtifactError, _acl_writers, _unsafe_writers

MAX_PLAN_BYTES = 128 * 1024
MAX_FILE_BYTES = 1024 * 1024
MAX_CHECK_BYTES = 4 * 1024 * 1024
MAX_PREDICATES = 16
_MAX_TEXT_BYTES = 8192
_CHECKER_VERSION = 1


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',', ':'),ensure_ascii=False,allow_nan=False).encode('utf-8')


def _integer(value,name,maximum,minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('Invalid bounded integer: '+name)
    return value


def _text(value,name,maximum,*,empty=False):
    if (not isinstance(value,str) or len(value)>maximum or len(value.encode('utf-8'))>maximum
            or (not empty and not value.strip())):
        raise ValueError('Invalid bounded text: '+name)
    return value


def _fields(value,required,optional=()):
    if type(value) is not dict or not set(required)<=set(value) or set(value)-set(required)-set(optional):
        raise ValueError('Missing or unknown phase-plan fields')


def _identity(info):
    return info.st_dev,info.st_ino


def _signature(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns,info.st_uid,info.st_gid)


def _absolute(path):
    value=os.fspath(path)
    if not isinstance(value,str) or not value or '\x00' in value or len(value.encode('utf-8'))>8192:
        raise ValueError('Invalid explicit filesystem path')
    return Path(os.path.abspath(value))


def _authority(info,fd,*,directory=False):
    """Selected operator-plan provenance, separate from result predicates."""
    try:
        if _acl_writers(fd):raise ValueError('Unsafe operator-plan authority ACL writers')
        unsafe=_unsafe_writers(info,fd)
    except (ArtifactError,AttributeError,OSError,TypeError) as exc:
        raise ValueError('Unreadable or unsafe operator-plan authority ACL') from exc
    if info.st_uid not in (0,os.geteuid()):
        raise ValueError('Foreign operator-plan authority owner')
    if unsafe and not (directory and info.st_mode&stat.S_ISVTX):
        raise ValueError('Shared writable operator-plan authority')


def _directory(path,*,authority=False):
    """Open from / using descriptor-relative nofollow traversal at every step."""
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            if authority:_authority(os.fstat(fd),fd,directory=True)
            next_fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=next_fd
        if authority:_authority(os.fstat(fd),fd,directory=True)
        return fd
    except BaseException:
        os.close(fd);raise


def _open_file(root_fd,parts):
    directory=os.dup(root_fd);chain=[]
    try:
        for part in parts[:-1]:
            next_fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            chain.append(_identity(os.fstat(next_fd)))
            os.close(directory);directory=next_fd
        fd=os.open(parts[-1],os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=directory)
        return fd,tuple(chain)
    finally:os.close(directory)


def _regular(info,maximum):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or not 0<=info.st_size<=maximum:
        raise ValueError('Check requires a bounded regular file without hard links')


def _read(root_fd,parts,maximum,*,authority=False):
    if authority:_authority(os.fstat(root_fd),root_fd,directory=True)
    fd,chain=_open_file(root_fd,parts)
    try:
        before=os.fstat(fd);_regular(before,maximum)
        if authority:_authority(before,fd)
        chunks=[];size=0
        while True:
            chunk=os.read(fd,min(65536,maximum+1-size))
            if not chunk:break
            size+=len(chunk)
            if size>maximum:raise ValueError('File grew beyond bounded read capacity')
            chunks.append(chunk)
        after=os.fstat(fd)
        if authority:_authority(after,fd)
        if _signature(before)!=_signature(after) or size!=before.st_size:
            raise ValueError('File changed during bounded read')
        return b''.join(chunks),(_signature(after),chain)
    finally:os.close(fd)


def _unchanged(root_fd,parts,snapshot,*,authority=False):
    if authority:_authority(os.fstat(root_fd),root_fd,directory=True)
    fd,chain=_open_file(root_fd,parts)
    try:
        info=os.fstat(fd);_regular(info,MAX_FILE_BYTES)
        if authority:_authority(info,fd)
        return (_signature(info),chain)==snapshot
    finally:os.close(fd)


def _read_plan(path,*,authority=False):
    # Installed checker source is already trusted executable code. Its digest
    # uses stable descriptors without imposing operator-plan permission rules.
    parent=_directory(path.parent,authority=authority)
    try:
        parent_id=_identity(os.fstat(parent))
        data,snapshot=_read(parent,(path.name,),MAX_PLAN_BYTES,authority=authority)
        if not _unchanged(parent,(path.name,),snapshot,authority=authority):
            raise ValueError('Plan changed during loading')
        fresh=_directory(path.parent,authority=authority)
        try:
            if _identity(os.fstat(fresh))!=parent_id or not _unchanged(fresh,(path.name,),snapshot,authority=authority):
                raise ValueError('Plan path changed during loading')
        finally:os.close(fresh)
        return data
    finally:os.close(parent)


def _relative(value):
    _text(value,'predicate path',1024)
    parts=value.split('/')
    if (len(parts)>32 or value.startswith(('/', '~')) or '\\' in value or ':' in value
            or any(part in ('','.','..') or len(part.encode('utf-8'))>255 for part in parts)
            or any(ord(char)<32 or ord(char)==127 for char in value)):
        raise ValueError('Predicate paths must be bounded relative POSIX file paths')
    return tuple(parts)


@dataclass(frozen=True)
class _Predicate:
    path: str
    min_bytes: int | None = None
    sha256: str | None = None
    text: str | None = None
    lines: int | None = None
    nonempty_lines: int | None = None

    def matches(self,body):
        if self.min_bytes is not None and len(body)<self.min_bytes:return False
        if self.sha256 is not None and _sha(body)!=self.sha256:return False
        if self.text is not None and body!=self.text.encode('utf-8'):return False
        if self.lines is not None or self.nonempty_lines is not None:
            lines=body.split(b'\n')
            if not body or body.endswith(b'\n'):lines.pop()
            if self.lines is not None and len(lines)!=self.lines:return False
            nonempty=sum(bool(line.strip(b' \t\r\v\f')) for line in lines)
            if self.nonempty_lines is not None and nonempty!=self.nonempty_lines:return False
        return True


@dataclass(frozen=True)
class _WorkspaceAnchor:
    path: Path
    identity: tuple[int,int]
    fd: int = field(repr=False,compare=False)

    def __post_init__(self):
        # Keep the inode allocated for this loaded plan's lifetime. The finalizer
        # retains only the integer descriptor, never the anchor itself.
        object.__setattr__(self,'_finalizer',weakref.finalize(self,os.close,self.fd))

    def close(self):
        self._finalizer()

    @property
    def alive(self):
        return self._finalizer.alive


@dataclass(frozen=True)
class _AcceptanceCheck:
    anchor: _WorkspaceAnchor
    predicates: tuple[_Predicate,...]

    def __call__(self):
        root=None
        try:
            if not self.anchor.alive:return False
            root=_directory(self.anchor.path)
            if _identity(os.fstat(root))!=self.anchor.identity:return False
            remaining=MAX_CHECK_BYTES;snapshots=[]
            for predicate in self.predicates:
                parts=_relative(predicate.path)
                body,snapshot=_read(root,parts,min(MAX_FILE_BYTES,remaining))
                remaining-=len(body)
                if not predicate.matches(body):return False
                snapshots.append((parts,snapshot))
            # Validate every observed file again after all reads; an early file
            # cannot silently change while a later predicate is being checked.
            for parts,snapshot in snapshots:
                if not _unchanged(root,parts,snapshot):return False
            fresh=_directory(self.anchor.path)
            try:
                if _identity(os.fstat(fresh))!=self.anchor.identity:return False
                for parts,snapshot in snapshots:
                    if not _unchanged(fresh,parts,snapshot):return False
            finally:os.close(fresh)
            return self.anchor.alive
        except (OSError,ValueError,UnicodeError):
            return False
        finally:
            if root is not None:os.close(root)


@dataclass(frozen=True)
class PhasePlan:
    goal: str
    project: str
    phases: tuple[PhaseSpec,...]
    limits: PhaseLimits
    workspace: Path
    source_sha256: str
    _anchor: _WorkspaceAnchor = field(repr=False,compare=False)

    def close(self):
        """Release the single workspace anchor; safe to call more than once."""
        self._anchor.close()

    def __enter__(self):
        if not self._anchor.alive:raise ValueError('Phase plan is closed')
        return self

    def __exit__(self,*args):
        self.close()


def _pairs(items):
    result={}
    for key,value in items:
        if key in result:raise ValueError('Duplicate JSON key in phase plan')
        result[key]=value
    return result


def _nonfinite(value):
    raise ValueError('Nonfinite JSON number in phase plan')


def _predicate(raw):
    _fields(raw,('path',),('min_bytes','sha256','text','lines','nonempty_lines'))
    if len(raw)==1:raise ValueError('A file predicate must assert at least one property')
    _relative(raw['path'])
    for name in ('min_bytes','lines','nonempty_lines'):
        if name in raw:_integer(raw[name],name,MAX_FILE_BYTES)
    if 'sha256' in raw and (not isinstance(raw['sha256'],str) or re.fullmatch('[0-9a-f]{64}',raw['sha256']) is None):
        raise ValueError('Expected an exact lowercase SHA256')
    if 'text' in raw:_text(raw['text'],'exact UTF8 text',_MAX_TEXT_BYTES,empty=True)
    return _Predicate(**raw)


def load_phase_plan(path,*,workspace):
    """Read one selected plan and capture immutable file-check definitions.

    A check returns False for unavailable, unsafe, oversized or changing files.
    Plan/schema/scope errors raise ValueError/OSError before any engine startup.
    No filesystem content beyond the selected plan and explicit workspace/file
    descriptors is discovered or read. The checker source itself is hash-pinned.
    """
    workspace=_absolute(workspace);root=_directory(workspace)
    try:
        identity=_identity(os.fstat(root))
        anchor=_WorkspaceAnchor(workspace,identity,root)
    except BaseException:
        os.close(root);raise
    try:
        return _load_captured_plan(path,workspace,anchor)
    except BaseException:
        anchor.close();raise


def _load_captured_plan(path,workspace,anchor):
    identity=anchor.identity
    source=_read_plan(_absolute(path),authority=True);source_sha256=_sha(source)
    try:
        data=json.loads(source.decode('utf-8'),object_pairs_hook=_pairs,parse_constant=_nonfinite)
    except (UnicodeError,json.JSONDecodeError,RecursionError) as exc:
        raise ValueError('Phase plan must be bounded valid UTF8 JSON') from exc
    _fields(data,('version','goal','limits','phases'),('project',))
    if type(data['version']) is not int or data['version']!=1:raise ValueError('Unsupported phase-plan version')
    goal=_text(data['goal'],'goal',8192)
    project=project_key(workspace)
    if 'project' in data and _text(data['project'],'project',4096,empty=True)!=project:
        raise ValueError('Explicit plan project must equal the canonical workspace project key')
    _fields(data['limits'],('max_dispatches','max_wall_seconds'))
    limits=PhaseLimits(**data['limits'])
    if type(data['phases']) is not list or not 1<=len(data['phases'])<=32:
        raise ValueError('A plan requires 1–32 phases')
    implementation_sha256=_sha(_read_plan(_absolute(__file__)))
    specs=[];ids=set()
    for phase in data['phases']:
        _fields(phase,('id','goal','contract','check_name','acceptance'))
        _fields(phase['acceptance'],('all',))
        predicates=phase['acceptance']['all']
        if type(predicates) is not list or not 1<=len(predicates)<=MAX_PREDICATES:
            raise ValueError('Acceptance requires ALL of 1–16 explicit file predicates')
        predicates=tuple(_predicate(raw) for raw in predicates)
        if len({p.path for p in predicates})!=len(predicates):raise ValueError('Duplicate predicate file path')
        definition={'version':_CHECKER_VERSION,'implementation_sha256':implementation_sha256,
                    'source_sha256':source_sha256,'workspace':str(workspace),'workspace_identity':identity,
                    'all':[asdict(p) for p in predicates]}
        spec=PhaseSpec(phase['id'],phase['goal'],phase['contract'],phase['check_name'],_sha(_canonical(definition)),
                       _AcceptanceCheck(anchor,predicates))
        if spec.id in ids:raise ValueError('Duplicate phase identity')
        specs.append(spec);ids.add(spec.id)
    # Recheck the captured root after parsing, without rejecting legitimate
    # additions/changes to children: only directory identity is pinned.
    root=_directory(workspace)
    try:
        if _identity(os.fstat(root))!=identity:raise ValueError('Workspace changed during plan loading')
    finally:os.close(root)
    return PhasePlan(goal,project,tuple(specs),limits,workspace,source_sha256,anchor)
