"""Native CLI control and executable parity, without running provider processes."""
from types import SimpleNamespace

import pytest

from dream.core import moe
from dream.core.backends.cli_agent import CliAgentBackend, adapter_for
from dream.core.providers import get_provider


@pytest.mark.parametrize('level', ['low', 'medium', 'high', 'xhigh'])
async def test_grok_main_forwards_native_effort_and_exact_model(monkeypatch, level):
    backend = CliAgentBackend(adapter_for('grok'), system_prompt='fixture', cwd='/tmp', model='grok-4.7')
    backend.set_effort(level)
    captured = []
    async def spawn(*args, **kwargs):
        captured.extend(args)
        raise RuntimeError('fixture prevents process launch')
    monkeypatch.setattr('dream.core.backends.cli_agent.asyncio.create_subprocess_exec', spawn)
    monkeypatch.setattr('dream.core.backends.cli_agent.supervised_command', lambda args: args)
    with pytest.raises(RuntimeError, match='prevents process'):
        async for _ in backend.ask('fixture'):
            pass
    assert captured[captured.index('--effort') + 1] == level
    assert captured[captured.index('--model') + 1] == 'grok-4.7'
    assert '-c' not in captured and not any('model_reasoning_effort' in arg for arg in captured)


@pytest.mark.parametrize('level', ['low', 'medium', 'high', 'xhigh'])
async def test_grok_advisor_uses_native_flag_without_corrupting_prompt_file(monkeypatch, tmp_path, level):
    from dream.core.cli_review import CLIConsultation
    binary = tmp_path / 'grok'
    binary.write_text('#!/bin/sh\n')
    binary.chmod(0o700)
    monkeypatch.setattr('dream.core.cli_review.locate_command', lambda name: str(binary))
    captured = []
    async def run(self, prompt):
        captured.extend(self.argv)
        assert self.argv[self.argv.index('--prompt-file') + 1] == str(self.root / 'prompt.txt')
        return 'fixture answer'
    monkeypatch.setattr(CLIConsultation, 'run', run)
    assert await moe._consult_cli(get_provider('grok'), 'fixture', cwd=str(tmp_path),
                                  model='grok-4.7', effort=level) == 'fixture answer'
    assert captured[captured.index('--effort') + 1] == level
    assert captured[captured.index('--model') + 1] == 'grok-4.7'
    assert '-c' not in captured and not any('model_reasoning_effort' in arg for arg in captured)


def test_grok_invalid_effort_preserves_prior_choice():
    backend = CliAgentBackend(adapter_for('grok'), system_prompt='fixture', cwd='/tmp', model='grok-4.7')
    backend.set_effort('high')
    for rejected in ('max', 'ultra', 'on', False):
        with pytest.raises(ValueError):
            backend.set_effort(rejected)
        assert backend._effort == 'high'


async def test_gemini_mcp_setup_and_cleanup_use_connected_binary_and_node_environment(monkeypatch, tmp_path):
    from dream.core.engine import Engine
    from dream.core.backends.gemini_adapter import GeminiAdapter
    folder = tmp_path / 'nvm-bin'
    folder.mkdir()
    for name, text in [('gemini', '#!/usr/bin/env node\n'), ('node', '#!/bin/sh\n')]:
        target = folder / name
        target.write_text(text)
        target.chmod(0o700)
    engine = Engine.__new__(Engine)
    engine.workspace = str(tmp_path)
    engine.provider = SimpleNamespace(label='Gemini fixture')
    engine.backend = SimpleNamespace(adapter=GeminiAdapter(), _executable=str(folder / 'gemini'),
        mcp_config={'name': 'dream-fixture', 'command': 'python3', 'args': [], 'env': {}})
    engine._cli_mcp_name = None
    original_env = {'PATH': '', 'HOME': '/fixture-home', 'LANG': 'C'}
    monkeypatch.setattr(engine, '_cli_setup_env', lambda: dict(original_env))
    captured = []
    async def run(argv, **kwargs):
        captured.append((argv, kwargs['env']))
        return SimpleNamespace(returncode=0, timed_out=False, output=b'')
    monkeypatch.setattr('dream.core.execution.run_owned', run)
    await engine._register_persistent_mcp()
    await engine._deregister_persistent_mcp()
    assert [argv[2] for argv, env in captured] == ['add', 'remove']
    for argv, env in captured:
        assert argv[0] == str(folder / 'gemini')
        assert env == {**original_env, 'PATH': str(folder)}
    assert original_env['PATH'] == ''
