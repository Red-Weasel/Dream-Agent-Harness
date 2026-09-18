"""Real Engine/HTTP/file-tool qualification with finite scripted transport.

This checks configured profile plumbing and observed artifacts, not model skill.
Startup is not exercised: disposable store/context and an empty MCP roster are
installed before real registry, system-prompt and Engine.ask preparation.
"""
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace
import asyncio
import json
import os
import socket
import subprocess

import httpx
import pytest

from dream import config
from dream.core import policy
from dream.core.context_budget import account
from dream.core.engine import Engine
from dream.core.providers import Provider
from dream.harness_eval import grade, load_cases
from dream.memory.embeddings import Embedder, Reranker
from dream.memory.store import MemoryStore
from dream.memory.working import WorkingMemory
from dream.tools import native, registry
from dream.tools.context import ToolContext
from test_compaction import _FakeStream, _text_round, _tool_round


CASE = next(case for case in load_cases() if case['id'] == 'coding-boundary')
OLD = 'OPTIONAL_OLD_DISCUSSION ' + 'Previously discussed an unrelated design. ' * 800
SENTINEL = b'Unrelated bytes\x00must stay unchanged\r\n'
PROFILES = {'small': (8192, 1024), 'roomy': (65536, 4096)}


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    """Deny external effects even if a caught exception would hide an attempt."""
    attempts = []
    def blocked(*args, **kwargs):
        attempts.append('live I/O, startup or model loading')
        raise AssertionError(attempts[-1])
    for obj, name in ((socket.socket, 'connect'), (socket.socket, 'connect_ex'),
                      (subprocess.Popen, '__init__'), (os, 'system'),
                      (Embedder, '_ensure'), (Reranker, '_ensure'),
                      (Engine, 'start'), (httpx.AsyncClient, '__init__')):
        monkeypatch.setattr(obj, name, blocked)
    # Preserve pytest's external-settings isolation, replace inherited tuning.
    for key in tuple(os.environ):
        if key.startswith('DREAM_') and key != 'DREAM_EXTENSION_SETTINGS':
            monkeypatch.delenv(key)
    root = tmp_path / 'runtime'
    root.mkdir()
    monkeypatch.setenv('DREAM_ROOT', str(root))
    for name, value in {'ROOT': root, 'DATA_DIR': root / 'data',
                        'DB_PATH': root / 'data/dream.db',
                        'SESSIONS_DIR': root / 'data/sessions',
                        'VAR_DIR': root / 'var', 'LOOP_DIR': root / 'var/loops',
                        'CUSTOM_TOOLS_DIR': root / 'custom',
                        'PLUGINS_DIR': root / 'plugins',
                        'MCP_CONFIG_PATH': root / 'mcp.json'}.items():
        monkeypatch.setattr(config, name, value)
    config.DATA_DIR.mkdir()
    saved = {'version': 1, 'profile': 'lean',
             'overrides': {'context_limit': 32768, 'output_tokens': 2048},
             'models': {f'openai:{name}': {'context_limit': window, 'output_tokens': output}
                        for name, (window, output) in PROFILES.items()}}
    (config.DATA_DIR / 'runtime-settings.json').write_text(json.dumps(saved))
    yield
    assert attempts == []


class Transport:
    """A finite SSE script. Observe payloads; never execute a tool or read a file."""
    def __init__(self, backend, rounds):
        self.backend, self.rounds = backend, rounds
        self.payloads, self.reports = [], []

    def stream(self, method, url, json=None):
        index = len(self.payloads)
        assert method == 'POST' and url.endswith('/chat/completions')
        assert index < len(self.rounds), 'unexpected extra provider request'
        self.payloads.append(deepcopy(json))
        self.reports.append(deepcopy(self.backend.context_report))
        return _FakeStream(self.rounds[index])


def tool_round(name, arguments, number):
    return [line.replace('"c1"', f'"call-{number}"')
            for line in _tool_round(name, json.dumps(arguments))]


def script(control='repair'):
    read = {'path': 'shipping.py'}
    edit = {'path': 'shipping.py', 'old_string': 'total > 50', 'new_string': 'total >= 50'}
    if control == 'false_done':
        return [_text_round('DONE. Fixed the comparison.\n' + CASE['expected'])]
    if control == 'invalid':
        edit['path'] = ['shipping.py']  # Real schema validation rejects this.
    return [tool_round('read_file', read, 1), tool_round('str_replace_edit', edit, 2),
            tool_round('read_file', read, 3), _text_round('DONE.\n' + CASE['expected'])]


