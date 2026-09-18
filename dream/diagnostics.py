"""Offline package diagnostics. No model, network, hardware or native UI probes.

Run ``python -m dream.diagnostics --json`` or export to a new private JSON file.
Optional wheel inspection and exported-record grading never install or replay code.
"""
from __future__ import annotations

import argparse
import base64
import csv
from email.parser import BytesParser
import hashlib
from html.parser import HTMLParser
from importlib import metadata
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import tomllib
import zipfile
from urllib.parse import unquote, urlsplit

from .environment import NUMERIC_SETTINGS, numeric_environment_errors

_DEPENDENCIES = frozenset(('claude-agent-sdk', 'mcp', 'rich', 'prompt-toolkit', 'httpx',
    'jsonschema', 'referencing', 'trafilatura', 'beautifulsoup4', 'lxml', 'camoufox', 'anyio',
    'fastembed', 'hnswlib', 'numpy', 'starlette', 'python-multipart', 'uvicorn', 'websockets',
    'playwright', 'pillow', 'python-pptx', 'pyyaml', 'packaging'))
_SKILLS = ('coding', 'research', 'writing', 'documents', 'data-analysis', 'media', 'library', 'verifying')
_STATIC = ('index.html', 'controls.js', 'controls.css', 'attachments.js', 'attachments.css',
           'media.js', 'media.css', 'companion.js', 'companion.css', 'turn-timing.js', 'turn-timing.css',
           'workflows.js', 'workflows.css', 'projects.js', 'projects.css', 'feed.js', 'feed.css',
           'council.js', 'council.css')
_REQUIRED = {f'dream/gui/static/{name}' for name in _STATIC} | {
    'dream/__init__.py', 'dream/__main__.py', 'dream/diagnostics.py', 'dream/environment.py', 'dream/agent_activity.py', 'dream/core/capabilities.py',
    'dream/core/inference_coordination.py', 'dream/local/load_lock.py',
    'dream/workflows/service.py', 'dream/workflows/store.py', 'dream/workflows/validation.py',
    'dream/projects/workspace.py', 'dream/projects/recovery.py',
    'dream/gui/workflow_routes.py', 'dream/gui/project_routes.py',
    'dream/eval_fixtures/harness_cases.json', 'dream/resources/skills/illuminati-handshake/SKILL.md'} | {
    f'dream/resources/skills/{skill}/{name}' for skill in _SKILLS
    for name in ('SKILL.md', 'manifest.json', 'references/examples.md')}
_MAX_INPUT = 2_000_000
_MAX_REPORT = 65_536
_MESSAGES = {
    'python_supported': 'Python 3.12 or newer.',
    'python_unsupported': 'Python 3.12 or newer is required.',
    'package_valid': 'Dream package metadata is readable.',
    'package_invalid': 'Package metadata is missing or malformed; verify the installation.',
    'dependency_ok': 'Installed version satisfies the declared requirement.',
    'dependency_missing': 'Required distribution is not installed.',
    'dependency_incompatible': 'Installed version does not satisfy the declared requirement.',
    'dependency_unknown': 'Cannot validate this dependency metadata.',
    'dependency_parser_missing': 'Version parser unavailable; dependency constraints were not checked.',
    'settings_valid': 'Supplied settings validate on a disposable copy.',
    'settings_invalid': 'Supplied settings failed validation; source was preserved.',
    'settings_fixture_pass': 'Legacy and v1 runtime settings validate; future versions are rejected in disposable fixtures.',
    'settings_fixture_fail': 'Runtime settings compatibility fixture failed.',
    'environment_valid': 'Central numeric environment settings have valid syntax and finite values; model-specific ranges are not checked.',
    'environment_integer_invalid': 'Invalid integer setting. Unset it to use its default, or provide an integer/documented unlimited alias.',
    'environment_float_invalid': 'Invalid number setting. Unset it to use its default, or provide a finite number/documented unlimited alias.',
    'assets_present': 'Required desktop assets, skill manifests and evaluation fixture are present.',
    'assets_missing': 'Required packaged assets are missing; check the package manifest.',
    'assets_invalid': 'The entry page is unreadable, unsafe or exceeds asset inspection bounds.',
    'wheel_not_run': 'No built wheel supplied; clean installation and release remain unqualified.',
    'wheel_valid': 'Supplied wheel assets and SHA-256 RECORD entries passed offline inspection.',
    'wheel_invalid': 'Wheel structure or metadata is invalid, unsafe, unreadable or exceeds inspection bounds.',
    'wheel_assets_missing': 'Wheel lacks required desktop assets, curated skill files or evaluation fixtures.',
    'wheel_record_invalid': 'Wheel RECORD is incomplete or a size/hash does not match.',
    'records_not_run': 'No offline evaluation records supplied.',
    'records_valid': 'All offline record checks passed; model performance and provenance are unqualified.',
    'records_failed': 'Offline record grading found failed or missing cases.',
    'records_invalid': 'Offline records are malformed, unreadable or exceed 2 MB.',
    'export_failed': 'Export failed. Choose a new writable file; existing files are never overwritten.',
}
_IDS = frozenset(('python', 'package', 'dependencies', 'settings_schema', 'environment', 'assets', 'wheel', 'records'))
_ENV_IDS = frozenset('environment:' + name for name in NUMERIC_SETTINGS)


