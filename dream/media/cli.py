"""JSON media CLI, also used for owned background worker dispatch."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from .service import MediaService


def main(argv=None):
    parser = argparse.ArgumentParser(prog='dream media')
    parser.add_argument('--workspace', type=Path, default=Path.cwd())
    parser.add_argument(
        'action',
        choices=[
            'status',
            'probe_blender',
            'projects',
            'create',
            'get',
            'save',
            'import',
            'jobs',
            'render',
            'cancel',
            'handoff',
            'complete',
            'complete_handoff',
            'generate',
            'reconcile',
            'open',
            'open_external',
            'recover',
            'worker',
        ],
    )
    parser.add_argument('--json', default='{}', help='Action payload as JSON object')
    for flag in ('project-id', 'job', 'title', 'path', 'provider', 'prompt', 'format', 'idempotency-key', 'base-url'):
        parser.add_argument('--' + flag)
    for flag in ('revision', 'expected-revision'):
        parser.add_argument('--' + flag, type=int)
    parser.add_argument('--composition', type=Path, help='Composition JSON file')
    parser.add_argument('--workflow', type=Path, help='Reviewed API workflow JSON file')
    parser.add_argument('--wait', action='store_true')
    parser.add_argument('--resource-confirmed', action='store_true')
    parser.add_argument('--workflow-confirmed', action='store_true')
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.json)
        if not isinstance(payload, dict):
            raise ValueError('--json must be an object')
        for key, value in vars(args).items():
            if key in {'workspace', 'action', 'json'} or value is None or value is False:
                continue
            payload['job_id' if key == 'job' else key] = json.loads(value.read_text()) if key in {'composition', 'workflow'} else value
        service = MediaService(args.workspace)
        action = {'complete': 'complete_handoff', 'open': 'open_external'}.get(args.action, args.action)
        result = asyncio.run(service.run_job(payload['job_id']) if action == 'worker' else service.execute(action, payload))
        print(json.dumps(result, allow_nan=False))
        return 1 if result.get('status') == 'failed' else 0
    except (ValueError, KeyError, OSError) as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        return 2
if __name__ == '__main__':
    sys.exit(main())