@pytest.fixture
async def task_engine(tmp_path):
    engines = []
    async def make(model='small', control='repair', history=True):
        workspace = tmp_path / f'workspace-{len(engines)}'
        workspace.mkdir()
        (workspace / 'shipping.py').write_text(CASE['input'])
        (workspace / 'untouched.bin').write_bytes(SENTINEL)
        permissions = []
        async def permission(name, args):
            permissions.append((name, deepcopy(args)))
            decision, _ = policy.decide(name, args, 'auto', workspace,
                execution_scope=e.execution_scope, execution_capability=e.execution_capability)
            return decision == 'allow'
        e = Engine(provider=Provider('openai', 'Offline fixture', 'openai',
                   base_url='https://fixture.invalid/v1'), model=model, workspace=workspace,
                   can_use_tool=permission, mode_getter=lambda: 'auto')
        e.store = MemoryStore(config.DATA_DIR / f'{e.session_id}.db')
        engines.append(e)
        e.store.start_session(e.session_id)
        e.working = WorkingMemory(e.store, e.session_id)
        e._tool_context = ToolContext(e.store, e.working, None, e.session_id, workspace=workspace)
        e._built_tools = registry.build(extra_tools=native.NATIVE_TOOLS, wrap_tool=e._wrap_tool)
        e._session_tools = {tool.name: tool for tool in e._built_tools['tools']}
        e._mcp = SimpleNamespace(servers=[])
        e.backend = await e._create_backend()
        e._started = True
        e.set_effort('high')
        transport = Transport(e.backend, script(control))
        e.backend._client = transport
        if history:
            e.working.log_turn('user', 'Earlier unrelated design question')
            e.working.log_turn('assistant', OLD)
            e.backend.messages.extend([{'role': 'user', 'content': 'Earlier unrelated design question'},
                                       {'role': 'assistant', 'content': OLD}])
        return e, transport, permissions
    yield make
    for e in engines:
        e.store.close()


async def run_task(e, transport, prompt=None):
    prompt = prompt or CASE['task'] + '\nEdit shipping.py only. Preserve untouched.bin byte for byte.'
    async with asyncio.timeout(15):
        events = [event async for event in e.ask(prompt)]
    calls, pending = [], {}
    for event in events:
        if event.kind == 'tool_use':
            pending[event.data['id']] = event.data
        elif event.kind == 'tool_result':
            use = pending.pop(event.data['id'])
            assert use['name'] == event.data['name']
            calls.append({'name': use['name'], 'arguments': use['input'],
                          'result': {'content': [{'type': 'text', 'text': event.data['content']}],
                                     'is_error': event.data['is_error']}})
    assert not pending
    source = (e.workspace / 'shipping.py').read_text()
    records = {'schema_version': 1, 'origin': 'external-recording', 'run_id': e.session_id,
               'cases': [{'id': 'coding-boundary', 'outputs': {'source': source}, 'tools': calls}]}
    report = grade(records)
    evidence = {'profile': asdict(e.profile), 'model': e.model, 'prompt': prompt,
                'payloads': transport.payloads, 'admission': transport.reports,
                'events': [asdict(event) for event in events], 'records': records, 'grade': report,
                'session_turns': e.store.session_turns(e.session_id),
                'sentinel_unchanged': (e.workspace / 'untouched.bin').read_bytes() == SENTINEL}
    # pytest's disposable tree retains actual HTTP/event/artifact evidence for review.
    (e.workspace.parent / f'{e.workspace.name}-evidence.json').write_text(json.dumps(evidence, indent=2))
    assert evidence['sentinel_unchanged']
    assert {path.name for path in e.workspace.iterdir()} == {'shipping.py', 'untouched.bin'}
    assert report['total'] == 5
    assert all(case['failures'] == ['Missing case record.'] for case in report['cases'][1:])
    assert any(turn['role'] == 'user' and turn['content'] == prompt for turn in evidence['session_turns'])
    return evidence


