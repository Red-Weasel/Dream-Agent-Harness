"""Actual Controls scope renderer in CPU Node with text-only DOM mocks."""
import json
from pathlib import Path
import subprocess

import pytest


_SOURCE = Path(__file__).resolve().parents[1] / 'dream/gui/static/controls.js'


def render(execution, *, deadline=None):
    source = _SOURCE.read_text()
    # Exercise the actual isolated renderer without booting the page's fetch/timers.
    start = source.index('  function updateScope() {')
    end = source.index("  byId('dc-profile-form').addEventListener", start)
    renderer = source[start:end]
    program = r'''
const fs = require('fs');
const {execution, deadline, renderer} = JSON.parse(fs.readFileSync(0, 'utf8'));
const fields = new Map();
const byId = id => {
  if(!fields.has(id)) fields.set(id, {
    textContent:'',
    set innerHTML(value) { throw new Error('Reason text must never be rendered as HTML'); }
  });
  return fields.get(id);
};
const runtime = {execution};
const scopeDeadline = deadline;
Date.now = () => 100000;
global.fetch = () => { throw new Error('render must not fetch or mutate controls'); };
global.setTimeout = () => { throw new Error('render must not schedule work'); };
eval(renderer + '\nupdateScope();');
console.log(JSON.stringify(Object.fromEntries([...fields].map(([id,node]) => [id,node.textContent]))));
'''
    result = subprocess.run(['node', '-e', program], input=json.dumps({
        'execution': execution, 'deadline': deadline, 'renderer': renderer}),
        text=True, capture_output=True, timeout=5, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize('scope,badge', [
    ({'checked': True, 'available': True}, 'Saved check passed'),
    ({'checked': True, 'available': False}, 'Saved check unavailable'),
    ({'checked': False, 'available': False}, 'No saved check'),
    ({'available': True}, 'Saved check passed'),
    ({'available': False, 'reason': 'legacy denial'}, 'Check status unreported'),
    ({}, 'Check status unreported'), (None, 'Check status unreported'),
    ([], 'Check status unreported'), ('bad', 'Check status unreported'),
    ({'checked': True, 'available': 'true'}, 'Check status unreported'),
    ({'checked': True, 'available': 1}, 'Check status unreported'),
    ({'checked': 'true', 'available': True}, 'Check status unreported'),
    ({'checked': False, 'available': True}, 'No saved check'),
])
def test_saved_check_states_and_legacy_payloads(scope, badge):
    fields = render(scope)
    assert fields['dc-scope-badge'] == badge
    note = fields['dc-scope-status']
    assert 'Saved shell check' in note
    assert 'startup' in note and 'scope' in note and 'recheck' in note
    assert 'JavaScript runtime readiness is not reported here' in note
    assert 'when called' in note
    assert 'available now' not in note.lower() and 'last check' not in note.lower()
    if isinstance(scope, dict) and 'reason' in scope:
        assert scope['reason'] in note


def test_reason_is_preserved_as_text():
    reason = '<img src=x onerror="evil()">\nfixture denied'
    fields = render({'checked': True, 'available': False, 'reason': reason})
    assert reason in fields['dc-scope-status']


@pytest.mark.parametrize('checked,available', [(True, True), (True, False), (False, False)])
@pytest.mark.parametrize('deadline,countdown', [(None, 'countdown unavailable'),
    (220000, 'Expires in 2 min.'), (100000, 'Scope expired.')])
def test_red_team_keeps_targets_countdown_and_evidence(checked, available, deadline, countdown):
    fields = render({'checked': checked, 'available': available, 'red_team': True,
                     'targets': ['fixture/target'], 'reason': 'fixture reason'}, deadline=deadline)
    assert fields['dc-scope-badge'] == 'Scoped exercise'
    note = fields['dc-scope-status']
    assert 'fixture/target' in note and countdown in note
    assert 'Saved shell check' in note and 'fixture reason' in note
    assert 'JavaScript runtime readiness is not reported here' in note
