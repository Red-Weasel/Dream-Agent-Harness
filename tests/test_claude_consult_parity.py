"""DREAM-137: the Claude advisor, the SDK reviewer and the prompt optimizer's Claude path
run like the main-model Claude path (AnthropicBackend). Fake SDK streams only; no live
Claude call is made and nothing is written to the owner's Claude home."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from claude_agent_sdk import (AssistantMessage, ResultMessage, TextBlock, ToolResultBlock,
                              ToolUseBlock, UserMessage)

from dream import config
from dream.core import moe
from dream.core.backends import anthropic
from dream.core.backends.anthropic import SAFE_BUILTINS, AnthropicBackend
from dream.core.evaluator import ReviewSettings, ScopedReader, collect_review, review_backend
from dream.core.providers import get_provider
from dream.core.subagents import subagents
from dream.tools import context, moe_tools
from dream.tools.context import ToolContext

_SERVER = {'type': 'sdk', 'name': config.MCP_SERVER_NAME, 'instance': object()}
_EXEMPT = [config.tool_id('recall'), config.tool_id('web_search'), config.tool_id('media_read')]
_RECURSION = {config.tool_id('consult'), config.tool_id('council')}


def _session(**extra):
    """A bound session carrying the main path's tool bundle, as the engine sets it."""
    bundle = {'server': _SERVER, 'exempt_tool_ids': list(_EXEMPT),
              'execution': lambda: (None, None)}
    return SimpleNamespace(claude_tools=bundle, runtime_meter=None, **extra)


def _main_options(workspace, model='fixture-model', effort=None):
    backend = AnthropicBackend(system_prompt='main', mcp_server=_SERVER, preapproved_tool_ids=list(_EXEMPT),
                               agents=subagents(), permission_cb=None, model=model, cwd=str(workspace))
    backend.set_effort(effort)
    return backend._build_options()


def _result(**extra):
    return ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False,
                         num_turns=3, session_id='fixture-sdk', terminal_reason='completed', **extra)


@pytest.mark.parametrize('mode', ['plan', 'ask', 'accept-edits', 'auto'])
def test_consult_options_equal_the_main_claude_path(mode, tmp_path):
    main = _main_options(tmp_path, effort='high')
    with context.bind_context(_session()):
        options = anthropic.consult_options(system_prompt='advisor', cwd=str(tmp_path), mode=mode,
                                            model='fixture-model', effort='high')
    for name in ('setting_sources', 'settings', 'strict_mcp_config', 'allowed_tools', 'permission_mode',
                 'cwd', 'model', 'effort', 'env', 'include_partial_messages', 'tools', 'max_turns',
                 'mcp_servers'):
        assert getattr(options, name) == getattr(main, name), name
    assert options.setting_sources == [] and json.loads(options.settings) == {'disableAllHooks': True}
    assert options.permission_mode == 'default' and options.max_turns is None and options.tools is None
    assert options.mcp_servers[config.MCP_SERVER_NAME] is _SERVER
    assert set(options.agents) == set(main.agents)
    # The only difference in tools: a consult cannot convene the Council again.
    assert set(options.disallowed_tools) == set(main.disallowed_tools) | _RECURSION
    assert set(SAFE_BUILTINS) <= set(options.allowed_tools)
    assert options.can_use_tool is not None and options.system_prompt == 'advisor'


def test_main_path_options_are_unchanged_by_the_shared_builder(tmp_path):
    options = _main_options(tmp_path)
    assert options.disallowed_tools == ['WebSearch', 'WebFetch', 'Bash']
    assert options.allowed_tools == _EXEMPT + SAFE_BUILTINS
    assert options.mcp_servers == {config.MCP_SERVER_NAME: _SERVER}
    assert options.env == {'CLAUDE_AGENT_SDK_CLIENT_APP': 'dream/0.1.0', 'ENABLE_TOOL_SEARCH': 'auto:100'}


async def _verdict(options, name, args):
    return (await options.can_use_tool(name, args, None)).behavior


