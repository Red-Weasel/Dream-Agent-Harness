"""DREAM-140: the Claude advisor and the SDK reviewer run like Claude Code in VS Code (the
owner's settings, Claude Code's preset and native tools, a permission mode mapped from
Dream's), by the owner's decision. The prompt optimizer keeps DREAM-137's posture. Fake SDK
streams only; no live Claude call is made and nothing is written to the owner's Claude home."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from claude_agent_sdk import (AssistantMessage, ResultMessage, TextBlock, ToolResultBlock,
                              ToolUseBlock, UserMessage)

from dream import config
from dream.core import moe
from dream.core.backends import anthropic
from dream.core.evaluator import ReviewSettings, ScopedReader, collect_review, review_backend
from dream.core.providers import get_provider
from dream.tools import context, moe_tools

_SERVER = {'type': 'sdk', 'name': config.MCP_SERVER_NAME, 'instance': object()}
_MODES = {'plan': 'plan', 'ask': 'plan', 'accept-edits': 'acceptEdits', 'auto': 'bypassPermissions'}
_PRESET_TOOLS = {'type': 'preset', 'preset': 'claude_code'}
_NATIVE = ('Bash', 'WebSearch', 'WebFetch', 'Read', 'Write', 'Edit', 'Glob', 'Grep')
_SESSION_STATE = ('update_plan', 'update_todos', 'set_project_title', 'project_note', 'skill_save',
                  'skill_patch', 'task_add', 'forget', 'memory_delete', 'memory_move')
_OWNER_FACING = ('questions_v2', 'ask_user_input', 'show_html', 'done', 'see', 'suggest_skills',
                 'suggest_plugin_install', 'visualize_show_widget', 'chart_display_v0')
_ISOLATION = "none: runs like Claude Code (owner's settings)"


def _session():
    """A bound session carrying the main path's tool bundle, as the engine sets it."""
    bundle = {'server': _SERVER, 'exempt_tool_ids': [config.tool_id('recall')],
              'execution': lambda: (None, None)}
    return SimpleNamespace(claude_tools=bundle, runtime_meter=None)


def _result(**extra):
    return ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False,
                         num_turns=3, session_id='fixture-sdk', terminal_reason='completed', **extra)


async def _advisor_options(tmp_path, monkeypatch, mode, **kwargs):
    seen = []
    async def query(**kw):
        seen.append(kw['options'])
        yield AssistantMessage(content=[TextBlock(text='advice')], model='fixture-sdk')
        yield _result()
    monkeypatch.setattr('claude_agent_sdk.query', query)
    with context.bind_context(_session()):
        answer = await moe.consult_advisor('anthropic', 'q', cwd=str(tmp_path), mode=mode, timeout=5, **kwargs)
    assert answer == 'advice'
    return seen[0]


def _reviewer(tmp_path, mode, query):
    settings = ReviewSettings(get_provider('anthropic'), 'fixture-sdk', mode=mode)
    return review_backend(settings, ScopedReader(tmp_path).tools(), 'review system', tmp_path, sdk_query=query)


async def _reviewer_options(tmp_path, mode):
    seen = []
    async def query(**kw):
        seen.append(kw['options'])
        yield AssistantMessage(content=[TextBlock(text='VERDICT: PASS\nGAPS: none')], model='fixture-sdk')
        yield _result()
    backend = _reviewer(tmp_path, mode, query)
    with context.bind_context(_session()):
        await collect_review(backend, 'q', 5)
    return seen[0], backend


async def _hook(options, tool, args=None):
    """What Dream's PreToolUse guard says about one tool call; None = no decision (Claude Code's own)."""
    matcher, = options.hooks['PreToolUse']
    assert matcher.matcher is None  # every tool
    out = await matcher.hooks[0]({'hook_event_name': 'PreToolUse', 'tool_name': tool, 'tool_input': args or {}},
                                 None, {'signal': None})
    specific = out.get('hookSpecificOutput') if out else None
    if not specific:
        return None
    assert specific['hookEventName'] == 'PreToolUse'
    return specific['permissionDecision'], specific['permissionDecisionReason']


def _assert_claude_code_posture(options, tmp_path, mode, append):
    assert options.setting_sources == ['user', 'project', 'local']
    assert options.settings is None  # the owner's hooks are not switched off
    assert options.system_prompt == {'type': 'preset', 'preset': 'claude_code', 'append': append}
    assert options.tools == _PRESET_TOOLS
    assert options.permission_mode == _MODES.get(mode, 'plan')
    assert options.cwd == str(tmp_path) and options.max_turns is None
    assert options.strict_mcp_config is False  # the owner's MCP servers load
    # Dream's own tool server is NOT attached: without Dream's policy callback its tools
    # would run ungated under bypassPermissions.
    assert config.MCP_SERVER_NAME not in options.mcp_servers
    assert not any(t.startswith(f'mcp__{config.MCP_SERVER_NAME}__') for t in options.allowed_tools)
    for tool in _NATIVE:
        assert tool not in options.disallowed_tools, tool
    assert {config.tool_id('consult'), config.tool_id('council')} <= set(options.disallowed_tools)
    assert options.can_use_tool is not None
    assert options.agents is None  # the owner's own agents, from their settings