def assert_admitted(evidence, window, output):
    assert len(evidence['payloads']) == 4
    for payload, observed in zip(evidence['payloads'], evidence['admission'], strict=True):
        assert payload['model'] == evidence['model']
        assert payload['max_tokens'] == output
        assert payload['reasoning_effort'] == 'high'
        assert evidence['prompt'] in '\n'.join(m.get('content') or '' for m in payload['messages'])
        assert {'read_file', 'str_replace_edit'} <= {s['function']['name'] for s in payload['tools']}
        actual = account(payload['messages'], payload['tools'], window, output).as_dict()
        assert {key: observed[key] for key in actual} == actual
        assert actual['admitted'] and actual['remaining'] >= 0
    assert evidence['records']['cases'][0]['outputs']['source'] == CASE['expected']
    calls = evidence['records']['cases'][0]['tools']
    assert [call['name'] for call in calls] == ['read_file', 'str_replace_edit', 'read_file']
    assert not any(call['result']['is_error'] for call in calls)
    assert 'total > 50' in calls[0]['result']['content'][0]['text']
    assert 'total >= 50' in calls[2]['result']['content'][0]['text']
    assert evidence['grade']['passed'] == 1 and evidence['grade']['cases'][0]['passed']
    assert any(event['kind'] == 'result' and event['data']['subtype'] == 'success'
               and not event['data']['is_error'] for event in evidence['events'])


@pytest.mark.parametrize('model', ['small', 'roomy'])
async def test_engine_completes_same_task_with_actual_profile_budget(task_engine, model):
    e, transport, permissions = await task_engine(model)
    evidence = await run_task(e, transport)
    assert_admitted(evidence, *PROFILES[model])
    first_history = '\n'.join(m.get('content') or '' for m in transport.payloads[0]['messages'])
    assert (OLD in first_history) is (model == 'roomy')
    assert [name for name, _ in permissions] == ['str_replace_edit']


async def test_environment_override_wins_exact_saved_profile(task_engine, monkeypatch):
    monkeypatch.setenv('DREAM_CONTEXT_WINDOW', '16384')
    monkeypatch.setenv('DREAM_MAX_TOKENS', '768')
    e, transport, _ = await task_engine('small')
    evidence = await run_task(e, transport)
    assert_admitted(evidence, 16384, 768)
    assert e.profile.context_limit == 16384 and e.profile.output_tokens == 768


async def test_mandatory_overflow_refuses_before_transport_or_tools(task_engine):
    e, transport, permissions = await task_engine(history=False)
    prompt = CASE['task'] + '\nMANDATORY DETAIL ' + 'Keep every constraint. ' * 5000
    evidence = await run_task(e, transport, prompt)
    assert transport.payloads == [] and permissions == []
    assert evidence['records']['cases'][0]['tools'] == []
    assert evidence['records']['cases'][0]['outputs']['source'] == CASE['input']
    assert evidence['grade']['passed'] == 0
    assert any(event['kind'] == 'error' and 'no request was sent' in str(event['data'])
               for event in evidence['events'])
    assert any(event['kind'] == 'result' and event['data']['is_error'] for event in evidence['events'])
    assert any(prompt in (message.get('content') or '') for message in e.backend.messages
               if message['role'] == 'user')


@pytest.mark.parametrize('control', ['invalid', 'false_done'])
async def test_claimed_done_cannot_replace_artifact_and_tool_evidence(task_engine, control):
    e, transport, permissions = await task_engine(control=control, history=False)
    evidence = await run_task(e, transport)
    assert evidence['records']['cases'][0]['outputs']['source'] == CASE['input']
    assert evidence['grade']['passed'] == 0 and not evidence['grade']['cases'][0]['passed']
    assert any(event['kind'] == 'assistant_done' and 'DONE' in event['data'] for event in evidence['events'])
    assert permissions == []
    calls = evidence['records']['cases'][0]['tools']
    if control == 'invalid':
        assert len(calls) == 3
        assert calls[1]['result']['is_error']
        assert 'Invalid arguments' in calls[1]['result']['content'][0]['text']
        assert 'total > 50' in calls[2]['result']['content'][0]['text']
    else:
        assert len(transport.payloads) == 1 and calls == []
    assert 'Missing successful edit evidence.' in evidence['grade']['cases'][0]['failures']