@pytest.mark.parametrize('mode', ['plan', 'ask'])
async def test_plan_and_ask_consults_are_read_only(mode, tmp_path):
    with context.bind_context(_session()):
        options = anthropic.consult_options(system_prompt='s', cwd=str(tmp_path), mode=mode, model=None, effort=None)
    target = str(tmp_path / 'a.txt')
    assert await _verdict(options, 'Read', {'file_path': target}) == 'allow'
    assert await _verdict(options, config.tool_id('web_search'), {'query': 'q'}) == 'allow'
    assert await _verdict(options, 'Write', {'file_path': target, 'content': 'x'}) == 'deny'
    assert await _verdict(options, 'Edit', {'file_path': target}) == 'deny'
    assert await _verdict(options, config.tool_id('write_file'), {'path': target}) == 'deny'
    assert await _verdict(options, config.tool_id('run_bash'), {'command': 'ls'}) == 'deny'
    assert await _verdict(options, config.tool_id('delete_file'), {'paths': [target]}) == 'deny'


@pytest.mark.parametrize('mode', ['accept-edits', 'auto'])
async def test_edit_mode_consults_write_the_workspace_but_never_outside_it(mode, tmp_path):
    with context.bind_context(_session()):
        options = anthropic.consult_options(system_prompt='s', cwd=str(tmp_path), mode=mode, model=None, effort=None)
    assert await _verdict(options, 'Write', {'file_path': str(tmp_path / 'a.txt')}) == 'allow'
    assert await _verdict(options, 'Edit', {'file_path': str(tmp_path / 'a.txt')}) == 'allow'
    denied = await options.can_use_tool('Write', {'file_path': '/etc/fixture-outside'}, None)
    assert denied.behavior == 'deny' and 'outside workspace' in denied.message
    # What the main path would ask the owner about is refused, since a consult has nobody to ask.
    assert 'cannot ask' in denied.message
    assert await _verdict(options, config.tool_id('delete_file'), {'paths': [str(tmp_path / 'a.txt')]}) == 'deny'


async def test_no_mode_runs_as_ask_and_no_session_means_builtins_only(tmp_path):
    with context.bind_context(SimpleNamespace(runtime_meter=None)):
        options = anthropic.consult_options(system_prompt='s', cwd=str(tmp_path), mode=None, model=None, effort=None)
    assert options.mcp_servers == {}
    assert await _verdict(options, 'Write', {'file_path': str(tmp_path / 'a')}) == 'deny'
    provenance = anthropic.consult_provenance(str(tmp_path), None, dream_tools=False)
    assert provenance['permission_mode'] == 'ask'
    assert 'no Dream session' in provenance['tool_scope']


def test_provenance_names_the_main_claude_path(tmp_path):
    provenance = anthropic.consult_provenance(str(tmp_path), 'auto', dream_tools=True)
    assert provenance['isolation'] == 'none: runs like the main-model Claude path'
    assert provenance['cwd'] == str(tmp_path) and provenance['permission_mode'] == 'auto'
    assert provenance['sdk_permission_mode'] == 'default'
    assert 'refused' in provenance['without_asking']
    assert 'consult' in provenance['tool_scope'] and 'council' in provenance['tool_scope']


async def test_advisor_multi_turn_consult_with_tool_events(tmp_path, monkeypatch):
    seen = []
    async def query(**kwargs):
        options = kwargs['options']
        seen.append(options)
        target = tmp_path / 'note.txt'
        yield AssistantMessage(content=[TextBlock(text='Checking the file.'),
                                        ToolUseBlock(id='t1', name='Write', input={'file_path': str(target)})],
                               model='fixture-sdk')
        # The SDK consults the permission callback before running the tool.
        assert (await options.can_use_tool('Write', {'file_path': str(target)}, None)).behavior == 'allow'
        target.write_text('written by the fixture tool')
        yield UserMessage(content=[ToolResultBlock(tool_use_id='t1', content='ok', is_error=False)])
        yield AssistantMessage(content=[TextBlock(text='nested sub-agent chatter')], model='fixture-sdk',
                               parent_tool_use_id='t1')
        yield AssistantMessage(content=[TextBlock(text='Final advice.')], model='fixture-sdk')
        yield _result(usage={'input_tokens': 5, 'output_tokens': 2})
    monkeypatch.setattr('claude_agent_sdk.query', query)
    with context.bind_context(_session()):
        answer = await moe.consult_advisor('anthropic', 'q', cwd=str(tmp_path), mode='accept-edits', timeout=5)
    assert answer == 'Checking the file.\n\nFinal advice.'
    assert (tmp_path / 'note.txt').read_text() == 'written by the fixture tool'
    assert seen[0].cwd == str(tmp_path) and seen[0].max_turns is None
    assert seen[0].system_prompt == moe.ADVISOR_SYSTEM


