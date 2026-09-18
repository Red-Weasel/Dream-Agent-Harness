"""Workspace-local transactions with bounded task and artifact retention."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import stat
from pathlib import Path
import sqlite3


class WorkflowStore:
    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise ValueError('Choose an existing workspace')
        self.path = self.workspace / '.dream' / 'workflows' / 'tasks.sqlite3'
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, name TEXT NOT NULL, data BLOB NOT NULL)')

    @contextmanager
    def transaction(self):
        # Reopen every component through directory descriptors on every access.
        # SQLite opens paths itself, so hold the expected inode and compare both
        # its reported main path and the anchored name before any write begins.
        descriptors = []
        db = None
        try:
            parent = os.open(self.workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(parent)
            for part in ('.dream', 'workflows'):
                try:
                    os.mkdir(part, mode=0o700, dir_fd=parent)
                except FileExistsError:
                    pass
                parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                descriptors.append(parent)
                os.fchmod(parent, 0o700)
            filename = 'tasks.sqlite3'
            file_fd = os.open(filename, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                              0o600, dir_fd=parent)
            descriptors.append(file_fd)
            expected = os.fstat(file_fd)
            if not stat.S_ISREG(expected.st_mode) or expected.st_nlink != 1:
                raise ValueError('Workflow database must be a regular file without hard links')
            os.fchmod(file_fd, 0o600)
            for suffix in ('-journal', '-wal', '-shm'):
                try:
                    side = os.stat(filename + suffix, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if not stat.S_ISREG(side.st_mode) or side.st_nlink != 1:
                    raise ValueError('Workflow database side files must not use links')
            anchored = f'/proc/self/fd/{parent}/{filename}'
            db = sqlite3.connect('file:' + anchored + '?mode=rw', uri=True, timeout=10)
            actual = os.stat(filename, dir_fd=parent, follow_symlinks=False)
            main = next(row[2] for row in db.execute('PRAGMA database_list') if row[1] == 'main')
            opened = os.stat(main, follow_symlinks=False)
            identity = (expected.st_dev, expected.st_ino)
            if (not stat.S_ISREG(actual.st_mode) or not stat.S_ISREG(opened.st_mode)
                    or (actual.st_dev, actual.st_ino) != identity or (opened.st_dev, opened.st_ino) != identity):
                raise ValueError('Workflow database changed while opening; no transaction was started')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except OSError as exc:
            if db is not None:
                db.rollback()
            raise ValueError('Cannot safely open private workflow state; symlinks are not allowed') from exc
        except BaseException:
            if db is not None:
                db.rollback()
            raise
        finally:
            if db is not None:
                db.close()
            for fd in reversed(descriptors):
                os.close(fd)

    def get(self, task_id, db=None):
        if db is None:
            with self.transaction() as connection:
                return self.get(task_id, connection)
        row = db.execute('SELECT body FROM tasks WHERE id=?', (task_id,)).fetchone()
        if row is None:
            raise ValueError('Task was not found in this workspace')
        return json.loads(row[0])

    def save(self, task, db):
        db.execute('INSERT OR REPLACE INTO tasks VALUES (?,?)', (task['id'], json.dumps(task)))

    def list(self):
        with self.transaction() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT body FROM tasks ORDER BY rowid DESC LIMIT 100')]
