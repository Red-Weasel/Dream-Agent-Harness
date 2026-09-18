"""Offline comparison of explicitly supplied, artifact-graded task trials.

Never runs a model, reads referenced artifact paths, or applies settings. Export
provenance and total task duration are supplied by the caller, not authenticated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import mean

from .harness_eval import grade_deliverable, load_cases

MAX_BYTES = 2_000_000
MAX_TRIALS = 200
_CONFIG_FIELDS = {'provider', 'model_sha256', 'profile', 'reasoning_effort',
                  'context_window', 'output_ceiling', 'source'}
_TRIAL_FIELDS = {'run_id', 'sample_id', 'case_id', 'harness_sha256', 'environment_sha256',
                 'configuration', 'total_elapsed_s', 'duration_scope', 'final',
                 'protocol_outcome', 'records', 'workspace_before', 'workspace_after',
                 'allowed_outputs'}


def _label(value, field):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value):
        raise ValueError(f'{field} must be a bounded opaque label, not content or a path.')
    return value


def _digest(value, field):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
        raise ValueError(f'{field} must be a lowercase SHA-256 digest.')
    return value


def _configuration(value):
    if not isinstance(value, dict) or set(value) != _CONFIG_FIELDS:
        raise ValueError('configuration requires exactly the documented identity and settings fields.')
    result = {}
    for key in _CONFIG_FIELDS:
        item = value[key]
        if item is None:
            result[key] = None
        elif key == 'model_sha256':
            result[key] = _digest(item, key)
        elif key in ('context_window', 'output_ceiling'):
            if type(item) is not int or not 0 < item <= 2**53 - 1:
                raise ValueError(f'{key} must be a positive integer or null.')
            result[key] = item
        else:
            result[key] = _label(item, key)
    return result


def compare_trials(bundle: dict) -> dict:
    """Grade every submitted trial and compare only complete matched evidence.

    One trial represents one whole task, including its repairs and verification.
    Do not feed request/turn snapshots here or count late revisions as new trials.
    The caller must export the final reconciled outcome for each unique run ID.
    """
    if (not isinstance(bundle, dict) or set(bundle) != {'schema_version', 'trials'}
            or type(bundle['schema_version']) is not int or bundle['schema_version'] != 1):
        raise ValueError('Expected schema_version 1 and trials.')
    trials = bundle['trials']
    if not isinstance(trials, list) or not 1 <= len(trials) <= MAX_TRIALS:
        raise ValueError(f'trials must contain 1–{MAX_TRIALS} records.')
    try:
        encoded = json.dumps(bundle, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError('Trials must be finite JSON data.') from exc
    if len(encoded) > MAX_BYTES:
        raise ValueError('Trial bundle exceeds the 2 MB bound.')
    groups, seen, scopes = {}, set(), set()
    for trial in trials:
        if not isinstance(trial, dict) or set(trial) != _TRIAL_FIELDS:
            raise ValueError('Each trial requires exactly the documented evidence fields.')
        run_id = _label(trial['run_id'], 'run_id')
        if run_id in seen:
            raise ValueError('Duplicate run_id: reconcile corrections before comparing.')
        seen.add(run_id)
        case_id = _label(trial['case_id'], 'case_id')
        sample_id = _label(trial['sample_id'], 'sample_id')
        scopes.add((_digest(trial['harness_sha256'], 'harness_sha256'),
                    _digest(trial['environment_sha256'], 'environment_sha256')))
        configuration = _configuration(trial['configuration'])
        identity = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
        elapsed = trial['total_elapsed_s']
        if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed <= 0:
            raise ValueError('total_elapsed_s must be a finite positive number.')
        if type(trial['final']) is not bool:
            raise ValueError('final must be boolean.')
        protocol = _label(trial['protocol_outcome'], 'protocol_outcome')
        duration_scope = _label(trial['duration_scope'], 'duration_scope')
        records = trial['records']
        if not isinstance(records, dict) or records.get('run_id') != run_id:
            raise ValueError('Recorded artifact evidence must have the same run_id.')
        if (not isinstance(records.get('cases'), list) or len(records['cases']) != 1
                or not isinstance(records['cases'][0], dict)
                or records['cases'][0].get('id') != case_id):
            raise ValueError('Each trial must contain only its own case evidence.')
        allowed = trial['allowed_outputs']
        if (not isinstance(allowed, list) or any(not isinstance(path, str) for path in allowed)
                or len(set(allowed)) != len(allowed)):
            raise ValueError('allowed_outputs must be a list of distinct relative paths.')
        grade = grade_deliverable(records, case_id=case_id,
                                  workspace_before=trial['workspace_before'],
                                  workspace_after=trial['workspace_after'], allowed_outputs=set(allowed))
        reasons = []
        if not trial['final']:
            reasons.append('task_not_final')
        if protocol != 'completed':
            reasons.append('protocol_not_completed')
        if not grade['artifact_passed']:
            reasons.append('artifact_failed')
        if not grade['workspace_preserved']:
            reasons.append('workspace_changed_outside_contract')
        if duration_scope != 'task_including_repairs_and_verification':
            reasons.append('incomplete_duration_scope')
        if any(value is None for value in configuration.values()) or configuration['source'] != 'prepared_http':
            reasons.append('effective_configuration_unknown')
        group = groups.setdefault(identity, {'configuration_id': identity, 'configuration': configuration,
                                             'trials': [], '_samples': set()})
        sample = (case_id, sample_id)
        if sample in group['_samples']:
            raise ValueError('Duplicate case/sample within one configuration; use distinct repetition IDs.')
        group['_samples'].add(sample)
        group['trials'].append({'run_id': run_id, 'case_id': case_id, 'sample_id': sample_id,
                                'total_elapsed_s': elapsed, 'eligible': not reasons, 'reasons': reasons})
    rows = list(groups.values())
    coverage = [row.pop('_samples') for row in rows]
    for row in rows:
        row['count'] = len(row['trials'])
        row['failed_or_unknown'] = sum(not trial['eligible'] for trial in row['trials'])
        row['mean_total_elapsed_s'] = mean(trial['total_elapsed_s'] for trial in row['trials'])
    rows.sort(key=lambda row: (row['failed_or_unknown'] > 0, row['mean_total_elapsed_s'], row['configuration_id']))
    status = ('insufficient_configurations' if len(rows) < 2 else
              'incomparable_environment_or_harness' if len(scopes) != 1 else
              'incomparable_task_coverage' if any(sample != coverage[0] for sample in coverage[1:]) else
              'incomplete_or_failed_evidence' if any(row['failed_or_unknown'] for row in rows) else
              'tied' if rows[0]['mean_total_elapsed_s'] == rows[1]['mean_total_elapsed_s'] else 'comparable')
    fixture_hash = hashlib.sha256(json.dumps(load_cases(), sort_keys=True).encode()).hexdigest()
    return {'schema_version': 1, 'comparison_status': status, 'configurations': rows,
            'observed_fastest_configuration': rows[0]['configuration_id'] if status == 'comparable' else None,
            'comparison_scopes': [{'harness_sha256': harness, 'environment_sha256': environment}
                                  for harness, environment in sorted(scopes)],
            'fixture_sha256': fixture_hash, 'trial_count': len(trials),
            'settings_applied': False, 'model_quality_measured_by_comparator': False,
            'raw_content_recorded': False,
            'limitation': 'Descriptive comparison of supplied evidence, not an optimum or statistical quality claim. '
                          'Provenance, environment identity, configuration and whole-task duration are caller supplied. '
                          'No provider call, live timing, export discovery or settings change is performed.'}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON object key.')
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compare', required=True, type=Path, help='Explicit task-trial JSON bundle (up to 2 MB).')
    args = parser.parse_args(argv)
    try:
        with args.compare.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('Trial bundle exceeds the 2 MB bound.')
        report = compare_trials(json.loads(raw, object_pairs_hook=_unique_object))
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        parser.exit(2, f'Comparison unavailable: {exc}\n')
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
