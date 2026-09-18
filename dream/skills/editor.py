"""Private skill drafts and overrides; editing never grants execution or trust."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from .. import config
from . import loader
from ..projects.workspace import _directory

MAX_CONTENT = 120_000
MAX_FILE = 1_000_000
MAX_BUNDLE = 8_000_000
MAX_FILES = 128
_NAME = re.compile(r'[a-z0-9][a-z0-9-]{0,79}\Z')


class Conflict(ValueError):
    pass


class NotFound(ValueError):
    pass


def _root():
    return config.DATA_DIR / 'skills'


def _target(name):
    return name if _NAME.fullmatch(name) else 'override-' + hashlib.sha256(name.casefold().encode()).hexdigest()


def _catalog():
    from ..tools.installed_skill_tools import inventory
    return inventory(refresh=True)


def _find(name):
    if not isinstance(name, str) or not name or len(name) > 256:
        raise ValueError('Skill name must be nonempty text of at most 256 characters.')
    for skill in _catalog():
        if skill.name.casefold() == name.casefold():
            return skill
    raise NotFound('Skill was not found. Refresh the list.')


def _managed(skill):
    return skill.root.absolute() == (_root() / _target(skill.name)).absolute()


def _info(skill):
    managed = _managed(skill)
    return {'name': skill.name, 'description': skill.description, 'source': skill.source,
            'provenance': 'private-managed' if managed else skill.provenance,
            'managed': managed, 'enabled': loader.enabled(skill), 'path': str(skill.manifest),
            'edit_target': str(_root() / _target(skill.name) / 'SKILL.md')}


def listing():
    from ..tools.installed_skill_tools import warnings
    skills = _catalog()
    return {'skills': [_info(skill) for skill in skills], 'warnings': warnings()}


def _bytes_at(fd, name, limit):
    try:
        child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except OSError as exc:
        raise ValueError('Cannot safely read the skill bundle; symlinks are not supported.') from exc
    try:
        info = os.fstat(child)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('Skill file is not regular or exceeds the editor size limit.')
        with os.fdopen(child, 'rb', closefd=False) as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError('Skill file exceeds the editor size limit.')
        return data
    finally:
        os.close(child)


def _content(skill):
    # The loader allows a symlink to the package root; individual bundle entries
    # must be regular files. Bind reads to the opened directory, not later paths.
    if _managed(skill):
        with _directory(_root()) as rootfd:
            fd = os.open(_target(skill.name), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=rootfd)
    else:
        fd = os.open(skill.root.resolve(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return _bytes_at(fd, skill.manifest.name, MAX_CONTENT).decode('utf-8')
    finally:
        os.close(fd)


def read(name):
    skill = _find(name)
    content = _content(skill)
    return {**_info(skill), 'content': content,
            'sha256': hashlib.sha256(content.encode()).hexdigest()}


def _validate(name, content):
    if not isinstance(content, str) or len(content.encode('utf-8')) > MAX_CONTENT:
        raise ValueError('SKILL.md must be UTF-8 text no larger than 120000 bytes.')
    if '\x00' in content:
        raise ValueError('SKILL.md must not contain NUL characters.')
    end = content.find('\n---', 3)
    if not content.startswith('---\n') or not 0 < end < loader.HEADER_MAX_CHARS:
        raise ValueError('Begin with YAML frontmatter containing name and description.')
    metadata = loader._frontmatter(content[:end + 4])
    if metadata.get('name') != name:
        raise ValueError('The frontmatter name must match the skill name; renaming is not supported.')
    desc = metadata.get('description')
    if not isinstance(desc, str) or not desc.strip() or len(desc) > 4096:
        raise ValueError('A nonempty description of at most 4096 characters is required.')


@contextmanager
def _locked_root():
    with _directory(_root(), create=True) as fd:
        lock = None
        try:
            if os.fstat(fd).st_uid != os.getuid():
                raise ValueError('The managed skill directory must belong to the current user.')
            lock = os.open('.editor.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                           0o600, dir_fd=fd)
            if not stat.S_ISREG(os.fstat(lock).st_mode):
                raise ValueError('Invalid skill editor lock file.')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Conflict('Another skill save is in progress. Retry after it completes.') from exc
            yield fd
        finally:
            if lock is not None:
                os.close(lock)


def _write(path, data):
    with path.open('xb') as stream:
        os.chmod(path, 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _copy_bundle(skill, stage):
    budget = [0, 0]
    def visit(fd, dest, depth):
        if depth > 8:
            raise ValueError('Bundle exceeds eight directory levels; prepare a smaller private skill.')
        for entry in os.scandir(fd):
            name = entry.name
            if name == skill.manifest.name and depth == 0:
                continue
            budget[0] += 1
            if budget[0] > MAX_FILES:
                raise ValueError('Bundle exceeds 128 entries; prepare a smaller private skill.')
            if name.startswith('.'):
                raise ValueError('Bundle contains hidden files; cannot preserve it safely in this editor.')
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise ValueError('Bundle contains a symlink; prepare a self-contained skill before editing.')
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    (dest / name).mkdir(mode=0o700)
                    visit(child, dest / name, depth + 1)
                finally:
                    os.close(child)
            else:
                data = _bytes_at(fd, name, MAX_FILE)
                budget[1] += len(data)
                if budget[1] > MAX_BUNDLE:
                    raise ValueError('Bundle exceeds 8 MB; prepare a smaller private skill.')
                _write(dest / name, data)
    fd = os.open(skill.root.resolve(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        visit(fd, stage, 0)
    finally:
        os.close(fd)


def _check_relative_references(content, bundle):
    # A copied package cannot retain paths that escape its source directory.
    if re.search(r'(?<!\w)\.\.[/\\]', content):
        raise ValueError('Skill references files outside its bundle; make it self-contained before saving an override.')
    from urllib.parse import unquote, urlsplit
    for raw in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', content):
        value = raw.strip()
        value = value[1:value.find('>')] if value.startswith('<') and '>' in value else value.split()[0] if value else ''
        parsed = urlsplit(value)
        if parsed.scheme in {'https', 'http', 'mailto'} or not parsed.path:
            continue
        relative = Path(unquote(parsed.path))
        if parsed.scheme or relative.is_absolute() or '..' in relative.parts or any(p.startswith('.') for p in relative.parts):
            raise ValueError('A local Markdown link escapes the bundle; make it self-contained before saving an override.')
        target = bundle / relative
        if not target.resolve().is_relative_to(bundle.resolve()):
            raise ValueError('A local Markdown link escapes the skill bundle.')
        if not target.exists():
            raise ValueError('A local Markdown link has no preserved support file. Add the file outside this editor before saving.')


def create(name, content):
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError('New skill names use lowercase letters, numbers and hyphens, up to 80 characters.')
    _validate(name, content)
    with _locked_root() as rootfd:
        if any(s.name.casefold() == name.casefold() for s in _catalog()):
            raise Conflict('A skill with this name already exists. Open it to edit.')
        _publish(rootfd, name, content)
    return read(name)


def _publish(rootfd, name, content, source=None):
    target = _target(name)
    if os.path.lexists(_root() / target):
        raise Conflict('The private edit target already exists. Refresh the list.')
    # Keep staging outside the discovery root, anchored to the opened parent.
    datafd = os.open('..', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=rootfd)
    stage = None
    try:
        stage = Path(tempfile.mkdtemp(prefix='skill-draft-', dir=f'/proc/self/fd/{datafd}'))
        if source is not None:
            _copy_bundle(source, stage)
        _write(stage / 'SKILL.md', content.encode())
        _check_relative_references(content, stage)
        for directory, _, _ in os.walk(stage, topdown=False):
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        os.rename(stage, target, dst_dir_fd=rootfd)
        os.fsync(rootfd)
    finally:
        try:
            if stage is not None and stage.exists():
                shutil.rmtree(stage)
        finally:
            os.close(datafd)


def save(name, content, expected_sha256):
    if not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
        raise ValueError('A current SHA-256 revision is required. Reopen this skill.')
    with _locked_root() as rootfd:
        skill = _find(name)
        _validate(skill.name, content)
        current = _content(skill)
        if hashlib.sha256(current.encode()).hexdigest() != expected_sha256:
            raise Conflict('Skill changed since it was opened. Reload before saving; your draft was not written.')
        if not _managed(skill):
            if not loader.enabled(skill):
                raise ValueError('This skill or its parent plugin is disabled. Enable it separately before creating an override.')
            _publish(rootfd, skill.name, content, source=skill)
        else:
            _check_relative_references(content, skill.root)
            fd = os.open(_target(skill.name), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=rootfd)
            temporary = '.draft-' + os.urandom(12).hex()
            try:
                out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                with os.fdopen(out, 'wb') as stream:
                    stream.write(content.encode())
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, 'SKILL.md', src_dir_fd=fd, dst_dir_fd=fd)
                os.fsync(fd)
            finally:
                try:
                    os.unlink(temporary, dir_fd=fd)
                except FileNotFoundError:
                    pass
                os.close(fd)
    return read(skill.name)
