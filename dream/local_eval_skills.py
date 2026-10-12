"""Bounded packaged SKILL.md snapshots for the isolated selection evaluation.

No operator settings, plugin registry, references or scripts are consulted. Bodies
are captured on the host; the model receives metadata until it calls skill_open.
Descriptor-relative no-follow reads protect the selected path at open time. This
is a file-reading boundary, not a sandbox for arbitrary trusted Python imports.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat

import yaml

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_MAX_FILE = 128 * 1024
_MAX_TOTAL = 2 * 1024 * 1024
_MAX_SKILLS = 128
_NAME = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


@dataclass(frozen=True)
class PackagedSkill:
    name: str
    description: str
    body: str
    sha256: str
    source: str


class _HeaderLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ValueError("Packaged skill header keys must be unique strings")
        result[key] = loader.construct_object(value_node)
    return result


_HeaderLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _metadata(data: bytes, source: str) -> PackagedSkill:
    body = data.decode("utf-8", errors="strict")
    lines = body.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("Packaged skill requires YAML frontmatter")
    closing = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if closing is None:
        raise ValueError("Packaged skill header is unterminated")
    header = "".join(lines[1:closing])
    if len(header.encode("utf-8")) > 16 * 1024:
        raise ValueError("Packaged skill header exceeds 16 KiB")
    try:
        fields = yaml.load(header, Loader=_HeaderLoader)
    except (yaml.YAMLError, RecursionError) as exc:
        raise ValueError("Invalid packaged skill header") from exc
    if not isinstance(fields, dict):
        raise ValueError("Packaged skill header must be a mapping")
    name, description = fields.get("name"), fields.get("description")
    if (not isinstance(name, str) or not _NAME.fullmatch(name)
            or not isinstance(description, str) or not description.strip()
            or len(description) > 4096 or any(ord(c) < 32 and c not in "\n\t\r" for c in description)):
        raise ValueError("Packaged skill name or description is invalid or oversized")
    return PackagedSkill(name, description, body, hashlib.sha256(data).hexdigest(), source)


def _root_fd(root: Path) -> int:
    # Do not resolve(): that would erase symlink evidence before O_NOFOLLOW.
    path = Path(root).absolute()
    if ".." in path.parts:
        raise ValueError("Packaged root cannot contain parent traversal")
    fd = os.open(path.anchor, _DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            child = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _directories(fd: int):
    entries = os.listdir(fd)
    if len(entries) > 512:
        raise ValueError("Packaged directory entry count exceeds 512")
    for name in sorted(entries):
        if name.startswith("."):
            continue
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("Packaged skill directories cannot be symlinks")
        if stat.S_ISDIR(info.st_mode):
            yield name


def _read_skill(fd: int, source: str) -> PackagedSkill | None:
    try:
        file_fd = os.open("SKILL.md", _FILE_FLAGS, dir_fd=fd)
    except FileNotFoundError:
        return None
    try:
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_FILE:
            raise ValueError("Packaged SKILL.md must be a regular file at most 128 KiB")
        data = bytearray()
        while len(data) <= _MAX_FILE:
            chunk = os.read(file_fd, min(8192, _MAX_FILE + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(file_fd)
        if (len(data) > _MAX_FILE or len(data) != before.st_size
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise ValueError("Packaged SKILL.md changed during bounded read")
        return _metadata(bytes(data), source)
    finally:
        os.close(file_fd)


def load_packaged_skills(root: Path) -> tuple[PackagedSkill, ...]:
    """Snapshot root/*/SKILL.md and root/domains/*/SKILL.md, without recursion."""
    rows, names, total = [], set(), 0
    root_fd = _root_fd(root)
    try:
        for name in _directories(root_fd):
            directory = os.open(name, _DIR_FLAGS, dir_fd=root_fd)
            try:
                if name == "domains":
                    candidates = [(child, f"domains/{child}/SKILL.md") for child in _directories(directory)]
                else:
                    candidates = [(None, f"{name}/SKILL.md")]
                for child, source in candidates:
                    selected = os.open(child, _DIR_FLAGS, dir_fd=directory) if child is not None else directory
                    try:
                        row = _read_skill(selected, source)
                    finally:
                        if child is not None:
                            os.close(selected)
                    if row is None:
                        continue
                    total += len(row.body.encode("utf-8"))
                    if len(rows) >= _MAX_SKILLS or total > _MAX_TOTAL:
                        raise ValueError("Packaged skill count or aggregate byte limit exceeded")
                    if row.name.casefold() in names:
                        raise ValueError("Duplicate packaged skill name")
                    names.add(row.name.casefold())
                    rows.append(row)
            finally:
                os.close(directory)
    finally:
        os.close(root_fd)
    return tuple(rows)


def find_skills(rows: tuple[PackagedSkill, ...], query: str) -> tuple[PackagedSkill, ...]:
    if not isinstance(query, str) or len(query) > 4096:
        raise ValueError("Skill query must be text at most 4096 characters")
    terms = query.casefold().split()
    return tuple(row for row in rows if all(term in (row.name + " " + row.description).casefold() for term in terms))


def open_skill(rows: tuple[PackagedSkill, ...], name: str) -> str:
    if not isinstance(name, str):
        raise ValueError("Skill name must be text")
    for row in rows:
        if row.name.casefold() == name.strip().casefold():
            return row.body
    raise ValueError("Unknown packaged skill name")
