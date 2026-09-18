"""Durable, workspace-scoped metadata and streamed media storage."""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import stat
import uuid


class MediaError(ValueError):
    """Invalid media operation or unavailable workspace worker."""


_TRANSITIONS = {
    'queued': {'running', 'awaiting_user', 'cancelled', 'failed', 'interrupted'},
    'running': {'cancel_requested', 'cancelled', 'succeeded', 'failed', 'interrupted', 'unknown', 'awaiting_user'},
    'awaiting_user': {'succeeded', 'cancelled', 'failed'},
    'cancel_requested': {'cancelled', 'succeeded', 'failed', 'interrupted', 'unknown'},
    'unknown': {'running', 'succeeded', 'failed', 'cancelled'},
    'cancelled': set(), 'succeeded': set(), 'failed': set(), 'interrupted': set(),
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    try:
        return json.dumps(value, allow_nan=False, sort_keys=True, separators=(',', ':'))
    except (TypeError, ValueError) as exc:
        raise MediaError('Value must be finite JSON data') from exc


def _id(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
        raise MediaError('Invalid media ID')
    return value


class MediaStore:
    """Each call uses a separate SQLite transaction; no process-global connection.

    Imports must already be inside the workspace. Files are streamed in 1 MiB
    chunks and capped at 2 GiB. Worker locks are nonblocking and non-reentrant.
    recover_jobs obtains its own lock; call it outside an existing worker lock.
    """

    MAX_ASSET_BYTES = 2 * 1024**3
    CHUNK_SIZE = 1024**2

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).absolute()
        self._safe(self.workspace)
        if not self.workspace.is_dir():
            raise MediaError('Workspace must be an existing directory')
        self.root = self.workspace / '.dream' / 'media'
        for path in (self.root.parent, self.root, self.root / 'assets', self.root / 'tmp'):
            self._safe(path)
            path.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.db_path = self.root / 'media.sqlite3'
        self._safe(self.db_path)
        fd = os.open(self.db_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, revision INTEGER NOT NULL,
                    composition TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions (
                    project_id TEXT NOT NULL REFERENCES projects(id), revision INTEGER NOT NULL,
                    composition TEXT NOT NULL, created TEXT NOT NULL,
                    PRIMARY KEY(project_id, revision));
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    name TEXT NOT NULL, path TEXT NOT NULL, mime TEXT NOT NULL,
                    size INTEGER NOT NULL, sha256 TEXT NOT NULL, provenance TEXT NOT NULL,
                    created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    kind TEXT NOT NULL, status TEXT NOT NULL, request TEXT NOT NULL,
                    backend_id TEXT, progress REAL NOT NULL, result TEXT, error TEXT,
                    created TEXT NOT NULL, updated TEXT NOT NULL, idempotency_key TEXT,
                    UNIQUE(project_id, idempotency_key));
            ''')

    def _safe(self, path):
        path = Path(os.path.abspath(path))
        if not path.is_relative_to(self.workspace):
            raise MediaError('Path escapes workspace')
        # Check all ancestors, including the workspace itself, before resolving.
        for part in (*reversed(path.parents), path):
            if part.is_symlink():
                raise MediaError('Symlink paths are not allowed in media storage')
        return path

    @contextmanager
    def _db(self):
        self._safe(self.db_path)
        for suffix in ('-journal', '-wal', '-shm'):
            self._safe(Path(str(self.db_path) + suffix))
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _record(row):
        value = dict(row)
        value.pop('idempotency_key', None)
        for key in ('composition', 'provenance', 'request', 'result'):
            if key in value and value[key] is not None:
                value[key] = json.loads(value[key])
        return value

    def _get(self, db, table, identifier):
        row = db.execute(f'SELECT * FROM {table} WHERE id=?', (_id(identifier),)).fetchone()
        if row is None:
            raise MediaError(f'Unknown {table[:-1]} ID')
        return self._record(row)

    def _refs(self, db, project_id, value):
        if isinstance(value, dict):
            for key, item in value.items():
                ids = [item] if key in {'asset_id', 'audio_asset_id'} and item else item if key == 'asset_ids' else []
                if not isinstance(ids, list):
                    raise MediaError('asset_ids must be a list')
                for identifier in ids:
                    asset = self._get(db, 'assets', identifier)
                    if asset['project_id'] != project_id:
                        raise MediaError('Asset belongs to another project')
                self._refs(db, project_id, item)
        elif isinstance(value, list):
            for item in value:
                self._refs(db, project_id, item)

    def create_project(self, title, composition=None):
        if not isinstance(title, str) or not title.strip() or len(title) > 500:
            raise MediaError('Project title must contain 1 to 500 characters')
        composition = {} if composition is None else composition
        if not isinstance(composition, dict):
            raise MediaError('Composition must be an object')
        encoded, identifier, now = _json(composition), uuid.uuid4().hex, _now()
        with self._db() as db:
            self._refs(db, identifier, composition)
            db.execute('INSERT INTO projects VALUES (?,?,?,?,?,?)', (identifier, title.strip(), 1, encoded, now, now))
            db.execute('INSERT INTO revisions VALUES (?,?,?,?)', (identifier, 1, encoded, now))
            return self._get(db, 'projects', identifier)

    def list_projects(self):
        with self._db() as db:
            return [self._record(x) for x in db.execute('SELECT * FROM projects ORDER BY created,id')]

    def get_project(self, id):
        with self._db() as db:
            return self._get(db, 'projects', id)

    def get_revision(self, project_id, revision):
        """Return an immutable composition snapshot with its original timestamp."""
        if type(revision) is not int or revision < 1:
            raise MediaError('Invalid revision')
        with self._db() as db:
            self._get(db, 'projects', project_id)
            row = db.execute('SELECT * FROM revisions WHERE project_id=? AND revision=?',
                             (project_id, revision)).fetchone()
            if row is None:
                raise MediaError('Unknown project revision')
            return self._record(row)

    def update_project(self, id, composition, expected_revision):
        if not isinstance(composition, dict):
            raise MediaError('Composition must be an object')
        encoded = _json(composition)
        with self._db() as db:
            p = self._get(db, 'projects', id)
            if type(expected_revision) is not int or p['revision'] != expected_revision:
                raise MediaError(f"Conflicting revision; current revision is {p['revision']}")
            self._refs(db, id, composition)
            now, revision = _now(), expected_revision + 1
            db.execute('INSERT INTO revisions VALUES (?,?,?,?)', (id, revision, encoded, now))
            db.execute('UPDATE projects SET revision=?,composition=?,updated=? WHERE id=?', (revision, encoded, now, id))
            return self._get(db, 'projects', id)

    def import_asset(self, project_id, source: Path, *, provenance=None):
        self.get_project(project_id)
        source = Path(source)
        if not source.is_absolute():
            source = self.workspace / source
        source = self._safe(source)
        provenance = {} if provenance is None else provenance
        if not isinstance(provenance, dict):
            raise MediaError('Provenance must be an object')
        encoded = _json({**provenance, 'source': str(source.relative_to(self.workspace))})
        temporary = self._safe(self.root / 'tmp' / uuid.uuid4().hex)
        total, digest = 0, hashlib.sha256()
        try:
            source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(source_fd, 'rb') as incoming:
                if not stat.S_ISREG(os.fstat(incoming.fileno()).st_mode):
                    raise MediaError('Import source must be a regular file')
                with open(temporary, 'xb') as outgoing:
                    os.chmod(temporary, 0o600)
                    while chunk := incoming.read(self.CHUNK_SIZE):
                        total += len(chunk)
                        if total > self.MAX_ASSET_BYTES:
                            raise MediaError('Asset exceeds size limit')
                        digest.update(chunk)
                        outgoing.write(chunk)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
            sha = digest.hexdigest()
            destination = self._safe(self.root / 'assets' / sha)
            # A hard link publishes an already flushed file without overwriting.
            try:
                os.link(temporary, destination)
            except FileExistsError:
                self._safe(destination)
                if not destination.is_file() or destination.stat().st_size != total or self._hash_file(destination) != sha:
                    raise MediaError('Stored asset content is inconsistent')
            directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            identifier = uuid.uuid4().hex
            with self._db() as db:
                db.execute('INSERT INTO assets VALUES (?,?,?,?,?,?,?,?,?)', (
                    identifier, project_id, source.name, str(destination.relative_to(self.workspace)),
                    mimetypes.guess_type(source.name)[0] or 'application/octet-stream', total,
                    sha, encoded, _now()))
                return self._get(db, 'assets', identifier)
        except OSError as exc:
            raise MediaError(f'Cannot import asset: {exc}') from exc
        finally:
            if temporary.exists():
                temporary.unlink()

    def _hash_file(self, path):
        self._safe(path)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            digest = hashlib.sha256()
            while chunk := stream.read(self.CHUNK_SIZE):
                digest.update(chunk)
            return digest.hexdigest()

    def list_assets(self, project_id):
        with self._db() as db:
            self._get(db, 'projects', project_id)
            return [self._record(x) for x in db.execute('SELECT * FROM assets WHERE project_id=? ORDER BY created,id', (project_id,))]

    def output_history(self, limit=30):
        """Bounded index of immutable registered assets, without opening payloads.

        Provenance is retained as recorded; missing revision/job/review evidence
        is unknown. This does not infer that arbitrary workspace files are saved.
        """
        if type(limit) is not int or not 1 <= limit <= 100:
            raise MediaError('History limit must be an integer from 1 to 100')
        with self._db() as db:
            rows = db.execute('SELECT * FROM assets ORDER BY created DESC,id DESC LIMIT ?', (limit,))
            return [{**self._record(row), 'verification': 'unreported',
                     'source': 'registered media asset'} for row in rows]

    def get_asset(self, id):
        with self._db() as db:
            return self._get(db, 'assets', id)

    def asset_path(self, id):
        asset = self.get_asset(id)
        path = self._safe(self.workspace / asset['path'])
        if not path.is_relative_to(self.root / 'assets') or not path.is_file():
            raise MediaError('Asset file missing or outside media storage')
        return path

    def create_job(self, project_id, kind, request, *, idempotency_key=None):
        if not isinstance(kind, str) or not kind.strip() or not isinstance(request, dict):
            raise MediaError('Job requires a kind and request object')
        if idempotency_key is not None and (not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 500):
            raise MediaError('Invalid idempotency key')
        encoded = _json(request)
        with self._db() as db:
            self._get(db, 'projects', project_id)
            self._refs(db, project_id, request)
            if idempotency_key is not None:
                row = db.execute('SELECT * FROM jobs WHERE project_id=? AND idempotency_key=?', (project_id, idempotency_key)).fetchone()
                if row:
                    if row['kind'] != kind or row['request'] != encoded:
                        raise MediaError('Conflicting idempotency request')
                    return self._record(row)
            identifier, now = uuid.uuid4().hex, _now()
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (identifier, project_id, kind, 'queued', encoded, None, 0, None, None, now, now, idempotency_key))
            return self._get(db, 'jobs', identifier)

    def get_job(self, id):
        with self._db() as db:
            return self._get(db, 'jobs', id)

    def list_jobs(self, project_id=None):
        with self._db() as db:
            if project_id is not None:
                self._get(db, 'projects', project_id)
                rows = db.execute('SELECT * FROM jobs WHERE project_id=? ORDER BY created,id', (project_id,))
            else:
                rows = db.execute('SELECT * FROM jobs ORDER BY created,id')
            return [self._record(x) for x in rows]

    def update_job(self, id, status, **fields):
        if set(fields) - {'backend_id', 'progress', 'result', 'error'}:
            raise MediaError('Unsupported job fields')
        if status not in _TRANSITIONS:
            raise MediaError('Unknown job status')
        with self._db() as db:
            job = self._get(db, 'jobs', id)
            if status != job['status'] and status not in _TRANSITIONS[job['status']]:
                raise MediaError(f"Illegal job transition: {job['status']} to {status}")
            if not _TRANSITIONS[job['status']]:
                if fields:
                    raise MediaError('Terminal job is immutable')
                return job
            values = {**job, **fields}
            progress = values['progress']
            if isinstance(progress, bool) or not isinstance(progress, (int, float)) or not math.isfinite(progress) or not 0 <= progress <= 1:
                raise MediaError('Progress must be a finite number between 0 and 1')
            for key in ('backend_id', 'error'):
                if values[key] is not None and not isinstance(values[key], str):
                    raise MediaError(f'{key} must be text or null')
            self._refs(db, job['project_id'], values['result'])
            if status == 'succeeded':
                result = values['result']
                if not isinstance(result, dict) or not result.get('asset_ids'):
                    raise MediaError('Success requires result asset_ids')
                for asset_id in result['asset_ids']:
                    asset = self._get(db, 'assets', asset_id)
                    path = self._safe(self.workspace / asset['path'])
                    if not path.is_relative_to(self.root / 'assets') or not path.is_file() or path.stat().st_size != asset['size'] or self._hash_file(path) != asset['sha256']:
                        raise MediaError('Result asset file missing or invalid')
            db.execute('UPDATE jobs SET status=?,backend_id=?,progress=?,result=?,error=?,updated=? WHERE id=?', (status, values['backend_id'], progress, _json(values['result']), values['error'], _now(), id))
            return self._get(db, 'jobs', id)

    @contextmanager
    def worker_lock(self):
        """Acquire a nonblocking, non-reentrant exclusive workspace worker lock."""
        path = self._safe(self.root / 'worker.lock')
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise MediaError('A media worker already owns this workspace') from exc
            yield
        finally:
            os.close(fd)

    def recover_jobs(self):
        """Stop queued/local work and flag uncertain submissions under worker lock.

        Explicit recovery also interrupts queued dispatches whose child may have
        died before claiming work. A living queued child will see the terminal
        state before any side effects. Generation already claimed by a worker
        remains unknown and must be reconciled, never automatically replayed.
        """
        with self.worker_lock(), self._db() as db:
            rows = db.execute(
                "SELECT id FROM jobs WHERE status IN "
                "('queued','running','cancel_requested') ORDER BY created,id"
            ).fetchall()
            recovered = []
            for row in rows:
                job = self._get(db, 'jobs', row['id'])
                if job['status'] == 'queued' and not job['backend_id']:
                    status = 'interrupted'
                    error = 'Explicit recovery stopped queued work before worker claim'
                else:
                    # Persisted remote IDs must be reconciled, never replayed.
                    status = 'unknown' if job['backend_id'] or job['kind'] == 'generate' else 'interrupted'
                    error = 'Worker stopped; explicit recovery required'
                db.execute(
                    'UPDATE jobs SET status=?,error=?,updated=? WHERE id=?',
                    (status, error, _now(), job['id']),
                )
                recovered.append(self._get(db, 'jobs', job['id']))
            return recovered
