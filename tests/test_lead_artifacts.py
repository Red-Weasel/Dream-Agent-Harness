"""Managed lead saves originals and changes only the newest observation tail."""
import copy
import json
from types import SimpleNamespace

import pytest

from dream.core.tool_artifacts import ArtifactError, ArtifactStore
from dream.core.task_checkpoints import bind, wrap_tool
from dream.tools.artifact_tools import artifact_read
from dream.tools.context import ToolContext, bind_context
from test_task_checkpoints import managed
from test_cache_friendly_head import FakeEngine, backend, tool, turn


def metadata(text):
    return json.loads(text.split('\n', 1)[1].split('\n\nPreview', 1)[0])


async def test_actual_lead_persists_before_next_request_and_restores_scoped_access(managed):
    c, _ = managed
    context = ToolContext(store=None, working=None, browser=None, workspace=c.journal.path.parent.parent, session_id='lead-session')
    original = '界🚀\x00' * 15000 + '\nneedle evidence\n'
    calls = []
    engine = FakeEngine(lead=[{'calls': [('web_search', {})]}, {'text': 'finished'}])
    b = backend(engine, tools=[tool('web_search', lambda args: original, calls=calls), artifact_read])
    b.messages.append({'role': 'user', 'content': 'old unchecked data stays unchanged'})
    old = copy.deepcopy(b.messages)
    with bind(c), bind_context(context):
        await turn(b)
    assert b.messages[:len(old)] == old and len(calls) == 1
    last = next(m for m in reversed(engine.requests[1]['messages']) if m['role'] == 'tool')
    receipt = metadata(last['content'])
    saved = ArtifactStore(context.workspace, context.session_id, c.journal.run_id).read(receipt['handle'], limit=32768)
    assert saved['text'] == original[:saved['next_offset']]
    assert receipt['full_bytes'] == len(original.encode('utf-8'))
    assert c.journal.state['lead_artifacts'][0]['handle'] == receipt['handle']
    resumed = backend(FakeEngine(), tools=[artifact_read])
    resumed.restore_checked_artifacts(c)
    # A fresh receiving session may read only journal-authorized originals.
    context.session_id = 'resumed-session'
    with bind_context(context):
        text, failed = await resumed._exec_tool('artifact_read', {'handle': receipt['handle'], 'query': 'needle', 'limit': 100})
    assert not failed and 'needle evidence' in text
    resumed.restore_checked_artifacts(SimpleNamespace(
        journal=SimpleNamespace(state={'worker_workspace': str(context.workspace)}, run_id='new-run'),
        _state={'boundary': None}))
    with bind_context(context):
        denied, failed = await resumed._exec_tool('artifact_read', {'handle': receipt['handle']})
    assert failed and 'needle evidence' not in denied


async def test_save_failure_stops_later_calls_without_replaying_completed_tool(managed, monkeypatch):
    c, _ = managed
    context = ToolContext(store=None, working=None, browser=None, workspace=c.journal.path.parent.parent, session_id='lead-session')
    calls = []
    engine = FakeEngine(lead=[{'calls': [('web_search', {}), ('write_file', {})]}])
    b = backend(engine, tools=[wrap_tool(tool('web_search', lambda args: 'x' * 15000, calls=calls)),
                               tool('write_file', lambda args: 'effect', calls=calls)])
    def fail(*args, **kwargs):
        raise ArtifactError('disk_limit')
    monkeypatch.setattr(ArtifactStore, 'save', fail)
    with bind(c), bind_context(context):
        events = await turn(b)
    assert [name for name, args in calls] == ['web_search']
    assert len(engine.requests) == 1
    assert any(e.kind == 'tool_result' and 'must not be replayed' in str(e.data) for e in events)
    assert len([m for m in b.messages if m['role'] == 'tool']) == 2
    assert c.pending_recovery['kind'] == 'blocked'
    assert c._state['observation_failure'] == 'artifact_persistence'
    # The actual handler succeeded; archival failure is a separate host fault.
    assert c._state['recent'][0]['disposition'] == 'returned-success'
    from dream.core.run_state import RunState
    from dream.core.task_checkpoints import ManagedTaskCoordinator
    c.journal.close()
    journal = RunState(c.journal.path.parent, c.journal.run_id)
    try:
        restored = ManagedTaskCoordinator(journal, c.tasks)
        restored.attach()
        assert restored.pending_recovery['kind'] == 'blocked'
        receiver = backend(FakeEngine(), tools=[tool('write_file', 'effect', calls=calls)])
        with bind(restored), bind_context(context):
            text, failed = await receiver._exec_tool('write_file', {})
        assert failed and [name for name, args in calls] == ['web_search']
        assert 'admission is closed' in text.lower()
    finally:
        journal.close()


async def test_journal_failure_does_not_install_preview(managed, monkeypatch):
    c, _ = managed
    context = ToolContext(store=None, working=None, browser=None, workspace=c.journal.path.parent.parent, session_id='lead-session')
    b = backend(FakeEngine())
    def fail(*args, **kwargs):
        raise OSError('ledger unavailable')
    monkeypatch.setattr(c.journal, 'record', fail)
    with bind(c), bind_context(context):
        with pytest.raises(OSError):
            await b._lead_observation('x' * 10000, 'call')
    assert b.messages == [{'role': 'system', 'content': 'SYSTEM PROMPT'}]


