"""Private project Markdown notes; explicit recall and compare-before-save writes."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from .. import config
from .workspace import ProjectError, StaleRevision, _directory

MAX_BYTES = 150_000
MAX_CONTEXT = 6000
PREFIX = '<!-- dream-project-document '


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', value):
        raise ProjectError('Invalid project or document identity.')
    return value


class ProjectDocuments:
    def __init__(self, project_id):
        self.root = config.DATA_DIR / 'project-documents' / _id(project_id)

    @contextmanager
    def _locked(self):
        with _directory(self.root, create=True) as directory:
            fd = os.open('.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ProjectError('Document lock is not a regular file.')
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise StaleRevision('Another document save is in progress. Retry shortly.') from exc
                yield directory
            finally:
                os.close(fd)

    def _read(self, directory, identifier):
        fd = os.open(_id(identifier)+'.md', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd,'rb') as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
                raise ProjectError('Project document is not a bounded regular Markdown file.')
            raw = source.read(MAX_BYTES+1)
            after = os.fstat(source.fileno())
            if len(raw)>MAX_BYTES or (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                raise ProjectError('Document changed while reading. Try again.')
        header, sep, content = raw.decode('utf-8').partition('\n')
        if not sep or not header.startswith(PREFIX) or not header.endswith(' -->'):
            raise ProjectError('Document metadata is invalid; preserve the file for recovery.')
        try:
            meta=json.loads(header[len(PREFIX):-4])
        except (ValueError, RecursionError) as exc:
            raise ProjectError('Document metadata is invalid; preserve the file for recovery.') from exc
        if (not isinstance(meta,dict) or meta.get('id')!=identifier or meta.get('kind') not in {'reference','memory'}
                or not isinstance(meta.get('title'),str) or not 1<=len(meta['title'])<=160
                or type(meta.get('include')) is not bool):
            raise ProjectError('Document metadata is invalid.')
        return {**meta,'content':content,'sha256':hashlib.sha256(raw).hexdigest()}

    def _list(self,directory):
        with os.scandir(directory) as entries:
            names=[]
            for entry in entries:
                if entry.name.endswith('.md'):
                    names.append(entry.name[:-3])
                    if len(names)>100:
                        raise ProjectError('Project document limit is 100; preserve excess files for recovery.')
        return [self._read(directory,name) for name in sorted(names)]

    def list(self):
        try:
            with _directory(self.root) as directory:
                return self._list(directory)
        except FileNotFoundError:
            if self.root.exists():
                raise ProjectError('A document changed during listing. Refresh the project.')
            return []

    def get(self,identifier):
        with _directory(self.root) as directory:
            return self._read(directory,_id(identifier))

    def save(self,payload,identifier=None):
        title,content=payload.get('title'),payload.get('content')
        kind,include=payload.get('kind','reference'),payload.get('include',False)
        if not isinstance(title,str) or not 1<=len(title.strip())<=160:
            raise ProjectError('Use a document title of 1–160 characters.')
        if not isinstance(content,str) or len(content.encode('utf-8'))>120_000:
            raise ProjectError('Document content must be text of at most 120,000 UTF-8 bytes.')
        if kind not in {'reference','memory'} or type(include) is not bool:
            raise ProjectError('Choose reference or memory and an explicit context selection.')
        with self._locked() as directory:
            documents=self._list(directory)
            if identifier is not None:
                previous=self._read(directory,identifier)
                if payload.get('expected_sha256')!=previous['sha256']:
                    raise StaleRevision('Document changed since it was opened. Keep your draft and reload the saved version.')
            else:
                if len(documents)>=100:
                    raise ProjectError('Project has reached 100 documents.')
                identifier=uuid.uuid4().hex
            meta={'id':identifier,'title':title.strip(),'kind':kind,'include':include,
                  'updated_at':datetime.now(timezone.utc).isoformat(timespec='seconds')}
            candidate={**meta,'content':content}
            included=[d for d in documents if d['id']!=identifier]+[candidate]
            if len(_context(included))>MAX_CONTEXT:
                raise ProjectError('Selected project context exceeds 6,000 characters. Shorten notes or deselect another document.')
            raw=(PREFIX+json.dumps(meta,ensure_ascii=False)+' -->\n'+content).encode('utf-8')
            temporary='.write-'+uuid.uuid4().hex
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
            try:
                with os.fdopen(fd,'wb') as target:
                    target.write(raw);target.flush();os.fsync(target.fileno())
                os.replace(temporary,identifier+'.md',src_dir_fd=directory,dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try:os.unlink(temporary,dir_fd=directory)
                except FileNotFoundError:pass
            return self._read(directory,identifier)


def _context(documents):
    return '\n\n'.join(f"### {d['title']} ({d['kind']})\n{d['content']}" for d in documents if d['include'])


def build_context(project_id):
    value=_context(ProjectDocuments(project_id).list())
    if len(value)>MAX_CONTEXT:
        raise ProjectError('Saved project context exceeds 6,000 characters; review selected documents in Projects.')
    return value
