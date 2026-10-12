"""A saved parent round cannot migrate to different worker authority."""
from copy import deepcopy
from dataclasses import replace

import pytest

from test_local_subagents import _backend, _lead_final, _sub_final
from test_worker_contracts import _Engine, _spec
from test_worker_delegations import parent
from test_parent_continuation_http import stop_at_return, task_round


async def saved_parent(parent):
    specs = _spec(name='worker')
    backend = _backend([], specs)
    transport = backend._client = _Engine(
        _sub_final('original settled result'), lead=[task_round(), _lead_final()])
    await stop_at_return(backend, parent)
    assert parent.c.journal.state['parent_context']['status'] == 'settled'
    assert len(transport.posted) == 1
    return backend, specs


@pytest.mark.parametrize('change', ['roster', 'prompt', 'tool_names', 'control_schema', 'worker_model'])
async def test_changed_worker_authority_refuses_saved_parent_before_install(parent, change, monkeypatch):
    original, specs = await saved_parent(parent)
    changed = dict(specs)
    if change == 'roster':
        changed.update(_spec(name='other_worker'))
    elif change == 'prompt':
        changed['worker'] = replace(specs['worker'], prompt='Different worker instructions')
    elif change == 'tool_names':
        changed['worker'] = replace(specs['worker'], tool_names=('read_file',))
    fresh = _backend([], changed)
    if change == 'control_schema':
        schema = deepcopy(fresh._task_schema())
        schema['function']['description'] += '\nDifferent delegation contract'
        monkeypatch.setattr(fresh, '_task_schema', lambda: deepcopy(schema))
    elif change == 'worker_model':
        monkeypatch.setattr(fresh, '_role_model', lambda role: 'different-worker-model')
    transport = fresh._client = _Engine(_sub_final('must not execute'), lead=[_lead_final()])
    assert fresh.model == original.model
    assert fresh.messages[0] == original.messages[0]
    assert fresh.tools == original.tools
    before = deepcopy(fresh.messages)
    with pytest.raises(ValueError):
        await fresh.restore_parent_context(parent.c)
    assert fresh.messages == before
    assert transport.streamed == 0 and not transport.posted


async def test_unchanged_worker_authority_restores_one_original_pair(parent):
    original, specs = await saved_parent(parent)
    fresh = _backend([], dict(specs))
    transport = fresh._client = _Engine(_sub_final('must not execute'), lead=[_lead_final()])
    assert await fresh.restore_parent_context(parent.c)
    assert fresh.messages == original.messages
    assert fresh.messages[-1]['content'] == 'original settled result'
    assert sum(message.get('role') == 'tool' and message.get('tool_call_id') == 'same-call'
               for message in fresh.messages) == 1
    assert transport.streamed == 0 and not transport.posted
