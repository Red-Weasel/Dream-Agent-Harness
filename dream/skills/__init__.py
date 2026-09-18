"""Installed skills — the ones that arrive as files, not the ones Dream writes.

``memory.skills`` holds procedural memory: what Dream learned by doing. This package
holds the other kind — skill *packages* authored elsewhere and dropped on disk
(Claude Code / Codex layout: a directory with ``SKILL.md``, YAML frontmatter, and
whatever ``references/`` and ``scripts/`` the author bundled).

They are kept apart on purpose. A written skill is a memory row and belongs in the
store; an installed skill is a directory that its author still owns and may update
underneath us. Copying one into the store would fork it and throw away every file
next to the ``SKILL.md``.
"""

from .loader import (
    FileSkill,
    bundled_file,
    discover,
    find,
    index_lines,
    load_body,
)

__all__ = [
    "FileSkill",
    "bundled_file",
    "discover",
    "find",
    "index_lines",
    "load_body",
]
