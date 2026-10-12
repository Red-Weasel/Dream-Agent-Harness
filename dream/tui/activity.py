"""Explicit file targets for session display; never used for authorization."""
from pathlib import Path


def file_targets(name: str, arguments: dict, workspace: Path) -> set[str]:
    """Capture file identities before execution, excluding shell/folder heuristics."""
    name = str(name).split('__')[-1]
    root = workspace.resolve()
    raw = []
    if name == 'copy_files':
        files = arguments.get('files')
        for item in files if isinstance(files, list) else []:
            if not isinstance(item, dict):
                continue
            source = item.get('src')
            if isinstance(source, str) and (root / source).is_dir():
                continue  # Directory trees are not a single changed file.
            raw.append(item.get('dest'))
            if item.get('move'):
                raw.append(source)
    elif name == 'delete_file':
        paths = arguments.get('paths')
        raw.extend(paths if isinstance(paths, list) else [])
    elif name in {'Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'write_file', 'str_replace_edit'}:
        raw.append(arguments.get('file_path') or arguments.get('path') or arguments.get('notebook_path'))
    result = set()
    for value in raw:
        if not isinstance(value, str) or not value:
            continue
        try:
            path = Path(value).expanduser()
            # Normalize parent, preserving a removed symlink's own identity.
            path = root / path
            path = path.parent.resolve() / path.name
            if path.is_dir():
                continue
            result.add(str(path.relative_to(root)) if path.is_relative_to(root) else str(path))
        except (OSError, ValueError, RuntimeError):
            continue
    return result
