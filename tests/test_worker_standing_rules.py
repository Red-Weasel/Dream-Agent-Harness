import asyncio
from dataclasses import replace
from types import SimpleNamespace

from claude_agent_sdk import AgentDefinition
import pytest

from dream import config
from dream.core.instructions import worker_prompt
from dream.core.system_prompt import SystemPrompt, build_system_prompt
from dream.core.backends.anthropic import AnthropicBackend
from dream.memory.store import MemoryStore
from dream.tools.context import bind_context
from test_context_inspection_http import _worker_backend, _session, _reply


def _head(global_rule, project_rule):
    result = SystemPrompt(global_rule + project_rule + 'WAKE_PRIVATE')
    result.provenance = ((0, len(global_rule), 'global_instructions', None),
        (len(global_rule), len(global_rule + project_rule), 'project_file', {'file': 'DREAM.md'}),
        (len(global_rule + project_rule), len(result), 'wake', None))
    return result


@pytest.mark.asyncio
async def test_actual_http_worker_requests_inherit_only_captured_standing_rules(tmp_path):
    seen = []
    async def answer(payload):
        seen.append(payload)
        await asyncio.sleep(0)
        return _reply('Complete checked report.')
    async def launch(suffix):
        backend = _worker_backend(answer, system=_head('GLOBAL_' + suffix, 'PROJECT_' + suffix))
        backend.messages.append({'role': 'user', 'content': 'LEAD_SECRET_' + suffix})
        with bind_context(_session('session-' + suffix)):
            return await backend._run_subagent('explorer', 'Task ' + suffix)
    results = await asyncio.gather(launch('A'), launch('B'))
    assert all(not failed for _, failed in results)
    assert len(seen) == 2
    for payload in seen:
        suffix = payload['messages'][1]['content'][-1]
        head = payload['messages'][0]['content']
        assert 'GLOBAL_' + suffix in head and 'PROJECT_' + suffix in head
        assert 'You explore.' in head
        assert 'PRIVATE' not in head and 'LEAD_SECRET' not in str(payload)
        assert 'GLOBAL_' + ('B' if suffix == 'A' else 'A') not in head
        assert {'global_instructions', 'project_file', 'subagent_role_prompt'} <= {s[2] for s in head.provenance}


def test_sdk_supplied_agent_prompt_is_snapshot_without_mutating_shared_definition(tmp_path):
    definition = AgentDefinition(description='Review', prompt='Never edit.', tools=['Read'])
    def backend(suffix):
        return AnthropicBackend(system_prompt=_head('GLOBAL_' + suffix, 'PROJECT_' + suffix),
            mcp_server=None, preapproved_tool_ids=[], agents={'reviewer': definition},
            permission_cb=None, model=None, cwd=str(tmp_path))
    a, b = backend('A'), backend('B')
    for backend, suffix in ((a, 'A'), (b, 'B')):
        options = backend._build_options()
        text = options.agents['reviewer'].prompt
        assert 'GLOBAL_' + suffix in text and 'PROJECT_' + suffix in text
        assert 'PRIVATE' not in text and 'Never edit.' in text
        assert options.agents['reviewer'].tools == ['Read']
    assert definition.prompt == 'Never edit.'


def test_captured_empty_standing_rules_do_not_fall_back_to_changed_disk(tmp_path, monkeypatch):
    (tmp_path / 'DREAM.md').write_text('NEW_DISK_SECRET')
    monkeypatch.setattr(config, 'INSTRUCTIONS_FILE', tmp_path / 'global.md')
    (tmp_path / 'global.md').write_text('NEW_GLOBAL_SECRET')
    head = SystemPrompt('BASE_ONLY'); head.provenance = ((0, len(head), 'base', None),)
    assert worker_prompt('role', head, workspace=tmp_path) == 'role'


def test_legacy_fallback_requires_explicit_workspace_and_never_global_tool_context(tmp_path, monkeypatch):
    from dream.tools import context
    monkeypatch.setattr(config, 'INSTRUCTIONS_FILE', tmp_path / 'global.md')
    (tmp_path / 'global.md').write_text('GLOBAL_OK')
    (tmp_path / 'DREAM.md').write_text('PROJECT_OK')
    monkeypatch.setattr(context, '_CTX', SimpleNamespace(workspace=tmp_path))
    assert worker_prompt('role', 'LEGACY_LEAD_SECRET') == 'role'
    result = worker_prompt('role', 'LEGACY_LEAD_SECRET', workspace=tmp_path)
    assert 'GLOBAL_OK' in result and 'PROJECT_OK' in result and 'LEGACY_LEAD_SECRET' not in result


@pytest.mark.asyncio
async def test_oversized_standing_rules_refuse_worker_before_request():
    calls = []
    async def answer(payload):
        calls.append(payload)
        return _reply('wrong')
    backend = _worker_backend(answer, system=_head('RULE ' * 40000, 'PROJECT'))
    with bind_context(_session('oversized')):
        result, failed = await backend._run_subagent('explorer', 'Task')
    assert failed and not calls
    assert 'context' in result.lower() or 'budget' in result.lower()