async def test_model_cannot_select_foreign_artifact_run(managed):
    c, _ = managed
    context = ToolContext(store=None, working=None, browser=None, workspace=c.journal.path.parent.parent, session_id='lead-session')
    b = backend(FakeEngine(), tools=[artifact_read])
    with bind(c), bind_context(context):
        receipt = metadata(await b._lead_observation('x' * 10000, 'call'))
    foreign = ArtifactStore(context.workspace, context.session_id, 'foreign-run').save('private other run', 'call')
    with bind_context(context):
        text, failed = await b._exec_tool('artifact_read', {'handle': foreign.handle})
        own, own_failed = await b._exec_tool('artifact_read', {'handle': receipt['handle'], 'limit': 20})
    assert failed and 'private other run' not in text
    assert not own_failed and 'x' * 20 in own


async def test_cancel_during_archival_preserves_completed_original_and_pairing(managed, monkeypatch):
    import asyncio
    c, _ = managed
    context = ToolContext(store=None, working=None, browser=None,
                          workspace=c.journal.path.parent.parent, session_id='lead-session')
    from test_local_subagents import _tool
    original, calls = 'original evidence\x00界' * 1000, []
    async def visual(args):
        calls.append(('web_search', args))
        return {'content': [{'type': 'text', 'text': original},
                            {'type': 'image', 'mimeType': 'image/png', 'data': 'c3ludGhldGlj'}]}
    engine = FakeEngine(lead=[{'calls': [('web_search', {}), ('write_file', {})]}])
    b = backend(engine, tools=[wrap_tool(_tool('web_search', visual)),
                               tool('write_file', lambda args: 'effect', calls=calls)], multimodal=True)
    saving = asyncio.Event()
    async def suspended(*args, **kwargs):
        saving.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(b, '_lead_observation', suspended)
    with bind(c), bind_context(context):
        task = asyncio.create_task(turn(b))
        await asyncio.wait_for(saving.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    observations = [m for m in b.messages if m['role'] == 'tool']
    assert observations[0]['content'] == original + '\n[image]'
    assert len(observations) == 2 and len(calls) == 1
    assert any(isinstance(m.get('content'), list) and 'data:image/png;base64,c3ludGhldGlj' in json.dumps(m)
               for m in b.messages)
    assert c.pending_recovery['kind'] == 'blocked'
    assert c._state['observation_failure'] == 'artifact_archival_interrupted'
    assert c._state['recent'][0]['disposition'] == 'returned-success'


def test_tail_fit_cannot_cut_unarchived_small_result():
    b = backend(FakeEngine())
    small = {'role': 'tool', 'tool_call_id': 'small', 'content': 'small original evidence' * 120}
    messages = [{'role': 'system', 'content': 'head' * 1000},
                {'role': 'assistant', 'content': 'older middle' * 1000}, small]
    original = copy.deepcopy(messages)
    b._fit_worker_observations(messages, [], 2, window=4500, reply_reserve=1000,
                               trigger=.8, preserve_unarchived=True)
    assert messages == original


def test_half_context_is_finish_cue_without_reset_or_middle_edit(managed, monkeypatch):
    c, _ = managed
    b = backend(FakeEngine(), n_ctx=10000)
    b.messages.append({'role': 'assistant', 'content': 'old middle'})
    old = copy.deepcopy(b.messages)
    monkeypatch.setattr(b, '_fill_bound', lambda: 5100)
    monkeypatch.setattr(b, '_ctx_fill', lambda: 5100)
    with bind(c):
        events = b._context_warning()
        again = b._context_warning()
    assert events and not again
    assert b.messages[:len(old)] == old
    assert 'finish-step cue, not reset permission' in b.messages[-1]['content']


@pytest.mark.parametrize('name,args', [
    ('task', {'subagent_type': 'fixture', 'prompt': 'do work'}),
    ('fork_verifier_agent', {'prompt': 'verify'}),
    ('artifact_read', {'handle': 'not-authorized'}),
])
async def test_failed_failure_journal_closes_builtin_admission(managed, monkeypatch, name, args):
    c, _ = managed
    action = c.begin_action('read_fixture', {})
    c.finish_action(action, 'returned-success', {'content': []})
    b = backend(FakeEngine(), tools=[artifact_read])
    b._subagents = {'fixture': object()}  # Dispatch trap, not a provider schema fixture.
    entered = []
    async def forbidden(*args, **kwargs):
        entered.append(True)
        pytest.fail('lost journal authority must not dispatch builtins')
    monkeypatch.setattr(b, '_run_subagent', forbidden)
    monkeypatch.setattr(b, '_fork_verifier', forbidden)
    def fail(*args, **kwargs):
        raise OSError('ledger unavailable')
    monkeypatch.setattr(c.journal, 'record', fail)
    with pytest.raises(OSError):
        c.record_observation_failure()
    assert c.pending_recovery is None  # The durable failure record could not land.
    with bind(c):
        text, failed = await b._exec_tool(name, args)
    assert failed and not entered and 'admission is closed' in text.lower()
    assert not c.actions_admitted
    assert c._state['recent'][0]['disposition'] == 'returned-success'
