"""Process-local source snapshot for diagnosing changes that need a restart.

This describes selected files on disk when Dream imports, not provider versions
or a guarantee about every dynamically imported module.
"""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import stat

_ROOT = Path(__file__).parent
_FILES = ('__init__.py', 'runtime_identity.py', 'core/engine.py', 'core/steering.py', 'core/backends/openai_compat.py',
          'core/backends/anthropic.py', 'core/backends/cli_agent.py',
          'tui/app.py', 'gui/server.py', 'computer.py', 'media/providers.py',
          'media/service.py', 'tools/media_tools.py')
_STARTED_AT = datetime.now(timezone.utc).isoformat()


def _snapshot(root):
    result = {}
    for name in _FILES:
        try:
            path = root / name
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as source:
                info = os.fstat(source.fileno())
                limit = 2 * 1024 * 1024
                if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                    result[name] = None
                    continue
                content = source.read(limit + 1)
                result[name] = hashlib.sha256(content).hexdigest() if len(content) <= limit else None
        except OSError:
            result[name] = None
    return result


def _fingerprint(snapshot):
    if any(value is None for value in snapshot.values()):
        return None
    value = '\n'.join(name + ':' + digest for name, digest in sorted(snapshot.items()))
    return hashlib.sha256(value.encode()).hexdigest()[:16]


_INITIAL = _snapshot(_ROOT)


def runtime_identity():
    from . import __version__
    current = _snapshot(_ROOT)
    initial_id, current_id = _fingerprint(_INITIAL), _fingerprint(current)
    changed = [name for name in _FILES if _INITIAL[name] != current[name]]
    known = initial_id is not None and current_id is not None
    return {
        'version': __version__, 'pid': os.getpid(), 'imported_at': _STARTED_AT,
        'startup_source_id': initial_id, 'disk_source_id': current_id,
        'source_changed': bool(changed) if known else None,
        'changed_files': changed,
        'scope': 'Selected core source files at Dream package import; provider builds and other files are not covered.',
        'restart_guidance': 'Source identity is unavailable; inspect the installation before inferring its loaded version.' if not known else
                            'Restart Dream when convenient to load changed Python source.' if changed else
                            'No change detected in the selected core files; this does not verify every loaded module.',
    }
