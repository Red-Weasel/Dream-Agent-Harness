"""Offline task fixtures and exported-record grading, with no inference adapter.

Self-test drives fixed native CPU file tools, not an agent. Grade never executes
recorded code or tool calls. See docs/harness-evaluation.md for the evidence limits.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import csv
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import stat
import tempfile
from typing import Any
from xml.sax.saxutils import escape
import zipfile

_CASES = Path(__file__).with_name('eval_fixtures') / 'harness_cases.json'
_MAX_RECORD_BYTES = 2_000_000
_MAX_MANIFEST_FILES = 10_000
_MAX_MANIFEST_ENTRIES = 20_000
_MAX_MANIFEST_BYTES = 512 * 1024 * 1024
_HASH_CHUNK_BYTES = 1024 * 1024


def load_cases() -> list[dict[str, Any]]:
    return json.loads(_CASES.read_text(encoding='utf-8'))


def _text(result: dict[str, Any]) -> str:
    return '\n'.join(block.get('text', '') for block in result.get('content', [])
                     if block.get('type') == 'text')


def _ast(source: Any) -> str | None:
    if not isinstance(source, str) or len(source) > 10000:
        return None
    try:
        return ast.dump(ast.parse(source))
    except (SyntaxError, ValueError, RecursionError):
        return None


def _validate(records: Any, known: set[str]) -> dict[str, dict[str, Any]]:
    if not isinstance(records, dict) or type(records.get('schema_version')) is not int or records['schema_version'] != 1:
        raise ValueError('Expected schema_version 1 object.')
    if records.get('origin') not in ('offline-self-test', 'external-recording'):
        raise ValueError('origin must be offline-self-test or external-recording.')
    if not isinstance(records.get('run_id'), str) or not records['run_id'].strip():
        raise ValueError('A nonempty run_id is required.')
    if not isinstance(records.get('cases'), list) or len(records['cases']) > len(known):
        raise ValueError('cases must be a list with at most one record per fixture.')
    found = {}
    for record in records['cases']:
        if not isinstance(record, dict) or not isinstance(record.get('id'), str):
            raise ValueError('Each case requires an id.')
        name = record['id']
        if name not in known or name in found:
            raise ValueError(f'Unknown or duplicate case: {name}')
        if not isinstance(record.get('outputs'), dict) or not isinstance(record.get('tools'), list):
            raise ValueError(f'{name}: outputs must be an object and tools a list.')
        for call in record['tools']:
            if (not isinstance(call, dict) or not isinstance(call.get('name'), str)
                    or not isinstance(call.get('arguments'), dict) or not isinstance(call.get('result'), dict)):
                raise ValueError(f'{name}: tools require name, arguments and result.')
            content = call['result'].get('content')
            if not isinstance(content, list) or any(
                    not isinstance(block, dict) or not isinstance(block.get('text'), str)
                    or block.get('type') != 'text' for block in content):
                raise ValueError(f'{name}: expected text tool-result content.')
            for key in ('is_error', 'isError'):
                if key in call['result'] and type(call['result'][key]) is not bool:
                    raise ValueError(f'{name}: {key} must be boolean.')
        found[name] = record
    return found


def _failed(call: dict[str, Any]) -> bool:
    return call['result'].get('is_error', False) or call['result'].get('isError', False)


def grade(
        records: dict[str, Any], *,
        allowed_outputs_by_case: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    """Check all fixed cases; missing cases fail, self-reported pass flags are ignored."""
    fixtures = load_cases()
    found = _validate(records, {case['id'] for case in fixtures})
    results = []
    for fixture in fixtures:
        name, expected = fixture['id'], fixture['expected']
        record = found.get(name)
        failures = []
        def check(condition, message):
            if not condition:
                failures.append(message)
        if record is None:
            failures.append('Missing case record.')
        else:
            out, calls = record['outputs'], record['tools']
            successful = [call for call in calls if not _failed(call) and _text(call['result']).strip()]
            def observed(tool, path):
                return [call for call in successful if call['name'] == tool and call['arguments'].get('path') == path]
            if name == 'coding-boundary':
                check(_ast(out.get('source')) == _ast(expected), 'Final source must contain only the requested comparison repair.')
                check(bool(observed('read_file', 'shipping.py')), 'Missing successful source read.')
                check(bool(observed('str_replace_edit', 'shipping.py')), 'Missing successful edit evidence.')
            elif name == 'documents-facts':
                facts = out.get('facts')
                check(facts == expected and all(type(facts[key]) is type(value)
                                               for key, value in expected.items()),
                      'Document facts or their required types differ from the fixture.')
                check(out.get('source') == 'brief.docx', 'Missing source filename attribution.')
                reads = observed('read_file', 'brief.docx')
                check(any(all(p in _text(call['result']) for p in fixture['paragraphs']) for call in reads),
                      'Document read evidence does not contain the source facts.')
            elif name == 'data-totals':
                for key, value in expected.items():
                    check(type(out.get(key)) is type(value) and out.get(key) == value, f'Incorrect {key}.')
                check(bool(observed('read_file', 'sales.csv')), 'Missing CSV source read.')
                output_paths = ({'totals.json'} if allowed_outputs_by_case is None
                                else allowed_outputs_by_case.get(name, {'totals.json'}))
                check(any(observed('write_file', path) for path in output_paths),
                      'Missing saved result evidence.')
                try:
                    artifact = json.loads(out.get('artifact', ''))
                except (ValueError, TypeError):
                    artifact = None
                check(artifact == expected and all(type(artifact[key]) is type(value) for key, value in expected.items()),
                      'Saved JSON does not match computed totals and counts.')
            elif name == 'recovery-ambiguous-edit':
                check(out.get('source') == expected, 'Primary must change while backup remains 30.')
                edits = [(i, call) for i, call in enumerate(calls) if call['name'] == 'str_replace_edit'
                         and call['arguments'].get('path') == 'config.txt']
                refused = [(i, call) for i, call in edits if _failed(call) and 'matches 2 time(s)' in _text(call['result'])]
                repaired = [(i, call) for i, call in edits if not _failed(call) and _text(call['result']).strip()
                            and call['arguments'].get('old_string') == 'primary=30'
                            and call['arguments'].get('new_string') == 'primary=60']
                check(any(i < j and a['arguments'] != b['arguments'] for i, a in refused for j, b in repaired),
                      'Missing ambiguous refusal followed by a specific changed retry.')
                check(any(c['name'] == 'read_file' and c['arguments'].get('path') == 'config.txt' and not _failed(c)
                          and set(fixture['input'].splitlines()).issubset(
                              line.strip() for line in _text(c['result']).splitlines())
                          for i, _ in refused for j, _ in repaired if i < j for c in calls[i+1:j]),
                      'Missing inspection between refusal and retry.')
            elif name == 'tool-use-scope':
                matches = out.get('matches', '')
                check(isinstance(matches, str) and expected in matches and 'API_VERSION = 3' in matches
                      and 'decoy' not in matches, 'Search must cite the scoped path, line and value without decoy.')
                searches = observed('grep', 'src')
                check(any(expected in _text(c['result']) and c['arguments'].get('pattern') == 'API_VERSION' for c in searches),
                      'Missing scoped grep evidence.')
        results.append({'id': name, 'passed': not failures, 'failures': failures})
    return {'schema_version': 1, 'run_id': records['run_id'], 'origin': records['origin'],
            'measurement': ('offline framework checks' if records['origin'] == 'offline-self-test'
                            else 'offline grading of externally supplied records; provenance not authenticated'),
            'limitation': 'No model quality or speed was measured by this command.',
            'passed': sum(result['passed'] for result in results), 'total': len(results), 'cases': results}


def _canonical_relative_path(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError(f'{field} must contain canonical relative paths.')
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ('', '.', '..') for part in path.parts) or path.as_posix() != value:
        raise ValueError(f'{field} must contain canonical relative paths.')
    return value


def _validate_manifest(value: Any, *, field: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise ValueError(f'{field} must be a workspace manifest object.')
    if len(value) > _MAX_MANIFEST_FILES:
        raise ValueError(f'{field} exceeds the file limit.')
    validated = {}
    total_bytes = 0
    for raw_path, raw_entry in value.items():
        path = _canonical_relative_path(raw_path, field=field)
        if path in validated:
            raise ValueError(f'{field} contains a duplicate path.')
        if not isinstance(raw_entry, dict) or set(raw_entry) != {'size', 'sha256'}:
            raise ValueError(f'{field} entries require only size and sha256.')
        size, digest = raw_entry['size'], raw_entry['sha256']
        if type(size) is not int or size < 0:
            raise ValueError(f'{field} sizes must be nonnegative integers.')
        total_bytes += size
        if total_bytes > _MAX_MANIFEST_BYTES:
            raise ValueError(f'{field} exceeds the byte limit.')
        if (not isinstance(digest, str) or len(digest) != 64
                or any(character not in '0123456789abcdef' for character in digest)):
            raise ValueError(f'{field} sha256 values must be lowercase hexadecimal digests.')
        validated[path] = {'size': size, 'sha256': digest}
    return validated


def capture_workspace_manifest(
        workspace: Path, *, max_files: int = _MAX_MANIFEST_FILES,
        max_total_bytes: int = _MAX_MANIFEST_BYTES,
        max_entries: int = _MAX_MANIFEST_ENTRIES,
) -> dict[str, dict[str, Any]]:
    """Record bounded, content-free evidence for every file under a workspace."""
    if type(max_files) is not int or max_files < 0:
        raise ValueError('max_files must be a nonnegative integer.')
    if type(max_total_bytes) is not int or max_total_bytes < 0:
        raise ValueError('max_total_bytes must be a nonnegative integer.')
    if type(max_entries) is not int or max_entries < 0:
        raise ValueError('max_entries must be a nonnegative integer.')
    root = Path(workspace)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('workspace must be a real directory, not a symlink.')
    manifest: dict[str, dict[str, Any]] = {}
    total_bytes = 0
    visited_entries = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                visited_entries += 1
                if visited_entries > max_entries:
                    raise ValueError('workspace manifest exceeds the entry limit.')
                entry_stat = entry.stat(follow_symlinks=False)
                relative = Path(entry.path).relative_to(root).as_posix()
                if stat.S_ISLNK(entry_stat.st_mode):
                    raise ValueError(f'workspace contains a symlink: {relative}')
                if stat.S_ISDIR(entry_stat.st_mode):
                    pending.append(Path(entry.path))
                    continue
                if not stat.S_ISREG(entry_stat.st_mode):
                    raise ValueError(f'workspace entry is not a regular file: {relative}')
                if len(manifest) >= max_files:
                    raise ValueError('workspace manifest exceeds the file limit.')
                digest = hashlib.sha256()
                descriptor = os.open(entry.path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                try:
                    opened_stat = os.fstat(descriptor)
                    if not stat.S_ISREG(opened_stat.st_mode):
                        raise ValueError(f'workspace entry is not a regular file: {relative}')
                    while chunk := os.read(descriptor, _HASH_CHUNK_BYTES):
                        total_bytes += len(chunk)
                        if total_bytes > max_total_bytes:
                            raise ValueError('workspace manifest exceeds the byte limit.')
                        digest.update(chunk)
                    final_stat = os.fstat(descriptor)
                finally:
                    os.close(descriptor)
                identity = lambda value: (
                    value.st_dev, value.st_ino, value.st_size,
                    value.st_mtime_ns, value.st_ctime_ns,
                )
                if identity(entry_stat) != identity(opened_stat) or identity(opened_stat) != identity(final_stat):
                    raise ValueError(f'workspace file changed while hashing: {relative}')
                manifest[_canonical_relative_path(relative, field='workspace')] = {
                    'size': final_stat.st_size,
                    'sha256': digest.hexdigest(),
                }
    return dict(sorted(manifest.items()))


def grade_deliverable(
        records: dict[str, Any], *, case_id: str,
        workspace_before: Any, workspace_after: Any,
        allowed_outputs: set[str] | None,
) -> dict[str, Any]:
    """Grade one fixture artifact and explicit workspace-preservation evidence."""
    fixtures = {case['id'] for case in load_cases()}
    if case_id not in fixtures:
        raise ValueError(f'Unknown case: {case_id}')
    if allowed_outputs is None or not isinstance(allowed_outputs, set):
        raise ValueError('allowed_outputs must be an explicit set, including an empty set for read-only tasks.')
    outputs = {_canonical_relative_path(path, field='allowed_outputs') for path in allowed_outputs}
    before = _validate_manifest(workspace_before, field='workspace_before')
    after = _validate_manifest(workspace_after, field='workspace_after')
    report = grade(records, allowed_outputs_by_case={case_id: outputs})
    artifact = next(case for case in report['cases'] if case['id'] == case_id)
    artifact_failures = list(artifact['failures'])
    record = next((case for case in records['cases'] if case['id'] == case_id), None)
    represented_bytes = None
    write_tool = None
    if record is not None and case_id == 'data-totals':
        represented = record['outputs'].get('artifact')
        represented_bytes = represented.encode('utf-8') if isinstance(represented, str) else None
        write_tool = 'write_file'
    elif record is not None and case_id in ('coding-boundary', 'recovery-ambiguous-edit'):
        represented = record['outputs'].get('source')
        represented_bytes = represented.encode('utf-8') if isinstance(represented, str) else None
        write_tool = 'str_replace_edit'
    if record is not None:
        recorded_outputs = {
            call['arguments'].get('path') for call in record['tools']
            if call['name'] == write_tool and not _failed(call)
            and _text(call['result']).strip() and call['arguments'].get('path') in outputs
        }
    else:
        recorded_outputs = set()
    if artifact['passed'] and write_tool is not None:
        if not outputs:
            artifact_failures.append('Write-bearing fixtures require an explicit allowed output path.')
        elif len(recorded_outputs) != 1:
            artifact_failures.append('Saved-artifact evidence must identify exactly one allowed output path.')
        else:
            path = next(iter(recorded_outputs))
            evidence = after.get(path)
            expected_evidence = None if represented_bytes is None else {
                'size': len(represented_bytes),
                'sha256': hashlib.sha256(represented_bytes).hexdigest(),
            }
            if evidence != expected_evidence:
                artifact_failures.append('Saved artifact bytes do not match the workspace manifest.')
    elif artifact['passed'] and outputs:
        artifact_failures.append('This fixture has no supported saved-artifact binding; use a read-only contract.')
    artifact_passed = artifact['passed'] and not artifact_failures
    failures = []
    for path, evidence in before.items():
        if path not in outputs and after.get(path) != evidence:
            failures.append(f'Pre-existing path changed or disappeared: {path}')
    for path in after.keys() - before.keys():
        if path not in outputs:
            failures.append(f'Unexpected output path: {path}')
    workspace_preserved = not failures
    return {
        'schema_version': 1,
        'case_id': case_id,
        'artifact_passed': artifact_passed,
        'artifact_failures': artifact_failures,
        'workspace_preserved': workspace_preserved,
        'workspace_failures': failures,
        'passed': artifact_passed and workspace_preserved,
    }


async def run_self_test() -> dict[str, Any]:
    """Run fixed CPU fixture procedures in a disposable workspace, never a model."""
    from .tools.context import ToolContext, bind_context
    from .tools.files import grep, str_replace_edit
    from .tools.native import read_file, write_file

    records = []
    with tempfile.TemporaryDirectory(prefix='dream-harness-eval-') as directory:
        workspace = Path(directory)
        # These file tools only access workspace. No database, browser, Engine,
        # provider or global context is initialized for this bounded procedure.
        context = ToolContext(store=None, working=None, browser=None, session_id='offline-eval', workspace=workspace)
        with bind_context(context):
            for fixture in load_cases():
                name = fixture['id']
                calls = []
                async def call(tool, **arguments):
                    result = await tool.handler(arguments)
                    calls.append({'name': tool.name, 'arguments': arguments, 'result': result})
                    return result
                if name == 'coding-boundary':
                    (workspace/'shipping.py').write_text(fixture['input'], encoding='utf-8')
                    await call(read_file, path='shipping.py')
                    await call(str_replace_edit, path='shipping.py', old_string='total > 50', new_string='total >= 50')
                    await call(read_file, path='shipping.py')
                    out = {'source': (workspace/'shipping.py').read_text(encoding='utf-8')}
                elif name == 'documents-facts':
                    paragraphs = ''.join(f'<w:p><w:r><w:t>{escape(p)}</w:t></w:r></w:p>' for p in fixture['paragraphs'])
                    with zipfile.ZipFile(workspace/'brief.docx', 'w') as archive:
                        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
                        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + paragraphs + '</w:body></w:document>')
                    result = await call(read_file, path='brief.docx')
                    lines = _text(result).splitlines()
                    def value(prefix):
                        return next((line.split(prefix, 1)[1].strip() for line in lines if prefix in line), '')
                    budget = value('Budget USD:')
                    out = {'facts': {'project': value('Project:'), 'budget_usd': int(budget) if budget.isdigit() else None,
                                     'deadline': value('Deadline:')}, 'source': 'brief.docx'}
                elif name == 'data-totals':
                    (workspace/'sales.csv').write_text(fixture['input'], encoding='utf-8')
                    await call(read_file, path='sales.csv')
                    rows = list(csv.DictReader(io.StringIO((workspace/'sales.csv').read_text(encoding='utf-8'))))
                    totals, excluded = {}, 0
                    for row in rows:
                        if not row['amount'].strip():
                            excluded += 1
                            continue
                        totals[row['region']] = totals.get(row['region'], Decimal('0')) + Decimal(row['amount'])
                    out = {'totals': {region: format(value, '.2f') for region, value in totals.items()},
                           'rows': len(rows), 'excluded': excluded}
                    await call(write_file, path='totals.json', content=json.dumps(out, sort_keys=True))
                    await call(read_file, path='totals.json')
                    out['artifact'] = (workspace/'totals.json').read_text(encoding='utf-8')
                elif name == 'recovery-ambiguous-edit':
                    (workspace/'config.txt').write_text(fixture['input'], encoding='utf-8')
                    await call(read_file, path='config.txt')
                    await call(str_replace_edit, path='config.txt', old_string='30', new_string='60')
                    await call(read_file, path='config.txt')
                    await call(str_replace_edit, path='config.txt', old_string='primary=30', new_string='primary=60')
                    out = {'source': (workspace/'config.txt').read_text(encoding='utf-8')}
                else:
                    (workspace/'src').mkdir()
                    (workspace/'src/client.py').write_text(fixture['input'], encoding='utf-8')
                    (workspace/'decoy.py').write_text('API_VERSION = 99\n', encoding='utf-8')
                    result = await call(grep, path='src', pattern='API_VERSION')
                    out = {'matches': _text(result)}
                # Local temp paths are incidental; retain portable fixture evidence.
                calls = json.loads(json.dumps(calls).replace(str(workspace), '<fixture-workspace>'))
                records.append({'id': name, 'tools': calls, 'outputs': out})
    return {'schema_version': 1, 'origin': 'offline-self-test', 'run_id': 'native-cpu-fixtures-v1', 'cases': records}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--grade', type=Path, help='Grade exported JSON records without replaying tools or code.')
    action.add_argument('--list', action='store_true', help='Print tasks and fixture inputs; do not run tools.')
    parser.add_argument('--export', type=Path, help='Save self-test records to a new JSON file; never overwrites.')
    args = parser.parse_args(argv)
    if args.export and (args.grade or args.list):
        parser.error('--export is only available for the default offline self-test.')
    try:
        if args.list:
            print(json.dumps(load_cases(), indent=2))
            return 0
        if args.grade:
            with args.grade.open('rb') as stream:
                raw = stream.read(_MAX_RECORD_BYTES + 1)
            if len(raw) > _MAX_RECORD_BYTES:
                raise ValueError('Record file exceeds 2 MB.')
            records = json.loads(raw)
        else:
            if args.export and args.export.exists():
                raise ValueError('Export path already exists; choose a new file.')
            records = asyncio.run(run_self_test())
            if args.export:
                with args.export.open('x', encoding='utf-8') as stream:
                    json.dump(records, stream, indent=2)
        report = grade(records)
        print(json.dumps(report, indent=2))
        return 0 if report['passed'] == report['total'] else 1
    except (OSError, ValueError, RecursionError) as exc:
        print(json.dumps({'error': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
