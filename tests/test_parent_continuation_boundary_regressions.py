"""Actual multi-call and restore transaction boundaries with fake HTTP only."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from dream.core import parent_continuation as pc
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.task_checkpoints import bind
from test_local_subagents import _lead_final, _sub_final, _sse, _backend
from test_worker_contracts import _Engine, _workspace, _spec
from test_worker_delegations import parent
from test_parent_continuation_http import task_round, stop_at_return


@pytest.mark.parametrize('wrapped', [False, True])
async def test_task_and_real_sibling_settle_only_after_both_results(parent, wrapped):
    handled = []
    async def read(args):
        handled.append(args)
        return {'content': [{'type': 'text', 'text': 'sibling result'}]}
    tool = SimpleNamespace(name='read_file', description='Read a fixture file.',
        input_schema={'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']},
        handler=read)
    base = _backend([tool], _spec(name='worker'))
    backend = OpenAICompatBackend(provider=base.provider, model=base.model,
        system_prompt='s', tools=[tool], permission_cb=None, subagents=base._subagents,
        discovery_mode='descriptions' if wrapped else 'default')
    args = {'name': 'read_file', 'arguments': {'path': 'note'}} if wrapped else {'path': 'note'}
    calls = [
        {'index': 0, 'id': 'task-id', 'function': {'name': 'task',
            'arguments': json.dumps({'subagent_type': 'worker', 'prompt': 'original task'})}},
        {'index': 1, 'id': 'sibling-id', 'function': {'name': 'tool_call' if wrapped else 'read_file',
            'arguments': json.dumps(args)}}]
    lead = [_sse({'choices': [{'delta': {'tool_calls': calls}, 'finish_reason': None}]}),
            _sse({'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}]}), 'data: [DONE]']
    transport = backend._client = _Engine(_sub_final('task result'), lead=[lead, _lead_final()])
    with bind(parent.c), _workspace(parent.root):
        events = backend.ask('original goal')
        try:
            async for event in events:
                if event.kind != 'tool_result':
                    continue
                status = parent.c.journal.state['parent_context']['status']
                if event.data['id'] == 'task-id':
                    assert status == 'captured' and not handled
                else:
                    assert status == 'settled'
                    break
        finally:
            await events.aclose()
    assert transport.streamed == 1 and len(transport.posted) == 1
    assert handled == [{'path': 'note'}]
    reference = parent.c.journal.state['parent_context']
    restored = pc.load(parent.root, reference, execution_binding=backend._parent_context_binding())
    assert [m['tool_call_id'] for m in restored if m['role'] == 'tool'] == ['task-id', 'sibling-id']
    assert restored[reference['assistant_index']]['tool_calls'][1]['function']['name'] == (
        'tool_call' if wrapped else 'read_file')


@pytest.mark.parametrize('error', [OSError, asyncio.CancelledError])
async def test_restore_ledger_failure_or_cancel_changes_no_context_or_grant(parent, monkeypatch, error):
    original = _backend([], _spec(name='worker'))
    original._client = _Engine(_sub_final('original result'), lead=[task_round(), _lead_final()])
    await stop_at_return(original, parent)
    fresh = _backend([], _spec(name='worker'))
    transport = fresh._client = _Engine(_sub_final('must not run'), lead=[_lead_final()])
    fresh._lead_artifact_access = {'prior': {'tag': 'prior grant'}}
    fresh._checked_archive_access = {'tag': 'prior archive'}
    before = deepcopy((fresh.messages, fresh._lead_artifact_access, fresh._checked_archive_access))
    real_record = parent.c.journal.record
    def fail_record(event, **kwargs):
        if event == 'parent_context_installed':
            raise error()
        return real_record(event, **kwargs)
    monkeypatch.setattr(parent.c.journal, 'record', fail_record)
    with pytest.raises(error):
        await fresh.restore_parent_context(parent.c)
    assert (fresh.messages, fresh._lead_artifact_access, fresh._checked_archive_access) == before
    assert not transport.posted and transport.streamed == 0