@pytest.mark.parametrize('mode', ['plan', 'ask', 'accept-edits', 'auto'])
async def test_advisor_runs_with_claude_code_posture(mode, tmp_path, monkeypatch):
    options = await _advisor_options(tmp_path, monkeypatch, mode, model='fixture-model', effort='high')
    _assert_claude_code_posture(options, tmp_path, mode, moe.ADVISOR_SYSTEM)
    assert options.model == 'fixture-model' and options.effort == 'high'
    assert options.mcp_servers == {} and options.allowed_tools == []


async def test_no_mode_runs_as_ask_which_is_plan(tmp_path, monkeypatch):
    options = await _advisor_options(tmp_path, monkeypatch, None)
    assert options.permission_mode == 'plan'
    row = anthropic.claude_code_provenance(str(tmp_path), None)
    assert row['permission_mode'] == 'ask' and row['sdk_permission_mode'] == 'plan'


@pytest.mark.parametrize('mode', ['plan', 'ask'])
async def test_plan_and_ask_are_read_only(mode, tmp_path, monkeypatch):
    options = await _advisor_options(tmp_path, monkeypatch, mode)
    for tool in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit'):
        decision, reason = await _hook(options, tool, {'file_path': str(tmp_path / 'a')})
        assert decision == 'deny' and 'read-only' in reason, tool
    for tool in ('Read', 'Glob', 'Grep', 'WebSearch', 'WebFetch', 'Bash'):
        assert await _hook(options, tool) is None, tool  # Claude Code's plan mode decides
    # Anything Claude Code would prompt the owner for is refused: nobody can answer.
    refused = await options.can_use_tool('Bash', {'command': 'touch x'}, None)
    assert refused.behavior == 'deny' and 'cannot ask the owner' in refused.message


@pytest.mark.parametrize('mode', ['accept-edits', 'auto'])
async def test_edit_modes_leave_native_tools_to_claude_code(mode, tmp_path, monkeypatch):
    options = await _advisor_options(tmp_path, monkeypatch, mode)
    for tool in _NATIVE + ('MultiEdit', 'NotebookEdit'):
        assert await _hook(options, tool, {'file_path': str(tmp_path / 'a')}) is None, tool
    # A prompt Claude Code would still show (acceptEdits: a shell command) is refused.
    refused = await options.can_use_tool('Bash', {'command': 'rm -rf build'}, None)
    assert refused.behavior == 'deny' and 'cannot ask the owner' in refused.message


@pytest.mark.parametrize('mode', ['plan', 'ask', 'accept-edits', 'auto'])
async def test_recursion_session_state_and_owner_facing_refusals_hold(mode, tmp_path, monkeypatch):
    options = await _advisor_options(tmp_path, monkeypatch, mode)
    # On Dream's server name and on any other server (a Dream bridge in the owner's Claude config).
    for server in (config.MCP_SERVER_NAME, 'dream-bridge'):
        for name in ('consult', 'council'):
            decision, reason = await _hook(options, f'mcp__{server}__{name}')
            assert decision == 'deny' and 'Council' in reason, name
        for name in _SESSION_STATE:
            decision, reason = await _hook(options, f'mcp__{server}__{name}')
            assert decision == 'deny' and 'main session' in reason, name
        for name in _OWNER_FACING:
            decision, reason = await _hook(options, f'mcp__{server}__{name}')
            assert decision == 'deny' and 'owner' in reason, name
        remember = await _hook(options, f'mcp__{server}__remember', {'content': 'x'})
        assert (remember is None) == (mode in ('accept-edits', 'auto'))
    # The callback gives the same reasons when the CLI asks it instead.
    refused = await options.can_use_tool(config.tool_id('council'), {}, None)
    assert refused.behavior == 'deny' and 'Council' in refused.message
    # Native tools that share a short name are not caught.
    assert await _hook(options, 'Read') is None


async def test_advisor_multi_turn_with_tool_events(tmp_path, monkeypatch):
    seen = []
    async def query(**kwargs):
        seen.append(kwargs['options'])
        yield AssistantMessage(content=[TextBlock(text='Looking it up.'),
                                        ToolUseBlock(id='t1', name='WebSearch', input={'query': 'q'})],
                               model='fixture-sdk')
        yield UserMessage(content=[ToolResultBlock(tool_use_id='t1', content='results', is_error=False)])
        yield AssistantMessage(content=[TextBlock(text='nested sub-agent chatter')], model='fixture-sdk',
                               parent_tool_use_id='t1')
        yield AssistantMessage(content=[ToolUseBlock(id='t2', name='Bash', input={'command': 'ls'})],
                               model='fixture-sdk')
        yield UserMessage(content=[ToolResultBlock(tool_use_id='t2', content='a.txt', is_error=False)])
        yield AssistantMessage(content=[TextBlock(text='Final advice.')], model='fixture-sdk')
        yield _result(usage={'input_tokens': 5, 'output_tokens': 2})
    monkeypatch.setattr('claude_agent_sdk.query', query)
    with context.bind_context(_session()):
        answer = await moe.consult_advisor('anthropic', 'q', cwd=str(tmp_path), mode='auto', timeout=5)
    assert answer == 'Looking it up.\n\nFinal advice.'
    assert seen[0].permission_mode == 'bypassPermissions'