class _HangingStream:
    def __init__(self):
        self.closed = False
        self.started = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.started.is_set():
            self.started.set()
            return AssistantMessage(content=[ToolUseBlock(id='t1', name='Read', input={'file_path': 'x'})],
                                    model='fixture-sdk')
        await asyncio.sleep(3600)

    async def aclose(self):
        self.closed = True


async def test_advisor_timeout_closes_the_sdk_stream(tmp_path, monkeypatch):
    stream = _HangingStream()
    monkeypatch.setattr('claude_agent_sdk.query', lambda **kwargs: stream)
    with context.bind_context(_session()):
        answer = await moe.consult_advisor('anthropic', 'q', cwd=str(tmp_path), mode='auto', timeout=0.2)
    assert 'unavailable' in answer and 'TimeoutError' in answer
    assert stream.closed


async def test_advisor_cancellation_closes_the_sdk_stream(tmp_path, monkeypatch):
    stream = _HangingStream()
    monkeypatch.setattr('claude_agent_sdk.query', lambda **kwargs: stream)
    with context.bind_context(_session()):
        task = asyncio.create_task(moe._consult_anthropic(get_provider('anthropic'), 'q', cwd=str(tmp_path),
                                                          mode='auto'))
        await asyncio.wait_for(stream.started.wait(), 2)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stream.closed


async def test_council_threads_mode_and_records_claude_provenance(tmp_path, monkeypatch):
    calls = []
    async def advisor(provider, prompt, **kwargs):
        calls.append(kwargs)
        return 'advice'
    monkeypatch.setattr(moe, '_consult_anthropic', advisor)
    row, = await moe.council(['anthropic'], 'q', cwd=str(tmp_path), mode='plan')
    assert calls[0]['mode'] == 'plan' and calls[0]['cwd'] == str(tmp_path)
    assert row['isolation']['isolation'] == 'none: runs like the main-model Claude path'
    assert row['isolation']['permission_mode'] == 'plan'


async def test_consult_tool_passes_mode_and_says_how_claude_ran(tmp_path, monkeypatch):
    calls = []
    async def advisor(provider, prompt, **kwargs):
        calls.append(kwargs)
        return 'advice'
    monkeypatch.setattr(moe, '_consult_anthropic', advisor)
    cfg = moe.MoeConfig('machx', ['anthropic'])
    session = SimpleNamespace(moe_config=cfg, workspace=tmp_path, mode_getter=lambda: 'auto')
    with context.bind_context(session):
        result = await moe_tools.consult.handler({'advisor': 'anthropic', 'question': 'q'})
    text = result['content'][0]['text']
    assert calls[0]['mode'] == 'auto'
    assert 'runs like the main Claude path' in text and 'advice' in text


def test_tool_context_carries_the_claude_tool_bundle():
    assert ToolContext.__dataclass_fields__['claude_tools'].default is None


def _reviewer(tmp_path, mode, query, tools=None):
    settings = ReviewSettings(get_provider('anthropic'), 'fixture-sdk', mode=mode)
    return review_backend(settings, ScopedReader(tmp_path).tools() if tools is None else tools,
                          'review system', tmp_path, sdk_query=query)


@pytest.mark.parametrize('mode', ['plan', 'ask', 'accept-edits', 'auto'])
async def test_sdk_reviewer_runs_like_the_main_path(mode, tmp_path):
    seen = []
    async def query(**kwargs):
        options = kwargs['options']
        seen.append(options)
        yield AssistantMessage(content=[TextBlock(text='Inspecting.'),
                                        ToolUseBlock(id='r1', name='mcp__review__read_file', input={'path': 'x'})],
                               model='fixture-sdk')
        yield UserMessage(content=[ToolResultBlock(tool_use_id='r1', content='x', is_error=False)])
        yield AssistantMessage(content=[TextBlock(text='VERDICT: PASS\nGAPS: none')], model='fixture-sdk')
        yield _result()
    backend = _reviewer(tmp_path, mode, query)
    with context.bind_context(_session()):
        text = await collect_review(backend, 'q', 5)
    assert 'Inspecting.' in text and 'VERDICT: PASS' in text
    options = seen[0]
    main = _main_options(tmp_path, model='fixture-sdk')
    for name in ('setting_sources', 'settings', 'strict_mcp_config', 'permission_mode', 'cwd', 'env',
                 'tools', 'max_turns'):
        assert getattr(options, name) == getattr(main, name), name
    assert options.mcp_servers[config.MCP_SERVER_NAME] is _SERVER and 'review' in options.mcp_servers
    assert {'mcp__review__read_file', 'mcp__review__list_files'} <= set(options.allowed_tools)
    assert set(main.allowed_tools) <= set(options.allowed_tools)
    assert _RECURSION <= set(options.disallowed_tools)
    assert await _verdict(options, 'mcp__review__read_file', {'path': 'x'}) == 'allow'
    write = await _verdict(options, 'Write', {'file_path': str(tmp_path / 'a')})
    assert write == ('deny' if mode in ('plan', 'ask') else 'allow')
    assert backend.provenance['isolation'] == 'none: runs like the main-model Claude path'
    assert backend.provenance['permission_mode'] == mode


