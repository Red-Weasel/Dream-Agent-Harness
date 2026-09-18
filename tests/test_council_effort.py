"""Native effort reaches provider request options, without live providers."""
import pytest
from dream.core import moe
from dream.core.backends.anthropic import AnthropicBackend
from dream.core.backends.cli_agent import CliAgentBackend, CodexAdapter
from dream.core.providers import get_provider


def test_claude_effort_reaches_sdk_options():
    backend = AnthropicBackend(system_prompt='test', mcp_server={}, preapproved_tool_ids=[],
        agents={}, permission_cb=None, model=None)
    backend.set_effort('max')
    assert backend._build_options().effort == 'max'


@pytest.mark.parametrize('level,native', [
    ('low', 'low'), ('medium', 'medium'), ('high', 'high'), ('xhigh', 'xhigh'),
    ('med', 'medium'), ('max', 'max'), ('ultra', 'ultra'),
])
async def test_codex_model_and_effort_reach_process_arguments(monkeypatch, level, native):
    backend = CliAgentBackend(CodexAdapter(), system_prompt='test', cwd='/tmp', model='chosen')
    backend.set_effort(level)
    captured = []
    async def spawn(*args, **kwargs):
        captured.extend(args)
        raise RuntimeError('fixture stops before process launch')
    monkeypatch.setattr('dream.core.backends.cli_agent.asyncio.create_subprocess_exec', spawn)
    monkeypatch.setattr('dream.core.backends.cli_agent.supervised_command', lambda args: args)
    with pytest.raises(RuntimeError, match='fixture stops'):
        async for _ in backend.ask('test'):
            pass
    assert 'model_reasoning_effort="' + native + '"' in captured
    assert 'chosen' in captured


async def test_council_fanout_passes_exact_advisor_effort(monkeypatch):
    seen = []
    async def consult(key, question, context='', **kwargs):
        seen.append((key, kwargs))
        return 'answer'
    monkeypatch.setattr(moe, 'consult_advisor', consult)
    results = await moe.council(['openai'], 'q', efforts={'openai': 'high'})
    assert seen[0][1]['effort'] == 'high'
    assert results[0]['effort'] == 'high'


async def test_sdk_advisor_receives_effort_without_tools(monkeypatch):
    import claude_agent_sdk as sdk
    captured = []
    async def query(*, prompt, options):
        captured.append(options)
        yield sdk.ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False,
                                num_turns=1, session_id='fixture-sdk')
    monkeypatch.setattr(sdk, 'query', query)
    await moe._consult_anthropic(get_provider('anthropic'), 'q', effort='high')
    assert captured[0].effort == 'high'
    assert captured[0].tools == [] and captured[0].mcp_servers == {}


async def test_http_advisor_effort_reaches_request_payload(monkeypatch):
    seen = []
    async def run(backend, prompt):
        seen.append(backend._base_effort_params())
        return 'answer'
    monkeypatch.setattr(moe, '_run_backend', run)
    await moe._consult_openai(get_provider('openai'), 'q', effort='high')
    assert seen == [{'reasoning_effort': 'high'}]


async def test_sdk_disconnect_does_not_hide_handoff_failure():
    backend = AnthropicBackend(system_prompt='test', mcp_server={}, preapproved_tool_ids=[],
        agents={}, permission_cb=None, model=None)
    class Client:
        async def disconnect(self):
            raise RuntimeError('sdk cleanup failed')
    backend.client = Client()
    with pytest.raises(RuntimeError, match='sdk cleanup failed'):
        await backend.disconnect()


@pytest.mark.parametrize('provider', ['grok', 'gemini'])
def test_unsupported_cli_effort_rejected_without_mutation(provider):
    from dream.core.backends.cli_agent import adapter_for
    backend = CliAgentBackend(adapter_for(provider), system_prompt='test', cwd='/tmp')
    with pytest.raises(ValueError, match='not implemented'):
        backend.set_effort('high')
    assert backend._effort is None
    backend.set_effort(None)
    assert backend._effort is None


def test_invalid_codex_effort_preserves_prior_native_setting():
    backend = CliAgentBackend(CodexAdapter(), system_prompt='test', cwd='/tmp')
    backend.set_effort('low')
    with pytest.raises(ValueError):
        backend.set_effort('invented')
    assert backend._effort == 'low'


def test_engine_rejected_effort_does_not_change_current_setting():
    from dream.core.engine import Engine
    from dream.core.backends.cli_agent import adapter_for
    engine = Engine(provider='grok')
    engine.backend = CliAgentBackend(adapter_for('grok'), system_prompt='test', cwd='/tmp')
    with pytest.raises(ValueError, match='not implemented'):
        engine.set_effort('high')
    assert engine.effort is None
    assert engine.backend._effort is None


def test_codex_known_model_rejects_unsupported_effort(tmp_path, monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    backend = CliAgentBackend(CodexAdapter(), system_prompt='test', cwd='/tmp', model='gpt-5.6-luna')
    backend.set_effort('max')
    with pytest.raises(ValueError, match='not supported'):
        backend.set_effort('ultra')
    assert backend._effort == 'max'