@pytest.mark.asyncio
async def test_plain_legacy_http_heads_use_each_bound_workspace_only(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'INSTRUCTIONS_FILE', tmp_path / 'global.md')
    (tmp_path / 'global.md').write_text('GLOBAL_STANDING')
    seen = []
    async def answer(payload):
        seen.append(payload)
        await asyncio.sleep(0)
        return _reply('Complete report.')
    async def launch(suffix):
        root = tmp_path / suffix; root.mkdir()
        (root / 'DREAM.md').write_text('RULE_' + suffix)
        session = _session('legacy-' + suffix); session.workspace = root
        backend = _worker_backend(answer, system='LEAD_SECRET')
        with bind_context(session):
            return await backend._run_subagent('explorer', suffix)
    results = await asyncio.gather(launch('A'), launch('B'))
    assert all(not failed for _, failed in results)
    for payload in seen:
        suffix = payload['messages'][1]['content']
        head = payload['messages'][0]['content']
        assert 'GLOBAL_STANDING' in head and 'RULE_' + suffix in head
        assert 'RULE_' + ('B' if suffix == 'A' else 'A') not in head
        assert 'LEAD_SECRET' not in str(payload)


def test_real_assembled_snapshot_keeps_hashes_and_ignores_later_file_edits(tmp_path, monkeypatch):
    import hashlib
    root = tmp_path / 'project'; root.mkdir()
    raw = b'PROJECT_AT_START\n'
    (root / 'DREAM.md').write_bytes(raw)
    monkeypatch.setattr(config, 'INSTRUCTIONS_FILE', tmp_path / 'global.md')
    (tmp_path / 'global.md').write_text('GLOBAL_AT_START')
    store = MemoryStore(tmp_path / 'memory.db')
    try:
        prompt = build_system_prompt(store, 'worker-snapshot', workspace=root)
    finally:
        store.close()
    (root / 'DREAM.md').write_text('PROJECT_CHANGED')
    result = worker_prompt('role', prompt, workspace=root)
    assert 'PROJECT_AT_START' in result and 'PROJECT_CHANGED' not in result
    assert 'GLOBAL_AT_START' in result and '## Waking up' not in result
    detail = next(detail for _, _, source, detail in result.provenance if source == 'project_file')
    assert detail['file_sha256'] == hashlib.sha256(raw).hexdigest()


@pytest.mark.asyncio
async def test_claude_local_helpers_keep_each_captured_session_rules_after_disk_changes(tmp_path, monkeypatch):
    from dream.core.local_helpers import LocalHelpers
    from dream.core.providers import Provider
    from dream.core.subagents import LocalSubagentSpec
    from test_context_inspection_http import _Client
    monkeypatch.setattr(config, 'INSTRUCTIONS_FILE', tmp_path / 'global.md')
    seen = []
    async def answer(payload):
        seen.append(payload)
        await asyncio.sleep(0)
        return _reply('Checked report.')
    sessions = []
    for suffix in ('A', 'B'):
        root = tmp_path / suffix; root.mkdir()
        (root / 'DREAM.md').write_text('LATER_PROJECT_' + suffix)
        helpers = LocalHelpers(tools=[], permission_cb=None, emit=None,
            provider=Provider(key='fixture-http', label='Fixture', kind='openai',
                base_url='https://fixture.invalid/v1', default_api_key='unused'),
            subagents={'explorer': LocalSubagentSpec(name='explorer', description='Read only',
                prompt='ROLE_READ_ONLY', tool_names=())})
        AnthropicBackend(system_prompt=_head('SESSION_GLOBAL_' + suffix, 'SESSION_PROJECT_' + suffix),
            mcp_server=None, preapproved_tool_ids=[], agents=None, permission_cb=None, model=None,
            cwd=str(root), local_helpers=helpers)
        sessions.append((suffix, root, helpers))
    (tmp_path / 'global.md').write_text('LATER_GLOBAL')
    from unittest.mock import AsyncMock
    from dream.core.backends.openai_compat import OpenAICompatBackend
    async def connect(helper):
        helper._client = _Client(responder=answer)
        helper._coordinator = lambda model=None: None
        helper._role_model = lambda name: None
        helper._lane_ctx = lambda: 32768
        helper.n_ctx = 100000
    monkeypatch.setattr(OpenAICompatBackend, 'connect', connect)
    monkeypatch.setattr(OpenAICompatBackend, 'disconnect', AsyncMock())
    async def launch(suffix, root, helpers):
        helpers._served_models = AsyncMock(return_value=[{'id': 'fixture-model'}])
        session = _session('local-' + suffix); session.workspace = root
        with bind_context(session):
            return await helpers.delegate([{'subagent_type': 'explorer', 'prompt': suffix}])
    results = await asyncio.gather(*(launch(*session) for session in sessions))
    assert all(not failed and 'Checked report.' in text for text, failed in results)
    assert all(helpers.running == [] for _, _, helpers in sessions)
    for payload in seen:
        suffix = payload['messages'][1]['content']
        text = payload['messages'][0]['content']
        assert 'SESSION_GLOBAL_' + suffix in text and 'SESSION_PROJECT_' + suffix in text
        assert 'LATER_' not in text and 'WAKE_PRIVATE' not in text
        assert 'SESSION_PROJECT_' + ('B' if suffix == 'A' else 'A') not in text
        assert 'ROLE_READ_ONLY' in text and payload['tools'] == []


def test_legacy_worker_fallback_reads_without_bootstrapping_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'INSTRUCTIONS_FILE', tmp_path / 'missing-global.md')
    workspace = tmp_path / 'project'
    workspace.mkdir()
    assert worker_prompt('role', 'legacy', workspace=workspace) == 'role'
    assert list(workspace.iterdir()) == []
    (workspace / 'DREAM.md').write_text('existing project rule')
    assert 'existing project rule' in worker_prompt('role', 'legacy', workspace=workspace)
    assert [p.name for p in workspace.iterdir()] == ['DREAM.md']
