"""The Library: durable, versioned, id-addressed files.

Dream's workspace is disposable; the Library is not. See ``store`` for why identity
lives on the id rather than the path, and ``dream/tools/library_tools.py`` for the
tools the model actually reaches for.
"""

from .store import (
    FindMatch,
    Library,
    LibraryError,
    LibraryFile,
    SearchHit,
    VersionConflict,
)

__all__ = [
    "FindMatch",
    "Library",
    "LibraryError",
    "LibraryFile",
    "SearchHit",
    "VersionConflict",
]
