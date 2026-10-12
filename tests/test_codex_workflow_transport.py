"""DREAM-284: model-free Codex transport, provenance and failure regressions."""
import json
import sys
import tomllib

import pytest

from dream.core.backends.cli_agent import CliAgentBackend, CodexAdapter
from dream.core.instructions import project_instructions


@pytest.mark.parametrize('failure', [
    {'result': {'isError': True, 'content': [{'type': 'text', 'text': 'Target unavailable'}]}},
    {'result': {'is_error': True, 'content': [{'type': 'text', 'text': 'Target unavailable'}]}},
    {'status': 'failed'},
])
def test_mcp_failure_and_server_survive_translation(failure):
    item = {'type': 'mcp_tool_call', 'id': 'call-1', 'server': 'dream',
            'tool': 'session_send', 'arguments': {'target': 'fixture-node'},
            'status': 'completed', **failure}
    adapter = CodexAdapter()
    start, = adapter.translate({'type': 'item.started', 'item': item}, {})
    end, = adapter.translate({'type': 'item.completed', 'item': item}, {})
    assert end.data['is_error'] is True
    assert start.data['server'] == end.data['server'] == 'dream'
    assert start.data['input'] == {'target': 'fixture-node'}


def test_mcp_overrides_roundtrip_special_characters():
    cfg = {'name': 'dream', 'command': '/fixture/quote"/🌲/python',
           'args': ['-m', 'dream.mcp'],
           'env': {'DREAM_PARENT_BRIDGE_FILE': '/fixture/quote"\\path/session.json',
                   'DREAM_SESSION_ID': 'fixture-session'}}
    argv = CodexAdapter().argv('hello', cwd='/fixture', resume_id=None,
                               sandbox='read-only', mcp_config=cfg)
    parsed = tomllib.loads('\n'.join(argv[i + 1] for i, arg in enumerate(argv) if arg == '-c'))
    server = parsed['mcp_servers']['dream']
    assert server['command'] == cfg['command']
    assert server['args'] == cfg['args']
    assert server['env'] == cfg['env']


@pytest.mark.asyncio
async def test_workspace_instructions_once_large_stdin_exact_resume(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    other = tmp_path / 'other'; other.mkdir()
    (workspace / 'DREAM.md').write_text('WORKSPACE_RULE_CANARY')
    (other / 'DREAM.md').write_text('UNRELATED_RULE_CANARY')
    system = str(project_instructions(workspace)) + '\n' + ('fixture-wake ' * 20000)
    fixture = tmp_path / 'codex'
    fixture.write_text('#!' + sys.executable + '\n' + '''
import json, pathlib, sys
root = pathlib.Path(__file__).parent
calls = sorted(root.glob('capture-*.json'))
payload = sys.stdin.read() if sys.argv[-1] == '-' else sys.argv[-1]
(root / ('capture-%s.json' % len(calls))).write_text(json.dumps({'argv':sys.argv, 'payload':payload}))
print(json.dumps({'type':'thread.started','thread_id':'fixture-exact-thread'}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':1}}))
''')
    fixture.chmod(0o700)
    adapter = CodexAdapter(); monkeypatch.setattr(adapter, 'cmd', str(fixture))
    backend = CliAgentBackend(adapter, system_prompt=system, cwd=str(workspace))
    for prompt in ('hello', '--literal-second-message'):
        events = [event async for event in backend.ask(prompt)]
        assert events[-1].kind == 'result' and not events[-1].data['is_error']
    first, second = [json.loads(path.read_text()) for path in sorted(tmp_path.glob('capture-*.json'))]
    assert first['payload'] == system + '\n\nhello'
    assert first['payload'].count('WORKSPACE_RULE_CANARY') == 1
    assert 'UNRELATED_RULE_CANARY' not in first['payload']
    assert first['argv'][-1] == second['argv'][-1] == '-'
    assert not any('WORKSPACE_RULE_CANARY' in arg for arg in first['argv'])
    assert first['argv'][first['argv'].index('-C') + 1] == str(workspace)
    assert second['payload'] == '--literal-second-message'
    assert second['argv'][-3:] == ['resume', 'fixture-exact-thread', '-']
    await backend.disconnect()


def test_cumulative_usage_resumed_turns_cache_and_context_are_distinct():
    adapter = CodexAdapter()
    adapter.translate({'type': 'thread.started', 'thread_id': 'same'}, {})
    def complete(raw):
        result, = adapter.translate({'type': 'turn.completed', 'usage': raw}, {})
        adapter.commit_result(result)
        return result.data
    first = complete({'input_tokens': 403513, 'cached_input_tokens': 350000, 'output_tokens': 500})
    adapter.translate({'type': 'thread.started', 'thread_id': 'same'}, {})
    second = complete({'input_tokens': 443285, 'cached_input_tokens': 388000, 'output_tokens': 527})
    assert first['usage']['prompt_tokens'] == 403513
    assert second['usage']['prompt_tokens'] == 39772
    assert second['usage']['completion_tokens'] == 27
    assert second['usage']['cached_tokens'] == 38000
    assert second['usage_scope'] == 'turn' and second['context_tokens'] is None
    assert second['provider_usage']['input_tokens'] == 443285
    adapter.translate({'type': 'thread.started', 'thread_id': 'different'}, {})
    assert complete({'input_tokens': 10})['usage']['prompt_tokens'] == 10


def test_counter_reset_does_not_invent_negative_or_full_session_usage():
    adapter = CodexAdapter()
    first, = adapter.translate({'type': 'turn.completed', 'usage': {'input_tokens': 900}}, {})
    adapter.commit_result(first)
    result, = adapter.translate({'type': 'turn.completed', 'usage': {'input_tokens': 100}}, {})
    assert result.data['usage']['prompt_tokens'] is None
    assert result.data['usage_counter_reset'] is True
    adapter.commit_result(result)
    result, = adapter.translate({'type': 'turn.completed', 'usage': {'input_tokens': 150}}, {})
    assert result.data['usage']['prompt_tokens'] == 50


def test_duplicate_terminal_record_does_not_erase_observed_usage():
    adapter, state = CodexAdapter(), {}
    record = {'type': 'turn.completed', 'usage': {'input_tokens': 10}}
    first, = adapter.translate(record, state)
    repeated, = adapter.translate(record, state)
    assert repeated.data['usage'] == first.data['usage']


def test_translated_cache_survives_runtime_meter_across_resume():
    from types import SimpleNamespace
    from dream.telemetry.runtime import RunMeter
    adapter = CodexAdapter()
    meter = RunMeter('fixture', 1, SimpleNamespace())
    for raw in ({'input_tokens': 100, 'cached_input_tokens': 80, 'output_tokens': 2},
                {'input_tokens': 125, 'cached_input_tokens': 100, 'output_tokens': 5}):
        result, = adapter.translate({'type': 'turn.completed', 'usage': raw}, {})
        meter.usage(result.data['usage'])
        adapter.commit_result(result)
    assert (meter.prompt_tokens, meter.cached_tokens, meter.output_tokens) == (125, 100, 5)
