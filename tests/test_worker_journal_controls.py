"""Owner controls become durable in managed HTTP workers before acknowledgement."""
import asyncio

from dream.core.worker_checkpoints import WorkerJournal
from test_local_subagents import _backend, _lead_final, _sub_final, _sub_toolcall, _tool
from test_parent_continuation_http import collect, task_round
from test_worker_contracts import _Engine, _spec
from test_worker_delegations import parent
from test_worker_journal_http import durable, journals


async def test_owner_message_is_durable_before_ack_and_consumed_with_next_snapshot(parent, journals):
    message = 'owner instruction 🌙 preserve exact text'
    observed = []
    async def read_file(arguments):
        return {'content': [{'type': 'text', 'text': 'fixture result'}]}
    backend = _backend([_tool('read_file', read_file)], _spec('read_file', name='worker'))
    class Engine(_Engine):
        async def post(self, url, json=None):
            if not self.posted:
                assert len(journals) == 1
                ack = backend.worker_control('agent_message', journals[0].run_id, message)
                state = durable(journals[0])
                assert ack['pending'] == 1
                assert [item['text'] for item in state['messages']] == [message]
                observed.append('durable message')
            else:
                assert durable(journals[0])['messages'] == []
                assert any(message in str(item.get('content', '')) for item in json['messages'])
                observed.append('consumed')
            return await super().post(url, json=json)
    backend._client = Engine(_sub_toolcall('read_file', {'path': 'fixture'}), _sub_final('done'),
                             lead=[task_round(), _lead_final()])
    await collect(backend, parent)
    assert observed == ['durable message', 'consumed']


async def test_requested_pause_waits_for_round_boundary_before_credit(parent, journals, monkeypatch):
    observations = []
    async def read_file(arguments):
        return {'content': []}
    backend = _backend([_tool('read_file', read_file)], _spec('read_file', name='worker'))
    original_control = WorkerJournal.control
    def control(journal, action):
        result = original_control(journal, action)
        if action == 'paused':
            state = durable(journal)
            assert state['control'] == 'paused' and state['pending'] is None
            observations.append('round boundary')
            # Release only after the durable pause is observed, with no sleep.
            asyncio.get_running_loop().call_soon(backend.worker_control, 'agent_resume', journal.run_id)
        return result
    monkeypatch.setattr(WorkerJournal, 'control', control)
    class Engine(_Engine):
        async def post(self, url, json=None):
            if not self.posted:
                journal = journals[0]
                before = durable(journal)
                ack = backend.worker_control('agent_pause', journal.run_id)
                after = durable(journal)
                assert ack['state'] == 'pausing'
                assert after['control'] == 'running'
                assert after['deadline_ns'] == before['deadline_ns']
                assert after['pending'] == before['pending']
                observations.append('requested')
            else:
                assert observations == ['requested', 'round boundary']
                assert durable(journals[0])['control'] == 'running'
                observations.append('resumed request')
            return await super().post(url, json=json)
    backend._client = Engine(_sub_toolcall('read_file', {'path': 'fixture'}), _sub_final('done'),
                             lead=[task_round(), _lead_final()])
    await collect(backend, parent)
    assert observations == ['requested', 'round boundary', 'resumed request']


async def test_stop_is_durable_before_transport_cancellation(parent, journals):
    observations = []
    backend = _backend([], _spec(name='worker'))
    class Engine(_Engine):
        async def post(self, url, json=None):
            assert len(journals) == 1
            journal = journals[0]
            ack = backend.worker_control('agent_stop', journal.run_id)
            assert ack['state'] == 'stopping'
            assert durable(journal)['control'] == 'stopped'
            observations.append('durable stop')
            try:
                await asyncio.Future()
            finally:
                assert durable(journal)['control'] == 'stopped'
                observations.append('cancelled transport')
    backend._client = Engine(lead=[task_round(), _lead_final()])
    await collect(backend, parent)
    assert observations == ['durable stop', 'cancelled transport']
