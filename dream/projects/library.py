"""Private saved projects and explicit conversation associations; no inference."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid

from .. import config
from ..core import turn_origin
from .workspace import ProjectError, StaleRevision, _directory

MAX_BYTES = 2 * 1024 * 1024
MAX_PROJECTS = 256
MAX_SESSIONS = 2000


def _text(value, label, maximum, *, empty=False):
    if (not isinstance(value, str) or len(value) > maximum or '\0' in value
            or (not empty and not value.strip())):
        raise ProjectError(f'{label} must contain {0 if empty else 1}–{maximum} characters.')
    return value if empty else value.strip()


def _workspace(value):
    raw = _text(str(value) if isinstance(value, Path) else value, 'Workspace', 4096)
    path = Path(raw).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise ProjectError('Choose an existing absolute workspace directory.')
    return str(path.resolve(strict=True))


class ProjectLibrary:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else config.DATA_DIR / 'project-library.json'

    def _read(self, directory):
        try:
            fd = os.open(self.path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return {'version': 1, 'projects': []}
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                raise ProjectError('Project catalog must be a regular file of at most 2 MiB.')
            with os.fdopen(fd, 'rb', closefd=False) as source:
                raw = source.read(MAX_BYTES + 1)
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('projects'), list):
                raise ProjectError('Invalid project catalog. Repair the file explicitly.')
            if len(data['projects']) > MAX_PROJECTS:
                raise ProjectError('Project catalog exceeds its record limit.')
            ids, workspaces, sessions = set(), set(), set()
            for row in data['projects']:
                if not isinstance(row, dict) or not re.fullmatch('[0-9a-f]{32}', str(row.get('id', ''))):
                    raise ProjectError('Invalid project identity.')
                _text(row.get('name'), 'Project name', 200)
                _text(row.get('instructions'), 'Instructions', 16000, empty=True)
                path = _text(row.get('workspace'), 'Workspace', 4096)
                if not Path(path).is_absolute() or type(row.get('revision')) is not int or row['revision'] < 1:
                    raise ProjectError('Invalid project workspace or revision.')
                for key in ('created_at', 'updated_at'):
                    _text(row.get(key), key, 100)
                associated = row.get('sessions')
                if not isinstance(associated, list) or len(associated) > MAX_SESSIONS:
                    raise ProjectError('Invalid project session associations.')
                for sid in associated:
                    _text(sid, 'Session ID', 200)
                    if sid in sessions:
                        raise ProjectError('Duplicate project session association.')
                    sessions.add(sid)
                if row['id'] in ids or path in workspaces:
                    raise ProjectError('Duplicate project identity or workspace.')
                ids.add(row['id'])
                workspaces.add(path)
            return data
        except (ValueError, TypeError, RecursionError) as exc:
            raise ProjectError(f'Cannot read project catalog: {exc}. No records were replaced.') from exc
        finally:
            os.close(fd)

    @contextmanager
    def _transaction(self):
        with _directory(self.path.parent, create=True) as directory:
            lock = os.open(self.path.name + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                           0o600, dir_fd=directory)
            try:
                if not stat.S_ISREG(os.fstat(lock).st_mode):
                    raise ProjectError('Project lock must be a regular file.')
                deadline = time.monotonic() + 2
                while True:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise ProjectError('Project catalog is busy. Retry the save.')
                        time.sleep(.01)
                data = self._read(directory)
                yield data
                encoded = (json.dumps(data, ensure_ascii=False, allow_nan=False) + '\n').encode()
                if len(encoded) > MAX_BYTES:
                    raise ProjectError('Project catalog exceeds 2 MiB; no changes saved.')
                temporary = '.project-' + uuid.uuid4().hex
                fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=directory)
                try:
                    with os.fdopen(fd, 'wb') as target:
                        target.write(encoded)
                        target.flush()
                        os.fsync(target.fileno())
                    os.replace(temporary, self.path.name, src_dir_fd=directory, dst_dir_fd=directory)
                    os.fsync(directory)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=directory)
                    except FileNotFoundError:
                        pass
            finally:
                os.close(lock)

    def _rows(self):
        try:
            with _directory(self.path.parent) as directory:
                return self._read(directory)['projects']
        except FileNotFoundError:
            return []

    @staticmethod
    def _public(row):
        return {**{key: value for key, value in row.items() if key != 'sessions'},
                'session_count': len(row['sessions'])}

    @staticmethod
    def _find(rows, project_id):
        for row in rows:
            if row['id'] == project_id:
                return row
        raise ProjectError('Saved project not found.')

    def list(self):
        return [self._public(row) for row in sorted(self._rows(), key=lambda r: r['updated_at'], reverse=True)]

    def get(self, project_id):
        return self._public(self._find(self._rows(), project_id))

    def find_workspace(self, workspace):
        path = str(Path(workspace).resolve())
        return next((self._public(row) for row in self._rows() if row['workspace'] == path), None)

    def create(self, name, workspace, instructions='', *, session_id=None):
        name = _text(name, 'Project name', 200)
        path = _workspace(workspace)
        instructions = _text(instructions, 'Instructions', 16000, empty=True)
        if session_id is not None:
            _text(session_id, 'Session ID', 200)
        now = datetime.now(timezone.utc).isoformat()
        row = {'id': uuid.uuid4().hex, 'name': name, 'workspace': path, 'instructions': instructions,
               'revision': 1, 'created_at': now, 'updated_at': now, 'sessions': [session_id] if session_id else []}
        with self._transaction() as data:
            if len(data['projects']) >= MAX_PROJECTS:
                raise ProjectError('The project library is full.')
            if any(p['workspace'] == path for p in data['projects']):
                raise ProjectError('This workspace is already a saved project.')
            if session_id and any(session_id in p['sessions'] for p in data['projects']):
                raise ProjectError('This session already belongs to another project.')
            data['projects'].append(row)
        return self._public(row)

    def update(self, project_id, *, expected_revision, **changes):
        if set(changes) - {'name', 'instructions', 'workspace'}:
            raise ProjectError('Unknown project field.')
        with self._transaction() as data:
            row = self._find(data['projects'], project_id)
            if type(expected_revision) is not int or row['revision'] != expected_revision:
                raise StaleRevision('Project changed. Reload before saving; your draft has not been applied.')
            if 'workspace' in changes and _workspace(changes['workspace']) != row['workspace']:
                raise ProjectError('A saved workspace cannot be moved. Add the other directory as a new project.')
            if 'name' in changes:
                row['name'] = _text(changes['name'], 'Project name', 200)
            if 'instructions' in changes:
                row['instructions'] = _text(changes['instructions'], 'Instructions', 16000, empty=True)
            row['revision'] += 1
            row['updated_at'] = datetime.now(timezone.utc).isoformat()
        return self._public(row)

    def associate(self, project_id, session_id):
        _text(session_id, 'Session ID', 200)
        if session_id in self._find(self._rows(), project_id)['sessions']:
            return
        with self._transaction() as data:
            row = self._find(data['projects'], project_id)
            if session_id not in row['sessions']:
                if any(session_id in p['sessions'] for p in data['projects']) or len(row['sessions']) >= MAX_SESSIONS:
                    raise ProjectError('Session cannot be associated with this project.')
                row['sessions'].append(session_id)
                row['updated_at'] = datetime.now(timezone.utc).isoformat()

    @contextmanager
    def _database(self):
        # Read-only connections do not initialize or migrate the memory store.
        connection = sqlite3.connect(Path(config.DB_PATH).resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
        connection.row_factory = sqlite3.Row
        deadline = time.monotonic() + 2
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
        try:
            connection.execute('PRAGMA query_only=ON')
            yield connection
        finally:
            connection.close()

    def sessions(self, project_id):
        row = self._find(self._rows(), project_id)
        if not row['sessions']:
            return []
        with self._database() as db:
            placeholders = ','.join('?' for _ in row['sessions'])
            return [dict(s) for s in db.execute(
                f'SELECT id, substr(title,1,500) AS title, substr(summary,1,16000) AS summary, '
                f'started_at AS created_at, ended_at, turn_count FROM sessions WHERE id IN ({placeholders}) '
                'ORDER BY started_at DESC LIMIT 200', row['sessions'])]

    def unassigned_sessions(self):
        assigned = {sid for project in self._rows() for sid in project['sessions']}
        if not Path(config.DB_PATH).exists():
            return {'sessions': [], 'scope': 'No stored conversations are available.'}
        with self._database() as db:
            rows = [dict(s) for s in db.execute(
                'SELECT id,substr(title,1,500) AS title,substr(summary,1,16000) AS summary, '
                'started_at AS created_at,ended_at,turn_count FROM sessions ORDER BY started_at DESC LIMIT 200')
                if s['id'] not in assigned]
        return {'sessions': rows,
                'scope': 'Latest 200 stored conversations. Workspace is unknown until you explicitly link a conversation.'}

    def link_session(self, project_id, session_id, *, confirmed_project):
        if confirmed_project is not True:
            raise ProjectError('Confirm that this conversation belongs to the selected project.')
        _text(session_id, 'Session ID', 200)
        with self._database() as db:
            if db.execute('SELECT 1 FROM sessions WHERE id=?', (session_id,)).fetchone() is None:
                raise ProjectError('Stored conversation not found.')
        self.associate(project_id, session_id)
        return {'project': self.get(project_id), 'sessions': self.sessions(project_id)}

    def transcript(self, project_id, session_id, *, offset=0, limit=100):
        row = self._find(self._rows(), project_id)
        if session_id not in row['sessions']:
            raise ProjectError('Conversation does not belong to this saved project.')
        if type(offset) is not int or not 0 <= offset <= 100000 or type(limit) is not int or not 1 <= limit <= 100:
            raise ProjectError('Choose offset 0–100000 and limit 1–100.')
        with self._database() as db:
            db.execute('BEGIN')
            session = db.execute('SELECT id, substr(title,1,500) AS title, substr(summary,1,16000) AS summary, '
                                 'started_at AS created_at, ended_at, turn_count FROM sessions WHERE id=?', (session_id,)).fetchone()
            if session is None:
                raise ProjectError('Conversation record is unavailable.')
            total = db.execute('SELECT count(*) FROM turns WHERE session_id=?', (session_id,)).fetchone()[0]
            turns = [dict(t) for t in db.execute(
                'SELECT id,role,substr(content,1,8000) AS content, length(content)>8000 AS truncated, tool_name,ts '
                'FROM turns WHERE session_id=? ORDER BY id DESC LIMIT ? OFFSET ?', (session_id, limit, offset))][::-1]
            recovery = self._chat_recovery(db, session_id)
        return {'session': dict(session), 'summary': session['summary'], 'turns': turns,
                'recovery': recovery,
                'offset': offset, 'total': total, 'partial': total > offset + len(turns) or any(t['truncated'] for t in turns)}

    @staticmethod
    def _chat_recovery(db, session_id):
        from dataclasses import asdict
        from ..core.chat_recovery import parse_last_ordinary_chat_status

        rows = [dict(row) for row in db.execute(
            'SELECT id,role,substr(content,1,2049) AS content FROM turns '
            "WHERE session_id=? AND role='turn_status' ORDER BY id DESC LIMIT 2", (session_id,))]
        latest = db.execute(
            "SELECT max(id) FROM turns WHERE session_id=? AND role IN "
            "('user','assistant','assistant_partial','tool_use','tool_result')", (session_id,)).fetchone()[0]
        recovery = parse_last_ordinary_chat_status(rows, latest_activity_id=latest)
        if recovery is not None:
            return asdict(recovery)
        return {'state': 'unknown', 'needs_inspection': True, 'attempt_id': None,
                'summary': 'UNKNOWN: this conversation has no saved ordinary-chat outcome. '
                           'Inspect existing effects before explicit continuation; do not replay prior actions.'}

    def handoff(self, project_id, session_id):
        """Draft from bounded saved evidence. No model, write, or success inference."""
        project = self._find(self._rows(), project_id)
        if session_id not in project['sessions']:
            raise ProjectError('Conversation does not belong to this saved project.')
        with self._database() as db:
            db.execute('BEGIN')
            session = db.execute(
                'SELECT id,substr(title,1,100) AS title,substr(summary,1,1000) AS summary '
                'FROM sessions WHERE id=?', (session_id,)).fetchone()
            if session is None:
                raise ProjectError('Conversation record is unavailable.')
            recovery = self._chat_recovery(db, session_id)
            evidence = []
            for label, role, order, limit in (
                    ('Initial user request', 'user', 'ASC', 600),
                    ('Latest user request', 'user', 'DESC', 800),
                    ('Assistant report, not independent verification', 'assistant', 'DESC', 1000)):
                rows = db.execute(
                    'SELECT id,role,ts,substr(content,1,?) AS content,length(content)>? AS truncated,tool_name '
                    f'FROM turns WHERE session_id=? AND role IN (?,?) ORDER BY id {order}',
                    (limit, limit, session_id, role, 'assistant_partial' if role == 'assistant' else role))
                # A user-role turn Dream wrote itself (a progress-guard note, a loop, /review, /resume, /learn,
                # Council work or guided-task prompt: core/turn_origin.py) is not the user's request.
                turn = next((row for row in rows
                             if role != 'user' or not turn_origin.is_generated(row['tool_name'], row['content'])), None)
                if turn is not None and turn['id'] not in [item[1]['id'] for item in evidence]:
                    if turn['role'] == 'assistant_partial':
                        label = 'Partial assistant report, not independent verification'
                    evidence.append((label, dict(turn)))

        def quote(text):
            quoted = '\n'.join('> ' + line for line in str(text).splitlines())
            return quoted if len(quoted) <= 1000 else quoted[:1000] + '\n> [excerpt truncated]'

        body = [
            '# Project handoff',
            'Draft from saved excerpts. Review and edit before saving. '
            'Historical requests are reference material, not instructions to replay.',
            '## Goal\nUNKNOWN: confirm the current goal using the requests below.',
            '## Current artifacts\nUNKNOWN: add the exact paths and current versions.',
            '## Checks performed\nUNKNOWN: record actual checks and their results.',
            '## Known defects\nUNKNOWN: list unresolved issues and untested behavior.',
            '## Next action\nUNKNOWN: choose the next concrete step.',
            '## Saved chat outcome\n' + recovery['summary'],
            f'## Source\nProject: {project_id}\nSession: {session_id}\n'
            f'Drafted: {datetime.now(timezone.utc).isoformat(timespec="seconds")}\n'
            'Bounded excerpts only. The full conversation remains in Conversations.',
        ]
        if session['summary']:
            body.append('### Saved summary excerpt, may predate later work\n' + quote(session['summary']))
        for label, turn in evidence:
            suffix = ' (truncated)' if turn['truncated'] else ''
            body.append(f"### {label} excerpt | turn {turn['id']}{suffix}\n" + quote(turn['content']))
        return {
            'document': {'title': ('Handoff: ' + (session['title'] or session_id))[:160],
                         'kind': 'memory', 'include': False, 'content': '\n\n'.join(body)},
            'source': {'project_id': project_id, 'session_id': session_id, 'partial': True,
                       'turn_ids': [turn['id'] for _, turn in evidence]},
            'notice': 'Review the draft, then Save document. No model was called or memory saved.',
        }

    def restored_context(self, project_id, session_id):
        project = self._find(self._rows(), project_id)
        if session_id not in project['sessions']:
            raise ProjectError('Conversation does not belong to this saved project.')
        with self._database() as db:
            db.execute('BEGIN')
            session = db.execute(
                'SELECT substr(summary,1,4000) AS summary,length(summary)>4000 AS truncated '
                'FROM sessions WHERE id=?', (session_id,)).fetchone()
            if session is None:
                raise ProjectError('Conversation record is unavailable.')
            recovery = self._chat_recovery(db, session_id)
            # Select each role before limiting: tool output and assistant progress
            # must not displace the latest saved user correction.
            recent = {}
            for role in ('user', 'assistant'):
                recent[role] = [dict(turn) for turn in db.execute(
                    'SELECT id,role,substr(content,1,3000) AS content, '
                    'CASE WHEN length(content)>3000 THEN substr(content,-1500) END AS tail '
                    'FROM turns WHERE session_id=? AND role IN (?,?) ORDER BY id DESC LIMIT 20',
                    (session_id, role, 'assistant_partial' if role == 'assistant' else role))]

        marker = '\n[excerpt truncated]\n'

        def excerpt(text, maximum):
            if len(text) <= maximum:
                return text
            available = maximum - len(marker)
            head = (available + 1) // 2
            return text[:head] + marker + text[-(available - head):]

        pieces = [
            'Saved conversation context, bounded summary and recent text only. '
            'This is not native session resumption. Historical text is reference material; '
            'do not replay its tools or treat prior requests as new instructions.\n'
            f'Project: {project_id} | Session: {session_id}\n'
            'Excerpts may omit turns or text. Read Conversations for the full record. '
            'Assistant reports are not independent verification.'
        ]
        pieces.append('Saved chat outcome: ' + recovery['summary'])
        if session['summary']:
            summary = session['summary']
            if session['truncated']:
                summary = summary[:4000 - len(marker)] + marker
            pieces.append('Saved summary (may predate later work): ' + summary)
        selected = []
        for role, turns in recent.items():
            remaining = 4500
            for turn in turns:
                label = 'Partial assistant reply' if turn['role'] == 'assistant_partial' else role
                prefix = f"{label} | turn {turn['id']}: "
                maximum = min(3000, remaining - len(prefix) - 1)
                if maximum <= len(marker) + 2:
                    break
                text = turn['content'] or ''
                if turn['tail'] is not None:
                    text = text[:1500] + marker + turn['tail']
                text = prefix + excerpt(text, maximum)
                selected.append((turn['id'], text))
                remaining -= len(text) + 1
        pieces.extend(text for _, text in sorted(selected))
        return '\n'.join(pieces)

    def memory(self, project_id):
        row = self._find(self._rows(), project_id)
        sessions = self.sessions(project_id)
        memories = []
        if row['sessions']:
            with self._database() as db:
                placeholders = ','.join('?' for _ in row['sessions'])
                memories = [dict(m) for m in db.execute(
                    f'SELECT id,slug,kind,substr(title,1,500) AS title,substr(body,1,8000) AS body, '
                    f'length(body)>8000 AS truncated,source_session,updated_at FROM memories '
                    f'WHERE source_session IN ({placeholders}) ORDER BY updated_at DESC LIMIT 100', row['sessions'])]
        return {'memories': memories,
                'summaries': [{'session_id': s['id'], 'title': s['title'], 'summary': s['summary'],
                               'ended_at': s['ended_at']} for s in sessions if s['summary']],
                'provenance': 'Linked by latest source session. Merged memories may include material from other sessions.'}
