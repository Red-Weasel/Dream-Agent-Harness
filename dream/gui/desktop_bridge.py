"""Private discovery for the native parent of a Dream session.

The parent supplies a path inside its private temporary directory. The child
publishes only after Studio is listening; the token stays in this 0600 file.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class DesktopDiscovery:
    def __init__(self) -> None:
        raw = os.environ.get("DREAM_DESKTOP_SESSION_FILE")
        self.path = Path(raw) if raw else None
        self._published: dict | None = None

    def publish(self, url: str) -> None:
        if self.path is None:
            return
        payload = {"url": url, "pid": os.getpid()}
        # A sibling rename makes polling see a complete old or new document.
        # mkstemp creates mode 0600 even when the process has a permissive umask.
        fd, raw = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        temporary = Path(raw)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self._published = payload
        finally:
            temporary.unlink(missing_ok=True)

    def clear(self) -> None:
        """Do not remove discovery belonging to a newer server or another child."""
        if self.path is None or self._published is None:
            return
        try:
            if json.loads(self.path.read_text(encoding="utf-8")) == self._published:
                self.path.unlink()
        except (OSError, ValueError):
            pass  # the parent may already have removed its temporary directory
        self._published = None