def _row(identifier, status, code, **counts):
    row = {'id': identifier, 'status': status, 'code': code}
    if counts:
        row['counts'] = counts
    return row


def _read_regular(path: Path, maximum: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('regular file required')
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('input limit exceeded')
    return raw


def _entry_assets(raw: bytes) -> set[str]:
    """Direct local script/link references only; never execute or fetch HTML."""
    if len(raw) > 4_000_000:
        raise ValueError('entry page limit')
    assets = set()
    class References(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == 'base' and any(key == 'href' for key, _ in attrs):
                # A base can change the origin/path even of root-relative URLs.
                raise ValueError('base URL is not supported')
            attribute = {'script': 'src', 'link': 'href'}.get(tag)
            if attribute is None:
                return
            for key, value in attrs:
                if key != attribute or value is None:
                    continue
                if len(value) > 8192:
                    raise ValueError('resource URL limit')
                # URL padding is ASCII whitespace. Unicode spaces can be part
                # of a filename (including an HTML-decoded nonbreaking space).
                value = value.strip(' \t\r\n\f')
                if any(ord(c) < 32 for c in value):
                    raise ValueError('control character in URL')
                url = urlsplit(value)
                if url.scheme or url.netloc:
                    continue
                path = unquote(url.path, errors='strict')
                if path.startswith('./'):
                    path = path[2:]
                # Check before prefix filtering: browsers can normalize these
                # forms into /assets/ even when the literal prefix differs.
                if ('\\' in path or path.startswith('//')
                        or any(part in ('.', '..') for part in path.split('/'))):
                    raise ValueError('ambiguous local resource path')
                if path.startswith('/'):
                    path = path[1:]
                if not path.startswith('assets/'):
                    continue
                relative = path.removeprefix('assets/')
                if (not relative or any(c in relative for c in ('\\', '%'))
                        or any(ord(c) < 32 for c in relative)
                        or any(part in ('', '.', '..') for part in relative.split('/'))):
                    raise ValueError('unsafe asset path')
                assets.add('dream/gui/static/' + relative)
                if len(assets) > 4096:
                    raise ValueError('asset count limit')
    parser = References(convert_charrefs=True)
    parser.feed(raw.decode('utf-8'))
    parser.close()
    return assets


def _check_source_assets(package: Path) -> dict:
    required = set(_REQUIRED)
    try:
        try:
            required.update(_entry_assets(_read_regular(package / 'gui/static/index.html', 4_000_000)))
        except FileNotFoundError:
            pass  # the fixed inventory includes the absent entry page
        missing = 0
        for name in required:
            path = package.parent / name
            if name.startswith('dream/resources/skills/') and not (package / 'resources/skills').is_dir():
                path = package.parent / name.removeprefix('dream/resources/')
            if (not path.is_file() or path.is_symlink()
                    or (name.startswith('dream/gui/static/')
                        and not path.resolve().is_relative_to(package.resolve()))):
                missing += 1
        return _row('assets', 'fail' if missing else 'pass',
                    'assets_missing' if missing else 'assets_present', missing=missing)
    except (OSError, ValueError, UnicodeError, RecursionError):
        return _row('assets', 'fail', 'assets_invalid')


def check_dependencies(requirements: list[str], *, version_getter=None) -> list[dict]:
    """Read distribution metadata, never import dependency modules or execute CLIs."""
    try:
        from packaging.requirements import Requirement, InvalidRequirement
        from packaging.version import InvalidVersion
    except ImportError:
        return [_row('dependencies', 'warn', 'dependency_parser_missing')]
    version_getter = version_getter or metadata.version
    if not isinstance(requirements, list) or len(requirements) > 128:
        return [_row('dependencies', 'fail', 'dependency_unknown')]
    rows = []
    for raw in requirements:
        try:
            if not isinstance(raw, str) or len(raw) > 4096:
                raise ValueError('invalid requirement')
            requirement = Requirement(raw)
            if requirement.marker and not requirement.marker.evaluate({'extra': ''}):
                continue
            name = re.sub(r'[-_.]+', '-', requirement.name).lower()
            # Reports deliberately cannot echo arbitrary names or URLs from
            # untrusted metadata. New dependencies need a reviewed display name.
            if name not in _DEPENDENCIES or requirement.url:
                rows.append(_row('dependencies', 'warn', 'dependency_unknown'))
                continue
            identifier = 'dependency:' + name
            try:
                version = version_getter(requirement.name)
            except metadata.PackageNotFoundError:
                rows.append(_row(identifier, 'fail', 'dependency_missing'))
                continue
            good = requirement.specifier.contains(version, prereleases=True)
            rows.append(_row(identifier, 'pass' if good else 'fail',
                             'dependency_ok' if good else 'dependency_incompatible'))
        except (ValueError, TypeError, InvalidRequirement, InvalidVersion):
            rows.append(_row('dependencies', 'fail', 'dependency_unknown'))
    return rows


def check_settings_fixture(value: object) -> dict:
    """Use the actual runtime reader on a disposable JSON copy, without live state."""
    try:
        from .core.profiles import read_settings
        raw = json.dumps(value, allow_nan=False).encode()
        if len(raw) > _MAX_INPUT:
            raise ValueError('fixture too large')
        with tempfile.TemporaryDirectory(prefix='dream-diagnostic-settings-') as directory:
            path = Path(directory) / 'runtime-settings.json'
            path.write_bytes(raw)
            read_settings(path)
            if path.read_bytes() != raw:
                raise ValueError('reader modified fixture')
        return _row('settings_schema', 'pass', 'settings_valid')
    except (OSError, ValueError, TypeError, ImportError, RecursionError):
        return _row('settings_schema', 'fail', 'settings_invalid')


def inspect_wheel(path: Path) -> dict:
    """Validate a supplied wheel in memory; do not extract, execute or install it."""
    try:
        raw = _read_regular(Path(path), 64_000_000)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            names = {entry.filename for entry in entries}
            if (len(entries) > 40_000 or len(names) != len(entries)
                    or sum(e.file_size for e in entries) > 256_000_000):
                raise ValueError('archive bounds')
            for entry in entries:
                name = PurePosixPath(entry.filename)
                if (not name.parts or name.is_absolute() or '..' in name.parts or '\\' in entry.filename
                        or entry.filename.rstrip('/') != name.as_posix()
                        or stat.S_ISLNK(entry.external_attr >> 16)
                        or entry.file_size > 4_000_000 or entry.flag_bits & 1):
                    raise ValueError('unsafe member')
            metadata_names = [n for n in names if n.endswith('.dist-info/METADATA')]
            if len(metadata_names) != 1:
                raise ValueError('metadata missing')
            meta_name = metadata_names[0]
            prefix = meta_name.rsplit('/', 1)[0]
            if any(PurePosixPath(name).parts[0] not in ('dream', prefix) for name in names):
                raise ValueError('unexpected package root')
            parsed = BytesParser().parsebytes(archive.read(meta_name))
            if parsed.get('Name', '').lower() != 'dream' or not re.fullmatch(r'[0-9][a-zA-Z0-9.+!-]{0,63}', parsed.get('Version', '')):
                raise ValueError('invalid project metadata')
            wheel = BytesParser().parsebytes(archive.read(prefix + '/WHEEL'))
            if wheel.get('Wheel-Version') != '1.0' or not wheel.get('Tag'):
                raise ValueError('unsupported wheel metadata')
            if _REQUIRED - names:
                return _row('wheel', 'fail', 'wheel_assets_missing', missing=len(_REQUIRED - names))
            required = _REQUIRED | _entry_assets(archive.read('dream/gui/static/index.html'))
            if required - names:
                return _row('wheel', 'fail', 'wheel_assets_missing', missing=len(required - names))
            for skill in _SKILLS:
                manifest = json.loads(archive.read(f'dream/resources/skills/{skill}/manifest.json'))
                if not isinstance(manifest, dict) or manifest.get('format') != 'dream-skill/v1' or manifest.get('name') != skill:
                    raise ValueError('invalid skill manifest')
            cases = json.loads(archive.read('dream/eval_fixtures/harness_cases.json'))
            expected_ids = {'coding-boundary', 'documents-facts', 'data-totals',
                            'recovery-ambiguous-edit', 'tool-use-scope'}
            if (not isinstance(cases, list) or len(cases) != len(expected_ids)
                    or any(not isinstance(case, dict) or not isinstance(case.get('id'), str)
                           or not isinstance(case.get('task'), str) or not case['task']
                           or 'expected' not in case for case in cases)
                    or {case['id'] for case in cases} != expected_ids):
                raise ValueError('invalid evaluation fixture')
            record_name = prefix + '/RECORD'
            record = list(csv.reader(io.StringIO(archive.read(record_name).decode('utf-8'))))
            seen = set()
            for row in record:
                if len(row) != 3 or row[0] in seen or row[0] not in names:
                    return _row('wheel', 'fail', 'wheel_record_invalid')
                name, digest, size = row
                seen.add(name)
                if name == record_name:
                    if digest or size:
                        return _row('wheel', 'fail', 'wheel_record_invalid')
                    continue
                content = archive.read(name)
                wanted = 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b'=').decode()
                if digest != wanted or size != str(len(content)):
                    return _row('wheel', 'fail', 'wheel_record_invalid')
            if seen != {e.filename for e in entries if not e.is_dir()}:
                return _row('wheel', 'fail', 'wheel_record_invalid')
            return _row('wheel', 'pass', 'wheel_valid', files=len(seen))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, UnicodeError, RecursionError, zipfile.BadZipFile, csv.Error):
        return _row('wheel', 'fail', 'wheel_invalid')


