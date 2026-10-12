#!/usr/bin/env python3
"""Inspect explicitly selected settled run ledgers; never start or resume work."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# Permit invocation from a checkout without installing a second environment.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dream.core.endurance_report import build_endurance_report, build_phase_endurance_report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_root', type=Path, help='Directory containing canonical run directories')
    parser.add_argument('run_ids', nargs='*', help='Explicit run IDs; directories are not scanned')
    parser.add_argument('--schedule', help='One canonical parent schedule; inspect only its linked children')
    args = parser.parse_args(argv)
    if bool(args.run_ids) == bool(args.schedule):
        parser.error('Select child run IDs or one --schedule, exclusively.')
    try:
        report = (build_phase_endurance_report(args.run_root, args.schedule) if args.schedule
                  else build_endurance_report(args.run_root, args.run_ids))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'Endurance inspection unavailable: {exc}', file=sys.stderr)
        return 2
    # This CLI has no task-store or acceptance-check authority. It reports
    # ledger observations; accepted work and qualification stay unverified.
    print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
