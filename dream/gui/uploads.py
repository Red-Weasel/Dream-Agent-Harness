"""Bounded, non-executing file intake for the active workspace."""
from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

from starlette.requests import Request

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_ATTACHMENTS = 8


class UploadError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


async def receive_file(request: Request) -> tuple[str, bytes]:
    """Bound the entire transport, including multipart overhead, before parsing."""
    multipart = request.headers.get('content-type', '').startswith('multipart/form-data')
    limit = MAX_FILE_BYTES + (64 * 1024 if multipart else 0)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise UploadError('File exceeds the 25 MB upload limit.', 413)
        body.extend(chunk)
    if not multipart:
        name = request.query_params.get('name', '')
        if not name:
            raise UploadError('Choose a file to attach.')
        return name, bytes(body)

    async def receive():
        return {'type': 'http.request', 'body': bytes(body), 'more_body': False}

    bounded = Request(request.scope, receive)
    try:
        async with bounded.form(max_files=1, max_fields=4) as form:
            up = form.get('file')
            if up is None or not getattr(up, 'filename', ''):
                raise UploadError('Choose a file to attach.')
            data = await up.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise UploadError('File exceeds the 25 MB upload limit.', 413)
            return up.filename, data
    except UploadError:
        raise
    except Exception as exc:
        raise UploadError('Could not read the uploaded file. Choose it again.') from exc


def save_file(workspace: Path, raw_name: str, data: bytes) -> dict:
    """Use directory descriptors so a workspace symlink cannot redirect writes."""
    name = re.sub(r'[^\w.-]+', '_', raw_name.replace('\\', '/').rsplit('/', 1)[-1]).strip('._') or 'file'
    suffix = Path(name).suffix[:16]
    name = (Path(name).stem[:100] + suffix) if len(name) > 120 else name
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    root_fd = os.open(workspace, flags)
    try:
        try:
            os.mkdir('uploads', mode=0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
        directory_fd = os.open('uploads', flags, dir_fd=root_fd)
        try:
            identifier = secrets.token_urlsafe(18)
            stored = name
            for attempt in range(10):
                try:
                    fd = os.open(stored, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 0o600, dir_fd=directory_fd)
                    break
                except FileExistsError:
                    stored = f'{Path(name).stem}-{secrets.token_hex(4)}{suffix}'
            else:
                raise UploadError('Could not choose an unused filename. Try again.')
            try:
                with os.fdopen(fd, 'wb') as output:
                    output.write(data)
            except BaseException:
                os.unlink(stored, dir_fd=directory_fd)
                raise
            return {'id': identifier, 'name': name, 'path': f'uploads/{stored}', 'size': len(data)}
        finally:
            os.close(directory_fd)
    finally:
        os.close(root_fd)


def validate_attachments(workspace: Path, identifiers, registered: dict) -> list[dict]:
    if not isinstance(identifiers, list) or len(identifiers) > MAX_ATTACHMENTS:
        raise UploadError(f'Attach at most {MAX_ATTACHMENTS} files to a message.')
    files = []
    for identifier in dict.fromkeys(i for i in identifiers if isinstance(i, str)):
        record = registered.get(identifier)
        if record is None or record.get('workspace') != str(workspace):
            raise UploadError('An attachment is no longer available. Remove it and attach the file again.')
        path = workspace / record['path']
        if path.is_symlink() or path.parent.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(workspace):
            raise UploadError(f"Attachment {record['name']} moved or is unavailable. Attach it again.")
        files.append(record)
    if any(not isinstance(i, str) for i in identifiers):
        raise UploadError('Invalid attachment identifier.')
    return files