def check_records(path: Path) -> dict:
    """Grade saved evidence only. Discard run IDs, prompts, outputs and paths."""
    try:
        from .harness_eval import grade
        report = grade(json.loads(_read_regular(Path(path), _MAX_INPUT)))
        good = report['passed'] == report['total']
        return _row('records', 'pass' if good else 'fail', 'records_valid' if good else 'records_failed',
                    passed=report['passed'], total=report['total'])
    except (OSError, ValueError, TypeError, KeyError, ImportError, RecursionError):
        return _row('records', 'fail', 'records_invalid')


def collect_report(*, wheel: Path | None = None, records: Path | None = None) -> dict:
    """Check this Python installation and package using bounded, read-only inputs."""
    supported = sys.version_info >= (3, 12)
    checks = [_row('python', 'pass' if supported else 'fail', 'python_supported' if supported else 'python_unsupported')]
    environment_errors = numeric_environment_errors()
    checks.extend([_row('environment:' + error.name, 'fail', error.code) for error in environment_errors]
                  or [_row('environment', 'pass', 'environment_valid', total=len(NUMERIC_SETTINGS))])
    package = Path(__file__).parent
    requirements = None
    try:
        project = package.parent / 'pyproject.toml'
        if project.is_file():
            parsed = tomllib.loads(_read_regular(project, _MAX_INPUT).decode())['project']
            if parsed['name'] != 'dream':
                raise ValueError('wrong project')
            requirements = parsed['dependencies']
        else:
            distribution = metadata.distribution('dream')
            requirements = distribution.requires
        if not isinstance(requirements, list) or not requirements:
            raise ValueError('dependency metadata missing')
        checks.append(_row('package', 'pass', 'package_valid'))
    except (OSError, ValueError, KeyError, TypeError, metadata.PackageNotFoundError):
        checks.append(_row('package', 'fail', 'package_invalid'))
    if requirements is not None:
        checks.extend(check_dependencies(requirements))
    states = [check_settings_fixture(value)['status'] for value in (
        {}, {'version': 1, 'profile': 'lean', 'overrides': {'max_parallel': 1}}, {'version': 2})]
    good = states == ['pass', 'pass', 'fail']
    checks.append(_row('settings_schema', 'pass' if good else 'fail', 'settings_fixture_pass' if good else 'settings_fixture_fail'))
    checks.append(_check_source_assets(package))
    checks.append(inspect_wheel(wheel) if wheel else _row('wheel', 'not_run', 'wheel_not_run'))
    checks.append(check_records(records) if records else _row('records', 'not_run', 'records_not_run'))
    return redact_report({'schema_version': 1, 'checks': checks})


