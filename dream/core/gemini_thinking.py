"""Private selected-model thinking settings for the qualified Gemini CLI.

No account probe or persistent configuration write. Existing model configuration
is refused rather than overwritten; Gemini can still select a fallback model.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import sys
import tempfile


REDUCED_LEVELS = {
    'gemini-3.5-flash-lite': 'MINIMAL',
    'gemini-3.8-flash': 'LOW',
    'gemini-3.1-pro-preview': 'LOW',
}
_MAX_SETTINGS_BYTES = 1_000_000


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _read_object(path: Path, *, missing=False) -> dict:
    """Bound reads, refuse special files/links, and never expose settings text."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError:
        if missing and not path.is_symlink():
            return {}
        raise ValueError('Gemini thinking configuration file is unavailable') from None
    except OSError:
        raise ValueError('Gemini thinking requires readable regular settings files') from None
    try:
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError('Gemini thinking requires regular settings files')
            source = os.fdopen(descriptor, 'rb')
            descriptor = None
            with source:
                raw = source.read(_MAX_SETTINGS_BYTES + 1)
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if len(raw) > _MAX_SETTINGS_BYTES:
            raise ValueError('Settings file exceeds the supported size')
        def reject_constant(value):
            raise ValueError('Nonfinite JSON value')
        value = json.loads(raw, object_pairs_hook=_object, parse_constant=reject_constant)
        if not isinstance(value, dict):
            raise ValueError('Settings root must be an object')
        return value
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise ValueError('Gemini thinking requires bounded plain JSON object settings; use Provider default for other formats') from None


def _path(value: str | Path, cwd: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else cwd / path


@contextmanager
def thinking_environment(model: str | None, level: str | None, *, cwd: str,
                         executable: str | None, env: dict | None = None):
    """Keep the overlay alive until the caller has reaped every owned child."""
    if level is None:
        yield env
        return
    if not isinstance(level, str) or level not in ('on', 'off') or not isinstance(model, str) or model not in REDUCED_LEVELS:
        raise ValueError('Gemini extended thinking requires a supported exact model and on/off control')
    if sys.platform != 'linux' or not executable:
        raise ValueError('Gemini thinking is only qualified for the installed Linux CLI 0.52.0')
    resolved = Path(executable).resolve()
    package = _read_object(resolved.parent.parent / 'package.json')
    if (resolved.name != 'gemini.js' or resolved.parent.name != 'bundle'
            or package.get('name') != '@google/gemini-cli' or package.get('version') != '0.52.0'):
        raise ValueError('Gemini thinking is only qualified for CLI 0.52.0; use Provider default')
    child = dict(os.environ if env is None else env)
    root = Path(cwd).absolute()
    system = _path(child.get('GEMINI_CLI_SYSTEM_SETTINGS_PATH') or '/etc/gemini-cli/settings.json', root)
    defaults = _path(child.get('GEMINI_CLI_SYSTEM_DEFAULTS_PATH') or system.parent / 'system-defaults.json', root)
    home = _path(child.get('GEMINI_CLI_HOME') or child.get('HOME') or Path.home(), root)
    user = home / '.gemini' / 'settings.json'
    workspace = root / '.gemini' / 'settings.json'
    settings = {}
    for path in dict.fromkeys((system, defaults, user, workspace)):
        loaded = _read_object(path, missing=True)
        # Do not promote potentially untrusted workspace settings into a system
        # layer or attempt to reproduce the CLI's native trust/alias resolver.
        if 'modelConfigs' in loaded and loaded['modelConfigs'] != {}:
            raise ValueError('Gemini thinking conflicts with existing modelConfigs; use Provider default')
        settings[path] = loaded
    overlay = settings[system]
    overlay['modelConfigs'] = {'customOverrides': [{
        'match': {'model': model, 'overrideScope': 'core'},
        'modelConfig': {'generateContentConfig': {'thinkingConfig': {
            'thinkingLevel': 'HIGH' if level == 'on' else REDUCED_LEVELS[model],
        }}},
    }]}
    with tempfile.TemporaryDirectory(prefix='dream-gemini-thinking-') as folder:
        destination = Path(folder) / 'settings.json'
        with open(destination, 'x', encoding='utf-8', opener=lambda path, flags: os.open(path, flags, 0o600)) as output:
            json.dump(overlay, output, ensure_ascii=False, allow_nan=False)
        child['GEMINI_CLI_SYSTEM_SETTINGS_PATH'] = str(destination)
        child['GEMINI_CLI_SYSTEM_DEFAULTS_PATH'] = str(defaults)
        yield child