def test_review_settings_resolve_keeps_the_engines_mode():
    engine = SimpleNamespace(provider=get_provider('anthropic'), model='fixture-sdk', profile=None,
                             runtime_meter=None, _mode_getter=lambda: 'accept-edits')
    assert ReviewSettings.resolve(engine).mode == 'accept-edits'


async def test_prompt_optimizer_claude_path_is_read_only_in_the_workspace(tmp_path, monkeypatch):
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
    settings = ReviewSettings(get_provider('anthropic'), 'fixture-sdk', mode='auto')
    with context.bind_context(_session()):
        await prompt_optimizer.optimize({'draft': 'Summarise the notes'}, [], tmp_path, settings)
    options = seen[0]
    assert options.cwd == str(tmp_path)
    assert await _verdict(options, 'Write', {'file_path': str(tmp_path / 'a')}) == 'deny'
    assert await _verdict(options, 'Read', {'file_path': str(tmp_path / 'a')}) == 'allow'


def test_advisor_ui_text_no_longer_claims_read_only():
    root = Path(__file__).resolve().parents[1] / 'dream'
    for name in ('tui/picker.py', 'desktop/onboarding.py'):
        text = (root / name).read_text()
        assert 'read-only advisors' not in text and 'Advisors are read-only' not in text
        assert "Dream's permission mode" in text or "Dream\\'s permission mode" in text


# Gate round 1: Dream's session-state tools must not ride the main path's pre-approval
# into a consult. Built with the real session tool server, not a stub bundle.
_SESSION_STATE = ('update_plan', 'update_todos', 'set_project_title', 'project_note', 'skill_save',
                  'skill_patch', 'task_add', 'forget', 'memory_delete', 'memory_move')


def _real_session():
    from dream.tools import registry
    built = registry.build()
    names = set(built['names'])
    assert set(_SESSION_STATE) | {'remember'} <= names
    bundle = {'server': built['server'], 'exempt_tool_ids': built['exempt_tool_ids'],
              'execution': lambda: (None, None)}
    return SimpleNamespace(claude_tools=bundle, runtime_meter=None)


async def _assert_state_rule(options, tmp_path, *, remember):
    for name in _SESSION_STATE:
        tool = config.tool_id(name)
        assert tool not in options.allowed_tools, name
        assert await _verdict(options, tool, {'path': str(tmp_path / 'x')}) == 'deny', name
    tool = config.tool_id('remember')
    assert tool not in options.allowed_tools
    assert await _verdict(options, tool, {'content': 'x'}) == ('allow' if remember else 'deny')
    # Read-only Dream tools stay pre-approved, as on the main path.
    assert config.tool_id('web_search') in options.allowed_tools


@pytest.mark.parametrize('mode', ['plan', 'ask', 'accept-edits', 'auto'])
async def test_advisor_consult_never_gets_session_state_tools(mode, tmp_path, monkeypatch):
    seen = []
    async def query(**kwargs):
        seen.append(kwargs['options'])
        yield AssistantMessage(content=[TextBlock(text='advice')], model='fixture-sdk')
        yield _result()
    monkeypatch.setattr('claude_agent_sdk.query', query)
    with context.bind_context(_real_session()):
        answer = await moe.consult_advisor('anthropic', 'q', cwd=str(tmp_path), mode=mode, timeout=5)
    assert answer == 'advice'
    await _assert_state_rule(seen[0], tmp_path, remember=mode in ('accept-edits', 'auto'))