def redact_report(report: dict) -> dict:
    """Allowlist the support export. Unknown strings and exception text never leave."""
    checks = []
    for row in report.get('checks', [])[:256]:
        if not isinstance(row, dict):
            continue
        identifier = row.get('id')
        if identifier not in _IDS and identifier not in _ENV_IDS and identifier not in {'dependency:' + name for name in _DEPENDENCIES}:
            continue
        if row.get('status') not in ('pass', 'warn', 'fail', 'not_run') or row.get('code') not in _MESSAGES:
            continue
        safe = {key: row[key] for key in ('id', 'status', 'code')}
        counts = row.get('counts', {})
        if isinstance(counts, dict):
            safe_counts = {key: value for key, value in counts.items() if key in ('missing', 'passed', 'total', 'files')
                           and type(value) is int and 0 <= value <= 1_000_000}
            if safe_counts:
                safe['counts'] = safe_counts
        checks.append(safe)
    return {'schema_version': 1, 'scope': 'offline CPU checks; no saved runtime configuration, inference or hardware inspection',
            'checks': checks, 'ok': bool(checks) and not any(row['status'] == 'fail' for row in checks)}


def export_report(report: dict, path: Path) -> None:
    raw = (json.dumps(redact_report(report), indent=2) + '\n').encode()
    if len(raw) > _MAX_REPORT:
        raise ValueError('diagnostic export exceeds 64 KiB')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)


def render_text(report: dict) -> str:
    report = redact_report(report)
    rows = ['Dream offline diagnostics', report['scope']]
    rows.extend(f"{row['status'].upper():7} {row['id']}: {_MESSAGES[row['code']]}" for row in report['checks'])
    return '\n'.join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='Print the redacted machine report.')
    parser.add_argument('--export', type=Path, help='Write a new private JSON report; refuse overwrite.')
    parser.add_argument('--wheel', type=Path, help='Inspect a supplied built wheel without installing.')
    parser.add_argument('--records', type=Path, help='Grade offline records without executing recorded tools.')
    args = parser.parse_args(argv)
    report = collect_report(wheel=args.wheel, records=args.records)
    if args.export:
        try:
            export_report(report, args.export)
        except (OSError, ValueError):
            print(json.dumps({'error': 'export_failed', 'message': _MESSAGES['export_failed']}))
            return 2
    print(json.dumps(report, indent=2) if args.json else render_text(report))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