class _HangingStream:
    def __init__(self):
        self.closed = False
        self.started = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.started.is_set():
            self.started.set()
            return AssistantMessage(content=[ToolUseBlock(id='t1', name='Bash', input={'command': 'sleep 99'})],
                                    model='fixture-sdk')
        await asyncio.sleep(3600)

    async def aclose(self):
        self.closed = True


async def test_timeout_and_cancel_close_the_stream(tmp_path, monkeypatch):
    stream = _HangingStream()
    monkeypatch.setattr('claude_agent_sdk.query', lambda **kwargs: stream)
    answer = await moe.consult_advisor('anthropic', 'q', cwd=str(tmp_path), mode='auto', timeout=0.2)
    assert 'TimeoutError' in answer and stream.closed
    stream = _HangingStream()
    monkeypatch.setattr('claude_agent_sdk.query', lambda **kwargs: stream)
    task = asyncio.create_task(moe._consult_anthropic(get_provider('anthropic'), 'q', cwd=str(tmp_path), mode='auto'))
    await asyncio.wait_for(stream.started.wait(), 2)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed


async def test_council_records_claude_code_provenance(tmp_path, monkeypatch):
    async def advisor(provider, prompt, **kwargs):
        return 'advice'
    monkeypatch.setattr(moe, '_consult_anthropic', advisor)
    row, = await moe.council(['anthropic'], 'q', cwd=str(tmp_path), mode='auto')
    provenance = row['isolation']
    assert provenance['isolation'] == _ISOLATION
    assert provenance['permission_mode'] == 'auto' and provenance['sdk_permission_mode'] == 'bypassPermissions'
    assert provenance['cwd'] == str(tmp_path)
    assert 'settings' in provenance['configuration'] and 'refused' in provenance['without_asking']


async def test_consult_tool_says_how_claude_ran(tmp_path, monkeypatch):
    async def advisor(provider, prompt, **kwargs):
        return 'advice'
    monkeypatch.setattr(moe, '_consult_anthropic', advisor)
    session = SimpleNamespace(moe_config=moe.MoeConfig('machx', ['anthropic']), workspace=tmp_path,
                              mode_getter=lambda: 'plan')
    with context.bind_context(session):
        result = await moe_tools.consult.handler({'advisor': 'anthropic', 'question': 'q'})
    assert 'runs like Claude Code' in result['content'][0]['text']


@pytest.mark.parametrize('mode', ['plan', 'ask', 'accept-edits', 'auto'])
async def test_sdk_reviewer_runs_with_claude_code_posture(mode, tmp_path):
    options, backend = await _reviewer_options(tmp_path, mode)
    _assert_claude_code_posture(options, tmp_path, mode, 'review system')
    # The bound review reader stays attached and pre-approved (the loop's changed-file check).
    assert set(options.mcp_servers) == {'review'}
    assert options.allowed_tools == ['mcp__review__read_file', 'mcp__review__list_files']
    assert await _hook(options, 'mcp__review__read_file', {'path': 'x'}) is None
    decision, _ = await _hook(options, config.tool_id('update_plan'))
    assert decision == 'deny'
    assert backend.provenance['isolation'] == _ISOLATION
    assert backend.provenance['sdk_permission_mode'] == _MODES[mode]


async def test_prompt_optimizer_keeps_dream137_posture(tmp_path, monkeypatch):
    from dream import prompt_optimizer
    seen = []
    async def query(**kwargs):
        seen.append(kwargs['options'])
        yield AssistantMessage(content=[TextBlock(text=json.dumps({'prompt': 'GOAL\nx', 'questions': [], 'notes': []}))],
                               model='fixture-sdk')
        yield _result()
    real = prompt_optimizer.review_backend
    monkeypatch.setattr(prompt_optimizer, 'review_backend',
                        lambda settings, tools, system, cwd: real(settings, tools, system, cwd, sdk_query=query))
    with context.bind_context(_session()):
        await prompt_optimizer.optimize({'draft': 'Summarise the notes'}, [], tmp_path,
                                        ReviewSettings(get_provider('anthropic'), 'fixture-sdk', mode='auto'))
    options = seen[0]
    assert options.setting_sources == [] and json.loads(options.settings) == {'disableAllHooks': True}
    assert options.permission_mode == 'default' and options.system_prompt == prompt_optimizer._SYSTEM
    assert options.mcp_servers[config.MCP_SERVER_NAME] is _SERVER
    assert (await options.can_use_tool('Write', {'file_path': str(tmp_path / 'a')}, None)).behavior == 'deny'
