"""Late packaged-skill retrieval cannot qualify an otherwise correct artifact."""
import json
from pathlib import Path

import pytest

from dream.local_harness_eval import LocalEvalConfig, launch
from test_local_harness_eval_http import responder


@pytest.mark.parametrize('mode', ['default', 'descriptions'])
def test_correct_skill_found_and_opened_after_csv_read_does_not_qualify(responder, tmp_path, mode):
    endpoint, replies, observed = responder
    expected = {'totals': {'North, Central': '12.30', 'South': '20.00'}, 'rows': 4, 'excluded': 1}
    replies.extend([
        {'call': ('read_file', {'path': 'sales.csv'})},
        {'call': ('skill_find', {'query': 'CSV'})},
        {'call': ('skill_open', {'name': 'data-analysis'})},
        {'call': ('write_file', {'path': 'totals.json', 'content': json.dumps(expected)})},
        {'content': 'STATUS: DONE'},
        {'call': ('read_file', {'path': 'totals.json'})},
        {'content': 'VERDICT: PASS\nGAPS: none'},
    ])
    run = tmp_path / 'late-workflow'
    report = launch(LocalEvalConfig(endpoint, 'synthetic-fixture', mode, max_requests=10,
                                   max_wall_seconds=12, catalog_mode='builtin_tools_skills'), run)
    assert report['passed'] is False, (report, (run / 'process.log').read_text())
    assert report['skill_selection']['required'] is True
    assert report['skill_selection']['checked'] is False
    assert report['ledger_evidence']['accepted_work_count'] == 0
    assert json.loads((run / 'workspace/totals.json').read_text()) == expected

    calls = report['tool_calls']
    assert [call['name'] for call in calls[:4]] == ['read_file', 'skill_find', 'skill_open', 'write_file']
    for call in calls[:4]:
        assert not call['result'].get('is_error') and not call['result'].get('isError')
    matches = json.loads(calls[1]['result']['content'][0]['text'])['matches']
    assert any(row['name'] == 'data-analysis' for row in matches)
    workflow = (Path(__file__).resolve().parents[1] / 'skills/data-analysis/SKILL.md').read_text()
    assert calls[2]['result']['content'][0]['text'] == workflow
    assert observed and len(observed) <= 10
    assert not report['live_endurance_qualified']
