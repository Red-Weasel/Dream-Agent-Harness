"""Managed HTTP dispatch persists worker authority before external effects."""
import json
from json import loads

import pytest

from dream.core.worker_checkpoints import WorkerJournal, WorkerCheckpointError
from test_local_subagents import _backend, _lead_final, _sub_final, _sub_toolcall, _tool
from test_parent_continuation_http import collect, task_round
from test_worker_contracts import _Engine, _spec
from test_worker_delegations import parent


@pytest.fixture
def journals(monkeypatch):
    opened = []
    original = WorkerJournal.create
    def create(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        opened.append(self)
        return result
    monkeypatch.setattr(WorkerJournal, 'create', create)
    return opened


def durable(journal):
    return json.loads((journal.path / 'ledger.jsonl').read_text().splitlines()[-1])['state']


async def test_worker_transport_observes_durable_request_debt(parent, journals):
    backend = _backend([], _spec(name='worker'))
    observed = []
    class Engine(_Engine):
        async def post(self, url, json=None):
            assert len(journals) == 1, 'Managed child journal must exist before transport.'
            state = durable(journals[0])
            assert state['requests'] == 1
            assert state['pending']['request'] == 1
            assert state['pending']['tools'] == {}
            observed.append(state)
            return await super().post(url, json=json)
    backend._client = Engine(_sub_final('exact child result'), lead=[task_round(), _lead_final()])
    await collect(backend, parent)
    assert len(observed) == 1


async def test_tool_handler_observes_durable_intent_and_complete_round_settles(parent, journals):
    observations = []
    async def read_file(arguments):
        assert len(journals) == 1
        state = durable(journals[0])
        assert state['requests'] == 1 and state['pending'] is not None
        assert len(state['pending']['tools']) == 1
        assert next(iter(state['pending']['tools'].values())) == {'state': 'intent', 'sha256': None}
        observations.append('handler')
        return {'content': [{'type': 'text', 'text': 'fixture observation'}]}
    backend = _backend([_tool('read_file', read_file)], _spec('read_file', name='worker'))
    class Engine(_Engine):
        async def post(self, url, json=None):
            if self.posted:
                state = durable(journals[0])
                assert state['requests'] == 2 and state['pending']['request'] == 2
                assert state['pending']['tools'] == {}
                rows = [loads(line) for line in
                        (journals[0].path / 'ledger.jsonl').read_text().splitlines()]
                assert any(row['event'] == 'settled' and row['state']['pending'] is None
                           and row['state']['requests'] == 1 for row in rows)
                observations.append('next request')
            return await super().post(url, json=json)
    backend._client = Engine(_sub_toolcall('read_file', {'path': 'fixture'}),
                             _sub_final('exact child result'), lead=[task_round(), _lead_final()])
    await collect(backend, parent)
    assert observations == ['handler', 'next request']


async def test_reopened_terminal_journal_preserves_exact_parent_return_and_ack(parent, journals):
    result = 'original child return 🌙\n' + 'unclipped result ' * 1000 + 'END'
    backend = _backend([], _spec(name='worker'))
    backend._client = _Engine(_sub_final(result), lead=[task_round(), _lead_final()])
    await collect(backend, parent)
    assert len(journals) == 1
    binding = durable(journals[0])['binding']
    with WorkerJournal(parent.root, binding['session_id'], journals[0].run_id) as restored:
        state = restored.inspect()
        assert state['outcome'] == 'completed' and state['acknowledged'] is True
        assert state['snapshot']['parent_result'] == {'text': result, 'is_error': False}
        assert state['requests'] == 1 and state['pending'] is None
    assert parent.c.pending_children == {}


async def test_failed_round_settlement_refuses_next_worker_request(parent, journals, monkeypatch):
    async def read_file(arguments):
        return {'content': []}
    backend = _backend([_tool('read_file', read_file)],
                       _spec('read_file', name='worker'))
    calls = []
    def fail(self, *args, **kwargs):
        calls.append(self.run_id)
        raise WorkerCheckpointError('storage_io')
    monkeypatch.setattr(WorkerJournal, 'settle', fail)
    fake = backend._client = _Engine(_sub_toolcall('read_file', {'path': 'fixture'}),
                                    _sub_final('must not execute'), lead=[task_round(), _lead_final()])
    events = await collect(backend, parent)
    assert calls and len(fake.posted) == 1
    assert any(event.kind == 'tool_result' and event.data['is_error'] for event in events)
