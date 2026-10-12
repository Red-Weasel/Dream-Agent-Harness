"""Explicit owner reconciliation parsing; notes never substitute for action IDs."""
from __future__ import annotations
import re
import shlex


def reconciliation_actions(values: list[str] | None) -> dict[str, str]:
    actions = {}
    for value in values or []:
        action, separator, report = value.partition('=')
        if not separator or not re.fullmatch(r'[0-9a-f]{32}', action) or not report.strip():
            raise ValueError('Reconciliation requires ACTION_ID=REPORT (32 lowercase hex characters and a nonempty report).')
        if action in actions:
            raise ValueError('Duplicate reconciliation action ID.')
        if len(actions) >= 128 or len(report) > 8192:
            raise ValueError('Reconciliation exceeds its bounded action/report limit.')
        actions[action] = report.strip()
    return actions


def resume_arguments(text: str) -> tuple[str, str | None, dict[str, str]]:
    run_id, _, tail = text.strip().partition(' ')
    if not run_id:
        raise ValueError('/loop-resume requires a run ID.')
    # Legacy free-form notes remain verbatim, including unbalanced quotes.
    if not re.search(r'(^|\s)--(?:reconcile|note)(?:\s|$)', tail):
        return run_id, tail.strip() or None, {}
    fields = shlex.split(tail)
    actions, note = [], None
    index = 0
    while index < len(fields):
        flag = fields[index]
        if flag not in ('--reconcile', '--note') or index + 1 == len(fields):
            raise ValueError('Use --reconcile ACTION_ID="verified report" and optional --note "resume note".')
        value = fields[index + 1]
        if flag == '--reconcile':
            actions.append(value)
        elif note is not None:
            raise ValueError('Specify --note once.')
        else:
            note = value.strip() or None
        index += 2
    return run_id, note, reconciliation_actions(actions)
