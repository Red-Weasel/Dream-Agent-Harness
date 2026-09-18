"""Read durable run ledgers without opening RunState or acquiring its write lock."""
import os
from pathlib import Path
import re
import time

from ..core.run_state import inspect_run
from .workspace import _directory


def list_recoveries(workspace, root=None):
    if root is None:
        from ..config import LOOP_DIR
        root = LOOP_DIR
    wanted = str(Path(os.path.abspath(workspace)))
    results = []
    started = time.monotonic()
    try:
        with _directory(root) as directory, os.scandir(directory) as runs:
            for number, entry in enumerate(runs):
                if number >= 200 or time.monotonic() - started > 1:
                    break
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', entry.name) or not entry.is_dir(follow_symlinks=False):
                    continue
                try:
                    view = inspect_run(root, entry.name)
                    state = view['state']
                    if state.get('worker_workspace') != wanted:
                        continue
                    result = state.get('result') or {}
                    results.append({'run_id': entry.name, 'status': view['status'],
                        'recorded_status': view['recorded_status'], 'ownership': view['ownership'],
                        'continuation': view['continuation'],
                        'goal': str(state.get('goal', ''))[:500], 'updated_at': view['updated_at'],
                        'requires_reconciliation': view['requires_reconciliation'],
                        'summary': str(result.get('message', state.get('next', '')))[:1000] if isinstance(result, dict) else '',
                        'requirements': 'Verify the saved goal, workspace, contract and remaining budget. Reconcile uncertain actions explicitly. No tools have been replayed.'})
                except (OSError, ValueError, TypeError, KeyError):
                    # A corrupt/unscoped ledger is not authority to reveal a run in this workspace.
                    continue
    except OSError:
        return []
    results.sort(key=lambda item: item['updated_at'], reverse=True)
    return results[:30]
