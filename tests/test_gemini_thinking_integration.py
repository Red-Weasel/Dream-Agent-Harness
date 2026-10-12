"""Selected-model settings reach native boundaries and outlive owned cleanup."""
import asyncio
import json
from pathlib import Path

import pytest

from dream.core import moe
from dream.core.backends.cli_agent import CliAgentBackend, adapter_for
from dream.core.cli_review import CLIConsultation
from dream.core.providers import get_provider
from test_gemini_thinking_overlay import fixture


def configure(fixture, monkeypatch):
    root, executable, _, env = fixture
    for name in ('GEMINI_CLI_HOME', 'GEMINI_CLI_SYSTEM_DEFAULTS_PATH'):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    backend = CliAgentBackend(adapter_for('gemini'), system_prompt='fixture',
                              cwd=str(root), model='gemini-3.8-flash')
    backend._executable = str(executable)
    backend.set_effort('off')
    return backend


async def test_main_native_spawn_receives_private_settings_and_selected_workspace(fixture, monkeypatch):
    backend = configure(fixture, monkeypatch)
    targets = []
    async def spawn(*args, **kwargs):
        assert kwargs['cwd'] == backend.cwd
        target = Path(kwargs['env']['GEMINI_CLI_SYSTEM_SETTINGS_PATH'])
        targets.append(target)
        body = json.loads(target.read_text())
        assert body['modelConfigs']['customOverrides'][0]['modelConfig']['generateContentConfig']['thinkingConfig'] == {'thinkingLevel': 'LOW'}
        assert args[args.index('--model') + 1] == 'gemini-3.8-flash'
        assert not any('model_reasoning_effort' in arg for arg in args)
        raise RuntimeError('owned fixture spawn failure')
    monkeypatch.setattr('dream.core.backends.cli_agent.asyncio.create_subprocess_exec', spawn)
    with pytest.raises(RuntimeError, match='owned fixture spawn failure'):
        async for _ in backend.ask('fixture'):
            pass
    assert targets and not targets[0].exists()
    assert backend._proc is None and backend._spawning is None


@pytest.mark.parametrize('failure', [None, RuntimeError, asyncio.CancelledError])
async def test_advisor_settings_survive_until_stop_and_cleanup_after_every_outcome(fixture, monkeypatch, failure):
    backend = configure(fixture, monkeypatch)
    monkeypatch.setattr('dream.core.cli_review.locate_command', lambda name: backend._executable)
    targets = []
    async def run(self, prompt):
        target = Path(self.env['GEMINI_CLI_SYSTEM_SETTINGS_PATH'])
        targets.append(target)
        assert target.exists()
        assert '--model' in self.argv and 'gemini-3.8-flash' in self.argv
        assert not any('model_reasoning_effort' in arg for arg in self.argv)
        if failure:
            raise failure('owned advisor fixture')
        return 'fixture answer'
    async def stop(self):
        if targets:
            assert targets[-1].exists(), 'Delete only after owned stop has settled'
    monkeypatch.setattr(CLIConsultation, 'run', run)
    monkeypatch.setattr(CLIConsultation, '_stop', stop)
    call = moe._consult_cli(get_provider('gemini'), 'fixture', cwd=backend.cwd,
                           model=backend.model, effort='off')
    if failure:
        with pytest.raises(failure):
            await call
    else:
        assert await call == 'fixture answer'
    assert targets and not targets[0].exists()


async def test_main_owned_cli_interrupt_reaps_before_overlay_removal(fixture, monkeypatch):
    backend = configure(fixture, monkeypatch)
    executable = fixture[1]
    audit = fixture[0] / 'owned-audit.json'
    executable.write_text('''#!/usr/bin/python3
import json, os, pathlib, signal, sys, time
target=pathlib.Path(os.environ['GEMINI_CLI_SYSTEM_SETTINGS_PATH'])
def stop(signum, frame):
    pathlib.Path(AUDIT).write_text(json.dumps({'present_at_stop':target.exists(),'path':str(target)}))
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
print(json.dumps({'type':'message','role':'assistant','content':'owned fixture ready'}), flush=True)
while True: time.sleep(1)
'''.replace('AUDIT', repr(str(audit))))
    executable.chmod(0o700)
    ready = asyncio.Event()
    async def consume():
        async for event in backend.ask('fixture'):
            if event.kind == 'text_delta':
                ready.set()
    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(ready.wait(), 5)
        await backend.interrupt()
        await asyncio.wait_for(task, 5)
    finally:
        await backend.interrupt()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    receipt = json.loads(audit.read_text())
    assert receipt['present_at_stop'] is True
    assert not Path(receipt['path']).exists()
    assert backend._proc is None and backend._spawning is None