@pytest.mark.parametrize('mode', ['plan', 'auto'])
async def test_sdk_reviewer_never_gets_session_state_tools(mode, tmp_path):
    seen = []
    async def query(**kwargs):
        seen.append(kwargs['options'])
        yield AssistantMessage(content=[TextBlock(text='VERDICT: PASS\nGAPS: none')], model='fixture-sdk')
        yield _result()
    with context.bind_context(_real_session()):
        await collect_review(_reviewer(tmp_path, mode, query), 'q', 5)
    await _assert_state_rule(seen[0], tmp_path, remember=mode == 'auto')


async def test_prompt_optimizer_refuses_every_memory_and_session_state_tool(tmp_path, monkeypatch):
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
    with context.bind_context(_real_session()):
        await prompt_optimizer.optimize({'draft': 'Summarise the notes'}, [], tmp_path,
                                        ReviewSettings(get_provider('anthropic'), 'fixture-sdk', mode='auto'))
    options = seen[0]
    await _assert_state_rule(options, tmp_path, remember=False)
    from dream.core import policy
    for tool in options.allowed_tools:
        assert policy.capability(tool) == policy.READONLY or tool in SAFE_BUILTINS, tool


# Gate round 2: owner-facing Studio/UI tools, read-only on the main path, act on the owner's
# Studio, the shared hidden frame or the main session's next prompt; a consult gets none.
_OWNER_FACING = ('questions_v2', 'ask_user_input', 'suggest_research', 'visual_check', 'image_search',
                 'eval_js_user_view', 'screenshot_user_view', 'done', 'show_html', 'show_to_user', 'eval_js',
                 'get_webview_logs', 'multi_screenshot', 'present_fs_item_for_download', 'visualize_show_widget',
                 'save_screenshot', 'end_conversation', 'chart_display_v0', 'comparison_card_display_v0',
                 'featured_card_display_v0', 'itinerary_display_v0', 'link_preview_display_v0',
                 'options_card_display_v0', 'places_list_display_v0', 'product_carousel_display_v0',
                 'quiz_display_v0', 'step_card_display_v0', 'translation_display_v0',
                 # gate round 3: offer cards that spend the main session's once-per-session offer,
                 # and see, which mirrors into the owner's pane (a consult uses Read instead)
                 'suggest_plugin_install', 'suggest_skills', 'see')
_LOOKING = ('media_read', 'web_search', 'browse')


async def _assert_owner_rule(options, tmp_path):
    for name in _OWNER_FACING:
        tool = config.tool_id(name)
        assert tool not in options.allowed_tools, name
        refused = await options.can_use_tool(tool, {'path': str(tmp_path / 'x.html')}, None)
        assert refused.behavior == 'deny' and 'cannot show things to or ask the owner' in refused.message, name
    for name in _LOOKING:
        assert config.tool_id(name) in options.allowed_tools, name
        assert await _verdict(options, config.tool_id(name), {'path': str(tmp_path / 'x.png')}) == 'allow', name
    assert await _verdict(options, 'Read', {'file_path': str(tmp_path / 'x.png')}) == 'allow'


def test_owner_facing_tools_are_registered_where_the_rule_expects_them():
    from dream.tools import registry
    names = set(registry.build()['names'])
    # visual_check joins the toolset outside registry.build(); the callback still refuses it by name.
    assert set(_OWNER_FACING) - {'visual_check'} <= names and set(_LOOKING) <= names


@pytest.mark.parametrize('mode', ['plan', 'ask', 'accept-edits', 'auto'])
async def test_consult_and_reviewer_never_reach_the_owner(mode, tmp_path, monkeypatch):
    seen = []
    async def query(**kwargs):
        seen.append(kwargs['options'])
        yield AssistantMessage(content=[TextBlock(text='VERDICT: PASS\nGAPS: none')], model='fixture-sdk')
        yield _result()
    monkeypatch.setattr('claude_agent_sdk.query', query)
    with context.bind_context(_real_session()):
        await moe.consult_advisor('anthropic', 'q', cwd=str(tmp_path), mode=mode, timeout=5)
        await collect_review(_reviewer(tmp_path, mode, query), 'q', 5)
    for options in seen:
        await _assert_owner_rule(options, tmp_path)


async def test_prompt_optimizer_never_reaches_the_owner(tmp_path, monkeypatch):
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
    with context.bind_context(_real_session()):
        await prompt_optimizer.optimize({'draft': 'Summarise the notes'}, [], tmp_path,
                                        ReviewSettings(get_provider('anthropic'), 'fixture-sdk', mode='auto'))
    await _assert_owner_rule(seen[0], tmp_path)
